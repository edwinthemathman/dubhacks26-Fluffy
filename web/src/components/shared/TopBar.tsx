import { NavLink, useLocation, useSearchParams } from "react-router-dom";
import { FluffyAvatar } from "./FluffyAvatar";
import { ThemeToggle } from "./ThemeToggle";

const LINKS = [
  { to: "/demo", label: "Demo" },
  { to: "/mind", label: "Mind" },
  { to: "/companion", label: "Companion" },
  { to: "/caregiver", label: "Caregiver" },
];

export function TopBar() {
  const [params] = useSearchParams();
  const { pathname } = useLocation();
  const search = params.toString();
  const suffix = search ? `?${search}` : "";

  return (
    <header className="topbar">
      <NavLink to={`/${suffix}`} className="brand">
        <FluffyAvatar state="idle" size={36} />
        Fluffy
      </NavLink>
      <nav className="nav-links" aria-label="Screens">
        {LINKS.map((link) => (
          <NavLink
            key={link.to}
            to={`${link.to}${suffix}`}
            aria-current={pathname === link.to ? "page" : undefined}
          >
            {link.label}
          </NavLink>
        ))}
      </nav>
      <ThemeToggle />
    </header>
  );
}
