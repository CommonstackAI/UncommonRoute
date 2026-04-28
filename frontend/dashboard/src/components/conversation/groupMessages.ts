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
        out.push({ type: "compact_break", atTurnIndex: pending.shift()! });
      }
    }
  }
  flushBuffer();
  return out;
}
