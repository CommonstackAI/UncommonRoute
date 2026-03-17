import { useCallback, useEffect, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  fetchMapping,
  fetchRoutingConfig,
  resetRoutingTier,
  resetRoutingConfig,
  setDefaultRoutingMode,
  setRoutingTier,
  type Mapping,
  type RoutingConfigState,
  type RoutingTierConfig,
} from "../api";
import { Card } from "./ui/Card";

const MODES = ["auto", "fast", "best"] as const;
const TIERS = ["SIMPLE", "MEDIUM", "COMPLEX"] as const;
type ModeName = (typeof MODES)[number];
type TierName = (typeof TIERS)[number];

interface DraftState {
  primary: string;
  fallbackCsv: string;
  selectionMode: "adaptive" | "hard-pin";
}

interface NoticeState {
  text: string;
  tone: "success" | "error";
}

interface Props {
  onRefresh?: () => void;
}

export default function Routing({ onRefresh }: Props) {
  const [config, setConfig] = useState<RoutingConfigState | null>(null);
  const [mapping, setMapping] = useState<Mapping | null>(null);
  const [drafts, setDrafts] = useState<Record<string, DraftState>>({});
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [notice, setNotice] = useState<NoticeState | null>(null);
  const [editorMode, setEditorMode] = useState<ModeName>("auto");

  const load = useCallback(async () => {
    const [nextConfig, nextMapping] = await Promise.all([
      fetchRoutingConfig(),
      fetchMapping(),
    ]);
    if (nextConfig) {
      setConfig(nextConfig);
      setDrafts(buildDrafts(nextConfig));
      setEditorMode(nextConfig.default_mode as ModeName);
    }
    if (nextMapping) setMapping(nextMapping);
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!notice) return;
    const timeoutId = window.setTimeout(() => setNotice(null), 2400);
    return () => window.clearTimeout(timeoutId);
  }, [notice]);

  const defaultMode = config?.default_mode ?? "auto";
  const editable = config?.editable ?? true;
  const modeMeta = getModeMeta(defaultMode);
  const editModeMeta = getModeMeta(editorMode);
  const selectedModeRows = config?.modes?.[editorMode]?.tiers ?? {};
  const modelOptions = mapping?.pool.map((model) => model.id) ?? [];
  const modeSwitchBusy = busyKey?.startsWith("mode:") ?? false;

  function applyConfig(next: RoutingConfigState) {
    setConfig(next);
    setDrafts(buildDrafts(next));
  }

  function showNotice(text: string, tone: NoticeState["tone"] = "success") {
    setNotice({ text, tone });
  }

  async function handleModeChange(nextMode: ModeName) {
    if (!editable || nextMode === defaultMode) return;
    setBusyKey(`mode:${nextMode}`);
    setNotice(null);
    const next = await setDefaultRoutingMode(nextMode);
    if (next) {
      applyConfig(next);
      showNotice(`Default mode set to ${nextMode}.`);
      onRefresh?.();
    } else {
      showNotice("Failed to update default mode.", "error");
    }
    setBusyKey(null);
  }

  async function handleReset() {
    if (!editable) return;
    setBusyKey("reset");
    setNotice(null);
    const next = await resetRoutingConfig();
    if (next) {
      applyConfig(next);
      showNotice("Routing config reset to discovery-managed defaults.");
      onRefresh?.();
    } else {
      showNotice("Failed to reset routing config.", "error");
    }
    setBusyKey(null);
  }

  function updateDraft(mode: ModeName, tier: TierName, patch: Partial<DraftState>) {
    const key = draftKey(mode, tier);
    setDrafts((prev) => ({
      ...prev,
      [key]: {
        ...(prev[key] ?? createDraft()),
        ...patch,
      },
    }));
  }

  async function handleSaveOverride(mode: ModeName, tier: TierName) {
    const draft = drafts[draftKey(mode, tier)] ?? createDraft();
    const primary = draft.primary.trim();
    if (!primary) {
      showNotice(`Primary model is required to save ${mode} / ${tier}.`, "error");
      return;
    }

    setBusyKey(`save:${mode}:${tier}`);
    setNotice(null);
    const next = await setRoutingTier(
      mode,
      tier,
      primary,
      parseCsv(draft.fallbackCsv),
      draft.selectionMode,
    );
    if (next) {
      applyConfig(next);
      showNotice(`Saved override for ${mode} / ${tier}.`);
      onRefresh?.();
    } else {
      showNotice(`Failed to save override for ${mode} / ${tier}.`, "error");
    }
    setBusyKey(null);
  }

  async function handleResetOverride(mode: ModeName, tier: TierName) {
    setBusyKey(`reset:${mode}:${tier}`);
    setNotice(null);
    const next = await resetRoutingTier(mode, tier);
    if (next) {
      applyConfig(next);
      showNotice(`Reset ${mode} / ${tier} to discovery-managed defaults.`);
      onRefresh?.();
    } else {
      showNotice(`Failed to reset ${mode} / ${tier}.`, "error");
    }
    setBusyKey(null);
  }

  return (
    <>
      <AnimatePresence>
        {notice ? (
          <motion.div
            initial={{ opacity: 0, y: -12, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -8, scale: 0.98 }}
            transition={{ duration: 0.18, ease: "easeOut" }}
            className="pointer-events-none fixed right-8 top-8 z-50"
          >
            <div
              className={`max-w-[420px] rounded-2xl px-4 py-3 shadow-[0_18px_60px_-24px_rgba(15,23,42,0.35)] ring-1 backdrop-blur ${
                notice.tone === "error"
                  ? "bg-rose-50/95 text-rose-700 ring-rose-200/80"
                  : "bg-white/95 text-[#374151] ring-black/[0.06]"
              }`}
            >
              <div className="text-[13px] font-medium">{notice.text}</div>
            </div>
          </motion.div>
        ) : null}
      </AnimatePresence>

      <div className="space-y-6">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-[#111827]">Routing</h1>
          <p className="mt-1 text-[13px] font-medium text-[#6B7280]">
            Choose the default mode used when a request does not explicitly set a virtual model.
          </p>
        </div>

      <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }}>
        <Card className="p-6">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="max-w-[640px]">
              <div className="text-[12px] font-medium uppercase tracking-wider text-[#9CA3AF]">Default Mode</div>
              <h2 className="mt-2 text-[18px] font-semibold tracking-tight text-[#111827]">
                Choose the router's starting bias
              </h2>
              <p className="mt-1 text-[13px] font-medium text-[#6B7280]">
                Used when a request omits `model`. Explicit models still win.
              </p>
            </div>
            <div className="flex items-center gap-3">
              <button
                disabled={!editable || busyKey === "reset"}
                onClick={handleReset}
                className="rounded-xl border border-black/[0.06] bg-white px-4 py-2 text-[12px] font-medium text-[#6B7280] shadow-sm transition-colors hover:bg-gray-50 hover:text-[#111827] disabled:opacity-40"
              >
                Reset All
              </button>
            </div>
          </div>

          <div className="mt-6 grid gap-2 rounded-[28px] bg-[#F8FAFC] p-2 ring-1 ring-black/[0.04] md:grid-cols-3">
            {MODES.map((mode) => {
              const active = mode === defaultMode;
              const meta = getModeMeta(mode);
              return (
                <button
                  key={mode}
                  type="button"
                  aria-pressed={active}
                  disabled={!editable || modeSwitchBusy}
                  onClick={() => handleModeChange(mode)}
                  className={`rounded-[22px] px-4 py-4 text-left transition-all disabled:opacity-40 ${
                    active
                      ? `bg-white shadow-sm ring-1 ${meta.ring}`
                      : "bg-transparent text-[#6B7280] hover:bg-white/80 hover:text-[#111827]"
                  }`}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <div className="flex items-center gap-2">
                        <span className={`h-2 w-2 rounded-full ${meta.dot}`} />
                        <span className="text-[13px] font-semibold capitalize text-[#111827]">{mode}</span>
                      </div>
                      <div className="mt-2 text-[12px] font-medium text-[#6B7280]">{meta.description}</div>
                    </div>
                    {active ? (
                      <span className="rounded-full bg-[#F9FAFB] px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-[#6B7280]">
                        active
                      </span>
                    ) : null}
                  </div>
                </button>
              );
            })}
          </div>

          <div className={`mt-4 rounded-2xl px-4 py-4 ring-1 ${modeMeta.bg} ${modeMeta.ring}`}>
            <div className="flex items-start gap-3">
              <span className={`mt-1 h-2 w-2 rounded-full ${modeMeta.dot}`} />
              <div>
                <div className="text-[11px] font-medium uppercase tracking-wider text-[#6B7280]">Current behavior</div>
                <div className="mt-1 flex items-center gap-2">
                  <span className="text-[14px] font-semibold capitalize text-[#111827]">{defaultMode}</span>
                  <span className="text-[12px] font-medium text-[#6B7280]">{modeMeta.description}</span>
                </div>
                <p className="mt-2 text-[13px] font-medium text-[#4B5563]">{modeMeta.summary}</p>
              </div>
            </div>
          </div>
        </Card>
      </motion.div>

      <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.04 }}>
        <Card className="p-6">
          <div className="flex items-start justify-between gap-6">
            <div>
              <div className="text-[12px] font-medium uppercase tracking-wider text-[#9CA3AF]">Advanced Routing</div>
              <h2 className="mt-2 text-[18px] font-semibold tracking-tight text-[#111827]">
                Override any mode, tier by tier
              </h2>
              <p className="mt-1 text-[13px] font-medium text-[#6B7280]">
                Defaults are discovery-managed. Add an explicit primary only when you want to pin behavior away from the live pool.
              </p>
            </div>
            <div className="rounded-2xl bg-gray-50/80 px-4 py-3 text-right ring-1 ring-black/[0.04]">
              <div className="text-[11px] font-medium uppercase tracking-wider text-[#9CA3AF]">Model suggestions</div>
              <div className="mt-1 text-[14px] font-semibold text-[#111827]">{modelOptions.length}</div>
              <div className="mt-1 text-[12px] font-medium text-[#6B7280]">
                {modelOptions.length > 0 ? "discovered for autocomplete" : "type any model id manually"}
              </div>
            </div>
          </div>

          <div className="mt-6">
            <div className="text-[11px] font-medium uppercase tracking-wider text-[#9CA3AF]">Edit mode</div>
            <div className="mt-2 inline-flex rounded-2xl bg-gray-50/90 p-1 ring-1 ring-black/[0.04]">
              {MODES.map((mode) => {
                const active = mode === editorMode;
                return (
                  <button
                    key={mode}
                    onClick={() => setEditorMode(mode)}
                    className={`rounded-[14px] px-4 py-2.5 text-[12px] font-semibold capitalize transition-all ${
                      active ? "bg-white text-[#111827] shadow-sm ring-1 ring-black/[0.04]" : "text-[#6B7280] hover:text-[#111827]"
                    }`}
                  >
                    {mode}
                  </button>
                );
              })}
            </div>
            <div className="mt-3 text-[13px] font-medium text-[#6B7280]">
              Editing `{editorMode}`: {editModeMeta.description}.
            </div>
          </div>

          <datalist id="routing-model-options">
            {modelOptions.map((modelId) => (
              <option key={modelId} value={modelId} />
            ))}
          </datalist>

          <div className="mt-6 grid grid-cols-3 gap-3">
            {TIERS.map((tier) => (
              <EditableTierCard
                key={tier}
                mode={editorMode}
                tier={tier}
                row={selectedModeRows[tier]}
                draft={drafts[draftKey(editorMode, tier)] ?? createDraft(selectedModeRows[tier])}
                editable={editable}
                busyKey={busyKey}
                onChange={updateDraft}
                onSave={handleSaveOverride}
                onReset={handleResetOverride}
              />
            ))}
          </div>
        </Card>
      </motion.div>

        <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.08 }}>
          <Card className="p-6">
            <div className="text-[12px] font-medium uppercase tracking-wider text-[#9CA3AF]">CLI</div>
            <div className="mt-2 text-[18px] font-semibold tracking-tight text-[#111827]">Same switch from terminal</div>
            <pre className="mt-4 overflow-x-auto rounded-2xl bg-[#F9FAFB] px-4 py-4 text-[12px] leading-relaxed text-[#4B5563] ring-1 ring-black/[0.04]">
{`uncommon-route config show
uncommon-route config set-default-mode ${defaultMode}
uncommon-route config set-tier ${editorMode} SIMPLE openai/gpt-4o-mini --fallback anthropic/claude-haiku-4.5 --strategy hard-pin
uncommon-route config reset-tier ${editorMode} SIMPLE
uncommon-route route "hello"
uncommon-route route --mode best "design a distributed database"`}
            </pre>
          </Card>
        </motion.div>
      </div>
    </>
  );
}

