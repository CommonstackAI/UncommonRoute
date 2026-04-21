import { type ReactNode, useMemo, useState } from "react";
import type { Stats } from "../api";

type UsageView = "requests" | "cost" | "avg";

interface Props {
  stats: Stats | null;
}

export default function Activity({ stats }: Props) {
  const [usageView, setUsageView] = useState<UsageView>("requests");

  const models = useMemo(() => {
    if (!stats) return [];
    const rows = Object.entries(stats.by_model).map(([name, data]) => {
      const avgCost = data.count > 0 ? data.total_cost / data.count : 0;
      return {
        name,
        count: data.count,
        total_cost: data.total_cost,
        avg_cost: avgCost,
        share: stats.total_requests > 0 ? (data.count / stats.total_requests) * 100 : 0,
      };
    });

    rows.sort((a, b) => getUsageValue(b, usageView) - getUsageValue(a, usageView));
    return rows;
  }, [stats, usageView]);

  if (!stats || stats.total_requests === 0) {
    return <div className="flex items-center justify-center py-20 font-mono text-[14px] text-n-disabled">No activity recorded yet.</div>;
  }

  const simpleCount = stats.by_tier.SIMPLE?.count ?? 0;
  const mediumCount = stats.by_tier.MEDIUM?.count ?? 0;
  const complexCount = stats.by_tier.COMPLEX?.count ?? 0;

  const simpleCost = stats.by_tier.SIMPLE?.total_cost ?? 0;
  const mediumCost = stats.by_tier.MEDIUM?.total_cost ?? 0;
  const complexCost = stats.by_tier.COMPLEX?.total_cost ?? 0;

  const classifiedCount = simpleCount + mediumCount + complexCount;
  const passthroughCount = Math.max(stats.total_requests - classifiedCount, 0);

  const tierBuckets = [
    { label: "Simple", count: simpleCount, totalCost: simpleCost },
    { label: "Medium", count: mediumCount, totalCost: mediumCost },
    { label: "Complex", count: complexCount, totalCost: complexCost },
  ];

  const totalTierCount = tierBuckets.reduce((sum, bucket) => sum + bucket.count, 0) || 1;
  const tierSegments = tierBuckets.map((bucket) => ({
    ...bucket,
    pct: (bucket.count / totalTierCount) * 100,
  }));
  const dominantBucket = tierSegments.reduce((best, bucket) => (bucket.count > best.count ? bucket : best));
  const complexShare = (complexCount / totalTierCount) * 100;

  const modeTiles = Object.entries(stats.by_mode)
    .sort((a, b) => b[1] - a[1])
    .map(([mode, count]) => ({
      mode,
      count,
      pct: stats.total_requests > 0 ? (count / stats.total_requests) * 100 : 0,
      ...getModeMeta(mode),
    }));

  const transportRows = Object.entries(stats.by_transport)
    .map(([transport, data]) => ({
      transport,
      count: data.count,
      total_cost: data.total_cost,
      pct: stats.total_requests > 0 ? (data.count / stats.total_requests) * 100 : 0,
      ...getTransportMeta(transport),
    }))
    .sort((a, b) => b.count - a.count);

  const maxUsageValue = Math.max(...models.map((row) => getUsageValue(row, usageView)), 0.000001);

  return (
    <div className="space-y-6 animate-fadeIn">
      <div>
        <h1 className="font-display text-[36px] text-n-display tracking-tight">ACTIVITY</h1>
        <p className="mt-1 text-[13px] text-n-secondary">
          {formatTimeRange(stats.time_range_s)} · {stats.total_requests.toLocaleString()} routed requests
        </p>
      </div>

      {/* Overview stat row */}
      <div className="grid grid-cols-12 gap-3">
        <div className="col-span-3">
          <OverviewCard
            label="ACTUAL SPEND"
            value={<>${stats.total_actual_cost.toFixed(2)}</>}
            meta={`$${stats.total_baseline_cost.toFixed(2)} baseline`}
          />
        </div>
        <div className="col-span-3">
          <OverviewCard
            label="SAVED"
            value={<>{(stats.total_savings_ratio * 100).toFixed(1)}%</>}
            meta={`$${stats.total_savings_absolute.toFixed(2)} below baseline`}
            tone="success"
          />
        </div>
        <div className="col-span-3">
          <OverviewCard
            label="AVG LATENCY"
            value={<>{stats.avg_latency_ms.toFixed(1)}ms</>}
            meta={`${stats.total_requests.toLocaleString()} routed turns`}
          />
        </div>
        <div className="col-span-3">
          <OverviewCard
            label="OPTIMIZATION"
            value={<>{(stats.avg_input_reduction_ratio * 100).toFixed(1)}%</>}
            meta={`${(stats.avg_cache_hit_ratio * 100).toFixed(1)}% cache hit \u00B7 $${stats.total_compaction_savings.toFixed(2)} compaction`}
          />
        </div>
      </div>

      <div className="grid grid-cols-12 gap-5">
        {/* Request Complexity */}
        <div className="col-span-7">
          <div className="flex h-full flex-col rounded-card border border-n-border bg-n-surface p-6">
            <div>
              <div className="flex items-center justify-between mb-5">
                <div className="label">REQUEST COMPLEXITY</div>
                <div className="font-mono text-[11px] text-n-secondary">
                  {classifiedCount} classified · {passthroughCount} passthrough
                </div>
              </div>

              {/* Segmented bar for tier distribution */}
              <div className="segmented-bar" style={{ height: "8px" }}>
                {tierSegments.map((bucket) => {
                  const segCount = Math.max(Math.round(bucket.pct / 5), bucket.count > 0 ? 1 : 0);
                  return Array.from({ length: segCount }).map((_, i) => (
                    <div key={`${bucket.label}-${i}`} className="segment filled" />
                  ));
                })}
                {/* Fill remaining to total 20 */}
                {(() => {
                  const filled = tierSegments.reduce((s, b) => s + Math.max(Math.round(b.pct / 5), b.count > 0 ? 1 : 0), 0);
                  const empty = Math.max(20 - filled, 0);
                  return Array.from({ length: empty }).map((_, i) => (
                    <div key={`empty-${i}`} className="segment" />
                  ));
                })()}
              </div>

              <div className="mt-5 grid grid-cols-3 gap-3">
                {tierSegments.map((bucket) => (
                  <div key={bucket.label} className="rounded-compact border border-n-border px-4 py-4">
                    <div className="flex items-center justify-between">
                      <span className="h-1.5 w-1.5 rounded-full bg-n-display" />
                      <span className="font-mono text-[11px] text-n-secondary">{bucket.pct.toFixed(0)}%</span>
                    </div>
                    <div className="mt-4 font-mono text-2xl font-semibold tracking-tight text-n-display">
                      {bucket.count.toLocaleString()}
                    </div>
                    <div className="mt-1 label">{bucket.label.toUpperCase()}</div>
                    <div className="mt-3 font-mono text-[11px] text-n-secondary">
                      Spent ${bucket.totalCost.toFixed(2)}
                    </div>
                  </div>
                ))}
              </div>
            </div>

            <div className="mt-4 grid grid-cols-3 gap-2">
              <MiniMetric label="DOMINANT BAND" value={`${dominantBucket.label} ${dominantBucket.pct.toFixed(0)}%`} />
              <MiniMetric label="COMPLEX SHARE" value={`${complexShare.toFixed(0)}%`} />
              <MiniMetric label="PASSTHROUGH" value={passthroughCount.toLocaleString()} />
            </div>
          </div>
        </div>

        {/* By Mode */}
        <div className="col-span-5">
          <div className="flex h-full flex-col gap-5">
            <div className="rounded-card border border-n-border bg-n-surface p-6">
              <div className="flex items-center justify-between mb-5">
                <div className="label">BY MODE</div>
                <div className="font-mono text-[11px] text-n-secondary">{modeTiles.length} active modes</div>
              </div>
              <div className="grid grid-cols-2 gap-3">
                {modeTiles.map((tile) => (
                  <div key={tile.mode} className="rounded-compact border border-n-border px-4 py-4">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <span className="h-1.5 w-1.5 rounded-full bg-n-display" />
                        <span className="font-mono text-[12px] uppercase tracking-wider text-n-secondary">{tile.mode}</span>
                      </div>
                      <span className="font-mono text-[11px] text-n-secondary">{tile.pct.toFixed(0)}%</span>
                    </div>
                    <div className="mt-3 font-mono text-2xl font-semibold tracking-tight text-n-display">
                      {tile.count.toLocaleString()}
                    </div>
                    <div className="mt-1 font-mono text-[11px] text-n-secondary">{tile.description}</div>
                    <div className="mt-3 flex gap-[1px]" style={{ height: "4px" }}>
                      {Array.from({ length: 10 }).map((_, i) => (
                        <div
                          key={i}
                          className={`flex-1 ${i < Math.round(tile.pct / 10) ? "bg-n-display" : "bg-n-border"}`}
                        />
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </div>

            <div className="rounded-card border border-n-border bg-n-surface p-6">
              <div className="flex items-center justify-between mb-5">
                <div className="label">TRANSPORT MIX</div>
                <div className="font-mono text-[11px] text-n-secondary">{transportRows.length} active paths</div>
              </div>
              <div className="space-y-3">
                {transportRows.map((row) => (
                  <div key={row.transport} className="rounded-compact border border-n-border px-4 py-4">
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <div className="font-mono text-[12px] uppercase tracking-wider text-n-secondary">
                          {row.label}
                        </div>
                        <div className="mt-1 font-mono text-[11px] text-n-secondary">
                          {row.description}
                        </div>
                      </div>
                      <div className="text-right">
                        <div className="font-mono text-[18px] font-semibold tracking-tight text-n-display">
                          {row.count.toLocaleString()}
                        </div>
                        <div className="font-mono text-[11px] text-n-secondary">
                          {row.pct.toFixed(0)}%
                        </div>
                      </div>
                    </div>
                    <div className="mt-3 flex gap-[1px]" style={{ height: "4px" }}>
                      {Array.from({ length: 10 }).map((_, i) => (
                        <div
                          key={i}
                          className={`flex-1 ${i < Math.max(Math.round(row.pct / 10), row.count > 0 ? 1 : 0) ? "bg-n-display" : "bg-n-border"}`}
                        />
                      ))}
                    </div>
                    <div className="mt-3 flex items-center justify-between font-mono text-[11px] text-n-secondary">
                      <span>{row.transport}</span>
                      <span>${row.total_cost.toFixed(4)}</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>

        {/* Model Usage Table */}
        <div className="col-span-12">
          <div className="rounded-card border border-n-border bg-n-surface overflow-hidden">
            <div className="flex items-center justify-between border-b border-n-border px-6 py-4">
              <span className="label">MODEL USAGE</span>
              <div className="inline-flex gap-[2px] rounded-compact bg-n-black p-[2px]">
                {(["requests", "cost", "avg"] as UsageView[]).map((view) => {
                  const active = usageView === view;
                  return (
                    <button
                      key={view}
                      onClick={() => setUsageView(view)}
                      className={`rounded-[6px] px-3 py-1.5 font-mono text-[12px] uppercase tracking-wider transition-colors ${
                        active ? "bg-n-raised text-n-display" : "text-n-secondary hover:text-n-primary"
                      }`}
                    >
                      {view === "requests" ? "REQUESTS" : view === "cost" ? "COST" : "AVG COST"}
                    </button>
                  );
                })}
              </div>
            </div>
            <table className="w-full">
              <thead>
                <tr className="border-b border-n-border">
                  <th className="label px-6 py-3 text-left">MODEL</th>
                  <th className="label px-6 py-3 text-right">REQUESTS</th>
                  <th className="label px-6 py-3 text-right">SHARE</th>
                  <th className="label px-6 py-3 text-right">TOTAL COST</th>
                  <th className="label px-6 py-3 text-right">AVG / REQ</th>
                </tr>
              </thead>
              <tbody>
                {models.map((row) => {
                  const focusValue = getUsageValue(row, usageView);
                  const focusWidth = Math.max((focusValue / maxUsageValue) * 100, 3);
                  return (
                    <tr key={row.name} className="border-b border-n-border last:border-0 row-hover hover:bg-n-raised">
                      <td className="px-6 py-3.5">
                        <div className="flex items-center gap-3">
                          <div className="flex w-24 gap-[1px]" style={{ height: "4px" }}>
                            {Array.from({ length: 10 }).map((_, i) => (
                              <div
                                key={i}
                                className={`flex-1 ${i < Math.round(focusWidth / 10) ? "bg-n-display" : "bg-n-border"}`}
                              />
                            ))}
                          </div>
                          <div>
                            <div className="font-mono text-[13px] text-n-primary">
                              {row.name.split("/").pop()}
                            </div>
                            <div className="font-mono text-[11px] text-n-secondary">
                              {formatUsageValue(row, usageView)}
                            </div>
                          </div>
                        </div>
                      </td>
                      <td className="px-6 py-3.5 text-right font-mono text-[13px] text-n-secondary">{row.count.toLocaleString()}</td>
                      <td className="px-6 py-3.5 text-right font-mono text-[13px] text-n-secondary">{row.share.toFixed(1)}%</td>
                      <td className="px-6 py-3.5 text-right font-mono text-[13px] text-n-secondary">${row.total_cost.toFixed(4)}</td>
                      <td className="px-6 py-3.5 text-right font-mono text-[13px] text-n-secondary">${row.avg_cost.toFixed(4)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}

function MiniMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-compact border border-n-border px-4 py-3">
      <div className="label">{label}</div>
      <div className="mt-1 font-mono text-[16px] font-semibold tracking-tight text-n-display">
        {value}
      </div>
    </div>
  );
}

function OverviewCard({
  label,
  value,
  meta,
  tone = "default",
}: {
  label: string;
  value: ReactNode;
  meta: string;
  tone?: "default" | "success";
}) {
  return (
    <div className="h-full rounded-card border border-n-border bg-n-surface p-5">
      <div className="label">{label}</div>
      <div className={`mt-3 font-mono text-[34px] leading-none font-semibold tracking-tight ${tone === "success" ? "text-n-success" : "text-n-display"}`}>
        {value}
      </div>
      <div className="mt-2 font-mono text-[12px] text-n-secondary">{meta}</div>
    </div>
  );
}

function getModeMeta(mode: string): { description: string } {
  switch (mode) {
    case "best":
      return { description: "highest quality" };
    case "fast":
      return { description: "lighter and faster" };
    case "passthrough":
      return { description: "explicit model" };
    case "auto":
    default:
      return { description: "balanced default" };
  }
}

function getTransportMeta(transport: string): { label: string; description: string } {
  switch (transport) {
    case "anthropic-messages":
      return {
        label: "Anthropic Messages",
        description: "native block semantics preserved upstream",
      };
    case "openai-responses":
      return {
        label: "OpenAI Responses",
        description: "responses-style path",
      };
    case "openai-chat":
    default:
      return {
        label: "OpenAI Chat",
        description: "chat-completions compatible path",
      };
  }
}

function formatTimeRange(seconds: number): string {
  if (seconds <= 0) return "No recent window";
  if (seconds >= 86400) return `${(seconds / 86400).toFixed(1)}d history`;
  if (seconds >= 3600) return `${Math.round(seconds / 3600)}h history`;
  if (seconds >= 60) return `${Math.round(seconds / 60)}m history`;
  return `${Math.round(seconds)}s history`;
}

function getUsageValue(
  row: { count: number; total_cost: number; avg_cost: number },
  view: UsageView,
): number {
  if (view === "cost") return row.total_cost;
  if (view === "avg") return row.avg_cost;
  return row.count;
}

function formatUsageValue(
  row: { count: number; total_cost: number; avg_cost: number; share: number },
  view: UsageView,
): string {
  if (view === "cost") return `$${row.total_cost.toFixed(4)} total`;
  if (view === "avg") return `$${row.avg_cost.toFixed(4)} avg`;
  return `${row.count.toLocaleString()} requests \u00B7 ${row.share.toFixed(1)}%`;
}
