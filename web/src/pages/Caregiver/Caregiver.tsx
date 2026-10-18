import { TopBar } from "../../components/shared/TopBar";
import { useFluffyStream } from "../../stream/FluffyStream";
import styles from "./Caregiver.module.css";

const STATUS: Record<string, string> = {
  ok: "Sam: living room, okay",
  checkin1: "Sam: living room, check-in",
  checkin2: "Sam: living room, second check-in",
  alerted: "Sam: living room, needs you",
  acked: "Sam: living room, acknowledged",
};

export function Caregiver() {
  const { snapshot, send } = useFluffyStream();
  const urgent = snapshot.safety === "alerted" || snapshot.safety === "checkin2";

  return (
    <>
      <TopBar />
      <main id="main" className={`page ${styles.care}`}>
        <section className={`card ${styles.status} ${urgent ? styles.urgent : ""}`}>
          <p className={styles.kicker}>Caregiver</p>
          <h1>{STATUS[snapshot.safety]}</h1>
          <button
            type="button"
            className="primary-button"
            onClick={() => send({ type: "caregiver.ack" })}
            disabled={snapshot.alerts.length === 0 || snapshot.safety === "acked"}
          >
            Acknowledge
          </button>
        </section>
        <section className={`card ${styles.list}`} aria-label="Alerts">
          <h2>Timeline</h2>
          {snapshot.alerts.length === 0 && <p className="muted">No alerts.</p>}
          <ol>
            {snapshot.alerts.map((alert) => (
              <li key={alert.id}>
                <strong>{alert.severity}</strong>
                <span>{alert.message}</span>
                <em>{alert.acked ? "acknowledged" : "open"}</em>
              </li>
            ))}
          </ol>
        </section>
        <p className={styles.note}>Simulated alert. Fluffy is not a medical device.</p>
      </main>
    </>
  );
}
