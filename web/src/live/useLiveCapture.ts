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

export function useLiveCapture(options?: { isSpeaking?: () => boolean }) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [status, setStatus] = useState<LiveStatus>("off");
  const [error, setError] = useState<string | null>(null);
  const parts = useRef<{
    stream?: MediaStream;
    socket?: WebSocket;
    timer?: number;
    audio?: AudioContext;
    previous?: Uint8ClampedArray;
  }>({});

  const stop = useCallback(() => {
    const p = parts.current;
    if (p.timer) window.clearInterval(p.timer);
    if (p.socket && p.socket.readyState === WebSocket.OPEN) {
      p.socket.send(JSON.stringify({ type: "live.pause", paused: true }));
    }
    p.socket?.close();
    p.stream?.getTracks().forEach((track) => track.stop());
    void p.audio?.close();
    if (videoRef.current) videoRef.current.srcObject = null;
    parts.current = {};
    setStatus("off");
  }, []);

  const start = useCallback(async () => {
    if (parts.current.stream) return;
    setStatus("starting");
    setError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { width: { ideal: 1280 }, height: { ideal: 720 } },
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
      });
      parts.current.stream = stream;
      const video = videoRef.current;
      if (video) {
        video.srcObject = stream;
        await video.play().catch(() => undefined);
      }

      const protocol = window.location.protocol === "https:" ? "wss" : "ws";
      const socket = new WebSocket(`${protocol}://${window.location.host}/ws/live`);
      socket.binaryType = "arraybuffer";
      parts.current.socket = socket;
      socket.onopen = () => {
        socket.send(JSON.stringify({ type: "live.pause", paused: false }));
        setStatus("live");
      };
      socket.onerror = () => {
        setError("Can't reach the Fluffy harness. Is `uv run fluffy-live` running?");
        setStatus("error");
      };

      startFrames(stream, socket);
      await startAudio(stream, socket);
    } catch (err) {
      const name = err instanceof DOMException ? err.name : "";
      setError(
        name === "NotAllowedError"
          ? "Camera or microphone permission was denied. Allow it in the browser's address bar."
          : name === "NotFoundError"
            ? "No camera or microphone was found."
            : `Couldn't start the camera (${String(err)}).`,
      );
      stop();
      setStatus("error");
    }
  }, [stop]);

  function startFrames(stream: MediaStream, socket: WebSocket) {
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

    parts.current.timer = window.setInterval(() => {
      const video = videoRef.current;
      if (!video || !frameCtx || !smallCtx || video.readyState < 2) return;
      if (socket.readyState !== WebSocket.OPEN || socket.bufferedAmount > MAX_BUFFERED) return;
      const ts = Date.now();
      smallCtx.drawImage(video, 0, 0, small.width, small.height);
      const motion = motionScore(smallCtx.getImageData(0, 0, small.width, small.height).data);
      frameCtx.drawImage(video, 0, 0, frame.width, frame.height);
      frame.toBlob(
        (blob) => {
          if (!blob || socket.readyState !== WebSocket.OPEN) return;
          void blob.arrayBuffer().then((jpeg) => {
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

  async function startAudio(stream: MediaStream, socket: WebSocket) {
    if (stream.getAudioTracks().length === 0) return;
    const audio = new AudioContext();
    parts.current.audio = audio;
    await audio.audioWorklet.addModule(new URL("./pcm-worklet.js", import.meta.url));
    const source = audio.createMediaStreamSource(stream);
    const node = new AudioWorkletNode(audio, "pcm-chunker");
    node.port.onmessage = (event: MessageEvent<ArrayBuffer>) => {
      if (socket.readyState !== WebSocket.OPEN) return;
      // Half-duplex: don't send Fluffy's own voice back to Fluffy.
      if (options?.isSpeaking?.()) return;
      if ("speechSynthesis" in window && window.speechSynthesis.speaking) return;
      socket.send(join(new Uint8Array([2]).buffer, event.data));
    };
    source.connect(node);
    // The node must reach the destination to keep running; a muted gain keeps it silent.
    const mute = audio.createGain();
    mute.gain.value = 0;
    node.connect(mute).connect(audio.destination);
  }

  useEffect(() => stop, [stop]);

  return { videoRef, status, error, start, stop };
}

function join(head: ArrayBuffer, body: ArrayBuffer): ArrayBuffer {
  const out = new Uint8Array(head.byteLength + body.byteLength);
  out.set(new Uint8Array(head), 0);
  out.set(new Uint8Array(body), head.byteLength);
  return out.buffer;
}
