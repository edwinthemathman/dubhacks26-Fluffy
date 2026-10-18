import type { AvatarState } from "../../stream/events";

export function FluffyAvatar({ state, size = 88 }: { state: AvatarState; size?: number }) {
  return (
    <div className={`avatar avatar-${state}`} style={{ width: size, height: size }} aria-hidden="true">
      <svg viewBox="0 0 120 120">
        <ellipse cx="60" cy="78" rx="34" ry="8" fill="#0c4dad" opacity="0.12" />
        <path
          d="M60 18c14 0 22 10 24 20 10 1 18 9 18 20 0 12-9 20-20 21-2 12-12 21-22 21s-20-9-22-21c-11-1-20-9-20-21 0-11 8-19 18-20 2-10 10-20 24-20z"
          fill="white"
          stroke="#0c4dad"
          strokeWidth="3"
        />
        <circle cx="48" cy="58" r="4" fill="#0d2744" />
        <circle cx="74" cy="58" r="4" fill="#0d2744" />
        <path d="M50 72c4 6 16 6 20 0" fill="none" stroke="#1a6fe8" strokeWidth="3" strokeLinecap="round" />
        {state === "hearing" && (
          <path d="M92 52c8 4 8 16 0 20" fill="none" stroke="#1a6fe8" strokeWidth="3" strokeLinecap="round" />
        )}
        {state === "seeing" && <circle cx="60" cy="34" r="4" fill="#1a6fe8" />}
      </svg>
      <span className="avatar-badge">?</span>
      <span className="dots">
        <i />
        <i />
        <i />
      </span>
    </div>
  );
}
