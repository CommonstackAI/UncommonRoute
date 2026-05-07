/**
 * Nothing Design: App shell
 * OLED black, 200px sidebar, no animated transitions (percussive, not fluid)
 */

import { useState } from "react";
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
import ExplainerNew from "./components/ExplainerNew";
import { LiveDataProvider, useLiveData } from "./state/LiveDataContext";
import { I18nProvider, useI18n } from "./i18n";

type Page = "home" | "playground" | "routing" | "models" | "activity" | "budget" | "feedback" | "connections" | "explain" | "explain_new";

function AppShell() {
  const [page, setPage] = useState<Page>("home");
  const { health, stats, mapping, spend, feedbackPending, ready, refresh } = useLiveData();
  const { t } = useI18n();

  const upstream = health?.upstream?.replace(/^https?:\/\//, "").replace(/\/v1$/, "") ?? "";
  const isUp = health?.model_mapper?.discovered ?? false;
  const version = health?.version ?? "—";

  if (!ready) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-n-black">
        <div className="font-mono text-[11px] tracking-[0.1em] text-n-disabled animate-pulse">
          {t.common.loading}
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
          {page === "home" && <Home stats={stats} />}
          {page === "playground" && <Playground />}
          {page === "explain" && <Explainer />}
          {page === "explain_new" && <ExplainerNew />}
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

export default function App() {
  return (
    <I18nProvider>
      <LiveDataProvider>
        <AppShell />
      </LiveDataProvider>
    </I18nProvider>
  );
}
