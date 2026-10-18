import { Search, Zap } from "lucide-react";
import type { ToolRow } from "../../stream/events";
import { clip, formatArgs } from "../../stream/format";
import { StatusPill } from "./StatusPill";

export function ToolPill({ tool, trigger }: { tool: ToolRow; trigger?: string }) {
  const args = formatArgs(tool.args);
  return (
    <div className="tool-row">
      <div className="tool-main">
        {tool.kind === "meta" ? <Search size={16} aria-hidden="true" /> : <Zap size={16} aria-hidden="true" />}
        <span className="mono tool-name">{tool.tool}</span>
        {args && <span className="muted">{args}</span>}
      </div>
      <p className="tool-why" title={tool.why}>
        why: {clip(tool.why)}
      </p>
      {trigger && <p className="muted tool-trigger">{trigger}</p>}
      {tool.detail && tool.status === "asking" && <p className="tool-detail">asked: {tool.detail}</p>}
      <StatusPill status={tool.status} />
    </div>
  );
}
