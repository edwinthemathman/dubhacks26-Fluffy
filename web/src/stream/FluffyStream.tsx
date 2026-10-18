import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useLocation, useSearchParams } from "react-router-dom";
import type { AvatarState, ClientMessage, FluffyEvent, Line, Snapshot, Turn } from "./events";
import { playTimeline, readOrCreateStart, restartStart, storageKey } from "./MockPlayer";
import { freshSnapshot, reduce } from "./reduce";
import { isSceneId, timelines, type SceneId } from "./timelines";

type StreamContextValue = {
  snapshot: Snapshot;
  scene: SceneId | null;
  send: (message: ClientMessage) => void;
  replay: () => void;
};

const StreamContext = createContext<StreamContextValue | null>(null);

function isEvent(value: unknown): value is FluffyEvent {
  return typeof value === "object" && value !== null && "type" in value;
}

export function deriveAvatar(snapshot: Snapshot): AvatarState {
  if (snapshot.safety === "checkin1" || snapshot.safety === "checkin2" || snapshot.safety === "alerted") {
    return "safety";
  }
  const open = snapshot.turns.find((turn) => !turn.outcome);
  if (!open) return "idle";
  const last = open.lines[open.lines.length - 1];
  if (!last) return "thinking";
  if (last.type === "ask" && !last.answer) return "asking";
  if (last.type === "tool") return "acting";
  if (last.type === "heard") return "hearing";
  if (last.type === "think") return "thinking";
  if (last.type === "say") return "idle";
  return "thinking";
}

export function openQuestion(turns: Turn[]): Extract<Line, { type: "ask" }> | null {
  for (const turn of turns) {
    for (const line of turn.lines) {
      if (line.type === "ask" && !line.answer) return line;
    }
  }
  return null;
}

