/**
 * Nothing Design: Cost comparison — stat row with savings percentage
 */

interface CostComparisonProps {
  actual: number;
  baseline: number;
}

export function CostComparison({ actual, baseline }: CostComparisonProps) {
  const savings = baseline - actual;
  const ratio = baseline > 0 ? Math.round((savings / baseline) * 100) : 0;
  return (
    <div className="flex items-center gap-6 py-4 border-b border-n-border">
      <div>
        <div className="label mb-1">ACTUAL</div>
        <div className="font-mono text-[18px] text-n-success">${actual.toFixed(4)}</div>
      </div>
      <div>
        <div className="label mb-1">BASELINE</div>
        <div className="font-mono text-[18px] text-n-disabled line-through">${baseline.toFixed(4)}</div>
      </div>
      <div>
        <div className="label mb-1">SAVED</div>
        <div className="font-mono text-[18px] text-n-success">{ratio}%</div>
      </div>
    </div>
  );
}
