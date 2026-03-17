import { useCallback, useEffect, useState } from "react";
import { motion } from "framer-motion";
import {
  fetchRecent,
  submitFeedback,
  type RecentRequest,
  type FeedbackResult,
} from "../api";
import { Card } from "./ui/Card";
import { StaggerContainer, StaggerItem } from "./ui/Stagger";

const TIER_LABEL: Record<string, string> = {
  SIMPLE: "text-emerald-600",
  MEDIUM: "text-sky-600",
  COMPLEX: "text-orange-600",
};

function fmtTime(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function normalizeTier(tier: string): string {
  return tier;
}

function storedFeedback(request: RecentRequest): FeedbackResult | null {
  if (!request.feedback_action) return null;
  if (request.feedback_action === "expired") return null;
  return {
    ok: request.feedback_ok,
    action: request.feedback_action,
    from_tier: normalizeTier(request.feedback_from_tier),
    to_tier: normalizeTier(request.feedback_to_tier),
    reason: request.feedback_reason || undefined,
    total_updates: 0,
  };
}

function feedbackLabel(result: FeedbackResult): string {
  if (result.action === "updated") return `${result.from_tier} → ${result.to_tier}`;
  if (result.action === "reinforced" || result.action === "no_change") return "confirmed";
  if (result.action === "rate_limited") return "rate limited";
  return result.action;
}

function feedbackTone(result: FeedbackResult): string {
  if (result.action === "updated") return "text-sky-600";
  if (result.ok) return "text-emerald-600";
  if (result.action === "rate_limited") return "text-amber-600";
  return "text-[#6B7280]";
}

export default function Feedback() {
  const [requests, setRequests] = useState<RecentRequest[]>([]);
  const [submitted, setSubmitted] = useState<Record<string, FeedbackResult>>({});
  const [busy, setBusy] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const data = await fetchRecent();
    if (data) {
      setRequests(data);
      setSubmitted((prev) => {
        const next = { ...prev };
        for (const request of data) {
          const persisted = storedFeedback(request);
          if (persisted) next[request.request_id] = persisted;
        }
        return next;
      });
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 5000);
    return () => clearInterval(id);
  }, [refresh]);

  async function handle(requestId: string, signal: "ok" | "weak" | "strong") {
    setBusy(requestId);
    const result = await submitFeedback(requestId, signal);
    if (result && result.action !== "expired") {
      setSubmitted((prev) => ({ ...prev, [requestId]: result }));
    }
    await refresh();
    setBusy(null);
  }

  const visibleRequests = requests.filter((r) => r.feedback_pending || Boolean(submitted[r.request_id] ?? storedFeedback(r)));
  const pendingCount = visibleRequests.filter((r) => r.feedback_pending && !(submitted[r.request_id] ?? storedFeedback(r))).length;

  return (
    <div className="space-y-6">
      <div>
        <div className="flex items-baseline gap-3 mb-1">
          <h1 className="text-xl font-semibold tracking-tight text-[#111827]">Feedback</h1>
          {pendingCount > 0 && (
            <span className="text-[13px] font-medium text-indigo-600 bg-indigo-50 px-2 py-0.5 rounded-md">{pendingCount} awaiting</span>
          )}
        </div>
        <p className="text-[13px] font-medium text-[#6B7280]">
          Rate routing decisions to improve the classifier. All training happens locally.
        </p>
      </div>

      <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}>
        <Card>
          <table className="w-full">
            <thead>
              <tr className="border-b border-black/[0.04]">
                <th className="text-left text-[11px] font-medium text-[#9CA3AF] uppercase tracking-wider py-4 px-6 w-24">Time</th>
                <th className="text-left text-[11px] font-medium text-[#9CA3AF] uppercase tracking-wider py-4 px-6 w-24">Mode</th>
                <th className="text-left text-[11px] font-medium text-[#9CA3AF] uppercase tracking-wider py-4 px-6">Prompt</th>
                <th className="text-left text-[11px] font-medium text-[#9CA3AF] uppercase tracking-wider py-4 px-6 w-24">Tier</th>
                <th className="text-left text-[11px] font-medium text-[#9CA3AF] uppercase tracking-wider py-4 px-6">Model</th>
                <th className="text-right text-[11px] font-medium text-[#9CA3AF] uppercase tracking-wider py-4 px-6 w-24">Cost</th>
                <th className="text-right text-[11px] font-medium text-[#9CA3AF] uppercase tracking-wider py-4 pl-4 pr-8 w-[280px]"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-black/[0.04]">
              {visibleRequests.length === 0 ? (
                <tr><td colSpan={7} className="py-16 text-center text-[14px] font-medium text-[#9CA3AF]">No pending or rated requests yet.</td></tr>
              ) : (
                <StaggerContainer as="tbody" className="contents">
                  {visibleRequests.map((r) => {
                    const fb = submitted[r.request_id] ?? storedFeedback(r);
                    const isBusy = busy === r.request_id;
                    const displayTier = normalizeTier(r.tier);

                    return (
                      <StaggerItem as="tr" key={r.request_id} className="hover:bg-gray-50 transition-colors group">
                        <td className="text-[12px] font-mono text-[#6B7280] group-hover:text-[#4B5563] transition-colors py-4 px-6">{fmtTime(r.timestamp)}</td>
                        <td className="text-[12px] font-mono text-[#6B7280] group-hover:text-[#4B5563] transition-colors py-4 px-6">{r.mode || "auto"}</td>
                        <td className="py-4 px-6">
                          <div className="max-w-[300px] truncate text-[13px] font-medium text-[#4B5563] group-hover:text-[#111827] transition-colors" title={r.prompt_preview}>
                            {r.prompt_preview || "—"}
                          </div>
                          {(r.constraint_tags?.length || r.hint_tags?.length || (r.answer_depth && r.answer_depth !== "standard")) ? (
                            <div className="mt-2 flex flex-wrap gap-1.5">
                              {r.answer_depth && r.answer_depth !== "standard" && (
                                <span className="inline-flex items-center rounded-full bg-gray-50 px-2 py-0.5 text-[11px] font-medium text-[#6B7280] ring-1 ring-black/[0.04]">
                                  {r.answer_depth.replace(/[-_]/g, " ")}
                                </span>
                              )}
                              {r.constraint_tags?.map((tag) => (
                                <span key={`${r.request_id}-${tag}`} className="inline-flex items-center rounded-full bg-gray-50 px-2 py-0.5 text-[11px] font-medium text-[#6B7280] ring-1 ring-black/[0.04]">
                                  {tag.replace(/[-_]/g, " ")}
                                </span>
                              ))}
                              {r.hint_tags?.map((tag) => (
                                <span key={`${r.request_id}-${tag}`} className="inline-flex items-center rounded-full bg-gray-50 px-2 py-0.5 text-[11px] font-medium text-[#6B7280] ring-1 ring-black/[0.04]">
                                  {tag.replace(/[-_]/g, " ")}
                                </span>
                              ))}
                            </div>
                          ) : null}
                        </td>
                        <td className={`text-[12px] font-mono font-medium py-4 px-6 ${TIER_LABEL[displayTier] ?? "text-[#6B7280]"}`}>
                          {displayTier}
                        </td>
                        <td className="font-mono text-[12px] text-[#6B7280] group-hover:text-[#111827] transition-colors py-4 px-6">{r.model.split("/").pop()}</td>
                        <td className="text-right font-mono text-[12px] text-[#6B7280] group-hover:text-[#4B5563] transition-colors py-4 px-6">${r.cost.toFixed(4)}</td>
                        <td className="text-right py-4 pl-4 pr-8">
                          {fb ? (
                            <motion.span 
                              initial={{ opacity: 0, scale: 0.9 }}
                              animate={{ opacity: 1, scale: 1 }}
                              className={`text-[12px] font-mono font-medium ${feedbackTone(fb)}`} 
                              title={fb.reason}
                            >
                              {feedbackLabel(fb)}
                            </motion.span>
                          ) : (
                            <div className="flex justify-end gap-2">
                              <motion.button
                                whileHover={{ scale: 1.02 }}
                                whileTap={{ scale: 0.98 }}
                                disabled={isBusy}
                                onClick={() => handle(r.request_id, "strong")}
                                className="px-3 py-1.5 rounded-lg text-[12px] font-medium bg-white border border-black/[0.06] shadow-sm text-[#6B7280] hover:text-[#111827] hover:bg-gray-50 transition-colors disabled:opacity-40"
                              >
                                Cheaper
                              </motion.button>
                              <motion.button
                                whileHover={{ scale: 1.02 }}
                                whileTap={{ scale: 0.98 }}
                                disabled={isBusy}
                                onClick={() => handle(r.request_id, "ok")}
                                className="px-3 py-1.5 rounded-lg text-[12px] font-medium bg-[#111827] shadow-sm text-white hover:bg-black transition-colors disabled:opacity-40"
                              >
                                Right
                              </motion.button>
                              <motion.button
                                whileHover={{ scale: 1.02 }}
                                whileTap={{ scale: 0.98 }}
                                disabled={isBusy}
                                onClick={() => handle(r.request_id, "weak")}
                                className="px-3 py-1.5 rounded-lg text-[12px] font-medium bg-white border border-black/[0.06] shadow-sm text-[#6B7280] hover:text-[#111827] hover:bg-gray-50 transition-colors disabled:opacity-40"
                              >
                                Stronger
                              </motion.button>
                            </div>
                          )}
                        </td>
                      </StaggerItem>
                    );
                  })}
                </StaggerContainer>
              )}
            </tbody>
          </table>
        </Card>
      </motion.div>
    </div>
  );
}
