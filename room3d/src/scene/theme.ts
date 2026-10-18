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
      bg: "#071422",
      floor: "#0e243c",
      wall: "#163454",
      wallSide: "#10263f",
      rug: "#1a3c60",
      trim: "#8ec0ff",
      shade: "#234468",
      metal: "#9eb4c9",
      wood: "#6e543c",
      speaker: "#173049",
      phone: "#0c1c30",
      outline: "#8ec0ff",
      fail: "#ff8b8b",
      ink: "#f4f8fd",
      bubble: "#10263f",
      note: "#8ec0ff",
    };
  }
  return {
    bg: "#f3f7fc",
    floor: "#d5e5f6",
    wall: "#f7fbff",
    wallSide: "#e7f1fd",
    rug: "#c5daf2",
    trim: "#0c4dad",
    shade: "#fff6e8",
    metal: "#8ea3b8",
    wood: "#c9a27a",
    speaker: "#24384f",
    phone: "#1c2c3e",
    outline: "#1a6fe8",
    fail: "#d64545",
    ink: "#0d2744",
    bubble: "#ffffff",
    note: "#0c4dad",
  };
}

export function warmthColor(warmth: "warm" | "neutral" | "cool"): string {
  if (warmth === "warm") return "#ffb15a";
  if (warmth === "cool") return "#9ec4ff";
  return "#fff1cf";
}

export function channelColor(channel: string | undefined): string {
  if (channel === "sms") return "#1f9d55";
  if (channel === "whatsapp") return "#128c7e";
  if (channel === "imessage") return "#1a6fe8";
  return "#5c6f86";
}
