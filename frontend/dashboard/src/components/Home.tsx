/**
 * Nothing Design: Home dashboard
 *
 * Three vertical zones:
 *   Zone A: Hero savings (primary — Doto display + one context line)
 *   Zone B: 4-column stat grid (secondary — requests, tiers, model pool)
 *   Zone C: Live traffic (fills remaining viewport, dot-grid empty state)
 */

import { useEffect, useState } from "react";
import { type Health, type Stats, type RecentRequest, fetchRecent } from "../api";

interface Props {
  stats: Stats | null;
  health: Health | null;
}

export default function Home({ stats, health }: Props) {
  const [recent, setRecent] = useState<RecentRequest[]>([]);

  useEffect(() => {
    const load = async () => {
      const data = await fetchRecent(8);
      if (data) setRecent(data);
    };
    load();
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, []);

  const totalRequests = stats?.total_requests ?? 0;
  const totalSaved = stats?.total_savings_absolute ?? 0;
  const savingsRatio = stats?.total_savings_ratio ?? 0;
  const baselineCost = stats?.total_baseline_cost ?? 0;

  const lowCount = stats?.by_tier?.SIMPLE?.count ?? 0;
  const midCount = stats?.by_tier?.MEDIUM?.count ?? 0;
  const highCount = stats?.by_tier?.COMPLEX?.count ?? 0;
  const totalTier = lowCount + midCount + highCount || 1;

  const upstreamModels = health?.model_mapper?.upstream_models ?? 0;
  const poolSize = health?.model_mapper?.pool_size ?? 0;

  // ─── Empty state ───
  if (totalRequests === 0) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[calc(100vh-64px)] animate-fadeIn">
        <div className="font-display text-[64px] text-n-display tracking-tight">0</div>
        <div className="label mt-4">REQUESTS ROUTED</div>
        <p className="mt-6 text-[14px] text-n-secondary max-w-sm text-center">
          Send a request through the proxy to see routing in action.
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
        <div className="label mb-2">TOTAL SAVED</div>
        <div className="font-display text-[64px] leading-none text-n-display tracking-tight">
          ${totalSaved.toFixed(2)}
        </div>
        <div className="mt-3 font-mono text-[13px] text-n-secondary">
          saving <span className="text-n-success">{(savingsRatio * 100).toFixed(0)}%</span> vs ${baselineCost.toFixed(2)} baseline
        </div>
      </div>

      {/* ─── ZONE B: 4-Column Stat Grid ─── */}
      <div className="grid grid-cols-4 gap-px bg-n-border mb-10">
        {/* Requests */}
        <div className="bg-n-black p-5">
          <div className="label mb-2">REQUESTS</div>
          <div className="font-mono text-[32px] text-n-display leading-none tracking-tight">
            {totalRequests.toLocaleString()}
          </div>
          <div className="mt-3 font-mono text-[11px] text-n-disabled">
            routed through proxy
          </div>
        </div>

        {/* LOW tier */}
        <div className="bg-n-black p-5">
          <div className="label mb-2">LOW TIER</div>
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

        {/* MID tier (+ HIGH if nonzero) */}
        <div className="bg-n-black p-5">
          <div className="label mb-2">MID TIER</div>
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
          {highCount > 0 && (
            <div className="mt-3 font-mono text-[11px] text-n-accent">
              {highCount} HIGH
            </div>
          )}
        </div>

        {/* Model Pool */}
        <div className="bg-n-black p-5">
          <div className="label mb-2">MODEL POOL</div>
          <div className="font-mono text-[28px] text-n-display leading-none">
            {upstreamModels}
          </div>
          <div className="mt-2 font-mono text-[11px] text-n-disabled">
            upstream
          </div>
          {poolSize !== upstreamModels && (
            <div className="font-mono text-[11px] text-n-disabled">
              {poolSize} in pool
            </div>
          )}
        </div>
      </div>

      {/* ─── ZONE C: Live Traffic (fills remaining viewport) ─── */}
      <div className="flex-1 flex flex-col">
        <div className="flex items-center justify-between mb-3">
          <div className="label">LIVE TRAFFIC</div>
          <div className="label">{recent.length} LATEST</div>
        </div>

        <div className="flex-1 border-t border-n-border">
          {recent.length > 0 ? (
            recent.map((r, i) => (
              <div
                key={r.request_id || i}
                className="border-b border-n-border py-3 px-1 flex items-start gap-4 hover:bg-n-surface/50 transition-colors duration-150"
              >
                <span className={`mt-1.5 h-2 w-2 rounded-full shrink-0 ${
                  r.tier === "COMPLEX" ? "bg-n-accent" :
                  r.tier === "MEDIUM" ? "bg-n-warning" :
                  "bg-n-success"
                }`} />
                <div className="min-w-0 flex-1">
                  <div className="text-[13px] text-n-primary truncate">
                    {r.prompt_preview || "[no preview]"}
                  </div>
                  <div className="mt-1 flex items-center gap-3 font-mono text-[12px] tracking-[0.04em] text-n-disabled">
                    <span>{r.tier || "—"}</span>
                    <span>·</span>
                    <span>{r.method || "pool"}</span>
                    <span>·</span>
                    <span>{r.transport || "openai"}</span>
                  </div>
                </div>
                <div className="shrink-0 text-right">
                  <div className="font-mono text-[12px] text-n-primary">
                    {(r.model || "").split("/").pop()}
                  </div>
                  <div className="font-mono text-[11px] text-n-success mt-0.5">
                    ${r.cost?.toFixed(4) ?? "0.0000"}
                  </div>
                </div>
              </div>
            ))
          ) : (
            <div className="flex-1 flex items-center justify-center dot-grid-subtle min-h-[300px]">
              <span className="font-mono text-[11px] text-n-disabled tracking-[0.1em]">
                AWAITING FIRST REQUEST
              </span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
