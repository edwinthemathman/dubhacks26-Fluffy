"use client";

import dynamic from "next/dynamic";

const RoomCanvas = dynamic(() => import("@/components/room/RoomCanvas"), {
  ssr: false,
  loading: () => <div className="mx-auto min-h-[320px] max-w-5xl px-6 py-8 text-sm text-neutral-500 md:min-h-[480px]">Opening the room…</div>,
});

export default function RoomPage() {
  return <RoomCanvas />;
}
