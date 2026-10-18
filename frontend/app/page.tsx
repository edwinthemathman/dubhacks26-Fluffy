"use client";

import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { HomeSvg } from "../components/HomeSvg";
import { applySnapshot, createState, markSpeechPlayed, reduce, visibleCaptions } from "../lib/reducer";
import { AppState, HomeSnapshot, UiEvent } from "../lib/types";

const HARNESS = process.env.NEXT_PUBLIC_HARNESS_URL ?? "http://127.0.0.1:8000";
const SPOTIFY_CONNECT = "http://127.0.0.1:8100/api/integrations/spotify/connect";

type Integration = { provider: string; readiness: string; detail: string };
type Device = { id: string; name: string; supports_volume: boolean };

export default function Page() {
  const [state, setState] = useState<AppState>(createState());
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [clientId, setClientId] = useState("");
  const [sessionId, setSessionId] = useState("ses_demo");
  const [speakerReady, setSpeakerReady] = useState(false);
  const [audioBlocked, setAudioBlocked] = useState(false);
  const [cameraDetail, setCameraDetail] = useState("Camera capture is not connected.");
  const [integrations, setIntegrations] = useState<Integration[]>([]);
  const [devices, setDevices] = useState<Device[]>([]);
  const [tail, setTail] = useState(false);
  const [devTool, setDevTool] = useState("dim_lights");
  const [luminosity, setLuminosity] = useState(35);
  const [warmth, setWarmth] = useState("warm");
  const [blind, setBlind] = useState(40);
  const [notice, setNotice] = useState("");
  const playing = useRef(new Set<string>());

  useEffect(() => {
    const existing = window.localStorage.getItem("fluffy-client");
    const id = existing ?? `client_${crypto.randomUUID()}`;
    window.localStorage.setItem("fluffy-client", id);
    setClientId(id);
    void fetch(`${HARNESS}/api/session`).then((response) => response.json()).then((body) => {
      if (body.session_id) setSessionId(body.session_id);
    }).catch(() => undefined);
    const timer = window.setInterval(() => setNowMs(Date.now()), 300);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    let socket: WebSocket | null = null;
    let stopped = false;
    const connect = async () => {
      const snapshot = await fetch(`${HARNESS}/api/state`).then((response) => response.json()).catch(() => null);
      if (snapshot && !stopped) {
        setState((current) => applySnapshot(current, snapshot, true));
      }
      const health = await fetch(`${HARNESS}/api/integrations`).then((response) => response.json()).catch(() => null);
      if (health?.integrations) setIntegrations(health.integrations);
      if (stopped) return;
      socket = new WebSocket(`${HARNESS.replace("http", "ws")}/ws/ui`);
      socket.onmessage = (message) => {
        const event = JSON.parse(message.data) as UiEvent;
        if (event.type === "home.snapshot") {
          setState((current) => applySnapshot(current, event.payload as unknown as HomeSnapshot, false));
          return;
        }
        setState((current) => reduce(current, event));
      };
      socket.onopen = () => setState((current) => ({ ...current, connected: true }));
      socket.onclose = () => setState((current) => ({ ...current, connected: false }));
    };
    void connect();
    return () => {
      stopped = true;
      socket?.close();
    };
  }, []);

  async function ack(messageId: string, status: string) {
    await fetch(`${HARNESS}/api/audio/ack`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ message_id: messageId, session_id: sessionId, status, client_id: clientId }),
    });
  }

  const queueHead = state.speechQueue[0];
  useEffect(() => {
    if (!queueHead || !speakerReady || audioBlocked || playing.current.has(queueHead)) return;
    const caption = state.captions.find((item) => item.id === queueHead && item.audio_url);
    if (!caption?.audio_url) return;
    playing.current.add(queueHead);
    const audio = new Audio(`${HARNESS}${caption.audio_url}`);
    const finish = (status: "ended" | "failed" | "blocked") => {
      void ack(queueHead, status);
      if (status === "ended") {
        setTail(true);
        window.setTimeout(() => setTail(false), 300);
      }
      setState((current) => markSpeechPlayed(current, queueHead));
    };
    audio.onended = () => finish("ended");
    audio.onerror = () => finish("failed");
    audio.play().then(() => void ack(queueHead, "started")).catch(() => {
      setAudioBlocked(true);
      finish("blocked");
    });
  }, [queueHead, speakerReady, audioBlocked, state.captions]);

  const captions = useMemo(() => visibleCaptions(state.captions, nowMs), [state.captions, nowMs]);

  async function startFluffy() {
    await fetch(`${HARNESS}/api/session/speaker`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ client_id: clientId }),
    });
    setSpeakerReady(true);
    setAudioBlocked(false);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: true });
      stream.getTracks().forEach((track) => track.stop());
      setCameraDetail("Permission granted. Frame capture is not connected, so there is no live preview.");
    } catch {
      setCameraDetail("Camera unavailable. Capture is not connected, and the browser did not grant a camera.");
    }
  }

  async function post(path: string, body?: unknown) {
    const response = await fetch(`${HARNESS}${path}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || payload.error_code) {
      setNotice(payload.message ?? payload.error_code ?? response.statusText);
    } else {
      setNotice("");
    }
    return payload;
  }

  async function submitDeveloper(event: FormEvent) {
    event.preventDefault();
    const args = developerArgs(devTool, luminosity, warmth, blind);
    await post("/api/dev/request", { tool: devTool, args, intent: "Developer explicit request" });
  }

  async function loadDevices() {
    const payload = await fetch(`${HARNESS}/api/integrations/spotify/devices`).then((response) => response.json());
    setDevices(payload.devices ?? []);
    if (payload.detail) setNotice(typeof payload.detail === "string" ? payload.detail : JSON.stringify(payload.detail));
  }

  const listening = tail || state.listening === "suppressed" ? "Microphone forwarding suppressed" : state.listening;

  return (
    <main className="app">
      <header>
        <div>
          <h1>Fluffy</h1>
          <p>{state.connected ? "Harness connected" : "Harness disconnected"} · executor {state.executorOnline ? "online" : "offline"} · {listening}</p>
        </div>
      </header>

      <section className="bubble" aria-live="polite">
        <h2>Fluffy</h2>
        {captions.length === 0 ? <p>No caption yet.</p> : null}
        {captions.map((caption) => (
          <div className={caption.priority === "high" ? "caption high" : "caption"} key={caption.id}>
            <span>{caption.text}</span>
            <button type="button" onClick={() => void post(`/api/captions/${caption.id}/dismiss`)}>Dismiss</button>
          </div>
        ))}
        <div className="questions">
          {state.questions.map((question) => (
            <div key={question.questionId}>
              <p>{question.text}</p>
              {question.choices.map((choice) => (
                <button key={choice} type="button" onClick={() => void post(`/api/questions/${question.questionId}/answer`, { answer: choice, source: "button" })}>
                  {choiceLabel(choice)}
                </button>
              ))}
            </div>
          ))}
          {state.incident?.question_id && state.questions.length === 0 ? (
            <>
              <p>{state.incident.question_text}</p>
              <button type="button" onClick={() => void post(`/api/questions/${state.incident?.question_id}/answer`, { answer: "im_okay", source: "button" })}>I&apos;m okay</button>
              <button type="button" onClick={() => void post(`/api/questions/${state.incident?.question_id}/answer`, { answer: "need_help", source: "button" })}>I need help</button>
            </>
          ) : null}
        </div>
      </section>

      <section className="camera">
        <div>
          <h2>Camera</h2>
          <p>{cameraDetail}</p>
        </div>
      </section>

      <section className="home-card">
        <h2>Living room</h2>
        <HomeSvg state={state} nowMs={nowMs} />
      </section>

      <section className="activity">
        <h2>Activity</h2>
        <ul>
          {state.activity.length === 0 ? <li>No actions yet.</li> : null}
          {state.activity.map((item) => (
            <li key={item.id}>
              <span>{item.tool}</span>
              <span className={activityTone(item.status, item.verification)}>{activityLabel(item.status, item.verification)}{item.errorCode ? ` · ${item.errorCode}` : ""}</span>
            </li>
          ))}
        </ul>
      </section>

      <section className="controls">
        <button type="button" onClick={() => void startFluffy()}>Start Fluffy</button>
        {audioBlocked ? <button type="button" onClick={() => setAudioBlocked(false)}>Enable audio</button> : null}
        <button type="button" onClick={() => void post("/api/sensing/pause")}>Pause sensing</button>
        <button type="button" onClick={() => void post("/api/sensing/resume")}>Resume sensing</button>
        <button type="button" onClick={() => void post("/api/help")}>I need help</button>
        <a href={SPOTIFY_CONNECT}>Connect Spotify</a>
        <button type="button" onClick={() => void loadDevices()}>List Spotify devices</button>
        {devices.map((device) => (
          <button key={device.id} type="button" onClick={() => void post("/api/integrations/spotify/device", { device_id: device.id })}>
            Use {device.name}{device.supports_volume ? "" : " (no volume)"}
          </button>
        ))}
      </section>

      <section className="status-row">
        <ul>
          {integrations.map((item) => (
            <li key={item.provider}><span>{item.provider}</span><span>{item.readiness}: {item.detail}</span></li>
          ))}
        </ul>
      </section>

      <section className="developer">
        <h2>Developer tools</h2>
        <p>Explicit request path. This is not live perception, and it cannot submit a confidence score or an authorization id.</p>
        <form onSubmit={(event) => void submitDeveloper(event)}>
          <label>Tool
            <select value={devTool} onChange={(event) => setDevTool(event.target.value)}>
              <option value="dim_lights">dim_lights</option>
              <option value="set_light_warmth">set_light_warmth</option>
              <option value="set_blinds">set_blinds</option>
              <option value="flash_lights">flash_lights</option>
              <option value="show_caption">show_caption</option>
              <option value="play_sound">play_sound</option>
              <option value="stop_sound">stop_sound</option>
            </select>
          </label>
          {devTool === "dim_lights" ? (
            <label>Brightness
              <input type="number" min={0} max={100} value={luminosity} onChange={(event) => setLuminosity(Number(event.target.value))} />
            </label>
          ) : null}
          {devTool === "set_light_warmth" ? (
            <label>Warmth
              <select value={warmth} onChange={(event) => setWarmth(event.target.value)}>
                <option value="warm">warm</option>
                <option value="neutral">neutral</option>
                <option value="cool">cool</option>
              </select>
            </label>
          ) : null}
          {devTool === "set_blinds" ? (
            <label>Position
              <input type="number" min={0} max={100} value={blind} onChange={(event) => setBlind(Number(event.target.value))} />
            </label>
          ) : null}
          <button type="submit">Send developer request</button>
        </form>
        <form onSubmit={(event) => { event.preventDefault(); void post("/api/manual/dim_lights", { room: "living_room", luminosity, light: "lamp.living_floor" }); }}>
          <button type="submit">Manual brightness (direct control)</button>
        </form>
        {notice ? <p className="tone-bad">{notice}</p> : null}
      </section>
    </main>
  );
}

function developerArgs(tool: string, luminosity: number, warmth: string, blind: number) {
  if (tool === "dim_lights") return { room: "living_room", luminosity, light: "lamp.living_floor" };
  if (tool === "set_light_warmth") return { room: "living_room", warmth, light: "lamp.living_floor" };
  if (tool === "set_blinds") return { room: "living_room", position: blind };
  if (tool === "flash_lights") return { room: "living_room", color: "blue", pulses: 2 };
  if (tool === "show_caption") return { text: "Silent developer caption", priority: "normal", ttl_s: 10 };
  if (tool === "play_sound") return { room: "living_room", sound: "chime", volume: 30 };
  return { room: "living_room" };
}

function choiceLabel(choice: string) {
  if (choice === "im_okay") return "I'm okay";
  if (choice === "need_help") return "I need help";
  if (choice === "yes") return "Yes";
  if (choice === "no") return "No";
  return choice;
}

function activityLabel(status: string, verification: string | null) {
  if (verification === "provider_accepted") return "Submitted";
  if (verification === "provider_delivered") return "Delivered";
  if (status === "succeeded" && (verification === "state_committed" || verification === "playback_confirmed")) return "Succeeded";
  if (status === "unknown") return "Unknown";
  if (status === "requested") return "Requested";
  if (status === "executing") return "Executing";
  if (status === "cancelled") return "Cancelled";
  if (status === "skipped") return "Skipped";
  if (status === "pending") return "Pending";
  if (status === "failed") return "Failed";
  return status;
}

function activityTone(status: string, verification: string | null) {
  if (verification === "provider_delivered" || (status === "succeeded" && verification !== "provider_accepted" && verification !== "unverified")) return "tone-good";
  if (status === "failed" || status === "cancelled") return "tone-bad";
  if (status === "unknown" || verification === "unverified") return "tone-unknown";
  return "tone-pending";
}
