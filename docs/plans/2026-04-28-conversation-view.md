# Conversation View Implementation Plan (PR 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the EXPLAIN NEW page's right pane with a chat-style renderer that consumes `/v1/sessions/{id}/conversation`, folds consecutive agent tool-loop steps into one expandable group, and inlines a compact decision badge on every captured assistant bubble — with graceful fallback to the existing per-turn list when content capture is off.

**Architecture:** A new `conversation/` component family lives under `frontend/dashboard/src/components/`. `ExplainerNew` becomes a thin shell that picks between `<ConversationView>` and `<TurnListView>` based on `content_available`. Pure grouping logic is extracted into `groupMessages.ts` and tested with Node 20's built-in test runner — no new test framework is added.

**Tech Stack:** React 18, TypeScript, Vite, Tailwind. New dependency: `react-markdown` (assistant text rendering).

**Spec:** `docs/specs/2026-04-28-conversation-view-design.md`

---

## File Structure

| Path | Status | Responsibility |
|---|---|---|
| `frontend/dashboard/src/api.ts` | modify | Add `Conversation`, `ConversationMessage`, `ConversationToolCall`, `DecisionCard` types + `fetchConversation(sessionId)`. |
| `frontend/dashboard/package.json` | modify | Add `react-markdown` dep. |
| `frontend/dashboard/src/components/conversation/groupMessages.ts` | **new** | Pure function `group()` — folds tool-loop, inserts compact-break markers. |
| `frontend/dashboard/src/components/conversation/groupMessages.test.mjs` | **new** | Node `node:test` cases for the grouping logic. |
| `frontend/dashboard/src/components/conversation/MarkdownContent.tsx` | **new** | Thin `react-markdown` wrapper with project styling. |
| `frontend/dashboard/src/components/conversation/DecisionDetail.tsx` | **new** | Full expanded decision card (model header, metrics, route reasoning, transport, attempts, tags, error). Takes a `DecisionCard`. |
| `frontend/dashboard/src/components/conversation/DecisionBadge.tsx` | **new** | Compact `model · tier · cost · latency ▾` pill that toggles `<DecisionDetail>`. |
| `frontend/dashboard/src/components/conversation/MessageBubble.tsx` | **new** | Renders one user / captured-assistant / pre-capture-assistant / tool_result bubble. |
| `frontend/dashboard/src/components/conversation/ToolStepGroup.tsx` | **new** | Collapsed/expanded "AGENT THOUGHT FOR N STEPS" card. |
| `frontend/dashboard/src/components/conversation/ConversationView.tsx` | **new** | Top-level chat renderer; polls the endpoint; calls `group()`; handles loading/error. |
| `frontend/dashboard/src/components/conversation/TurnListView.tsx` | **new** | Extracted fallback (current `ExplainerNew` right-pane behavior). |
| `frontend/dashboard/src/components/ExplainerNew.tsx` | modify | Trim to: left session list + right-pane router (Conversation vs TurnList). |

---

## Task 1: Add Conversation Types and `fetchConversation` to `api.ts`

**Files:**
- Modify: `frontend/dashboard/src/api.ts`

- [ ] **Step 1.1: Open `api.ts` and find the existing trace types**

Confirm the file already exports `TraceAttempt` and `TraceRecord` (around line 383+). Append the new types and helper at the END of the file, after the last export.

- [ ] **Step 1.2: Append types and `fetchConversation`**

```ts
// === conversation view (PR 2) ===

export interface DecisionCard {
  model: string;
  decision_tier: string;
  served_quality: string;
  capability_lane: string;
  raw_confidence: number;
  latency_us: number;
  estimated_cost: number;
  route_reasoning: string;
  feature_tags: string[];
  constraint_tags: string[];
  hint_tags: string[];
  transport: string;
  transport_reason: string;
  attempts_payload: TraceAttempt[];
  fallback_reason: string;
}

export interface ConversationToolCall {
  id: string;
  name: string;
  input: Record<string, unknown> | string;
}

export interface ConversationMessage {
  role: "user" | "assistant" | "tool_result";
  text: string;
  tool_calls?: ConversationToolCall[];
  tool_use_id?: string;
  ts?: number | null;
  request_id?: string | null;
  from_request_id?: string;
  decision?: DecisionCard | null;
}

export interface Conversation {
  session_id: string;
  turn_count: number;
  content_available: boolean;
  compact_breaks: number[];
  messages: ConversationMessage[];
}

export const fetchConversation = (sessionId: string) =>
  get<Conversation>(`/v1/sessions/${encodeURIComponent(sessionId)}/conversation`);
```

- [ ] **Step 1.3: Verify the build still type-checks**

