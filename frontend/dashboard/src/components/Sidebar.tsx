/**
 * Nothing Design: Sidebar navigation
 * Space Mono ALL CAPS labels, dot indicator for active, OLED black
 */

import { useI18n } from "../i18n";

interface Props {
  current: string;
  onChange: (page: string) => void;
  upstream: string;
  isUp: boolean;
  version: string;
  feedbackPending: number;
}

type NavEntry =
  | { kind: "item"; id: string; labelKey: string }
  | { kind: "divider"; labelKey: string };

const NAV: NavEntry[] = [
  { kind: "divider", labelKey: "monitor" },
  { kind: "item", id: "home", labelKey: "home" },
  { kind: "item", id: "playground", labelKey: "playground" },
  { kind: "item", id: "explain_new", labelKey: "explain" },
  { kind: "item", id: "activity", labelKey: "activity" },
  { kind: "divider", labelKey: "configure" },
  { kind: "item", id: "routing", labelKey: "routing" },
  { kind: "item", id: "models", labelKey: "models" },
  { kind: "item", id: "connections", labelKey: "connections" },
  { kind: "item", id: "budget", labelKey: "budget" },
  { kind: "divider", labelKey: "interact" },
  { kind: "item", id: "feedback", labelKey: "feedback" },
];

export default function Sidebar({ current, onChange, upstream, isUp, version, feedbackPending }: Props) {
  const { t, locale, setLocale, isZh } = useI18n();
  const sidebar = t.sidebar as unknown as Record<string, string>;
  // CJK glyphs don't render well with the latin tracking baked into the design system.
  const trackTight = isZh ? "tracking-normal" : "tracking-[0.12em]";
  const trackItem = isZh ? "tracking-normal" : "tracking-[0.08em]";
  const trackStatus = isZh ? "tracking-normal" : "tracking-[0.06em]";
  const trackVersion = isZh ? "tracking-normal" : "tracking-[0.1em]";

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
              <div key={`divider-${entry.labelKey}`} className={`font-mono text-[10px] text-n-disabled ${trackTight} ${i === 0 ? "" : "mt-6"} mb-1 px-4`}>
                {sidebar[entry.labelKey]}
              </div>
            );
          }
          const active = current === entry.id;
          return (
            <button
              key={entry.id}
              onClick={() => onChange(entry.id)}
              className={`relative w-full text-left px-4 py-2.5 font-mono text-[11px] ${trackItem} transition-colors duration-150 ${
                active ? "text-n-display" : "text-n-disabled hover:text-n-secondary"
              }`}
            >
              {active && (
                <span className="absolute left-1 top-1/2 -translate-y-1/2 w-1 h-1 rounded-full bg-n-accent animate-pulse" style={{ animationDuration: '2s' }} />
              )}
              {sidebar[entry.labelKey]}
              {entry.id === "feedback" && feedbackPending > 0 && (
                <span className="ml-2 font-mono text-[11px] text-n-accent">
                  {feedbackPending}
                </span>
              )}
            </button>
          );
        })}
      </nav>

      {/* Language toggle */}
      <div className="px-4 pb-3">
        <div className="inline-flex items-center gap-[2px] rounded-pill border border-n-border bg-n-surface p-[2px] font-mono text-[10px]">
          {(["en", "zh"] as const).map((code) => {
            const isActive = locale === code;
            return (
              <button
                key={code}
                onClick={() => setLocale(code)}
                className={`rounded-pill px-2 py-0.5 transition-colors ${
                  isActive ? "bg-n-display text-n-black" : "text-n-disabled hover:text-n-secondary"
                }`}
              >
                {code === "en" ? "EN" : "中文"}
              </button>
            );
          })}
        </div>
      </div>

      {/* Status */}
      <div className="px-6 py-5 border-t border-n-border">
        <div className={`flex items-center gap-2 font-mono text-[12px] ${trackStatus} text-n-disabled`}>
          <span className={`h-1.5 w-1.5 rounded-full ${isUp ? "bg-n-success" : "bg-n-disabled"}`} />
          <span className="truncate">{upstream ? upstream.toUpperCase() : t.sidebar.noUpstream}</span>
        </div>
        <div className={`mt-2 font-mono text-[12px] ${trackVersion} text-n-disabled`}>
          V{version}
        </div>
      </div>
    </aside>
  );
}
