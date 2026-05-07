import { useEffect, useMemo, useState } from "react";
import {
  submitFeedback,
  type RecentRequest,
  type FeedbackResult,
} from "../api";
import { useLiveData } from "../state/LiveDataContext";
import { useT } from "../i18n";
import type { Dictionary } from "../i18n/types";

const TIER_COLOR: Record<string, string> = {
  SIMPLE: "text-n-success",
  MEDIUM: "text-n-warning",
  COMPLEX: "text-n-accent",
};

const TIER_BORDER: Record<string, string> = {
  SIMPLE: "border-n-success",
  MEDIUM: "border-n-warning",
  COMPLEX: "border-n-accent",
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

function feedbackLabel(result: FeedbackResult, t: Dictionary): string {
  if (result.action === "updated") return `${t.common.tierLabel(result.from_tier)} \u2192 ${t.common.tierLabel(result.to_tier)}`;
  if (result.action === "reinforced" || result.action === "no_change") return t.common.confirmed;
  if (result.action === "rate_limited") return t.common.rateLimited;
  return result.action;
}

function feedbackTone(result: FeedbackResult): string {
  if (result.action === "updated") return "text-n-interactive";
  if (result.ok) return "text-n-success";
  if (result.action === "rate_limited") return "text-n-warning";
  return "text-n-secondary";
}

export default function Feedback() {
  const t = useT();
  const { recent } = useLiveData();
  const requests = useMemo(
    () =>
      recent.filter(
        (r): r is RecentRequest & { state: typeof r.state } =>
          Boolean(r.tier) && r.state === "completed",
      ) as unknown as RecentRequest[],
    [recent],
  );
  const [submitted, setSubmitted] = useState<Record<string, FeedbackResult>>({});
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    setSubmitted((prev) => {
      const next = { ...prev };
      for (const request of requests) {
        const persisted = storedFeedback(request);
        if (persisted) next[request.request_id] = persisted;
      }
      return next;
    });
  }, [requests]);

  async function handle(requestId: string, signal: "ok" | "weak" | "strong") {
    setBusy(requestId);
    const result = await submitFeedback(requestId, signal);
    if (result && result.action !== "expired") {
      setSubmitted((prev) => ({ ...prev, [requestId]: result }));
    }
    setBusy(null);
  }

  const visibleRequests = requests.filter((r) => r.feedback_pending || Boolean(submitted[r.request_id] ?? storedFeedback(r)));
  const pendingCount = visibleRequests.filter((r) => r.feedback_pending && !(submitted[r.request_id] ?? storedFeedback(r))).length;

  return (
    <div className="space-y-6 animate-fadeIn">
      <div>
        <div className="flex items-baseline gap-3 mb-1">
          <h1 className="font-display text-[36px] text-n-display tracking-tight">{t.feedback.title}</h1>
          {pendingCount > 0 && (
            <span className="rounded-pill border border-n-warning px-2 py-0.5 font-mono text-[11px] uppercase tracking-wider text-n-warning">
              {t.feedback.awaiting(pendingCount)}
            </span>
          )}
        </div>
        <p className="text-[13px] text-n-secondary">
          {t.feedback.subtitle}
        </p>
      </div>

      <div className="rounded-card border border-n-border bg-n-surface overflow-x-auto">
        <table className="w-full">
          <thead>
            <tr className="border-b border-n-border">
              <th className="label px-6 py-4 text-left w-24">{t.feedback.time}</th>
              <th className="label px-6 py-4 text-left">{t.feedback.request}</th>
              <th className="label px-6 py-4 text-left">{t.feedback.model}</th>
              <th className="label px-6 py-4 text-right w-24">{t.feedback.cost}</th>
              <th className="label px-6 py-4 text-right pl-4 pr-8 w-[320px]">{t.feedback.feedback}</th>
            </tr>
          </thead>
          <tbody>
            {visibleRequests.length === 0 ? (
              <tr><td colSpan={5} className="py-16 text-center font-mono text-[14px] text-n-disabled">{t.feedback.empty}</td></tr>
            ) : (
              visibleRequests.map((r) => {
                const fb = submitted[r.request_id] ?? storedFeedback(r);
                const isBusy = busy === r.request_id;
                const displayTier = normalizeTier(r.tier);

                return (
                  <tr key={r.request_id} className="border-b border-n-border last:border-0 row-hover hover:bg-n-raised">
                    <td className="px-6 py-4 font-mono text-[12px] text-n-secondary">{fmtTime(r.timestamp)}</td>
                    <td className="px-6 py-4">
                      <div className="max-w-[300px] truncate text-[13px] text-n-primary" title={r.prompt_preview}>
                        {r.prompt_preview || "\u2014"}
                      </div>
                      <div className="mt-2 flex flex-wrap gap-1.5">
                        <span className={`rounded-pill border px-2 py-0.5 font-mono text-[11px] font-medium uppercase tracking-wider ${TIER_BORDER[displayTier] ?? "border-n-border-vis"} ${TIER_COLOR[displayTier] ?? "text-n-secondary"}`}>
                          {t.common.tierLabel(displayTier)}
                        </span>
                        <span className="rounded-pill border border-n-border-vis px-2 py-0.5 font-mono text-[11px] uppercase tracking-wider text-n-secondary">
                          {t.tags.mode(r.mode || "auto")}
                        </span>
                        {r.answer_depth && r.answer_depth !== "standard" && (
                          <span className="rounded-pill border border-n-border-vis px-2 py-0.5 font-mono text-[11px] uppercase tracking-wider text-n-secondary">
                            {t.tags.answerDepth(r.answer_depth)}
                          </span>
                        )}
                        {r.constraint_tags?.map((tag) => (
                          <span key={`${r.request_id}-${tag}`} className="rounded-pill border border-n-border-vis px-2 py-0.5 font-mono text-[11px] uppercase tracking-wider text-n-secondary">
                            {t.tags.constraint(tag)}
                          </span>
                        ))}
                        {r.hint_tags?.map((tag) => (
                          <span key={`${r.request_id}-${tag}`} className="rounded-pill border border-n-border-vis px-2 py-0.5 font-mono text-[11px] uppercase tracking-wider text-n-secondary">
                            {t.tags.hint(tag)}
                          </span>
                        ))}
                      </div>
                    </td>
                    <td className="px-6 py-4 font-mono text-[12px] text-n-secondary">{r.model.split("/").pop()}</td>
                    <td className="px-6 py-4 text-right font-mono text-[12px] text-n-secondary">${r.cost.toFixed(4)}</td>
                    <td className="py-4 pl-4 pr-8 text-right">
                      {fb ? (
                        <span
                          className={`font-mono text-[12px] font-medium ${feedbackTone(fb)}`}
                          title={fb.reason}
                        >
                          {feedbackLabel(fb, t)}
                        </span>
                      ) : (
                        <div className="flex justify-end gap-1.5">
                          <button
                            disabled={isBusy}
                            onClick={() => handle(r.request_id, "strong")}
                            className="rounded-pill border border-n-border-vis px-2.5 py-1 font-mono text-[11px] uppercase tracking-wider text-n-secondary transition-colors hover:border-n-primary hover:text-n-primary disabled:opacity-40 whitespace-nowrap"
                          >
                            {t.feedback.tooStrong}
                          </button>
                          <button
                            disabled={isBusy}
                            onClick={() => handle(r.request_id, "ok")}
                            className="rounded-pill bg-n-display px-2.5 py-1 font-mono text-[11px] uppercase tracking-wider text-n-black transition-colors hover:bg-n-primary disabled:opacity-40 whitespace-nowrap"
                          >
                            {t.feedback.justRight}
                          </button>
                          <button
                            disabled={isBusy}
                            onClick={() => handle(r.request_id, "weak")}
                            className="rounded-pill border border-n-border-vis px-2.5 py-1 font-mono text-[11px] uppercase tracking-wider text-n-secondary transition-colors hover:border-n-primary hover:text-n-primary disabled:opacity-40 whitespace-nowrap"
                          >
                            {t.feedback.tooWeak}
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
