import { asLevel, asWarmth, CEILING_ID, cssColor, LAMP_ID } from "@/lib/fluffy/levels";
import type { RoomObject } from "./state";

export { asLevel, asWarmth, cssColor };

/**
 * One tool name to the room objects it may mark.
 * open_door and close_door are included because the executor returns a
 * confirmed door in the action result. show_caption changes no object.
 */
const TOOL_TARGETS: Record<string, RoomObject[] | "dim"> = {
  dim_lights: "dim",
  set_brightness: "dim",
  set_light_warmth: ["lamp", "ceiling"],
  flash_lights: ["lamp", "ceiling"],
  set_blinds: ["blinds"],
  play_sound: ["speaker"],
  stop_sound: ["speaker"],
  play_music: ["speaker"],
  stop_music: ["speaker"],
  set_volume: ["speaker"],
  announce: ["phone"],
  alert_caregiver: ["phone"],
  show_caption: [],
  open_door: ["door"],
  close_door: ["door"],
};

export const SUPPORTED_TOOLS = Object.keys(TOOL_TARGETS);

export function toolSupported(name: string): boolean {
  return Object.prototype.hasOwnProperty.call(TOOL_TARGETS, name);
}

/** Unknown tools match nothing. A missing dim target means both configured lights. */
export function actionTargets(tool: string, args: Record<string, unknown>): RoomObject[] {
  const target = TOOL_TARGETS[tool];
  if (target == null) return [];
  if (target !== "dim") return target;
  if (args.light === CEILING_ID) return ["ceiling"];
  if (args.light === LAMP_ID) return ["lamp"];
  if (args.light == null) return ["lamp", "ceiling"];
  return [];
}
