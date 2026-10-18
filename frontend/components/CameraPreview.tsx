"use client";

import { useEffect, useRef } from "react";
import { useSession } from "@/providers/FluffySessionProvider";

export default function CameraPreview() {
  const session = useSession();
  const ref = useRef<HTMLVideoElement>(null);
  const { sensing, stream } = session;

  useEffect(() => {
    const video = ref.current;
    if (!video) return;
    video.srcObject = stream;
    if (stream) void video.play().catch(() => undefined);
    return () => {
      video.srcObject = null;
    };
  }, [stream]);

  const camera = sensing.camera === "on" ? "Camera on" : sensing.camera === "denied" ? "Permission denied" : sensing.camera === "absent" ? "No camera" : sensing.camera === "error" ? "Camera error" : sensing.camera === "starting" ? "Starting camera" : "Camera off";
  const transport = sensing.transport === "reaching" ? "Media reaching backend" : sensing.transport === "offline" ? "Transport offline" : sensing.transport === "unsupported" ? "Media route unavailable" : sensing.transport === "connecting" ? "Connecting media" : "Media idle";
  const analyzed = sensing.lastAnalyzedAt ? `Last analyzed ${formatTime(sensing.lastAnalyzedAt)}` : "Not analyzed yet";

  return (
    <section className="overflow-hidden rounded-2xl border border-neutral-200 bg-white" aria-label="Camera preview">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-neutral-100 px-4 py-2 text-xs text-neutral-500">
        <span>{camera}</span>
        <span aria-hidden>·</span>
        <span>{transport}</span>
        <span aria-hidden>·</span>
        <span>{analyzed}</span>
        {sensing.delayed ? <span className="text-amber-700">Processing delayed</span> : null}
      </div>
      <div className="relative aspect-video bg-neutral-50">
        <video ref={ref} autoPlay muted playsInline className="h-full w-full object-contain" />
        {sensing.camera !== "on" ? (
          <p className="absolute inset-0 flex items-center justify-center px-6 text-center text-sm text-neutral-500">
            {sensing.error ?? "Start Fluffy to open the camera. The preview stays off until you do."}
          </p>
        ) : null}
      </div>
      {sensing.camera === "on" && sensing.mic !== "off" ? (
        <div className="flex justify-end px-3 py-2">
          <button
            type="button"
            onClick={() => session.muteMic(sensing.mic !== "muted")}
            className="h-9 rounded-xl px-3 text-xs font-medium text-neutral-600 hover:bg-neutral-50"
          >
            {sensing.mic === "muted" ? "Unmute microphone" : sensing.mic === "denied" ? "Microphone denied" : "Mute microphone"}
          </button>
        </div>
      ) : null}
    </section>
  );
}

function formatTime(value: number): string {
  return new Date(value).toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" });
}
