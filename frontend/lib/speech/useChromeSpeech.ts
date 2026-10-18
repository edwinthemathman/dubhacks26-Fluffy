import { useEffect, useRef } from "react";

// Chrome's speech recognizer is the only transcript. Final phrases are plain text.

export type SpeechPhase = {
  listening: boolean;
  phrase: string;
  error: string | null;
};

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
  if (typeof window === "undefined") return null;
  const browser = window as Window & {
    SpeechRecognition?: SpeechCtor;
    webkitSpeechRecognition?: SpeechCtor;
  };
  return browser.SpeechRecognition ?? browser.webkitSpeechRecognition ?? null;
}

const MISSING = "This browser has no Chrome speech recognition. You can still type.";
const DENIED = "Microphone speech recognition was denied. You can still type.";

export function useChromeSpeech(
  enabled: boolean,
  onFinal: (text: string) => void,
  onStatus: (phase: SpeechPhase) => void,
  isSpeaking: () => boolean,
) {
  const finalRef = useRef(onFinal);
  const statusRef = useRef(onStatus);
  const speakingRef = useRef(isSpeaking);
  const phraseRef = useRef("");

  useEffect(() => {
    finalRef.current = onFinal;
    statusRef.current = onStatus;
    speakingRef.current = isSpeaking;
  }, [onFinal, onStatus, isSpeaking]);

  useEffect(() => {
    if (!enabled) {
      statusRef.current({ listening: false, phrase: phraseRef.current, error: null });
      return;
    }
    const Ctor = speechCtor();
    if (!Ctor) {
      statusRef.current({ listening: false, phrase: phraseRef.current, error: MISSING });
      return;
    }
    const recognition = new Ctor();
    recognition.continuous = true;
    recognition.interimResults = false;
    recognition.lang = "en-US";
    let stopped = false;

    recognition.onresult = (event) => {
      if (speakingRef.current()) return;
      for (let index = event.resultIndex; index < event.results.length; index += 1) {
        const phrase = event.results[index];
        if (!phrase.isFinal) continue;
        const text = phrase[0].transcript.trim();
        if (!text) continue;
        phraseRef.current = text;
        statusRef.current({ listening: true, phrase: text, error: null });
        finalRef.current(text);
      }
    };
    recognition.onerror = (event) => {
      if (event.error === "not-allowed" || event.error === "audio-capture") {
        stopped = true;
        statusRef.current({ listening: false, phrase: phraseRef.current, error: DENIED });
      }
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
      statusRef.current({ listening: true, phrase: phraseRef.current, error: null });
    } catch {
      statusRef.current({ listening: false, phrase: phraseRef.current, error: MISSING });
      return;
    }
    return () => {
      stopped = true;
      recognition.onend = null;
      recognition.stop();
    };
  }, [enabled]);
}
