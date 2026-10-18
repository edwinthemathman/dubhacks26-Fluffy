import { useEffect, useRef } from "react";
import { TopBar } from "../../components/shared/TopBar";
import { ProfileBadge } from "../../components/shared/ProfileBadge";
import { ToolPill } from "../../components/shared/ToolPill";
import { useChromeSpeech } from "../../live/useChromeSpeech";
import { useLiveCapture } from "../../live/useLiveCapture";
import { useVoice } from "../../live/useVoice";
import { deriveAvatar, useFluffyStream } from "../../stream/FluffyStream";
import type { ProfileId, Turn } from "../../stream/events";
import { SCENES } from "../../stream/timelines";
import { Room } from "./Room";
import styles from "./Demo.module.css";

const PROFILES: { id: ProfileId; label: string }[] = [
  { id: "blind", label: "Blind" },
  { id: "deaf", label: "Deaf" },
  { id: "speech_cognitive", label: "Speech" },
];

export function Demo() {
  const { snapshot, scene, send, replay } = useFluffyStream();
  const speaking = useRef(false);
  const live = useLiveCapture({ isSpeaking: () => speaking.current });
  useChromeSpeech(live.status === "live" && !scene, send, () => speaking.current);
  const voice = useVoice({
    enabled: live.status === "live",
    sayId: snapshot.speechId,
    voice: snapshot.speechVoice,
    text: snapshot.speech,
    rate: snapshot.profile === "speech_cognitive" ? 0.85 : 1,
    send,
    speakingRef: speaking,
  });
  const camOn = live.status === "live" || live.status === "starting";
  useEffect(() => {
    // #region agent log
    fetch("http://127.0.0.1:7643/ingest/889f4935-7c87-40ef-88c8-e26de67696a8",{method:"POST",headers:{"Content-Type":"application/json","X-Debug-Session-Id":"9d207c"},body:JSON.stringify({sessionId:"9d207c",hypothesisId:"B",location:"Demo.tsx:state",message:"demo camera state",data:{scene:scene??null,status:live.status,error:live.error,link:snapshot.link,cameraDisabled:Boolean(scene)},timestamp:Date.now()})}).catch(()=>{});
    // #endregion
  }, [scene, live.status, live.error, snapshot.link]);
  const avatar = deriveAvatar(snapshot);
  const thinking = avatar === "thinking" || avatar === "seeing" || avatar === "hearing";
  const doneCount = countDone(snapshot.turns);
  const lastDone = useRef(doneCount);

  useEffect(() => {
    if (doneCount > lastDone.current) chime();
    lastDone.current = doneCount;
  }, [doneCount]);

  const calls = snapshot.turns.flatMap((turn) =>
    turn.lines
      .filter((line) => line.type === "tool")
      .map((line) => ({ tool: turn.tools[line.callId], trigger: turn.trigger }))
      .filter((item) => item.tool),
  );

  return (
    <>
      <TopBar />
      <main id="main" className={`page ${styles.demo}`}>
        <div className={styles.toolbar}>
          <h1 className="screen-title">Living room</h1>
          <ProfileBadge profile={snapshot.profile} />
          <span className="pill quiet">
            {scene ? "Recorded run" : snapshot.link === "live" ? "Live" : snapshot.link === "reconnecting" ? "Reconnecting…" : "Harness offline"}
          </span>
          <div className="segment" role="group" aria-label="Profile">
            {PROFILES.map((profile) => (
              <button
                key={profile.id}
                type="button"
                aria-pressed={snapshot.profile === profile.id}
                onClick={() => send({ type: "profile.set", profile: profile.id })}
              >
                {profile.label}
              </button>
            ))}
          </div>
          <div className="segment" role="group" aria-label="Scene">
            {SCENES.map((item) => (
              <button
                key={item.id}
                type="button"
                aria-pressed={scene === item.id}
                onClick={() => send({ type: "mock.play", scene: item.id })}
              >
                {item.label}
              </button>
            ))}
          </div>
          <button type="button" className="text-button" onClick={() => send({ type: "room.reset" })}>
            Reset room
          </button>
          <button type="button" className="text-button" onClick={replay} disabled={!scene}>
            Replay
          </button>
        </div>

        <div className={styles.columns}>
          <section className={`card ${styles.camera}`} aria-label="Camera">
            <div className={styles.view}>
              <video ref={live.videoRef} className={styles.feed} muted playsInline aria-label="Live camera" hidden={!camOn} />
              <div className={styles.overlay}>
                <div className={styles.overlayTop}>
                  <span className="pill">{live.status === "live" ? "Camera live" : live.status === "starting" ? "Starting camera…" : "Camera off"}</span>
                  {thinking && <span className={`pill ${styles.think}`}>Fluffy is thinking</span>}
                  <button
                    type="button"
                    className={`pill ${styles.camButton}`}
                    onClick={camOn ? live.stop : () => {
                      // #region agent log
                      fetch("http://127.0.0.1:7643/ingest/889f4935-7c87-40ef-88c8-e26de67696a8",{method:"POST",headers:{"Content-Type":"application/json","X-Debug-Session-Id":"9d207c"},body:JSON.stringify({sessionId:"9d207c",hypothesisId:"B",location:"Demo.tsx:camera",message:"start camera clicked",data:{scene:scene??null,status:live.status,link:snapshot.link},timestamp:Date.now()})}).catch(()=>{});
                      // #endregion
                      voice.unlock();
                      void live.start();
                    }}
                    disabled={Boolean(scene)}
                    title={scene ? "Recorded runs don't use the camera" : undefined}
                  >
                    {camOn ? "Stop camera" : "Start camera"}
                  </button>
                </div>
                {!camOn && (
                  <p>{live.error ?? "Start the camera to let Fluffy watch and listen live. Frames stay in memory."}</p>
                )}
                <ul>
                  {snapshot.senses.chips.map((chip) => (
                    <li key={chip}>{chip}</li>
                  ))}
                </ul>
              </div>
            </div>
          </section>
          <section className={`card ${styles.roomCard}`} aria-label="Fluffy's room">
            <Room home={snapshot.home} onVolume={(volume) => send({ type: "volume.set", volume })} />
          </section>
          <section className={`card ${styles.calls}`} aria-label="Tool calls">
            <h2>Tool calls</h2>
            {calls.length === 0 && <p className="muted">Waiting for a decision.</p>}
            {calls.map((item) =>
              item.tool ? <ToolPill key={item.tool.callId} tool={item.tool} trigger={item.trigger} /> : null,
            )}
          </section>
        </div>
      </main>
    </>
  );
}

function countDone(turns: Turn[]): number {
  return turns.reduce(
    (sum, turn) => sum + Object.values(turn.tools).filter((tool) => tool.status === "done").length,
    0,
  );
}

function chime() {
  const Ctx = window.AudioContext || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!Ctx) return;
  const ctx = new Ctx();
  const osc = ctx.createOscillator();
  const gain = ctx.createGain();
  osc.frequency.value = 740;
  gain.gain.setValueAtTime(0.03, ctx.currentTime);
  gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.18);
  osc.connect(gain).connect(ctx.destination);
  osc.start();
  osc.stop(ctx.currentTime + 0.2);
  osc.onended = () => void ctx.close();
}
