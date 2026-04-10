/**
 * Nothing Design: Playground
 *
 * Hierarchy:
 *   Primary: Predicted tier (Doto display)
 *   Secondary: Signal readout (Space Mono data rows)
 *   Tertiary: Labels, metadata
 *
 * Industrial: textarea as raw input field, signal data as instrument readout
 */

import { useState, useEffect, useRef } from "react";
import { type RoutePreviewResult } from "../api";

const TIER_NAMES = ["LOW", "MID", "MID_HIGH", "HIGH"];

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
    if (!prompt.trim()) {
      if (abortRef.current) abortRef.current.abort();
      setResult(null); setError(null); setLoading(false);
      return;
    }
    debounceRef.current = setTimeout(async () => {
      if (abortRef.current) abortRef.current.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      setLoading(true); setError(null);
      try {
        const res = await fetch("/v1/route-preview", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ prompt, risk_tolerance: riskTolerance }),
          signal: controller.signal,
        });
        if (controller.signal.aborted) return;
        if (!res.ok) { setError("[ERROR: PREVIEW FAILED]"); setResult(null); return; }
        const data: RoutePreviewResult = await res.json();
        if (controller.signal.aborted) return;
        setResult(data); setError(null);
      } catch (e: any) {
        if (e.name === "AbortError") return;
        setError("[ERROR: PROXY UNREACHABLE]"); setResult(null);
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }, 300);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
      if (abortRef.current) abortRef.current.abort();
    };
  }, [prompt, riskTolerance]);

  useEffect(() => () => { abortRef.current?.abort(); }, []);

  return (
    <div>
      {/* Header */}
      <div className="mb-8">
        <h1 className="font-display text-[36px] text-n-display tracking-tight">PLAYGROUND</h1>
        <p className="mt-2 text-[14px] text-n-secondary">
          Type a prompt. Watch the router decide in real time.
        </p>
      </div>

      <div className="grid grid-cols-5 gap-8">
        {/* ─── Left: Input ─── */}
        <div className="col-span-3">
          <div className="label mb-2">PROMPT</div>
          <textarea
            className="w-full h-48 bg-n-surface border border-n-border rounded-compact p-4 font-sans text-[14px] text-n-primary placeholder:text-n-disabled focus:border-n-border-vis focus:outline-none resize-none transition-colors duration-150"
            placeholder="Type a prompt to see where it would be routed..."
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
          />

          {/* Risk tolerance slider */}
          <div className="mt-6 flex items-center gap-4">
            <div className="label w-32">RISK TOLERANCE</div>
            <input
              type="range" min="0" max="1" step="0.05"
              value={riskTolerance}
              onChange={(e) => setRiskTolerance(parseFloat(e.target.value))}
              className="flex-1 accent-n-display h-[2px]"
            />
            <span className="font-mono text-[13px] text-n-display w-10 text-right">
              {riskTolerance.toFixed(2)}
            </span>
          </div>

          {/* Segmented bar for risk */}
          <div className="mt-2 segmented-bar" style={{ maxWidth: 300 }}>
            {Array.from({ length: 20 }).map((_, i) => (
              <div
                key={i}
                className={`segment ${i < Math.round(riskTolerance * 20) ? "filled" : ""}`}
              />
            ))}
          </div>
          <div className="mt-1 flex justify-between font-mono text-[9px] text-n-disabled tracking-[0.06em]" style={{ maxWidth: 300 }}>
            <span>CONSERVATIVE</span>
            <span>AGGRESSIVE</span>
          </div>
        </div>

        {/* ─── Right: Result ─── */}
        <div className="col-span-2">
          {error && (
            <div className="font-mono text-[12px] text-n-accent mb-4">{error}</div>
          )}

          {result ? (
            <div>
              {/* Primary: Tier (Doto hero) */}
              <div className="mb-6">
                <div className="label mb-2">PREDICTED TIER</div>
                <div className="font-display text-[48px] text-n-display leading-none tracking-tight">
                  {result.tier_name?.toUpperCase() || "—"}
                </div>
                <div className="mt-2 font-mono text-[14px] text-n-secondary">
                  {Math.round(result.confidence * 100)}% CONFIDENCE · {result.method?.toUpperCase()}
                </div>
              </div>

              {/* Cost comparison */}
              <div className="flex gap-6 mb-6 pb-6 border-b border-n-border">
                <div>
                  <div className="label mb-1">EST. COST</div>
                  <div className="font-mono text-[20px] text-n-success">
                    ${result.cost_estimate?.toFixed(4)}
                  </div>
                </div>
                <div>
                  <div className="label mb-1">VS PREMIUM</div>
                  <div className="font-mono text-[20px] text-n-disabled line-through">
                    ${result.cost_baseline?.toFixed(4)}
                  </div>
                </div>
                <div>
                  <div className="label mb-1">SAVED</div>
                  <div className="font-mono text-[20px] text-n-success">
                    {result.cost_baseline > 0
                      ? Math.round((1 - result.cost_estimate / result.cost_baseline) * 100)
                      : 0}%
                  </div>
                </div>
              </div>

              {/* Signal readout — instrument panel style */}
              <div className="label mb-3">SIGNAL READOUT</div>
              <div className="space-y-0 border-t border-n-border">
                {result.signals?.map((s) => (
                  <div
                    key={s.name}
                    className="flex items-center justify-between py-2.5 border-b border-n-border"
                  >
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-[11px] tracking-[0.06em] text-n-secondary uppercase w-24">
                        {s.name}
                      </span>
                      {s.shadow && (
                        <span className="font-mono text-[9px] text-n-disabled border border-n-border px-1.5 py-0.5">
                          SHADOW
                        </span>
                      )}
                    </div>
                    <div className="flex items-center gap-4">
                      <span className="font-mono text-[12px] text-n-display">
                        {s.tier !== null ? TIER_NAMES[s.tier] : "ABSTAIN"}
                      </span>
                      <span className="font-mono text-[11px] text-n-secondary">
                        {Math.round(s.confidence * 100)}%
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <div className="flex items-center justify-center h-64 border border-dashed border-n-border rounded-compact">
              <span className="font-mono text-[11px] text-n-disabled tracking-[0.08em]">
                {loading ? "[ANALYZING...]" : "[AWAITING INPUT]"}
              </span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
