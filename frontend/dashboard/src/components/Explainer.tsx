/**
 * Nothing Design: Route Explainer
 * Left: trace inbox. Right: route + transport reasoning.
 */

import { type ReactNode, useEffect, useMemo, useState } from "react";
import {
  fetchTraceDetail,
  fetchTraces,
  type TraceAttempt,
  type TraceRecord,
} from "../api";
import { useLiveData } from "../state/LiveDataContext";
import { useT } from "../i18n";
import type { Dictionary } from "../i18n/types";

export default function Explainer() {
  const t = useT();
  const { recent: liveRecent } = useLiveData();
  const completedCount = useMemo(
    () => liveRecent.filter((r) => r.state === "completed").length,
    [liveRecent],
  );
  const [recent, setRecent] = useState<TraceRecord[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selected, setSelected] = useState<TraceRecord | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      const payload = await fetchTraces(30);
      if (cancelled) return;
      if (!payload) {
        setError(t.explainer.errEndpoint);
        setRecent([]);
        return;
      }
      setError(null);
      setRecent(payload.items);
      setSelectedId((current) => {
        if (current && payload.items.some((item) => item.request_id === current)) return current;
        return payload.items[0]?.request_id ?? null;
      });
    };
    load();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [completedCount]);

  useEffect(() => {
    let cancelled = false;
    if (!selectedId) {
      setSelected(null);
      return;
    }

    setLoadingDetail(true);
    fetchTraceDetail(selectedId)
      .then((detail) => {
        if (cancelled) return;
        setSelected(detail);
      })
      .catch(() => {
        if (cancelled) return;
        setSelected(null);
      })
      .finally(() => {
        if (!cancelled) setLoadingDetail(false);
      });

    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  const transportSummary = useMemo(() => {
    if (!selected) return null;
    return {
      requested: prettyTransport(selected.requested_transport || selected.transport),
      selected: prettyTransport(selected.transport),
      source: prettifySource(selected.transport_preference_source, t),
      reason: selected.transport_reason || t.explainer.noTransportReason,
    };
  }, [selected, t]);

  return (
    <div className="animate-fadeIn">
      <div className="mb-8">
        <h1 className="font-display text-[36px] text-n-display tracking-tight">{t.explainer.title}</h1>
        <p className="mt-2 text-[14px] text-n-secondary">
          {t.explainer.subtitle}
        </p>
      </div>

      {error ? <div className="mb-4 font-mono text-[12px] text-n-accent">{error}</div> : null}

      <div className="grid grid-cols-12 gap-8">
        <div className="col-span-4 max-h-[720px] overflow-y-auto rounded-card border border-n-border bg-n-surface">
          <div className="flex items-center justify-between border-b border-n-border px-5 py-4">
            <div className="label">{t.explainer.recentTraces}</div>
            <div className="font-mono text-[11px] text-n-secondary">{t.explainer.loaded(recent.length)}</div>
          </div>

          {recent.length === 0 && !error ? (
            <div className="flex items-center justify-center py-16 font-mono text-[11px] tracking-[0.08em] text-n-disabled">
              {t.explainer.noTraces}
            </div>
          ) : null}

          <div>
            {recent.map((trace) => {
              const active = trace.request_id === selectedId;
              return (
                <button
                  key={trace.request_id}
                  onClick={() => setSelectedId(trace.request_id)}
                  className={`row-hover w-full border-b border-n-border px-5 py-4 text-left ${
                    active ? "bg-n-raised" : "hover:bg-n-raised"
                  }`}
                >
                  <div className="flex items-center justify-between gap-3">
                    <div className="flex items-center gap-2">
                      <span className={`h-1.5 w-1.5 rounded-full ${trace.status_code >= 400 ? "bg-n-accent" : "bg-n-success"}`} />
                      <span className="font-mono text-[11px] uppercase tracking-[0.08em] text-n-secondary">
                        {t.common.tierLabel(trace.decision_tier || trace.tier || "")}
                      </span>
                    </div>
                    <span className="font-mono text-[11px] text-n-disabled">
                      {prettyTransport(trace.transport)}
                    </span>
                  </div>

                  <div className="mt-2 truncate text-[13px] text-n-primary">
                    {trace.prompt_preview || t.common.noPreview}
                  </div>

                  <div className="mt-3 flex items-center justify-between gap-3 font-mono text-[11px] text-n-secondary">
                    <span className="truncate">{shortModel(trace.model)}</span>
                    <span>{trace.status_code >= 400 ? `ERR ${trace.status_code}` : `${trace.status_code}`}</span>
                  </div>
                </button>
              );
            })}
          </div>
        </div>

        <div className="col-span-8">
          {!selectedId ? (
            <EmptyPanel label={t.explainer.selectTrace} />
          ) : loadingDetail && !selected ? (
            <EmptyPanel label={t.explainer.loadingTrace} />
          ) : selected ? (
            <div className="space-y-6">
              <div className="rounded-card border border-n-border bg-n-surface p-6">
                <div className="flex flex-wrap items-start justify-between gap-4">
                  <div>
                    <div className="label">{t.explainer.routedTo}</div>
                    <div className="mt-2 font-display text-[44px] leading-none tracking-tight text-n-display">
                      {shortModel(selected.model) || "—"}
                    </div>
                    <div className="mt-3 flex flex-wrap items-center gap-2 font-mono text-[11px] text-n-secondary">
                      <Badge>{t.common.tierLabel(selected.decision_tier || selected.tier || "")}</Badge>
                      {selected.served_quality ? <Badge>{prettyQuality(selected.served_quality)}</Badge> : null}
                      {selected.capability_lane ? <Badge>{prettyLane(selected.capability_lane)}</Badge> : null}
                      <Badge>{(selected.method || "pool").toUpperCase()}</Badge>
                      <Badge>{(selected.endpoint || "chat_completions").replace(/_/g, " ")}</Badge>
                      <Badge>{selected.streaming ? t.common.stream : t.common.nonStream}</Badge>
                      <Badge tone={selected.status_code >= 400 ? "error" : "default"}>
                        {selected.status_code >= 400 ? `ERR ${selected.status_code}` : `HTTP ${selected.status_code}`}
                      </Badge>
                    </div>
                  </div>

                  <div className="grid grid-cols-2 gap-3 min-w-[280px]">
                    <MiniMetric label={t.explainer.confidence} value={selected.raw_confidence ? `${Math.round(selected.raw_confidence * 100)}%` : "—"} />
                    <MiniMetric label={t.explainer.latency} value={`${(selected.latency_us / 1000).toFixed(1)}ms`} />
                    <MiniMetric label={t.explainer.estCost} value={`$${selected.estimated_cost.toFixed(4)}`} />
                    <MiniMetric label={t.explainer.requestId} value={selected.request_id} monoSmall />
                  </div>
                </div>

                <div className="mt-6 border-t border-n-border pt-5">
                  <div className="label mb-2">{t.explainer.routeReasoning}</div>
                  <div className="text-[14px] text-n-primary">
                    {selected.route_reasoning || t.explainer.noReasoning}
                  </div>
                  {selected.fallback_reason ? (
                    <div className="mt-3 font-mono text-[12px] text-n-warning">
                      {t.explainer.fallback(selected.fallback_reason)}
                    </div>
                  ) : null}
                </div>

                <div className="mt-6 border-t border-n-border pt-5">
                  <div className="label mb-3">{t.explainer.serviceContract}</div>
                  <div className="grid grid-cols-4 gap-3">
                    <MiniMetric label={t.explainer.request} value={t.common.tierLabel(selected.decision_tier || selected.tier || "")} />
                    <MiniMetric label={t.explainer.served} value={prettyQuality(selected.served_quality)} />
                    <MiniMetric label={t.explainer.target} value={prettyQuality(selected.served_quality_target)} />
                    <MiniMetric label={t.explainer.lane} value={prettyLane(selected.capability_lane)} />
                  </div>
                  {selected.served_quality_floor ? (
                    <div className="mt-3 font-mono text-[11px] text-n-secondary">
                      {t.explainer.floor(prettyQuality(selected.served_quality_floor))}
                    </div>
                  ) : null}
                </div>
              </div>

              {transportSummary ? (
                <div className="rounded-card border border-n-border bg-n-surface p-6">
                  <div className="flex items-center justify-between gap-3">
                    <div className="label">{t.explainer.transportDecision}</div>
                    <div className="font-mono text-[11px] text-n-secondary">
                      {transportSummary.source}
                    </div>
                  </div>

                  <div className="mt-5 grid grid-cols-12 gap-4">
                    <div className="col-span-5 rounded-compact border border-n-border px-4 py-4">
                      <div className="label">{t.explainer.requested}</div>
                      <div className="mt-2 font-mono text-[18px] font-semibold text-n-display">
                        {transportSummary.requested}
                      </div>
                    </div>

                    <div className="col-span-2 flex items-center justify-center">
                      <div className="font-display text-[28px] text-n-display">→</div>
                    </div>

                    <div className="col-span-5 rounded-compact border border-n-border px-4 py-4">
                      <div className="label">{t.explainer.servedCol}</div>
                      <div className="mt-2 font-mono text-[18px] font-semibold text-n-display">
                        {transportSummary.selected}
                      </div>
                    </div>
                  </div>

                  <div className="mt-5 rounded-compact border border-n-border px-4 py-4">
                    <div className="label">{t.explainer.whyProtocol}</div>
                    <div className="mt-2 text-[14px] text-n-primary">
                      {transportSummary.reason}
                    </div>
                  </div>
                </div>
              ) : null}

              <div className="grid grid-cols-12 gap-6">
                <div className="col-span-7 rounded-card border border-n-border bg-n-surface p-6">
                  <div className="flex items-center justify-between gap-3">
                    <div className="label">{t.explainer.attemptChain}</div>
                    <div className="font-mono text-[11px] text-n-secondary">
                      {t.explainer.attempts(selected.attempts_payload.length)}
                    </div>
                  </div>

                  <div className="mt-4 space-y-3">
                    {selected.attempts_payload.length === 0 ? (
                      <div className="font-mono text-[11px] text-n-disabled">{t.explainer.noAttempts}</div>
                    ) : (
                      selected.attempts_payload.map((attempt) => (
                        <AttemptRow key={`${attempt.attempt_index}-${attempt.selected_model}`} attempt={attempt} t={t} />
                      ))
                    )}
                  </div>
                </div>

                <div className="col-span-5 rounded-card border border-n-border bg-n-surface p-6">
                  <div className="label">{t.explainer.requestShape}</div>
                  <div className="mt-4 grid grid-cols-2 gap-3">
                    <MiniMetric label={t.explainer.apiFormat} value={(selected.api_format || "openai").toUpperCase()} />
                    <MiniMetric label={t.explainer.stepType} value={(selected.step_type || "general").toUpperCase()} />
                    <MiniMetric label={t.explainer.inputTokens} value={`${selected.usage_input_tokens || selected.input_tokens_after || 0}`} />
                    <MiniMetric label={t.explainer.outputTokens} value={`${selected.usage_output_tokens || 0}`} />
                  </div>

                  <div className="mt-5 border-t border-n-border pt-5">
                    <div className="label mb-3">{t.explainer.tags}</div>
                    <TagGroup title={t.explainer.feature} items={selected.feature_tags} />
                    <TagGroup title={t.explainer.constraint} items={selected.constraint_tags} format={t.tags.constraint} />
                    <TagGroup title={t.explainer.hint} items={selected.hint_tags} format={t.tags.hint} />
                  </div>

                  {(selected.error_code || selected.error_message) ? (
                    <div className="mt-5 rounded-compact border border-n-accent px-4 py-4">
                      <div className="label text-n-accent">{t.explainer.error}</div>
                      <div className="mt-2 font-mono text-[12px] text-n-accent">
                        {selected.error_code || "upstream_error"}
                        {selected.error_stage ? ` · ${selected.error_stage}` : ""}
                      </div>
                      <div className="mt-2 text-[13px] text-n-primary">
                        {selected.error_message || t.explainer.noErrorMessage}
                      </div>
                    </div>
                  ) : null}
                </div>
              </div>
            </div>
          ) : (
            <EmptyPanel label={t.explainer.traceUnavailable} />
          )}
        </div>
      </div>
    </div>
  );
}

function AttemptRow({ attempt, t }: { attempt: TraceAttempt; t: Dictionary }) {
  return (
    <div className="rounded-compact border border-n-border px-4 py-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <span className={`h-1.5 w-1.5 rounded-full ${attempt.success ? "bg-n-success" : attempt.blocked ? "bg-n-warning" : "bg-n-accent"}`} />
            <span className="font-mono text-[12px] uppercase tracking-[0.08em] text-n-secondary">
              {t.explainer.attempt} {attempt.attempt_index}
            </span>
          </div>
          <div className="mt-2 font-mono text-[15px] text-n-display">
            {shortModel(attempt.selected_model)}
          </div>
          <div className="mt-1 font-mono text-[11px] text-n-secondary">
            {prettyTransport(attempt.requested_transport || attempt.transport)} → {prettyTransport(attempt.transport)}
          </div>
        </div>

        <div className="text-right">
          <div className="font-mono text-[12px] text-n-secondary">
            {attempt.provider_name || "gateway"}
          </div>
          <div className={`mt-1 font-mono text-[11px] ${attempt.success ? "text-n-success" : "text-n-accent"}`}>
            {attempt.blocked ? t.common.blocked : attempt.success ? t.common.success : `HTTP ${attempt.status_code || "—"}`}
          </div>
        </div>
      </div>

      {attempt.transport_reason ? (
        <div className="mt-3 text-[13px] text-n-primary">
          {attempt.transport_reason}
        </div>
      ) : null}

      <div className="mt-3 flex items-center justify-between gap-3 font-mono text-[11px] text-n-secondary">
        <span className="truncate">{attempt.target_url || ""}</span>
        <span>{attempt.transport_preference_source ? prettifySource(attempt.transport_preference_source, t) : ""}</span>
      </div>

      {(attempt.error_code || attempt.error_message) ? (
        <div className="mt-3 font-mono text-[11px] text-n-accent">
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
    <div className="mt-3">
      <div className="font-mono text-[11px] text-n-secondary">{title}</div>
      <div className="mt-2 flex flex-wrap gap-2">
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
      className={`rounded-pill border px-2 py-1 font-mono text-[11px] uppercase tracking-[0.06em] ${
        tone === "error"
          ? "border-n-accent text-n-accent"
          : "border-n-border-vis text-n-secondary"
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
    <div className="rounded-compact border border-n-border px-4 py-3">
      <div className="label">{label}</div>
      <div className={`mt-1 font-mono font-semibold tracking-tight text-n-display ${monoSmall ? "text-[12px]" : "text-[16px]"}`}>
        {value}
      </div>
    </div>
  );
}

function EmptyPanel({ label }: { label: string }) {
  return (
    <div className="flex min-h-[320px] items-center justify-center rounded-card border border-dashed border-n-border dot-grid-subtle">
      <span className="font-mono text-[11px] tracking-[0.08em] text-n-disabled">{label}</span>
    </div>
  );
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

function prettifySource(source?: string, t?: Dictionary) {
  if (!source) return t?.explainer.unspecified ?? "unspecified";
  return source.replace(/-/g, " ").replace(/_/g, " ").toUpperCase();
}
