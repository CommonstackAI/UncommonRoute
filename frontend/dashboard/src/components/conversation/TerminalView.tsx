import { useMemo, useState, type ReactNode } from "react";
import type { Conversation, ConversationMessage, ConversationToolCall } from "../../api";
import CodeBlock, { languageFromPath } from "./CodeBlock";
import DecisionBadge from "./DecisionBadge";
import MarkdownContent from "./MarkdownContent";

const SYSTEM_REMINDER_PATTERN =
  /^\s*(<(system-reminder|command-name|local-command-stdout|command-message|command-args)\b[^>]*>)/i;

const PREVIEW_LINES = 5;
const PREVIEW_CHARS = 800;

type ToolCallLookup = Record<string, ConversationToolCall>;

export default function TerminalView({ data }: { data: Conversation }) {
  const lookup = useMemo(() => buildLookup(data), [data]);
  const compactSet = useMemo(() => new Set(data.compact_breaks), [data.compact_breaks]);
  const rows: ReactNode[] = [];
  let capturedAsstCount = 0;

  data.messages.forEach((m, idx) => {
    if (m.role === "user") {
      rows.push(<UserRow key={`u-${idx}`} message={m} />);
    } else if (m.role === "assistant") {
      if (m.text && m.text.trim()) {
        rows.push(<AssistantTextRow key={`at-${idx}`} message={m} />);
      } else if (m.decision != null && (!m.tool_calls || m.tool_calls.length === 0)) {
        rows.push(<AssistantDecisionRow key={`ad-${idx}`} message={m} />);
      }
      for (const tc of m.tool_calls ?? []) {
        rows.push(<ToolCallRow key={`tc-${tc.id || idx}`} call={tc} decision={m.decision} />);
      }
      if (m.decision != null) {
        capturedAsstCount += 1;
        if (compactSet.has(capturedAsstCount)) {
          rows.push(<CompactBreak key={`cb-${capturedAsstCount}`} />);
        }
      }
    } else if (m.role === "tool_result") {
      const call = m.tool_use_id ? lookup[m.tool_use_id] : undefined;
      rows.push(<ToolResultRow key={`tr-${idx}`} message={m} call={call} />);
    }
  });

  return <div className="font-mono text-[13px] leading-[1.55]">{rows}</div>;
}

function buildLookup(data: Conversation): ToolCallLookup {
  const map: ToolCallLookup = {};
  for (const m of data.messages) {
    if (m.role !== "assistant" || !m.tool_calls) continue;
    for (const c of m.tool_calls) {
      if (c.id) map[c.id] = c;
    }
  }
  return map;
}

// ===== Row layout primitives =====

