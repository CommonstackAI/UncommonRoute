import { useState, useMemo } from "react";
import { motion, AnimatePresence } from "framer-motion";
import type { Mapping } from "../api";
import { Card } from "./ui/Card";
import { Search } from "lucide-react";

interface Props {
  mapping: Mapping | null;
}

const PROVIDER_COLORS: Record<string, string> = {
  anthropic: "bg-amber-500",
  openai: "bg-emerald-500",
  google: "bg-blue-500",
  deepseek: "bg-violet-500",
  "x-ai": "bg-slate-500",
  minimax: "bg-rose-500",
  moonshotai: "bg-sky-500",
  qwen: "bg-orange-500",
  "zai-org": "bg-lime-500",
  zhipu: "bg-teal-500",
};

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
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-[#111827]">Models</h1>
          <p className="text-[13px] font-medium text-[#6B7280] mt-1">{pool.length} models from {new Set(pool.map(m => m.provider)).size} providers</p>
        </div>
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[#9CA3AF]" />
          <input
            type="text"
            placeholder="Filter models..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-64 bg-white border border-black/[0.06] shadow-sm rounded-xl pl-9 pr-4 py-2 text-[13px] font-medium text-[#111827] placeholder-[#9CA3AF] focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-400 transition-all"
          />
        </div>
      </div>

      <AnimatePresence mode="popLayout">
        {providers.map((p) => (
          <motion.div key={p} layout initial={{ opacity: 0, scale: 0.98 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0, scale: 0.98 }} transition={{ duration: 0.2 }}>
            <Card>
              <div className="px-5 py-3 border-b border-black/[0.04] flex items-center justify-between bg-gray-50/50">
                <div className="flex items-center gap-3">
                  <div className={`h-2 w-2 rounded-full ${PROVIDER_COLORS[p] || "bg-gray-400"}`} />
                  <h3 className="text-[13px] font-semibold text-[#111827] capitalize">{p}</h3>
                </div>
                <span className="text-[12px] font-medium text-[#9CA3AF]">{grouped[p].length}</span>
              </div>
              <AnimatePresence mode="popLayout">
                {grouped[p].map((m) => {
                  const coreName = m.id.split("/").pop() || m.id;
                  const c = m.capabilities;
                  return (
                    <motion.div layout key={m.id} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.15 }}
                      className="px-5 py-3 border-b border-black/[0.03] last:border-0 flex items-center justify-between hover:bg-gray-50 transition-colors group"
                    >
                      <div className="flex items-center gap-4">
                        <div className="text-[13px] font-medium text-[#4B5563] group-hover:text-[#111827] transition-colors">{coreName}</div>
                        <div className="flex gap-1.5">
                          {c.reasoning && <Tag>Reasoning</Tag>}
                          {c.vision && <Tag>Vision</Tag>}
                          {c.tool_calling && <Tag>Tools</Tag>}
                          {c.free && <Tag accent>Free</Tag>}
                        </div>
                      </div>
                      <span className="text-[12px] font-mono font-medium text-[#9CA3AF] group-hover:text-[#6B7280] transition-colors">
                        ${m.pricing.input.toFixed(2)} / ${m.pricing.output.toFixed(2)}
                      </span>
                    </motion.div>
                  );
                })}
              </AnimatePresence>
            </Card>
          </motion.div>
        ))}
      </AnimatePresence>
      {providers.length === 0 && (
        <div className="text-center py-20 text-[14px] font-medium text-[#9CA3AF]">No models match your search.</div>
      )}
    </div>
  );
}

function Tag({ children, accent }: { children: React.ReactNode; accent?: boolean }) {
  return (
    <span className={`px-2 py-0.5 rounded-md text-[10px] font-semibold uppercase tracking-wider ${
      accent
        ? "bg-emerald-50 text-emerald-600 ring-1 ring-emerald-500/20"
        : "bg-gray-100 text-[#6B7280] ring-1 ring-black/[0.04]"
    }`}>
      {children}
    </span>
  );
}
