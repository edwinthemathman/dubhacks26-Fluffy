import { useCallback, useEffect, useRef, useState } from "react";

// Sends the webcam and mic to the live harness (ws://<host>/ws/live).
//   frame: [1][uint64 ts_ms][float32 motion][jpeg]   about 4 per second, 640 px wide
//   audio: [2][pcm16 mono 16 kHz]                    100 ms chunks
// Nothing is stored in the browser.

export type LiveStatus = "off" | "starting" | "live" | "error";

const FRAME_MS = 250;
const WIDTH = 640;
const JPEG_QUALITY = 0.7;
const MAX_BUFFERED = 1_000_000; // skip frames if the socket backs up

type Parts = {
  stream?: MediaStream;
  socket?: WebSocket;
  timer?: number;
  audio?: AudioContext;
  previous?: Uint8ClampedArray;
};

function teardown(parts: Parts, video: HTMLVideoElement | null) {
  if (parts.timer) window.clearInterval(parts.timer);
  parts.socket?.close();
  parts.stream?.getTracks().forEach((track) => track.stop());
  void parts.audio?.close();
  if (video) video.srcObject = null;
  parts.stream = undefined;
  parts.socket = undefined;
  parts.timer = undefined;
  parts.audio = undefined;
  parts.previous = undefined;
}

