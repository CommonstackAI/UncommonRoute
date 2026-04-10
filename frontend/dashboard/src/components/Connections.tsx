import { useCallback, useEffect, useState } from "react";
import {
  deleteProvider,
  fetchConnections,
  fetchProviders,
  saveProvider,
  updateConnections,
  verifyProvider,
  type ConnectionState,
  type ProviderRecord,
} from "../api";

interface Props {
  initialConnection: ConnectionState | null;
  onRefresh: () => void;
}

interface ProviderDraft {
  name: string;
  apiKey: string;
  baseUrl: string;
  modelsCsv: string;
  plan: string;
}

const EMPTY_PROVIDER: ProviderDraft = {
  name: "",
  apiKey: "",
  baseUrl: "",
  modelsCsv: "",
  plan: "",
};

export default function Connections({ initialConnection, onRefresh }: Props) {
  const [connection, setConnection] = useState<ConnectionState | null>(initialConnection);
  const [providers, setProviders] = useState<ProviderRecord[]>([]);
  const [upstream, setUpstream] = useState(initialConnection?.upstream ?? "");
  const [apiKey, setApiKey] = useState("");
  const [providerDraft, setProviderDraft] = useState<ProviderDraft>(EMPTY_PROVIDER);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [message, setMessage] = useState<string>("");

  const load = useCallback(async () => {
    const [nextConnection, nextProviders] = await Promise.all([
      fetchConnections(),
      fetchProviders(),
    ]);
    if (nextConnection) {
      setConnection(nextConnection);
      setUpstream(nextConnection.upstream);
    }
    if (nextProviders) {
      setProviders(nextProviders.providers);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function handleSaveConnection() {
    setBusyKey("primary");
    setMessage("");
    const updated = await updateConnections(upstream.trim(), apiKey.trim());
    if (updated) {
      setConnection(updated);
      setUpstream(updated.upstream);
      setApiKey("");
      setMessage("Primary upstream updated.");
      onRefresh();
    } else {
      setMessage("Failed to update primary upstream.");
    }
    setBusyKey(null);
  }

  async function handleSaveProvider() {
    if (!providerDraft.name.trim() || !providerDraft.apiKey.trim()) {
      setMessage("Provider name and API key are required.");
      return;
    }
    setBusyKey("provider:add");
    setMessage("");
    const result = await saveProvider({
      name: providerDraft.name.trim().toLowerCase(),
      apiKey: providerDraft.apiKey.trim(),
      baseUrl: providerDraft.baseUrl.trim() || undefined,
      models: providerDraft.modelsCsv
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean),
      plan: providerDraft.plan.trim() || undefined,
    });
    if (result) {
      setProviders(result.providers);
      setProviderDraft(EMPTY_PROVIDER);
      setMessage("Provider saved and loaded live.");
      onRefresh();
    } else {
      setMessage("Failed to save provider.");
    }
    setBusyKey(null);
  }

  async function handleRemoveProvider(name: string) {
    setBusyKey(`provider:remove:${name}`);
    setMessage("");
    const result = await deleteProvider(name);
    if (result) {
      setProviders(result.providers);
      setMessage(`Removed provider ${name}.`);
      onRefresh();
    } else {
      setMessage(`Failed to remove provider ${name}.`);
    }
    setBusyKey(null);
  }

  async function handleVerifyProvider(name: string) {
    setBusyKey(`provider:verify:${name}`);
    setMessage("");
    const result = await verifyProvider(name);
    if (result) {
      setMessage(result.ok ? `${name}: ${result.detail}` : `${name}: ${result.detail}`);
    } else {
      setMessage(`Failed to verify provider ${name}.`);
    }
    setBusyKey(null);
  }

  const connectionEditable = connection?.editable ?? true;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-n-display">Connections</h1>
        <p className="mt-1 text-[13px] text-n-secondary">
          Manage the primary upstream and live BYOK provider keys.
        </p>
      </div>

      {message ? (
        <div className="rounded-compact border border-n-border bg-n-surface px-4 py-3 font-mono text-[13px] text-n-primary">
          {message}
        </div>
      ) : null}

      {/* Primary Upstream */}
      <div className="rounded-card border border-n-border bg-n-surface p-6">
        <div className="flex items-start justify-between gap-6">
          <div>
            <div className="label">Primary Upstream</div>
            <h2 className="mt-2 text-[18px] font-semibold tracking-tight text-n-display">Runtime connection</h2>
            <p className="mt-1 text-[13px] text-n-secondary">
              Source: {connection?.source ?? "unknown"} · Provider: {connection?.provider ?? "unknown"} ·
              {` `}{connection?.discovered ? "live catalog" : "static catalog"}
            </p>
          </div>
          <div className="min-w-[240px] rounded-compact border border-n-border px-4 py-3 text-right">
            <div className="label">Current</div>
            <div className="mt-2 break-all font-mono text-[13px] text-n-primary">
              {connection?.upstream || "Not configured"}
            </div>
            <div className="mt-2 text-[12px] text-n-secondary">
              Key: {connection?.api_key_preview || "none"}
            </div>
          </div>
        </div>

        <div className="mt-6 grid grid-cols-2 gap-4">
          <Field
            label="UPSTREAM URL"
            value={upstream}
            onChange={setUpstream}
            placeholder="https://api.commonstack.ai/v1"
            disabled={!connectionEditable || busyKey === "primary"}
          />
          <Field
            label="API KEY"
            value={apiKey}
            onChange={setApiKey}
            placeholder={connection?.has_api_key ? "Leave empty to keep current key" : "sk-..."}
            disabled={!connectionEditable || busyKey === "primary"}
            type="password"
          />
        </div>

        <div className="mt-4 flex items-center justify-between">
          <div className="text-[12px] text-n-secondary">
            {connectionEditable
              ? "Changes apply live after validation succeeds."
              : `Locked by ${connection?.source ?? "external source"}.`}
          </div>
          <button
            disabled={!connectionEditable || busyKey === "primary"}
            onClick={handleSaveConnection}
            className="rounded-pill bg-n-display px-5 py-2.5 font-mono text-[13px] uppercase tracking-wider text-n-black transition-colors hover:bg-n-primary disabled:opacity-40"
          >
            SAVE PRIMARY
          </button>
        </div>
      </div>

      {/* Provider Keys */}
      <div className="rounded-card border border-n-border bg-n-surface p-6">
        <div className="flex items-center justify-between">
          <div>
            <div className="label">Provider Keys</div>
            <h2 className="mt-2 text-[18px] font-semibold tracking-tight text-n-display">Bring your own keys</h2>
            <p className="mt-1 text-[13px] text-n-secondary">
              Save provider credentials and make them available immediately to routing.
            </p>
          </div>
          <div className="font-mono text-[13px] text-n-secondary">
            {providers.length} configured
          </div>
        </div>

        <div className="mt-6 grid grid-cols-2 gap-4">
          <Field
            label="PROVIDER NAME"
            value={providerDraft.name}
            onChange={(value) => setProviderDraft((prev) => ({ ...prev, name: value }))}
            placeholder="openai"
            disabled={busyKey === "provider:add"}
          />
          <Field
            label="API KEY"
            value={providerDraft.apiKey}
            onChange={(value) => setProviderDraft((prev) => ({ ...prev, apiKey: value }))}
            placeholder="sk-..."
            disabled={busyKey === "provider:add"}
            type="password"
          />
          <Field
            label="BASE URL"
            value={providerDraft.baseUrl}
            onChange={(value) => setProviderDraft((prev) => ({ ...prev, baseUrl: value }))}
            placeholder="Optional override"
            disabled={busyKey === "provider:add"}
          />
          <Field
            label="PLAN"
            value={providerDraft.plan}
            onChange={(value) => setProviderDraft((prev) => ({ ...prev, plan: value }))}
            placeholder="Optional note"
            disabled={busyKey === "provider:add"}
          />
        </div>

        <div className="mt-4">
          <label className="label mb-1.5 block">MODELS (COMMA SEPARATED)</label>
          <input
            value={providerDraft.modelsCsv}
            onChange={(e) => setProviderDraft((prev) => ({ ...prev, modelsCsv: e.target.value }))}
            placeholder="Optional explicit model list"
            disabled={busyKey === "provider:add"}
            className="w-full border-b border-n-border-vis bg-transparent px-0 py-2 font-mono text-[13px] text-n-primary placeholder-n-disabled focus:border-n-display focus:outline-none"
          />
        </div>

        <div className="mt-4 flex justify-end">
          <button
            disabled={busyKey === "provider:add"}
            onClick={handleSaveProvider}
            className="rounded-pill bg-n-display px-5 py-2.5 font-mono text-[13px] uppercase tracking-wider text-n-black transition-colors hover:bg-n-primary disabled:opacity-40"
          >
            ADD PROVIDER
          </button>
        </div>
      </div>

      {/* Configured Providers Table */}
      <div className="rounded-card border border-n-border bg-n-surface overflow-hidden">
        <div className="border-b border-n-border px-6 py-4">
          <span className="label">Configured Providers</span>
        </div>
        <table className="w-full">
          <thead>
            <tr className="border-b border-n-border">
              <th className="label px-6 py-3 text-left">NAME</th>
              <th className="label px-6 py-3 text-left">BASE URL</th>
              <th className="label px-6 py-3 text-left">MODELS</th>
              <th className="label px-6 py-3 text-left">KEY</th>
              <th className="label px-6 py-3 text-right"></th>
            </tr>
          </thead>
          <tbody>
            {providers.length === 0 ? (
              <tr>
                <td colSpan={5} className="py-16 text-center font-mono text-[14px] text-n-disabled">
                  No provider keys saved yet.
                </td>
              </tr>
            ) : (
              providers.map((provider) => (
                <tr key={provider.name} className="border-b border-n-border last:border-0 transition-colors hover:bg-n-raised">
                  <td className="px-6 py-4 font-mono text-[13px] font-semibold text-n-display">{provider.name}</td>
                  <td className="px-6 py-4 font-mono text-[12px] text-n-secondary">{provider.base_url || "\u2014"}</td>
                  <td className="px-6 py-4 font-mono text-[13px] text-n-primary">
                    {provider.model_count > 0 ? `${provider.model_count} models` : "Default set"}
                  </td>
                  <td className="px-6 py-4 font-mono text-[12px] text-n-secondary">{provider.api_key_preview || "\u2014"}</td>
                  <td className="px-6 py-4">
                    <div className="flex justify-end gap-2">
                      <button
                        disabled={busyKey === `provider:verify:${provider.name}`}
                        onClick={() => handleVerifyProvider(provider.name)}
                        className="rounded-pill border border-n-border-vis px-3 py-1.5 font-mono text-[12px] uppercase tracking-wider text-n-secondary transition-colors hover:border-n-primary hover:text-n-primary disabled:opacity-40"
                      >
                        VERIFY
                      </button>
                      <button
                        disabled={busyKey === `provider:remove:${provider.name}`}
                        onClick={() => handleRemoveProvider(provider.name)}
                        className="rounded-pill border border-n-accent px-3 py-1.5 font-mono text-[12px] uppercase tracking-wider text-n-accent transition-colors hover:bg-n-accent hover:text-n-display disabled:opacity-40"
                      >
                        REMOVE
                      </button>
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

interface FieldProps {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  disabled?: boolean;
  type?: string;
}

function Field({ label, value, onChange, placeholder, disabled = false, type = "text" }: FieldProps) {
  return (
    <div>
      <label className="label mb-1.5 block">{label}</label>
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        disabled={disabled}
        className="w-full border-b border-n-border-vis bg-transparent px-0 py-2 font-mono text-[13px] text-n-primary placeholder-n-disabled focus:border-n-display focus:outline-none disabled:opacity-50"
      />
    </div>
  );
}
