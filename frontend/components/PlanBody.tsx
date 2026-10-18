import type { ReactNode } from "react";
import {
  EVIDENCE_LABEL,
  roomLabel,
  toolLabel,
  type Evidence,
  type Plan,
} from "@/lib/scenes";

function SectionTitle({ children }: { children: ReactNode }) {
  return (
    <h3 className="text-[13px] font-semibold text-neutral-400">{children}</h3>
  );
}

function formatArg(value: string | number | boolean) {
  if (typeof value === "boolean") return value ? "on" : "off";
  return String(value);
}

export default function PlanBody({
  plan,
  view,
}: {
  plan: Plan;
  view: "intent" | "actions" | "evidence" | "json";
}) {
  if (view === "json") {
    return (
      <pre className="overflow-x-auto rounded-xl bg-neutral-50 p-4 font-mono text-[13px] leading-relaxed text-neutral-800">
        {JSON.stringify({ type: "plan.proposed", schema_version: "1.0", plan }, null, 2)}
      </pre>
    );
  }

  if (view === "actions") {
    return (
      <ol className="divide-y divide-neutral-100 rounded-2xl border border-neutral-200">
        {plan.actions.map((action, i) => (
          <li key={action.action_id} className="flex items-start gap-3 px-3 py-3">
            <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-md bg-neutral-100 text-[11px] font-medium text-neutral-500">
              {i + 1}
            </span>
            <div className="flex min-w-0 flex-1 flex-col gap-2">
              <div className="flex items-start justify-between gap-3">
                <p className="text-[15px] font-medium capitalize leading-relaxed">
                  {toolLabel(action.tool)}
                </p>
                <span className="shrink-0 rounded-md bg-neutral-100 px-2 py-0.5 text-[11px] font-medium text-neutral-600">
                  {action.sensitivity}
                </span>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {Object.entries(action.args).map(([key, value]) => (
                  <span
                    key={key}
                    className="rounded-md border border-neutral-200 bg-white px-2 py-0.5 text-xs text-neutral-700"
                  >
                    <span className="text-neutral-400">{key.replaceAll("_", " ")}</span>{" "}
                    {formatArg(value)}
                  </span>
                ))}
              </div>
            </div>
          </li>
        ))}
      </ol>
    );
  }

  if (view === "evidence") {
    return (
      <ul className="flex flex-col divide-y divide-neutral-100 rounded-2xl border border-neutral-200">
        {plan.evidence.map((item, i) => (
          <li key={i} className="flex items-start gap-3 px-3 py-3">
            <EvidenceRow item={item} />
          </li>
        ))}
      </ul>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <p className="text-[15px] leading-relaxed">{plan.intent}</p>
      <p className="text-sm leading-relaxed text-neutral-600">
        {plan.reasoning_summary}
      </p>
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="rounded-md bg-neutral-100 px-2 py-0.5 text-[11px] font-medium capitalize text-neutral-600">
          {plan.trigger.kind}
        </span>
        <span className="rounded-md bg-neutral-100 px-2 py-0.5 text-[11px] font-medium capitalize text-neutral-600">
          {roomLabel(plan.trigger.room)}
        </span>
        <span className="rounded-md bg-violet-50 px-2 py-0.5 font-mono text-[11px] font-medium text-violet-700">
          {plan.confidence.toFixed(2)}
        </span>
        {plan.requires_confirmation && (
          <span className="rounded-md bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-700">
            Needs a yes
          </span>
        )}
      </div>
    </div>
  );
}

function EvidenceRow({ item }: { item: Evidence }) {
  if (item.kind === "speech") {
    return (
      <p className="text-[15px] leading-relaxed">
        <span className="text-neutral-400">Heard </span>
        <span className="font-medium">&ldquo;{item.text}&rdquo;</span>
      </p>
    );
  }
  if (item.kind === "vision") {
    return (
      <p className="text-[15px] leading-relaxed">
        <span className="text-neutral-400">Saw </span>
        {item.summary}
        <span className="text-neutral-400"> · {roomLabel(item.camera)}</span>
      </p>
    );
  }
  return (
    <p className="text-[15px] leading-relaxed">
      <span className="font-medium">{item.device_id}</span>
      <span className="text-neutral-400"> was </span>
      {Object.entries(item.value)
        .map(([k, v]) => `${k.replaceAll("_", " ")} ${formatArg(v)}`)
        .join(", ")}
    </p>
  );
}

export function Noticed({
  plan,
  done,
}: {
  plan: Plan;
  done: boolean;
}) {
  const kinds = [...new Set(plan.evidence.map((e) => e.kind))];
  return (
    <section className="flex flex-col gap-2">
      <SectionTitle>{done ? "Noticed" : "Noticing"}</SectionTitle>
      <div className="flex flex-wrap items-center gap-1.5">
        {kinds.map((kind, i) => (
          <span
            key={kind}
            className="animate-pop-in rounded-md border border-neutral-200 bg-white px-2 py-0.5 text-xs text-neutral-700"
            style={{ animationDelay: `${i * 220}ms` }}
          >
            {EVIDENCE_LABEL[kind]}
          </span>
        ))}
      </div>
    </section>
  );
}
