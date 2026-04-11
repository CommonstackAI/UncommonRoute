/**
 * Nothing Design: Sidebar navigation
 * Space Mono ALL CAPS labels, dot indicator for active, OLED black
 */

interface Props {
  current: string;
  onChange: (page: string) => void;
  upstream: string;
  isUp: boolean;
  version: string;
  feedbackPending: number;
}

type NavEntry =
  | { kind: "item"; id: string; label: string }
  | { kind: "divider"; label: string };

const NAV: NavEntry[] = [
  { kind: "divider", label: "MONITOR" },
  { kind: "item", id: "home", label: "HOME" },
  { kind: "item", id: "playground", label: "PLAYGROUND" },
  { kind: "item", id: "explain", label: "EXPLAIN" },
  { kind: "item", id: "activity", label: "ACTIVITY" },
  { kind: "divider", label: "CONFIGURE" },
  { kind: "item", id: "routing", label: "ROUTING" },
  { kind: "item", id: "models", label: "MODELS" },
  { kind: "item", id: "connections", label: "CONNECTIONS" },
  { kind: "item", id: "budget", label: "BUDGET" },
  { kind: "divider", label: "INTERACT" },
  { kind: "item", id: "feedback", label: "FEEDBACK" },
];

export default function Sidebar({ current, onChange, upstream, isUp, version, feedbackPending }: Props) {
  return (
    <aside className="fixed top-0 left-0 h-full w-[200px] bg-n-black border-r border-n-border flex flex-col z-50">
      {/* Logo — Doto hero + mono label */}
      <div className="px-6 h-16 flex items-center gap-2">
        <span className="font-display text-[20px] text-n-display tracking-tight">UR</span>
        <span className="font-mono text-[12px] text-n-disabled tracking-[0.1em]">ROUTE</span>
      </div>

      {/* Nav items */}
      <nav className="flex-1 py-4 px-3 flex flex-col gap-px">
        {NAV.map((entry, i) => {
          if (entry.kind === "divider") {
            return (
              <div key={`divider-${entry.label}`} className={`font-mono text-[10px] text-n-disabled tracking-[0.12em] ${i === 0 ? "" : "mt-6"} mb-1 px-4`}>
                {entry.label}
              </div>
            );
          }
          const active = current === entry.id;
          return (
            <button
              key={entry.id}
              onClick={() => onChange(entry.id)}
              className={`relative w-full text-left px-4 py-2.5 font-mono text-[11px] tracking-[0.08em] transition-colors duration-150 ${
                active ? "text-n-display" : "text-n-disabled hover:text-n-secondary"
              }`}
            >
              {active && (
                <span className="absolute left-1 top-1/2 -translate-y-1/2 w-1 h-1 rounded-full bg-n-accent animate-pulse" style={{ animationDuration: '2s' }} />
              )}
              {entry.label}
              {entry.id === "feedback" && feedbackPending > 0 && (
                <span className="ml-2 font-mono text-[11px] text-n-accent">
                  {feedbackPending}
                </span>
              )}
            </button>
          );
        })}
      </nav>

      {/* Status */}
      <div className="px-6 py-5 border-t border-n-border">
        <div className="flex items-center gap-2 font-mono text-[12px] tracking-[0.06em] text-n-disabled">
          <span className={`h-1.5 w-1.5 rounded-full ${isUp ? "bg-n-success" : "bg-n-disabled"}`} />
          <span className="truncate">{(upstream || "NO UPSTREAM").toUpperCase()}</span>
        </div>
        <div className="mt-2 font-mono text-[12px] tracking-[0.1em] text-n-disabled">
          V{version}
        </div>
      </div>
    </aside>
  );
}
