import { useState, useEffect, useRef } from "react";
import { TierBadge } from "./TierBadge";
import { SignalCard } from "./SignalCard";
import { CostComparison } from "./CostComparison";
import { type RoutePreviewResult } from "../api";

const TIER_NAMES = ["low", "mid", "mid_high", "high"];

export default function Playground() {
  const [prompt, setPrompt] = useState("");
  const [riskTolerance, setRiskTolerance] = useState(0.5);
  const [result, setResult] = useState<RoutePreviewResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>();
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    if (!prompt.trim()) { setResult(null); setError(null); setLoading(false); return; }

    debounceRef.current = setTimeout(async () => {
      // Cancel any in-flight request
      if (abortRef.current) abortRef.current.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      setLoading(true);
      setError(null);
      try {
        const res = await fetch("/v1/route-preview", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ prompt, risk_tolerance: riskTolerance }),
          signal: controller.signal,
        });
        if (controller.signal.aborted) return; // stale
        if (!res.ok) { setError("Preview failed"); setResult(null); return; }
        const data: RoutePreviewResult = await res.json();
        if (controller.signal.aborted) return; // stale
        setResult(data);
        setError(null);
      } catch (e: any) {
        if (e.name === "AbortError") return; // cancelled, ignore
        setError("Preview failed — is the proxy running?");
        setResult(null);
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }, 300);

    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [prompt, riskTolerance]);

  // Cleanup abort on unmount
  useEffect(() => () => { abortRef.current?.abort(); }, []);

  return (
    <div>
      <h1 className="text-[22px] font-semibold text-[#111827] mb-1">Playground</h1>
      <p className="text-[13px] text-[#6B7280] mb-6">Type a prompt and watch the router decide in real-time.</p>

      <div className="grid grid-cols-5 gap-6">
        <div className="col-span-3 space-y-4">
          <textarea
            className="w-full h-48 p-4 border border-gray-200 rounded-xl focus:ring-2 focus:ring-blue-500 focus:border-transparent resize-none text-[14px] text-[#111827]"
            placeholder="Type a prompt to see where it would be routed..."
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
          />
          <div className="flex items-center gap-4">
            <label className="text-[13px] text-[#6B7280] w-32">Risk Tolerance</label>
            <input type="range" min="0" max="1" step="0.05" value={riskTolerance}
              onChange={(e) => setRiskTolerance(parseFloat(e.target.value))} className="flex-1" />
            <span className="text-[13px] font-mono text-[#374151] w-10">{riskTolerance.toFixed(2)}</span>
          </div>
        </div>

        <div className="col-span-2 space-y-4">
          {error && <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">{error}</div>}
          {result ? (
            <>
              <div className="text-center p-4 bg-white rounded-xl border border-gray-200">
                <TierBadge tier={result.tier_name} size="lg" />
                <div className="text-3xl font-bold text-[#111827] mt-2">{Math.round(result.confidence * 100)}%</div>
                <p className="text-[13px] text-[#6B7280] mt-1">{result.method} routing</p>
              </div>
              <CostComparison actual={result.cost_estimate} baseline={result.cost_baseline} />
              <div className="space-y-2">
                {result.signals.map((s) => (
                  <SignalCard key={s.name} name={s.name}
                    tier={s.tier} tierName={s.tier !== null ? TIER_NAMES[s.tier] : ""}
                    confidence={s.confidence} shadow={s.shadow} />
                ))}
              </div>
            </>
          ) : (
            <div className="flex items-center justify-center h-64 text-[#9CA3AF] border border-dashed border-gray-200 rounded-xl text-[13px]">
              {loading ? "Analyzing..." : "Start typing to see routing..."}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
