"use client";

import { usePathname } from "next/navigation";
import { useEffect, useRef, useState, type ReactNode } from "react";
import FluffyBrand from "@/components/FluffyBrand";
import Sidebar from "@/components/layout/Sidebar";
import { useSession } from "@/providers/FluffySessionProvider";

export default function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const [openPath, setOpenPath] = useState<string | null>(null);
  const open = openPath === pathname;

  return (
    <div className="flex min-h-full bg-white">
      <div className="sticky top-0 hidden h-screen md:block">
        <Sidebar />
      </div>
      <div className="flex min-h-screen min-w-0 flex-1 flex-col">
        <MobileBar onOpen={() => setOpenPath(pathname)} />
        <div className="relative min-w-0 flex-1">{children}</div>
      </div>
      {open ? <MobileDrawer onClose={() => setOpenPath(null)} /> : null}
    </div>
  );
}

function MobileBar({ onOpen }: { onOpen: () => void }) {
  const session = useSession();
  const started = session.sensing.camera === "on" || session.sensing.camera === "starting";
  return (
    <header className="sticky top-0 z-30 flex h-14 items-center justify-between border-b border-[#E5E5E5] bg-[#FAFAFA] px-4 md:hidden">
      <button
        type="button"
        onClick={onOpen}
        className="h-11 rounded-xl px-3 text-sm font-medium outline-none focus-visible:ring-2 focus-visible:ring-neutral-400"
        aria-haspopup="dialog"
      >
        Menu
      </button>
      <FluffyBrand className="text-[22px]" />
      <button
        type="button"
        onClick={() => void (started ? session.pauseFluffy() : session.startFluffy())}
        className="h-11 rounded-xl bg-neutral-900 px-3 text-sm font-medium text-white"
      >
        {started ? (session.paused ? "Resume" : "Pause") : "Start"}
      </button>
    </header>
  );
}

function MobileDrawer({ onClose }: { onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    closeRef.current?.focus();
    const root = ref.current;
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== "Tab" || !root) return;
      const items = [...root.querySelectorAll<HTMLElement>("a, button")].filter((node) => !node.hasAttribute("disabled"));
      const first = items[0];
      const last = items[items.length - 1];
      if (!first || !last) return;
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      previous?.focus();
    };
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-40 md:hidden">
      <button type="button" aria-label="Close menu" className="absolute inset-0 bg-black/20" onClick={onClose} />
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-label="Navigation"
        className="absolute inset-y-0 left-0 flex w-[min(100%,280px)] flex-col bg-[#FAFAFA] shadow-xl"
      >
        <div className="flex justify-end px-3 pt-3">
          <button ref={closeRef} type="button" onClick={onClose} className="h-11 rounded-xl px-3 text-sm font-medium">
            Close
          </button>
        </div>
        <Sidebar className="!w-full border-r-0" />
      </div>
    </div>
  );
}
