import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { type Health, type Stats, type RecentRequest, fetchRecent } from "../api";
import { AnimatedNumber } from "./ui/AnimatedNumber";
import { Card } from "./ui/Card";
import { Wallet, Zap, Cpu, ArrowDownRight } from "lucide-react";

interface Props {
  stats: Stats | null;
  health: Health | null;
}

export default function Home({ stats, health }: Props) {
  const [recent, setRecent] = useState<RecentRequest[]>([]);

  useEffect(() => {
    const load = async () => {
      const data = await fetchRecent(5);
      if (data) setRecent(data);
    };
    load();
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, []);

  const totalRequests = stats?.total_requests ?? 0;
  const totalSaved = stats?.total_savings_absolute ?? 0;
  const savingsRatio = stats?.total_savings_ratio ?? 0;
  const avgConfidence = (stats?.avg_confidence ?? 0) * 100;
  const avgSavings = (stats?.avg_savings ?? 0) * 100;
  const modelCount =
    health?.model_mapper?.upstream_models ??
    health?.model_mapper?.pool_size ??
    0;
  const actualCost = stats?.total_actual_cost ?? 0;
  const baselineCost = stats?.total_baseline_cost ?? 0;
  const cacheSaved = stats?.total_cache_savings ?? 0;
  const compactionSaved = stats?.total_compaction_savings ?? 0;
  const unresolvedCount = health?.model_mapper?.unresolved?.length ?? 0;
  const resolvedCount = Math.max(modelCount - unresolvedCount, 0);
  const mapperProvider = health?.model_mapper?.provider ?? "upstream";
  const mapperMode = health?.model_mapper?.is_gateway ? "Gateway" : "Direct";
  const discoveryMode = health?.model_mapper?.discovered ? "Live catalog" : "Static catalog";
  const timeRangeLabel = formatTimeRange(stats?.time_range_s ?? 0);

  const models = Object.entries(stats?.by_model ?? {})
    .map(([name, data]) => ({ name, count: data.count, cost: data.total_cost }))
    .sort((a, b) => b.count - a.count)
    .slice(0, 6);

  const modeEntries = Object.entries(stats?.by_mode ?? {})
    .sort((a, b) => b[1] - a[1])
    .map(([mode, count]) => ({
      mode,
      count,
      pct: totalRequests > 0 ? (count / totalRequests) * 100 : 0,
      ...getModeMeta(mode),
    }));

  const maxModelCount = models.length > 0 ? models[0].count : 1;

  const complexityBuckets = [
    {
      label: "Simple",
      val: stats?.by_tier?.SIMPLE?.count ?? 0,
      grad: "from-sky-400 to-blue-500",
    },
    {
      label: "Medium",
      val: stats?.by_tier?.MEDIUM?.count ?? 0,
      grad: "from-amber-400 to-orange-500",
    },
    {
      label: "Complex",
      val: stats?.by_tier?.COMPLEX?.count ?? 0,
      grad: "from-rose-400 to-pink-500",
    },
  ];
  const totalDist = complexityBuckets.reduce((sum, bucket) => sum + bucket.val, 0) || 1;

  const getReason = (r: RecentRequest) => {
    if (r.complexity < 0.33) return "Simple, cheapest pick";
    if (r.complexity < 0.67) return "Balanced";
    return "Complex, best quality";
  };

  if (totalRequests === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-32 text-center">
        <div className="h-16 w-16 mb-6 rounded-2xl bg-white shadow-[0_2px_8px_-2px_rgba(0,0,0,0.05),0_1px_2px_rgba(0,0,0,0.02)] ring-1 ring-black/[0.04] flex items-center justify-center">
          <Zap className="w-8 h-8 text-indigo-500" />
        </div>
        <h2 className="text-2xl font-semibold tracking-tight text-[#111827] mb-2">Ready to route</h2>
        <p className="text-[#6B7280] max-w-md mb-10 text-[14px]">Send a request to see routing in action.</p>
        <div className="bg-white ring-1 ring-black/[0.04] shadow-sm rounded-2xl p-5 text-left w-full max-w-lg">
          <pre className="text-[12px] text-[#4B5563] font-mono overflow-x-auto leading-relaxed">
            {`curl localhost:8403/v1/chat/completions \\
  -H "Content-Type: application/json" \\
  -d '{"model":"uncommon-route/auto",
       "messages":[{"role":"user","content":"hello"}]}'`}
          </pre>
        </div>
      </div>
    );
  }

  return (
    <div className="grid grid-cols-12 gap-5 auto-rows-min">

      {/* Savings — Premium White Card */}
      <motion.div className="col-span-6" initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0 }}>
        <Card className="h-full p-6">
          <div>
            <div className="flex items-center justify-between mb-4">
              <div className="text-[13px] font-medium text-[#6B7280]">Total Saved</div>
              <div className="h-8 w-8 rounded-full bg-emerald-50 flex items-center justify-center text-emerald-600">
                <Wallet className="w-4 h-4" />
              </div>
            </div>
            <div className="text-[40px] leading-none font-semibold tracking-tight text-[#111827]">
              $<AnimatedNumber value={totalSaved} format={(v) => v.toFixed(2)} />
            </div>
            <div className="text-[13px] font-medium text-emerald-600 mt-3 flex items-center gap-1">
              <ArrowDownRight className="w-4 h-4" />
              <AnimatedNumber value={savingsRatio * 100} format={(v) => v.toFixed(1)} />% less than baseline
            </div>
          </div>
          <div className="mt-6 grid grid-cols-3 gap-3">
            <SummaryTile label="Baseline" value={`$${baselineCost.toFixed(2)}`} />
            <SummaryTile label="Actual" value={`$${actualCost.toFixed(2)}`} />
            <SummaryTile
              label="Efficiency"
              value={baselineCost > 0 ? `${((actualCost / baselineCost) * 100).toFixed(1)}%` : "n/a"}
              tone="success"
            />
          </div>
          <div className="mt-4 grid grid-cols-3 gap-2">
            <PillMetric label="Cache Saved" value={`$${cacheSaved.toFixed(2)}`} />
            <PillMetric label="Compaction" value={`$${compactionSaved.toFixed(2)}`} />
            <PillMetric label="Net Saved" value={`$${totalSaved.toFixed(2)}`} tone="success" />
          </div>
        </Card>
      </motion.div>

      {/* Requests */}
      <motion.div className="col-span-3" initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.05 }}>
        <Card className="p-6 h-full flex flex-col justify-between">
          <div className="flex items-center justify-between mb-4">
            <div className="text-[13px] font-medium text-[#6B7280]">Requests Routed</div>
            <div className="h-8 w-8 rounded-full bg-indigo-50 flex items-center justify-center text-indigo-600">
              <Zap className="w-4 h-4" />
            </div>
          </div>
          <div>
            <div className="text-4xl font-semibold tracking-tight text-[#111827]">
              <AnimatedNumber value={totalRequests} format={(v) => Math.round(v).toLocaleString()} />
            </div>
            <div className="mt-2 text-[12px] font-medium text-[#6B7280]">
              {timeRangeLabel}
            </div>
          </div>
          <div className="mt-6 grid grid-cols-2 gap-3">
            <SummaryTile label="Avg confidence" value={`${avgConfidence.toFixed(1)}%`} compact />
            <SummaryTile label="Avg savings" value={`${avgSavings.toFixed(1)}%`} compact />
          </div>
        </Card>
      </motion.div>

      {/* Models */}
      <motion.div className="col-span-3" initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.1 }}>
        <Card className="p-6 h-full flex flex-col justify-between">
          <div className="flex items-center justify-between mb-4">
            <div className="text-[13px] font-medium text-[#6B7280]">Models Active</div>
            <div className="h-8 w-8 rounded-full bg-orange-50 flex items-center justify-center text-orange-600">
              <Cpu className="w-4 h-4" />
            </div>
          </div>
          <div>
            <div className="text-4xl font-semibold tracking-tight text-[#111827]">
              <AnimatedNumber value={modelCount} format={(v) => Math.round(v).toString()} />
            </div>
            <div className="mt-2 text-[12px] font-medium text-[#6B7280]">
              {mapperProvider} · {mapperMode} · {discoveryMode}
            </div>
          </div>
          <div className="mt-6 grid grid-cols-2 gap-3">
            <SummaryTile label="Mapped" value={`${resolvedCount}`} compact />
            <SummaryTile
              label="Unresolved"
              value={`${unresolvedCount}`}
              compact
              tone={unresolvedCount > 0 ? "warning" : "success"}
            />
          </div>
        </Card>
      </motion.div>

      {/* Complexity */}
      <motion.div className="col-span-12" initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.12 }}>
        <Card className="p-6">
          <div className="text-[12px] font-medium text-[#9CA3AF] uppercase tracking-wider mb-5">Complexity Distribution</div>
          <div className="grid grid-cols-3 gap-4">
            {complexityBuckets.map((d) => (
              <div key={d.label} className="flex-1">
                <div className="h-1.5 rounded-full bg-gray-100 overflow-hidden mb-3">
                  <motion.div
                    initial={{ width: 0 }}
                    animate={{ width: `${(d.val / totalDist) * 100}%` }}
                    transition={{ duration: 1, ease: "easeOut" }}
                    className={`h-full rounded-full bg-gradient-to-r ${d.grad}`}
                  />
                </div>
                <div className="flex items-baseline justify-between">
                  <div className="text-[13px] font-medium text-[#6B7280]">{d.label}</div>
                  <div className="text-lg font-semibold text-[#111827] tracking-tight">{d.val}</div>
                </div>
              </div>
            ))}
          </div>
        </Card>
      </motion.div>

      {/* Top Models */}
      <motion.div className="col-span-7" initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.15 }}>
        <Card className="p-6 h-full">
          <div className="text-[12px] font-medium text-[#9CA3AF] uppercase tracking-wider mb-5">Top Models</div>
          <div className="space-y-4">
            {models.map((m, i) => {
              const coreName = m.name.split("/").pop() || m.name;
              const pct = Math.max(2, (m.count / maxModelCount) * 100);
              const colors = [
                "bg-indigo-500",
                "bg-sky-500",
                "bg-emerald-500",
                "bg-amber-500",
                "bg-rose-500",
                "bg-violet-500",
              ];
              return (
                <div key={m.name} className="flex items-center gap-4 group">
                  <div className="w-36 truncate text-[13px] font-medium text-[#6B7280] group-hover:text-[#111827] transition-colors">{coreName}</div>
                  <div className="flex-1 h-1.5 bg-gray-100 rounded-full overflow-hidden">
                    <motion.div
                      initial={{ width: 0 }}
                      animate={{ width: `${pct}%` }}
                      transition={{ duration: 0.8, delay: 0.2 + i * 0.04, ease: [0.25, 0.46, 0.45, 0.94] }}
                      className={`h-full rounded-full ${colors[i % colors.length]}`}
                    />
                  </div>
                  <div className="w-10 text-right text-[13px] font-semibold text-[#111827]">{m.count}</div>
                </div>
              );
            })}
          </div>
        </Card>
      </motion.div>

      {/* Mode breakdown */}
      <motion.div className="col-span-5" initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.18 }}>
        <Card className="p-6 h-full">
          <div className="flex items-center justify-between mb-5">
            <div className="text-[12px] font-medium text-[#9CA3AF] uppercase tracking-wider">By Mode</div>
            <div className="text-[11px] font-medium text-[#9CA3AF]">{modeEntries.length} modes</div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            {modeEntries.map((item) => (
              <div
                key={item.mode}
                className={`rounded-2xl ${item.bg} ring-1 ${item.ring} px-4 py-4`}
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className={`h-2 w-2 rounded-full ${item.dot}`} />
                    <span className="text-[12px] font-medium text-[#6B7280] capitalize">{item.mode}</span>
                  </div>
                  <span className="text-[11px] font-medium text-[#9CA3AF]">
                    {item.pct.toFixed(0)}%
                  </span>
                </div>
                <div className="mt-3 flex items-end justify-between gap-3">
                  <div className="text-2xl font-semibold tracking-tight text-[#111827]">
                    <AnimatedNumber value={item.count} />
                  </div>
                  <div className={`text-[11px] font-medium ${item.text}`}>{item.description}</div>
                </div>
                <div className="mt-3 h-1.5 rounded-full bg-white/80 overflow-hidden">
                  <motion.div
                    initial={{ width: 0 }}
                    animate={{ width: `${item.pct}%` }}
                    transition={{ duration: 0.75, ease: "easeOut" }}
                    className={`h-full rounded-full ${item.bar}`}
                  />
                </div>
              </div>
            ))}
          </div>
        </Card>
      </motion.div>

      {/* Live Traffic */}
      <motion.div className="col-span-12" initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.22 }}>
        <Card>
          <div className="px-6 py-4 border-b border-black/[0.04] flex items-center justify-between">
            <span className="text-[12px] font-medium text-[#9CA3AF] uppercase tracking-wider">Live Traffic</span>
            <span className="text-[11px] font-medium text-[#9CA3AF]">{recent.length} latest requests</span>
          </div>
          {recent.map((r, i) => (
            <motion.div
              key={r.request_id || i}
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ delay: 0.25 + i * 0.03 }}
              className="px-6 py-4 border-b border-black/[0.03] last:border-0 hover:bg-gray-50 transition-colors"
            >
              <div className="flex gap-4">
                <div className="flex flex-col items-center shrink-0">
                  <span className={`mt-1 h-2.5 w-2.5 rounded-full ${getTierMeta(r.tier).dot}`} />
                  {i < recent.length - 1 && <span className="mt-2 h-full w-px bg-black/[0.05]" />}
                </div>

                <div className="min-w-0 flex-1">
                  <div className="flex items-start justify-between gap-4">
                    <div className="min-w-0">
                      <div
                        className="text-[13px] font-medium leading-5 text-[#111827] break-words"
                        title={r.prompt_preview}
                      >
                        {r.prompt_preview}
                      </div>
                      <div className="mt-2 flex flex-wrap items-center gap-2">
                        <FeedBadge label={r.mode || "auto"} />
                        <FeedBadge label={getDisplayTier(r.tier)} tone={getTierMeta(r.tier).badge} />
                        {r.answer_depth && r.answer_depth !== "standard" && (
                          <FeedBadge label={humanizeLabel(r.answer_depth)} subtle />
                        )}
                        {r.constraint_tags?.map((tag) => (
                          <FeedBadge key={`${r.request_id}-${tag}`} label={humanizeLabel(tag)} subtle />
                        ))}
                        {r.hint_tags?.map((tag) => (
                          <FeedBadge key={`${r.request_id}-${tag}`} label={humanizeLabel(tag)} subtle />
                        ))}
                        <FeedBadge label={getReason(r)} subtle />
                      </div>
                    </div>

                    <div className="shrink-0 text-right">
                      <div className="text-[12px] font-semibold text-[#111827]">${r.cost.toFixed(4)}</div>
                      <div className="mt-1 text-[11px] font-medium text-[#9CA3AF]">
                        {formatRelativeTime(r.timestamp)}
                      </div>
                    </div>
                  </div>

                  <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
                    <span className="inline-flex items-center rounded-full bg-white shadow-sm ring-1 ring-black/[0.06] px-2.5 py-1 text-[12px] font-medium text-[#111827]">
                      {r.model.split("/").pop()}
                    </span>
                    <div className="flex flex-wrap items-center gap-2 text-[11px] font-medium text-[#9CA3AF]">
                      <span>{humanizeLabel(r.method)}</span>
                      <span className="h-1 w-1 rounded-full bg-black/[0.12]" />
                      <span>{humanizeLabel(r.transport)}</span>
                    </div>
                  </div>
                </div>
              </div>
            </motion.div>
          ))}
          {recent.length === 0 && (
            <div className="px-6 py-8 text-center text-[13px] text-[#9CA3AF]">Waiting for requests...</div>
          )}
        </Card>
      </motion.div>
    </div>
  );
}

