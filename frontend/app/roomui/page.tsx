"use client";

import dynamic from "next/dynamic";

const RoomBoard = dynamic(() => import("@/features/roomui/RoomBoard").then((mod) => mod.RoomBoard), {
  ssr: false,
  loading: () => <div className="grid h-screen place-items-center text-sm text-neutral-500">Opening the room…</div>,
});

export default function RoomUiPage() {
  return <RoomBoard />;
}
