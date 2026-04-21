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

export default function Explainer() {
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
        setError("[ERROR: TRACE ENDPOINT UNREACHABLE]");
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
    const id = window.setInterval(load, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

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
      source: prettifySource(selected.transport_preference_source),
      reason: selected.transport_reason || "No explicit transport reason recorded.",
    };
  }, [selected]);

  return (
    <div className="animate-fadeIn">
      <div className="mb-8">
        <h1 className="font-display text-[36px] text-n-display tracking-tight">EXPLAIN</h1>
        <p className="mt-2 text-[14px] text-n-secondary">
          Inspect one request at a time, including why the proxy chose a model and which upstream protocol it used.
        </p>
      </div>

      {error ? <div className="mb-4 font-mono text-[12px] text-n-accent">{error}</div> : null}

      <div className="grid grid-cols-12 gap-8">
        <div className="col-span-4 max-h-[720px] overflow-y-auto rounded-card border border-n-border bg-n-surface">
          <div className="flex items-center justify-between border-b border-n-border px-5 py-4">
            <div className="label">RECENT TRACES</div>
            <div className="font-mono text-[11px] text-n-secondary">{recent.length} loaded</div>
          </div>

          {recent.length === 0 && !error ? (
            <div className="flex items-center justify-center py-16 font-mono text-[11px] tracking-[0.08em] text-n-disabled">
              [NO TRACES YET]
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
                        {normTier(trace.decision_tier || trace.tier)}
                      </span>
                    </div>
                    <span className="font-mono text-[11px] text-n-disabled">
                      {prettyTransport(trace.transport)}
                    </span>
                  </div>

                  <div className="mt-2 truncate text-[13px] text-n-primary">
                    {trace.prompt_preview || "[no preview]"}
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
            <EmptyPanel label="[SELECT A TRACE]" />
          ) : loadingDetail && !selected ? (
            <EmptyPanel label="[LOADING TRACE...]" />
          ) : selected ? (
            <div className="space-y-6">
              <div className="rounded-card border border-n-border bg-n-surface p-6">
                <div className="flex flex-wrap items-start justify-between gap-4">
                  <div>
                    <div className="label">ROUTED TO</div>
                    <div className="mt-2 font-display text-[44px] leading-none tracking-tight text-n-display">
                      {shortModel(selected.model) || "—"}
                    </div>
                    <div className="mt-3 flex flex-wrap items-center gap-2 font-mono text-[11px] text-n-secondary">
                      <Badge>{normTier(selected.decision_tier || selected.tier)}</Badge>
                      <Badge>{(selected.method || "pool").toUpperCase()}</Badge>
                      <Badge>{(selected.endpoint || "chat_completions").replace(/_/g, " ")}</Badge>
                      <Badge>{selected.streaming ? "STREAM" : "NON-STREAM"}</Badge>
                      <Badge tone={selected.status_code >= 400 ? "error" : "default"}>
                        {selected.status_code >= 400 ? `ERR ${selected.status_code}` : `HTTP ${selected.status_code}`}
                      </Badge>
                    </div>
                  </div>

                  <div className="grid grid-cols-2 gap-3 min-w-[280px]">
                    <MiniMetric label="CONFIDENCE" value={selected.raw_confidence ? `${Math.round(selected.raw_confidence * 100)}%` : "—"} />
                    <MiniMetric label="LATENCY" value={`${(selected.latency_us / 1000).toFixed(1)}ms`} />
                    <MiniMetric label="EST. COST" value={`$${selected.estimated_cost.toFixed(4)}`} />
                    <MiniMetric label="REQUEST ID" value={selected.request_id} monoSmall />
                  </div>
                </div>

                <div className="mt-6 border-t border-n-border pt-5">
                  <div className="label mb-2">ROUTE REASONING</div>
                  <div className="text-[14px] text-n-primary">
                    {selected.route_reasoning || "No route reasoning recorded."}
                  </div>
                  {selected.fallback_reason ? (
                    <div className="mt-3 font-mono text-[12px] text-n-warning">
                      Fallback: {selected.fallback_reason}
                    </div>
                  ) : null}
                </div>
              </div>

              {transportSummary ? (
                <div className="rounded-card border border-n-border bg-n-surface p-6">
                  <div className="flex items-center justify-between gap-3">
                    <div className="label">TRANSPORT DECISION</div>
                    <div className="font-mono text-[11px] text-n-secondary">
                      {transportSummary.source}
                    </div>
                  </div>

                  <div className="mt-5 grid grid-cols-12 gap-4">
                    <div className="col-span-5 rounded-compact border border-n-border px-4 py-4">
                      <div className="label">REQUESTED</div>
                      <div className="mt-2 font-mono text-[18px] font-semibold text-n-display">
                        {transportSummary.requested}
                      </div>
                    </div>

                    <div className="col-span-2 flex items-center justify-center">
                      <div className="font-display text-[28px] text-n-display">→</div>
                    </div>

                    <div className="col-span-5 rounded-compact border border-n-border px-4 py-4">
                      <div className="label">SERVED</div>
                      <div className="mt-2 font-mono text-[18px] font-semibold text-n-display">
                        {transportSummary.selected}
                      </div>
                    </div>
                  </div>

                  <div className="mt-5 rounded-compact border border-n-border px-4 py-4">
                    <div className="label">WHY THIS PROTOCOL</div>
                    <div className="mt-2 text-[14px] text-n-primary">
                      {transportSummary.reason}
                    </div>
                  </div>
                </div>
              ) : null}

              <div className="grid grid-cols-12 gap-6">
                <div className="col-span-7 rounded-card border border-n-border bg-n-surface p-6">
                  <div className="flex items-center justify-between gap-3">
                    <div className="label">ATTEMPT CHAIN</div>
                    <div className="font-mono text-[11px] text-n-secondary">
                      {selected.attempts_payload.length} attempts
                    </div>
                  </div>

                  <div className="mt-4 space-y-3">
                    {selected.attempts_payload.length === 0 ? (
                      <div className="font-mono text-[11px] text-n-disabled">[NO ATTEMPTS RECORDED]</div>
                    ) : (
                      selected.attempts_payload.map((attempt) => (
                        <AttemptRow key={`${attempt.attempt_index}-${attempt.selected_model}`} attempt={attempt} />
                      ))
                    )}
                  </div>
                </div>

                <div className="col-span-5 rounded-card border border-n-border bg-n-surface p-6">
                  <div className="label">REQUEST SHAPE</div>
                  <div className="mt-4 grid grid-cols-2 gap-3">
                    <MiniMetric label="API FORMAT" value={(selected.api_format || "openai").toUpperCase()} />
                    <MiniMetric label="STEP TYPE" value={(selected.step_type || "general").toUpperCase()} />
                    <MiniMetric label="INPUT TOKENS" value={`${selected.usage_input_tokens || selected.input_tokens_after || 0}`} />
                    <MiniMetric label="OUTPUT TOKENS" value={`${selected.usage_output_tokens || 0}`} />
                  </div>

                  <div className="mt-5 border-t border-n-border pt-5">
                    <div className="label mb-3">TAGS</div>
                    <TagGroup title="FEATURE" items={selected.feature_tags} />
                    <TagGroup title="CONSTRAINT" items={selected.constraint_tags} />
                    <TagGroup title="HINT" items={selected.hint_tags} />
                  </div>

                  {(selected.error_code || selected.error_message) ? (
                    <div className="mt-5 rounded-compact border border-n-accent px-4 py-4">
                      <div className="label text-n-accent">ERROR</div>
                      <div className="mt-2 font-mono text-[12px] text-n-accent">
                        {selected.error_code || "upstream_error"}
                        {selected.error_stage ? ` · ${selected.error_stage}` : ""}
                      </div>
                      <div className="mt-2 text-[13px] text-n-primary">
                        {selected.error_message || "No detailed error message recorded."}
                      </div>
                    </div>
                  ) : null}
                </div>
              </div>
            </div>
          ) : (
            <EmptyPanel label="[TRACE DETAIL UNAVAILABLE]" />
          )}
        </div>
      </div>
    </div>
  );
}

