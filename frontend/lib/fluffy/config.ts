import type { ContractMode } from "./types";

export type FluffyPublicConfig = {
  httpBase: string;
  wsBase: string;
  contract: ContractMode;
};

const DEFAULT_HTTP = "http://127.0.0.1:8000";

export function readPublicConfig(env: Record<string, string | undefined> = process.env): FluffyPublicConfig {
  const httpBase = stripSlash(env.NEXT_PUBLIC_FLUFFY_HTTP || DEFAULT_HTTP);
  const contract: ContractMode = env.NEXT_PUBLIC_FLUFFY_CONTRACT === "legacy-live" ? "legacy-live" : "execution-v2";
  const wsBase = stripSlash(env.NEXT_PUBLIC_FLUFFY_WS || toWebSocket(httpBase));
  return { httpBase, wsBase, contract };
}

export function toWebSocket(httpBase: string): string {
  if (httpBase.startsWith("https://")) return "wss://" + httpBase.slice("https://".length);
  if (httpBase.startsWith("http://")) return "ws://" + httpBase.slice("http://".length);
  return httpBase;
}

function stripSlash(value: string): string {
  return value.replace(/\/+$/, "");
}
