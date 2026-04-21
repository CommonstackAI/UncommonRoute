export interface Health {
  status: string;
  version: string;
  upstream: string;
  connections?: ConnectionState;
  spending: {
    limits: Record<string, number>;
    spent: Record<string, number>;
    remaining: Record<string, number>;
    calls: number;
  };
  providers: { count: number; names: string[]; keyed_models: string[] };
  model_mapper: {
    provider: string;
    is_gateway: boolean;
    discovered: boolean;
    upstream_models: number;
    pool_size: number;
    unresolved: string[];
    pricing_source: string;
  };
  stats: { total_requests: number };
  feedback: { pending: number; total_updates: number; online_model: boolean };
  routing_config?: { source: string; editable: boolean; default_mode: string };
}

export interface TierStats {
  count: number;
  avg_confidence: number;
  avg_savings: number;
  total_cost: number;
}

export interface ModelStats {
  count: number;
  total_cost: number;
}

export interface Stats {
  total_requests: number;
  time_range_s: number;
  avg_confidence: number;
  avg_savings: number;
  avg_latency_ms: number;
  avg_input_reduction_ratio: number;
  avg_cache_hit_ratio: number;
  total_estimated_cost: number;
  total_baseline_cost: number;
  total_actual_cost: number;
  total_savings_absolute: number;
  total_savings_ratio: number;
  total_cache_savings: number;
  total_compaction_savings: number;
  total_cache_breakpoints: number;
  total_input_tokens_before: number;
  total_input_tokens_after: number;
  by_mode: Record<string, number>;
  by_tier: Record<string, TierStats>;
  by_served_quality: Record<string, number>;
  by_capability_lane: Record<string, number>;
  by_model: Record<string, ModelStats>;
  by_transport: Record<string, ModelStats>;
  by_cache_mode: Record<string, ModelStats>;
  by_cache_family: Record<string, ModelStats>;
  by_method: Record<string, number>;
}

export interface PoolModel {
  id: string;
  provider: string;
  owned_by: string;
  pricing: {
    input: number;
    output: number;
    cached_input: number | null;
    cache_write: number | null;
  };
  capabilities: {
    tool_calling: boolean;
    vision: boolean;
    reasoning: boolean;
    free: boolean;
  };
}

export interface Mapping {
  provider: string;
  is_gateway: boolean;
  discovered: boolean;
  upstream_model_count: number;
  pool_size: number;
  pool: PoolModel[];
  unresolved: string[];
  pricing_source: string;
}

export interface Spend {
  limits: Record<string, number>;
  spent: Record<string, number>;
  remaining: Record<string, number>;
  calls: number;
}

export interface RoutingTierConfig {
  primary: string;
  fallback: string[];
  overridden: boolean;
  hard_pin: boolean;
  selection_mode: "adaptive" | "hard-pin";
}

export interface RoutingModeConfig {
  tiers: Record<string, RoutingTierConfig>;
}

export interface RoutingConfigState {
  source: string;
  editable: boolean;
  default_mode: string;
  modes: Record<string, RoutingModeConfig>;
}

export interface ConnectionState {
  source: string;
  upstream_source: string;
  api_key_source: string;
  editable: boolean;
  upstream: string;
  has_api_key: boolean;
  api_key_preview: string;
  provider: string;
  is_gateway: boolean;
  discovered: boolean;
  upstream_model_count: number;
  pool_size: number;
  unresolved: string[];
  pricing_source: string;
}

export interface ProviderRecord {
  name: string;
  base_url: string;
  models: string[];
  model_count: number;
  plan: string;
  has_api_key: boolean;
  api_key_preview: string;
}

export interface ProvidersState {
  count: number;
  providers: ProviderRecord[];
}

export interface ProviderVerificationResult {
  ok: boolean;
  detail: string;
  provider: {
    name: string;
    base_url: string;
    model_count: number;
    api_key_preview: string;
  };
}

async function get<T>(path: string): Promise<T | null> {
  try {
    const res = await fetch(path);
    return res.ok ? ((await res.json()) as T) : null;
  } catch {
    return null;
  }
}