function BulletRow({
  tone = "success",
  children,
}: {
  tone?: "success" | "accent" | "secondary";
  children: ReactNode;
}) {
  const color =
    tone === "accent" ? "text-n-accent" : tone === "secondary" ? "text-n-secondary" : "text-n-success";
  return (
    <div className="mt-3 flex gap-2">
      <span className={`shrink-0 select-none leading-[1.55] ${color}`}>●</span>
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}

function ContinuationRow({ children }: { children: ReactNode }) {
  return (
    <div className="mt-1 flex gap-2 pl-4">
      <span className="shrink-0 select-none leading-[1.55] text-n-disabled">└</span>
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}

// ===== User =====

function UserRow({ message }: { message: ConversationMessage }) {
  const [showWrapper, setShowWrapper] = useState(false);
  let displayText = message.text;
  let wrapperText = "";
  while (SYSTEM_REMINDER_PATTERN.test(displayText)) {
    const m = displayText.match(
      /^([\s\S]*?<\/(system-reminder|command-name|local-command-stdout|command-message|command-args)>\s*)/i,
    );
    if (!m) break;
    wrapperText += m[1];
    displayText = displayText.slice(m[1].length);
  }
  return (
    <div className="mt-4 flex gap-2 rounded-compact bg-n-surface px-2 py-1">
      <span className="shrink-0 select-none leading-[1.55] text-n-display">{">"}</span>
      <div className="min-w-0 flex-1 text-n-display">
        {wrapperText ? (
          <button
            onClick={() => setShowWrapper((v) => !v)}
            className="mr-2 align-middle font-mono text-[10px] uppercase tracking-[0.06em] text-n-secondary hover:text-n-primary"
          >
            ⚙ {showWrapper ? "hide" : "show"} reminder
          </button>
        ) : null}
        {showWrapper && wrapperText ? (
          <pre className="my-1 overflow-x-auto whitespace-pre-wrap rounded-compact border border-n-border bg-n-raised px-3 py-2 font-mono text-[11px] text-n-secondary">
            {wrapperText}
          </pre>
        ) : null}
        {displayText.trim() ? <CollapsibleProse text={displayText} /> : null}
      </div>
    </div>
  );
}

function CollapsibleProse({ text }: { text: string }) {
  const [expanded, setExpanded] = useState(false);
  const lineCount = countLines(text);
  const isLong = lineCount > 18 || text.length > 1400;
  return (
    <div>
      <div
        className={
          expanded || !isLong
            ? "whitespace-pre-wrap leading-relaxed text-n-display"
            : "relative max-h-[260px] overflow-hidden whitespace-pre-wrap leading-relaxed text-n-display"
        }
      >
        {text}
        {!expanded && isLong ? (
          <div className="pointer-events-none absolute inset-x-0 bottom-0 h-12 bg-gradient-to-t from-n-black to-transparent" />
        ) : null}
      </div>
      {isLong ? (
        <button
          onClick={() => setExpanded((v) => !v)}
          className="mt-1 font-mono text-[10px] uppercase tracking-[0.06em] text-n-secondary hover:text-n-primary"
        >
          {expanded ? "show less" : "show more"}
        </button>
      ) : null}
    </div>
  );
}

// ===== Assistant prose =====

function AssistantTextRow({ message }: { message: ConversationMessage }) {
  return (
    <BulletRow>
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <div className="min-w-0 flex-1">
          <CollapsibleMarkdown text={message.text} />
        </div>
        {message.decision ? (
          <div className="shrink-0">
            <DecisionBadge decision={message.decision} />
          </div>
        ) : null}
      </div>
    </BulletRow>
  );
}

function AssistantDecisionRow({ message }: { message: ConversationMessage }) {
  return (
    <BulletRow tone="secondary">
      <div className="flex items-center gap-2 text-n-secondary">
        <span>(no text)</span>
        {message.decision ? <DecisionBadge decision={message.decision} /> : null}
      </div>
    </BulletRow>
  );
}

function CollapsibleMarkdown({ text }: { text: string }) {
  const [expanded, setExpanded] = useState(false);
  const lineCount = countLines(text);
  const isLong = lineCount > 18 || text.length > 1400;
  if (!isLong) return <MarkdownContent text={text} />;
  return (
    <div>
      <div className={expanded ? "" : "relative max-h-[260px] overflow-hidden"}>
        <MarkdownContent text={text} />
        {!expanded ? (
          <div className="pointer-events-none absolute inset-x-0 bottom-0 h-12 bg-gradient-to-t from-n-black to-transparent" />
        ) : null}
      </div>
      <button
        onClick={() => setExpanded((v) => !v)}
        className="mt-1 font-mono text-[10px] uppercase tracking-[0.06em] text-n-secondary hover:text-n-primary"
      >
        {expanded ? "show less" : "show more"}
      </button>
    </div>
  );
}

// ===== Tool call header =====

function ToolCallRow({
  call,
  decision,
}: {
  call: ConversationToolCall;
  decision?: ConversationMessage["decision"];
}) {
  const headline = formatToolCallInline(call);
  return (
    <BulletRow>
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <div className="min-w-0 flex-1 truncate">
          <span className="font-semibold text-n-display">{call.name}</span>
          <span className="text-n-secondary">(</span>
          <span className="text-n-primary">{headline}</span>
          <span className="text-n-secondary">)</span>
        </div>
        {decision ? (
          <div className="shrink-0">
            <DecisionBadge decision={decision} />
          </div>
        ) : null}
      </div>
    </BulletRow>
  );
}

function formatToolCallInline(call: ConversationToolCall): string {
  const input = call.input;
  if (input == null) return "";
  if (typeof input === "string") return truncate(input, 140);
  const obj = input as Record<string, unknown>;
  // Prefer the most informative single key for the headline
  const primaryKeys = ["file_path", "command", "path", "url", "query", "pattern"];
  for (const k of primaryKeys) {
    if (k in obj && obj[k] != null) {
      return truncate(String(obj[k]), 140);
    }
  }
  // Fall back to compact key=value list
  const entries = Object.entries(obj);
  if (entries.length === 0) return "";
  const out = entries
    .map(([k, v]) => `${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`)
    .join(", ");
  return truncate(out, 140);
}

// ===== Tool result =====

function ToolResultRow({
  message,
  call,
}: {
  message: ConversationMessage;
  call?: ConversationToolCall;
}) {
  // Special-cased renderers:
  if (call && (call.name === "Edit" || call.name === "MultiEdit" || call.name === "Write")) {
    return (
      <ContinuationRow>
        <EditDiffBlock call={call} />
      </ContinuationRow>
    );
  }
  if (call?.name === "Read") {
    const filePath = readToolPath(call);
    return (
      <ContinuationRow>
        <CodePreview text={message.text || ""} language={languageFromPath(filePath)} />
      </ContinuationRow>
    );
  }
  return (
    <ContinuationRow>
      <OutputPreview text={message.text || ""} />
    </ContinuationRow>
  );
}

function readToolPath(call: ConversationToolCall): string | undefined {
  if (typeof call.input !== "object" || call.input == null) return undefined;
  const v = (call.input as Record<string, unknown>).file_path;
  return typeof v === "string" ? v : undefined;
}

function CodePreview({ text, language }: { text: string; language: string }) {
  const [expanded, setExpanded] = useState(false);
  const lines = text.split("\n");
  const isLong = lines.length > PREVIEW_LINES || text.length > PREVIEW_CHARS;
  const visibleText = expanded || !isLong ? text : lines.slice(0, PREVIEW_LINES).join("\n");
  const hiddenCount = isLong && !expanded ? lines.length - PREVIEW_LINES : 0;
  return (
    <div>
      <CodeBlock text={visibleText} language={language} />
      {isLong ? (
        <button
          onClick={() => setExpanded((v) => !v)}
          className="mt-1 text-[11px] text-n-disabled hover:text-n-primary"
        >
          {expanded ? "show less" : `… +${hiddenCount} lines (click to expand)`}
        </button>
      ) : null}
    </div>
  );
}

function OutputPreview({ text }: { text: string }) {
  const [expanded, setExpanded] = useState(false);
  const lines = text.split("\n");
  const isLong = lines.length > PREVIEW_LINES || text.length > PREVIEW_CHARS;
  const visible = expanded || !isLong ? text : lines.slice(0, PREVIEW_LINES).join("\n");
  const hiddenCount = isLong && !expanded ? lines.length - PREVIEW_LINES : 0;
  return (
    <div>
      <pre className="whitespace-pre-wrap text-[12px] text-n-primary">{visible}</pre>
      {isLong ? (
        <button
          onClick={() => setExpanded((v) => !v)}
          className="mt-1 text-[11px] text-n-disabled hover:text-n-primary"
        >
          {expanded ? "show less" : `… +${hiddenCount} lines (click to expand)`}
        </button>
      ) : null}
    </div>
  );
}

// ===== Edit / MultiEdit / Write diff =====

interface EditPair {
  oldStr: string;
  newStr: string;
}

function collectEditPairs(call: ConversationToolCall): EditPair[] {
  if (typeof call.input === "string" || call.input == null) return [];
  const obj = call.input as Record<string, unknown>;
  if (call.name === "Write") {
    const content = typeof obj.content === "string" ? obj.content : "";
    return [{ oldStr: "", newStr: content }];
  }
  if (call.name === "Edit") {
    return [
      {
        oldStr: typeof obj.old_string === "string" ? obj.old_string : "",
        newStr: typeof obj.new_string === "string" ? obj.new_string : "",
      },
    ];
  }
  if (call.name === "MultiEdit") {
    const edits = Array.isArray(obj.edits) ? obj.edits : [];
    return edits.map((e: unknown) => {
      const ee = (e ?? {}) as Record<string, unknown>;
      return {
        oldStr: typeof ee.old_string === "string" ? ee.old_string : "",
        newStr: typeof ee.new_string === "string" ? ee.new_string : "",
      };
    });
  }
  return [];
}

function EditDiffBlock({ call }: { call: ConversationToolCall }) {
  const [expanded, setExpanded] = useState(false);
  const pairs = collectEditPairs(call);
  if (pairs.length === 0) return null;
  const language =
    typeof call.input === "object" && call.input != null
      ? languageFromPath((call.input as Record<string, unknown>).file_path as string | undefined)
      : "";

  let totalAdded = 0;
  let totalRemoved = 0;
  const blocks = pairs.map((p) => {
    const oldLines = p.oldStr === "" ? [] : p.oldStr.split("\n");
    const newLines = p.newStr === "" ? [] : p.newStr.split("\n");
    totalRemoved += oldLines.length;
    totalAdded += newLines.length;
    return { oldLines, newLines };
  });

  const summary =
    call.name === "Write"
      ? `Wrote ${totalAdded} line${totalAdded === 1 ? "" : "s"}`
      : `Added ${totalAdded} line${totalAdded === 1 ? "" : "s"}, removed ${totalRemoved} line${
          totalRemoved === 1 ? "" : "s"
        }`;

  const shouldClamp = totalAdded + totalRemoved > 12;
  const renderClamped = shouldClamp && !expanded;

  return (
    <div>
      <div className="text-[12px] text-n-secondary">{summary}</div>
      <div
        className={
          renderClamped
            ? "relative mt-1 max-h-[200px] overflow-hidden"
            : "mt-1"
        }
      >
        {blocks.map((b, i) => (
          <DiffHunk
            key={i}
            oldLines={b.oldLines}
            newLines={b.newLines}
            startLine={1}
            language={language}
          />
        ))}
        {renderClamped ? (
          <div className="pointer-events-none absolute inset-x-0 bottom-0 h-10 bg-gradient-to-t from-n-black to-transparent" />
        ) : null}
      </div>
      {shouldClamp ? (
        <button
          onClick={() => setExpanded((v) => !v)}
          className="mt-1 text-[11px] text-n-disabled hover:text-n-primary"
        >
          {expanded ? "show less" : `show full diff (${totalAdded + totalRemoved} lines)`}
        </button>
      ) : null}
    </div>
  );
}

function DiffHunk({
  oldLines,
  newLines,
  startLine,
  language,
}: {
  oldLines: string[];
  newLines: string[];
  startLine: number;
  language: string;
}) {
  const rows: ReactNode[] = [];
  oldLines.forEach((line, i) => {
    rows.push(
      <DiffLine
        key={`o-${i}`}
        ln={startLine + i}
        sign="-"
        text={line}
        tone="remove"
        language={language}
      />,
    );
  });
  newLines.forEach((line, i) => {
    rows.push(
      <DiffLine
        key={`n-${i}`}
        ln={startLine + i}
        sign="+"
        text={line}
        tone="add"
        language={language}
      />,
    );
  });
  return <div className="code-hl">{rows}</div>;
}

function DiffLine({
  ln,
  sign,
  text,
  tone,
  language,
}: {
  ln: number;
  sign: "+" | "-";
  text: string;
  tone: "add" | "remove";
  language: string;
}) {
  const bg = tone === "add" ? "bg-[rgba(34,197,94,0.10)]" : "bg-[rgba(239,68,68,0.10)]";
  const signColor = tone === "add" ? "text-n-success" : "text-n-accent";
  return (
    <div className={`flex gap-2 ${bg} text-[12px]`}>
      <span className="w-8 shrink-0 select-none text-right text-n-disabled">{ln}</span>
      <span className={`w-3 shrink-0 select-none ${signColor}`}>{sign}</span>
      <DiffCode text={text} language={language} />
    </div>
  );
}

function DiffCode({ text, language }: { text: string; language: string }) {
  if (!language) {
    return <span className="min-w-0 flex-1 whitespace-pre-wrap break-all text-n-primary">{text}</span>;
  }
  // Inline single-line highlight via CodeBlock with no line numbers
  return (
    <span className="min-w-0 flex-1">
      <CodeBlock text={text} language={language} />
    </span>
  );
}

// ===== Compact break =====

function CompactBreak() {
  return (
    <div className="my-3 flex items-center gap-3 font-mono text-[11px] uppercase tracking-[0.08em] text-n-disabled">
      <span className="h-px flex-1 bg-n-border" />
      <span>⟪HISTORY COMPACTED⟫</span>
      <span className="h-px flex-1 bg-n-border" />
    </div>
  );
}

// ===== Helpers =====

function truncate(s: string, n: number): string {
  if (s.length <= n) return s;
  return s.slice(0, n) + "…";
}

function countLines(s: string): number {
  if (!s) return 0;
  let n = 1;
  for (let i = 0; i < s.length; i++) if (s.charCodeAt(i) === 10) n++;
  return n;
}
