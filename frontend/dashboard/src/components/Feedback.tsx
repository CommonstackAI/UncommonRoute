import { useCallback, useEffect, useState } from "react";
import {
  fetchRecent,
  submitFeedback,
  type RecentRequest,
  type FeedbackResult,
} from "../api";

const TIER_COLOR: Record<string, string> = {
  SIMPLE: "text-n-success",
  MEDIUM: "text-n-warning",
  COMPLEX: "text-n-accent",
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
  if (result.action === "updated") return `${result.from_tier} \u2192 ${result.to_tier}`;
  if (result.action === "reinforced" || result.action === "no_change") return "confirmed";
  if (result.action === "rate_limited") return "rate limited";
  return result.action;
}

function feedbackTone(result: FeedbackResult): string {
  if (result.action === "updated") return "text-n-interactive";
  if (result.ok) return "text-n-success";
  if (result.action === "rate_limited") return "text-n-warning";
  return "text-n-secondary";
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
          <h1 className="text-xl font-semibold tracking-tight text-n-display">Feedback</h1>
          {pendingCount > 0 && (
            <span className="rounded-pill border border-n-warning px-2 py-0.5 font-mono text-[11px] uppercase tracking-wider text-n-warning">
              {pendingCount} AWAITING
            </span>
          )}
        </div>
        <p className="text-[13px] text-n-secondary">
          Rate routing decisions to improve the classifier. All training happens locally.
        </p>
      </div>

      <div className="rounded-card border border-n-border bg-n-surface overflow-hidden">
        <table className="w-full">
          <thead>
            <tr className="border-b border-n-border">
              <th className="label px-6 py-4 text-left w-24">TIME</th>
              <th className="label px-6 py-4 text-left w-24">MODE</th>
              <th className="label px-6 py-4 text-left">PROMPT</th>
              <th className="label px-6 py-4 text-left w-24">TIER</th>
              <th className="label px-6 py-4 text-left">MODEL</th>
              <th className="label px-6 py-4 text-right w-24">COST</th>
              <th className="label px-6 py-4 text-right pl-4 pr-8 w-[280px]"></th>
            </tr>
          </thead>
          <tbody>
            {visibleRequests.length === 0 ? (
              <tr><td colSpan={7} className="py-16 text-center font-mono text-[14px] text-n-disabled">No pending or rated requests yet.</td></tr>
            ) : (
              visibleRequests.map((r) => {
                const fb = submitted[r.request_id] ?? storedFeedback(r);
                const isBusy = busy === r.request_id;
                const displayTier = normalizeTier(r.tier);

                return (
                  <tr key={r.request_id} className="border-b border-n-border last:border-0 transition-colors hover:bg-n-raised">
                    <td className="px-6 py-4 font-mono text-[12px] text-n-secondary">{fmtTime(r.timestamp)}</td>
                    <td className="px-6 py-4 font-mono text-[12px] text-n-secondary">{r.mode || "auto"}</td>
                    <td className="px-6 py-4">
                      <div className="max-w-[300px] truncate text-[13px] text-n-primary" title={r.prompt_preview}>
                        {r.prompt_preview || "\u2014"}
                      </div>
                      {(r.constraint_tags?.length || r.hint_tags?.length || (r.answer_depth && r.answer_depth !== "standard")) ? (
                        <div className="mt-2 flex flex-wrap gap-1.5">
                          {r.answer_depth && r.answer_depth !== "standard" && (
                            <span className="rounded-pill border border-n-border-vis px-2 py-0.5 font-mono text-[11px] uppercase tracking-wider text-n-secondary">
                              {r.answer_depth.replace(/[-_]/g, " ")}
                            </span>
                          )}
                          {r.constraint_tags?.map((tag) => (
                            <span key={`${r.request_id}-${tag}`} className="rounded-pill border border-n-border-vis px-2 py-0.5 font-mono text-[11px] uppercase tracking-wider text-n-secondary">
                              {tag.replace(/[-_]/g, " ")}
                            </span>
                          ))}
                          {r.hint_tags?.map((tag) => (
                            <span key={`${r.request_id}-${tag}`} className="rounded-pill border border-n-border-vis px-2 py-0.5 font-mono text-[11px] uppercase tracking-wider text-n-secondary">
                              {tag.replace(/[-_]/g, " ")}
                            </span>
                          ))}
                        </div>
                      ) : null}
                    </td>
                    <td className={`px-6 py-4 font-mono text-[12px] font-medium ${TIER_COLOR[displayTier] ?? "text-n-secondary"}`}>
                      {displayTier}
                    </td>
                    <td className="px-6 py-4 font-mono text-[12px] text-n-secondary">{r.model.split("/").pop()}</td>
                    <td className="px-6 py-4 text-right font-mono text-[12px] text-n-secondary">${r.cost.toFixed(4)}</td>
                    <td className="py-4 pl-4 pr-8 text-right">
                      {fb ? (
                        <span
                          className={`font-mono text-[12px] font-medium ${feedbackTone(fb)}`}
                          title={fb.reason}
                        >
                          {feedbackLabel(fb)}
                        </span>
                      ) : (
                        <div className="flex justify-end gap-2">
                          <button
                            disabled={isBusy}
                            onClick={() => handle(r.request_id, "strong")}
                            className="rounded-pill border border-n-border-vis px-3 py-1.5 font-mono text-[12px] uppercase tracking-wider text-n-secondary transition-colors hover:border-n-primary hover:text-n-primary disabled:opacity-40"
                          >
                            CHEAPER
                          </button>
                          <button
                            disabled={isBusy}
                            onClick={() => handle(r.request_id, "ok")}
                            className="rounded-pill bg-n-display px-3 py-1.5 font-mono text-[12px] uppercase tracking-wider text-n-black transition-colors hover:bg-n-primary disabled:opacity-40"
                          >
                            RIGHT
                          </button>
                          <button
                            disabled={isBusy}
                            onClick={() => handle(r.request_id, "weak")}
                            className="rounded-pill border border-n-border-vis px-3 py-1.5 font-mono text-[12px] uppercase tracking-wider text-n-secondary transition-colors hover:border-n-primary hover:text-n-primary disabled:opacity-40"
                          >
                            STRONGER
                          </button>
                        </div>
                      )}
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
