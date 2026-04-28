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
