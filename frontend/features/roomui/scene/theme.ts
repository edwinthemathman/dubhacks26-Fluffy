export type RoomThemeName = "light" | "dark";

export type ThemeColors = {
  bg: string;
  floor: string;
  wall: string;
  wallSide: string;
  rug: string;
  trim: string;
  shade: string;
  metal: string;
  wood: string;
  speaker: string;
  phone: string;
  outline: string;
  fail: string;
  ink: string;
  bubble: string;
  note: string;
};

export function themeColors(theme: RoomThemeName): ThemeColors {
  if (theme === "dark") {
    return {
      bg: "#171717",
      floor: "#262626",
      wall: "#1f1f1f",
      wallSide: "#1a1a1a",
      rug: "#333333",
      trim: "#a3a3a3",
      shade: "#404040",
      metal: "#d4d4d4",
      wood: "#6e543c",
      speaker: "#0a0a0a",
      phone: "#0a0a0a",
      outline: "#e5e5e5",
      fail: "#fca5a5",
      ink: "#fafafa",
      bubble: "#262626",
      note: "#e5e5e5",
    };
  }
  return {
    bg: "#f7f7f5",
    floor: "#e7e2d8",
    wall: "#f4f1ea",
    wallSide: "#ebe6dc",
    rug: "#ddd4c4",
    trim: "#d6d3d1",
    shade: "#f3efe6",
    metal: "#8a8680",
    wood: "#c4b29a",
    speaker: "#3f3f46",
    phone: "#27272a",
    outline: "#3f3f46",
    fail: "#b91c1c",
    ink: "#171717",
    bubble: "#ffffff",
    note: "#404040",
  };
}

export function warmthColor(warmth: "warm" | "neutral" | "cool" | null): string {
  if (warmth === "warm") return "#e7a15a";
  if (warmth === "cool") return "#c5d4ea";
  if (warmth === "neutral") return "#f4efe4";
  return "#d4d4d4";
}

export function channelColor(channel: string | undefined): string {
  if (channel === "sms") return "#3f6b4e";
  if (channel === "whatsapp") return "#3d6b63";
  if (channel === "imessage") return "#3f3f46";
  return "#52525b";
}
