"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { SpeechPlayer } from "@/lib/audio/player";
import { getJson, healthUrl, postJson, questionUrl, sessionUrl, stateUrl, stopUrl, textInputUrl, toolsUrl, transcriptUrl } from "@/lib/fluffy/client";
import { readPublicConfig, type FluffyPublicConfig } from "@/lib/fluffy/config";
import { normalizeEvent } from "@/lib/fluffy/normalizeEvent";
import { MASCOT_MS } from "@/lib/fluffy/mascot";
import { thinkingLabel, type SensingFacts } from "@/lib/fluffy/phrases";
import { sceneToRecorded } from "@/lib/fluffy/recorded";
import { createSession, reduceSession } from "@/lib/fluffy/reducer";
import type { CanonicalEvent, SessionState } from "@/lib/fluffy/types";
import { CaptureController, type CapturePhase } from "@/lib/media/capture";
import { useChromeSpeech, type SpeechPhase } from "@/lib/speech/useChromeSpeech";
import { matchScene } from "@/lib/scenes";

type SessionApi = {
  config: FluffyPublicConfig;
  state: SessionState;
  draft: string;
  setDraft: (value: string) => void;
  recordedDemo: boolean;
  setRecordedDemo: (on: boolean) => void;
  submitting: boolean;
  answering: boolean;
  paused: boolean;
  sensing: CapturePhase;
  sensingFacts: SensingFacts;
  stageLabel: string;
  greeting: boolean;
  stream: MediaStream | null;
  now: number;
  submitText: () => Promise<void>;
  scanRoom: () => void;
  saveMoment: () => void;
  answerQuestion: (choice: string) => Promise<void>;
  confirmRecorded: (requestId: string) => void;
  startFluffy: () => Promise<void>;
  pauseFluffy: () => void;
  stopFluffy: () => Promise<void>;
  muteMic: (muted: boolean) => void;
  registerCaptureVideo: (video: HTMLVideoElement | null) => void;
  speech: SpeechPhase;
};

const SessionContext = createContext<SessionApi | null>(null);

const idleCapture: CapturePhase = {
  camera: "off",
  mic: "off",
  transport: "idle",
  lastAnalyzedAt: null,
  delayed: false,
  error: null,
};

