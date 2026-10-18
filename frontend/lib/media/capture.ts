import type { FluffyPublicConfig } from "@/lib/fluffy/config";
import { mediaAudioUrl, mediaFrameUrl, postJson } from "@/lib/fluffy/client";

export type CapturePhase = {
  camera: "off" | "starting" | "on" | "denied" | "absent" | "error";
  mic: "off" | "on" | "denied" | "muted" | "absent";
  transport: "idle" | "connecting" | "reaching" | "offline" | "unsupported";
  lastAnalyzedAt: number | null;
  delayed: boolean;
  error: string | null;
};

type Listener = (phase: CapturePhase) => void;

const FRAME_MS = 250;
const WIDTH = 640;
const MAX_BUFFERED = 1_000_000;

/**
 * The provider owns the stream and a hidden video element. Unmounting the
 * chat preview does not stop tracks.
 */
export class CaptureController {
  epoch = 0;
  stream: MediaStream | null = null;
  phase: CapturePhase = {
    camera: "off",
    mic: "off",
    transport: "idle",
    lastAnalyzedAt: null,
    delayed: false,
    error: null,
  };
  private video: HTMLVideoElement | null = null;
  private listeners = new Set<Listener>();
  private timer = 0;
  private audio: AudioContext | null = null;
  private socket: WebSocket | null = null;
  private previous: Uint8ClampedArray | null = null;
  private sending = false;
  private paused = false;
  private mediaUnsupported = false;
  private speechActive: () => boolean = () => false;
  private tailUntil = 0;
  private sessionId = "local";

  constructor(private readonly config: FluffyPublicConfig) {}

  setSpeechActive(read: () => boolean): void {
    this.speechActive = read;
  }

  noteSpeechEnded(now = Date.now()): void {
    this.tailUntil = now + 300;
  }

