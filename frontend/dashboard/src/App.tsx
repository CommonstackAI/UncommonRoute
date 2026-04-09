import { useCallback, useEffect, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
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

const pageVariants = {
  initial: { opacity: 0, y: 10 },
  animate: { opacity: 1, y: 0, transition: { duration: 0.3, ease: "easeOut" as const } },
  exit: { opacity: 0, transition: { duration: 0.15 } },
};

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
      <div className="flex min-h-screen items-center justify-center">
        <div className="h-3 w-3 rounded-full bg-white/10 animate-pulse" />
      </div>
    );
  }

  return (
    <div className="min-h-screen">
      <Sidebar
        current={page}
        onChange={(p) => setPage(p as Page)}
        upstream={upstream}
        isUp={isUp}
        version={version}
        feedbackPending={feedbackPending}
      />

      <main className="ml-[240px] min-h-screen relative">
        <div className="px-10 py-10 max-w-[1200px] mx-auto">
          <AnimatePresence mode="wait">
            <motion.div
              key={page}
              variants={pageVariants}
              initial="initial"
              animate="animate"
              exit="exit"
            >
              {page === "home" && <Home stats={stats} health={health} />}
              {page === "routing" && <Routing onRefresh={refresh} />}
              {page === "activity" && <Activity stats={stats} />}
              {page === "models" && <Models mapping={mapping} />}
              {page === "connections" && <Connections initialConnection={health?.connections ?? null} onRefresh={refresh} />}
              {page === "budget" && <SpendPanel spend={spend} onRefresh={refresh} />}
              {page === "playground" && <Playground />}
              {page === "explain" && <Explainer />}
              {page === "feedback" && <Feedback />}
            </motion.div>
          </AnimatePresence>
        </div>
      </main>
    </div>
  );
}
