import type { DecisionCard, TraceAttempt } from "../../api";
import RouteReasoning from "./RouteReasoning";
import { useT } from "../../i18n";
import type { Dictionary } from "../../i18n/types";

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
  const t = useT();
  return (
    <div className="mt-2 space-y-4 rounded-compact border border-n-border bg-n-raised px-4 py-3">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <div className="label">{t.conversation.routedTo}</div>
          <div className="mt-1 font-display text-[20px] leading-none tracking-tight text-n-display">
            {shortModel(decision.model)}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2 font-mono text-[11px] text-n-secondary">
          <Badge>{t.common.tierLabel(decision.decision_tier || "")}</Badge>
          {decision.served_quality ? <Badge>{prettyQuality(decision.served_quality)}</Badge> : null}
          {decision.capability_lane ? <Badge>{prettyLane(decision.capability_lane)}</Badge> : null}
        </div>
      </div>

      <div className="grid grid-cols-5 gap-3">
        <Mini label={t.explainer.confidence} value={decision.raw_confidence ? `${Math.round(decision.raw_confidence * 100)}%` : "—"} />
        <Mini label={t.explainer.route} value={formatMs(decision.route_latency_ms ?? decision.latency_us / 1000)} />
        <Mini label={t.explainer.upstream} value={formatOptionalMs(decision.upstream_elapsed_ms)} />
        <Mini label={t.explainer.firstToken} value={formatOptionalMs(decision.first_token_ms)} />
        <Mini label={t.explainer.cost} value={`$${decision.estimated_cost.toFixed(4)}`} />
      </div>

      <div>
        <RouteReasoning text={decision.route_reasoning || ""} />
        {decision.fallback_reason ? (
          <div className="mt-1 font-mono text-[11px] text-n-warning">
            {t.conversation.fallback(decision.fallback_reason)}
          </div>
        ) : null}
      </div>

      <div>
        <div className="flex items-center justify-between gap-3">
          <div className="label">{t.conversation.transport}</div>
        </div>
        <div className="mt-2 grid grid-cols-12 gap-2">
          <div className="col-span-5 rounded-compact border border-n-border px-2 py-2">
            <div className="label">{t.explainer.selected}</div>
            <div className="mt-1 font-mono text-[12px] font-semibold text-n-display">{prettyTransport(decision.transport)}</div>
          </div>
          <div className="col-span-7 rounded-compact border border-n-border px-2 py-2">
            <div className="label">{t.conversation.reason}</div>
            <div className="mt-1 text-[11px] text-n-primary">
              {decision.transport_reason || "—"}
            </div>
          </div>
        </div>
      </div>

      {decision.attempts_payload && decision.attempts_payload.length > 0 ? (
        <div>
          <div className="label mb-1">{t.conversation.attemptChainN(decision.attempts_payload.length)}</div>
          <div className="space-y-1">
            {decision.attempts_payload.map((a, i) => (
              <AttemptRow key={`${i}-${a.selected_model}`} attempt={a} t={t} />
            ))}
          </div>
        </div>
      ) : null}

      {(decision.feature_tags?.length || decision.constraint_tags?.length || decision.hint_tags?.length) ? (
        <div>
          <div className="label mb-1">{t.explainer.tags}</div>
          <TagRow title={t.explainer.feature} items={decision.feature_tags} />
          <TagRow title={t.explainer.constraint} items={decision.constraint_tags} format={t.tags.constraint} />
          <TagRow title={t.explainer.hint} items={decision.hint_tags} format={t.tags.hint} />
        </div>
      ) : null}
    </div>
  );
}

function AttemptRow({ attempt, t }: { attempt: TraceAttempt; t: Dictionary }) {
  const dot = attempt.success ? "bg-n-success" : attempt.blocked ? "bg-n-warning" : "bg-n-accent";
  return (
    <div className="rounded-compact border border-n-border px-2 py-1.5">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <span className={`h-1.5 w-1.5 rounded-full ${dot}`} />
          <span className="font-mono text-[11px] text-n-display">{shortModel(attempt.selected_model)}</span>
        </div>
        <span className="font-mono text-[11px] text-n-secondary">
          {attempt.success ? t.common.ok : attempt.blocked ? t.common.blocked : `HTTP ${attempt.status_code || "—"}`}
        </span>
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-[10px] text-n-secondary">
        {attempt.upstream_elapsed_ms ? <span>{t.explainer.upstream} {formatMs(attempt.upstream_elapsed_ms)}</span> : null}
        {attempt.response_headers_ms ? <span>{t.explainer.responseHeaders} {formatMs(attempt.response_headers_ms)}</span> : null}
        {attempt.first_token_ms ? <span>{t.explainer.firstToken} {formatMs(attempt.first_token_ms)}</span> : null}
      </div>
    </div>
  );
}

function formatMs(value: number): string {
  if (value >= 1000) return `${(value / 1000).toFixed(1)}s`;
  return `${value.toFixed(0)}ms`;
}

function formatOptionalMs(value?: number): string {
  if (!value || value <= 0) return "—";
  return formatMs(value);
}

function TagRow({ title, items, format }: { title: string; items: string[] | undefined; format?: (raw: string) => string }) {
  if (!items || items.length === 0) return null;
  return (
    <div className="mt-1 flex flex-wrap items-center gap-1.5">
      <span className="font-mono text-[10px] text-n-secondary">{title}</span>
      {items.map((it) => (
        <Badge key={`${title}-${it}`}>{format ? format(it) : it}</Badge>
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
