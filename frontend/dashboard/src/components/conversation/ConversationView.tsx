import { useEffect, useRef, useState } from "react";
import { fetchConversation, type Conversation } from "../../api";
import TerminalView from "./TerminalView";
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
  const containerRef = useRef<HTMLDivElement | null>(null);
  const pendingScrollRef = useRef(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setData(null);
    pendingScrollRef.current = true;

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

  useEffect(() => {
    if (!pendingScrollRef.current) return;
    if (loading) return;
    if (!data && !fallbackSession) return;
    pendingScrollRef.current = false;
    requestAnimationFrame(() => {
      const el = containerRef.current;
      if (el) {
        const rect = el.getBoundingClientRect();
        const target = window.scrollY + rect.bottom - window.innerHeight + 16;
        window.scrollTo({ top: Math.max(0, target), behavior: "auto" });
      }
    });
  }, [data, loading, fallbackSession]);

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
      return (
        <div ref={containerRef}>
          <TurnListView session={fallbackSession} />
        </div>
      );
    }
    return (
      <div className="font-mono text-[11px] text-n-disabled">
        [NO CONTENT CAPTURED FOR THIS SESSION]
      </div>
    );
  }

  return (
    <div ref={containerRef}>
      <TerminalView data={data} />
    </div>
  );
}
