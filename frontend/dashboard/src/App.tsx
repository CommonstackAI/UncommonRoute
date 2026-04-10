/**
 * Nothing Design: App shell
 * OLED black, 200px sidebar, no animated transitions (percussive, not fluid)
 */

import { useCallback, useEffect, useState } from "react";
import {
  fetchHealth,
  fetchStats,
  fetchMapping,
  fetchSpend,
  type Health,
  type Stats,
  type Mapping,
  type Spend,
} from "./api";
import Sidebar from "./components/Sidebar";
import Home from "./components/Home";
import Activity from "./components/Activity";
import Models from "./components/Models";
import SpendPanel from "./components/Spend";
import Feedback from "./components/Feedback";
import Connections from "./components/Connections";
import Routing from "./components/Routing";
import Playground from "./components/Playground";
import Explainer from "./components/Explainer";

type Page = "home" | "playground" | "routing" | "models" | "activity" | "budget" | "feedback" | "connections" | "explain";

export default function App() {
  const [page, setPage] = useState<Page>("home");
  const [health, setHealth] = useState<Health | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);
  const [mapping, setMapping] = useState<Mapping | null>(null);
  const [spend, setSpend] = useState<Spend | null>(null);
  const [ready, setReady] = useState(false);

  const refresh = useCallback(async () => {
    const [h, st, m, sp] = await Promise.all([
      fetchHealth(), fetchStats(), fetchMapping(), fetchSpend(),
    ]);
    if (h) { setHealth(h); setReady(true); }
    if (st) setStats(st);
    if (m) setMapping(m);
    if (sp) setSpend(sp);
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 5000);
    return () => clearInterval(id);
  }, [refresh]);

  const upstream = health?.upstream?.replace(/^https?:\/\//, "").replace(/\/v1$/, "") ?? "";
  const isUp = health?.model_mapper?.discovered ?? false;
  const version = health?.version ?? "—";
  const feedbackPending = health?.feedback?.pending ?? 0;

  if (!ready) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-n-black">
        <div className="font-mono text-[11px] tracking-[0.1em] text-n-disabled animate-pulse">
          [CONNECTING...]
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-n-black">
      <Sidebar
        current={page}
        onChange={(p) => setPage(p as Page)}
        upstream={upstream}
        isUp={isUp}
        version={version}
        feedbackPending={feedbackPending}
      />

      <main className="ml-[200px] min-h-screen">
        <div className="px-8 py-8 max-w-[1100px] mx-auto">
          {page === "home" && <Home stats={stats} health={health} />}
          {page === "playground" && <Playground />}
          {page === "explain" && <Explainer />}
          {page === "routing" && <Routing onRefresh={refresh} />}
          {page === "activity" && <Activity stats={stats} />}
          {page === "models" && <Models mapping={mapping} />}
          {page === "connections" && <Connections initialConnection={health?.connections ?? null} onRefresh={refresh} />}
          {page === "budget" && <SpendPanel spend={spend} onRefresh={refresh} />}
          {page === "feedback" && <Feedback />}
        </div>
      </main>
    </div>
  );
}
