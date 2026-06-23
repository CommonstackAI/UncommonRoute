/**
 * Nothing Design: Home dashboard
 *
 * Three vertical zones:
 *   Zone A: Hero savings (primary — Doto display + one context line)
 *   Zone B: 4-column stat grid (secondary — requests, tiers, model pool)
 *   Zone C: Live traffic (fills remaining viewport, dot-grid empty state)
 */

import { useEffect, useRef } from "react";
import { type Stats } from "../api";
import { useLiveData, type LiveRecent } from "../state/LiveDataContext";
import { useT } from "../i18n";

interface Props {
  stats: Stats | null;
}

const TIER_DOT: Record<string, string> = {
  COMPLEX: "bg-n-accent",
  MEDIUM: "bg-n-warning",
  SIMPLE: "bg-n-success",
};

const TIER_TEXT: Record<string, string> = {
  COMPLEX: "text-n-accent",
  MEDIUM: "text-n-warning",
  SIMPLE: "text-n-success",
};

const TIER_RANK: Record<string, number> = { COMPLEX: 3, MEDIUM: 2, SIMPLE: 1 };

interface TurnGroup {
  key: string;
  representative: LiveRecent;
  entries: LiveRecent[];
  tierCounts: Array<[string, number]>;
  dominantTier: string;
  totalCost: number;
  maxRouteMs: number;
  maxUpstreamMs: number;
  firstTokenMs: number;
  hasPending: boolean;
  hasRouted: boolean;
}

function groupTurns(items: LiveRecent[]): TurnGroup[] {
  const buckets = new Map<string, TurnGroup>();
  const order: string[] = [];
  for (const r of items) {
    const key = r.turn_id || `_solo:${r.request_id}`;
    let g = buckets.get(key);
    if (!g) {
      g = {
        key,
        representative: r,
        entries: [],
        tierCounts: [],
        dominantTier: "",
        totalCost: 0,
        maxRouteMs: 0,
        maxUpstreamMs: 0,
        firstTokenMs: 0,
        hasPending: false,
        hasRouted: false,
      };
      buckets.set(key, g);
      order.push(key);
    }
    g.entries.push(r);
    if (r.prompt_preview && !g.representative.prompt_preview) g.representative = r;
    g.totalCost += r.cost ?? 0;
    g.maxRouteMs = Math.max(g.maxRouteMs, r.route_latency_ms ?? ((r.latency_us ?? 0) / 1000));
    g.maxUpstreamMs = Math.max(g.maxUpstreamMs, r.upstream_elapsed_ms ?? 0);
    if (!g.firstTokenMs && (r.first_token_ms ?? 0) > 0) g.firstTokenMs = r.first_token_ms ?? 0;
    if (r.state === "pending") g.hasPending = true;
    if (r.state === "routed") g.hasRouted = true;
  }
  for (const g of buckets.values()) {
    const counts = new Map<string, number>();
    for (const r of g.entries) {
      const t = (r.tier || "").toUpperCase();
      if (!t) continue;
      counts.set(t, (counts.get(t) || 0) + 1);
    }
    g.tierCounts = Array.from(counts.entries()).sort(
      (a, b) => (TIER_RANK[b[0]] ?? 0) - (TIER_RANK[a[0]] ?? 0),
    );
    g.dominantTier = g.tierCounts[0]?.[0] ?? "";
  }
  return order.map((k) => buckets.get(k)!);
}

function turnDotClass(g: TurnGroup): string {
  if (g.hasPending) return "bg-n-interactive animate-dotPulse";
  const base = TIER_DOT[g.dominantTier] ?? "bg-n-success";
  if (g.hasRouted) return `${base} animate-dotPulse`;
  return base;
}

