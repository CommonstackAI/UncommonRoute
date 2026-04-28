import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
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
    <>
      <button
        onClick={() => setOpen(true)}
        className="row-hover flex w-full items-center gap-2 rounded-pill border border-n-border-vis bg-n-surface px-2 py-0.5 font-mono text-[11px] text-n-secondary"
      >
        <span className="text-n-display">{shortModel(decision.model)}</span>
        <span className="text-n-disabled">·</span>
        <span>{shortTier(decision.decision_tier)}</span>
        <span className="text-n-disabled">·</span>
        <span>{cost}</span>
        <span className="text-n-disabled">·</span>
        <span>{latencyMs}ms</span>
        <span className="ml-auto text-n-disabled">▸</span>
      </button>
      {open ? <DecisionModal decision={decision} onClose={() => setOpen(false)} /> : null}
    </>
  );
}

function DecisionModal({
  decision,
  onClose,
}: {
  decision: DecisionCard;
  onClose: () => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [onClose]);

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/70 px-4 py-12 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="relative w-full max-w-[720px] max-h-[calc(100vh-6rem)] overflow-y-auto rounded-card border border-n-border bg-n-surface shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-n-border px-5 py-3">
          <div className="label">DECISION DETAIL</div>
          <button
            onClick={onClose}
            className="row-hover rounded-compact border border-n-border-vis px-2 py-0.5 font-mono text-[11px] text-n-secondary hover:text-n-primary"
            aria-label="Close"
          >
            ✕ ESC
          </button>
        </div>
        <div className="px-5 py-4">
          <DecisionDetail decision={decision} />
        </div>
      </div>
    </div>,
    document.body,
  );
}
