import { useState, type ReactNode } from "react";
import type { ConversationMessage, ConversationToolCall } from "../../api";
import DecisionBadge from "./DecisionBadge";
import MarkdownContent from "./MarkdownContent";
import { useT } from "../../i18n";
import type { Dictionary } from "../../i18n/types";

export type ToolCallLookup = Record<string, ConversationToolCall>;

function formatToolInput(input: ConversationToolCall["input"]): string {
  if (input == null) return "";
  if (typeof input === "string") return input;
  try {
    const entries = Object.entries(input);
    if (entries.length === 0) return "";
    return entries
      .map(([k, v]) => {
        const s = typeof v === "string" ? v : JSON.stringify(v);
        return `${k}: ${s}`;
      })
      .join(", ");
  } catch {
    return JSON.stringify(input);
  }
}

const SYSTEM_REMINDER_PATTERN = /^\s*(<(system-reminder|command-name|local-command-stdout|command-message|command-args)\b[^>]*>)/i;

const LONG_TEXT_LINE_THRESHOLD = 18;
const LONG_TEXT_CHAR_THRESHOLD = 1400;

function isLongText(text: string): boolean {
  if (!text) return false;
  if (text.length > LONG_TEXT_CHAR_THRESHOLD) return true;
  let lines = 1;
  for (let i = 0; i < text.length; i++) {
    if (text.charCodeAt(i) === 10) lines++;
    if (lines > LONG_TEXT_LINE_THRESHOLD) return true;
  }
  return false;
}

function CollapsibleContent({ text, children, t }: { text: string; children: ReactNode; t: Dictionary }) {
  const [expanded, setExpanded] = useState(false);
  if (!isLongText(text)) return <>{children}</>;
  return (
    <div>
      <div
        className={
          expanded
            ? ""
            : "relative max-h-[260px] overflow-hidden"
        }
      >
        {children}
        {!expanded ? (
          <div className="pointer-events-none absolute inset-x-0 bottom-0 h-16 bg-gradient-to-t from-n-surface to-transparent" />
        ) : null}
      </div>
      <button
        onClick={() => setExpanded((v) => !v)}
        className="mt-2 font-mono text-[10px] uppercase tracking-[0.06em] text-n-secondary hover:text-n-primary"
      >
        {expanded ? t.common.showLess : t.common.showMore}
      </button>
    </div>
  );
}

function formatRelativeTs(ts: number | null | undefined, t: Dictionary): string {
  if (ts == null) return "";
  const seconds = Math.max(0, Date.now() / 1000 - ts);
  if (seconds < 60) return t.explainer.timeAgoSec(Math.floor(seconds));
  if (seconds < 3600) return t.explainer.timeAgoMin(Math.floor(seconds / 60));
  if (seconds < 86400) return t.explainer.timeAgoHour(Math.floor(seconds / 3600));
  return t.explainer.timeAgoDay(Math.floor(seconds / 86400));
}

export default function MessageBubble({
  message,
  toolCalls,
}: {
  message: ConversationMessage;
  toolCalls?: ToolCallLookup;
}) {
  const t = useT();
  if (message.role === "user") {
    return <UserBubble message={message} t={t} />;
  }
  if (message.role === "assistant") {
    return <AssistantBubble message={message} t={t} />;
  }
  return <ToolResultBubble message={message} toolCalls={toolCalls} t={t} />;
}

