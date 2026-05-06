# Conversation View — Design Spec (PR 2)

**Date:** 2026-04-28
**Branch:** `feat/conversation-persistence` (continued)
**Depends on:** PR 1 (`docs/specs/2026-04-27-conversation-persistence-design.md`) — `/v1/sessions/{id}/conversation` endpoint and per-turn cold field capture.

## 1. Goal

Replace the `EXPLAIN NEW` page's right-pane per-turn card list with a chat-style view that renders the actual conversation reconstructed by the backend assembly endpoint, with each captured assistant message carrying its routing decision inline and consecutive agent tool-call steps folded into a single expandable group.

## 2. Scope

**In scope:**
- New components: `ConversationView`, `MessageBubble`, `ToolStepGroup`, `DecisionBadge`, `MarkdownContent`.
- New API helper: `fetchConversation(sessionId)`.
- Modifications: `ExplainerNew.tsx` switches the right pane between `ConversationView` (when `content_available=true`) and a renamed `TurnListView` (when `false`).
- New runtime dependency: `react-markdown` (for assistant text rendering).
- Tests for grouping logic, fallback rendering, and bubble snapshots.

**Out of scope:**
- Streaming token display (5 s polling matches PR 1's session list).
- Edit/resend flows, export-as-markdown, cross-session search.
- Feedback write UI (existing fields are surfaced read-only via the decision card).
- Removing the legacy `Explainer.tsx` page or the `EXPLAIN` sidebar entry.
- Backend changes — PR 1 already exposes everything needed.

## 3. File Structure

| Path | Status | Responsibility |
|---|---|---|
| `frontend/dashboard/src/api.ts` | modify | Add `fetchConversation(sessionId)` returning the assembly endpoint shape; add `Conversation` / `ConversationMessage` / `DecisionCard` types. |
| `frontend/dashboard/src/components/ExplainerNew.tsx` | modify | Keep the left session-list pane; swap right-pane renderer based on `content_available`. Trim turn-rendering logic out of this file (move to `TurnListView`). |
| `frontend/dashboard/src/components/conversation/ConversationView.tsx` | **new** | Top-level chat renderer. Handles polling the conversation endpoint, the grouping pass, compact-break separators, and the empty/loading states. |
| `frontend/dashboard/src/components/conversation/MessageBubble.tsx` | **new** | Renders one `user` / `assistant` / `tool_result` entry. |
| `frontend/dashboard/src/components/conversation/ToolStepGroup.tsx` | **new** | Renders a folded "AGENT THOUGHT FOR N STEPS" card; expanded view lists each step with its decision badge. |
| `frontend/dashboard/src/components/conversation/DecisionBadge.tsx` | **new** | Compact `model · tier · cost · latency` badge that expands into the full decision card on click. Reuses styling from the existing `TurnDecision` block in `ExplainerNew`. |
| `frontend/dashboard/src/components/conversation/MarkdownContent.tsx` | **new** | Thin wrapper around `react-markdown` with the project's typography / code-block styles applied. |
| `frontend/dashboard/src/components/conversation/TurnListView.tsx` | **new** | Extracted fallback view — what `ExplainerNew` renders today when `content_available=false`. Receives the existing `TraceRecord[]` from the parent. |
| `frontend/dashboard/src/components/conversation/groupMessages.ts` | **new** | Pure function: `group(messages: ConversationMessage[]) -> ConversationItem[]`. Folds consecutive agent-loop steps into `tool_group` items. Unit-tested. |
| `frontend/dashboard/src/components/conversation/__tests__/*` | **new** | Vitest suites — see §10. |
| `frontend/dashboard/package.json` | modify | Add `react-markdown` dependency. |

Single-responsibility split: rendering, grouping, fetching, and folding are each one file.

## 4. API Layer

Add to `frontend/dashboard/src/api.ts`:

```ts
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

`Conversation` is allowed to be `null` from `get()` on network errors — callers must handle that.

## 5. Grouping Pass (`groupMessages.ts`)

Pure function over the `messages` array. Output items:

```ts
export type ConversationItem =
  | { type: "message"; message: ConversationMessage }
  | { type: "tool_group"; steps: ToolStep[] }
  | { type: "compact_break"; atTurnIndex: number };

export interface ToolStep {
  assistant: ConversationMessage; // role === "assistant", has tool_calls, no text
  results: ConversationMessage[]; // role === "tool_result", in order
}
```

Algorithm:

```
1. Walk messages, tracking running_turn_index (incremented on each assistant entry).
2. Maintain a buffer for the current tool step + accumulating group.
3. For each message m:
     if m is assistant AND has tool_calls AND text is empty:
         flush any pending tool_result-only buffer as the previous step's results
         start a new ToolStep(assistant=m, results=[])
     elif m is tool_result AND a current ToolStep exists:
         append m to current step's results
     else:
         flush the current group into the output as { type: "tool_group", steps }
         output { type: "message", message: m }
4. Compact-break placement.
   Backend emits `compact_breaks` as TURN indices (1-based positions in the
   captured-turn list). The frontend doesn't know the alignment offset
   between turn index and message-array position when there are pre-capture
   assistants. Mapping rule: walk messages, count captured assistants
   (`message.role === "assistant" && message.decision !== null`); when the
   count reaches `k` for any `k` in `compact_breaks`, insert
   `{ type: "compact_break", atTurnIndex: k }` AFTER the just-counted message
   (i.e., just before the next message). Pre-capture assistants are not
   counted toward `k`.
```

Edge cases:
- Assistant with BOTH text and tool_calls (rare in Claude Code, but possible) → emit as `message`, not part of a tool group. The text means it's "speaking" to the user.
- Tool_result with no preceding assistant tool step → emit as standalone `message` (degraded, shouldn't normally happen).
- Pre-capture assistant (`decision === null`) inside a tool group: still belongs to its group; renders with greyed badge.

## 6. ConversationView

```tsx
<ConversationView sessionId={selectedId} />
```

Behavior:

1. On mount and on `sessionId` change: call `fetchConversation(sessionId)`.
2. Re-fetch every 5 s while mounted (matches PR 1's poll cadence).
3. While loading: show a skeleton placeholder (5 dim bubbles).
4. On null response: surface "[ERROR: CONVERSATION ENDPOINT UNREACHABLE]" — same style as the existing trace-fetch error.
5. On `content_available === false`: render `<TurnListView session={selectedSession} />` instead.
6. Otherwise: run `group(messages)` and render the resulting items.

Layout: single vertical column, max width 760px (matches existing `ExplainerNew` content column), spacing matches Nothing Design conventions (`space-y-4`).

## 7. Bubble Components

### `MessageBubble`

Three role variants:

**user**
- Background: `bg-n-surface`, border: `border-n-border`, header label `USER` + relative timestamp.
- If `text` contains a `<system-reminder>` / `<command-name>` wrapper at the start: collapse the wrapper into a small "⚙ system reminder" pill that expands to show the wrapper text. The user's actual prompt below renders normally.
- No markdown rendering for user content (raw text, preserving newlines via `whitespace-pre-wrap`).

**assistant (captured — has decision)**
- Same surface tones as user, label `ASSISTANT` plus a `<DecisionBadge decision={...} />` in the header row (compact form).
- Body renders via `<MarkdownContent>` with the assistant text.
- If `tool_calls` are present (assistant with text AND tool_calls — rare), render the calls as tool-step children below the text.

**assistant (pre-capture — `decision === null`)**
- Reduced opacity (`opacity-60`), label `ASSISTANT` plus a `⟪PRE-CAPTURE⟫` pill instead of decision badge.
- Body renders via `<MarkdownContent>`.

**tool_result**
- Compact card, label `TOOL RESULT` plus `tool_use_id` short hash.
- Body: monospace text, default-clipped to 5 lines with a "show all" toggle. Long results (>5 KB) truncate at 5 KB before clipping for the pre-clipped state and lazy-fetch the rest only when expanded (we already have the full text in memory; this is just a render guard).

### `DecisionBadge`

Compact mode (default):
```
[ opus-4-7 · COMPLEX · $0.1585 · 220ms ▾ ]
```
Click → expand the full decision card below the bubble. The expanded form reuses the same fields the current `TurnDecision` shows in `ExplainerNew.tsx`: `route_reasoning`, transport requested→served, mini-metrics (confidence/latency/cost/req_id), `attempts_payload`, feature/constraint/hint tags, errors. We can extract the existing `TurnDecision` JSX into this component verbatim.

State (open/closed) is local to each badge — no global expansion state.

### `ToolStepGroup`

Collapsed:
```
▸ AGENT THOUGHT FOR 4 STEPS  ·  Read×11 Bash×1  ·  2 models
```

Computed metadata:
- step count = `steps.length`
- tool counts: aggregate `step.assistant.tool_calls[].name` across all steps
- distinct models: `new Set(steps.map(s => s.assistant.decision?.model)).size` (treat null as "pre-capture", count separately)

Expanded:
```
▾ AGENT THOUGHT FOR 4 STEPS
  ├─ STEP 1  [opus-4-5 ▾]
  │   tool_use: Read({...}), Read({...})
  │   ↳ 2 results
  │       result 1: <first 5 lines, [show all]>
  │       result 2: <first 5 lines, [show all]>
  ├─ STEP 2  [opus-4-7 ▾]
  │   ...
```

Click on a `STEP N`'s decision badge expands its full decision card inline (same component as on assistant bubbles).

## 8. Compact Break Renderer

Single horizontal divider with a centered label:
```
─── ⟪HISTORY COMPACTED⟫ ───
```
No interaction. Inserted between the message at `atTurnIndex - 1` and `atTurnIndex`. If `compact_breaks` is empty, no separators render.

## 9. Markdown Rendering

`MarkdownContent` wraps `react-markdown` with:
- Allowed elements: paragraphs, headings (h1-h4), strong/em, inline code, code blocks, ordered/unordered lists, blockquotes, links (target=_blank, rel=noopener).
- Disallowed: HTML pass-through (set `disallowedElements={["html"]}` and `unwrapDisallowed`), images (we don't expect them in routing-decision content; if needed later, add an allow-list).
- Code blocks: render with `font-mono` and `bg-n-raised` for visual consistency. No syntax highlighting (YAGNI for v1).
- Long inline content wraps via `break-words`.

`react-markdown` is added to `frontend/dashboard/package.json`. Confirm bundle impact in PR (≈ 30 KB minified including `remark-parse`).

## 10. Verification Plan

The dashboard project has no test runner today (`vitest`/`jest` not in deps). Adding one for this PR's UI code is scope creep — frontend visual code is well-served by manual smoke against live data. We add ONE pure-logic test using Node 20's built-in `node:test` runner to lock down `groupMessages` (the only non-trivial logic in this PR), and rely on manual smoke for everything else.

### Automated: `groupMessages.test.mjs` (Node built-in test runner)

Runs via `node --test frontend/dashboard/src/components/conversation/groupMessages.test.mjs` (no new dev deps). The test file imports a compiled `.js` (via `tsc` in build) or an inline JS port of `groupMessages.ts` for testability. If that proves friction-prone in practice, downgrade to manual review of the function plus a one-paragraph design comment.

Cases:
- Bare conversation (one user + one assistant text) → `[message, message]`.
- Single agent-loop cycle → `[message(user), tool_group(steps=1), message(assistant_text)]`.
- 4-cycle agent loop → one `tool_group` with 4 steps.
- Two tool sequences split by an assistant text → two distinct `tool_group`s.
- `compact_breaks: [3]` → a `compact_break` item appears after the 3rd captured-assistant message.
- Pre-capture assistant inside a tool step (decision null) → still grouped; `count_distinct_models()` does not count null.

### Manual smoke (drives the PR through the local dashboard)

1. Run proxy with content capture enabled (per PR 1 README); generate a multi-turn Claude Code conversation that includes a tool loop.
2. Open the dashboard at `:5173`, navigate to EXPLAIN NEW, select the captured session.
3. Verify:
   - Right pane renders chat bubbles, not the per-turn card list.
   - User bubble shows the prompt; assistant bubbles show markdown-formatted text.
   - The assistant tool-call cycle shows as ONE collapsed `▸ AGENT THOUGHT FOR N STEPS` card. Expanding it lists the steps with each step's decision badge.
   - Each captured assistant has a compact `model · tier · cost · latency` badge that expands the full decision card on click.
   - Pre-capture assistants render greyed with `⟪PRE-CAPTURE⟫` instead of a decision badge.
   - When you turn off `UNCOMMON_ROUTE_CAPTURE_CONTENT` and start a new session, that session falls back to the per-turn `TurnListView` (no chat bubbles).
   - Polling: while the page is open, send another turn from Claude Code; within 5 s the new message appears.
   - Visual style matches the rest of the dashboard (Nothing Design conventions: OLED black, percussive transitions).

## 11. Defaults Locked In

| Choice | Default |
|---|---|
| Right-pane swap | replace `TurnListView` with `ConversationView` when `content_available` |
| Tool-loop folding | tight (consecutive tool-only assistant + tool_result merged into one group) |
| Decision card placement | compact badge inline, click-to-expand |
| Markdown library | `react-markdown` |
| Polling | 5 s for both session list and selected conversation |
| User-bubble alignment | left (consistent with assistant bubbles, OLED-quiet) |
| Code highlighting | none (YAGNI) |
| Streaming partial responses | none (poll-only) |

## 12. Open Questions for Implementation Plan

- Whether to extract the existing `TurnDecision` JSX from `ExplainerNew.tsx` directly (good — single source of truth) or copy it into `DecisionBadge` (worse — drift). The implementation plan should specify extraction.
- Whether `ExplainerNew.tsx` shrinks below ~300 lines after this work; if not, may need a follow-up split.

## 13. Migration / Breaking Changes

- None. Existing endpoints and traces continue working. The `EXPLAIN` sidebar entry (legacy `Explainer.tsx`) is unchanged. Users who turn off `UNCOMMON_ROUTE_CAPTURE_CONTENT` see the fallback `TurnListView`, which is functionally identical to today's `ExplainerNew` right pane.