export function useLiveCapture(options?: { isSpeaking?: () => boolean }) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [status, setStatus] = useState<LiveStatus>("off");
  const [error, setError] = useState<string | null>(null);
  const parts = useRef<Parts>({});
  const generation = useRef(0);
  const isSpeaking = useRef(options?.isSpeaking);
  isSpeaking.current = options?.isSpeaking;

  const stop = useCallback(() => {
    generation.current += 1;
    const socket = parts.current.socket;
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: "live.pause", paused: true }));
    }
    teardown(parts.current, videoRef.current);
    setStatus("off");
    setError(null);
  }, []);

  const start = useCallback(async () => {
    if (parts.current.stream) return;
    const token = ++generation.current;
    setStatus("starting");
    setError(null);
    // Open the audio graph inside the click. Creating it after getUserMedia
    // leaves the context suspended, so the mic worklet never runs.
    const audio = new AudioContext();
    parts.current.audio = audio;
    void audio.resume();
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { width: { ideal: 1280 }, height: { ideal: 720 } },
        // Noise suppression eats quiet speech and thuds. Recordings keep that
        // soundtrack; the live mic should too. Echo cancellation still stops
        // Fluffy from hearing its own speaker.
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: false,
          autoGainControl: true,
        },
      });
      if (generation.current !== token) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      parts.current.stream = stream;
      const video = videoRef.current;
      if (video) {
        video.srcObject = stream;
        await video.play().catch(() => undefined);
      }
      if (generation.current !== token) return;

      const protocol = window.location.protocol === "https:" ? "wss" : "ws";
      const socket = new WebSocket(`${protocol}://${window.location.host}/ws/live`);
      socket.binaryType = "arraybuffer";
      parts.current.socket = socket;

      let opened = false;
      const fail = (message: string) => {
        if (generation.current !== token) return;
        generation.current += 1;
        teardown(parts.current, videoRef.current);
        setError(message);
        setStatus("error");
      };
      const drop = () => {
        fail(
          opened
            ? "The Fluffy harness disconnected."
            : "Can't reach the Fluffy harness. Is `uv run fluffy-live` running?",
        );
      };
      socket.onopen = () => {
        if (generation.current !== token) return;
        opened = true;
        socket.send(JSON.stringify({ type: "live.pause", paused: false }));
        setStatus("live");
      };
      socket.onerror = drop;
      socket.onclose = drop;

      startFrames(stream, socket, token);
      await startAudio(stream, socket, token);
    } catch (err) {
      if (generation.current !== token) return;
      const name = err instanceof DOMException ? err.name : "";
      const message =
        name === "NotAllowedError"
          ? "Camera or microphone permission was denied. Allow it in the browser's address bar."
          : name === "NotFoundError"
            ? "No camera or microphone was found."
            : `Couldn't start the camera (${String(err)}).`;
      stop();
      setError(message);
      setStatus("error");
    }

    function startFrames(stream: MediaStream, socket: WebSocket, token: number) {
      const settings = stream.getVideoTracks()[0]?.getSettings() ?? {};
      const aspect = (settings.height ?? 720) / (settings.width ?? 1280);
      const frame = document.createElement("canvas");
      frame.width = WIDTH;
      frame.height = Math.round((WIDTH * aspect) / 2) * 2;
      const small = document.createElement("canvas");
      small.width = 64;
      small.height = 36;
      const frameCtx = frame.getContext("2d");
      const smallCtx = small.getContext("2d", { willReadFrequently: true });
      let busy = false;
      let lastSent = 0;

      parts.current.timer = window.setInterval(() => {
        const video = videoRef.current;
        if (busy || !video || !frameCtx || !smallCtx || video.readyState < 2) return;
        if (socket.readyState !== WebSocket.OPEN || socket.bufferedAmount > MAX_BUFFERED) return;
        const ts = Date.now();
        smallCtx.drawImage(video, 0, 0, small.width, small.height);
        const motion = motionScore(smallCtx.getImageData(0, 0, small.width, small.height).data);
        frameCtx.drawImage(video, 0, 0, frame.width, frame.height);
        busy = true;
        frame.toBlob(
          (blob) => {
            busy = false;
            // toBlob can finish out of order. A late older frame would become "now".
            if (!blob || generation.current !== token || ts < lastSent) return;
            if (socket.readyState !== WebSocket.OPEN) return;
            lastSent = ts;
            void blob.arrayBuffer().then((jpeg) => {
              if (generation.current !== token || ts < lastSent) return;
              const header = new DataView(new ArrayBuffer(13));
              header.setUint8(0, 1);
              header.setBigUint64(1, BigInt(ts), true);
              header.setFloat32(9, motion, true);
              socket.send(join(header.buffer, jpeg));
            });
          },
          "image/jpeg",
          JPEG_QUALITY,
        );
      }, FRAME_MS);
    }

    function motionScore(pixels: Uint8ClampedArray): number {
      const gray = new Uint8ClampedArray(pixels.length / 4);
      for (let i = 0; i < gray.length; i++) {
        gray[i] = (pixels[i * 4] * 0.299 + pixels[i * 4 + 1] * 0.587 + pixels[i * 4 + 2] * 0.114) | 0;
      }
      const previous = parts.current.previous;
      parts.current.previous = gray;
      if (!previous) return 0;
      let total = 0;
      for (let i = 0; i < gray.length; i++) total += Math.abs(gray[i] - previous[i]);
      return total / gray.length / 255;
    }

    async function startAudio(stream: MediaStream, socket: WebSocket, token: number) {
      if (stream.getAudioTracks().length === 0) return;
      const audio = parts.current.audio ?? new AudioContext();
      parts.current.audio = audio;
      if (audio.state !== "running") await audio.resume();
      await audio.audioWorklet.addModule(new URL("./pcm-worklet.js", import.meta.url));
      if (generation.current !== token) {
        void audio.close();
        return;
      }
      const source = audio.createMediaStreamSource(stream);
      const node = new AudioWorkletNode(audio, "pcm-chunker");
      node.port.onmessage = (event: MessageEvent<ArrayBuffer>) => {
        if (generation.current !== token || socket.readyState !== WebSocket.OPEN) return;
        // Half-duplex only while our own line is playing. The browser's
        // speechSynthesis.speaking flag can stick on and mute the mic for good.
        if (isSpeaking.current?.()) return;
        socket.send(join(new Uint8Array([2]).buffer, event.data));
      };
      source.connect(node);
      // The node must reach the destination to keep running; a muted gain keeps it silent.
      const mute = audio.createGain();
      mute.gain.value = 0;
      node.connect(mute).connect(audio.destination);
    }
  }, [stop]);

  useEffect(() => stop, [stop]);

  return { videoRef, status, error, start, stop };
}

function join(head: ArrayBuffer, body: ArrayBuffer): ArrayBuffer {
  const out = new Uint8Array(head.byteLength + body.byteLength);
  out.set(new Uint8Array(head), 0);
  out.set(new Uint8Array(body), head.byteLength);
  return out.buffer;
}