function SummaryTile({
  label,
  value,
  compact = false,
  tone = "default",
}: {
  label: string;
  value: string;
  compact?: boolean;
  tone?: "default" | "success" | "warning";
}) {
  const toneClass =
    tone === "success"
      ? "text-emerald-600"
      : tone === "warning"
        ? "text-amber-600"
        : "text-[#111827]";

  return (
    <div className="rounded-2xl bg-gray-50/80 ring-1 ring-black/[0.03] px-4 py-3">
      <div className="text-[11px] uppercase tracking-wider text-[#9CA3AF]">{label}</div>
      <div className={`mt-1 font-semibold tracking-tight ${compact ? "text-[17px]" : "text-[15px]"} ${toneClass}`}>
        {value}
      </div>
    </div>
  );
}

function PillMetric({
  label,
  value,
  tone = "default",
}: {
  label: string;
  value: string;
  tone?: "default" | "success";
}) {
  return (
    <div className="flex w-full items-center justify-between rounded-2xl bg-gray-50/90 ring-1 ring-black/[0.04] px-3 py-2">
      <span className="text-[11px] font-medium text-[#6B7280]">{label}</span>
      <span className={`text-[11px] font-semibold ${tone === "success" ? "text-emerald-600" : "text-[#111827]"}`}>
        {value}
      </span>
    </div>
  );
}

