import { motion } from "framer-motion";
import { cn } from "../lib/utils";

interface Props {
  current: string;
  onChange: (page: string) => void;
  upstream: string;
  isUp: boolean;
  version: string;
  feedbackPending: number;
}

const NAV = [
  { id: "home", label: "Home" },
  { id: "routing", label: "Routing" },
  { id: "models", label: "Models" },
  { id: "connections", label: "Connections" },
  { id: "activity", label: "Activity" },
  { id: "budget", label: "Budget" },
  { id: "feedback", label: "Feedback" },
];

export default function Sidebar({ current, onChange, upstream, isUp, version, feedbackPending }: Props) {
  return (
    <aside className="fixed top-0 left-0 h-full w-[240px] bg-[#F9FAFB] border-r border-black/[0.04] flex flex-col z-50">
      <div className="px-6 h-16 flex items-center">
        <span className="text-[15px] font-semibold text-[#111827] tracking-tight">UncommonRoute</span>
      </div>

      <nav className="flex-1 py-3 px-3 flex flex-col gap-1">
        {NAV.map((item) => {
          const isActive = current === item.id;
          return (
            <button
              key={item.id}
              onClick={() => onChange(item.id)}
              className={cn(
                "relative w-full text-left px-3 py-2 rounded-xl text-[13px] font-medium transition-colors flex items-center justify-between",
                isActive ? "text-[#111827]" : "text-[#6B7280] hover:text-[#111827] hover:bg-black/[0.03]"
              )}
            >
              {isActive && (
                <motion.div
                  layoutId="sidebar-active"
                  className="absolute inset-0 bg-white shadow-sm ring-1 ring-black/[0.04] rounded-xl"
                  transition={{ type: "spring", stiffness: 500, damping: 35 }}
                />
              )}
              <span className="relative z-10">{item.label}</span>
              {item.id === "feedback" && feedbackPending > 0 && (
                <span className="relative z-10 text-[11px] font-semibold bg-indigo-500 text-white rounded-full h-5 min-w-5 flex items-center justify-center px-1.5 shadow-sm">
                  {feedbackPending}
                </span>
              )}
            </button>
          );
        })}
      </nav>

      <div className="px-6 py-6">
        <div className="flex items-center gap-2 text-[12px] font-medium text-[#9CA3AF]">
          <div className={cn("h-2 w-2 rounded-full", isUp ? "bg-emerald-500" : "bg-gray-300")} />
          <span className="truncate">{upstream || "No upstream"}</span>
        </div>
        <div className="text-[11px] font-mono font-medium text-[#D1D5DB] mt-1.5">v{version}</div>
      </div>
    </aside>
  );
}
