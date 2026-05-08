import { type ReactNode, useState } from "react";
import type { TraceAttempt, TraceRecord } from "../../api";
import { useT } from "../../i18n";
import type { Dictionary } from "../../i18n/types";

// ===== Types =====

export interface Session {
  id: string;
  turns: TraceRecord[];
  firstTimestamp: number;
  lastTimestamp: number;
  totalCost: number;
  hasError: boolean;
  models: string[];
  tierCounts: Array<[string, number]>;
}

// ===== Top-level component =====

export default function TurnListView({ session }: { session: Session }) {
  const t = useT();
  const [expandedTurns, setExpandedTurns] = useState<Set<string>>(new Set());
  const toggleTurn = (rid: string) => {
    setExpandedTurns((prev) => {
      const next = new Set(prev);
      if (next.has(rid)) next.delete(rid);
      else next.add(rid);
      return next;
    });
  };

  return (
    <div className="space-y-6">
      <div className="rounded-card border border-n-border bg-n-surface p-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="label">{t.explainer.session}</div>
            <div className="mt-2 font-display text-[28px] leading-tight tracking-tight text-n-display">
              {session.id}
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-2 font-mono text-[11px] text-n-secondary">
              <Badge>{t.explainer.turns(session.turns.length)}</Badge>
              {session.tierCounts.map(([tier, n]) => (
                <Badge key={`tier-${tier}`}>{`${t.common.tierLabel(tier)} × ${n}`}</Badge>
              ))}
              {session.hasError ? <Badge tone="error">{t.explainer.hasErrors}</Badge> : null}
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3 min-w-[260px]">
            <MiniMetric label={t.explainer.modelsCount} value={`${session.models.length}`} />
            <MiniMetric label={t.explainer.totalCost} value={`$${session.totalCost.toFixed(4)}`} />
            <MiniMetric label={t.explainer.started} value={relativeTime(session.firstTimestamp, t)} />
            <MiniMetric label={t.explainer.last} value={relativeTime(session.lastTimestamp, t)} />
          </div>
        </div>

        {session.models.length > 0 ? (
          <div className="mt-5 border-t border-n-border pt-4">
            <div className="label mb-2">{t.explainer.modelsUsed}</div>
            <div className="flex flex-wrap gap-2">
              {session.models.map((m) => (
                <Badge key={`model-${m}`}>{shortModel(m)}</Badge>
              ))}
            </div>
          </div>
        ) : null}
      </div>

      <div className="space-y-3">
        {session.turns.map((turn, idx) => (
          <TurnRow
            key={turn.request_id}
            index={idx + 1}
            turn={turn}
            isOpen={expandedTurns.has(turn.request_id)}
            onToggle={() => toggleTurn(turn.request_id)}
            t={t}
          />
        ))}
      </div>
    </div>
  );
}

// ===== Sub-components =====

function TurnRow({
  index,
  turn,
  isOpen,
  onToggle,
  t,
}: {
  index: number;
  turn: TraceRecord;
  isOpen: boolean;
  onToggle: () => void;
  t: Dictionary;
}) {
  const isError = turn.status_code >= 400;
  return (
    <div className="rounded-card border border-n-border bg-n-surface">
      <button
        onClick={onToggle}
        className="row-hover w-full px-5 py-4 text-left"
      >
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <span className={`h-1.5 w-1.5 rounded-full ${isError ? "bg-n-accent" : "bg-n-success"}`} />
            <span className="font-mono text-[11px] uppercase tracking-[0.08em] text-n-secondary">
              {t.explainer.turn} {index}
            </span>
            <span className="font-mono text-[11px] text-n-disabled">
              {t.common.tierLabel(turn.decision_tier || turn.tier || "")}
            </span>
          </div>
          <span className="font-mono text-[11px] text-n-disabled">
            {isOpen ? "▾" : "▸"}
          </span>
        </div>
        <div className="mt-2 truncate text-[14px] text-n-primary">
          {turn.prompt_preview || t.common.noPreview}
        </div>
        <div className="mt-3 flex items-center justify-between gap-3 font-mono text-[11px] text-n-secondary">
          <span className="truncate">{shortModel(turn.model) || "—"}</span>
          <span>
            {prettyTransport(turn.transport)} · {(turn.latency_us / 1000).toFixed(1)}ms · {isError ? `ERR ${turn.status_code}` : `${turn.status_code}`}
          </span>
        </div>
      </button>
      {isOpen ? <TurnDecision turn={turn} t={t} /> : null}
    </div>
  );
}

