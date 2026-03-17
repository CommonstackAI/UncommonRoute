import { type ReactNode, useMemo, useState } from "react";
import { motion } from "framer-motion";
import type { Stats } from "../api";
import { Card } from "./ui/Card";
import { AnimatedNumber } from "./ui/AnimatedNumber";

type UsageView = "requests" | "cost" | "avg";

interface Props {
  stats: Stats | null;
}

export default function Activity({ stats }: Props) {
  const [usageView, setUsageView] = useState<UsageView>("requests");

  if (!stats || stats.total_requests === 0) {
    return <div className="flex items-center justify-center py-20 text-[14px] text-[#9CA3AF]">No activity recorded yet.</div>;
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
    {
      label: "Simple",
      count: simpleCount,
      totalCost: simpleCost,
      bg: "bg-sky-50/80",
      ring: "ring-sky-500/10",
      dot: "bg-sky-500",
      bar: "bg-sky-500",
      text: "text-sky-600",
    },
    {
      label: "Medium",
      count: mediumCount,
      totalCost: mediumCost,
      bg: "bg-amber-50/80",
      ring: "ring-amber-500/10",
      dot: "bg-amber-500",
      bar: "bg-amber-500",
      text: "text-amber-600",
    },
    {
      label: "Complex",
      count: complexCount,
      totalCost: complexCost,
      bg: "bg-rose-50/80",
      ring: "ring-rose-500/10",
      dot: "bg-rose-500",
      bar: "bg-rose-500",
      text: "text-rose-600",
    },
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

  const models = useMemo(() => {
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
  }, [stats.by_model, stats.total_requests, usageView]);

  const maxUsageValue = Math.max(...models.map((row) => getUsageValue(row, usageView)), 0.000001);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-[#111827]">Activity</h1>
        <p className="mt-1 text-[13px] font-medium text-[#6B7280]">
          {formatTimeRange(stats.time_range_s)} · {stats.total_requests.toLocaleString()} routed requests
        </p>
      </div>

      <div className="grid grid-cols-12 gap-3">
        <motion.div className="col-span-3" initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }}>
          <OverviewCard
            label="Actual Spend"
            value={
              <>
                $<AnimatedNumber value={stats.total_actual_cost} format={(v) => v.toFixed(2)} />
              </>
            }
            meta={`$${stats.total_baseline_cost.toFixed(2)} baseline`}
          />
        </motion.div>
        <motion.div className="col-span-3" initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.03 }}>
          <OverviewCard
            label="Saved"
            value={
              <>
                <AnimatedNumber value={stats.total_savings_ratio * 100} format={(v) => v.toFixed(1)} />%
              </>
            }
            meta={`$${stats.total_savings_absolute.toFixed(2)} below baseline`}
            tone="success"
          />
        </motion.div>
        <motion.div className="col-span-3" initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.06 }}>
          <OverviewCard
            label="Avg Latency"
            value={
              <>
                <AnimatedNumber value={stats.avg_latency_ms} format={(v) => v.toFixed(1)} />ms
              </>
            }
            meta={`${stats.total_requests.toLocaleString()} routed turns`}
          />
        </motion.div>
        <motion.div className="col-span-3" initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.09 }}>
          <OverviewCard
            label="Optimization"
            value={
              <>
                <AnimatedNumber value={stats.avg_input_reduction_ratio * 100} format={(v) => v.toFixed(1)} />%
              </>
            }
            meta={`${(stats.avg_cache_hit_ratio * 100).toFixed(1)}% cache hit · $${stats.total_compaction_savings.toFixed(2)} compaction`}
          />
        </motion.div>
      </div>

      <div className="grid grid-cols-12 gap-5">
        <motion.div className="col-span-7" initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.12 }}>
          <Card className="flex h-full flex-col p-6">
            <div>
              <div className="flex items-center justify-between mb-5">
                <div className="text-[12px] font-medium text-[#9CA3AF] uppercase tracking-wider">Request Complexity</div>
                <div className="text-[11px] font-medium text-[#9CA3AF]">
                  {classifiedCount} classified · {passthroughCount} passthrough
                </div>
              </div>

              <div className="h-2 rounded-full bg-gray-100 overflow-hidden flex">
                {tierSegments.map((bucket) => (
                  <motion.div
                    key={bucket.label}
                    initial={{ width: 0 }}
                    animate={{ width: `${bucket.pct}%` }}
                    transition={{ duration: 0.8, ease: "easeOut" }}
                    className={bucket.bar}
                  />
                ))}
              </div>

              <div className="mt-5 grid grid-cols-3 gap-3">
                {tierSegments.map((bucket) => (
                  <div
                    key={bucket.label}
                    className={`rounded-2xl ${bucket.bg} ring-1 ${bucket.ring} px-4 py-4`}
                  >
                    <div className="flex items-center justify-between">
                      <span className={`h-2 w-2 rounded-full ${bucket.dot}`} />
                      <span className="text-[11px] font-medium text-[#9CA3AF]">{bucket.pct.toFixed(0)}%</span>
                    </div>
                    <div className="mt-4 text-2xl font-semibold tracking-tight text-[#111827]">
                      <AnimatedNumber value={bucket.count} />
                    </div>
                    <div className={`mt-1 text-[12px] font-medium ${bucket.text}`}>{bucket.label}</div>
                    <div className="mt-3 text-[11px] font-medium text-[#6B7280]">
                      Spent ${bucket.totalCost.toFixed(2)}
                    </div>
                  </div>
                ))}
              </div>
            </div>

            <div className="mt-4 grid grid-cols-3 gap-2">
              <MiniMetric
                label="Dominant Band"
                value={`${dominantBucket.label} ${dominantBucket.pct.toFixed(0)}%`}
              />
              <MiniMetric
                label="Complex Share"
                value={`${complexShare.toFixed(0)}%`}
              />
              <MiniMetric
                label="Passthrough"
                value={passthroughCount.toLocaleString()}
              />
            </div>
          </Card>
        </motion.div>

        <motion.div className="col-span-5" initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.16 }}>
          <Card className="p-6 h-full">
            <div className="flex items-center justify-between mb-5">
              <div className="text-[12px] font-medium text-[#9CA3AF] uppercase tracking-wider">By Mode</div>
              <div className="text-[11px] font-medium text-[#9CA3AF]">{modeTiles.length} active modes</div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              {modeTiles.map((tile) => (
                <div
                  key={tile.mode}
                  className={`rounded-2xl ${tile.bg} ring-1 ${tile.ring} px-4 py-4`}
                >
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span className={`h-2 w-2 rounded-full ${tile.dot}`} />
                      <span className="text-[12px] font-medium text-[#6B7280] capitalize">{tile.mode}</span>
                    </div>
                    <span className="text-[11px] font-medium text-[#9CA3AF]">{tile.pct.toFixed(0)}%</span>
                  </div>
                  <div className="mt-3 text-2xl font-semibold tracking-tight text-[#111827]">
                    <AnimatedNumber value={tile.count} />
                  </div>
                  <div className={`mt-1 text-[11px] font-medium ${tile.text}`}>{tile.description}</div>
                  <div className="mt-3 h-1.5 rounded-full bg-white/80 overflow-hidden">
                    <motion.div
                      initial={{ width: 0 }}
                      animate={{ width: `${tile.pct}%` }}
                      transition={{ duration: 0.75, ease: "easeOut" }}
                      className={`h-full rounded-full ${tile.bar}`}
                    />
                  </div>
                </div>
              ))}
            </div>
          </Card>
        </motion.div>

        <motion.div className="col-span-12" initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.2 }}>
          <Card>
            <div className="px-6 py-4 border-b border-black/[0.04] flex items-center justify-between">
              <span className="text-[12px] font-medium text-[#9CA3AF] uppercase tracking-wider">Model Usage</span>
              <div className="flex items-center gap-1 rounded-xl bg-gray-50/90 ring-1 ring-black/[0.04] p-1">
                {(["requests", "cost", "avg"] as UsageView[]).map((view) => {
                  const active = usageView === view;
                  return (
                    <button
                      key={view}
                      onClick={() => setUsageView(view)}
                      className={`rounded-lg px-3 py-1.5 text-[12px] font-medium transition-colors ${
                        active
                          ? "bg-white text-[#111827] shadow-sm ring-1 ring-black/[0.04]"
                          : "text-[#6B7280] hover:text-[#111827]"
                      }`}
                    >
                      {view === "requests" ? "Requests" : view === "cost" ? "Cost" : "Avg cost"}
                    </button>
                  );
                })}
              </div>
            </div>
            <table className="w-full">
              <thead>
                <tr className="border-b border-black/[0.04]">
                  <th className="text-left text-[11px] font-medium text-[#9CA3AF] uppercase tracking-wider py-3 px-6">Model</th>
                  <th className="text-right text-[11px] font-medium text-[#9CA3AF] uppercase tracking-wider py-3 px-6">Requests</th>
                  <th className="text-right text-[11px] font-medium text-[#9CA3AF] uppercase tracking-wider py-3 px-6">Share</th>
                  <th className="text-right text-[11px] font-medium text-[#9CA3AF] uppercase tracking-wider py-3 px-6">Total Cost</th>
                  <th className="text-right text-[11px] font-medium text-[#9CA3AF] uppercase tracking-wider py-3 px-6">Avg / Req</th>
                </tr>
              </thead>
              <tbody>
                {models.map((row) => {
                  const focusValue = getUsageValue(row, usageView);
                  const focusWidth = Math.max((focusValue / maxUsageValue) * 100, 3);
                  return (
                    <tr key={row.name} className="border-b border-black/[0.03] last:border-0 hover:bg-gray-50 transition-colors group">
                      <td className="py-3.5 px-6">
                        <div className="flex items-center gap-3">
                          <div className="w-24 h-1.5 rounded-full bg-gray-100 overflow-hidden">
                            <motion.div
                              initial={{ width: 0 }}
                              animate={{ width: `${focusWidth}%` }}
                              transition={{ duration: 0.7, ease: "easeOut" }}
                              className={`h-full rounded-full ${
                                usageView === "requests"
                                  ? "bg-indigo-500"
                                  : usageView === "cost"
                                    ? "bg-rose-500"
                                    : "bg-amber-500"
                              }`}
                            />
                          </div>
                          <div>
                            <div className="text-[13px] font-medium text-[#4B5563] group-hover:text-[#111827] transition-colors">
                              {row.name.split("/").pop()}
                            </div>
                            <div className="text-[11px] font-medium text-[#9CA3AF]">
                              {formatUsageValue(row, usageView)}
                            </div>
                          </div>
                        </div>
                      </td>
                      <td className="py-3.5 px-6 text-[13px] font-mono text-[#6B7280] text-right">{row.count.toLocaleString()}</td>
                      <td className="py-3.5 px-6 text-[13px] font-mono text-[#6B7280] text-right">{row.share.toFixed(1)}%</td>
                      <td className="py-3.5 px-6 text-[13px] font-mono text-[#6B7280] text-right">${row.total_cost.toFixed(4)}</td>
                      <td className="py-3.5 px-6 text-[13px] font-mono text-[#6B7280] text-right">${row.avg_cost.toFixed(4)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </Card>
        </motion.div>
      </div>
    </div>
  );
}

function MiniMetric({
  label,
  value,
  tone = "default",
}: {
  label: string;
  value: string;
  tone?: "default" | "success";
}) {
  return (
    <div className="rounded-2xl bg-gray-50/80 ring-1 ring-black/[0.03] px-4 py-3">
      <div className="text-[11px] uppercase tracking-wider text-[#9CA3AF]">{label}</div>
      <div className={`mt-1 text-[16px] font-semibold tracking-tight ${tone === "success" ? "text-emerald-600" : "text-[#111827]"}`}>
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
    <Card className="p-5 h-full">
      <div className="text-[12px] font-medium text-[#9CA3AF] uppercase tracking-wider">{label}</div>
      <div className={`mt-3 text-[34px] leading-none font-semibold tracking-tight ${tone === "success" ? "text-emerald-600" : "text-[#111827]"}`}>
        {value}
      </div>
      <div className="mt-2 text-[12px] font-medium text-[#6B7280]">{meta}</div>
    </Card>
  );
}

function getModeMeta(mode: string): {
  bg: string;
  ring: string;
  dot: string;
  bar: string;
  text: string;
  description: string;
} {
  switch (mode) {
    case "best":
      return {
        bg: "bg-violet-50/80",
        ring: "ring-violet-500/10",
        dot: "bg-violet-500",
        bar: "bg-violet-500",
        text: "text-violet-600",
        description: "highest quality",
      };
    case "fast":
      return {
        bg: "bg-sky-50/80",
        ring: "ring-sky-500/10",
        dot: "bg-sky-500",
        bar: "bg-sky-500",
        text: "text-sky-600",
        description: "lighter and faster",
      };
    case "passthrough":
      return {
        bg: "bg-gray-50/90",
        ring: "ring-black/[0.05]",
        dot: "bg-gray-400",
        bar: "bg-gray-400",
        text: "text-[#6B7280]",
        description: "explicit model",
      };
    case "auto":
    default:
      return {
        bg: "bg-indigo-50/80",
        ring: "ring-indigo-500/10",
        dot: "bg-indigo-500",
        bar: "bg-indigo-500",
        text: "text-indigo-600",
        description: "balanced default",
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
  return `${row.count.toLocaleString()} requests · ${row.share.toFixed(1)}%`;
}
