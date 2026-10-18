import type { DeviceChange, Scene } from "@/lib/scenes";
import { roomLabel } from "@/lib/scenes";

export default function HomeOutcome({
  scene,
  confirmed,
  onConfirm,
}: {
  scene: Scene;
  confirmed: boolean;
  onConfirm: () => void;
}) {
  const changes = confirmed && scene.confirm
    ? [...scene.changes, scene.confirm.then]
    : scene.changes;

  return (
    <div className="flex flex-col gap-3 p-4">
      <p className="text-xs capitalize text-neutral-500">
        <span className="font-medium text-neutral-700">
          {roomLabel(scene.plan.trigger.room)}
        </span>
        {" · "}
        {(scene.tookMs / 1000).toFixed(1)}s
        {" · "}
        {scene.chime ? "chime" : "silent"}
        {" · recorded scene"}
      </p>

      <ul className="flex flex-col gap-2">
        {changes.map((change, i) => (
          <li
            key={change.device + change.to}
            className="animate-pop-in"
            style={{ animationDelay: `${i * 90}ms` }}
          >
            <ChangeRow change={change} />
          </li>
        ))}
        {scene.skipped.map((skip, i) => (
          <li
            key={skip.label}
            className="animate-pop-in flex items-center justify-between gap-3 rounded-2xl border border-dashed border-neutral-200 px-4 py-3"
            style={{ animationDelay: `${(changes.length + i) * 90}ms` }}
          >
            <p className="text-[15px] font-medium text-neutral-500">{skip.label}</p>
            <p className="text-sm text-neutral-400">{skip.why}</p>
          </li>
        ))}
      </ul>

      {scene.spoken && (
        <div className="rounded-2xl border border-neutral-200 px-4 py-3">
          <p className="text-[11px] font-medium uppercase tracking-wide text-neutral-400">
            Fluffy asked
          </p>
          <p className="mt-1 text-[15px] leading-relaxed">{scene.spoken}</p>
          {scene.confirm && !confirmed ? (
            <button
              type="button"
              onClick={onConfirm}
              className="mt-3 rounded-xl bg-neutral-900 px-3 py-1.5 text-sm font-medium tracking-tight text-white"
            >
              Yes
            </button>
          ) : scene.confirm && confirmed ? (
            <p className="mt-2 text-xs text-neutral-500">You said yes.</p>
          ) : null}
        </div>
      )}
    </div>
  );
}

function ChangeRow({ change }: { change: DeviceChange }) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-2xl border border-neutral-200 px-4 py-3">
      <p className="text-[15px] font-medium tracking-tight">{change.label}</p>
      <p className="text-sm text-neutral-600">
        <span className="text-neutral-400">{change.from}</span>
        <span className="px-1.5 text-neutral-300">→</span>
        <span className="font-medium text-neutral-900">{change.to}</span>
      </p>
    </div>
  );
}
