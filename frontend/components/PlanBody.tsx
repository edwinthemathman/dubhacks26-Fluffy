import type { ReactNode } from "react";
import type { EvidenceRow, LivePlan } from "@/lib/fluffy/types";
import { toolTitle } from "@/lib/fluffy/phrases";

function SectionTitle({ children }: { children: ReactNode }) {
  return <h3 className="text-[13px] font-semibold text-neutral-400">{children}</h3>;
}

function formatArg(value: unknown) {
  if (typeof value === "boolean") return value ? "on" : "off";
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  if (typeof value === "string") return value;
  return "";
}

export default function PlanBody({
  plan,
  view,
}: {
  plan: LivePlan;
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
          <li key={action.actionId} className="flex items-start gap-3 px-3 py-3">
            <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-md bg-neutral-100 text-[11px] font-medium text-neutral-500">
              {i + 1}
            </span>
            <div className="flex min-w-0 flex-1 flex-col gap-2">
              <p className="text-[15px] font-medium capitalize leading-relaxed">{toolTitle(action.tool)}</p>
              <div className="flex flex-wrap gap-1.5">
                {Object.entries(action.args).map(([key, value]) => (
                  <span key={key} className="rounded-md border border-neutral-200 bg-white px-2 py-0.5 text-xs text-neutral-700">
                    <span className="text-neutral-400">{key.replaceAll("_", " ")}</span> {formatArg(value)}
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
          <li key={i} className="px-3 py-3 text-[15px] leading-relaxed">
            <EvidenceRowView item={item} />
          </li>
        ))}
      </ul>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <p className="text-[15px] leading-relaxed">{plan.intent}</p>
      {plan.reasoning ? <p className="text-sm leading-relaxed text-neutral-600">{plan.reasoning}</p> : null}
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="rounded-md bg-neutral-100 px-2 py-0.5 text-[11px] font-medium capitalize text-neutral-600">
          {plan.triggerKind.replaceAll("_", " ")}
        </span>
        {plan.room ? (
          <span className="rounded-md bg-neutral-100 px-2 py-0.5 text-[11px] font-medium capitalize text-neutral-600">
            {plan.room.replaceAll("_", " ")}
          </span>
        ) : null}
        {plan.confidence != null ? (
          <span className="rounded-md bg-violet-50 px-2 py-0.5 font-mono text-[11px] font-medium text-violet-700">
            {plan.confidence.toFixed(2)}
          </span>
        ) : null}
        {plan.consentRequired ? (
          <span className="rounded-md bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-700">Needs a yes</span>
        ) : null}
      </div>
    </div>
  );
}

function EvidenceRowView({ item }: { item: EvidenceRow }) {
  return (
    <p>
      <span className="text-neutral-400">{item.kind} </span>
      {item.text}
    </p>
  );
}

export function Noticed({
  evidence,
  done,
}: {
  evidence: EvidenceRow[];
  done: boolean;
}) {
  const kinds = [...new Set(evidence.map((item) => item.kind))];
  return (
    <section className="flex flex-col gap-2">
      <SectionTitle>{done ? "Noticed" : "Noticing"}</SectionTitle>
      <div className="flex flex-wrap items-center gap-1.5">
        {kinds.map((kind, i) => (
          <span
            key={kind}
            className="animate-pop-in rounded-md border border-neutral-200 bg-white px-2 py-0.5 text-xs capitalize text-neutral-700"
            style={{ animationDelay: `${i * 220}ms` }}
          >
            {kind}
          </span>
        ))}
      </div>
    </section>
  );
}