function EditableTierCard({
  mode,
  tier,
  row,
  draft,
  editable,
  busyKey,
  onChange,
  onSave,
  onReset,
}: {
  mode: ModeName;
  tier: TierName;
  row: RoutingTierConfig | undefined;
  draft: DraftState;
  editable: boolean;
  busyKey: string | null;
  onChange: (mode: ModeName, tier: TierName, patch: Partial<DraftState>) => void;
  onSave: (mode: ModeName, tier: TierName) => Promise<void>;
  onReset: (mode: ModeName, tier: TierName) => Promise<void>;
}) {
  const primary = row?.primary?.trim() || "";
  const fallback = row?.fallback ?? [];
  const overridden = row?.overridden ?? false;
  const selectionMode = row?.selection_mode ?? "adaptive";
  const discoveryManaged = primary.length === 0;
  const saveBusy = busyKey === `save:${mode}:${tier}`;
  const resetBusy = busyKey === `reset:${mode}:${tier}`;

  return (
    <div className="rounded-2xl bg-white px-4 py-4 ring-1 ring-black/[0.04]">
      <div className="flex items-center justify-between">
        <span className="text-[12px] font-semibold tracking-wide text-[#111827]">{tier}</span>
        {overridden ? (
          <span className="rounded-full bg-indigo-50 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-indigo-600">
            override
          </span>
        ) : (
          <span className="rounded-full bg-gray-50 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-[#9CA3AF]">
            default
          </span>
        )}
      </div>

      <div className="mt-4 text-[14px] font-semibold tracking-tight text-[#111827]">
        {discoveryManaged ? "Discovery-managed" : primary}
      </div>
      <div className="mt-1 text-[12px] font-medium text-[#6B7280]">
        {discoveryManaged
          ? "Chosen live from the discovered pool using the current mode policy."
          : `${selectionMode} strategy`}
      </div>

      <div className="mt-4 space-y-2">
        <Row label="Strategy" value={selectionMode} />
        <Row
          label="Fallback"
          value={fallback.length > 0 ? `${fallback.length} model${fallback.length === 1 ? "" : "s"}` : "none"}
        />
      </div>

      <div className="mt-5 border-t border-black/[0.04] pt-4">
        <div className="text-[11px] font-medium uppercase tracking-wider text-[#9CA3AF]">Edit Override</div>

        <div className="mt-3 space-y-3">
          <div>
            <label className="mb-1.5 block text-[12px] font-medium text-[#6B7280]">Primary model</label>
            <input
              list="routing-model-options"
              value={draft.primary}
              onChange={(e) => onChange(mode, tier, { primary: e.target.value })}
              placeholder="e.g. openai/gpt-4o-mini"
              disabled={!editable || saveBusy || resetBusy}
              className="w-full rounded-xl border border-black/[0.06] bg-white px-3 py-2.5 text-[13px] font-medium text-[#111827] shadow-sm focus:outline-none focus:ring-2 focus:ring-indigo-500/20 disabled:opacity-50"
            />
          </div>

          <div>
            <label className="mb-1.5 block text-[12px] font-medium text-[#6B7280]">Fallback models</label>
            <input
              value={draft.fallbackCsv}
              onChange={(e) => onChange(mode, tier, { fallbackCsv: e.target.value })}
              placeholder="comma separated, optional"
              disabled={!editable || saveBusy || resetBusy}
              className="w-full rounded-xl border border-black/[0.06] bg-white px-3 py-2.5 text-[13px] font-medium text-[#111827] shadow-sm focus:outline-none focus:ring-2 focus:ring-indigo-500/20 disabled:opacity-50"
            />
          </div>

          <div>
            <div className="mb-1.5 block text-[12px] font-medium text-[#6B7280]">Strategy</div>
            <div className="inline-flex rounded-xl bg-gray-50/90 p-1 ring-1 ring-black/[0.04]">
              {(["adaptive", "hard-pin"] as const).map((value) => {
                const active = draft.selectionMode === value;
                return (
                  <button
                    key={value}
                    onClick={() => onChange(mode, tier, { selectionMode: value })}
                    disabled={!editable || saveBusy || resetBusy}
                    className={`rounded-[10px] px-3 py-2 text-[12px] font-medium transition-all disabled:opacity-40 ${
                      active ? "bg-white text-[#111827] shadow-sm ring-1 ring-black/[0.04]" : "text-[#6B7280] hover:text-[#111827]"
                    }`}
                  >
                    {value}
                  </button>
                );
              })}
            </div>
          </div>
        </div>

        <div className="mt-4 flex gap-2">
          <button
            disabled={!editable || !draft.primary.trim() || saveBusy || resetBusy}
            onClick={() => void onSave(mode, tier)}
            className="rounded-xl bg-[#111827] px-4 py-2.5 text-[12px] font-medium text-white shadow-sm transition-colors hover:bg-black disabled:opacity-40"
          >
            {saveBusy ? "Saving..." : "Save override"}
          </button>
          <button
            disabled={!editable || !overridden || saveBusy || resetBusy}
            onClick={() => void onReset(mode, tier)}
            className="rounded-xl border border-black/[0.06] bg-white px-4 py-2.5 text-[12px] font-medium text-[#6B7280] shadow-sm transition-colors hover:bg-gray-50 hover:text-[#111827] disabled:opacity-40"
          >
            {resetBusy ? "Resetting..." : "Reset to discovery"}
          </button>
        </div>
      </div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between text-[12px] font-medium">
      <span className="text-[#9CA3AF]">{label}</span>
      <span className="text-[#4B5563]">{value}</span>
    </div>
  );
}

