import type { Scene } from "@/lib/scenes";
import type { CanonicalEvent, LivePlan } from "./types";

export function sceneToRecorded(scene: Scene, requestId: string, text: string): Extract<CanonicalEvent, { type: "recorded" }> {
  const plan: LivePlan = {
    planId: scene.plan.plan_id,
    createdAt: scene.plan.created_at,
    expiresAt: scene.plan.expires_at,
    intent: scene.plan.intent,
    reasoning: scene.plan.reasoning_summary,
    confidence: scene.plan.confidence,
    triggerKind: scene.plan.trigger.kind,
    room: scene.plan.trigger.room,
    consentRequired: scene.plan.requires_confirmation,
    evidence: scene.plan.evidence.map((item) => {
      if (item.kind === "speech") return { kind: "speech", text: `Heard “${item.text}”` };
      if (item.kind === "vision") return { kind: "vision", text: item.summary };
      return { kind: "state", text: item.device_id };
    }),
    actions: scene.plan.actions.map((action) => ({
      actionId: action.action_id,
      tool: action.tool,
      args: action.args,
    })),
  };
  return {
    type: "recorded",
    requestId,
    text,
    title: scene.title,
    plan,
    rows: scene.changes.map((change) => ({ label: change.label, from: change.from, to: change.to })),
    spoken: scene.spoken,
    confirm: scene.confirm
      ? { question: scene.confirm.question, then: { label: scene.confirm.then.label, from: scene.confirm.then.from, to: scene.confirm.then.to } }
      : null,
  };
}