export function FluffyStreamProvider({ children }: { children: ReactNode }) {
  const location = useLocation();
  const [searchParams, setSearchParams] = useSearchParams();
  const sceneParam = searchParams.get("mock");
  const scene = isSceneId(sceneParam) ? sceneParam : null;
  const active = location.pathname !== "/";
  const [nonce, setNonce] = useState(0);
  const [snapshot, setSnapshot] = useState<Snapshot>(() => freshSnapshot("idle"));
  const wsRef = useRef<WebSocket | null>(null);
  const sceneRef = useRef(scene);
  sceneRef.current = scene;

  useEffect(() => {
    if (!active) return;
    if (scene) {
      const start = readOrCreateStart(scene);
      setSnapshot({ ...freshSnapshot("mock"), fallback: true });
      const stop = playTimeline(timelines[scene], start, (event) => {
        setSnapshot((current) => reduce(current, event));
      });
      return stop;
    }

    setSnapshot(freshSnapshot("offline"));
    let stopped = false;
    let retry = 0;
    let timer = 0;

    const connect = () => {
      if (stopped) return;
      const protocol = window.location.protocol === "https:" ? "wss" : "ws";
      const socket = new WebSocket(`${protocol}://${window.location.host}/ws/ui`);
      wsRef.current = socket;
      socket.onopen = () => {
        retry = 0;
        setSnapshot((current) => ({ ...current, link: "live", fallback: false }));
      };
      socket.onmessage = (message) => {
        try {
          const data = JSON.parse(String(message.data)) as unknown;
          if (isEvent(data)) setSnapshot((current) => reduce({ ...current, link: "live" }, data));
        } catch {
          /* ignore malformed frames */
        }
      };
      socket.onclose = () => {
        if (wsRef.current === socket) wsRef.current = null;
        if (stopped) return;
        setSnapshot((current) => ({ ...current, link: "reconnecting" }));
        timer = window.setTimeout(connect, Math.min(4000, 500 * 2 ** retry));
        retry += 1;
      };
    };

    connect();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      wsRef.current?.close();
      wsRef.current = null;
    };
  }, [active, scene, nonce]);

  useEffect(() => {
    if (!scene) return;
    const onStorage = (event: StorageEvent) => {
      if (event.key === storageKey(scene)) setNonce((value) => value + 1);
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, [scene]);

  const replay = useCallback(() => {
    if (!sceneRef.current) return;
    restartStart(sceneRef.current);
    setNonce((value) => value + 1);
  }, []);

  const send = useCallback((message: ClientMessage) => {
    if (message.type === "mock.play") {
      if (!isSceneId(message.scene)) return;
      restartStart(message.scene);
      setSearchParams({ mock: message.scene });
      setNonce((value) => value + 1);
      return;
    }
    if (message.type === "room.reset") {
      if (sceneRef.current) {
        restartStart(sceneRef.current);
        setNonce((value) => value + 1);
      } else {
        setSnapshot(freshSnapshot("offline"));
        wsRef.current?.send(JSON.stringify(message));
      }
      return;
    }
    if (sceneRef.current) {
      setSnapshot((current) => applyLocal(current, message));
      return;
    }
    const socket = wsRef.current;
    // #region agent log
    fetch("http://127.0.0.1:7643/ingest/889f4935-7c87-40ef-88c8-e26de67696a8",{method:"POST",headers:{"Content-Type":"application/json","X-Debug-Session-Id":"9d207c"},body:JSON.stringify({sessionId:"9d207c",hypothesisId:"A",location:"FluffyStream.tsx:send",message:"client send",data:{type:message.type,scene:sceneRef.current,ready:socket?.readyState??-1,sent:Boolean(socket&&socket.readyState===WebSocket.OPEN)},timestamp:Date.now()})}).catch(()=>{});
    // #endregion
    socket?.send(JSON.stringify(message));
  }, [setSearchParams]);

  const value = useMemo(() => ({ snapshot, scene, send, replay }), [snapshot, scene, send, replay]);
  return <StreamContext.Provider value={value}>{children}</StreamContext.Provider>;
}

function applyLocal(snapshot: Snapshot, message: ClientMessage): Snapshot {
  if (message.type === "profile.set") {
    return reduce(snapshot, { type: "profile", profile: message.profile });
  }
  if (message.type === "caregiver.ack") {
    return reduce(snapshot, { type: "safety", state: "acked" });
  }
  if (message.type === "typed") {
    const text = message.text.trim();
    if (!text) return snapshot;
    return reduce(snapshot, { type: "sense.hear", text });
  }
  if (message.type === "answer") {
    const turn = snapshot.turns.find((item) => item.lines.some((line) => line.type === "ask" && !line.answer));
    if (!turn) return snapshot;
    let next = reduce(snapshot, {
      type: "ask.answer",
      turn_id: turn.id,
      by: message.by,
      choice: message.choice,
    });
    const declining = ["no", "help", "i'm fine", "im fine"].includes(message.choice.toLowerCase());
    if (declining) {
      for (const tool of Object.values(turn.tools)) {
        if (tool.status === "asking" || tool.status === "proposed") {
          next = reduce(next, { type: "tool.status", call_id: tool.callId, status: "declined" });
        }
      }
      return next;
    }
    for (const tool of Object.values(turn.tools)) {
      if (tool.status !== "asking" && tool.status !== "proposed") continue;
      next = reduce(next, { type: "tool.status", call_id: tool.callId, status: "approved" });
      next = reduce(next, { type: "tool.status", call_id: tool.callId, status: "done" });
      if (tool.tool === "dim_lights") {
        const luminosity = Number(tool.args.luminosity ?? 35);
        next = reduce(next, { type: "state", lamp: { luminosity } });
      }
    }
    return reduce(next, { type: "say", text: "Okay." });
  }
  return snapshot;
}

export function useFluffyStream(): StreamContextValue {
  const value = useContext(StreamContext);
  if (!value) throw new Error("useFluffyStream must be used inside FluffyStreamProvider");
  return value;
}