export const fetchHealth = () => get<Health>("/health");
export const fetchConnections = () => get<ConnectionState>("/v1/connections");
export const fetchProviders = () => get<ProvidersState>("/v1/providers");
export const fetchStats = () => get<Stats>("/v1/stats");
export const fetchMapping = () => get<Mapping>("/v1/models/mapping");
export const fetchSpend = () => get<Spend>("/v1/spend");
export const fetchRoutingConfig = () => get<RoutingConfigState>("/v1/routing-config");

export async function updateConnections(
  upstream: string,
  apiKey: string | undefined,
): Promise<ConnectionState | null> {
  try {
    const body: Record<string, string> = { upstream };
    // undefined = keep current key; empty string = clear key; non-empty = set new key
    if (apiKey !== undefined) body.api_key = apiKey;
    const res = await fetch("/v1/connections", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return res.ok ? ((await res.json()) as ConnectionState) : null;
  } catch {
    return null;
  }
}

export async function saveProvider(input: {
  name: string;
  apiKey: string;
  baseUrl?: string;
  models?: string[];
  plan?: string;
  verify?: boolean;
}): Promise<ProvidersState | null> {
  try {
    const res = await fetch("/v1/providers", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: input.name,
        api_key: input.apiKey,
        base_url: input.baseUrl ?? "",
        models: input.models ?? [],
        plan: input.plan ?? "",
        verify: input.verify ?? false,
      }),
    });
    return res.ok ? ((await res.json()) as ProvidersState) : null;
  } catch {
    return null;
  }
}

export async function deleteProvider(name: string): Promise<ProvidersState | null> {
  try {
    const res = await fetch(`/v1/providers/${encodeURIComponent(name)}`, {
      method: "DELETE",
    });
    return res.ok ? ((await res.json()) as ProvidersState) : null;
  } catch {
    return null;
  }
}

export async function verifyProvider(name: string): Promise<ProviderVerificationResult | null> {
  try {
    const res = await fetch(`/v1/providers/${encodeURIComponent(name)}/verify`, {
      method: "POST",
    });
    const text = await res.text();
    if (!text) return null;
    return JSON.parse(text) as ProviderVerificationResult;
  } catch {
    return null;
  }
}

export async function setSpendLimit(
  window: string,
  amount: number,
): Promise<boolean> {
  try {
    const res = await fetch("/v1/spend", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "set", window, amount }),
    });
    return res.ok;
  } catch {
    return false;
  }
}

export async function clearSpendLimit(window: string): Promise<boolean> {
  try {
    const res = await fetch("/v1/spend", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "clear", window }),
    });
    return res.ok;
  } catch {
    return false;
  }
}

export async function setRoutingTier(
  mode: string,
  tier: string,
  primary: string,
  fallback: string[],
  selectionMode: "adaptive" | "hard-pin",
): Promise<RoutingConfigState | null> {
  try {
    const res = await fetch("/v1/routing-config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "set-tier", mode, tier, primary, fallback, selection_mode: selectionMode }),
    });
    return res.ok ? ((await res.json()) as RoutingConfigState) : null;
  } catch {
    return null;
  }
}

export async function setDefaultRoutingMode(mode: string): Promise<RoutingConfigState | null> {
  try {
    const res = await fetch("/v1/routing-config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "set-default-mode", mode }),
    });
    return res.ok ? ((await res.json()) as RoutingConfigState) : null;
  } catch {
    return null;
  }
}

export async function resetRoutingTier(
  mode: string,
  tier: string,
): Promise<RoutingConfigState | null> {
  try {
    const res = await fetch("/v1/routing-config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "reset-tier", mode, tier }),
    });
    return res.ok ? ((await res.json()) as RoutingConfigState) : null;
  } catch {
    return null;
  }
}

export async function resetRoutingConfig(): Promise<RoutingConfigState | null> {
  try {
    const res = await fetch("/v1/routing-config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "reset" }),
    });
    return res.ok ? ((await res.json()) as RoutingConfigState) : null;
  } catch {
    return null;
  }
}

export interface RecentRequest {
  request_id: string;
  timestamp: number;
  mode: string;
  model: string;
  tier: string;
  method: string;
  cost: number;
  savings: number;
  transport: string;
  cache_mode: string;
  cache_family: string;
  cache_breakpoints: number;
  prompt_preview: string;
  complexity: number;
  constraint_tags: string[];
  hint_tags: string[];
  answer_depth: string;
  feedback_pending: boolean;
  feedback_signal: string;
  feedback_ok: boolean;
  feedback_action: string;
  feedback_from_tier: string;
  feedback_to_tier: string;
  feedback_reason: string;
  feedback_submitted_at: number;
}

