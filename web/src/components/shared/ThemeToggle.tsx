import { Moon, Sun } from "lucide-react";
import { useEffect, useState } from "react";

export function ThemeToggle() {
  const [dark, setDark] = useState(() => document.documentElement.dataset.theme === "dark");

  useEffect(() => {
    document.documentElement.dataset.theme = dark ? "dark" : "light";
    localStorage.setItem("fluffy.theme", dark ? "dark" : "light");
  }, [dark]);

  return (
    <button
      type="button"
      className="icon-button"
      aria-label={dark ? "Switch to light theme" : "Switch to dark theme"}
      onClick={() => setDark((value) => !value)}
    >
      {dark ? <Sun size={18} /> : <Moon size={18} />}
    </button>
  );
}
