import { useState, useEffect } from "react";
import { TierBadge } from "./TierBadge";
import { SignalCard } from "./SignalCard";
import { CostComparison } from "./CostComparison";

const TIER_NAMES = ["low", "mid", "mid_high", "high"];

interface RecentReq {
  prompt_preview?: string;
  tier?: string;
  model?: string;
  estimated_cost?: number;
  confidence?: number;
}

export default function Explainer() {
  const [recent, setRecent] = useState<RecentReq[]>([]);
  const [selected, setSelected] = useState<RecentReq | null>(null);

  useEffect(() => {
    fetch("/v1/stats/recent?limit=20")
      .then(r => r.ok ? r.json() : { requests: [] })
      .then(data => setRecent(data.requests || data.recent || []))
      .catch(() => setRecent([]));
  }, []);

  return (
    <div>
      <h1 className="text-[22px] font-semibold text-[#111827] mb-1">Route Explainer</h1>
      <p className="text-[13px] text-[#6B7280] mb-6">Understand why each request was routed to a specific model.</p>

      <div className="grid grid-cols-3 gap-6">
        <div className="col-span-1 space-y-2 max-h-[600px] overflow-y-auto">
          <h2 className="text-[13px] font-semibold text-[#6B7280] mb-2">Recent Requests</h2>
          {recent.length === 0 && <p className="text-[13px] text-[#9CA3AF]">No requests yet. Send some requests through the proxy first.</p>}
          {recent.map((req, i) => (
            <button key={i} onClick={() => setSelected(req)}
              className={`w-full text-left p-3 rounded-lg border transition-colors ${
                selected === req ? "border-blue-500 bg-blue-50" : "border-gray-200 hover:border-gray-300"}`}>
              <div className="flex items-center justify-between mb-1">
                {req.tier && <TierBadge tier={req.tier} size="sm" />}
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
                  {selected.tier && <TierBadge tier={selected.tier} size="lg" />}
                  <span className="text-2xl font-bold text-[#111827]">
                    {selected.confidence ? `${Math.round(selected.confidence * 100)}%` : "\u2014"}
                  </span>
                </div>
              </div>
              <CostComparison actual={selected.estimated_cost || 0.001} baseline={0.02} />
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
