import { useEffect, useRef, useState } from "react";
import type { HomeState } from "../../stream/events";
import styles from "./Room.module.css";

export function Room({ home, onVolume }: { home: HomeState; onVolume?: (volume: number) => void }) {
  const glow = Math.max(0.08, home.lamp.luminosity / 100);
  const [volume, setVolume] = useState(home.speaker.volume);
  const dragging = useRef(false);
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => {
    if (!dragging.current) setVolume(home.speaker.volume);
  }, [home.speaker.volume]);
  useEffect(() => () => window.clearTimeout(timer.current), []);

  const sendVolume = (next: number) => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => onVolume?.(next), 180);
  };
  const shade = home.lamp.warmth === "cool" ? "#9ec4ff" : home.lamp.warmth === "neutral" ? "#d5e6ff" : "#eef4ff";
  const slatShift = (100 - home.blinds) * 0.42;

  return (
    <figure className={`${styles.room} ${home.flash.active ? styles.flash : ""}`}>
      <svg viewBox="0 0 640 420" role="img" aria-label="Fluffy's living room">
        <rect width="640" height="420" fill="#eef5fc" />
        <polygon points="0,250 640,250 640,420 0,420" fill="#d5e4f6" />
        <rect x="250" y="48" width="150" height="130" rx="8" fill="#c5ddf8" stroke="#0c4dad" strokeWidth="4" />
        <g transform={`translate(0 ${slatShift})`}>
          {Array.from({ length: 6 }, (_, index) => (
            <rect key={index} x="262" y={60 + index * 18} width="126" height="8" rx="2" fill="#f7fbff" opacity="0.92" />
          ))}
        </g>
        <rect x="70" y="188" width="210" height="78" rx="28" fill="white" stroke="#0c4dad" strokeWidth="4" />
        <rect x="86" y="250" width="16" height="28" fill="#0c4dad" />
        <rect x="248" y="250" width="16" height="28" fill="#0c4dad" />
        <rect x="150" y="230" width="92" height="14" rx="4" fill="white" stroke="#0c4dad" strokeWidth="3" />
        <circle cx="176" cy="222" r="5" fill="#1a6fe8" />
        <circle cx="196" cy="222" r="4" fill="#0c4dad" />
        <ellipse cx="500" cy="300" rx={70 * glow + 20} ry={28 * glow + 8} fill="#1a6fe8" opacity={0.18 + glow * 0.35} />
        <rect x="492" y="168" width="16" height="120" fill="#0c4dad" />
        <path d="M470 176h60l-8 28h-44z" fill={shade} stroke="#0c4dad" strokeWidth="3" />
        <rect x="430" y="250" width="46" height="36" rx="6" fill="white" stroke="#0c4dad" strokeWidth="3" />
        {home.speaker.sound && (
          <g className={styles.waves}>
            <rect x="438" y="258" width="5" height="18" fill="#1a6fe8" />
            <rect x="448" y="254" width="5" height="26" fill="#1a6fe8" />
            <rect x="458" y="258" width="5" height="18" fill="#1a6fe8" />
          </g>
        )}
      </svg>
      <figcaption className={styles.meta}>
        <span>Lamp {home.lamp.luminosity}% · {home.lamp.warmth}</span>
        <span>Blinds {home.blinds}%</span>
        <span>{home.speaker.sound ? home.speaker.sound : "Quiet"}</span>
      </figcaption>
      <label className={styles.volume}>
        Volume {volume}%
        <input
          type="range"
          min={0}
          max={100}
          value={volume}
          aria-label="Spotify volume"
          onPointerDown={() => {
            dragging.current = true;
          }}
          onPointerUp={(event) => {
            dragging.current = false;
            const next = Number(event.currentTarget.value);
            window.clearTimeout(timer.current);
            setVolume(next);
            onVolume?.(next);
          }}
          onChange={(event) => {
            const next = Number(event.target.value);
            setVolume(next);
            sendVolume(next);
          }}
        />
      </label>
      {(home.caption || home.flash.active) && (
        <p className={styles.caption} role="status">
          {home.caption ?? `Lights pulse ${home.flash.color ?? "blue"}`}
        </p>
      )}
    </figure>
  );
}
