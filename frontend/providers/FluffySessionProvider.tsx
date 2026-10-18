"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { SpeechPlayer } from "@/lib/audio/player";
import { getJson, healthUrl, postJson, questionUrl, speechAckUrl, stopUrl, textInputUrl, toolsUrl } from "@/lib/fluffy/client";
import { readPublicConfig, type FluffyPublicConfig } from "@/lib/fluffy/config";
import { normalizeEvent } from "@/lib/fluffy/normalizeEvent";
import { thinkingLabel, type SensingFacts } from "@/lib/fluffy/phrases";
import { sceneToRecorded } from "@/lib/fluffy/recorded";
import { createSession, reduceSession } from "@/lib/fluffy/reducer";
import type { CanonicalEvent, SessionState } from "@/lib/fluffy/types";
import { CaptureController, type CapturePhase } from "@/lib/media/capture";
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
  stream: MediaStream | null;
  now: number;
  submitText: () => Promise<void>;
  answerQuestion: (choice: string) => Promise<void>;
  confirmRecorded: (requestId: string) => void;
  startFluffy: () => Promise<void>;
  pauseFluffy: () => void;
  stopFluffy: () => Promise<void>;
  muteMic: (muted: boolean) => void;
  registerCaptureVideo: (video: HTMLVideoElement | null) => void;
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
  const stateRef = useRef(state);
  const socketRef = useRef<WebSocket | null>(null);
  const captureRef = useRef<CaptureController | null>(null);
  const playerRef = useRef<SpeechPlayer | null>(null);
  const videoNode = useRef<HTMLVideoElement | null>(null);
  const retryRef = useRef(0);
  useEffect(() => {
    stateRef.current = state;
  }, [state]);

  const apply = useCallback((event: CanonicalEvent, historical = false) => {
    setState((current) => reduceSession(current, event, { historical, now: Date.now() }));
  }, []);

  const ingest = useCallback((raw: unknown, historical: boolean) => {
    for (const event of normalizeEvent(raw, { now: Date.now() })) {
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
      apply({ type: "speech", messageId, status });
      capture.noteSpeechEnded();
      void postJson(speechAckUrl(config), { session_id: stateRef.current.sessionId, message_id: messageId, status }).then((result) => {
        if (result.missing) {
          apply({ type: "capability", name: "inference", available: false, detail: "POST /api/speech/status is not on this backend. Playback was local only." });
        }
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
      if (item.audioUrl && item.permitAudio) player.enqueue({ messageId: item.messageId, audioUrl: item.audioUrl });
    }
  }, [state.speechQueue]);

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
    let resync = false;
    let cancelled = false;
    const connect = () => {
      if (cancelled) return;
      apply({ type: "connection", status: retryRef.current === 0 ? "connecting" : "reconnecting" });
      const socket = new WebSocket(`${config.wsBase}/ws/ui`);
      socketRef.current = socket;
      socket.onopen = () => {
        retryRef.current = 0;
        buffer = [];
        resync = true;
        apply({ type: "connection", status: "open" });
        timer = window.setTimeout(() => {
          resync = false;
          flushBuffer(buffer, ingest);
          buffer = [];
        }, 80);
      };
      socket.onmessage = (message) => {
        const raw = parseMessage(message.data);
        if (raw == null) return;
        if (resync) buffer.push(raw);
        else ingest(raw, false);
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
    connect();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
      socketRef.current?.close();
    };
  }, [apply, config.wsBase, ingest]);

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
      session_id: stateRef.current.sessionId,
      choice,
      text: choice,
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

  const startFluffy = useCallback(async () => {
    const capture = captureRef.current;
    if (!capture) return;
    if (videoNode.current) capture.attach(videoNode.current);
    await capture.start(stateRef.current.sessionId);
    setPaused(false);
    setStream(capture.previewStream());
  }, []);

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
    playerRef.current?.clearPending();
    setStream(null);
    setPaused(false);
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
    stream,
    now,
    submitText,
    answerQuestion,
    confirmRecorded: (requestId) => apply({ type: "recorded.confirm", requestId }),
    startFluffy,
    pauseFluffy,
    stopFluffy,
    muteMic,
    registerCaptureVideo,
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

function flushBuffer(buffer: unknown[], ingest: (raw: unknown, historical: boolean) => void): void {
  let snapshot = -1;
  buffer.forEach((raw, index) => {
    const record = raw && typeof raw === "object" ? (raw as { type?: unknown }).type : null;
    if (record === "state.snapshot" || record === "home.state" || record === "snapshot" || (record === "state" && raw && typeof raw === "object" && "state" in raw)) {
      snapshot = index;
    }
  });
  if (snapshot >= 0) {
    ingest(buffer[snapshot], true);
    for (let i = snapshot + 1; i < buffer.length; i += 1) ingest(buffer[i], false);
    return;
  }
  for (const raw of buffer) ingest(raw, true);
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
