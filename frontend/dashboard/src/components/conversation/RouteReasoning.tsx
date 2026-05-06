import { useMemo, useState } from "react";

interface Token {
  raw: string;
  key?: string;
  value?: string;
  qualifier?: string;
}

interface Group {
  id: string;
  title: string;
  hint: string;
  tokens: Token[];
}

const GROUP_DEFS: Array<{ id: string; title: string; hint: string; matches: (t: Token) => boolean }> = [
  {
    id: "signals",
    title: "CLASSIFIER SIGNALS",
    hint: "task complexity vote",
    matches: (t) => t.raw.startsWith("v2:") || t.raw.startsWith("v2-"),
  },
  {
    id: "selector",
    title: "SELECTOR",
    hint: "how the model pool was scored",
    matches: (t) =>
      hasKey(t, "chooser") ||
      hasKey(t, "mode") ||
      hasKey(t, "depth") ||
      hasKey(t, "hints") ||
      hasKey(t, "constraints") ||
      hasKey(t, "required_caps") ||
      t.raw === "step-stable=no-bandit" ||
      t.raw.startsWith("byok-preferred"),
  },
  {
    id: "quality",
    title: "QUALITY GUARD",
    hint: "quality / risk floors",
    matches: (t) =>
      hasKey(t, "lane") ||
      t.raw.startsWith("served-quality") ||
      t.raw.startsWith("step-risk") ||
      t.raw.startsWith("continuity-"),
  },
  {
    id: "pool",
    title: "CANDIDATE POOL",
    hint: "transport / capability filters",
    matches: (t) =>
      hasKey(t, "transport-filter") ||
      hasKey(t, "thinking-context") ||
      hasKey(t, "context-fit") ||
      hasKey(t, "cost-guard"),
  },
];

function hasKey(t: Token, key: string): boolean {
  return t.key === key;
}

function parseToken(raw: string): Token {
  const trimmed = raw.trim();
  // First-segment shape: v2:metadata=3(0.30) — key=value(qualifier)
  // Others: key=value, key=value(meta), key=value[tag], or bare flag like step-stable=no-bandit
  const m = trimmed.match(/^([a-zA-Z][a-zA-Z0-9_:.-]*)=([^|]+)$/);
  if (!m) return { raw: trimmed };
  return { raw: trimmed, key: m[1], value: m[2].trim() };
}

function parseReasoning(text: string): Token[] {
  // The first pipe-segment can contain comma-separated v2:* tokens.
  // Subsequent pipe-segments are each one token.
  const segments = text.split("|").map((s) => s.trim()).filter(Boolean);
  const out: Token[] = [];
  segments.forEach((seg, idx) => {
    if (idx === 0 && seg.includes(",") && seg.includes("v2:")) {
      // Split on commas, but keep the trailing "v2:tier=3 complexity=0.90 method=direct"
      // which has spaces (not commas) between sub-fields. Treat each comma piece as one token.
      const parts = seg.split(",").map((s) => s.trim()).filter(Boolean);
      for (const p of parts) out.push(parseToken(p));
    } else {
      out.push(parseToken(seg));
    }
  });
  return out;
}

function groupTokens(tokens: Token[]): { groups: Group[]; other: Token[] } {
  const buckets: Record<string, Token[]> = {};
  GROUP_DEFS.forEach((g) => (buckets[g.id] = []));
  const other: Token[] = [];
  for (const t of tokens) {
    const found = GROUP_DEFS.find((g) => g.matches(t));
    if (found) buckets[found.id].push(t);
    else other.push(t);
  }
  const groups = GROUP_DEFS.map((g) => ({
    id: g.id,
    title: g.title,
    hint: g.hint,
    tokens: buckets[g.id],
  })).filter((g) => g.tokens.length > 0);
  return { groups, other };
}

export default function RouteReasoning({ text }: { text: string }) {
  const [view, setView] = useState<"grouped" | "raw">("grouped");
  const parsed = useMemo(() => parseReasoning(text), [text]);
  const { groups, other } = useMemo(() => groupTokens(parsed), [parsed]);
  const hasGroups = groups.length > 0 || other.length > 0;

  return (
    <div>
      <div className="mb-1 flex items-center justify-between">
        <div className="label">ROUTE REASONING</div>
        <Toggle value={view} onChange={setView} />
      </div>

      {view === "raw" || !hasGroups ? (
        <pre className="whitespace-pre-wrap break-words font-mono text-[11px] text-n-primary">
          {text || "—"}
        </pre>
      ) : (
        <div className="space-y-2">
          {groups.map((g) => (
            <GroupBlock key={g.id} group={g} />
          ))}
          {other.length > 0 ? (
            <GroupBlock
              group={{ id: "other", title: "OTHER", hint: "uncategorized notes", tokens: other }}
            />
          ) : null}
        </div>
      )}
    </div>
  );
}

function Toggle({
  value,
  onChange,
}: {
  value: "grouped" | "raw";
  onChange: (v: "grouped" | "raw") => void;
}) {
  return (
    <div className="flex items-center gap-0 overflow-hidden rounded-pill border border-n-border-vis font-mono text-[10px] uppercase tracking-[0.06em]">
      <button
        onClick={() => onChange("grouped")}
        className={`px-2 py-0.5 ${
          value === "grouped" ? "bg-n-raised text-n-display" : "text-n-secondary hover:text-n-primary"
        }`}
      >
        Grouped
      </button>
      <button
        onClick={() => onChange("raw")}
        className={`px-2 py-0.5 ${
          value === "raw" ? "bg-n-raised text-n-display" : "text-n-secondary hover:text-n-primary"
        }`}
      >
        Raw
      </button>
    </div>
  );
}

function GroupBlock({ group }: { group: Group }) {
  return (
    <div className="rounded-compact border border-n-border bg-n-raised px-3 py-2">
      <div className="flex items-baseline justify-between gap-3">
        <div className="label">{group.title}</div>
        <div className="font-mono text-[10px] text-n-disabled">{group.hint}</div>
      </div>
      <div className="mt-1.5 flex flex-wrap gap-1.5">
        {group.tokens.map((t, i) => (
          <TokenChip key={i} token={t} />
        ))}
      </div>
    </div>
  );
}

function TokenChip({ token }: { token: Token }) {
  if (!token.key) {
    return (
      <span className="rounded-pill border border-n-border-vis px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.06em] text-n-secondary">
        {token.raw}
      </span>
    );
  }
  return (
    <span
      className="rounded-pill border border-n-border-vis px-2 py-0.5 font-mono text-[10px] tracking-[0.04em] text-n-primary"
      title={token.raw}
    >
      <span className="text-n-secondary">{token.key}</span>
      <span className="text-n-disabled">=</span>
      <span className="text-n-display">{token.value}</span>
    </span>
  );
}