export default function Home({ stats }: Props) {
  const t = useT();
  const { recent } = useLiveData();
  const turns = groupTurns(recent).slice(0, 8);

  const seenTurnsRef = useRef<Set<string>>(new Set());
  const initialPrimedRef = useRef(false);

  const newKeys = new Set<string>();
  if (initialPrimedRef.current) {
    for (const g of turns) {
      if (!seenTurnsRef.current.has(g.key)) newKeys.add(g.key);
    }
  }

  useEffect(() => {
    if (!initialPrimedRef.current) {
      if (turns.length > 0) {
        seenTurnsRef.current = new Set(turns.map((g) => g.key));
        initialPrimedRef.current = true;
      }
      return;
    }
    for (const g of turns) seenTurnsRef.current.add(g.key);
  }, [turns]);

  const totalRequests = stats?.total_requests ?? 0;
  const totalSaved = stats?.total_savings_absolute ?? 0;
  const savingsRatio = stats?.total_savings_ratio ?? 0;
  const baselineCost = stats?.total_baseline_cost ?? 0;

  const lowCount = stats?.by_tier?.SIMPLE?.count ?? 0;
  const midCount = stats?.by_tier?.MEDIUM?.count ?? 0;
  const highCount = stats?.by_tier?.COMPLEX?.count ?? 0;
  const totalTier = lowCount + midCount + highCount || 1;

  // ─── Empty state ───
  if (totalRequests === 0 && turns.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[calc(100vh-64px)] animate-fadeIn">
        <div className="font-display text-[64px] text-n-display tracking-tight">0</div>
        <div className="label mt-4">{t.home.requestsRouted}</div>
        <p className="mt-6 text-[14px] text-n-secondary max-w-sm text-center">
          {t.home.emptyHint}
        </p>
        <div className="mt-8 bg-n-surface border border-n-border rounded-compact p-5 font-mono text-[12px] text-n-secondary leading-relaxed max-w-lg w-full">
          <span className="text-n-disabled">$</span> curl localhost:8403/v1/chat/completions \{"\n"}
          {"  "}-H "Content-Type: application/json" \{"\n"}
          {"  "}-d '{`{"model":"uncommon-route/auto","messages":[{"role":"user","content":"hello"}]}`}'
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col min-h-[calc(100vh-64px)] pt-4 animate-fadeIn">
      {/* ─── ZONE A: Hero Savings ─── */}
      <div className="mb-10">
        <div className="label mb-2">{t.home.totalSaved}</div>
        <div className="font-display text-[64px] leading-none text-n-display tracking-tight">
          ${totalSaved.toFixed(2)}
        </div>
        <div className="mt-3 font-mono text-[13px] text-n-secondary">
          {t.home.savingBefore(baselineCost.toFixed(2))}<span className="text-n-success">{(savingsRatio * 100).toFixed(0)}%</span>{t.home.savingAfter(baselineCost.toFixed(2))}
        </div>
      </div>

      {/* ─── ZONE B: 4-Column Stat Grid ─── */}
      <div className="grid grid-cols-4 gap-px bg-n-border mb-10">
        {/* Requests */}
        <div className="bg-n-black p-5">
          <div className="label mb-2">{t.home.requests}</div>
          <div className="font-mono text-[32px] text-n-display leading-none tracking-tight">
            {totalRequests.toLocaleString()}
          </div>
          <div className="mt-3 font-mono text-[11px] text-n-disabled">
            {t.home.routedThroughProxy}
          </div>
        </div>

        {/* LOW tier */}
        <div className="bg-n-black p-5">
          <div className="label mb-2">{t.home.lowTier}</div>
          <div className="font-mono text-[28px] text-n-display leading-none">
            {lowCount}
          </div>
          <div className="mt-2 flex items-center gap-2">
            <div className="flex gap-px h-[4px] flex-1">
              {Array.from({ length: 10 }).map((_, i) => (
                <div
                  key={i}
                  className={`flex-1 ${i < Math.round((lowCount / totalTier) * 10) ? "bg-n-success" : "bg-n-border"}`}
                />
              ))}
            </div>
            <span className="font-mono text-[11px] text-n-disabled">
              {Math.round((lowCount / totalTier) * 100)}%
            </span>
          </div>
        </div>

        {/* MID tier */}
        <div className="bg-n-black p-5">
          <div className="label mb-2">{t.home.midTier}</div>
          <div className="font-mono text-[28px] text-n-display leading-none">
            {midCount}
          </div>
          <div className="mt-2 flex items-center gap-2">
            <div className="flex gap-px h-[4px] flex-1">
              {Array.from({ length: 10 }).map((_, i) => (
                <div
                  key={i}
                  className={`flex-1 ${i < Math.round((midCount / totalTier) * 10) ? "bg-n-warning" : "bg-n-border"}`}
                />
              ))}
            </div>
            <span className="font-mono text-[11px] text-n-disabled">
              {Math.round((midCount / totalTier) * 100)}%
            </span>
          </div>
        </div>

        {/* HIGH tier */}
        <div className="bg-n-black p-5">
          <div className="label mb-2">{t.home.highTier}</div>
          <div className="font-mono text-[28px] text-n-display leading-none">
            {highCount}
          </div>
          <div className="mt-2 flex items-center gap-2">
            <div className="flex gap-px h-[4px] flex-1">
              {Array.from({ length: 10 }).map((_, i) => (
                <div
                  key={i}
                  className={`flex-1 ${i < Math.round((highCount / totalTier) * 10) ? "bg-n-accent" : "bg-n-border"}`}
                />
              ))}
            </div>
            <span className="font-mono text-[11px] text-n-disabled">
              {Math.round((highCount / totalTier) * 100)}%
            </span>
          </div>
        </div>
      </div>

      {/* ─── ZONE C: Live Traffic (fills remaining viewport) ─── */}
      <div className="flex-1 flex flex-col">
        <div className="flex items-center justify-between mb-3">
          <div className="label">{t.home.liveTraffic}</div>
          <div className="label">{t.home.latest(turns.length)}</div>
        </div>

        <div className="flex-1 border-t border-n-border">
          {turns.length > 0 ? (
            turns.map((g) => {
              const isNew = newKeys.has(g.key);
              const animClass = isNew ? "animate-rowEnter" : "";
              const dotFlashClass = isNew ? "animate-dotFlash" : "";
              const rep = g.representative;
              const modelTail = (rep.model || "").split("/").pop() || "";
              const calls = g.entries.length;
              return (
                <div
                  key={g.key}
                  className={`border-b border-n-border py-3 px-1 flex items-start gap-4 hover:bg-n-surface/50 transition-colors duration-150 ${animClass}`}
                >
                  <span className={`mt-1.5 h-2 w-2 rounded-full shrink-0 ${turnDotClass(g)} ${dotFlashClass}`} />
                  <div className="min-w-0 flex-1">
                    <div className="text-[13px] text-n-primary truncate">
                      {rep.prompt_preview || (g.hasPending ? "…" : t.common.noPreview)}
                    </div>
                    <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-[12px] tracking-[0.04em] text-n-disabled">
                      {g.tierCounts.length > 0 ? (
                        g.tierCounts.map(([tier, n], i) => (
                          <span key={tier} className="flex items-center gap-3">
                            {i > 0 ? <span>/</span> : null}
                            <span className={calls > 1 ? (TIER_TEXT[tier] ?? "") : ""}>
                              {calls > 1 ? t.home.toolCalls(n, t.common.tierLabel(tier)) : t.common.tierLabel(tier)}
                            </span>
                          </span>
                        ))
                      ) : (
                        <span>{g.hasPending ? t.common.routing : "—"}</span>
                      )}
                      <span>·</span>
                      <span>{rep.transport || "openai"}</span>
                      {g.maxUpstreamMs > 0 ? (
                        <>
                          <span>·</span>
                          <span>upstream {formatMs(g.maxUpstreamMs)}</span>
                        </>
                      ) : g.maxRouteMs > 0 ? (
                        <>
                          <span>·</span>
                          <span>route {formatMs(g.maxRouteMs)}</span>
                        </>
                      ) : null}
                    </div>
                  </div>
                  <div className="shrink-0 text-right">
                    <div className="font-mono text-[12px] text-n-primary">
                      {modelTail || (g.hasPending ? "…" : "")}
                    </div>
                    <div className="font-mono text-[11px] text-n-success mt-0.5">
                      {g.hasPending ? "···" : `$${g.totalCost.toFixed(4)}`}
                    </div>
                  </div>
                </div>
              );
            })
          ) : (
            <div className="flex-1 flex items-center justify-center dot-grid-subtle min-h-[300px]">
              <span className="font-mono text-[11px] text-n-disabled tracking-[0.1em]">
                {t.common.awaitingFirstRequest}
              </span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function formatMs(value: number): string {
  if (value >= 1000) return `${(value / 1000).toFixed(1)}s`;
  return `${value.toFixed(0)}ms`;
}