export function FluffySessionProvider({ children }: { children: ReactNode }) {
  const config = useMemo(() => readPublicConfig(), []);
  const [state, setState] = useState<SessionState>(() => createSession(createId("ses")));
  const [draft, setDraft] = useState("");
  const [recordedDemo, setRecordedDemo] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [answering, setAnswering] = useState(false);
  const [paused, setPaused] = useState(false);
  const [sensing, setSensing] = useState<CapturePhase>(idleCapture);
  const [stream, setStream] = useState<MediaStream | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [started, setStarted] = useState(false);
  const [greeting, setGreeting] = useState(false);
  const [speech, setSpeech] = useState<SpeechPhase>({ listening: false, phrase: "", error: null });
  const stateRef = useRef(state);
  const socketRef = useRef<WebSocket | null>(null);
  const captureRef = useRef<CaptureController | null>(null);
  const playerRef = useRef<SpeechPlayer | null>(null);
  const videoNode = useRef<HTMLVideoElement | null>(null);
  const clientIdRef = useRef(createId("cli"));
  const retryRef = useRef(0);
  useEffect(() => {
    stateRef.current = state;
  }, [state]);

  const apply = useCallback((event: CanonicalEvent, historical = false) => {
    setState((current) => reduceSession(current, event, { historical, now: Date.now() }));
  }, []);

  const ingest = useCallback((raw: unknown, historical: boolean) => {
    for (const event of normalizeEvent(raw, { now: Date.now() })) {
      if (event.type === "stage" && event.analyzed) {
        captureRef.current?.markAnalyzed();
        apply({ type: "capability", name: "inference", available: true, detail: "Perceptron returned a scene read." });
      }
      if (event.type === "observation" || (event.type === "turn.start" && event.source !== "text" && event.source !== "recorded")) {
        apply({ type: "capability", name: "inference", available: true, detail: "The backend sent a perception event." });
      }
      apply(event, historical);
    }
  }, [apply]);

  useEffect(() => {
    const capture = new CaptureController(config);
    captureRef.current = capture;
    capture.setSpeechActive(() => playerRef.current?.speaking === true);
    const unsubscribe = capture.subscribe((phase) => {
      setSensing(phase);
      setStream(capture.previewStream());
      if (phase.transport === "unsupported") {
        apply({ type: "capability", name: "media", available: false, detail: phase.error ?? "The media route is missing." });
      } else if (phase.transport === "reaching") {
        apply({ type: "capability", name: "media", available: true, detail: "Media is reaching the backend. That alone does not mean a model has looked at it." });
      }
    });
    const player = new SpeechPlayer((messageId, status) => {
      const shown = status === "started" ? "playing" : status === "ended" ? "played" : "failed";
      apply({ type: "speech", messageId, status: shown });
      if (status !== "started") capture.noteSpeechEnded();
      void postJson(`${config.httpBase}/api/audio/ack`, {
        session_id: stateRef.current.sessionId,
        message_id: messageId,
        client_id: clientIdRef.current,
        status,
      });
    });
    playerRef.current = player;
    return () => {
      unsubscribe();
      player.stop();
      void capture.stop();
    };
  }, [apply, config]);

  useEffect(() => {
    const player = playerRef.current;
    if (!player) return;
    for (const item of state.speechQueue) {
      if (item.audioUrl && item.permitAudio) player.enqueue({ messageId: item.messageId, audioUrl: resolveHarnessUrl(config.httpBase, item.audioUrl) });
    }
  }, [config.httpBase, state.speechQueue]);

  useEffect(() => {
    const flash = state.home.flash;
    const alert = state.home.caregiver;
    const active = (flash != null && Date.now() < flash.endedAt) || (alert != null && Date.now() < alert.animateUntil);
    if (!active) return;
    const timer = window.setInterval(() => setNow(Date.now()), 120);
    return () => window.clearInterval(timer);
  }, [state.home.flash, state.home.caregiver]);

  useEffect(() => {
    let timer = 0;
    let buffer: unknown[] = [];
    let cancelled = false;
    const connect = () => {
      if (cancelled) return;
      apply({ type: "connection", status: retryRef.current === 0 ? "connecting" : "reconnecting" });
      const socket = new WebSocket(`${config.wsBase}/ws/ui`);
      socketRef.current = socket;
      socket.onopen = () => {
        retryRef.current = 0;
        buffer = [];
        apply({ type: "connection", status: "open" });
        apply({ type: "notice", text: null });
      };
      socket.onmessage = (message) => {
        const raw = parseMessage(message.data);
        if (raw == null) return;
        if (typeof raw === "object" && raw && (raw as { type?: string }).type === "still") {
          captureRef.current?.grabStill();
        }
        buffer.push(raw);
        window.clearTimeout(timer);
        timer = window.setTimeout(() => {
          const pending = buffer;
          buffer = [];
          for (const item of pending) ingest(item, false);
        }, 0);
      };
      socket.onerror = () => {
        apply({ type: "notice", text: "The UI connection could not reach the backend." });
      };
      socket.onclose = () => {
        if (cancelled) return;
        apply({ type: "connection", status: "reconnecting" });
        const wait = Math.min(8000, 500 * 2 ** retryRef.current);
        retryRef.current += 1;
        timer = window.setTimeout(connect, wait);
      };
    };
    void (async () => {
      const session = await getJson(sessionUrl(config));
      if (cancelled) return;
      const sessionBody = session.body && typeof session.body === "object" ? (session.body as { session_id?: unknown }) : null;
      const sessionId = typeof sessionBody?.session_id === "string" ? sessionBody.session_id : null;
      if (session.ok && sessionId) {
        setState((current) => (current.sessionId === sessionId ? current : { ...current, sessionId }));
      }
      const snapshot = await getJson(stateUrl(config));
      if (cancelled) return;
      if (snapshot.ok && snapshot.body && typeof snapshot.body === "object") {
        ingest({ type: "home.snapshot", ...(snapshot.body as Record<string, unknown>) }, false);
      } else if (!snapshot.ok && snapshot.status !== 0) {
        apply({ type: "notice", text: "Home state is unavailable." });
      }
      connect();
    })();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
      socketRef.current?.close();
    };
  }, [apply, config, ingest]);

  useEffect(() => {
    void getJson(healthUrl(config)).then((result) => {
      if (!result.ok) apply({ type: "notice", text: "The harness health check failed. Text will not be treated as a completed action." });
    });
    void getJson(toolsUrl(config));
  }, [apply, config]);

  const submitText = useCallback(async () => {
    const text = draft.trim();
    if (!text || submitting) return;
    if (recordedDemo) {
      const scene = matchScene(text);
      if (!scene) {
        apply({ type: "notice", text: "Recorded demo has no scene for that line. Nothing was sent." });
        return;
      }
      apply(sceneToRecorded(scene, createId("req"), text));
      setDraft("");
      return;
    }
    const requestId = createId("req");
    if (config.contract === "legacy-live") {
      const socket = socketRef.current;
      if (!socket || socket.readyState !== WebSocket.OPEN) {
        apply({ type: "notice", text: "The legacy UI socket is not connected. The draft was kept." });
        return;
      }
      apply({ type: "submit", requestId, text });
      socket.send(JSON.stringify({ type: "typed", text, request_id: requestId }));
      setDraft("");
      return;
    }
    setSubmitting(true);
    apply({ type: "submit", requestId, text });
    const result = await postJson(textInputUrl(config), { session_id: stateRef.current.sessionId, request_id: requestId, text });
    setSubmitting(false);
    if (!result.ok) {
      apply({ type: "capability", name: "text", available: false, detail: result.missing ? "POST /api/input/text is not implemented." : result.detail });
      apply({ type: "submit.failed", requestId, detail: result.missing ? "The backend has no text input route yet. The message was not accepted." : result.detail || "The message was not accepted." });
      return;
    }
    apply({ type: "capability", name: "text", available: true, detail: "Text input accepted." });
    apply({ type: "submit.accepted", requestId });
    setDraft("");
  }, [apply, config, draft, recordedDemo, submitting]);

  const submitTranscript = useCallback(async (phrase: string, confidence?: number) => {
    const text = phrase.trim();
    if (!text || recordedDemo) return;
    const requestId = createId("req");
    apply({ type: "submit", requestId, text });
    const result = await postJson(transcriptUrl(config), {
      session_id: stateRef.current.sessionId,
      request_id: requestId,
      text,
      epoch: captureRef.current?.epoch ?? 0,
      captured_at: new Date().toISOString(),
      final: true,
      source: "browser_speech",
      ...(typeof confidence === "number" ? { confidence } : {}),
    });
    if (!result.ok) {
      apply({ type: "submit.failed", requestId, detail: result.detail || "The transcript was not accepted." });
      return;
    }
    apply({ type: "capability", name: "text", available: true, detail: "Speech transcript accepted." });
    apply({ type: "submit.accepted", requestId });
  }, [apply, config, recordedDemo]);

  const speechEnabled = started && !paused && !recordedDemo && sensing.mic !== "muted";
  useChromeSpeech(speechEnabled, (text, confidence) => void submitTranscript(text, confidence), setSpeech, () => playerRef.current?.speaking === true);

  const answerQuestion = useCallback(async (choice: string) => {
    const question = stateRef.current.question;
    if (!question || answering) return;
    if (config.contract === "legacy-live") {
      const socket = socketRef.current;
      if (!socket || socket.readyState !== WebSocket.OPEN) {
        apply({ type: "notice", text: "The UI socket is not connected, so the answer was not sent." });
        return;
      }
      socket.send(JSON.stringify({ type: "answer", question_id: question.id, choice, by: "button", text: choice }));
      apply({ type: "notice", text: "Answer sent. Waiting for the backend." });
      return;
    }
    setAnswering(true);
    const result = await postJson(questionUrl(config, question.id), {
      answer: choice,
      source: "button",
    });
    setAnswering(false);
    if (!result.ok) {
      apply({
        type: "capability",
        name: "questions",
        available: false,
        detail: result.missing ? "POST /api/questions/{id}/answer is not implemented." : result.detail,
      });
      apply({ type: "notice", text: result.missing ? "The backend has no question route yet. The answer was not sent." : result.detail || "The answer was not accepted." });
      return;
    }
    apply({ type: "capability", name: "questions", available: true, detail: "Answer accepted." });
    apply({ type: "notice", text: "Answer sent. Waiting for the backend." });
  }, [answering, apply, config]);

  useEffect(() => {
    if (!greeting) return;
    const timer = window.setTimeout(() => setGreeting(false), MASCOT_MS.greeting);
    return () => window.clearTimeout(timer);
  }, [greeting]);

  const startFluffy = useCallback(async () => {
    const capture = captureRef.current;
    if (!capture) return;
    if (videoNode.current) capture.attach(videoNode.current);
    await capture.start(stateRef.current.sessionId);
    void postJson(`${config.httpBase}/api/session/speaker`, { client_id: clientIdRef.current });
    setStarted(true);
    setPaused(false);
    setGreeting(true);
    setStream(capture.previewStream());
  }, [config.httpBase]);

  const pauseFluffy = useCallback(() => {
    const capture = captureRef.current;
    if (!capture) return;
    if (paused) {
      capture.resume();
      setPaused(false);
    } else {
      capture.pause();
      setPaused(true);
    }
  }, [paused]);

  const stopFluffy = useCallback(async () => {
    await captureRef.current?.stop();
    playerRef.current?.stop();
    setStream(null);
    setStarted(false);
    setPaused(false);
    setGreeting(false);
    if (config.contract === "execution-v2") {
      const result = await postJson(stopUrl(config), { session_id: stateRef.current.sessionId });
      if (result.missing) {
        apply({ type: "capability", name: "stop", available: false, detail: "POST /api/session/stop is not implemented. Browser devices were released locally." });
      } else if (result.ok) {
        apply({ type: "capability", name: "stop", available: true, detail: "Stop requested." });
      }
    }
  }, [apply, config]);

  const muteMic = useCallback((muted: boolean) => {
    captureRef.current?.mute(muted);
  }, []);

  const registerCaptureVideo = useCallback((video: HTMLVideoElement | null) => {
    videoNode.current = video;
    captureRef.current?.attach(video);
  }, []);

  const sensingFacts: SensingFacts = {
    started: sensing.camera === "on" || sensing.camera === "starting",
    paused,
    preview: sensing.camera === "on",
    cameraOn: sensing.camera === "on",
    transport: sensing.transport,
    inferenceAvailable: state.capabilities.inference === "available" && state.capabilities.media !== "missing",
  };
  const stageLabel = thinkingLabel(state, sensingFacts);

  const api: SessionApi = {
    config,
    state,
    draft,
    setDraft,
    recordedDemo,
    setRecordedDemo: (on) => {
      setRecordedDemo(on);
      apply({ type: "notice", text: on ? "Recorded demo is on. Lines stay in the browser." : null });
    },
    submitting,
    answering,
    paused,
    sensing,
    sensingFacts,
    stageLabel,
    greeting,
    stream,
    now,
    submitText,
    scanRoom: () => {
      socketRef.current?.send(JSON.stringify({ type: "room.scan" }));
    },
    saveMoment: () => {
      socketRef.current?.send(JSON.stringify({ type: "moment.save" }));
    },
    answerQuestion,
    confirmRecorded: (requestId) => apply({ type: "recorded.confirm", requestId }),
    startFluffy,
    pauseFluffy,
    stopFluffy,
    muteMic,
    registerCaptureVideo,
    speech,
  };

  return (
    <SessionContext.Provider value={api}>
      {children}
      <CaptureHost />
    </SessionContext.Provider>
  );
}

function CaptureHost() {
  const session = useSession();
  const ref = useRef<HTMLVideoElement>(null);
  const register = session.registerCaptureVideo;
  useEffect(() => {
    register(ref.current);
    return () => register(null);
  }, [register]);
  return <video ref={ref} autoPlay muted playsInline className="pointer-events-none fixed h-px w-px opacity-0" aria-hidden />;
}

export function useSession(): SessionApi {
  const value = useContext(SessionContext);
  if (!value) throw new Error("useSession must be used inside FluffySessionProvider");
  return value;
}

function parseMessage(data: unknown): unknown {
  if (typeof data !== "string") return data;
  try {
    return JSON.parse(data);
  } catch {
    return null;
  }
}

function createId(prefix: string): string {
  const id = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}`;
  return `${prefix}_${id}`;
}

function resolveHarnessUrl(base: string, url: string): string {
  if (url.startsWith("http://") || url.startsWith("https://")) return url;
  return `${base.replace(/\/+$/, "")}${url.startsWith("/") ? url : `/${url}`}`;
}
