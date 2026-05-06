import { useEffect, useRef, useState } from "react";

export type StreamEvent =
  | { type: "request_started"; request_id: string; timestamp: number }
  | {
      type: "request_routed";
      request_id: string;
      turn_id: string;
      tier: string;
      model: string;
      method: string;
      transport: string;
      prompt_preview: string;
    }
  | { type: "request_completed"; record: Record<string, unknown> }
  | {
      type: "feedback_updated";
      request_id: string;
      feedback_signal: string;
      feedback_ok: boolean;
      feedback_action: string;
      feedback_from_tier: string;
      feedback_to_tier: string;
      feedback_reason: string;
      feedback_submitted_at: number;
    }
  | { type: "dropped"; reason: string };

type Handler = (event: StreamEvent) => void;

const BACKOFFS_MS = [1000, 2000, 4000, 8000, 15000, 30000];

export function useEventStream(url: string, onEvent: Handler) {
  const [connected, setConnected] = useState(false);
  const handlerRef = useRef(onEvent);
  handlerRef.current = onEvent;

  useEffect(() => {
    let source: EventSource | null = null;
    let retryIndex = 0;
    let reconnectTimer: number | null = null;
    let cancelled = false;

    const open = () => {
      if (cancelled) return;
      source = new EventSource(url);

      source.onopen = () => {
        retryIndex = 0;
        setConnected(true);
      };

      source.onmessage = (msg) => {
        if (!msg.data) return;
        try {
          const parsed = JSON.parse(msg.data) as StreamEvent;
          handlerRef.current(parsed);
        } catch {
          // malformed payload — ignore
        }
      };

      source.onerror = () => {
        setConnected(false);
        source?.close();
        source = null;
        if (cancelled) return;
        const wait = BACKOFFS_MS[Math.min(retryIndex, BACKOFFS_MS.length - 1)];
        retryIndex += 1;
        reconnectTimer = window.setTimeout(open, wait);
      };
    };

    open();

    return () => {
      cancelled = true;
      if (reconnectTimer !== null) window.clearTimeout(reconnectTimer);
      source?.close();
    };
  }, [url]);

  return { connected };
}
