/**
 * Nothing Design: Tier badge
 * Technical tag — border only, Space Mono ALL CAPS, 4px radius
 */

const TIER_NORMALIZE: Record<string, string> = {
  SIMPLE: "LOW", MEDIUM: "MID", COMPLEX: "HIGH", REASONING: "HIGH",
  low: "LOW", mid: "MID", mid_high: "MID_HIGH", high: "HIGH",
};

const TIER_STATUS: Record<string, string> = {
  LOW: "text-n-success border-n-success/40",
  MID: "text-n-warning border-n-warning/40",
  MID_HIGH: "text-n-warning border-n-warning/40",
  HIGH: "text-n-accent border-n-accent/40",
};

interface TierBadgeProps {
  tier: string;
}

export function TierBadge({ tier }: TierBadgeProps) {
  const normalized = TIER_NORMALIZE[tier] || tier.toUpperCase();
  const style = TIER_STATUS[normalized] || "text-n-secondary border-n-border-vis";
  return (
    <span className={`inline-flex items-center font-mono text-[10px] tracking-[0.06em] border rounded-technical px-2 py-0.5 ${style}`}>
      {normalized}
    </span>
  );
}