function UserBubble({ message, t }: { message: ConversationMessage; t: Dictionary }) {
  const [showWrapper, setShowWrapper] = useState(false);
  const hasWrapper = SYSTEM_REMINDER_PATTERN.test(message.text);
  let displayText = message.text;
  let wrapperText = "";
  if (hasWrapper) {
    const m = message.text.match(/^([\s\S]*?<\/(system-reminder|command-name|local-command-stdout|command-message|command-args)>\s*)/i);
    if (m) {
      wrapperText = m[1];
      displayText = message.text.slice(m[1].length);
    }
  }

  return (
    <div className="rounded-card border border-n-border bg-n-surface p-4">
      <div className="flex items-baseline justify-between gap-3">
        <div className="label">{t.conversation.user}</div>
        <div className="font-mono text-[10px] text-n-disabled">
          {formatRelativeTs(message.ts, t)}
        </div>
      </div>
      {wrapperText ? (
        <button
          onClick={() => setShowWrapper((v) => !v)}
          className="mt-2 inline-flex items-center gap-1.5 rounded-pill border border-n-border-vis px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.06em] text-n-secondary"
        >
          <span>⚙ {showWrapper ? t.conversation.hideSysReminder : t.conversation.showSysReminder}</span>
        </button>
      ) : null}
      {showWrapper ? (
        <pre className="mt-2 overflow-x-auto whitespace-pre-wrap rounded-compact border border-n-border bg-n-raised px-3 py-2 font-mono text-[11px] text-n-secondary">
          {wrapperText}
        </pre>
      ) : null}
      {displayText.trim() ? (
        <div className="mt-2">
          <CollapsibleContent text={displayText} t={t}>
            <div className="whitespace-pre-wrap text-[14px] leading-relaxed text-n-primary">
              {displayText}
            </div>
          </CollapsibleContent>
        </div>
      ) : null}
    </div>
  );
}

function AssistantBubble({ message, t }: { message: ConversationMessage; t: Dictionary }) {
  const isPreCapture = message.decision == null;
  return (
    <div
      className={`rounded-card border border-n-border bg-n-surface p-4 ${
        isPreCapture ? "opacity-60" : ""
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="label">{t.conversation.assistant}</div>
        <div className="flex shrink-0 items-baseline gap-2 font-mono text-[10px] text-n-disabled">
          {message.ts ? <span>{formatRelativeTs(message.ts, t)}</span> : null}
        </div>
      </div>
      <div className="mt-2">
        {isPreCapture ? (
          <span className="rounded-pill border border-n-border-vis px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.06em] text-n-disabled">
            {t.conversation.preCapture}
          </span>
        ) : (
          <DecisionBadge decision={message.decision!} />
        )}
      </div>
      {message.text ? (
        <div className="mt-2">
          <CollapsibleContent text={message.text} t={t}>
            <MarkdownContent text={message.text} />
          </CollapsibleContent>
        </div>
      ) : null}
    </div>
  );
}

function ToolResultBubble({
  message,
  toolCalls,
  t,
}: {
  message: ConversationMessage;
  toolCalls?: ToolCallLookup;
  t: Dictionary;
}) {
  const [expanded, setExpanded] = useState(false);
  const lines = (message.text || "").split("\n");
  const isLong = lines.length > 5 || (message.text || "").length > 1000;
  const preview = isLong && !expanded ? lines.slice(0, 5).join("\n") : message.text;
  const call = message.tool_use_id ? toolCalls?.[message.tool_use_id] : undefined;
  const inputStr = call ? formatToolInput(call.input) : "";
  return (
    <div className="rounded-compact border border-n-border bg-n-raised px-3 py-2">
      <div className="flex items-baseline justify-between gap-3">
        <div className="flex min-w-0 items-baseline gap-2">
          <div className="label">{t.conversation.toolResult}</div>
          {call ? (
            <span className="truncate font-mono text-[11px] text-n-primary">
              <span className="text-n-display">{call.name}</span>
              <span className="text-n-secondary">(</span>
              <span className="text-n-secondary">{inputStr}</span>
              <span className="text-n-secondary">)</span>
            </span>
          ) : null}
        </div>
        {message.tool_use_id ? (
          <div className="shrink-0 font-mono text-[10px] text-n-disabled">
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
          {expanded ? t.common.showLess : t.common.showAll}
        </button>
      ) : null}
    </div>
  );
}
