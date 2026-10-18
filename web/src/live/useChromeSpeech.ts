import { useEffect, useRef } from "react";
import type { ClientMessage } from "../stream/events";

// Chrome streams the mic and returns words while the person is still talking.
// Final phrases go to the harness as the utterance the decision reasons on.

type SpeechPhrase = { isFinal: boolean; 0: { transcript: string } };

type SpeechListener = {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  onresult: ((event: { resultIndex: number; results: ArrayLike<SpeechPhrase> }) => void) | null;
  onend: (() => void) | null;
  onerror: ((event: { error: string }) => void) | null;
  start: () => void;
  stop: () => void;
};

type SpeechCtor = new () => SpeechListener;

function speechCtor(): SpeechCtor | null {
  const browser = window as Window & {
    SpeechRecognition?: SpeechCtor;
    webkitSpeechRecognition?: SpeechCtor;
  };
  return browser.SpeechRecognition ?? browser.webkitSpeechRecognition ?? null;
}

export function useChromeSpeech(
  enabled: boolean,
  send: (message: ClientMessage) => void,
  isSpeaking: () => boolean,
) {
  const sendRef = useRef(send);
  const speakingRef = useRef(isSpeaking);
  sendRef.current = send;
  speakingRef.current = isSpeaking;

  useEffect(() => {
    if (!enabled) return;
    const Ctor = speechCtor();
    if (!Ctor) return;
    const recognition = new Ctor();
    recognition.continuous = true;
    recognition.interimResults = false;
    recognition.lang = "en-US";
    let stopped = false;

    recognition.onresult = (event) => {
      if (speakingRef.current()) return;
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const phrase = event.results[i];
        if (!phrase.isFinal) continue;
        const text = phrase[0].transcript.trim();
        if (text) sendRef.current({ type: "speech", text });
      }
    };
    recognition.onerror = (event) => {
      if (event.error === "not-allowed" || event.error === "audio-capture") stopped = true;
    };
    recognition.onend = () => {
      if (stopped) return;
      try {
        recognition.start();
      } catch {
        // Chrome throws if start() races a stop. The next onend retries.
      }
    };
    try {
      recognition.start();
    } catch {
      return;
    }
    return () => {
      stopped = true;
      recognition.onend = null;
      recognition.stop();
    };
  }, [enabled]);
}
