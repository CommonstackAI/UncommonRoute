import type { DecisionCard, TraceAttempt } from "../../api";

const TIER_NAMES: Record<string, string> = {
  SIMPLE: "LOW",
  MEDIUM: "MID",
  COMPLEX: "HIGH",
  REASONING: "HIGH",
  low: "LOW",
  mid: "MID",
  mid_high: "MID_HIGH",
  high: "HIGH",
};

function normTier(t?: string) {
  return TIER_NAMES[t || ""] || (t || "—").toUpperCase();
}

function shortModel(model?: string) {
  return (model || "").split("/").pop() || model || "—";
}

function prettyTransport(transport?: string) {
  switch (transport) {
    case "anthropic-messages":
      return "Anthropic Messages";
    case "openai-responses":
      return "OpenAI Responses";
    case "openai-chat":
      return "OpenAI Chat";
    default:
      return transport || "—";
  }
}

function prettyQuality(value?: string) {
  if (!value) return "—";
  return value.replace(/_/g, " ").toUpperCase();
}

function prettyLane(value?: string) {
  if (!value) return "—";
  return value.replace(/-/g, " ").replace(/_/g, " ").toUpperCase();
}

export default function DecisionDetail({ decision }: { decision: DecisionCard }) {
  return (
    <div className="mt-2 space-y-4 rounded-compact border border-n-border bg-n-raised px-4 py-3">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <div className="label">ROUTED TO</div>
          <div className="mt-1 font-display text-[20px] leading-none tracking-tight text-n-display">
            {shortModel(decision.model)}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2 font-mono text-[11px] text-n-secondary">
          <Badge>{normTier(decision.decision_tier)}</Badge>
          {decision.served_quality ? <Badge>{prettyQuality(decision.served_quality)}</Badge> : null}
          {decision.capability_lane ? <Badge>{prettyLane(decision.capability_lane)}</Badge> : null}
        </div>
      </div>

      <div className="grid grid-cols-3 gap-3">
        <Mini label="CONFIDENCE" value={decision.raw_confidence ? `${Math.round(decision.raw_confidence * 100)}%` : "—"} />
        <Mini label="LATENCY" value={`${(decision.latency_us / 1000).toFixed(1)}ms`} />
        <Mini label="COST" value={`$${decision.estimated_cost.toFixed(4)}`} />
      </div>

      <div>
        <div className="label mb-1">ROUTE REASONING</div>
        <div className="text-[12px] text-n-primary">
          {decision.route_reasoning || "—"}
        </div>
        {decision.fallback_reason ? (
          <div className="mt-1 font-mono text-[11px] text-n-warning">
            Fallback: {decision.fallback_reason}
          </div>
        ) : null}
      </div>

      <div>
        <div className="flex items-center justify-between gap-3">
          <div className="label">TRANSPORT</div>
        </div>
        <div className="mt-2 grid grid-cols-12 gap-2">
          <div className="col-span-5 rounded-compact border border-n-border px-2 py-2">
            <div className="label">SELECTED</div>
            <div className="mt-1 font-mono text-[12px] font-semibold text-n-display">{prettyTransport(decision.transport)}</div>
          </div>
          <div className="col-span-7 rounded-compact border border-n-border px-2 py-2">
            <div className="label">REASON</div>
            <div className="mt-1 text-[11px] text-n-primary">
              {decision.transport_reason || "—"}
            </div>
          </div>
        </div>
      </div>

      {decision.attempts_payload && decision.attempts_payload.length > 0 ? (
        <div>
          <div className="label mb-1">ATTEMPT CHAIN ({decision.attempts_payload.length})</div>
          <div className="space-y-1">
            {decision.attempts_payload.map((a, i) => (
              <AttemptRow key={`${i}-${a.selected_model}`} attempt={a} />
            ))}
          </div>
        </div>
      ) : null}

      {(decision.feature_tags?.length || decision.constraint_tags?.length || decision.hint_tags?.length) ? (
        <div>
          <div className="label mb-1">TAGS</div>
          <TagRow title="FEATURE" items={decision.feature_tags} />
          <TagRow title="CONSTRAINT" items={decision.constraint_tags} />
          <TagRow title="HINT" items={decision.hint_tags} />
        </div>
      ) : null}
    </div>
  );
}

function AttemptRow({ attempt }: { attempt: TraceAttempt }) {
  const dot = attempt.success ? "bg-n-success" : attempt.blocked ? "bg-n-warning" : "bg-n-accent";
  return (
    <div className="rounded-compact border border-n-border px-2 py-1.5">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <span className={`h-1.5 w-1.5 rounded-full ${dot}`} />
          <span className="font-mono text-[11px] text-n-display">{shortModel(attempt.selected_model)}</span>
        </div>
        <span className="font-mono text-[11px] text-n-secondary">
          {attempt.success ? "OK" : attempt.blocked ? "BLOCKED" : `HTTP ${attempt.status_code || "—"}`}
        </span>
      </div>
    </div>
  );
}

function TagRow({ title, items }: { title: string; items: string[] | undefined }) {
  if (!items || items.length === 0) return null;
  return (
    <div className="mt-1 flex flex-wrap items-center gap-1.5">
      <span className="font-mono text-[10px] text-n-secondary">{title}</span>
      {items.map((it) => (
        <Badge key={`${title}-${it}`}>{it}</Badge>
      ))}
    </div>
  );
}

function Badge({ children }: { children: React.ReactNode }) {
  return (
    <span className="rounded-pill border border-n-border-vis px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.06em] text-n-secondary">
      {children}
    </span>
  );
}

function Mini({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-compact border border-n-border px-2 py-1.5">
      <div className="label">{label}</div>
      <div className="mt-0.5 font-mono text-[12px] font-semibold tracking-tight text-n-display">{value}</div>
    </div>
  );
}
