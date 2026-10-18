import { Bell, Eye, Hand, LifeBuoy } from "lucide-react";
import { useEffect } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { FluffyAvatar } from "../../components/shared/FluffyAvatar";
import { ThemeToggle } from "../../components/shared/ThemeToggle";
import styles from "./Landing.module.css";

const SCENES = [
  { icon: Eye, label: "Describes and finds", to: "describe_find" },
  { icon: Bell, label: "Sounds become light", to: "doorbell" },
  { icon: Hand, label: "Nods and buttons count", to: "hands_free" },
  { icon: LifeBuoy, label: "Checks if you fell", to: "fall" },
];

export function Landing() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const search = params.toString();
  const suffix = search ? `?${search}` : "";

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Enter" && event.target === document.body) navigate(`/demo${suffix}`);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navigate, suffix]);

  return (
    <main id="main" className={styles.landing}>
      <div className={styles.theme}>
        <ThemeToggle />
      </div>
      <p className={styles.kicker}>DubHacks ’26</p>
      <FluffyAvatar state="idle" size={148} />
      <h1>Fluffy</h1>
      <p className={styles.tag}>A home that sees, listens, and asks first.</p>
      <ul className={styles.chips}>
        {SCENES.map((scene) => (
          <li key={scene.to}>
            <Link to={`/demo?mock=${scene.to}`}>
              <scene.icon size={18} aria-hidden="true" />
              {scene.label}
            </Link>
          </li>
        ))}
      </ul>
      <Link className={`primary-button ${styles.enter}`} to={`/demo${suffix}`}>
        Enter
      </Link>
      <a className={styles.mind} href={`/mind${suffix}`} target="fluffy-mind">
        Open Fluffy’s mind on a second screen
      </a>
      <p className={styles.note}>
        Prototype. Not a medical device. Camera frames stay in memory.
      </p>
    </main>
  );
}
