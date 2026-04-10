/**
 * Nothing Design: Route Explainer
 * Left: request list as data rows. Right: signal instrument panel.
 */

import { useState, useEffect } from "react";

const TIER_NAMES: Record<string, string> = {
  SIMPLE: "LOW", MEDIUM: "MID", COMPLEX: "HIGH", REASONING: "HIGH",
  low: "LOW", mid: "MID", mid_high: "MID_HIGH", high: "HIGH",
};

interface RecentReq {
  request_id?: string;
  prompt_preview?: string;
  tier?: string;
  model?: string;
  cost?: number;
  raw_confidence?: number;
  method?: string;
}

export default function Explainer() {
  const [recent, setRecent] = useState<RecentReq[]>([]);
  const [selected, setSelected] = useState<RecentReq | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/v1/stats/recent?limit=30")
      .then(r => { if (!r.ok) throw new Error(); return r.json(); })
      .then(data => setRecent(Array.isArray(data) ? data : []))
      .catch(() => { setError("[ERROR: PROXY UNREACHABLE]"); setRecent([]); });
  }, []);

  const normTier = (t?: string) => TIER_NAMES[t || ""] || (t || "—").toUpperCase();

  return (
    <div>
      <div className="mb-8">
        <h1 className="font-display text-[36px] text-n-display tracking-tight">EXPLAIN</h1>
        <p className="mt-2 text-[14px] text-n-secondary">
          Select a request to see why it was routed where it was.
        </p>
      </div>

      {error && <div className="font-mono text-[12px] text-n-accent mb-4">{error}</div>}

      <div className="grid grid-cols-3 gap-8">
        {/* Left: Request list */}
        <div className="col-span-1 max-h-[600px] overflow-y-auto">
          <div className="label mb-3">RECENT REQUESTS</div>
          {recent.length === 0 && !error && (
            <div className="font-mono text-[11px] text-n-disabled tracking-[0.06em]">
              [NO REQUESTS YET]
            </div>
          )}
          <div className="border-t border-n-border">
            {recent.map((req, i) => (
              <button
                key={req.request_id || i}
                onClick={() => setSelected(req)}
                className={`w-full text-left py-3 px-2 border-b border-n-border transition-colors duration-150 ${
                  selected === req ? "bg-n-surface" : "hover:bg-n-surface/50"
                }`}
              >
                <div className="flex items-center justify-between mb-1">
                  <span className="font-mono text-[10px] tracking-[0.06em] text-n-secondary">
                    {normTier(req.tier)}
                  </span>
                  <span className="font-mono text-[10px] text-n-disabled truncate ml-2">
                    {(req.model || "").split("/").pop()}
                  </span>
                </div>
                <div className="text-[12px] text-n-primary truncate">
                  {req.prompt_preview || "[no preview]"}
                </div>
              </button>
            ))}
          </div>
        </div>

        {/* Right: Explanation */}
        <div className="col-span-2">
          {selected ? (
            <div>
              {/* Verdict */}
              <div className="mb-8">
                <div className="label mb-2">ROUTED TO</div>
                <div className="font-display text-[48px] text-n-display leading-none tracking-tight">
                  {(selected.model || "").split("/").pop() || "—"}
                </div>
                <div className="mt-3 flex items-center gap-6">
                  <div>
                    <div className="label mb-1">TIER</div>
                    <div className="font-mono text-[16px] text-n-display">{normTier(selected.tier)}</div>
                  </div>
                  <div>
                    <div className="label mb-1">CONFIDENCE</div>
                    <div className="font-mono text-[16px] text-n-display">
                      {selected.raw_confidence != null
                        ? `${Math.round(selected.raw_confidence * 100)}%`
                        : "—"}
                    </div>
                  </div>
                  <div>
                    <div className="label mb-1">METHOD</div>
                    <div className="font-mono text-[16px] text-n-secondary">
                      {(selected.method || "pool").toUpperCase()}
                    </div>
                  </div>
                </div>
              </div>

              {/* Cost */}
              <div className="flex gap-6 mb-8 pb-6 border-b border-n-border">
                <div>
                  <div className="label mb-1">COST</div>
                  <div className="font-mono text-[20px] text-n-success">
                    ${(selected.cost ?? 0.001).toFixed(4)}
                  </div>
                </div>
                <div>
                  <div className="label mb-1">VS PREMIUM</div>
                  <div className="font-mono text-[20px] text-n-disabled line-through">$0.0200</div>
                </div>
              </div>

              {/* Signal breakdown — placeholder */}
              <div className="label mb-3">SIGNAL BREAKDOWN</div>
              <div className="border-t border-n-border">
                {[
                  { name: "METADATA", tier: "LOW", conf: "85%", shadow: false },
                  { name: "STRUCTURAL", tier: "MID", conf: "70%", shadow: true },
                  { name: "EMBEDDING", tier: "LOW", conf: "80%", shadow: false },
                ].map((s) => (
                  <div key={s.name} className="flex items-center justify-between py-2.5 border-b border-n-border">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-[11px] tracking-[0.06em] text-n-secondary w-24">{s.name}</span>
                      {s.shadow && (
                        <span className="font-mono text-[9px] text-n-disabled border border-n-border px-1.5 py-0.5">SHADOW</span>
                      )}
                    </div>
                    <div className="flex items-center gap-4">
                      <span className="font-mono text-[12px] text-n-display">{s.tier}</span>
                      <span className="font-mono text-[11px] text-n-secondary">{s.conf}</span>
                    </div>
                  </div>
                ))}
              </div>
              <div className="mt-3 font-mono text-[10px] text-n-disabled tracking-[0.04em]">
                SIGNAL DATA IS PLACEHOLDER. PER-REQUEST STORAGE COMING IN A FUTURE UPDATE.
              </div>
            </div>
          ) : (
            <div className="flex items-center justify-center h-96 border border-dashed border-n-border rounded-compact">
              <span className="font-mono text-[11px] text-n-disabled tracking-[0.08em]">
                [SELECT A REQUEST]
              </span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
