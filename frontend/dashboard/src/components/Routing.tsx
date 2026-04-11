import { useCallback, useEffect, useState } from "react";
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
      {notice ? (
        <div
          className="pointer-events-none fixed right-8 top-8 z-50"
          style={{ opacity: 1, transition: "opacity 200ms ease-out" }}
        >
          <div
            className={`max-w-[420px] rounded-compact border px-4 py-3 ${
              notice.tone === "error"
                ? "border-n-accent bg-n-surface text-n-accent"
                : "border-n-success bg-n-surface text-n-success"
            }`}
          >
            <div className="font-mono text-[13px]">{notice.text}</div>
          </div>
        </div>
      ) : null}

      <div className="space-y-6 animate-fadeIn">
        <div>
          <h1 className="font-display text-[36px] text-n-display tracking-tight">ROUTING</h1>
          <p className="mt-1 text-[13px] text-n-secondary">
            Choose the default mode used when a request does not explicitly set a virtual model.
          </p>
        </div>

        <div className="rounded-card border border-n-border bg-n-surface p-6">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="max-w-[640px]">
              <div className="label">Default Mode</div>
              <h2 className="mt-2 text-[18px] font-semibold tracking-tight text-n-display">
                Choose the router's starting bias
              </h2>
              <p className="mt-1 text-[13px] text-n-secondary">
                Used when a request omits `model`. Explicit models still win.
              </p>
            </div>
            <div className="flex items-center gap-3">
              <button
                disabled={!editable || busyKey === "reset"}
                onClick={handleReset}
                className="rounded-pill border border-n-border-vis px-4 py-2 font-mono text-[12px] uppercase tracking-wider text-n-secondary transition-colors hover:border-n-primary hover:text-n-primary disabled:opacity-40"
              >
                Reset All
              </button>
            </div>
          </div>

          {/* Segmented control for mode selection */}
          <div className="mt-6 flex gap-[2px] rounded-compact bg-n-black p-[2px]">
            {MODES.map((mode) => {
              const active = mode === defaultMode;
              return (
                <button
                  key={mode}
                  type="button"
                  aria-pressed={active}
                  disabled={!editable || modeSwitchBusy}
                  onClick={() => handleModeChange(mode)}
                  className={`flex-1 rounded-[6px] px-4 py-4 text-left transition-colors disabled:opacity-40 ${
                    active
                      ? "bg-n-raised text-n-display"
                      : "bg-transparent text-n-secondary hover:bg-n-surface hover:text-n-primary"
                  }`}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <div className="flex items-center gap-2">
                        <span className={`h-2 w-2 rounded-full ${active ? "bg-n-display" : "bg-n-disabled"}`} />
                        <span className="font-mono text-[13px] font-semibold uppercase tracking-wider">{mode}</span>
                      </div>
                      <div className="mt-2 text-[12px] text-n-secondary">{getModeMeta(mode).description}</div>
                    </div>
                    {active ? (
                      <span className="label rounded-pill border border-n-border-vis px-2 py-0.5 text-[12px]">
                        ACTIVE
                      </span>
                    ) : null}
                  </div>
                </button>
              );
            })}
          </div>

          {/* Current behavior summary */}
          <div className="mt-4 border-t border-n-border pt-4">
            <div className="flex items-start gap-3">
              <span className="mt-1 h-2 w-2 rounded-full bg-n-display" />
              <div>
                <div className="label">Current behavior</div>
                <div className="mt-1 flex items-center gap-2">
                  <span className="font-mono text-[14px] font-semibold uppercase text-n-display">{defaultMode}</span>
                  <span className="text-[12px] text-n-secondary">{modeMeta.description}</span>
                </div>
                <p className="mt-2 text-[13px] text-n-secondary">{modeMeta.summary}</p>
              </div>
            </div>
          </div>
        </div>

        <div className="rounded-card border border-n-border bg-n-surface p-6">
          <div className="flex items-start justify-between gap-6">
            <div>
              <div className="label">Advanced Routing</div>
              <h2 className="mt-2 text-[18px] font-semibold tracking-tight text-n-display">
                Override any mode, tier by tier
              </h2>
              <p className="mt-1 text-[13px] text-n-secondary">
                Defaults are discovery-managed. Add an explicit primary only when you want to pin behavior away from the live pool.
              </p>
            </div>
            <div className="border border-n-border rounded-compact px-4 py-3 text-right">
              <div className="label">Model suggestions</div>
              <div className="mt-1 font-mono text-[14px] font-semibold text-n-display">{modelOptions.length}</div>
              <div className="mt-1 text-[12px] text-n-secondary">
                {modelOptions.length > 0 ? "discovered for autocomplete" : "type any model id manually"}
              </div>
            </div>
          </div>

          {/* Editor mode segmented control */}
          <div className="mt-6">
            <div className="label">Edit mode</div>
            <div className="mt-2 inline-flex gap-[2px] rounded-compact bg-n-black p-[2px]">
              {MODES.map((mode) => {
                const active = mode === editorMode;
                return (
                  <button
                    key={mode}
                    onClick={() => setEditorMode(mode)}
                    className={`rounded-[6px] px-4 py-2.5 font-mono text-[12px] uppercase tracking-wider transition-colors ${
                      active ? "bg-n-raised text-n-display" : "text-n-secondary hover:text-n-primary"
                    }`}
                  >
                    {mode}
                  </button>
                );
              })}
            </div>
            <div className="mt-3 text-[13px] text-n-secondary">
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
        </div>

        <div className="rounded-card border border-n-border bg-n-surface p-6">
          <div className="label">CLI</div>
          <div className="mt-2 text-[18px] font-semibold tracking-tight text-n-display">Same switch from terminal</div>
          <pre className="mt-4 overflow-x-auto rounded-compact border border-n-border bg-n-black px-4 py-4 font-mono text-[12px] leading-relaxed text-n-secondary">
{`uncommon-route config show
uncommon-route config set-default-mode ${defaultMode}
uncommon-route config set-tier ${editorMode} SIMPLE openai/gpt-4o-mini --fallback anthropic/claude-haiku-4.5 --strategy hard-pin
uncommon-route config reset-tier ${editorMode} SIMPLE
uncommon-route route "hello"
uncommon-route route --mode best "design a distributed database"`}
          </pre>
        </div>
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
    <div className="rounded-compact border border-n-border bg-n-raised px-4 py-4 transition-micro hover:border-n-border-vis">
      <div className="flex items-center justify-between">
        <span className="font-mono text-[12px] font-semibold uppercase tracking-wider text-n-display">{tier}</span>
        {overridden ? (
          <span className="rounded-pill border border-n-accent px-2 py-0.5 font-mono text-[12px] uppercase tracking-wider text-n-accent">
            OVERRIDE
          </span>
        ) : (
          <span className="rounded-pill border border-n-border-vis px-2 py-0.5 font-mono text-[12px] uppercase tracking-wider text-n-secondary">
            DEFAULT
          </span>
        )}
      </div>

      <div className="mt-4 font-mono text-[14px] font-semibold tracking-tight text-n-display">
        {discoveryManaged ? "Discovery-managed" : primary}
      </div>
      <div className="mt-1 text-[12px] text-n-secondary">
        {discoveryManaged
          ? "Chosen live from the discovered pool using the current mode policy."
          : `${selectionMode} strategy`}
      </div>

      <div className="mt-4 space-y-2">
        <Row label="STRATEGY" value={selectionMode} />
        <Row
          label="FALLBACK"
          value={fallback.length > 0 ? `${fallback.length} model${fallback.length === 1 ? "" : "s"}` : "none"}
        />
      </div>

      <div className="mt-5 border-t border-n-border pt-4">
        <div className="label">Edit Override</div>

        <div className="mt-3 space-y-3">
          <div>
            <label className="label mb-1.5 block">PRIMARY MODEL</label>
            <input
              list="routing-model-options"
              value={draft.primary}
              onChange={(e) => onChange(mode, tier, { primary: e.target.value })}
              placeholder="e.g. openai/gpt-4o-mini"
              disabled={!editable || saveBusy || resetBusy}
              className="w-full border-b border-n-border-vis bg-transparent px-0 py-2 font-mono text-[13px] text-n-primary placeholder-n-disabled focus:border-n-display focus:outline-none disabled:opacity-50"
            />
          </div>

          <div>
            <label className="label mb-1.5 block">FALLBACK MODELS</label>
            <input
              value={draft.fallbackCsv}
              onChange={(e) => onChange(mode, tier, { fallbackCsv: e.target.value })}
              placeholder="comma separated, optional"
              disabled={!editable || saveBusy || resetBusy}
              className="w-full border-b border-n-border-vis bg-transparent px-0 py-2 font-mono text-[13px] text-n-primary placeholder-n-disabled focus:border-n-display focus:outline-none disabled:opacity-50"
            />
          </div>

          <div>
            <div className="label mb-1.5 block">STRATEGY</div>
            <div className="inline-flex gap-[2px] rounded-compact bg-n-black p-[2px]">
              {(["adaptive", "hard-pin"] as const).map((value) => {
                const active = draft.selectionMode === value;
                return (
                  <button
                    key={value}
                    onClick={() => onChange(mode, tier, { selectionMode: value })}
                    disabled={!editable || saveBusy || resetBusy}
                    className={`rounded-[6px] px-3 py-2 font-mono text-[12px] uppercase tracking-wider transition-colors disabled:opacity-40 ${
                      active ? "bg-n-raised text-n-display" : "text-n-secondary hover:text-n-primary"
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
            className="rounded-pill bg-n-display px-4 py-2.5 font-mono text-[12px] uppercase tracking-wider text-n-black transition-colors hover:bg-n-primary disabled:opacity-40"
          >
            {saveBusy ? "SAVING..." : "SAVE OVERRIDE"}
          </button>
          <button
            disabled={!editable || !overridden || saveBusy || resetBusy}
            onClick={() => void onReset(mode, tier)}
            className="rounded-pill border border-n-border-vis px-4 py-2.5 font-mono text-[12px] uppercase tracking-wider text-n-secondary transition-colors hover:border-n-primary hover:text-n-primary disabled:opacity-40"
          >
            {resetBusy ? "RESETTING..." : "RESET TO DISCOVERY"}
          </button>
        </div>
      </div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between text-[12px]">
      <span className="label">{label}</span>
      <span className="font-mono text-n-primary">{value}</span>
    </div>
  );
}

function getModeMeta(mode: string) {
  switch (mode) {
    case "best":
      return {
        description: "highest quality",
        summary: "Biases toward stronger answers and is the least price-sensitive.",
      };
    case "fast":
      return {
        description: "lighter and faster",
        summary: "Biases toward speed and cost-efficiency while staying capable.",
      };
    default:
      return {
        description: "balanced default",
        summary: "Balances quality, speed, and cost across the discovered pool.",
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
