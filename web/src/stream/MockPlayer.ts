import type { FluffyEvent, TimedEvent } from "./events";

export function storageKey(scene: string): string {
  return `fluffy.mock.${scene}`;
}

export function readOrCreateStart(scene: string): number {
  const existing = localStorage.getItem(storageKey(scene));
  const parsed = existing ? Number(existing) : NaN;
  if (!Number.isNaN(parsed) && parsed > 0) return parsed;
  const now = Date.now();
  localStorage.setItem(storageKey(scene), String(now));
  return now;
}

export function restartStart(scene: string): number {
  const now = Date.now();
  localStorage.setItem(storageKey(scene), String(now));
  return now;
}

export function playTimeline(
  events: TimedEvent[],
  startAt: number,
  onEvent: (event: FluffyEvent) => void,
): () => void {
  const elapsed = Date.now() - startAt;
  const timers: number[] = [];
  for (const item of events) {
    const delay = item.t - elapsed;
    if (delay <= 0) onEvent(item.event);
    else timers.push(window.setTimeout(() => onEvent(item.event), delay));
  }
  return () => {
    for (const timer of timers) window.clearTimeout(timer);
  };
}
