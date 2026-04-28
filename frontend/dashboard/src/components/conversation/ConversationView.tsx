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
