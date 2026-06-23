import { useState } from "react";
import { setSpendLimit, clearSpendLimit, type Spend } from "../api";
import { useT } from "../i18n";
import type { Dictionary } from "../i18n/types";

function formatWindowName(w: string, t: Dictionary): string {
  if (w === "per_request") return t.budget.windowPerRequest;
  if (w === "hourly") return t.budget.windowHourly;
  if (w === "daily") return t.budget.windowDaily;
  return w.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

const WINDOWS = ["per_request", "hourly", "daily"];

interface Props {
  spend: Spend | null;
  onRefresh: () => void;
}

export default function SpendPanel({ spend, onRefresh }: Props) {
  const t = useT();
  const [window, setWindow] = useState("hourly");
  const [amount, setAmount] = useState("5.00");
  const [busy, setBusy] = useState(false);

  const limits = spend?.limits ?? {};
  const spent = spend?.spent ?? {};
  const remaining = spend?.remaining ?? {};
  const calls = spend?.calls ?? 0;
  const activeWindows = WINDOWS.filter((w) => limits[w] != null);

  const totalSpent = Object.values(spent).reduce((sum, v) => sum + (v ?? 0), 0);

  async function handleSet() {
    const val = parseFloat(amount);
    if (isNaN(val)) return;
    setBusy(true);
    await setSpendLimit(window, val);
    onRefresh();
    setBusy(false);
  }

  async function handleClear() {
    setBusy(true);
    await clearSpendLimit(window);
    onRefresh();
    setBusy(false);
  }

  return (
    <div className="space-y-6 animate-fadeIn">
      <div>
        <h1 className="font-display text-[36px] text-n-display tracking-tight">{t.budget.title}</h1>
        <p className="mt-1 text-[13px] text-n-secondary">{t.budget.totalCalls(calls)}</p>
      </div>

      {/* Hero spend number */}
      <div className="rounded-card border border-n-border bg-n-surface p-6">
        <div className="label">{t.budget.totalSpent}</div>
        <div className="mt-2 font-display text-[48px] font-bold leading-none tracking-tight text-n-display">
          ${totalSpent.toFixed(4)}
        </div>
        <div className="mt-2 font-mono text-[13px] text-n-secondary">
          {t.budget.acrossWindows(activeWindows.length)}
        </div>

        {/* Segmented progress bars for each active limit */}
        {activeWindows.length > 0 && (
          <div className="mt-6 space-y-4">
            {activeWindows.map((w) => {
              const limit = limits[w] ?? 0;
              const spentVal = spent[w] ?? 0;
              const ratio = limit > 0 ? Math.min(spentVal / limit, 1) : 0;
              const totalSegments = 20;
              const filledSegments = Math.round(ratio * totalSegments);
              const isLow = remaining[w] != null && remaining[w] < limit * 0.2;

              return (
                <div key={w}>
                  <div className="flex items-center justify-between mb-2">
                    <span className="label">{formatWindowName(w, t).toUpperCase()}</span>
                    <span className={`font-mono text-[12px] ${isLow ? "text-n-accent" : "text-n-primary"}`}>
                      ${spentVal.toFixed(4)} / ${limit.toFixed(2)}
                    </span>
                  </div>
                  <div className="segmented-bar">
                    {Array.from({ length: totalSegments }).map((_, i) => (
                      <div
                        key={i}
                        className={`segment ${
                          i < filledSegments
                            ? isLow ? "accent" : "filled"
                            : ""
                        }`}
                      />
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Set Limit */}
      <div className="rounded-card border border-n-border bg-n-surface p-6">
        <div className="label mb-4">{t.budget.setLimit}</div>
        <div className="flex items-end gap-3">
          <div>
            <label className="label mb-1.5 block">{t.budget.window}</label>
            <select value={window} onChange={(e) => setWindow(e.target.value)}
              className="rounded-compact border border-n-border-vis bg-n-surface px-3 py-2.5 font-mono text-[13px] text-n-primary focus:border-n-display focus:outline-none"
            >
              {WINDOWS.map((w) => <option key={w} value={w}>{formatWindowName(w, t)}</option>)}
            </select>
          </div>
          <div>
            <label className="label mb-1.5 block">{t.budget.amount}</label>
            <input type="number" value={amount} onChange={(e) => setAmount(e.target.value)} min={0} step={0.5}
              className="w-28 rounded-compact border border-n-border-vis bg-n-surface px-3 py-2.5 font-mono text-[13px] text-n-primary focus:border-n-display focus:outline-none"
            />
          </div>
          <button disabled={busy} onClick={handleSet}
            className="rounded-pill bg-n-display px-5 py-2.5 font-mono text-[13px] uppercase tracking-[0.06em] text-n-black transition-colors hover:bg-n-primary disabled:opacity-40"
          >{t.budget.setLimitBtn}</button>
          <button disabled={busy} onClick={handleClear}
            className="rounded-pill border border-n-border-vis px-5 py-2.5 font-mono text-[13px] uppercase tracking-[0.06em] text-n-secondary transition-colors hover:border-n-primary hover:text-n-primary disabled:opacity-40"
          >{t.budget.clear}</button>
        </div>
      </div>

      {/* Current Limits Table */}
      <div className="rounded-card border border-n-border bg-n-surface overflow-hidden">
        <div className="border-b border-n-border px-6 py-4">
          <span className="label">{t.budget.currentLimits}</span>
        </div>
        <table className="w-full">
          <thead>
            <tr className="border-b border-n-border">
              <th className="label px-6 py-3 text-left">{t.budget.window}</th>
              <th className="label px-6 py-3 text-right">{t.budget.limit}</th>
              <th className="label px-6 py-3 text-right">{t.budget.spent}</th>
              <th className="label px-6 py-3 text-right">{t.budget.remaining}</th>
            </tr>
          </thead>
          <tbody>
            {activeWindows.length > 0 ? activeWindows.map((w) => {
              const isLow = remaining[w] != null && remaining[w] < limits[w] * 0.2;
              return (
                <tr key={w} className="border-b border-n-border last:border-0 row-hover hover:bg-n-raised">
                  <td className="px-6 py-3.5 font-mono text-[13px] text-n-primary">{formatWindowName(w, t)}</td>
                  <td className="px-6 py-3.5 text-right font-mono text-[13px] text-n-display">${limits[w].toFixed(2)}</td>
                  <td className="px-6 py-3.5 text-right font-mono text-[13px] text-n-secondary">${(spent[w] ?? 0).toFixed(4)}</td>
                  <td className={`px-6 py-3.5 text-right font-mono text-[13px] font-semibold ${isLow ? "text-n-accent" : "text-n-success"}`}>
                    {remaining[w] != null ? `$${remaining[w].toFixed(4)}` : "\u2014"}
                  </td>
                </tr>
              );
            }) : (
              <tr><td colSpan={4} className="py-16 text-center font-mono text-[14px] text-n-disabled">{t.budget.noLimits}</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