function AttemptRow({ attempt }: { attempt: TraceAttempt }) {
  return (
    <div className="rounded-compact border border-n-border px-4 py-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <span className={`h-1.5 w-1.5 rounded-full ${attempt.success ? "bg-n-success" : attempt.blocked ? "bg-n-warning" : "bg-n-accent"}`} />
            <span className="font-mono text-[12px] uppercase tracking-[0.08em] text-n-secondary">
              Attempt {attempt.attempt_index}
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
            {attempt.blocked ? "BLOCKED" : attempt.success ? "SUCCESS" : `HTTP ${attempt.status_code || "—"}`}
          </div>
        </div>
      </div>

      {attempt.transport_reason ? (
        <div className="mt-3 text-[13px] text-n-primary">
          {attempt.transport_reason}
        </div>
      ) : null}

      <div className="mt-3 flex items-center justify-between gap-3 font-mono text-[11px] text-n-secondary">
        <span className="truncate">{attempt.target_url || "no target url recorded"}</span>
        <span>{attempt.transport_preference_source ? prettifySource(attempt.transport_preference_source) : ""}</span>
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

function TagGroup({ title, items }: { title: string; items: string[] }) {
  if (!items || items.length === 0) return null;
  return (
    <div className="mt-3">
      <div className="font-mono text-[11px] text-n-secondary">{title}</div>
      <div className="mt-2 flex flex-wrap gap-2">
        {items.map((item) => (
          <Badge key={`${title}-${item}`}>{item}</Badge>
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

function prettifySource(source?: string) {
  if (!source) return "unspecified";
  return source.replace(/-/g, " ").replace(/_/g, " ").toUpperCase();
}
