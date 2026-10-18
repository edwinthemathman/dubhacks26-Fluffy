import { Check } from "lucide-react";
import type { ToolStatus } from "../../stream/events";

const LABEL: Record<ToolStatus, string> = {
  proposed: "proposed",
  asking: "asking",
  approved: "approved",
  done: "done",
  rejected: "rejected",
  declined: "declined",
  failed: "failed",
};

export function StatusPill({ status }: { status: ToolStatus }) {
  return (
    <span className={`status ${status}`}>
      {status === "done" && <Check size={12} aria-hidden="true" />}
      {LABEL[status]}
    </span>
  );
}