function formatTimeRange(seconds: number): string {
  if (seconds <= 0) return "No recent window";
  if (seconds >= 86400) return `${(seconds / 86400).toFixed(1)}d history`;
  if (seconds >= 3600) return `${Math.round(seconds / 3600)}h history`;
  if (seconds >= 60) return `${Math.round(seconds / 60)}m history`;
  return `${Math.round(seconds)}s history`;
}

function formatRelativeTime(timestamp: number): string {
  const delta = Math.max(0, Math.floor(Date.now() / 1000) - timestamp);
  if (delta < 60) return `${delta}s ago`;
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
  if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`;
  return `${Math.floor(delta / 86400)}d ago`;
}

function humanizeLabel(value: string): string {
  if (!value) return "unknown";
  return value.replace(/[-_]/g, " ");
}

function getTierMeta(tier: string): { dot: string; badge: "default" | "sky" | "amber" | "rose" | "violet" } {
  switch (tier) {
    case "SIMPLE":
      return { dot: "bg-sky-500", badge: "sky" };
    case "MEDIUM":
      return { dot: "bg-amber-500", badge: "amber" };
    case "COMPLEX":
      return { dot: "bg-rose-500", badge: "rose" };
    default:
      return { dot: "bg-gray-300", badge: "default" };
  }
}

function getDisplayTier(tier: string): string {
  return tier || "unknown";
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

function FeedBadge({
  label,
  tone = "default",
  subtle = false,
}: {
  label: string;
  tone?: "default" | "sky" | "amber" | "rose" | "violet";
  subtle?: boolean;
}) {
  const toneClass = subtle
    ? "bg-gray-50 text-[#6B7280] ring-black/[0.04]"
    : tone === "sky"
      ? "bg-sky-50 text-sky-600 ring-sky-500/10"
      : tone === "amber"
        ? "bg-amber-50 text-amber-600 ring-amber-500/10"
        : tone === "rose"
          ? "bg-rose-50 text-rose-600 ring-rose-500/10"
          : tone === "violet"
            ? "bg-violet-50 text-violet-600 ring-violet-500/10"
            : "bg-white text-[#6B7280] ring-black/[0.05]";

  return (
    <span className={`inline-flex items-center rounded-full px-2.5 py-1 text-[11px] font-medium ring-1 ${toneClass}`}>
      {label}
    </span>
  );
}