function TurnDecision({ turn, t }: { turn: TraceRecord; t: Dictionary }) {
  const transport = {
    requested: prettyTransport(turn.requested_transport || turn.transport),
    selected: prettyTransport(turn.transport),
    source: prettifySource(turn.transport_preference_source, t),
    reason: turn.transport_reason || t.explainer.noTransportReason,
  };

  return (
    <div className="border-t border-n-border px-5 py-5 space-y-5">
      <div>
        <div className="flex flex-wrap items-baseline justify-between gap-3">
          <div>
            <div className="label">{t.explainer.routedTo}</div>
            <div className="mt-1 font-display text-[24px] leading-none tracking-tight text-n-display">
              {shortModel(turn.model) || "—"}
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2 font-mono text-[11px] text-n-secondary">
            <Badge>{t.common.tierLabel(turn.decision_tier || turn.tier || "")}</Badge>
            {turn.served_quality ? <Badge>{prettyQuality(turn.served_quality)}</Badge> : null}
            {turn.capability_lane ? <Badge>{prettyLane(turn.capability_lane)}</Badge> : null}
            <Badge>{(turn.method || "pool").toUpperCase()}</Badge>
            <Badge>{turn.streaming ? t.common.stream : t.common.nonStream}</Badge>
            <Badge tone={turn.status_code >= 400 ? "error" : "default"}>
              {turn.status_code >= 400 ? `ERR ${turn.status_code}` : `HTTP ${turn.status_code}`}
            </Badge>
          </div>
        </div>

        <div className="mt-4 grid grid-cols-4 gap-3">
          <MiniMetric label={t.explainer.confidence} value={turn.raw_confidence ? `${Math.round(turn.raw_confidence * 100)}%` : "—"} />
          <MiniMetric label={t.explainer.latency} value={`${(turn.latency_us / 1000).toFixed(1)}ms`} />
          <MiniMetric label={t.explainer.cost} value={`$${turn.estimated_cost.toFixed(4)}`} />
          <MiniMetric label={t.explainer.requestId} value={turn.request_id} monoSmall />
        </div>
      </div>

      <div>
        <div className="label mb-2">{t.explainer.routeReasoning}</div>
        <div className="text-[13px] text-n-primary">
          {turn.route_reasoning || t.explainer.noReasoning}
        </div>
        {turn.fallback_reason ? (
          <div className="mt-2 font-mono text-[11px] text-n-warning">
            {t.explainer.fallback(turn.fallback_reason)}
          </div>
        ) : null}
      </div>

      <div>
        <div className="flex items-center justify-between gap-3">
          <div className="label">{t.explainer.transport}</div>
          <div className="font-mono text-[11px] text-n-secondary">{transport.source}</div>
        </div>
        <div className="mt-3 grid grid-cols-12 gap-3">
          <div className="col-span-5 rounded-compact border border-n-border px-3 py-3">
            <div className="label">{t.explainer.requested}</div>
            <div className="mt-1 font-mono text-[14px] font-semibold text-n-display">{transport.requested}</div>
          </div>
          <div className="col-span-2 flex items-center justify-center">
            <div className="font-display text-[20px] text-n-display">→</div>
          </div>
          <div className="col-span-5 rounded-compact border border-n-border px-3 py-3">
            <div className="label">{t.explainer.servedCol}</div>
            <div className="mt-1 font-mono text-[14px] font-semibold text-n-display">{transport.selected}</div>
          </div>
        </div>
        <div className="mt-3 text-[13px] text-n-primary">{transport.reason}</div>
      </div>

      <div>
        <div className="flex items-center justify-between gap-3">
          <div className="label">{t.explainer.attemptChain}</div>
          <div className="font-mono text-[11px] text-n-secondary">{t.explainer.attempts(turn.attempts_payload.length)}</div>
        </div>
        <div className="mt-3 space-y-2">
          {turn.attempts_payload.length === 0 ? (
            <div className="font-mono text-[11px] text-n-disabled">{t.explainer.noAttempts}</div>
          ) : (
            turn.attempts_payload.map((attempt) => (
              <AttemptRow key={`${attempt.attempt_index}-${attempt.selected_model}`} attempt={attempt} t={t} />
            ))
          )}
        </div>
      </div>

      {(turn.feature_tags?.length || turn.constraint_tags?.length || turn.hint_tags?.length) ? (
        <div>
          <div className="label mb-2">{t.explainer.tags}</div>
          <TagGroup title={t.explainer.feature} items={turn.feature_tags} />
          <TagGroup title={t.explainer.constraint} items={turn.constraint_tags} format={t.tags.constraint} />
          <TagGroup title={t.explainer.hint} items={turn.hint_tags} format={t.tags.hint} />
        </div>
      ) : null}

      {(turn.error_code || turn.error_message) ? (
        <div className="rounded-compact border border-n-accent px-4 py-3">
          <div className="label text-n-accent">{t.explainer.error}</div>
          <div className="mt-1 font-mono text-[11px] text-n-accent">
            {turn.error_code || "upstream_error"}
            {turn.error_stage ? ` · ${turn.error_stage}` : ""}
          </div>
          <div className="mt-1 text-[13px] text-n-primary">
            {turn.error_message || t.explainer.noErrorMessage}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function AttemptRow({ attempt, t }: { attempt: TraceAttempt; t: Dictionary }) {
  return (
    <div className="rounded-compact border border-n-border px-3 py-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <span className={`h-1.5 w-1.5 rounded-full ${attempt.success ? "bg-n-success" : attempt.blocked ? "bg-n-warning" : "bg-n-accent"}`} />
            <span className="font-mono text-[11px] uppercase tracking-[0.08em] text-n-secondary">
              {t.explainer.attempt} {attempt.attempt_index}
            </span>
          </div>
          <div className="mt-1 font-mono text-[13px] text-n-display">{shortModel(attempt.selected_model)}</div>
          <div className="mt-1 font-mono text-[11px] text-n-secondary">
            {prettyTransport(attempt.requested_transport || attempt.transport)} → {prettyTransport(attempt.transport)}
          </div>
        </div>
        <div className="text-right">
          <div className="font-mono text-[11px] text-n-secondary">{attempt.provider_name || "gateway"}</div>
          <div className={`mt-1 font-mono text-[11px] ${attempt.success ? "text-n-success" : "text-n-accent"}`}>
            {attempt.blocked ? t.common.blocked : attempt.success ? t.common.success : `HTTP ${attempt.status_code || "—"}`}
          </div>
        </div>
      </div>
      {attempt.transport_reason ? (
        <div className="mt-2 text-[12px] text-n-primary">{attempt.transport_reason}</div>
      ) : null}
      {(attempt.error_code || attempt.error_message) ? (
        <div className="mt-2 font-mono text-[11px] text-n-accent">
          {attempt.error_code || "upstream_error"}
          {attempt.error_message ? ` · ${attempt.error_message}` : ""}
        </div>
      ) : null}
    </div>
  );
}

function TagGroup({ title, items, format }: { title: string; items: string[]; format?: (raw: string) => string }) {
  if (!items || items.length === 0) return null;
  return (
    <div className="mt-2">
      <div className="font-mono text-[11px] text-n-secondary">{title}</div>
      <div className="mt-1 flex flex-wrap gap-1.5">
        {items.map((item) => (
          <Badge key={`${title}-${item}`}>{format ? format(item) : item}</Badge>
        ))}
      </div>
    </div>
  );
}

function Badge({
  children,
  tone = "default",
}: {
  children: ReactNode;
  tone?: "default" | "error";
}) {
  return (
    <span
      className={`rounded-pill border px-2 py-0.5 font-mono text-[11px] uppercase tracking-[0.06em] ${
        tone === "error" ? "border-n-accent text-n-accent" : "border-n-border-vis text-n-secondary"
      }`}
    >
      {children}
    </span>
  );
}

function MiniMetric({
  label,
  value,
  monoSmall = false,
}: {
  label: string;
  value: string;
  monoSmall?: boolean;
}) {
  return (
    <div className="rounded-compact border border-n-border px-3 py-2">
      <div className="label">{label}</div>
      <div className={`mt-0.5 font-mono font-semibold tracking-tight text-n-display ${monoSmall ? "text-[11px]" : "text-[14px]"}`}>
        {value}
      </div>
    </div>
  );
}

// ===== Helpers =====

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

export function relativeTime(ts: number, t?: Dictionary) {
  if (!ts) return "—";
  const seconds = Math.max(0, Date.now() / 1000 - ts);
  if (!t) {
    if (seconds < 60) return `${Math.floor(seconds)}s ago`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
    return `${Math.floor(seconds / 86400)}d ago`;
  }
  if (seconds < 60) return t.explainer.timeAgoSec(Math.floor(seconds));
  if (seconds < 3600) return t.explainer.timeAgoMin(Math.floor(seconds / 60));
  if (seconds < 86400) return t.explainer.timeAgoHour(Math.floor(seconds / 3600));
  return t.explainer.timeAgoDay(Math.floor(seconds / 86400));
}

export function normTier(t?: string) {
  return TIER_NAMES[t || ""] || (t || "—").toUpperCase();
}

export function shortModel(model?: string) {
  return (model || "").split("/").pop() || model || "—";
}

export function prettyTransport(transport?: string) {
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

function prettifySource(source?: string, t?: Dictionary) {
  if (!source) return t?.explainer.unspecified ?? "unspecified";
  return source.replace(/-/g, " ").replace(/_/g, " ").toUpperCase();
}

// ===== Public group helper used by ExplainerNew =====

export function groupSessions(traces: TraceRecord[]): Session[] {
  const buckets = new Map<string, TraceRecord[]>();
  for (const t of traces) {
    const key = t.session_id || `_solo:${t.request_id}`;
    const arr = buckets.get(key);
    if (arr) arr.push(t);
    else buckets.set(key, [t]);
  }

  const sessions: Session[] = [];
  for (const [id, items] of buckets) {
    items.sort((a, b) => a.timestamp - b.timestamp);
    const tierMap = new Map<string, number>();
    const modelSet = new Set<string>();
    let totalCost = 0;
    let hasError = false;
    for (const t of items) {
      const tier = normTier(t.decision_tier || t.tier);
      tierMap.set(tier, (tierMap.get(tier) || 0) + 1);
      if (t.model) modelSet.add(t.model);
      totalCost += t.estimated_cost || 0;
      if (t.status_code >= 400) hasError = true;
    }
    const tierOrder = ["LOW", "MID", "MID_HIGH", "HIGH"];
    const tierCounts = Array.from(tierMap.entries()).sort(
      (a, b) => tierOrder.indexOf(a[0]) - tierOrder.indexOf(b[0]),
    );
    sessions.push({
      id,
      turns: items,
      firstTimestamp: items[0].timestamp,
      lastTimestamp: items[items.length - 1].timestamp,
      totalCost,
      hasError,
      models: Array.from(modelSet),
      tierCounts,
    });
  }

  sessions.sort((a, b) => b.lastTimestamp - a.lastTimestamp);
  return sessions;
}