Run from `frontend/dashboard/`:
```
pnpm tsc -b
```
or whichever package manager the project uses (check `package.json` for `packageManager` or which lockfile is present — this project uses npm: there's `package-lock.json`).

Use:
```
cd frontend/dashboard && npx tsc -b
```

Expected: no errors.

- [ ] **Step 1.4: Commit**

```
git add frontend/dashboard/src/api.ts
git commit -m "Add Conversation types and fetchConversation API helper"
```

---

## Task 2: Add `react-markdown` Dependency

**Files:**
- Modify: `frontend/dashboard/package.json`
- Modify: `frontend/dashboard/package-lock.json` (auto via npm)

- [ ] **Step 2.1: Install**

```
cd frontend/dashboard && npm install react-markdown
```

- [ ] **Step 2.2: Verify version**

Confirm `react-markdown` lands in `dependencies` (not `devDependencies`). Open `package.json` and check.

Expected entry: `"react-markdown": "^9.x"` or similar. The exact version is whatever npm picks; don't pin manually.

- [ ] **Step 2.3: Smoke-import in a throwaway file** (skip if confident)

Optional: `npx tsc -b` from the dashboard dir. Should still pass.

- [ ] **Step 2.4: Commit**

```
git add frontend/dashboard/package.json frontend/dashboard/package-lock.json
git commit -m "Add react-markdown dep for assistant message rendering"
```

---

## Task 3: `groupMessages` Pure Function (TDD)

**Files:**
- Create: `frontend/dashboard/src/components/conversation/groupMessages.ts`
- Create: `frontend/dashboard/src/components/conversation/groupMessages.test.mjs`

This is the only piece of logic in PR 2 worth automating. Use Node 20's built-in `node:test` runner — no new framework added.

- [ ] **Step 3.1: Write the failing test file**

Create `frontend/dashboard/src/components/conversation/groupMessages.test.mjs` exactly:

```js
import { test } from "node:test";
import assert from "node:assert/strict";

import { group } from "./groupMessages.js";

const userMsg = (text) => ({ role: "user", text });
const asstText = (text, decision = { model: "m" }) => ({
  role: "assistant",
  text,
  tool_calls: [],
  decision,
});
const asstCall = (tools, decision = { model: "m" }) => ({
  role: "assistant",
  text: "",
  tool_calls: tools.map((name) => ({ id: name, name, input: {} })),
  decision,
});
const toolRes = (text) => ({ role: "tool_result", text, tool_use_id: "tu_x" });

test("bare conversation: user + assistant text", () => {
  const out = group({
    messages: [userMsg("hi"), asstText("hello")],
    compact_breaks: [],
  });
  assert.equal(out.length, 2);
  assert.equal(out[0].type, "message");
  assert.equal(out[1].type, "message");
});

test("single agent-loop cycle becomes one tool_group + final assistant", () => {
  const out = group({
    messages: [
      userMsg("do thing"),
      asstCall(["Read"]),
      toolRes("file contents"),
      asstText("done"),
    ],
    compact_breaks: [],
  });
  assert.equal(out.length, 3);
  assert.equal(out[0].type, "message");
  assert.equal(out[1].type, "tool_group");
  assert.equal(out[1].steps.length, 1);
  assert.equal(out[1].steps[0].results.length, 1);
  assert.equal(out[2].type, "message");
});

test("multi-step agent loop folds into one group", () => {
  const out = group({
    messages: [
      userMsg("question"),
      asstCall(["Read"]),
      toolRes("a"),
      asstCall(["Read", "Bash"]),
      toolRes("b"),
      toolRes("c"),
      asstCall(["Read"]),
      toolRes("d"),
      asstText("answer"),
    ],
    compact_breaks: [],
  });
  const groups = out.filter((it) => it.type === "tool_group");
  assert.equal(groups.length, 1);
  assert.equal(groups[0].steps.length, 3);
});

test("tool_result split by intermediate assistant text → two groups", () => {
  const out = group({
    messages: [
      userMsg("q"),
      asstCall(["Read"]),
      toolRes("a"),
      asstText("interim"),
      asstCall(["Bash"]),
      toolRes("b"),
      asstText("final"),
    ],
    compact_breaks: [],
  });
  const groups = out.filter((it) => it.type === "tool_group");
  assert.equal(groups.length, 2);
});

test("compact_breaks inserts a compact_break item after the k-th captured assistant", () => {
  // 3 captured assistants. compact_breaks: [2] — break after the 2nd.
  const out = group({
    messages: [
      userMsg("q1"),
      asstText("a1", { model: "m1" }),
      userMsg("q2"),
      asstText("a2", { model: "m2" }),
      userMsg("q3"),
      asstText("a3", { model: "m3" }),
    ],
    compact_breaks: [2],
  });
  const breakIdx = out.findIndex((it) => it.type === "compact_break");
  assert.notEqual(breakIdx, -1);
  // The item just before the break should be the assistant "a2".
  const before = out[breakIdx - 1];
  assert.equal(before.type, "message");
  assert.equal(before.message.text, "a2");
});

test("pre-capture assistant (decision === null) does not increment the compact_break counter", () => {
  const out = group({
    messages: [
      userMsg("q1"),
      asstText("pre-capture reply", null), // pre-capture, decision=null
      userMsg("q2"),
      asstText("captured 1", { model: "m1" }),
      userMsg("q3"),
      asstText("captured 2", { model: "m2" }),
    ],
    compact_breaks: [1],
  });
  const breakIdx = out.findIndex((it) => it.type === "compact_break");
  // Break should land after "captured 1" (1st captured), not after "pre-capture reply".
  const before = out[breakIdx - 1];
  assert.equal(before.message.text, "captured 1");
});

test("pre-capture assistant tool-call still folds into a tool_group", () => {
  const out = group({
    messages: [
      userMsg("q"),
      asstCall(["Read"], null), // pre-capture decision=null
      toolRes("a"),
      asstText("done"),
    ],
    compact_breaks: [],
  });
  const grp = out.find((it) => it.type === "tool_group");
  assert.ok(grp);
  assert.equal(grp.steps.length, 1);
  assert.equal(grp.steps[0].assistant.decision, null);
});
```

- [ ] **Step 3.2: Verify tests fail (module missing)**

Run from repo root:
```
node --test frontend/dashboard/src/components/conversation/groupMessages.test.mjs
```
Expected: `ERR_MODULE_NOT_FOUND` for `./groupMessages.js`. The test file imports `.js`, not `.ts`, because `node --test` runs raw JS — we'll dual-publish via TS compilation OR write the source as a `.ts` that compiles to a sibling `.js` via the project's `tsc -b` step.

Simpler approach: write a parallel `.mjs` source so the test can import it directly without compilation. We do that next.

- [ ] **Step 3.3: Create `groupMessages.ts` (TypeScript, used by React)**

Create `frontend/dashboard/src/components/conversation/groupMessages.ts`:

```ts
import type { ConversationMessage } from "../../api";

export interface ToolStep {
  assistant: ConversationMessage;
  results: ConversationMessage[];
}

export type ConversationItem =
  | { type: "message"; message: ConversationMessage }
  | { type: "tool_group"; steps: ToolStep[] }
  | { type: "compact_break"; atTurnIndex: number };

export interface GroupInput {
  messages: ConversationMessage[];
  compact_breaks: number[];
}

export function group(input: GroupInput): ConversationItem[] {
  const out: ConversationItem[] = [];
  let buffer: ToolStep[] = [];
  let capturedAsstCount = 0;
  // Pending compact-break thresholds, smallest-first.
  const pending = [...input.compact_breaks].sort((a, b) => a - b);

  const flushBuffer = () => {
    if (buffer.length > 0) {
      out.push({ type: "tool_group", steps: buffer });
      buffer = [];
    }
  };

  for (const m of input.messages) {
    const isAsstToolOnly =
      m.role === "assistant" &&
      Array.isArray(m.tool_calls) &&
      m.tool_calls.length > 0 &&
      (m.text === "" || m.text == null);

    if (isAsstToolOnly) {
      buffer.push({ assistant: m, results: [] });
    } else if (m.role === "tool_result" && buffer.length > 0) {
      buffer[buffer.length - 1].results.push(m);
    } else {
      flushBuffer();
      out.push({ type: "message", message: m });
    }

    // Increment captured-assistant counter and emit any compact_breaks.
    if (m.role === "assistant" && m.decision != null) {
      capturedAsstCount += 1;
      while (pending.length > 0 && pending[0] <= capturedAsstCount) {
        out.push({ type: "compact_break", atTurnIndex: pending.shift()! });
      }
    }
  }
  flushBuffer();
  return out;
}
```

- [ ] **Step 3.4: Create the matching `.mjs` source for `node:test`**

The test file imports `./groupMessages.js`. We provide a hand-written `.mjs` mirror so we don't need a build step for the test. Create `frontend/dashboard/src/components/conversation/groupMessages.js`:

```js
// Hand-mirrored from groupMessages.ts so node --test can import it
// without a compile step. Keep the two in sync — the .ts is the source
// of truth for the React build; this .js is the source of truth for tests.

export function group(input) {
  const out = [];
  let buffer = [];
  let capturedAsstCount = 0;
  const pending = [...input.compact_breaks].sort((a, b) => a - b);

  const flushBuffer = () => {
    if (buffer.length > 0) {
      out.push({ type: "tool_group", steps: buffer });
      buffer = [];
    }
  };

  for (const m of input.messages) {
    const isAsstToolOnly =
      m.role === "assistant" &&
      Array.isArray(m.tool_calls) &&
      m.tool_calls.length > 0 &&
      (m.text === "" || m.text == null);

    if (isAsstToolOnly) {
      buffer.push({ assistant: m, results: [] });
    } else if (m.role === "tool_result" && buffer.length > 0) {
      buffer[buffer.length - 1].results.push(m);
    } else {
      flushBuffer();
      out.push({ type: "message", message: m });
    }

    if (m.role === "assistant" && m.decision != null) {
      capturedAsstCount += 1;
      while (pending.length > 0 && pending[0] <= capturedAsstCount) {
        out.push({ type: "compact_break", atTurnIndex: pending.shift() });
      }
    }
  }
  flushBuffer();
  return out;
}
```

Add a `.gitignore` rule? No — both files are tracked sources, just keep them in sync.

- [ ] **Step 3.5: Run tests**

```
node --test frontend/dashboard/src/components/conversation/groupMessages.test.mjs
```

Expected: all 7 tests pass.

- [ ] **Step 3.6: Verify TypeScript build still passes**

```
cd frontend/dashboard && npx tsc -b
```

Expected: no errors. The `.js` sibling shouldn't conflict with the `.ts` because TS doesn't emit a `.js` for `groupMessages.ts` (we'd hit a duplicate-export warning if it did). If it does, add `groupMessages.js` to the `tsconfig.json`'s `exclude` array.

- [ ] **Step 3.7: Commit**

```
git add frontend/dashboard/src/components/conversation/groupMessages.ts \
        frontend/dashboard/src/components/conversation/groupMessages.js \
        frontend/dashboard/src/components/conversation/groupMessages.test.mjs
git commit -m "Add groupMessages tool-loop folder with tests"
```

---

## Task 4: `MarkdownContent` Wrapper

**Files:**
- Create: `frontend/dashboard/src/components/conversation/MarkdownContent.tsx`

- [ ] **Step 4.1: Create the file**

```tsx
import ReactMarkdown from "react-markdown";

interface Props {
  text: string;
}

export default function MarkdownContent({ text }: Props) {
  return (
    <div className="prose-chat text-[14px] leading-relaxed text-n-primary">
      <ReactMarkdown
        disallowedElements={["html", "img"]}
        unwrapDisallowed
        components={{
          code({ className, children, ...rest }) {
            const isBlock = /language-/.test(className ?? "");
            if (isBlock) {
              return (
                <pre className="my-2 overflow-x-auto rounded-compact border border-n-border bg-n-raised px-3 py-2 font-mono text-[12px] text-n-primary">
                  <code {...rest}>{children}</code>
                </pre>
              );
            }
            return (
              <code className="rounded bg-n-raised px-1 py-0.5 font-mono text-[12px] text-n-display">
                {children}
              </code>
            );
          },
          a({ children, href }) {
            return (
              <a
                href={href}
                target="_blank"
                rel="noopener noreferrer"
                className="text-n-accent underline"
              >
                {children}
              </a>
            );
          },
          ul({ children }) {
            return <ul className="my-1 ml-5 list-disc">{children}</ul>;
          },
          ol({ children }) {
            return <ol className="my-1 ml-5 list-decimal">{children}</ol>;
          },
          h1({ children }) {
            return <h1 className="mt-3 mb-1 text-[16px] font-semibold text-n-display">{children}</h1>;
          },
          h2({ children }) {
            return <h2 className="mt-3 mb-1 text-[15px] font-semibold text-n-display">{children}</h2>;
          },
          h3({ children }) {
            return <h3 className="mt-2 mb-1 text-[14px] font-semibold text-n-display">{children}</h3>;
          },
          h4({ children }) {
            return <h4 className="mt-2 mb-1 text-[13px] font-semibold text-n-display">{children}</h4>;
          },
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
```

- [ ] **Step 4.2: Verify it compiles**

```
cd frontend/dashboard && npx tsc -b
```

Expected: no errors.

- [ ] **Step 4.3: Commit**

```
git add frontend/dashboard/src/components/conversation/MarkdownContent.tsx
git commit -m "Add MarkdownContent wrapper around react-markdown"
```

---

## Task 5: `DecisionDetail` (Expanded Card Content)

**Files:**
- Create: `frontend/dashboard/src/components/conversation/DecisionDetail.tsx`

This is the chat-path equivalent of the existing `TurnDecision` block in `ExplainerNew.tsx`. We don't share code with `TurnDecision` because the input types differ (`DecisionCard` vs `TraceRecord`); the duplication is ~80 lines, acceptable. `TurnDecision` will continue to live in the legacy fallback (Task 9).

- [ ] **Step 5.1: Create the component**

```tsx
import type { DecisionCard, TraceAttempt } from "../../api";

const TIER_NAMES: Record<string, string> = {
  SIMPLE: "LOW",
  MEDIUM: "MID",
  COMPLEX: "HIGH",
  REASONING: "HIGH",
  low: "LOW",
  mid: "MID",
  mid_high: "MID_HIGH",
  high: "HIGH",
};

function normTier(t?: string) {
  return TIER_NAMES[t || ""] || (t || "—").toUpperCase();
}

function shortModel(model?: string) {
  return (model || "").split("/").pop() || model || "—";
}

function prettyTransport(transport?: string) {
  switch (transport) {
    case "anthropic-messages":
      return "Anthropic Messages";
    case "openai-responses":
      return "OpenAI Responses";
    case "openai-chat":
      return "OpenAI Chat";
    default:
      return transport || "—";
  }
}

function prettyQuality(value?: string) {
  if (!value) return "—";
  return value.replace(/_/g, " ").toUpperCase();
}

function prettyLane(value?: string) {
  if (!value) return "—";
  return value.replace(/-/g, " ").replace(/_/g, " ").toUpperCase();
}

export default function DecisionDetail({ decision }: { decision: DecisionCard }) {
  return (
    <div className="mt-2 space-y-4 rounded-compact border border-n-border bg-n-raised px-4 py-3">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <div className="label">ROUTED TO</div>
          <div className="mt-1 font-display text-[20px] leading-none tracking-tight text-n-display">
            {shortModel(decision.model)}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2 font-mono text-[11px] text-n-secondary">
          <Badge>{normTier(decision.decision_tier)}</Badge>
          {decision.served_quality ? <Badge>{prettyQuality(decision.served_quality)}</Badge> : null}
          {decision.capability_lane ? <Badge>{prettyLane(decision.capability_lane)}</Badge> : null}
        </div>
      </div>

      <div className="grid grid-cols-3 gap-3">
        <Mini label="CONFIDENCE" value={decision.raw_confidence ? `${Math.round(decision.raw_confidence * 100)}%` : "—"} />
        <Mini label="LATENCY" value={`${(decision.latency_us / 1000).toFixed(1)}ms`} />
        <Mini label="COST" value={`$${decision.estimated_cost.toFixed(4)}`} />
      </div>

      <div>
        <div className="label mb-1">ROUTE REASONING</div>
        <div className="text-[12px] text-n-primary">
          {decision.route_reasoning || "—"}
        </div>
        {decision.fallback_reason ? (
          <div className="mt-1 font-mono text-[11px] text-n-warning">
            Fallback: {decision.fallback_reason}
          </div>
        ) : null}
      </div>

      <div>
        <div className="flex items-center justify-between gap-3">
          <div className="label">TRANSPORT</div>
        </div>
        <div className="mt-2 grid grid-cols-12 gap-2">
          <div className="col-span-5 rounded-compact border border-n-border px-2 py-2">
            <div className="label">SELECTED</div>
            <div className="mt-1 font-mono text-[12px] font-semibold text-n-display">{prettyTransport(decision.transport)}</div>
          </div>
          <div className="col-span-7 rounded-compact border border-n-border px-2 py-2">
            <div className="label">REASON</div>
            <div className="mt-1 text-[11px] text-n-primary">
              {decision.transport_reason || "—"}
            </div>
          </div>
        </div>
      </div>

      {decision.attempts_payload && decision.attempts_payload.length > 0 ? (
        <div>
          <div className="label mb-1">ATTEMPT CHAIN ({decision.attempts_payload.length})</div>
          <div className="space-y-1">
            {decision.attempts_payload.map((a, i) => (
              <AttemptRow key={`${i}-${a.selected_model}`} attempt={a} />
            ))}
          </div>
        </div>
      ) : null}

      {(decision.feature_tags?.length || decision.constraint_tags?.length || decision.hint_tags?.length) ? (
        <div>
          <div className="label mb-1">TAGS</div>
          <TagRow title="FEATURE" items={decision.feature_tags} />
          <TagRow title="CONSTRAINT" items={decision.constraint_tags} />
          <TagRow title="HINT" items={decision.hint_tags} />
        </div>
      ) : null}
    </div>
  );
}

function AttemptRow({ attempt }: { attempt: TraceAttempt }) {
  const dot = attempt.success ? "bg-n-success" : attempt.blocked ? "bg-n-warning" : "bg-n-accent";
  return (
    <div className="rounded-compact border border-n-border px-2 py-1.5">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <span className={`h-1.5 w-1.5 rounded-full ${dot}`} />
          <span className="font-mono text-[11px] text-n-display">{shortModel(attempt.selected_model)}</span>
        </div>
        <span className="font-mono text-[11px] text-n-secondary">
          {attempt.success ? "OK" : attempt.blocked ? "BLOCKED" : `HTTP ${attempt.status_code || "—"}`}
        </span>
      </div>
    </div>
  );
}

function TagRow({ title, items }: { title: string; items: string[] | undefined }) {
  if (!items || items.length === 0) return null;
  return (
    <div className="mt-1 flex flex-wrap items-center gap-1.5">
      <span className="font-mono text-[10px] text-n-secondary">{title}</span>
      {items.map((it) => (
        <Badge key={`${title}-${it}`}>{it}</Badge>
      ))}
    </div>
  );
}

function Badge({ children }: { children: React.ReactNode }) {
  return (
    <span className="rounded-pill border border-n-border-vis px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.06em] text-n-secondary">
      {children}
    </span>
  );
}

function Mini({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-compact border border-n-border px-2 py-1.5">
      <div className="label">{label}</div>
      <div className="mt-0.5 font-mono text-[12px] font-semibold tracking-tight text-n-display">{value}</div>
    </div>
  );
}
```

- [ ] **Step 5.2: Verify build**

```
cd frontend/dashboard && npx tsc -b
```

Expected: no errors.

- [ ] **Step 5.3: Commit**

```
git add frontend/dashboard/src/components/conversation/DecisionDetail.tsx
git commit -m "Add DecisionDetail expanded card for chat view"
```

---

## Task 6: `DecisionBadge` (Compact Pill + Toggle)

**Files:**
- Create: `frontend/dashboard/src/components/conversation/DecisionBadge.tsx`

- [ ] **Step 6.1: Create the file**

```tsx
import { useState } from "react";
import type { DecisionCard } from "../../api";
import DecisionDetail from "./DecisionDetail";

function shortModel(model?: string) {
  return (model || "").split("/").pop() || model || "—";
}

function shortTier(tier?: string) {
  const t = (tier || "").toUpperCase();
  if (t === "SIMPLE") return "LOW";
  if (t === "MEDIUM") return "MID";
  if (t === "COMPLEX") return "HIGH";
  if (t === "REASONING") return "HIGH";
  return t || "—";
}

export default function DecisionBadge({ decision }: { decision: DecisionCard }) {
  const [open, setOpen] = useState(false);
  const latencyMs = (decision.latency_us / 1000).toFixed(0);
  const cost = `$${decision.estimated_cost.toFixed(4)}`;
  return (
    <div className="w-full">
      <button
        onClick={() => setOpen((v) => !v)}
        className="row-hover flex w-full items-center gap-2 rounded-pill border border-n-border-vis bg-n-surface px-2 py-0.5 font-mono text-[11px] text-n-secondary"
      >
        <span className="text-n-display">{shortModel(decision.model)}</span>
        <span className="text-n-disabled">·</span>
        <span>{shortTier(decision.decision_tier)}</span>
        <span className="text-n-disabled">·</span>
        <span>{cost}</span>
        <span className="text-n-disabled">·</span>
        <span>{latencyMs}ms</span>
        <span className="ml-auto text-n-disabled">{open ? "▾" : "▸"}</span>
      </button>
      {open ? <DecisionDetail decision={decision} /> : null}
    </div>
  );
}
```

- [ ] **Step 6.2: Build check**

```
cd frontend/dashboard && npx tsc -b
```

Expected: no errors.

- [ ] **Step 6.3: Commit**

```
git add frontend/dashboard/src/components/conversation/DecisionBadge.tsx
git commit -m "Add DecisionBadge compact pill with click-to-expand"
```

---

## Task 7: `MessageBubble`

**Files:**
- Create: `frontend/dashboard/src/components/conversation/MessageBubble.tsx`

- [ ] **Step 7.1: Create the file**

```tsx
import { useState } from "react";
import type { ConversationMessage } from "../../api";
import DecisionBadge from "./DecisionBadge";
import MarkdownContent from "./MarkdownContent";

const SYSTEM_REMINDER_PATTERN = /^\s*(<(system-reminder|command-name|local-command-stdout|command-message|command-args)\b[^>]*>)/i;

function formatRelativeTs(ts?: number | null): string {
  if (ts == null) return "";
  const seconds = Math.max(0, Date.now() / 1000 - ts);
  if (seconds < 60) return `${Math.floor(seconds)}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

export default function MessageBubble({ message }: { message: ConversationMessage }) {
  if (message.role === "user") {
    return <UserBubble message={message} />;
  }
  if (message.role === "assistant") {
    return <AssistantBubble message={message} />;
  }
  return <ToolResultBubble message={message} />;
}

function UserBubble({ message }: { message: ConversationMessage }) {
  const [showWrapper, setShowWrapper] = useState(false);
  // Detect Claude Code's <system-reminder> / <command-*> wrappers and offer
  // a collapsed pill so they don't dominate the conversation.
  const hasWrapper = SYSTEM_REMINDER_PATTERN.test(message.text);
  // If the entire message is a wrapper, body is empty.
  let displayText = message.text;
  let wrapperText = "";
  if (hasWrapper) {
    // Strip the leading wrapper(s) up to the last closing `>` of the first
    // top-level wrapper. Heuristic: find first `</xxx>` matching the opening.
    const m = message.text.match(/^([\s\S]*?<\/(system-reminder|command-name|local-command-stdout|command-message|command-args)>\s*)/i);
    if (m) {
      wrapperText = m[1];
      displayText = message.text.slice(m[1].length);
    }
  }

  return (
    <div className="rounded-card border border-n-border bg-n-surface p-4">
      <div className="flex items-baseline justify-between gap-3">
        <div className="label">USER</div>
        <div className="font-mono text-[10px] text-n-disabled">
          {formatRelativeTs(message.ts)}
        </div>
      </div>
      {wrapperText ? (
        <button
          onClick={() => setShowWrapper((v) => !v)}
          className="mt-2 inline-flex items-center gap-1.5 rounded-pill border border-n-border-vis px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.06em] text-n-secondary"
        >
          <span>⚙ {showWrapper ? "hide" : "show"} system reminder</span>
        </button>
      ) : null}
      {showWrapper ? (
        <pre className="mt-2 overflow-x-auto whitespace-pre-wrap rounded-compact border border-n-border bg-n-raised px-3 py-2 font-mono text-[11px] text-n-secondary">
          {wrapperText}
        </pre>
      ) : null}
      {displayText.trim() ? (
        <div className="mt-2 whitespace-pre-wrap text-[14px] leading-relaxed text-n-primary">
          {displayText}
        </div>
      ) : null}
    </div>
  );
}

function AssistantBubble({ message }: { message: ConversationMessage }) {
  const isPreCapture = message.decision == null;
  return (
    <div
      className={`rounded-card border border-n-border bg-n-surface p-4 ${
        isPreCapture ? "opacity-60" : ""
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="label">ASSISTANT</div>
        <div className="flex shrink-0 items-baseline gap-2 font-mono text-[10px] text-n-disabled">
          {message.ts ? <span>{formatRelativeTs(message.ts)}</span> : null}
        </div>
      </div>
      <div className="mt-2">
        {isPreCapture ? (
          <span className="rounded-pill border border-n-border-vis px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.06em] text-n-disabled">
            ⟪PRE-CAPTURE⟫
          </span>
        ) : (
          <DecisionBadge decision={message.decision!} />
        )}
      </div>
      {message.text ? (
        <div className="mt-2">
          <MarkdownContent text={message.text} />
        </div>
      ) : null}
    </div>
  );
}

function ToolResultBubble({ message }: { message: ConversationMessage }) {
  const [expanded, setExpanded] = useState(false);
  const lines = (message.text || "").split("\n");
  const isLong = lines.length > 5 || (message.text || "").length > 1000;
  const preview = isLong && !expanded ? lines.slice(0, 5).join("\n") : message.text;
  return (
    <div className="rounded-compact border border-n-border bg-n-raised px-3 py-2">
      <div className="flex items-baseline justify-between gap-3">
        <div className="label">TOOL RESULT</div>
        {message.tool_use_id ? (
          <div className="font-mono text-[10px] text-n-disabled">
            {message.tool_use_id.slice(-8)}
          </div>
        ) : null}
      </div>
      <pre className="mt-1 overflow-x-auto whitespace-pre-wrap font-mono text-[11px] text-n-primary">
        {preview}
      </pre>
      {isLong ? (
        <button
          onClick={() => setExpanded((v) => !v)}
          className="mt-1 font-mono text-[10px] uppercase tracking-[0.06em] text-n-secondary hover:text-n-primary"
        >
          {expanded ? "show less" : "show all"}
        </button>
      ) : null}
    </div>
  );
}
```

- [ ] **Step 7.2: Build check**

```
cd frontend/dashboard && npx tsc -b
```

Expected: no errors.

- [ ] **Step 7.3: Commit**

```
git add frontend/dashboard/src/components/conversation/MessageBubble.tsx
git commit -m "Add MessageBubble for user / assistant / tool_result variants"
```

---

## Task 8: `ToolStepGroup`

**Files:**
- Create: `frontend/dashboard/src/components/conversation/ToolStepGroup.tsx`

- [ ] **Step 8.1: Create the file**

```tsx
import { useState } from "react";
import type { ToolStep } from "./groupMessages";
import DecisionBadge from "./DecisionBadge";
import MessageBubble from "./MessageBubble";

function aggregateToolNames(steps: ToolStep[]): Record<string, number> {
  const acc: Record<string, number> = {};
  for (const s of steps) {
    for (const c of s.assistant.tool_calls ?? []) {
      acc[c.name] = (acc[c.name] || 0) + 1;
    }
  }
  return acc;
}

function distinctModels(steps: ToolStep[]): { captured: number; preCapture: number } {
  const captured = new Set<string>();
  let preCapture = 0;
  for (const s of steps) {
    if (s.assistant.decision == null) {
      preCapture += 1;
    } else {
      captured.add(s.assistant.decision.model);
    }
  }
  return { captured: captured.size, preCapture };
}

export default function ToolStepGroup({ steps }: { steps: ToolStep[] }) {
  const [open, setOpen] = useState(false);
  const tools = aggregateToolNames(steps);
  const toolSummary = Object.entries(tools)
    .map(([name, n]) => `${name}×${n}`)
    .join(" ");
  const { captured, preCapture } = distinctModels(steps);
  const modelSummary = preCapture > 0
    ? `${captured} models · ${preCapture} pre-capture`
    : `${captured} model${captured === 1 ? "" : "s"}`;

  return (
    <div className="rounded-card border border-n-border bg-n-surface">
      <button
        onClick={() => setOpen((v) => !v)}
        className="row-hover flex w-full items-center justify-between gap-3 px-4 py-3 text-left"
      >
        <div className="flex items-center gap-2">
          <span className="font-mono text-[10px] text-n-disabled">{open ? "▾" : "▸"}</span>
          <span className="label">AGENT THOUGHT FOR {steps.length} STEP{steps.length === 1 ? "" : "S"}</span>
        </div>
        <div className="flex items-center gap-3 font-mono text-[11px] text-n-secondary">
          <span className="truncate max-w-[260px]">{toolSummary}</span>
          <span className="text-n-disabled">·</span>
          <span>{modelSummary}</span>
        </div>
      </button>
      {open ? (
        <div className="border-t border-n-border px-4 py-3 space-y-3">
          {steps.map((step, i) => (
            <div key={i} className="rounded-compact border border-n-border bg-n-raised px-3 py-3 space-y-2">
              <div className="flex items-baseline justify-between gap-3">
                <div className="label">STEP {i + 1}</div>
                {step.assistant.decision ? (
                  <div className="min-w-0 max-w-[60%]">
                    <DecisionBadge decision={step.assistant.decision} />
                  </div>
                ) : (
                  <span className="rounded-pill border border-n-border-vis px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.06em] text-n-disabled">
                    ⟪PRE-CAPTURE⟫
                  </span>
                )}
              </div>
              {(step.assistant.tool_calls || []).map((c, j) => (
                <div key={j} className="font-mono text-[11px] text-n-primary">
                  <span className="text-n-secondary">tool_use:</span>{" "}
                  <span className="text-n-display">{c.name}</span>
                  <span className="text-n-secondary">(</span>
                  <span>{typeof c.input === "string" ? c.input : JSON.stringify(c.input)}</span>
                  <span className="text-n-secondary">)</span>
                </div>
              ))}
              {step.results.length > 0 ? (
                <div className="space-y-1">
                  {step.results.map((r, k) => (
                    <MessageBubble key={k} message={r} />
                  ))}
                </div>
              ) : null}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
```

- [ ] **Step 8.2: Build check**

```
cd frontend/dashboard && npx tsc -b
```

Expected: no errors.

- [ ] **Step 8.3: Commit**

```
git add frontend/dashboard/src/components/conversation/ToolStepGroup.tsx
git commit -m "Add ToolStepGroup folded agent-loop card"
```

---

## Task 9: `TurnListView` (Extracted Fallback)

**Files:**
- Create: `frontend/dashboard/src/components/conversation/TurnListView.tsx`

The current `ExplainerNew.tsx` right pane (the session header + list of `TurnRow` cards) becomes this component. We extract verbatim — same look, same behavior, just moved.

- [ ] **Step 9.1: Identify what to extract**

In `ExplainerNew.tsx`, these are the relevant pieces (read for line ranges, copy carefully):

- The `Session` interface (top of file).
- The `SessionDetail` function component.
- The `TurnRow` function component.
- The `TurnDecision` function component.
- The `AttemptRow`, `TagGroup`, `Badge`, `MiniMetric`, `EmptyPanel` helpers.
- The `groupSessions`, `relativeTime`, `normTier`, `shortModel`, `prettyTransport`, `prettyQuality`, `prettyLane`, `prettifySource` utility functions.

These are all referenced by `SessionDetail` directly or transitively.

- [ ] **Step 9.2: Create `TurnListView.tsx`**

The new component takes a `Session` (the type already in `ExplainerNew`) and renders the SessionDetail. Move ALL the helper components/functions listed above into this new file, then have `ExplainerNew.tsx` delegate to it. Concrete shape:

```tsx
import { type ReactNode, useState } from "react";
import type { TraceAttempt, TraceRecord } from "../../api";

// ===== Types =====

export interface Session {
  id: string;
  turns: TraceRecord[];
  firstTimestamp: number;
  lastTimestamp: number;
  totalCost: number;
  hasError: boolean;
  models: string[];
  tierCounts: Array<[string, number]>;
}

// ===== Top-level component =====

export default function TurnListView({ session }: { session: Session }) {
  const [expandedTurns, setExpandedTurns] = useState<Set<string>>(new Set());
  const toggleTurn = (rid: string) => {
    setExpandedTurns((prev) => {
      const next = new Set(prev);
      if (next.has(rid)) next.delete(rid);
      else next.add(rid);
      return next;
    });
  };

  return (
    <div className="space-y-6">
      <div className="rounded-card border border-n-border bg-n-surface p-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="label">SESSION</div>
            <div className="mt-2 font-display text-[28px] leading-tight tracking-tight text-n-display">
              {session.id}
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-2 font-mono text-[11px] text-n-secondary">
              <Badge>{session.turns.length} TURNS</Badge>
              {session.tierCounts.map(([t, n]) => (
                <Badge key={`tier-${t}`}>{`${t} × ${n}`}</Badge>
              ))}
              {session.hasError ? <Badge tone="error">HAS ERRORS</Badge> : null}
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3 min-w-[260px]">
            <MiniMetric label="MODELS" value={`${session.models.length}`} />
            <MiniMetric label="TOTAL COST" value={`$${session.totalCost.toFixed(4)}`} />
            <MiniMetric label="STARTED" value={relativeTime(session.firstTimestamp)} />
            <MiniMetric label="LAST" value={relativeTime(session.lastTimestamp)} />
          </div>
        </div>

        {session.models.length > 0 ? (
          <div className="mt-5 border-t border-n-border pt-4">
            <div className="label mb-2">MODELS USED</div>
            <div className="flex flex-wrap gap-2">
              {session.models.map((m) => (
                <Badge key={`model-${m}`}>{shortModel(m)}</Badge>
              ))}
            </div>
          </div>
        ) : null}
      </div>

      <div className="space-y-3">
        {session.turns.map((turn, idx) => (
          <TurnRow
            key={turn.request_id}
            index={idx + 1}
            turn={turn}
            isOpen={expandedTurns.has(turn.request_id)}
            onToggle={() => toggleTurn(turn.request_id)}
          />
        ))}
      </div>
    </div>
  );
}

// ===== Sub-components and helpers (verbatim from old ExplainerNew) =====

function TurnRow({
  index,
  turn,
  isOpen,
  onToggle,
}: {
  index: number;
  turn: TraceRecord;
  isOpen: boolean;
  onToggle: () => void;
}) {
  const isError = turn.status_code >= 400;
  return (
    <div className="rounded-card border border-n-border bg-n-surface">
      <button
        onClick={onToggle}
        className="row-hover w-full px-5 py-4 text-left"
      >
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <span className={`h-1.5 w-1.5 rounded-full ${isError ? "bg-n-accent" : "bg-n-success"}`} />
            <span className="font-mono text-[11px] uppercase tracking-[0.08em] text-n-secondary">
              TURN {index}
            </span>
            <span className="font-mono text-[11px] text-n-disabled">
              {normTier(turn.decision_tier || turn.tier)}
            </span>
          </div>
          <span className="font-mono text-[11px] text-n-disabled">
            {isOpen ? "▾" : "▸"}
          </span>
        </div>
        <div className="mt-2 truncate text-[14px] text-n-primary">
          {turn.prompt_preview || "[no preview]"}
        </div>
        <div className="mt-3 flex items-center justify-between gap-3 font-mono text-[11px] text-n-secondary">
          <span className="truncate">{shortModel(turn.model) || "—"}</span>
          <span>
            {prettyTransport(turn.transport)} · {(turn.latency_us / 1000).toFixed(1)}ms · {isError ? `ERR ${turn.status_code}` : `${turn.status_code}`}
          </span>
        </div>
      </button>
      {isOpen ? <TurnDecision turn={turn} /> : null}
    </div>
  );
}

function TurnDecision({ turn }: { turn: TraceRecord }) {
  const transport = {
    requested: prettyTransport(turn.requested_transport || turn.transport),
    selected: prettyTransport(turn.transport),
    source: prettifySource(turn.transport_preference_source),
    reason: turn.transport_reason || "No explicit transport reason recorded.",
  };

  return (
    <div className="border-t border-n-border px-5 py-5 space-y-5">
      <div>
        <div className="flex flex-wrap items-baseline justify-between gap-3">
          <div>
            <div className="label">ROUTED TO</div>
            <div className="mt-1 font-display text-[24px] leading-none tracking-tight text-n-display">
              {shortModel(turn.model) || "—"}
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2 font-mono text-[11px] text-n-secondary">
            <Badge>{normTier(turn.decision_tier || turn.tier)}</Badge>
            {turn.served_quality ? <Badge>{prettyQuality(turn.served_quality)}</Badge> : null}
            {turn.capability_lane ? <Badge>{prettyLane(turn.capability_lane)}</Badge> : null}
            <Badge>{(turn.method || "pool").toUpperCase()}</Badge>
            <Badge>{turn.streaming ? "STREAM" : "NON-STREAM"}</Badge>
            <Badge tone={turn.status_code >= 400 ? "error" : "default"}>
              {turn.status_code >= 400 ? `ERR ${turn.status_code}` : `HTTP ${turn.status_code}`}
            </Badge>
          </div>
        </div>

        <div className="mt-4 grid grid-cols-4 gap-3">
          <MiniMetric label="CONFIDENCE" value={turn.raw_confidence ? `${Math.round(turn.raw_confidence * 100)}%` : "—"} />
          <MiniMetric label="LATENCY" value={`${(turn.latency_us / 1000).toFixed(1)}ms`} />
          <MiniMetric label="COST" value={`$${turn.estimated_cost.toFixed(4)}`} />
          <MiniMetric label="REQ ID" value={turn.request_id} monoSmall />
        </div>
      </div>

      <div>
        <div className="label mb-2">ROUTE REASONING</div>
        <div className="text-[13px] text-n-primary">
          {turn.route_reasoning || "No route reasoning recorded."}
        </div>
        {turn.fallback_reason ? (
          <div className="mt-2 font-mono text-[11px] text-n-warning">
            Fallback: {turn.fallback_reason}
          </div>
        ) : null}
      </div>

      <div>
        <div className="flex items-center justify-between gap-3">
          <div className="label">TRANSPORT</div>
          <div className="font-mono text-[11px] text-n-secondary">{transport.source}</div>
        </div>
        <div className="mt-3 grid grid-cols-12 gap-3">
          <div className="col-span-5 rounded-compact border border-n-border px-3 py-3">
            <div className="label">REQUESTED</div>
            <div className="mt-1 font-mono text-[14px] font-semibold text-n-display">{transport.requested}</div>
          </div>
          <div className="col-span-2 flex items-center justify-center">
            <div className="font-display text-[20px] text-n-display">→</div>
          </div>
          <div className="col-span-5 rounded-compact border border-n-border px-3 py-3">
            <div className="label">SERVED</div>
            <div className="mt-1 font-mono text-[14px] font-semibold text-n-display">{transport.selected}</div>
          </div>
        </div>
        <div className="mt-3 text-[13px] text-n-primary">{transport.reason}</div>
      </div>

      <div>
        <div className="flex items-center justify-between gap-3">
          <div className="label">ATTEMPT CHAIN</div>
          <div className="font-mono text-[11px] text-n-secondary">{turn.attempts_payload.length} attempts</div>
        </div>
        <div className="mt-3 space-y-2">
          {turn.attempts_payload.length === 0 ? (
            <div className="font-mono text-[11px] text-n-disabled">[NO ATTEMPTS RECORDED]</div>
          ) : (
            turn.attempts_payload.map((attempt) => (
              <AttemptRow key={`${attempt.attempt_index}-${attempt.selected_model}`} attempt={attempt} />
            ))
          )}
        </div>
      </div>

      {(turn.feature_tags?.length || turn.constraint_tags?.length || turn.hint_tags?.length) ? (
        <div>
          <div className="label mb-2">TAGS</div>
          <TagGroup title="FEATURE" items={turn.feature_tags} />
          <TagGroup title="CONSTRAINT" items={turn.constraint_tags} />
          <TagGroup title="HINT" items={turn.hint_tags} />
        </div>
      ) : null}

      {(turn.error_code || turn.error_message) ? (
        <div className="rounded-compact border border-n-accent px-4 py-3">
          <div className="label text-n-accent">ERROR</div>
          <div className="mt-1 font-mono text-[11px] text-n-accent">
            {turn.error_code || "upstream_error"}
            {turn.error_stage ? ` · ${turn.error_stage}` : ""}
          </div>
          <div className="mt-1 text-[13px] text-n-primary">
            {turn.error_message || "No detailed error message recorded."}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function AttemptRow({ attempt }: { attempt: TraceAttempt }) {
  return (
    <div className="rounded-compact border border-n-border px-3 py-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <span className={`h-1.5 w-1.5 rounded-full ${attempt.success ? "bg-n-success" : attempt.blocked ? "bg-n-warning" : "bg-n-accent"}`} />
            <span className="font-mono text-[11px] uppercase tracking-[0.08em] text-n-secondary">
              Attempt {attempt.attempt_index}
            </span>
          </div>
          <div className="mt-1 font-mono text-[13px] text-n-display">{shortModel(attempt.selected_model)}</div>
          <div className="mt-1 font-mono text-[11px] text-n-secondary">
            {prettyTransport(attempt.requested_transport || attempt.transport)} → {prettyTransport(attempt.transport)}
          </div>
        </div>
        <div className="text-right">
          <div className="font-mono text-[11px] text-n-secondary">{attempt.provider_name || "gateway"}</div>
          <div className={`mt-1 font-mono text-[11px] ${attempt.success ? "text-n-success" : "text-n-accent"}`}>
            {attempt.blocked ? "BLOCKED" : attempt.success ? "SUCCESS" : `HTTP ${attempt.status_code || "—"}`}
          </div>
        </div>
      </div>
      {attempt.transport_reason ? (
        <div className="mt-2 text-[12px] text-n-primary">{attempt.transport_reason}</div>
      ) : null}
      {(attempt.error_code || attempt.error_message) ? (
        <div className="mt-2 font-mono text-[11px] text-n-accent">
          {attempt.error_code || "upstream_error"}
          {attempt.error_message ? ` · ${attempt.error_message}` : ""}
        </div>
      ) : null}
    </div>
  );
}

function TagGroup({ title, items }: { title: string; items: string[] }) {
  if (!items || items.length === 0) return null;
  return (
    <div className="mt-2">
      <div className="font-mono text-[11px] text-n-secondary">{title}</div>
      <div className="mt-1 flex flex-wrap gap-1.5">
        {items.map((item) => (
          <Badge key={`${title}-${item}`}>{item}</Badge>
        ))}
      </div>
    </div>
  );
}

function Badge({
  children,
  tone = "default",
}: {
  children: ReactNode;
  tone?: "default" | "error";
}) {
  return (
    <span
      className={`rounded-pill border px-2 py-0.5 font-mono text-[11px] uppercase tracking-[0.06em] ${
        tone === "error" ? "border-n-accent text-n-accent" : "border-n-border-vis text-n-secondary"
      }`}
    >
      {children}
    </span>
  );
}

function MiniMetric({
  label,
  value,
  monoSmall = false,
}: {
  label: string;
  value: string;
  monoSmall?: boolean;
}) {
  return (
    <div className="rounded-compact border border-n-border px-3 py-2">
      <div className="label">{label}</div>
      <div className={`mt-0.5 font-mono font-semibold tracking-tight text-n-display ${monoSmall ? "text-[11px]" : "text-[14px]"}`}>
        {value}
      </div>
    </div>
  );
}

// ===== Helpers =====

const TIER_NAMES: Record<string, string> = {
  SIMPLE: "LOW",
  MEDIUM: "MID",
  COMPLEX: "HIGH",
  REASONING: "HIGH",
  low: "LOW",
  mid: "MID",
  mid_high: "MID_HIGH",
  high: "HIGH",
};

export function relativeTime(ts: number) {
  if (!ts) return "—";
  const seconds = Math.max(0, Date.now() / 1000 - ts);
  if (seconds < 60) return `${Math.floor(seconds)}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

export function normTier(t?: string) {
  return TIER_NAMES[t || ""] || (t || "—").toUpperCase();
}

export function shortModel(model?: string) {
  return (model || "").split("/").pop() || model || "—";
}

export function prettyTransport(transport?: string) {
  switch (transport) {
    case "anthropic-messages":
      return "Anthropic Messages";
    case "openai-responses":
      return "OpenAI Responses";
    case "openai-chat":
      return "OpenAI Chat";
    default:
      return transport || "—";
  }
}

function prettyQuality(value?: string) {
  if (!value) return "—";
  return value.replace(/_/g, " ").toUpperCase();
}

function prettyLane(value?: string) {
  if (!value) return "—";
  return value.replace(/-/g, " ").replace(/_/g, " ").toUpperCase();
}

function prettifySource(source?: string) {
  if (!source) return "unspecified";
  return source.replace(/-/g, " ").replace(/_/g, " ").toUpperCase();
}

// ===== groupSessions exposed for ExplainerNew =====

export function groupSessions(traces: TraceRecord[]): Session[] {
  const buckets = new Map<string, TraceRecord[]>();
  for (const t of traces) {
    const key = t.session_id || `_solo:${t.request_id}`;
    const arr = buckets.get(key);
    if (arr) arr.push(t);
    else buckets.set(key, [t]);
  }

  const sessions: Session[] = [];
  for (const [id, items] of buckets) {
    items.sort((a, b) => a.timestamp - b.timestamp);
    const tierMap = new Map<string, number>();
    const modelSet = new Set<string>();
    let totalCost = 0;
    let hasError = false;
    for (const t of items) {
      const tier = normTier(t.decision_tier || t.tier);
      tierMap.set(tier, (tierMap.get(tier) || 0) + 1);
      if (t.model) modelSet.add(t.model);
      totalCost += t.estimated_cost || 0;
      if (t.status_code >= 400) hasError = true;
    }
    const tierOrder = ["LOW", "MID", "MID_HIGH", "HIGH"];
    const tierCounts = Array.from(tierMap.entries()).sort(
      (a, b) => tierOrder.indexOf(a[0]) - tierOrder.indexOf(b[0]),
    );
    sessions.push({
      id,
      turns: items,
      firstTimestamp: items[0].timestamp,
      lastTimestamp: items[items.length - 1].timestamp,
      totalCost,
      hasError,
      models: Array.from(modelSet),
      tierCounts,
    });
  }

  sessions.sort((a, b) => b.lastTimestamp - a.lastTimestamp);
  return sessions;
}
```

- [ ] **Step 9.3: Build check**

```
cd frontend/dashboard && npx tsc -b
```

Expected: no errors. (`ExplainerNew.tsx` may now have duplicate definitions of these helpers — that's fine for one commit; we'll clean it in Task 11.)

- [ ] **Step 9.4: Commit**

```
git add frontend/dashboard/src/components/conversation/TurnListView.tsx
git commit -m "Extract per-turn fallback view (TurnListView)"
```

---

## Task 10: `ConversationView`

**Files:**
- Create: `frontend/dashboard/src/components/conversation/ConversationView.tsx`

- [ ] **Step 10.1: Create the file**

```tsx
import { useEffect, useState } from "react";
import { fetchConversation, type Conversation } from "../../api";
import { group } from "./groupMessages";
import MessageBubble from "./MessageBubble";
import ToolStepGroup from "./ToolStepGroup";
import TurnListView, { type Session } from "./TurnListView";

export default function ConversationView({
  sessionId,
  fallbackSession,
}: {
  sessionId: string;
  fallbackSession: Session | null;
}) {
  const [data, setData] = useState<Conversation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setData(null);

    const load = async () => {
      const payload = await fetchConversation(sessionId);
      if (cancelled) return;
      if (!payload) {
        setError("[ERROR: CONVERSATION ENDPOINT UNREACHABLE]");
        setData(null);
      } else {
        setError(null);
        setData(payload);
      }
      setLoading(false);
    };
    load();
    const id = window.setInterval(load, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [sessionId]);

  if (loading && !data) {
    return (
      <div className="space-y-3">
        {[0, 1, 2, 3, 4].map((i) => (
          <div key={i} className="h-20 animate-pulse rounded-card border border-n-border bg-n-surface opacity-30" />
        ))}
      </div>
    );
  }

  if (error) {
    return <div className="font-mono text-[12px] text-n-accent">{error}</div>;
  }

  if (!data) {
    return null;
  }

  if (!data.content_available) {
    if (fallbackSession) {
      return <TurnListView session={fallbackSession} />;
    }
    return (
      <div className="font-mono text-[11px] text-n-disabled">
        [NO CONTENT CAPTURED FOR THIS SESSION]
      </div>
    );
  }

  const items = group({ messages: data.messages, compact_breaks: data.compact_breaks });

  return (
    <div className="space-y-4">
      {items.map((it, i) => {
        if (it.type === "compact_break") {
          return (
            <div
              key={`break-${i}`}
              className="flex items-center gap-3 px-1 py-1 font-mono text-[11px] uppercase tracking-[0.08em] text-n-disabled"
            >
              <span className="h-px flex-1 bg-n-border" />
              <span>⟪HISTORY COMPACTED⟫</span>
              <span className="h-px flex-1 bg-n-border" />
            </div>
          );
        }
        if (it.type === "tool_group") {
          return <ToolStepGroup key={`group-${i}`} steps={it.steps} />;
        }
        return <MessageBubble key={`msg-${i}`} message={it.message} />;
      })}
    </div>
  );
}
```

- [ ] **Step 10.2: Build check**

```
cd frontend/dashboard && npx tsc -b
```

Expected: no errors.

- [ ] **Step 10.3: Commit**

```
git add frontend/dashboard/src/components/conversation/ConversationView.tsx
git commit -m "Add ConversationView top-level chat renderer"
```

---

## Task 11: Slim `ExplainerNew` to a Router

**Files:**
- Modify: `frontend/dashboard/src/components/ExplainerNew.tsx` (rewrite, much smaller)

- [ ] **Step 11.1: Replace `ExplainerNew.tsx` ENTIRELY with**

```tsx
/**
 * EXPLAIN NEW: Conversation-grouped routing explorer.
 * Left: session list. Right: chat-style conversation when content was
 * captured; otherwise the per-turn fallback view.
 */

import { useEffect, useMemo, useState } from "react";
import { fetchTraces } from "../api";
import type { TraceRecord } from "../api";
import ConversationView from "./conversation/ConversationView";
import { groupSessions, type Session } from "./conversation/TurnListView";

const TIER_NAMES: Record<string, string> = {
  SIMPLE: "LOW",
  MEDIUM: "MID",
  COMPLEX: "HIGH",
  REASONING: "HIGH",
  low: "LOW",
  mid: "MID",
  mid_high: "MID_HIGH",
  high: "HIGH",
};

function relativeTime(ts: number) {
  if (!ts) return "—";
  const seconds = Math.max(0, Date.now() / 1000 - ts);
  if (seconds < 60) return `${Math.floor(seconds)}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

function normTier(t?: string) {
  return TIER_NAMES[t || ""] || (t || "—").toUpperCase();
}

export default function ExplainerNew() {
  const [traces, setTraces] = useState<TraceRecord[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      const payload = await fetchTraces(100);
      if (cancelled) return;
      if (!payload) {
        setError("[ERROR: TRACE ENDPOINT UNREACHABLE]");
        setTraces([]);
        return;
      }
      setError(null);
      setTraces(payload.items);
    };
    load();
    const id = window.setInterval(load, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  const sessions = useMemo<Session[]>(() => groupSessions(traces), [traces]);

  useEffect(() => {
    if (sessions.length === 0) {
      if (selectedId !== null) setSelectedId(null);
      return;
    }
    if (!selectedId || !sessions.some((s) => s.id === selectedId)) {
      setSelectedId(sessions[0].id);
    }
  }, [sessions, selectedId]);

  const selected = useMemo(
    () => sessions.find((s) => s.id === selectedId) ?? null,
    [sessions, selectedId],
  );

  return (
    <div className="animate-fadeIn">
      <div className="mb-8">
        <h1 className="font-display text-[36px] text-n-display tracking-tight">EXPLAIN NEW</h1>
        <p className="mt-2 text-[14px] text-n-secondary">
          Group requests by conversation. Each session shows its turns and assistant decisions inline.
        </p>
      </div>

      {error ? <div className="mb-4 font-mono text-[12px] text-n-accent">{error}</div> : null}

      <div className="grid grid-cols-12 gap-8">
        <div className="col-span-4 max-h-[760px] overflow-y-auto rounded-card border border-n-border bg-n-surface">
          <div className="flex items-center justify-between border-b border-n-border px-5 py-4">
            <div className="label">SESSIONS</div>
            <div className="font-mono text-[11px] text-n-secondary">
              {sessions.length} · {traces.length} turns
            </div>
          </div>

          {sessions.length === 0 && !error ? (
            <div className="flex items-center justify-center py-16 font-mono text-[11px] tracking-[0.08em] text-n-disabled">
              [NO SESSIONS YET]
            </div>
          ) : null}

          <div>
            {sessions.map((session) => {
              const active = session.id === selectedId;
              const firstTurn = session.turns[0];
              const title = firstTurn?.prompt_preview || "[no preview]";
              return (
                <button
                  key={session.id}
                  onClick={() => setSelectedId(session.id)}
                  className={`row-hover w-full border-b border-n-border px-5 py-4 text-left ${
                    active ? "bg-n-raised" : "hover:bg-n-raised"
                  }`}
                >
                  <div className="flex items-center justify-between gap-3">
                    <div className="flex items-center gap-2">
                      <span className={`h-1.5 w-1.5 rounded-full ${session.hasError ? "bg-n-accent" : "bg-n-success"}`} />
                      <span className="font-mono text-[11px] uppercase tracking-[0.08em] text-n-secondary">
                        {session.id.slice(0, 8)}
                      </span>
                    </div>
                    <span className="font-mono text-[11px] text-n-disabled">
                      {session.turns.length} TURNS
                    </span>
                  </div>

                  <div className="mt-2 truncate text-[13px] text-n-primary">{title}</div>

                  <div className="mt-3 flex items-center justify-between gap-3 font-mono text-[11px] text-n-secondary">
                    <span className="truncate">
                      {session.tierCounts.map(([t, n]) => `${t}×${n}`).join(" ") || "—"}
                    </span>
                    <span>{relativeTime(session.lastTimestamp)}</span>
                  </div>
                </button>
              );
            })}
          </div>
        </div>

        <div className="col-span-8">
          {!selected ? (
            <div className="flex min-h-[320px] items-center justify-center rounded-card border border-dashed border-n-border dot-grid-subtle">
              <span className="font-mono text-[11px] tracking-[0.08em] text-n-disabled">[SELECT A SESSION]</span>
            </div>
          ) : (
            <ConversationView sessionId={selected.id} fallbackSession={selected} />
          )}
        </div>
      </div>
    </div>
  );
}
```

The `normTier` helper here is unused — leave or delete. (Delete it; the linter will flag an unused import.) Same for the `TIER_NAMES` constant. Actually after moving everything, only `relativeTime` is used. Trim:

- [ ] **Step 11.2: Trim unused helpers**

In the new `ExplainerNew.tsx`, delete `TIER_NAMES` and `normTier` if they're not referenced. Keep only `relativeTime`. Re-run build.

- [ ] **Step 11.3: Build check**

```
cd frontend/dashboard && npx tsc -b
```

Expected: no errors. The original 572-line file is now ~125 lines.

- [ ] **Step 11.4: Run the dev server**

```
cd frontend/dashboard && npm run dev
```

Open `http://localhost:5173/dashboard/`, navigate to EXPLAIN NEW. Confirm:
- The page renders (no red error overlay).
- The session list on the left populates from existing traces.
- Selecting a session causes the right pane to render — either chat bubbles (if `content_available=true`) or the per-turn list (fallback).

- [ ] **Step 11.5: Commit**

```
git add frontend/dashboard/src/components/ExplainerNew.tsx
git commit -m "Slim ExplainerNew to a thin router; chat view replaces right pane"
```

---

## Task 12: Manual Smoke Against Real Conversation Data

**Files:** none.

This is the verification step the spec defines. The user drives this — the implementer (you) just confirms each item and notes which ones pass.

- [ ] **Step 12.1: Start the proxy with capture on**

```
.venv/bin/uncommon-route stop
UNCOMMON_ROUTE_CAPTURE_CONTENT=1 .venv/bin/uncommon-route serve --port 8403
```

- [ ] **Step 12.2: Generate a multi-turn Claude Code conversation**

Through the user's existing Claude Code setup, send a request that will trigger a tool loop (e.g., "read the project structure and give me an overview"). Wait for the agent to complete several rounds.

- [ ] **Step 12.3: Open the dashboard and verify**

Open the dashboard at `http://localhost:5173/dashboard/`. Navigate to EXPLAIN NEW. Select the captured session. Run through the checklist:

- Right pane shows chat bubbles, not the per-turn card list.
- The user prompt appears in a USER bubble.
- Assistant bubbles render markdown (bold, headings, code blocks, lists).
- The agent's tool-call cycle shows as a single `▸ AGENT THOUGHT FOR N STEPS` card.
- Click the card → expanded view shows each step with its decision badge and tool_use details.
- Each captured assistant has a compact `model · tier · cost · latency` badge that expands the full decision card on click.
- Pre-capture assistants (if any) render greyed with `⟪PRE-CAPTURE⟫` and no decision badge.
- Tool-result blocks default-clip to 5 lines with a `show all` toggle.
- When you start a fresh session WITHOUT `UNCOMMON_ROUTE_CAPTURE_CONTENT`, the right pane falls back to the per-turn `TurnListView` (no chat bubbles).
- Polling: send another turn from Claude Code; within ~5 s the new message appears in the open conversation.

- [ ] **Step 12.4: Note results**

Briefly log which items passed and any visual issues found. If something is broken, dispatch a fix subagent before claiming done.

---

## Self-Review Checklist

- [ ] **Spec coverage:**
  - §3 file list matches Task 1-11 file list. ✓
  - §4 API types match Task 1 exactly. ✓
  - §5 grouping algorithm matches Task 3 implementation (including pre-capture handling). ✓
  - §6 ConversationView behavior (5 s poll, fallback, error/loading) matches Task 10. ✓
  - §7 Bubble variants (4) match Task 7. ✓
  - §8 Compact-break renderer matches Task 10's render branch. ✓
  - §9 Markdown rules match Task 4's `disallowedElements` and `components` overrides. ✓
  - §10 Verification plan: groupMessages tests match Task 3.1; manual smoke matches Task 12. ✓
  - §11 Defaults: all locked-in choices reflected in component code. ✓

- [ ] **Placeholders:** none in the plan body. The "TBD" in spec §12 for whether `ExplainerNew.tsx` shrinks below 300 lines is answered: it ends at ~125 lines after Task 11.

- [ ] **Type consistency:**
  - `ConversationMessage`, `DecisionCard`, `ConversationToolCall`, `Conversation` defined once in Task 1, imported elsewhere by name. ✓
  - `ToolStep` and `ConversationItem` defined once in Task 3 (`groupMessages.ts`), imported by `ToolStepGroup.tsx` and `ConversationView.tsx`. ✓
  - `Session` exported from Task 9 (`TurnListView.tsx`), imported by `ExplainerNew.tsx` and `ConversationView.tsx`. ✓
  - `groupSessions` exported from Task 9, imported by `ExplainerNew.tsx`. ✓
