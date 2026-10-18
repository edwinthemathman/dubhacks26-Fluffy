import { useEffect } from "react";
import type { ClientMessage } from "../stream/events";

// Plays a voiced `say` from /api/voice/{id}. A 204, a network error, or a
// blocked autoplay falls back to the browser's built-in speech.

const SILENT_WAV =
  "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA=";

type Send = (message: ClientMessage) => void;

export function useVoice({
  enabled,
  sayId,
  voice,
  text,
  rate,
  send,
  speakingRef,
}: {
  enabled: boolean;
  sayId: string;
  voice: boolean;
  text: string;
  rate: number;
  send: Send;
  speakingRef: { current: boolean };
}) {
  const unlock = () => {
    const clip = new Audio(SILENT_WAV);
    void clip.play().catch(() => undefined);
  };

  useEffect(() => {
    if (!enabled || !voice || !sayId || !text) return;
    let stopped = false;
    let started = false;
    let fellBack = false;
    const audio = new Audio(`/api/voice/${sayId}`);

    const mark = (speaking: boolean) => {
      speakingRef.current = speaking;
    };
    const startedSpeak = () => {
      if (stopped || started) return;
      started = true;
      mark(true);
      send({ type: "tts.start" });
    };
    const finished = () => {
      if (!started) return;
      started = false;
      mark(false);
      send({ type: "tts.end" });
    };
    const fallback = () => {
      if (stopped || started || fellBack) return;
      fellBack = true;
      audio.pause();
      speakBuiltin(text, rate, send, speakingRef, () => stopped);
    };

    audio.onplaying = startedSpeak;
    audio.onended = finished;
    audio.onerror = fallback;
    void audio.play().then(
      () => undefined,
      () => fallback(),
    );

    return () => {
      stopped = true;
      audio.pause();
      audio.src = "";
      if (started) finished();
      if ("speechSynthesis" in window) window.speechSynthesis.cancel();
    };
  }, [enabled, voice, sayId, text, rate, send, speakingRef]);

  return { isSpeaking: () => speakingRef.current, unlock };
}

function speakBuiltin(
  text: string,
  rate: number,
  send: Send,
  speakingRef: { current: boolean },
  stopped: () => boolean,
) {
  if (!("speechSynthesis" in window)) return;
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.rate = rate;
  utterance.onstart = () => {
    if (stopped()) return;
    speakingRef.current = true;
    send({ type: "tts.start" });
  };
  const done = () => {
    speakingRef.current = false;
    send({ type: "tts.end" });
  };
  utterance.onend = done;
  utterance.onerror = done;
  window.speechSynthesis.speak(utterance);
}
