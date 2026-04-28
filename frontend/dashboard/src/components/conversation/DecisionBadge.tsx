import { useState } from "react";
import type { DecisionCard } from "../../api";
import DecisionDetail from "./DecisionDetail";

function shortModel(model?: string) {
  return (model || "").split("/").pop() || model || "—";
}

function shortTier(tier?: string) {
  const t = (tier || "").toUpperCase();
  if (t === "SIMPLE") return "LOW";
  if (t === "MEDIUM") return "MID";
  if (t === "COMPLEX") return "HIGH";
  if (t === "REASONING") return "HIGH";
  return t || "—";
}

export default function DecisionBadge({ decision }: { decision: DecisionCard }) {
  const [open, setOpen] = useState(false);
  const latencyMs = (decision.latency_us / 1000).toFixed(0);
  const cost = `$${decision.estimated_cost.toFixed(4)}`;
  return (
    <div className="w-full">
      <button
        onClick={() => setOpen((v) => !v)}
        className="row-hover flex w-full items-center gap-2 rounded-pill border border-n-border-vis bg-n-surface px-2 py-0.5 font-mono text-[11px] text-n-secondary"
      >
        <span className="text-n-display">{shortModel(decision.model)}</span>
        <span className="text-n-disabled">·</span>
        <span>{shortTier(decision.decision_tier)}</span>
        <span className="text-n-disabled">·</span>
        <span>{cost}</span>
        <span className="text-n-disabled">·</span>
        <span>{latencyMs}ms</span>
        <span className="ml-auto text-n-disabled">{open ? "▾" : "▸"}</span>
      </button>
      {open ? <DecisionDetail decision={decision} /> : null}
    </div>
  );
}
