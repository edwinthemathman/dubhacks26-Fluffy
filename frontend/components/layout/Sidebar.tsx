"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import FluffyBrand from "@/components/FluffyBrand";
import { useSession } from "@/providers/FluffySessionProvider";

export default function Sidebar({ className = "" }: { className?: string }) {
  return (
    <aside className={`flex h-full w-[232px] shrink-0 flex-col border-r border-[#E5E5E5] bg-[#FAFAFA] px-5 py-5 ${className}`}>
      <FluffyBrand className="px-2 text-[26px] leading-none" />
      <nav className="mt-6 flex flex-col gap-1" aria-label="Primary">
        <NavLink href="/" label="Fluffy" icon={<ChatIcon />} />
        <NavLink href="/room" label="Room" icon={<RoomIcon />} />
      </nav>
      <SidebarStatus />
    </aside>
  );
}

function NavLink({ href, label, icon }: { href: string; label: string; icon: ReactNode }) {
  const pathname = usePathname();
  const active = href === "/" ? pathname === "/" : pathname === href || pathname.startsWith(`${href}/`);
  return (
    <Link
      href={href}
      aria-current={active ? "page" : undefined}
      className={
        "flex h-11 items-center gap-3 rounded-xl px-3 text-[15px] outline-none focus-visible:ring-2 focus-visible:ring-neutral-400 " +
        (active ? "bg-neutral-200/80 font-medium text-neutral-950" : "text-neutral-500 hover:bg-neutral-100 hover:text-neutral-800")
      }
    >
      <span className="text-current" aria-hidden>
        {icon}
      </span>
      {label}
    </Link>
  );
}

function SidebarStatus() {
  const session = useSession();
  const started = session.sensing.camera === "on" || session.sensing.camera === "starting";
  const connection = session.state.connection === "open" ? "Connected" : session.state.connection === "offline" ? "Offline" : "Reconnecting";
  const sensing = describeSensing(session.sensing.camera, session.sensing.transport, session.paused, session.state.capabilities.inference === "available");
  return (
    <div className="mt-auto flex flex-col gap-3 pt-6">
      <p className="px-2 text-xs leading-relaxed text-neutral-500">
        <span className="block text-neutral-700">{connection}</span>
        <span className="block">{sensing}</span>
        {session.state.executorStatus ? <span className="block">{session.state.executorStatus}</span> : null}
      </p>
      <div className="flex gap-2">
        {started ? (
          <button type="button" onClick={session.pauseFluffy} className="h-11 flex-1 rounded-xl border border-neutral-200 bg-white text-sm font-medium">
            {session.paused ? "Resume" : "Pause"}
          </button>
        ) : (
          <button type="button" onClick={() => void session.startFluffy()} className="h-11 flex-1 rounded-xl bg-neutral-900 text-sm font-medium text-white">
            Start
          </button>
        )}
        {started ? (
          <button type="button" onClick={() => void session.stopFluffy()} className="h-11 rounded-xl border border-neutral-200 bg-white px-3 text-sm font-medium">
            Stop
          </button>
        ) : null}
      </div>
    </div>
  );
}

function describeSensing(camera: string, transport: string, paused: boolean, perception: boolean): string {
  if (camera === "denied") return "Camera permission denied";
  if (camera === "absent") return "No camera found";
  if (camera === "error") return "Camera unavailable";
  if (camera !== "on") return "Camera off";
  if (paused) return "Sensing paused";
  if (transport === "unsupported") return "Camera preview only";
  if (transport === "offline") return "Media transport offline";
  if (transport === "reaching" && perception) return "Watching and listening";
  if (transport === "reaching") return "Media uploading";
  return "Camera on";
}

function ChatIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
      <path d="M6 17.5 4 20V6.5A2.5 2.5 0 0 1 6.5 4h11A2.5 2.5 0 0 1 20 6.5v8A2.5 2.5 0 0 1 17.5 17H8z" />
    </svg>
  );
}

function RoomIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
      <path d="M4 10.5 12 4l8 6.5V20a1 1 0 0 1-1 1h-5.2v-6.2H10.2V21H5a1 1 0 0 1-1-1z" />
    </svg>
  );
}
