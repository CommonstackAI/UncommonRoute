import { useState, useMemo } from "react";
import type { Mapping } from "../api";
import { Search } from "lucide-react";

interface Props {
  mapping: Mapping | null;
}

export default function Models({ mapping }: Props) {
  const [search, setSearch] = useState("");
  const pool = mapping?.pool ?? [];

  const grouped = useMemo(() => {
    const q = search.toLowerCase();
    const filtered = pool.filter((m) => {
      if (!q) return true;
      return m.id.toLowerCase().includes(q) ||
        (q === "free" && m.capabilities.free) ||
        (q === "vision" && m.capabilities.vision) ||
        (q === "reasoning" && m.capabilities.reasoning) ||
        (q === "tools" && m.capabilities.tool_calling);
    });
    const groups: Record<string, typeof pool> = {};
    for (const m of filtered) {
      const p = m.provider || "unknown";
      if (!groups[p]) groups[p] = [];
      groups[p].push(m);
    }
    return groups;
  }, [pool, search]);

  const providers = Object.keys(grouped).sort();

  return (
    <div className="space-y-6 animate-fadeIn">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="font-display text-[36px] text-n-display tracking-tight">MODELS</h1>
          <p className="mt-1 text-[13px] text-n-secondary">
            {pool.length} models from {new Set(pool.map(m => m.provider)).size} providers
          </p>
        </div>
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-n-disabled" strokeWidth={1.5} />
          <input
            type="text"
            placeholder="Filter models..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-64 rounded-compact border border-n-border bg-n-surface pl-9 pr-4 py-2 font-mono text-[13px] text-n-primary placeholder-n-disabled focus:border-n-border-vis focus:outline-none transition-colors"
          />
        </div>
      </div>

      {providers.map((p) => (
        <div key={p} className="rounded-card border border-n-border bg-n-surface overflow-hidden">
          <div className="flex items-center justify-between border-b border-n-border px-5 py-3">
            <div className="flex items-center gap-3">
              <div className="h-1.5 w-1.5 rounded-full bg-n-display" />
              <h3 className="font-mono text-[13px] font-semibold uppercase tracking-wider text-n-display">{p}</h3>
            </div>
            <span className="font-mono text-[12px] text-n-secondary">{grouped[p].length}</span>
          </div>
          <table className="w-full">
            <thead>
              <tr className="border-b border-n-border">
                <th className="label px-5 py-3 text-left">MODEL</th>
                <th className="label px-5 py-3 text-left">CAPABILITIES</th>
                <th className="label px-5 py-3 text-right">IN / OUT</th>
              </tr>
            </thead>
            <tbody>
              {grouped[p].map((m) => {
                const coreName = m.id.split("/").pop() || m.id;
                const c = m.capabilities;
                return (
                  <tr key={m.id} className="border-b border-n-border last:border-0 transition-colors hover:bg-n-raised">
                    <td className="px-5 py-3 font-mono text-[13px] text-n-primary">{coreName}</td>
                    <td className="px-5 py-3">
                      <div className="flex gap-1.5">
                        {c.reasoning && <Tag>REASONING</Tag>}
                        {c.vision && <Tag>VISION</Tag>}
                        {c.tool_calling && <Tag>TOOLS</Tag>}
                        {c.free && <Tag accent>FREE</Tag>}
                      </div>
                    </td>
                    <td className="px-5 py-3 text-right font-mono text-[12px] text-n-secondary">
                      ${m.pricing.input.toFixed(2)} / ${m.pricing.output.toFixed(2)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ))}

      {providers.length === 0 && (
        <div className="py-20 text-center font-mono text-[14px] text-n-disabled">
          {pool.length === 0
            ? "Connect an upstream provider to discover available models."
            : "No models match your search."}
        </div>
      )}
    </div>
  );
}

function Tag({ children, accent }: { children: React.ReactNode; accent?: boolean }) {
  return (
    <span className={`rounded-pill border px-2 py-0.5 font-mono text-[12px] uppercase tracking-wider ${
      accent
        ? "border-n-success text-n-success"
        : "border-n-border-vis text-n-secondary"
    }`}>
      {children}
    </span>
  );
}