function getModeMeta(mode: string) {
  switch (mode) {
    case "best":
      return {
        description: "highest quality",
        summary: "Biases toward stronger answers and is the least price-sensitive.",
        bg: "bg-rose-50/80",
        ring: "ring-rose-500/10",
        dot: "bg-rose-500",
      };
    case "fast":
      return {
        description: "lighter and faster",
        summary: "Biases toward speed and cost-efficiency while staying capable.",
        bg: "bg-sky-50/80",
        ring: "ring-sky-500/10",
        dot: "bg-sky-500",
      };
    default:
      return {
        description: "balanced default",
        summary: "Balances quality, speed, and cost across the discovered pool.",
        bg: "bg-emerald-50/80",
        ring: "ring-emerald-500/10",
        dot: "bg-emerald-500",
      };
  }
}

function draftKey(mode: ModeName, tier: TierName): string {
  return `${mode}:${tier}`;
}

function createDraft(row?: RoutingTierConfig): DraftState {
  if (!row?.overridden) {
    return {
      primary: "",
      fallbackCsv: "",
      selectionMode: row?.selection_mode ?? "adaptive",
    };
  }
  return {
    primary: row.primary,
    fallbackCsv: row.fallback.join(", "),
    selectionMode: row.selection_mode,
  };
}

function buildDrafts(config: RoutingConfigState): Record<string, DraftState> {
  const next: Record<string, DraftState> = {};
  for (const mode of MODES) {
    const rows = config.modes?.[mode]?.tiers ?? {};
    for (const tier of TIERS) {
      next[draftKey(mode, tier)] = createDraft(rows[tier]);
    }
  }
  return next;
}

function parseCsv(value: string): string[] {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}
