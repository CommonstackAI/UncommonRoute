interface CostComparisonProps {
  actual: number;
  baseline: number;
}

export function CostComparison({ actual, baseline }: CostComparisonProps) {
  const savings = baseline - actual;
  const ratio = baseline > 0 ? Math.round((savings / baseline) * 100) : 0;
  return (
    <div className="flex items-center gap-4 p-3 bg-green-50 rounded-lg border border-green-200">
      <div className="text-center">
        <div className="text-lg font-bold text-green-700">${actual.toFixed(4)}</div>
        <div className="text-xs text-green-600">actual</div>
      </div>
      <div className="text-gray-400">vs</div>
      <div className="text-center">
        <div className="text-lg font-bold text-gray-400 line-through">${baseline.toFixed(4)}</div>
        <div className="text-xs text-gray-500">highest tier</div>
      </div>
      <div className="ml-auto text-center">
        <div className="text-2xl font-bold text-green-600">{ratio}%</div>
        <div className="text-xs text-green-600">saved</div>
      </div>
    </div>
  );
}
