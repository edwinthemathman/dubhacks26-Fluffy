import { activeFlashes } from "../lib/reducer";
import { AppState } from "../lib/types";

const FLASH_FILL = { blue: "#4C7DFF", amber: "#F0A202", white: "#F7F4EA" };

export function HomeSvg({ state, nowMs }: { state: AppState; nowMs: number }) {
  const floor = state.lights.find((light) => light.id === "lamp.living_floor");
  const ceiling = state.lights.find((light) => light.id === "light.living_ceiling");
  const blind = state.blinds[0];
  const cover = blind ? 150 * (1 - blind.position / 100) : 0;
  const flashes = activeFlashes(state, nowMs);
  const floorFlash = flashes.find((flash) => flash.light_ids.includes("lamp.living_floor"));
  const ceilingFlash = flashes.find((flash) => flash.light_ids.includes("light.living_ceiling"));
  const media = state.media[0];

  return (
    <div className="home-layout">
      <svg className="room-svg" viewBox="0 0 360 280" role="img" aria-label="Simulated living room">
        <rect x="0" y="0" width="360" height="280" rx="16" fill="#f7f1e6" />
        <rect x="40" y="36" width="120" height="150" fill="#d7e4ea" stroke="#241c16" />
        <rect x="40" y="36" width="120" height={cover} fill="#6f7c86" />
        <text x="48" y="24" fontSize="12">Blinds {blind ? blind.position : 0}%</text>
        {ceiling ? <Lamp x={230} y={48} light={ceiling} flash={ceilingFlash?.color} pulses={ceilingFlash?.pulses} /> : null}
        {floor ? <Lamp x={250} y={168} light={floor} flash={floorFlash?.color} pulses={floorFlash?.pulses} /> : null}
        <rect x="70" y="210" width="90" height="36" rx="8" fill="#c9845a" />
        <text x="78" y="232" fontSize="12">sofa</text>
      </svg>
      <div>
        <p className="simulated">Simulated devices. The drawing follows the output service, not a local timer.</p>
        {state.lights.map((light) => (
          <p className="lamp-readout" key={light.id}>
            {light.label}: {light.luminosity}% · {light.warmth}
          </p>
        ))}
        <div className="player">
          <span className="simulated">{mediaLabel(media)}</span>
          <strong>{mediaHeadline(media)}</strong>
          {media?.error_code ? <span className="tone-bad">{media.error_code}</span> : null}
        </div>
        {state.alerts.map((alert) => (
          <div className="alert" key={alert.incident_id + alert.status}>
            <span>{alert.severity === "high" ? "High" : "Info"} · {alertLabel(alert.status)}</span>
            <span className={alertTone(alert.status, alert.verification)}>{alert.verification}</span>
          </div>
        ))}
        {state.incident ? (
          <p>
            Check-in {state.incident.phase}
            {state.incident.audio_failed ? " · audio unavailable" : ""}
          </p>
        ) : null}
      </div>
    </div>
  );
}

function Lamp({
  x,
  y,
  light,
  flash,
  pulses,
}: {
  x: number;
  y: number;
  light: { color: string; luminosity: number; label: string };
  flash?: "blue" | "amber" | "white";
  pulses?: number;
}) {
  const glow = light.luminosity / 100;
  return (
    <g>
      <circle cx={x} cy={y} r="28" fill={light.color} opacity={Math.max(0.08, glow)} />
      <circle cx={x} cy={y} r="10" fill={light.luminosity === 0 ? "#241c16" : light.color} stroke="#241c16" />
      <text x={x - 28} y={y + 48} fontSize="12">{light.luminosity}%</text>
      {flash ? (
        <circle
          className="flash-overlay"
          cx={x}
          cy={y}
          r="34"
          fill={FLASH_FILL[flash]}
          style={{ ["--pulses" as string]: String(pulses ?? 2) }}
        />
      ) : null}
    </g>
  );
}

function mediaLabel(media: AppState["media"][number] | undefined) {
  if (!media || media.provider === "none") return "Speaker idle";
  if (media.provider === "local") return "Local notification";
  return media.simulated ? "Simulated speaker" : "Spotify";
}

function mediaHeadline(media: AppState["media"][number] | undefined) {
  if (!media || media.playback === "idle") return "No sound";
  const title = media.title ?? "Audio";
  if (media.playback === "requesting") return `Requesting ${title}`;
  if (media.playback === "playing") return `Playing ${title}`;
  if (media.playback === "paused") return `Paused ${title}`;
  return `Error · ${title}`;
}

function alertLabel(status: string) {
  if (status === "accepted") return "Submitted";
  if (status === "delivered") return "Delivered";
  if (status === "submitting") return "Submitting";
  if (status === "failed") return "Failed";
  if (status === "cancelled") return "Cancelled";
  return "Unknown";
}

function alertTone(status: string, verification: string) {
  if (verification === "provider_delivered") return "tone-good";
  if (status === "failed" || status === "cancelled") return "tone-bad";
  if (status === "accepted" || status === "submitting" || verification === "provider_accepted") return "tone-pending";
  return "tone-unknown";
}
