import { FormEvent, useRef, useState } from "react";
import { TopBar } from "../../components/shared/TopBar";
import { useVoice } from "../../live/useVoice";
import { openQuestion, useFluffyStream } from "../../stream/FluffyStream";
import styles from "./Companion.module.css";

export function Companion() {
  const { snapshot, send } = useFluffyStream();
  const question = openQuestion(snapshot.turns);
  const [text, setText] = useState("");
  const [playHere, setPlayHere] = useState(false);
  const speaking = useRef(false);
  const voice = useVoice({
    enabled: playHere,
    sayId: snapshot.speechId,
    voice: snapshot.speechVoice,
    text: snapshot.speech,
    rate: snapshot.profile === "speech_cognitive" ? 0.85 : 1,
    send,
    speakingRef: speaking,
  });

  const answer = (choice: "yes" | "no" | "help") => {
    send({ type: "answer", choice, by: "button" });
  };

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    const value = text.trim();
    if (!value) return;
    send({ type: "typed", text: value });
    setText("");
  };

  const caption = question?.question || snapshot.speech || snapshot.home.caption || "Fluffy is here.";

  return (
    <>
      <TopBar />
      <main id="main" className={styles.companion}>
        <p className={styles.caption} role="status" aria-live="polite">
          {caption}
        </p>
        <div className={styles.actions}>
          <button
            type="button"
            className={styles.yes}
            onClick={() => {
              // #region agent log
              fetch("http://127.0.0.1:7643/ingest/889f4935-7c87-40ef-88c8-e26de67696a8",{method:"POST",headers:{"Content-Type":"application/json","X-Debug-Session-Id":"9d207c"},body:JSON.stringify({sessionId:"9d207c",hypothesisId:"D",location:"Companion.tsx:yes",message:"yes clicked",data:{hasQuestion:Boolean(question)},timestamp:Date.now()})}).catch(()=>{});
              // #endregion
              answer("yes");
            }}
            disabled={!question}
          >
            Yes
          </button>
          <button type="button" className={styles.no} onClick={() => answer("no")} disabled={!question}>
            No
          </button>
          <button type="button" className={styles.help} onClick={() => answer("help")}>
            Need help
          </button>
        </div>
        <button
          type="button"
          className={styles.voice}
          aria-pressed={playHere}
          onClick={() => {
            if (!playHere) voice.unlock();
            setPlayHere((on) => !on);
          }}
        >
          Play Fluffy's voice here
        </button>
        <button
          type="button"
          className={styles.voice}
          onClick={() => {
            // #region agent log
            fetch("http://127.0.0.1:7643/ingest/889f4935-7c87-40ef-88c8-e26de67696a8",{method:"POST",headers:{"Content-Type":"application/json","X-Debug-Session-Id":"9d207c"},body:JSON.stringify({sessionId:"9d207c",hypothesisId:"A",location:"Companion.tsx:scan",message:"scan clicked",data:{hasQuestion:Boolean(question),link:snapshot.link,tags:snapshot.senses.tags.length},timestamp:Date.now()})}).catch(()=>{});
            // #endregion
            send({ type: "room.scan" });
          }}
        >
          Scan the room
        </button>
        <form onSubmit={onSubmit} className={styles.form}>
          <label htmlFor="typed">Type instead</label>
          <input
            id="typed"
            value={text}
            onChange={(event) => setText(event.target.value)}
            placeholder="Say it in writing"
            autoComplete="off"
          />
          <button type="submit" className="primary-button">
            Send
          </button>
        </form>
      </main>
    </>
  );
}
