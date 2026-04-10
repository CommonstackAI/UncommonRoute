/**
 * Nothing Design: Home dashboard
 *
 * Three-layer hierarchy:
 *   Primary: Total savings (Doto display, hero number)
 *   Secondary: Request count, model count, tier distribution
 *   Tertiary: Live traffic feed, metadata labels
 *
 * Signature elements: segmented progress bars, Space Mono labels,
 * data as beauty, mechanical honesty
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
  const actualCost = stats?.total_actual_cost ?? 0;
  const baselineCost = stats?.total_baseline_cost ?? 0;
  const modelCount = health?.model_mapper?.upstream_models ?? health?.model_mapper?.pool_size ?? 0;

  const tiers = [
    { key: "SIMPLE", label: "LOW", count: stats?.by_tier?.SIMPLE?.count ?? 0 },
    { key: "MEDIUM", label: "MID", count: stats?.by_tier?.MEDIUM?.count ?? 0 },
    { key: "COMPLEX", label: "HIGH", count: stats?.by_tier?.COMPLEX?.count ?? 0 },
  ];
  const totalTier = tiers.reduce((s, t) => s + t.count, 0) || 1;

  const topModels = Object.entries(stats?.by_model ?? {})
    .map(([name, data]) => ({ name: name.split("/").pop() || name, count: data.count, cost: data.total_cost }))
    .sort((a, b) => b.count - a.count)
    .slice(0, 5);
  const maxCount = topModels[0]?.count || 1;

  // ─── Empty state ───
  if (totalRequests === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-32">
        <div className="font-display text-[48px] text-n-display tracking-tight">0</div>
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
    <div>
      {/* ─── LAYER 1: Hero savings (Primary — one thing the user sees first) ─── */}
      <div className="mb-16">
        <div className="label mb-3">TOTAL SAVED</div>
        <div className="flex items-baseline gap-4">
          <span className="font-display text-[72px] leading-none text-n-display tracking-tight">
            ${totalSaved.toFixed(2)}
          </span>
          <span className="font-mono text-[14px] text-n-success">
            {(savingsRatio * 100).toFixed(1)}% LESS
          </span>
        </div>

        {/* Cost bar — segmented progress (Nothing signature) */}
        <div className="mt-6 flex items-center gap-6">
          <StatPair label="BASELINE" value={`$${baselineCost.toFixed(2)}`} />
          <StatPair label="ACTUAL" value={`$${actualCost.toFixed(2)}`} color="text-n-success" />
          <StatPair label="MODELS" value={`${modelCount}`} />
        </div>

        {/* Savings bar visualization */}
        <div className="mt-4 segmented-bar" style={{ maxWidth: 400 }}>
          {Array.from({ length: 20 }).map((_, i) => {
            const threshold = Math.round(savingsRatio * 20);
            return (
              <div
                key={i}
                className={`segment ${i < threshold ? "success" : ""}`}
              />
            );
          })}
        </div>
        <div className="mt-2 label">
          SAVINGS RATIO — {(savingsRatio * 100).toFixed(0)}% OF BASELINE COST AVOIDED
        </div>
      </div>

      {/* ─── LAYER 2: Metrics grid (Secondary) ─── */}
      <div className="grid grid-cols-3 gap-px bg-n-border mb-12">
        {/* Requests */}
        <div className="bg-n-black p-6">
          <div className="label mb-2">REQUESTS ROUTED</div>
          <div className="font-mono text-[36px] text-n-display leading-none tracking-tight">
            {totalRequests.toLocaleString()}
          </div>
        </div>

        {/* Tier distribution */}
        <div className="bg-n-black p-6">
          <div className="label mb-3">TIER DISTRIBUTION</div>
          <div className="flex gap-4">
            {tiers.map((t) => (
              <div key={t.key} className="flex-1">
                <div className="font-mono text-[24px] text-n-display leading-none">
                  {t.count}
                </div>
                <div className="label mt-1">{t.label}</div>
                {/* Mini segmented bar */}
                <div className="mt-2 flex gap-px h-[4px]">
                  {Array.from({ length: 10 }).map((_, i) => (
                    <div
                      key={i}
                      className={`flex-1 ${i < Math.round((t.count / totalTier) * 10) ? "bg-n-primary" : "bg-n-border"}`}
                    />
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Top models */}
        <div className="bg-n-black p-6">
          <div className="label mb-3">TOP MODELS</div>
          <div className="space-y-2">
            {topModels.map((m) => (
              <div key={m.name} className="flex items-center gap-3">
                <span className="font-mono text-[11px] text-n-secondary w-24 truncate">
                  {m.name}
                </span>
                <div className="flex-1 flex gap-px h-[3px]">
                  {Array.from({ length: 10 }).map((_, i) => (
                    <div
                      key={i}
                      className={`flex-1 ${i < Math.round((m.count / maxCount) * 10) ? "bg-n-primary" : "bg-n-border"}`}
                    />
                  ))}
                </div>
                <span className="font-mono text-[11px] text-n-display w-8 text-right">
                  {m.count}
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* ─── LAYER 3: Live traffic (Tertiary — metadata, never competing) ─── */}
      <div>
        <div className="flex items-center justify-between mb-4">
          <div className="label">LIVE TRAFFIC</div>
          <div className="label">{recent.length} LATEST</div>
        </div>

        <div className="border-t border-n-border">
          {recent.map((r, i) => (
            <div
              key={r.request_id || i}
              className="border-b border-n-border py-3 px-1 flex items-start gap-4 hover:bg-n-surface/50 transition-colors duration-150"
            >
              {/* Tier dot */}
              <span className={`mt-1.5 h-2 w-2 rounded-full shrink-0 ${
                r.tier === "COMPLEX" ? "bg-n-accent" :
                r.tier === "MEDIUM" ? "bg-n-warning" :
                "bg-n-success"
              }`} />

              {/* Prompt */}
              <div className="min-w-0 flex-1">
                <div className="text-[13px] text-n-primary truncate">
                  {r.prompt_preview || "[no preview]"}
                </div>
                <div className="mt-1 flex items-center gap-3 font-mono text-[10px] tracking-[0.04em] text-n-disabled">
                  <span>{r.tier || "—"}</span>
                  <span>·</span>
                  <span>{r.method || "pool"}</span>
                  <span>·</span>
                  <span>{r.transport || "openai"}</span>
                </div>
              </div>

              {/* Model + cost */}
              <div className="shrink-0 text-right">
                <div className="font-mono text-[12px] text-n-primary">
                  {(r.model || "").split("/").pop()}
                </div>
                <div className="font-mono text-[11px] text-n-success mt-0.5">
                  ${r.cost?.toFixed(4) ?? "0.0000"}
                </div>
              </div>
            </div>
          ))}

          {recent.length === 0 && (
            <div className="py-8 text-center font-mono text-[11px] text-n-disabled tracking-[0.06em]">
              [WAITING FOR REQUESTS...]
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/* ─── Sub-components ─── */

function StatPair({ label, value, color = "text-n-display" }: { label: string; value: string; color?: string }) {
  return (
    <div>
      <div className="label mb-1">{label}</div>
      <div className={`font-mono text-[16px] ${color}`}>{value}</div>
    </div>
  );
}