export interface FeedbackResult {
  ok: boolean;
  action: string;
  from_tier: string;
  to_tier: string;
  reason?: string;
  total_updates: number;
}

export const fetchRecent = (limit = 30) =>
  get<RecentRequest[]>(`/v1/stats/recent?limit=${limit}`);

export interface TraceAttempt {
  attempt_index: number;
  selected_model: string;
  resolved_model: string;
  provider_name: string;
  target_url: string;
  requested_transport?: string;
  transport: string;
  transport_reason?: string;
  transport_preference_source?: string;
  cache_mode: string;
  cache_family: string;
  cache_breakpoints: number;
  fallback_from: string;
  status_code: number;
  success: boolean;
  error_code: string;
  error_message: string;
  blocked?: boolean;
}

export interface TraceRecord {
  timestamp: number;
  request_id: string;
  requested_model: string;
  model: string;
  status_code: number;
  mode: string;
  tier: string;
  decision_tier: string;
  served_quality: string;
  served_quality_target: string;
  served_quality_floor: string;
  capability_lane: string;
  method: string;
  api_format: string;
  endpoint: string;
  is_virtual: boolean;
  session_id?: string | null;
  streaming: boolean;
  prompt_preview: string;
  prompt_hash: string;
  step_type: string;
  route_reasoning: string;
  confidence: number;
  raw_confidence: number;
  confidence_source: string;
  complexity: number;
  estimated_cost: number;
  baseline_cost: number;
  actual_cost?: number | null;
  savings: number;
  latency_us: number;
  usage_input_tokens: number;
  usage_output_tokens: number;
  cache_read_input_tokens: number;
  cache_write_input_tokens: number;
  cache_hit_ratio: number;
  transport: string;
  requested_transport: string;
  transport_reason: string;
  transport_preference_source: string;
  cache_mode: string;
  cache_family: string;
  cache_breakpoints: number;
  input_tokens_before: number;
  input_tokens_after: number;
  fallback_reason: string;
  answer_depth: string;
  constraint_tags: string[];
  hint_tags: string[];
  feature_tags: string[];
  attempts_payload: TraceAttempt[];
  error_code: string;
  error_stage: string;
  error_message: string;
}

export interface TraceListResponse {
  total_requests: number;
  summary: {
    total_requests: number;
    error_count: number;
    virtual_requests: number;
    passthrough_requests: number;
    by_endpoint: Record<string, number>;
    by_mode: Record<string, number>;
    by_method: Record<string, number>;
    by_status: Record<string, number>;
    by_error_code: Record<string, number>;
    by_served_quality: Record<string, number>;
    by_capability_lane: Record<string, number>;
  };
  items: TraceRecord[];
}

export const fetchTraces = (limit = 30, errorsOnly = false) =>
  get<TraceListResponse>(`/v1/traces?limit=${limit}${errorsOnly ? "&errors_only=1" : ""}`);

export const fetchTraceDetail = (requestId: string) =>
  get<TraceRecord>(`/v1/traces/${encodeURIComponent(requestId)}`);

export async function submitFeedback(
  requestId: string,
  signal: "ok" | "weak" | "strong",
): Promise<FeedbackResult | null> {
  try {
    const res = await fetch("/v1/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ request_id: requestId, signal }),
    });
    const text = await res.text();
    if (!text) return null;
    return JSON.parse(text) as FeedbackResult;
  } catch {
    return null;
  }
}

export interface RoutePreviewResult {
  tier: number;
  tier_name: string;
  confidence: number;
  method: string;
  signals: Array<{ name: string; tier: number | null; confidence: number; shadow?: boolean }>;
  cost_estimate: number;
  cost_baseline: number;
}

export async function fetchRoutePreview(prompt: string, riskTolerance: number = 0.5): Promise<RoutePreviewResult | null> {
  try {
    const res = await fetch("/v1/route-preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt, risk_tolerance: riskTolerance }),
    });
    if (!res.ok) return null;
    return res.json();
  } catch { return null; }
}
