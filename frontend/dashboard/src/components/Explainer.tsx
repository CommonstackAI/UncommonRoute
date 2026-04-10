import { useState, useEffect } from "react";
import { TierBadge } from "./TierBadge";
import { SignalCard } from "./SignalCard";
import { CostComparison } from "./CostComparison";

interface RecentReq {
  request_id?: string;
  prompt_preview?: string;
  tier?: string;
  model?: string;
  cost?: number;
  raw_confidence?: number;
  method?: string;
  complexity?: number;
}

// v1 returns uppercase tiers (SIMPLE/MEDIUM/COMPLEX), normalize for TierBadge
function normalizeTier(tier?: string): string {
  if (!tier) return "mid";
  const upper = tier.toUpperCase();
  if (upper === "SIMPLE") return "low";
  if (upper === "MEDIUM") return "mid";
  if (upper === "COMPLEX" || upper === "REASONING") return "high";
  return tier.toLowerCase();
}

export default function Explainer() {
  const [recent, setRecent] = useState<RecentReq[]>([]);
  const [selected, setSelected] = useState<RecentReq | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/v1/stats/recent?limit=30")
      .then(r => {
        if (!r.ok) throw new Error("Failed to fetch");
        return r.json();
      })
      .then(data => {
        // /v1/stats/recent returns a plain array, not an object
        const arr = Array.isArray(data) ? data : [];
        setRecent(arr);
      })
      .catch(() => {
        setError("Could not load recent requests. Is the proxy running?");
        setRecent([]);
      });
  }, []);

  return (
    <div>
      <h1 className="text-[22px] font-semibold text-[#111827] mb-1">Route Explainer</h1>
      <p className="text-[13px] text-[#6B7280] mb-6">Understand why each request was routed to a specific model.</p>

      {error && <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700 mb-4">{error}</div>}

      <div className="grid grid-cols-3 gap-6">
        <div className="col-span-1 space-y-2 max-h-[600px] overflow-y-auto">
          <h2 className="text-[13px] font-semibold text-[#6B7280] mb-2">Recent Requests</h2>
          {recent.length === 0 && !error && <p className="text-[13px] text-[#9CA3AF]">No requests yet. Send some requests through the proxy first.</p>}
          {recent.map((req, i) => (
            <button key={req.request_id || i} onClick={() => setSelected(req)}
              className={`w-full text-left p-3 rounded-lg border transition-colors ${
                selected === req ? "border-blue-500 bg-blue-50" : "border-gray-200 hover:border-gray-300"}`}>
              <div className="flex items-center justify-between mb-1">
                <TierBadge tier={normalizeTier(req.tier)} size="sm" />
                <span className="text-[11px] text-[#9CA3AF]">{req.model || ""}</span>
              </div>
              <p className="text-[13px] text-[#374151] truncate">{req.prompt_preview || "(no preview)"}</p>
            </button>
          ))}
        </div>

        <div className="col-span-2">
          {selected ? (
            <div className="space-y-4">
              <div className="p-6 bg-white rounded-xl border border-gray-200 text-center">
                <p className="text-[15px] text-[#374151]">
                  Routed to <span className="font-bold text-[#111827]">{selected.model || "unknown"}</span>
                </p>
                <div className="flex items-center justify-center gap-4 mt-3">
                  <TierBadge tier={normalizeTier(selected.tier)} size="lg" />
                  <span className="text-2xl font-bold text-[#111827]">
                    {selected.raw_confidence != null ? `${Math.round(selected.raw_confidence * 100)}%` : "\u2014"}
                  </span>
                </div>
                {selected.method && (
                  <p className="text-[13px] text-[#6B7280] mt-1">{selected.method} routing</p>
                )}
              </div>
              <CostComparison actual={selected.cost || 0.001} baseline={0.02} />
              <div className="grid grid-cols-3 gap-3">
                <SignalCard name="Metadata" tier={0} tierName="low" confidence={0.85} reasoning="Based on conversation metadata" />
                <SignalCard name="Structural" tier={1} tierName="mid" confidence={0.70} reasoning="Text structure analysis" shadow />
                <SignalCard name="Embedding" tier={0} tierName="low" confidence={0.80} reasoning="Similar to known low-tier tasks" />
              </div>
              <p className="text-[11px] text-[#9CA3AF] text-center">
                Signal breakdown shows placeholder data. Per-request signal storage coming in a future update.
              </p>
            </div>
          ) : (
            <div className="flex items-center justify-center h-96 text-[#9CA3AF] border border-dashed border-gray-200 rounded-xl text-[13px]">
              Select a request to see its routing explanation
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
