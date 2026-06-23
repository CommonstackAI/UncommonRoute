import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  type ReactNode,
} from "react";
import {
  fetchHealth,
  fetchStats,
  fetchMapping,
  fetchSpend,
  fetchRecent,
  type Health,
  type Stats,
  type Mapping,
  type Spend,
  type RecentRequest,
} from "../api";
import { useEventStream, type StreamEvent } from "../hooks/useEventStream";

export type LiveState = "pending" | "routed" | "completed";

export type LiveRecent = Partial<RecentRequest> & {
  request_id: string;
  timestamp: number;
  state: LiveState;
};

interface LiveData {
  health: Health | null;
  stats: Stats | null;
  mapping: Mapping | null;
  spend: Spend | null;
  recent: LiveRecent[];
  feedbackPending: number;
  ready: boolean;
  connected: boolean;
  refresh: () => Promise<void>;
}

type Action =
  | { type: "snapshot"; health: Health | null; stats: Stats | null; mapping: Mapping | null; spend: Spend | null; recent: RecentRequest[] }
  | { type: "stream"; event: StreamEvent }
  | { type: "stats"; stats: Stats | null; spend: Spend | null }
  | { type: "connection"; connected: boolean };

const RECENT_CAP = 50;

const initialState: LiveData = {
  health: null,
  stats: null,
  mapping: null,
  spend: null,
  recent: [],
  feedbackPending: 0,
  ready: false,
  connected: false,
  refresh: async () => {},
};

function computePending(recent: LiveRecent[]): number {
  return recent.filter((r) =>
    (r as RecentRequest).feedback_pending &&
    (!r.feedback_action || r.feedback_action === "expired"),
  ).length;
}

function reducer(state: LiveData, action: Action): LiveData {
  switch (action.type) {
    case "snapshot": {
      const recent: LiveRecent[] = action.recent
        .slice(0, RECENT_CAP)
        .map((r) => ({ ...r, state: "completed" as LiveState }));
      return {
        ...state,
        health: action.health ?? state.health,
        stats: action.stats ?? state.stats,
        mapping: action.mapping ?? state.mapping,
        spend: action.spend ?? state.spend,
        recent,
        feedbackPending: computePending(recent),
        ready: true,
      };
    }
    case "stats": {
      return {
        ...state,
        stats: action.stats ?? state.stats,
        spend: action.spend ?? state.spend,
      };
    }
    case "connection":
      return { ...state, connected: action.connected };
    case "stream": {
      const event = action.event;
      if (event.type === "request_started") {
        const next: LiveRecent = {
          request_id: event.request_id,
          timestamp: event.timestamp,
          state: "pending",
        };
        const filtered = state.recent.filter((r) => r.request_id !== event.request_id);
        return { ...state, recent: [next, ...filtered].slice(0, RECENT_CAP) };
      }
      if (event.type === "request_routed") {
        const recent = state.recent.map((r) =>
          r.request_id === event.request_id
            ? {
                ...r,
                turn_id: event.turn_id,
                tier: event.tier,
                model: event.model,
                method: event.method,
                transport: event.transport,
                prompt_preview: event.prompt_preview,
                state: "routed" as LiveState,
              }
            : r,
        );
        return { ...state, recent };
      }
      if (event.type === "request_completed") {
        const rec = event.record as unknown as RecentRequest;
        const filtered = state.recent.filter((r) => r.request_id !== rec.request_id);
        const merged: LiveRecent = { ...rec, state: "completed" };
        const recent = [merged, ...filtered].slice(0, RECENT_CAP);
        return { ...state, recent, feedbackPending: computePending(recent) };
      }
      if (event.type === "feedback_updated") {
        const recent = state.recent.map((r) =>
          r.request_id === event.request_id
            ? {
                ...r,
                feedback_signal: event.feedback_signal,
                feedback_ok: event.feedback_ok,
                feedback_action: event.feedback_action,
                feedback_from_tier: event.feedback_from_tier,
                feedback_to_tier: event.feedback_to_tier,
                feedback_reason: event.feedback_reason,
                feedback_submitted_at: event.feedback_submitted_at,
                feedback_pending: false,
              }
            : r,
        );
        return { ...state, recent, feedbackPending: computePending(recent) };
      }
      return state;
    }
  }
}

const LiveDataCtx = createContext<LiveData | null>(null);

export function LiveDataProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(reducer, initialState);
  const refetchTimer = useRef<number | null>(null);

  const loadSnapshot = useCallback(async () => {
    const [h, st, m, sp, recent] = await Promise.all([
      fetchHealth(),
      fetchStats(),
      fetchMapping(),
      fetchSpend(),
      fetchRecent(50),
    ]);
    dispatch({
      type: "snapshot",
      health: h,
      stats: st,
      mapping: m,
      spend: sp,
      recent: recent ?? [],
    });
  }, []);

  const refetchAggregates = useCallback(async () => {
    const [st, sp] = await Promise.all([fetchStats(), fetchSpend()]);
    dispatch({ type: "stats", stats: st, spend: sp });
  }, []);

  useEffect(() => {
    void loadSnapshot();
  }, [loadSnapshot]);

  const handleEvent = useCallback(
    (event: StreamEvent) => {
      dispatch({ type: "stream", event });
      if (event.type === "request_completed") {
        if (refetchTimer.current !== null) window.clearTimeout(refetchTimer.current);
        refetchTimer.current = window.setTimeout(() => {
          void refetchAggregates();
        }, 500);
      }
    },
    [refetchAggregates],
  );

  const { connected } = useEventStream("/v1/events/stream", handleEvent);

  useEffect(() => {
    dispatch({ type: "connection", connected });
    if (connected) {
      void loadSnapshot();
    }
  }, [connected, loadSnapshot]);

  const value = useMemo(
    () => ({ ...state, refresh: loadSnapshot }),
    [state, loadSnapshot],
  );
  return <LiveDataCtx.Provider value={value}>{children}</LiveDataCtx.Provider>;
}

export function useLiveData(): LiveData {
  const ctx = useContext(LiveDataCtx);
  if (!ctx) throw new Error("useLiveData must be used within LiveDataProvider");
  return ctx;
}
