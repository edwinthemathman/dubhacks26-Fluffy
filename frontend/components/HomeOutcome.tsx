import type { ActionModel, QuestionModel } from "@/lib/fluffy/types";
import { toolTitle } from "@/lib/fluffy/phrases";

export default function HomeOutcome({
  actions,
  recorded,
  recordedRows,
  spoken,
  question,
  answering,
  onAnswer,
  onRecordedConfirm,
  recordedConfirm,
  recordedConfirmed,
}: {
  actions: ActionModel[];
  recorded?: boolean;
  recordedRows?: { label: string; from: string; to: string }[];
  spoken?: string | null;
  question: QuestionModel | null;
  answering: boolean;
  onAnswer: (choice: string) => void;
  onRecordedConfirm?: () => void;
  recordedConfirm?: { question: string } | null;
  recordedConfirmed?: boolean;
}) {
  return (
    <div className="flex flex-col gap-3 p-4">
      <p className="text-xs text-neutral-500">
        {recorded ? "Recorded demo. This does not change the home." : "Live session"}
      </p>
      <ul className="flex flex-col gap-2">
        {recorded
          ? (recordedRows ?? []).map((row, i) => (
              <li key={row.label + row.to} className="animate-pop-in" style={{ animationDelay: `${i * 90}ms` }}>
                <Row label={row.label} detail={`${row.from} → ${row.to}`} status="Recorded" />
              </li>
            ))
          : actions.map((action, i) => (
              <li key={action.id} className="animate-pop-in" style={{ animationDelay: `${i * 90}ms` }}>
                <Row label={toolTitle(action.tool)} detail={action.summary ?? action.status} status={statusChip(action.status)} />
              </li>
            ))}
      </ul>
      {spoken ? (
        <div className="rounded-2xl border border-neutral-200 px-4 py-3">
          <p className="text-[11px] font-medium uppercase tracking-wide text-neutral-400">Fluffy said</p>
          <p className="mt-1 text-[15px] leading-relaxed">{spoken}</p>
        </div>
      ) : null}
      {question && !recorded ? (
        <div className="rounded-2xl border border-neutral-200 px-4 py-3">
          <p className="text-[15px] leading-relaxed">{question.text}</p>
          <div className="mt-3 flex flex-wrap gap-2">
            {(question.choices.length ? question.choices : ["Yes", "No"]).map((choice) => (
              <button
                key={choice}
                type="button"
                disabled={answering}
                onClick={() => onAnswer(choice)}
                className="rounded-xl bg-neutral-900 px-3 py-2 text-sm font-medium tracking-tight text-white disabled:opacity-40"
              >
                {choice}
              </button>
            ))}
          </div>
        </div>
      ) : null}
      {recorded && recordedConfirm && !recordedConfirmed ? (
        <div className="rounded-2xl border border-neutral-200 px-4 py-3">
          <p className="text-[15px] leading-relaxed">{recordedConfirm.question}</p>
          <button type="button" onClick={onRecordedConfirm} className="mt-3 rounded-xl bg-neutral-900 px-3 py-2 text-sm font-medium tracking-tight text-white">
            Yes
          </button>
          <p className="mt-2 text-xs text-neutral-500">Recorded answer. Nothing is sent to the home.</p>
        </div>
      ) : null}
    </div>
  );
}

function statusChip(status: ActionModel["status"]): string {
  if (status === "proposed") return "Proposed";
  if (status === "approved") return "Approved";
  if (status === "started") return "Started";
  if (status === "verified") return "Verified";
  if (status === "accepted") return "Submitted";
  if (status === "delivered") return "Delivered";
  if (status === "failed") return "Failed";
  if (status === "rejected") return "Rejected";
  return "Unknown";
}

function Row({ label, detail, status }: { label: string; detail: string; status: string }) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-2xl border border-neutral-200 px-4 py-3">
      <div>
        <p className="text-[15px] font-medium tracking-tight capitalize">{label}</p>
        <p className="text-sm text-neutral-600">{detail}</p>
      </div>
      <span className="shrink-0 rounded-md bg-neutral-100 px-2 py-0.5 text-[11px] font-medium text-neutral-600">{status}</span>
    </div>
  );
}
