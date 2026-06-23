import { useState } from "react";
import type { ToolStep } from "./groupMessages";
import DecisionBadge from "./DecisionBadge";
import MessageBubble, { type ToolCallLookup } from "./MessageBubble";
import { useT } from "../../i18n";
import type { Dictionary } from "../../i18n/types";

function aggregateToolNames(steps: ToolStep[]): Record<string, number> {
  const acc: Record<string, number> = {};
  for (const s of steps) {
    for (const c of s.assistant.tool_calls ?? []) {
      acc[c.name] = (acc[c.name] || 0) + 1;
    }
  }
  return acc;
}

function distinctModels(steps: ToolStep[], t: Dictionary): { captured: number; preCapture: number; summary: string } {
  const captured = new Set<string>();
  let preCapture = 0;
  for (const s of steps) {
    if (s.assistant.decision == null) {
      preCapture += 1;
    } else {
      captured.add(s.assistant.decision.model);
    }
  }
  const summary = preCapture > 0
    ? t.conversation.modelsAndPreCapture(captured.size, preCapture)
    : t.conversation.nModels(captured.size);
  return { captured: captured.size, preCapture, summary };
}

export default function ToolStepGroup({
  steps,
  toolCalls,
}: {
  steps: ToolStep[];
  toolCalls?: ToolCallLookup;
}) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const tools = aggregateToolNames(steps);
  const toolSummary = Object.entries(tools)
    .map(([name, n]) => `${name}×${n}`)
    .join(" ");
  const { summary: modelSummary } = distinctModels(steps, t);

  return (
    <div className="rounded-card border border-n-border bg-n-surface">
      <button
        onClick={() => setOpen((v) => !v)}
        className="row-hover flex w-full items-center justify-between gap-3 px-4 py-3 text-left"
      >
        <div className="flex items-center gap-2">
          <span className="font-mono text-[10px] text-n-disabled">{open ? "▾" : "▸"}</span>
          <span className="label">{t.conversation.agentThoughtFor(steps.length)}</span>
        </div>
        <div className="flex items-center gap-3 font-mono text-[11px] text-n-secondary">
          <span className="truncate max-w-[260px]">{toolSummary}</span>
          <span className="text-n-disabled">·</span>
          <span>{modelSummary}</span>
        </div>
      </button>
      {open ? (
        <div className="border-t border-n-border px-4 py-3 space-y-3">
          {steps.map((step, i) => (
            <div key={i} className="rounded-compact border border-n-border bg-n-raised px-3 py-3 space-y-2">
              <div className="flex items-baseline justify-between gap-3">
                <div className="label">{t.conversation.step} {i + 1}</div>
                {step.assistant.decision ? (
                  <div className="min-w-0 max-w-[60%]">
                    <DecisionBadge decision={step.assistant.decision} />
                  </div>
                ) : (
                  <span className="rounded-pill border border-n-border-vis px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.06em] text-n-disabled">
                    {t.conversation.preCapture}
                  </span>
                )}
              </div>
              {(step.assistant.tool_calls || []).map((c, j) => (
                <div key={j} className="font-mono text-[11px] text-n-primary">
                  <span className="text-n-secondary">tool_use:</span>{" "}
                  <span className="text-n-display">{c.name}</span>
                  <span className="text-n-secondary">(</span>
                  <span>{typeof c.input === "string" ? c.input : JSON.stringify(c.input)}</span>
                  <span className="text-n-secondary">)</span>
                </div>
              ))}
              {step.results.length > 0 ? (
                <div className="space-y-1">
                  {step.results.map((r, k) => (
                    <MessageBubble key={k} message={r} toolCalls={toolCalls} />
                  ))}
                </div>
              ) : null}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
