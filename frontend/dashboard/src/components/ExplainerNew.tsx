/**
 * EXPLAIN NEW: Conversation-grouped routing explorer.
 * Left: session list. Right: chat-style conversation when content was
 * captured; otherwise the per-turn fallback view.
 */

import { useEffect, useMemo, useState } from "react";
import { fetchTraces } from "../api";
import type { TraceRecord } from "../api";
import ConversationView from "./conversation/ConversationView";
import { groupSessions, relativeTime, type Session } from "./conversation/TurnListView";

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
