import { TierBadge } from "./TierBadge";

interface SignalCardProps {
  name: string;
  tier: number | null;
  tierName: string;
  confidence: number;
  reasoning?: string;
  shadow?: boolean;
}

export function SignalCard({ name, tier, tierName, confidence, reasoning, shadow }: SignalCardProps) {
  const pct = Math.round(confidence * 100);
  return (
    <div className={`rounded-xl border p-4 ${shadow ? "border-dashed border-gray-300 bg-gray-50" : "border-gray-200 bg-white"}`}>
      <div className="flex items-center justify-between mb-2">
        <h3 className="font-semibold text-sm text-gray-700">{name}</h3>
        {shadow && <span className="text-xs text-gray-400 bg-gray-100 px-2 py-0.5 rounded">shadow</span>}
      </div>
      <div className="flex items-center gap-2 mb-2">
        {tier !== null ? <TierBadge tier={tierName} size="sm" /> : <span className="text-gray-400 text-sm">abstained</span>}
        <span className="text-sm text-gray-500">{pct}%</span>
      </div>
      <div className="w-full bg-gray-100 rounded-full h-2 mb-2">
        <div className="h-2 rounded-full transition-all duration-500"
          style={{ width: `${pct}%`, backgroundColor: confidence >= 0.7 ? "#22c55e" : confidence >= 0.4 ? "#eab308" : "#ef4444" }} />
      </div>
      {reasoning && <p className="text-xs text-gray-500">{reasoning}</p>}
    </div>
  );
}