  markAnalyzed(now = Date.now()): void {
    this.patch({ lastAnalyzedAt: now, transport: "reaching", delayed: false, error: null });
  }

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    listener(this.phase);
    return () => this.listeners.delete(listener);
  }

  attach(video: HTMLVideoElement | null): void {
    this.video = video;
    if (video && this.stream) {
      video.srcObject = this.stream;
      void video.play().catch(() => undefined);
    }
  }

  previewStream(): MediaStream | null {
    return this.stream;
  }

  async start(sessionId: string): Promise<void> {
    if (this.stream) return;
    this.sessionId = sessionId;
    this.epoch += 1;
    const epoch = this.epoch;
    this.patch({ camera: "starting", error: null, transport: "connecting" });
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        video: { width: { ideal: 1280 }, height: { ideal: 720 } },
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
      });
    } catch (error) {
      const name = error instanceof DOMException ? error.name : "";
      if (name === "NotAllowedError") {
        try {
          stream = await navigator.mediaDevices.getUserMedia({ video: { width: { ideal: 1280 }, height: { ideal: 720 } }, audio: false });
          this.patch({ mic: "denied", error: "Microphone permission was denied. Camera preview and text still work." });
        } catch (videoError) {
          this.failPermission(videoError);
          return;
        }
      } else {
        this.failPermission(error);
        return;
      }
    }
    if (epoch !== this.epoch) {
      stream.getTracks().forEach((track) => track.stop());
      return;
    }
    this.stream = stream;
    const videoTrack = stream.getVideoTracks()[0];
    const audioTrack = stream.getAudioTracks()[0];
    this.patch({
      camera: videoTrack ? "on" : "absent",
      mic: audioTrack ? "on" : this.phase.mic === "denied" ? "denied" : "absent",
    });
    if (this.video) {
      this.video.srcObject = stream;
      await this.video.play().catch(() => undefined);
    }
    await this.openTransport(epoch);
    this.sending = true;
    this.paused = false;
    this.startFrames(epoch);
    if (audioTrack) await this.startAudio(stream, epoch);
  }

  pause(): void {
    this.paused = true;
    this.sending = false;
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify({ type: "live.pause", paused: true }));
    }
  }

  resume(): void {
    this.paused = false;
    this.sending = this.phase.transport !== "unsupported";
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify({ type: "live.pause", paused: false }));
    }
  }

  mute(muted: boolean): void {
    const track = this.stream?.getAudioTracks()[0];
    if (!track) return;
    track.enabled = !muted;
    this.patch({ mic: muted ? "muted" : "on" });
  }

  async stop(): Promise<void> {
    this.epoch += 1;
    this.sending = false;
    this.paused = false;
    if (this.timer) window.clearInterval(this.timer);
    this.timer = 0;
    if (this.socket && this.socket.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify({ type: "live.pause", paused: true }));
    }
    this.socket?.close();
    this.socket = null;
    this.stream?.getTracks().forEach((track) => track.stop());
    this.stream = null;
    if (this.video) this.video.srcObject = null;
    const audio = this.audio;
    this.audio = null;
    if (audio) await audio.close().catch(() => undefined);
    this.previous = null;
    this.mediaUnsupported = false;
    this.patch({
      camera: "off",
      mic: "off",
      transport: "idle",
      lastAnalyzedAt: null,
      delayed: false,
      error: null,
    });
  }

  private async openTransport(epoch: number): Promise<void> {
    if (this.config.contract === "legacy-live") {
      const socket = new WebSocket(`${this.config.wsBase}/ws/live`);
      socket.binaryType = "arraybuffer";
      this.socket = socket;
      socket.onopen = () => {
        if (epoch !== this.epoch) return;
        socket.send(JSON.stringify({ type: "live.pause", paused: false }));
        this.patch({ transport: "reaching" });
      };
      socket.onerror = () => {
        if (epoch !== this.epoch) return;
        this.patch({ transport: "offline", error: "The media socket is not reachable." });
      };
      socket.onclose = () => {
        if (epoch !== this.epoch) return;
        if (this.phase.transport === "reaching") this.patch({ transport: "offline" });
      };
      return;
    }
    this.patch({ transport: "connecting" });
  }

  private startFrames(epoch: number): void {
    const videoTrack = this.stream?.getVideoTracks()[0];
    const settings = videoTrack?.getSettings() ?? {};
    const aspect = (settings.height ?? 720) / (settings.width ?? 1280);
    const frame = document.createElement("canvas");
    frame.width = WIDTH;
    frame.height = Math.max(2, Math.round((WIDTH * aspect) / 2) * 2);
    const small = document.createElement("canvas");
    small.width = 64;
    small.height = 36;
    const frameCtx = frame.getContext("2d");
    const smallCtx = small.getContext("2d", { willReadFrequently: true });
    this.timer = window.setInterval(() => {
      if (epoch !== this.epoch || !this.sending || this.paused) return;
      const video = this.video;
      if (!video || !frameCtx || !smallCtx || video.readyState < 2) return;
      if (this.socket && (this.socket.readyState !== WebSocket.OPEN || this.socket.bufferedAmount > MAX_BUFFERED)) return;
      const ts = Date.now();
      smallCtx.drawImage(video, 0, 0, small.width, small.height);
      const motion = this.motionScore(smallCtx.getImageData(0, 0, small.width, small.height).data);
      frameCtx.drawImage(video, 0, 0, frame.width, frame.height);
      frame.toBlob((blob) => {
        if (!blob || epoch !== this.epoch || !this.sending) return;
        void blob.arrayBuffer().then((jpeg) => {
          if (epoch !== this.epoch || !this.sending) return;
          this.sendFrame(epoch, ts, motion, jpeg);
        });
      }, "image/jpeg", 0.7);
    }, FRAME_MS);
  }

  grabStill(): void {
    const video = this.video;
    if (!video || video.readyState < 2 || !this.sending) return;
    const settings = this.stream?.getVideoTracks()[0]?.getSettings() ?? {};
    const width = 1280;
    const aspect = (settings.height ?? 720) / (settings.width ?? 1280);
    const frame = document.createElement("canvas");
    frame.width = width;
    frame.height = Math.max(2, Math.round((width * aspect) / 2) * 2);
    const context = frame.getContext("2d");
    if (!context) return;
    context.drawImage(video, 0, 0, frame.width, frame.height);
    frame.toBlob((blob) => {
      if (!blob) return;
      void blob.arrayBuffer().then((jpeg) => {
        const bytes = new Uint8Array(jpeg);
        let binary = "";
        for (let i = 0; i < bytes.length; i += 1) binary += String.fromCharCode(bytes[i]);
        void postJson(mediaFrameUrl(this.config), {
          session_id: this.sessionId,
          epoch: this.epoch,
          ts_ms: Date.now(),
          motion: 0,
          still: true,
          jpeg_base64: btoa(binary),
        });
      });
    }, "image/jpeg", 0.85);
  }

  private sendFrame(epoch: number, ts: number, motion: number, jpeg: ArrayBuffer): void {
    if (this.config.contract === "legacy-live") {
      const socket = this.socket;
      if (!socket || socket.readyState !== WebSocket.OPEN) return;
      const header = new DataView(new ArrayBuffer(13));
      header.setUint8(0, 1);
      header.setBigUint64(1, BigInt(ts), true);
      header.setFloat32(9, motion, true);
      socket.send(join(header.buffer, jpeg));
      this.patch({ transport: "reaching", delayed: false, error: null });
      return;
    }
    if (this.mediaUnsupported) return;
    const bytes = new Uint8Array(jpeg);
    let binary = "";
    for (let i = 0; i < bytes.length; i += 1) binary += String.fromCharCode(bytes[i]);
    void postJson(mediaFrameUrl(this.config), {
      session_id: this.sessionId,
      epoch,
      ts_ms: ts,
      motion,
      jpeg_base64: btoa(binary),
    }).then((result) => {
      if (epoch !== this.epoch) return;
      if (result.missing) {
        this.mediaUnsupported = true;
        this.sending = false;
        this.patch({ transport: "unsupported", error: "This backend has no /api/media/frames route. The preview stays local." });
        return;
      }
      if (!result.ok) {
        const delayed = this.phase.lastAnalyzedAt != null && Date.now() - this.phase.lastAnalyzedAt > 8000;
        this.patch({ transport: "offline", delayed, error: "The media upload did not reach the backend." });
        return;
      }
      this.patch({ transport: "reaching", delayed: false, error: null });
    });
  }

  private async startAudio(stream: MediaStream, epoch: number): Promise<void> {
    const audio = new AudioContext();
    this.audio = audio;
    await audio.audioWorklet.addModule("/audio/pcm-worklet.js");
    if (epoch !== this.epoch) {
      await audio.close().catch(() => undefined);
      return;
    }
    const source = audio.createMediaStreamSource(stream);
    const node = new AudioWorkletNode(audio, "pcm-chunker");
    node.port.onmessage = (event: MessageEvent<ArrayBuffer>) => {
      if (epoch !== this.epoch || !this.sending || this.paused) return;
      if (this.phase.mic === "muted" || this.phase.mic === "denied") return;
      if (this.speechActive() || Date.now() < this.tailUntil) return;
      this.sendAudio(epoch, event.data);
    };
    source.connect(node);
    const mute = audio.createGain();
    mute.gain.value = 0;
    node.connect(mute).connect(audio.destination);
  }

  private sendAudio(epoch: number, pcm: ArrayBuffer): void {
    if (this.config.contract === "legacy-live") {
      const socket = this.socket;
      if (!socket || socket.readyState !== WebSocket.OPEN) return;
      socket.send(join(new Uint8Array([2]).buffer, pcm));
      return;
    }
    if (this.mediaUnsupported) return;
    const bytes = new Uint8Array(pcm);
    let binary = "";
    for (let i = 0; i < bytes.length; i += 1) binary += String.fromCharCode(bytes[i]);
    void postJson(mediaAudioUrl(this.config), {
      session_id: this.sessionId,
      epoch,
      pcm16_base64: btoa(binary),
      sample_rate: 16000,
      format: "pcm16",
    });
  }

  private motionScore(pixels: Uint8ClampedArray): number {
    const gray = new Uint8ClampedArray(pixels.length / 4);
    for (let i = 0; i < gray.length; i += 1) {
      gray[i] = (pixels[i * 4] * 0.299 + pixels[i * 4 + 1] * 0.587 + pixels[i * 4 + 2] * 0.114) | 0;
    }
    const previous = this.previous;
    this.previous = gray;
    if (!previous) return 0;
    let total = 0;
    for (let i = 0; i < gray.length; i += 1) total += Math.abs(gray[i] - previous[i]);
    return total / gray.length / 255;
  }

  private failPermission(error: unknown): void {
    const name = error instanceof DOMException ? error.name : "";
    if (name === "NotAllowedError") {
      this.patch({ camera: "denied", mic: "denied", transport: "idle", error: "Camera permission was denied. You can still type to Fluffy." });
      return;
    }
    if (name === "NotFoundError") {
      this.patch({ camera: "absent", mic: "absent", transport: "idle", error: "No camera was found. Text input still works." });
      return;
    }
    this.patch({ camera: "error", transport: "idle", error: "The camera could not start." });
  }

  private patch(partial: Partial<CapturePhase>): void {
    this.phase = { ...this.phase, ...partial };
    for (const listener of this.listeners) listener(this.phase);
  }
}

function join(head: ArrayBuffer, body: ArrayBuffer): ArrayBuffer {
  const out = new Uint8Array(head.byteLength + body.byteLength);
  out.set(new Uint8Array(head), 0);
  out.set(new Uint8Array(body), head.byteLength);
  return out.buffer;
}
