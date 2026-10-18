export type SpeechRequest = {
  messageId: string;
  audioUrl: string;
};

export type PlaybackStatus = "played" | "failed";

/**
 * One HTMLAudio element. Message ids are remembered so a route change or
 * reconnect cannot replay them. A missing or failed clip is not success.
 */
export class SpeechPlayer {
  private audio: HTMLAudioElement | null = null;
  private playingId: string | null = null;
  private readonly played = new Set<string>();
  private queue: SpeechRequest[] = [];
  private generation = 0;

  constructor(private readonly onStatus: (messageId: string, status: PlaybackStatus) => void) {}

  get speaking(): boolean {
    return this.playingId != null;
  }

  enqueue(item: SpeechRequest): void {
    if (this.played.has(item.messageId) || this.playingId === item.messageId) return;
    if (this.queue.some((queued) => queued.messageId === item.messageId)) return;
    this.queue.push(item);
    this.pump();
  }

  /** Drop queued audio without marking it played. Used when the session stops. */
  clearPending(): void {
    this.queue = [];
  }

  stop(): void {
    this.generation += 1;
    this.queue = [];
    this.playingId = null;
    if (this.audio) {
      this.audio.pause();
      this.audio.src = "";
      this.audio = null;
    }
  }

  private pump(): void {
    if (this.playingId || this.queue.length === 0) return;
    const next = this.queue.shift();
    if (!next) return;
    if (this.played.has(next.messageId)) {
      this.pump();
      return;
    }
    const generation = this.generation;
    const audio = new Audio(next.audioUrl);
    this.audio = audio;
    this.playingId = next.messageId;
    const finish = (status: PlaybackStatus) => {
      if (generation !== this.generation || this.playingId !== next.messageId) return;
      this.played.add(next.messageId);
      this.playingId = null;
      this.audio = null;
      this.onStatus(next.messageId, status);
      this.pump();
    };
    audio.onended = () => finish("played");
    audio.onerror = () => finish("failed");
    void audio.play().catch(() => finish("failed"));
  }
}
