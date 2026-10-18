import { useEffect, useRef, useState } from "react";
import { describeChange, FluffyRoom3D, toRoomEvent, useRoomFromEvents, type RoomEvent } from "../src/index";

const HARNESS = "ws://localhost:8000/ws/ui";

export function App() {
  const [events, setEvents] = useState<RoomEvent[]>([]);
  const room = useRoomFromEvents(events);
  const [theme, setTheme] = useState<"light" | "dark">("light");
  const [reduced, setReduced] = useState(false);
  const [connected, setConnected] = useState(false);
  const [link, setLink] = useState("not connected");
  const [playing, setPlaying] = useState(0);
  const heard = useHeard(room);

  useEffect(() => {
    if (!connected) return;
    const socket = new WebSocket(HARNESS);
    setLink("connecting");
    socket.onopen = () => setLink("live");
    socket.onclose = () => setLink("offline");
    socket.onerror = () => setLink("offline");
    socket.onmessage = (message) => {
      let raw: unknown;
      try {
        raw = JSON.parse(String(message.data));
      } catch {
        return;
      }
      const event = toRoomEvent(raw);
      if (event) setEvents((list) => [...list, event]);
    };
    return () => socket.close();
  }, [connected]);

  useEffect(() => {
    if (!playing) return;
    const timers = REPLAY.map((step, index) =>
      window.setTimeout(() => {
        setEvents((list) => [...list, ...step()]);
      }, index * 900),
    );
    return () => timers.forEach((timer) => window.clearTimeout(timer));
  }, [playing]);

  const push = (event: RoomEvent) => setEvents((list) => [...list, event]);
  const run = (
    tool: string,
    args: Record<string, unknown>,
    status: "asking" | "done" | "failed",
    result?: Record<string, unknown>,
  ) => setEvents((list) => [...list, ...call(tool, args, status, result)]);

  return (
    <main className="page">
      <h1>Fluffy room</h1>
      <p className="lede">A cut-away of the home. Tool calls move the lamp, door, speaker, and phone.</p>
      <div className="stage">
        <FluffyRoom3D state={room} size="lg" theme={theme} reducedMotion={reduced} />
      </div>
      <p className="heard" aria-live="polite">
        {heard || "Nothing has changed yet."}
      </p>
      <div className="row">
        <button className="primary" onClick={() => setPlaying((n) => n + 1)}>
          Replay script
        </button>
        <button onClick={() => setEvents([])}>Reset room</button>
        <button aria-pressed={connected} onClick={() => setConnected((on) => !on)}>
          {connected ? `Harness: ${link}` : "Connect to harness"}
        </button>
        <button aria-pressed={theme === "dark"} onClick={() => setTheme((mode) => (mode === "light" ? "dark" : "light"))}>
          {theme === "light" ? "Dark theme" : "Light theme"}
        </button>
        <button aria-pressed={reduced} onClick={() => setReduced((on) => !on)}>
          Reduce motion
        </button>
      </div>

      <section className="group">
        <h2>Lamp</h2>
        <div className="row">
          <button onClick={() => run("dim_lights", { luminosity: 30 }, "done")}>Dim to 30%</button>
          <button onClick={() => run("dim_lights", { luminosity: 100 }, "done")}>Brighten</button>
          <button className="ask" onClick={() => run("dim_lights", { luminosity: 20 }, "asking")}>
            Ask first
          </button>
          <button className="bad" onClick={() => run("dim_lights", { luminosity: 10 }, "failed")}>
            Dim fails
          </button>
          <button onClick={() => run("set_light_warmth", { warmth: "cool" }, "done")}>Cool light</button>
          <button onClick={() => run("flash_lights", { color: "blue", duration_s: 2 }, "done")}>Flash blue</button>
        </div>
      </section>

      <section className="group">
        <h2>Door</h2>
        <div className="row">
          <button onClick={() => run("close_door", {}, "done")}>Close</button>
          <button onClick={() => run("open_door", {}, "done")}>Open</button>
          <button className="ask" onClick={() => run("close_door", {}, "asking")}>
            Ask first
          </button>
          <button className="bad" onClick={() => run("open_door", {}, "failed")}>
            Open fails
          </button>
        </div>
      </section>

      <section className="group">
        <h2>Speaker</h2>
        <div className="row">
          <button
            onClick={() =>
              run("spotify_play", {}, "done", { track: "Clair de Lune", artist: "Debussy" })
            }
          >
            Play Spotify
          </button>
          <button onClick={() => run("play_sound", { sound: "rain" }, "done")}>Play rain</button>
          <button onClick={() => run("stop_sound", {}, "done")}>Stop</button>
          <button className="bad" onClick={() => run("spotify_play", {}, "failed")}>
            Play fails
          </button>
        </div>
      </section>

      <section className="group">
        <h2>Phone</h2>
        <div className="row">
          <button
            onClick={() =>
              run("send_text", { to: "Alex", text: "Sam may need help", channel: "imessage" }, "done", {
                delivered: true,
              })
            }
          >
            Text Alex
          </button>
          <button
            onClick={() =>
              run("photon_send", { to: "Alex", text: "Sam may need help", channel: "sms" }, "done", {
                delivered: false,
              })
            }
          >
            Text, not delivered
          </button>
          <button className="bad" onClick={() => run("send_message", { to: "Alex", text: "Hello" }, "failed")}>
            Text fails
          </button>
          <button onClick={() => push({ type: "message.received", from: "Alex", text: "On my way", channel: "imessage" })}>
            Alex replies
          </button>
          <button onClick={() => run("call_person", { to: "Alex" }, "done")}>Call Alex</button>
        </div>
      </section>

      <section className="group">
        <h2>Question</h2>
        <div className="row">
          <button className="ask" onClick={() => push({ type: "ask", question: "Leave the lamp on?", choices: ["Yes", "No"] })}>
            Ask a question
          </button>
          <button onClick={() => push({ type: "ask.answer" })}>Answer</button>
        </div>
      </section>
    </main>
  );
}

const REPLAY: Array<() => RoomEvent[]> = [
  () => call("dim_lights", { luminosity: 30 }, "done"),
  () => call("close_door", {}, "done"),
  () => call("spotify_play", {}, "done", { track: "Clair de Lune", artist: "Debussy" }),
  () => call("send_text", { to: "Alex", text: "Sam may need help", channel: "imessage" }, "done", { delivered: true }),
  () => [{ type: "ask", question: "Leave the lamp on?", choices: ["Yes", "No"] }],
];

function call(
  tool: string,
  args: Record<string, unknown>,
  status: "asking" | "done" | "failed",
  result?: Record<string, unknown>,
): RoomEvent[] {
  const call_id = `${tool}-${status}-${Math.random().toString(16).slice(2, 8)}`;
  return [
    { type: "tool.call", call_id, tool, args },
    { type: "tool.status", call_id, status, result, now: Date.now() },
  ];
}

function useHeard(room: ReturnType<typeof useRoomFromEvents>): string {
  const prev = useRef(room);
  const [line, setLine] = useState("");
  useEffect(() => {
    const said = describeChange(prev.current, room);
    prev.current = room;
    if (said) setLine(said);
  }, [room]);
  return line;
}
