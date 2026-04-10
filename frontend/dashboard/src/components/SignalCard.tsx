/**
 * Nothing Design: Signal card — instrument readout row
 */

const TIER_NAMES = ["LOW", "MID", "MID_HIGH", "HIGH"];

interface SignalCardProps {
  name: string;
  tier: number | null;
  tierName: string;
  confidence: number;
  reasoning?: string;
  shadow?: boolean;
}

export function SignalCard({ name, tier, confidence, reasoning, shadow }: SignalCardProps) {
  const pct = Math.round(confidence * 100);
  return (
    <div className={`py-3 border-b ${shadow ? "border-dashed border-n-border" : "border-n-border"}`}>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="font-mono text-[11px] tracking-[0.06em] text-n-secondary uppercase">{name}</span>
          {shadow && (
            <span className="font-mono text-[9px] text-n-disabled border border-n-border px-1.5 py-0.5">SHADOW</span>
          )}
        </div>
        <div className="flex items-center gap-3">
          <span className="font-mono text-[12px] text-n-display">
            {tier !== null ? TIER_NAMES[tier] : "ABSTAIN"}
          </span>
          <span className="font-mono text-[11px] text-n-secondary">{pct}%</span>
        </div>
      </div>
      <div className="mt-2 flex gap-px h-[3px]" style={{ maxWidth: 200 }}>
        {Array.from({ length: 10 }).map((_, i) => (
          <div key={i} className={`flex-1 ${i < Math.round(confidence * 10) ? "bg-n-primary" : "bg-n-border"}`} />
        ))}
      </div>
      {reasoning && <div className="mt-1.5 font-mono text-[10px] text-n-disabled">{reasoning}</div>}
    </div>
  );
}
