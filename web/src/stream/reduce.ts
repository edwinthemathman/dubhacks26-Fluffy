import type {
  AlertItem,
  FluffyEvent,
  HomeState,
  Snapshot,
  ToolRow,
  Turn,
} from "./events";
import { emptySnapshot } from "./events";

const MAX_TURNS = 20;

function mergeHome(home: HomeState, event: Extract<FluffyEvent, { type: "state" }>): HomeState {
  return {
    lamp: { ...home.lamp, ...event.lamp },
    ceiling: { ...home.ceiling, ...event.ceiling },
    blinds: event.blinds ?? home.blinds,
    door: { ...home.door, ...event.door },
    speaker: { ...home.speaker, ...event.speaker },
    phone: { ...home.phone, ...event.phone },
    flash: { ...home.flash, ...event.flash },
    caption: event.caption === undefined ? home.caption : event.caption,
  };
}

function mapTurn(turns: Turn[], id: string, fn: (turn: Turn) => Turn): Turn[] {
  return turns.map((turn) => (turn.id === id ? fn(turn) : turn));
}

function findTurnByCall(turns: Turn[], callId: string): string | undefined {
  return turns.find((turn) => turn.tools[callId])?.id;
}

export function reduce(snapshot: Snapshot, event: FluffyEvent): Snapshot {
  switch (event.type) {
    case "state":
      return { ...snapshot, home: mergeHome(snapshot.home, event) };
    case "profile":
      return { ...snapshot, profile: event.profile, needs: event.needs ?? snapshot.needs };
    case "sense.see":
      return {
        ...snapshot,
        senses: {
          ...snapshot.senses,
          chips: event.chips,
          thumb: event.thumb ?? snapshot.senses.thumb,
          tags: event.tags ?? snapshot.senses.tags,
        },
      };
    case "phone":
      return {
        ...snapshot,
        senses: { ...snapshot.senses, hear: event.text },
        home: {
          ...snapshot.home,
          phone: { mode: "reply", to: event.from ?? snapshot.home.phone.to, preview: event.text, delivered: true },
        },
      };
    case "sense.hear": {
      const text = event.text?.trim() ?? "";
      const next: Snapshot = {
        ...snapshot,
        senses: {
          ...snapshot.senses,
          hear: text || snapshot.senses.hear,
          sound: event.sound ?? (text ? undefined : snapshot.senses.sound),
        },
      };
      if (!text) return next;
      if (event.turn_id && next.turns.some((turn) => turn.id === event.turn_id)) {
        return {
          ...next,
          turns: mapTurn(next.turns, event.turn_id, (turn) => ({
            ...turn,
            lines: [...turn.lines, { type: "heard", text }],
          })),
        };
      }
      return next;
    }
    case "turn.start": {
      const heard = [snapshot.senses.sound, snapshot.senses.hear].filter(Boolean).join(" · ");
      const turn: Turn = {
        id: event.turn_id,
        kind: event.kind,
        trigger: event.trigger,
        lines: heard ? [{ type: "heard", text: heard }] : [],
        tools: {},
      };
      return { ...snapshot, turns: [turn, ...snapshot.turns].slice(0, MAX_TURNS) };
    }
    case "think":
      return {
        ...snapshot,
        turns: mapTurn(snapshot.turns, event.turn_id, (turn) => ({
          ...turn,
          lines: [...turn.lines, { type: "think", text: event.note }],
        })),
      };
    case "tool.call": {
      const row: ToolRow = {
        callId: event.call_id,
        tool: event.tool,
        args: event.args,
        why: event.why,
        kind: event.kind,
        status: "proposed",
      };
      let alerts = snapshot.alerts;
      if (event.tool === "alert_caregiver") {
        const message = String(event.args.message ?? "Check on Sam");
        const severity = String(event.args.severity ?? "info");
        const alert: AlertItem = { id: event.call_id, message, severity, acked: false };
        alerts = [alert, ...snapshot.alerts];
      }
      return {
        ...snapshot,
        alerts,
        turns: mapTurn(snapshot.turns, event.turn_id, (turn) => ({
          ...turn,
          lines: [...turn.lines, { type: "tool", callId: event.call_id }],
          tools: { ...turn.tools, [event.call_id]: row },
        })),
      };
    }
    case "tool.status": {
      const turnId = findTurnByCall(snapshot.turns, event.call_id);
      if (!turnId) return snapshot;
      return {
        ...snapshot,
        turns: mapTurn(snapshot.turns, turnId, (turn) => {
          const current = turn.tools[event.call_id];
          if (!current) return turn;
          return {
            ...turn,
            tools: {
              ...turn.tools,
              [event.call_id]: {
                ...current,
                status: event.status,
                detail: event.detail ?? current.detail,
                planId: event.plan_id ?? current.planId,
              },
            },
          };
        }),
      };
    }
    case "ask":
      return {
        ...snapshot,
        turns: mapTurn(snapshot.turns, event.turn_id, (turn) => ({
          ...turn,
          lines: [
            ...turn.lines,
            { type: "ask", question: event.question, choices: event.choices ?? ["yes", "no"] },
          ],
        })),
      };
    case "ask.answer":
      return {
        ...snapshot,
        turns: mapTurn(snapshot.turns, event.turn_id, (turn) => {
          let filled = false;
          return {
            ...turn,
            lines: turn.lines.map((line) => {
              if (filled || line.type !== "ask" || line.answer) return line;
              filled = true;
              return { ...line, answer: { by: event.by, choice: event.choice } };
            }),
          };
        }),
      };
    case "say": {
      const withSpeech = {
        ...snapshot,
        speech: event.text,
        speechId: event.say_id ?? "",
        speechVoice: Boolean(event.voice),
      };
      if (!event.turn_id) return withSpeech;
      return {
        ...withSpeech,
        turns: mapTurn(withSpeech.turns, event.turn_id, (turn) => ({
          ...turn,
          lines: [...turn.lines, { type: "say", text: event.text, voice: Boolean(event.voice), sayId: event.say_id }],
        })),
      };
    }
    case "turn.end":
      return {
        ...snapshot,
        turns: mapTurn(snapshot.turns, event.turn_id, (turn) => ({
          ...turn,
          ms: event.ms,
          outcome: event.outcome,
        })),
      };
    case "turn.raw":
      return {
        ...snapshot,
        turns: mapTurn(snapshot.turns, event.turn_id, (turn) => ({ ...turn, raw: event.text })),
      };
    case "safety":
      return {
        ...snapshot,
        safety: event.state,
        alerts:
          event.state === "acked"
            ? snapshot.alerts.map((alert) => ({ ...alert, acked: true }))
            : snapshot.alerts,
      };
    case "fallback":
      return { ...snapshot, fallback: event.active };
    default:
      return snapshot;
  }
}

export function freshSnapshot(link: Snapshot["link"]): Snapshot {
  return { ...emptySnapshot(), link };
}
