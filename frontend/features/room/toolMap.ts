import { asLevel, asWarmth, cssColor } from "@/lib/fluffy/levels";

export { asLevel, asWarmth, cssColor };

/** Door and phone-call aliases are scenery or unsupported. They are not exposed as controls. */
export const SUPPORTED_TOOLS = [
  "dim_lights",
  "set_brightness",
  "set_light_warmth",
  "flash_lights",
  "set_blinds",
  "play_sound",
  "stop_sound",
  "show_caption",
  "announce",
  "alert_caregiver",
] as const;

export function toolSupported(name: string): boolean {
  return (SUPPORTED_TOOLS as readonly string[]).includes(name);
}
