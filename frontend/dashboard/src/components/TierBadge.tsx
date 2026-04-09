const TIER_COLORS: Record<string, string> = {
  low: "bg-green-100 text-green-800 border-green-300",
  mid: "bg-yellow-100 text-yellow-800 border-yellow-300",
  mid_high: "bg-orange-100 text-orange-800 border-orange-300",
  high: "bg-red-100 text-red-800 border-red-300",
};

interface TierBadgeProps {
  tier: string;
  size?: "sm" | "md" | "lg";
}

export function TierBadge({ tier, size = "md" }: TierBadgeProps) {
  const colors = TIER_COLORS[tier] || TIER_COLORS.mid;
  const sizeClasses = { sm: "px-2 py-0.5 text-xs", md: "px-3 py-1 text-sm", lg: "px-4 py-1.5 text-base font-semibold" };
  return (
    <span className={`inline-flex items-center rounded-full border ${colors} ${sizeClasses[size]}`}>
      {tier.replace("_", " ")}
    </span>
  );
}
