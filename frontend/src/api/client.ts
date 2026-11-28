export type ServiceStatus = {
  name: string;
  ok: boolean;
  latency_ms: number | null;
  detail: string | null;
  model: string | null;
};

export type Health = {
  status: "ok" | "degraded";
  gpu_available: boolean;
  services: Record<string, ServiceStatus>;
  worker_last_check?: { checked_at: string } | null;
};

const API_DOWN = "The Smish API isn't reachable. Is the api container running?";

export class ApiError extends Error {
  constructor(message: string, readonly status = 0) {
    super(message);
  }
}

/** Fired when the server says the session is gone (401): the app shows the sign-in page. */
export const SIGNED_OUT_EVENT = "smish:signed-out";
function noteStatus(res: Response, path: string) {
  if (res.status === 401 && !path.startsWith("/auth/")) {
    window.dispatchEvent(new Event(SIGNED_OUT_EVENT));
  }
}

async function errorMessage(res: Response): Promise<string> {
  const text = await res.text().catch(() => "");
  if (res.headers.get("content-type")?.includes("json")) {
    try {
      const d = JSON.parse(text)?.detail;
      if (typeof d === "string") return d;
      if (Array.isArray(d)) return d.map((e) => e.msg ?? JSON.stringify(e)).join("; ");
    } catch {
      /* fall through */
    }
  }
  // The API itself answered with an error page: it's running, something failed inside it.
  if (res.status === 500 && text.trim() === "Internal Server Error") {
    return "Something went wrong on the server. Details are in the api logs.";
  }
  // The dev proxy / web server answers 500/502/503/504 without a JSON body when the API is down.
  if (res.status >= 500) return API_DOWN;
  return `${res.status} ${res.statusText}`;
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api/v1${path}`, {
    credentials: "include",
    ...init,
    headers: init?.body ? { "content-type": "application/json", ...init.headers } : init?.headers,
  });
  noteStatus(res, path);
  if (!res.ok) throw new ApiError(await errorMessage(res), res.status);
  return (res.status === 204 ? undefined : await res.json()) as T;
}

// ---- Account & sessions -------------------------------------------------------------------

export type StorageMode = "db" | "local";

export type User = {
  id: number;
  email: string;
  username: string | null;
  name: string;
  role: "admin" | "member" | string;
  is_active: boolean;
  approved_at: string | null;
  email_verified_at: string | null;
  storage_mode: StorageMode | null;
  last_login_at: string | null;
  created_at: string;
};

export type RegisterInput = {
  name: string;
  username: string;
  email: string;
  password: string;
};

export type RegisterOut = {
  id: number;
  email: string;
  username: string;
  name: string;
  pending_approval: boolean;
  email_verification_required: boolean;
  email_sent: boolean;
  message: string;
};

export type MessageOut = {
  message: string;
};

export type UserSession = {
  id: number;
  user_agent: string | null;
  ip: string | null;
  created_at: string;
  expires_at: string;
  is_current: boolean;
};

export const getMe = () => api<User>("/auth/me");
export const setStorageMode = (storage_mode: StorageMode, purge_server_data = false) =>
  api<User>("/auth/storage-mode", {
    method: "POST",
    body: JSON.stringify({ storage_mode, purge_server_data }),
  });
export const registerAccount = (body: RegisterInput) =>
  api<RegisterOut>("/auth/register", { method: "POST", body: JSON.stringify(body) });
export const verifyEmail = (token: string) =>
  api<MessageOut>("/auth/verify-email", { method: "POST", body: JSON.stringify({ token }) });
export const resendVerification = (identifier: string) =>
  api<MessageOut>("/auth/resend-verification", {
    method: "POST",
    body: JSON.stringify({ identifier }),
  });
export const forgotPassword = (identifier: string) =>
  api<MessageOut>("/auth/forgot-password", {
    method: "POST",
    body: JSON.stringify({ identifier }),
  });
export const resetPassword = (token: string, new_password: string) =>
  api<MessageOut>("/auth/reset-password", {
    method: "POST",
    body: JSON.stringify({ token, new_password }),
  });
export const signIn = (identifier: string, password: string) =>
  api<User>("/auth/login", { method: "POST", body: JSON.stringify({ identifier, password }) });
export const signOut = () => api<void>("/auth/logout", { method: "POST" });
export const updateProfile = (body: { name?: string; username?: string; email?: string }) =>
  api<User>("/auth/profile", { method: "PATCH", body: JSON.stringify(body) });
export const listSessions = () => api<UserSession[]>("/auth/sessions");
export const revokeSession = (id: number) => api<void>(`/auth/sessions/${id}`, { method: "DELETE" });
export const revokeOtherSessions = () => api<void>("/auth/sessions/revoke-others", { method: "POST" });
export const changePassword = (current_password: string, new_password: string) =>
  api<void>("/auth/change-password", {
    method: "POST",
    body: JSON.stringify({ current_password, new_password }),
  });

export type LegalDocId = "privacy" | "terms" | "disclaimer";

export type LegalMetaApi = {
  service_name: string;
  operator_name: string;
  operator_website: string;
  contact_email: string;
  privacy_email: string;
  jurisdiction: string;
  effective_date: string;
  inference_provider: string;
  training_commitment: string;
  liability_cap: string;
};

export type LegalBlock =
  | { kind: "p"; text: string }
  | { kind: "ul"; items: string[] }
  | { kind: "dl"; items: { term: string; detail: string }[] };

export type LegalSection = {
  heading: string;
  blocks: LegalBlock[];
};

export type LegalDocument = {
  id: LegalDocId;
  title: string;
  subtitle: string;
  summary: string;
  sections: LegalSection[];
};

export type LegalSettings = {
  meta: LegalMetaApi;
  documents: Partial<Record<LegalDocId, LegalDocument>>;
};

export const fetchLegalSettings = () => api<LegalSettings>("/legal");
export const getAdminLegal = () => api<LegalSettings>("/admin/legal");
export const updateAdminLegal = (body: LegalSettings) =>
  api<LegalSettings>("/admin/legal", { method: "PUT", body: JSON.stringify(body) });
export const resetAdminLegal = () =>
  api<LegalSettings>("/admin/legal/reset", { method: "POST" });

export const getJSON = <T>(path: string) => api<T>(path);
export const fetchHealth = () => getJSON<Health>("/health");

// ---- Conversations (PostgreSQL) ------------------------------------------------------------

export type StoredMessage = {
  id?: number;
  role: "user" | "assistant";
  content: string;
  mode?: "sites" | "model" | null;
  error?: string | null;
  sources?: Source[] | null;
  queries?: QueryRecord[] | null;
  meta?: (ChatDone | AskDone) | null;
  created_at?: string;
};

export type ConversationSummary = {
  id: string;
  name: string;
  title_source: "auto" | "llm" | "user";
  mode: "sites" | "model";
  selected_sources: number[];
  created_at: string;
  updated_at: string;
  message_count: number;
};

export type ConversationDetail = ConversationSummary & {
  messages: StoredMessage[];
};

export const listConversations = () => api<ConversationSummary[]>("/conversations");
export const createConversation = (body: {
  id?: string;
  name?: string;
  mode?: "sites" | "model";
  selected_sources?: number[];
}) => api<ConversationSummary>("/conversations", { method: "POST", body: JSON.stringify(body) });
export const getConversation = (id: string) => api<ConversationDetail>(`/conversations/${id}`);
export const updateConversation = (
  id: string,
  body: {
    name?: string;
    title_source?: "auto" | "llm" | "user";
    mode?: "sites" | "model";
    selected_sources?: number[];
  },
) => api<ConversationSummary>(`/conversations/${id}`, { method: "PATCH", body: JSON.stringify(body) });
export const deleteConversation = (id: string) =>
  api<void>(`/conversations/${id}`, { method: "DELETE" });
export const importConversations = (
  items: {
    id: string;
    name: string;
    title_source: "auto" | "llm" | "user";
    mode: "sites" | "model";
    selected_sources: number[];
    updated_ms?: number;
    messages: StoredMessage[];
  }[],
) => api<ConversationSummary[]>("/conversations/import", { method: "POST", body: JSON.stringify(items) });

// ---- Admin: users, metrics, system settings ------------------------------------------------

export type AdminSiteSummary = {
  id: number;
  name: string;
  root_url: string;
  enabled: boolean;
  pages: number;
  chunks: number;
  last_job_status: string | null;
};

export type AdminDbSummary = {
  id: number;
  name: string;
  kind: DbKind;
  edit_policy: EditPolicy;
  enabled: boolean;
  tables: number;
  last_test_ok: boolean | null;
  schema_refreshed_at: string | null;
};

export type AdminUser = {
  id: number;
  email: string;
  username: string | null;
  name: string;
  role: "admin" | "member";
  is_active: boolean;
  pending_approval: boolean;
  approved_at: string | null;
  email_verified: boolean;
  email_verified_at: string | null;
  storage_mode?: StorageMode | null;
  last_login_at: string | null;
  locked_until: string | null;
  created_at: string;
  conversation_count: number;
  sites: AdminSiteSummary[];
  databases: AdminDbSummary[];
};

export type AdminUserCreate = {
  email: string;
  username: string;
  name: string;
  password: string;
  role: "admin" | "member";
};

export type AdminUserUpdate = {
  email?: string;
  username?: string;
  name?: string;
  role?: "admin" | "member";
  is_active?: boolean;
  email_verified?: boolean;
  password?: string;
  unlock?: boolean;
};

export type AdminMetrics = {
  users: {
    total: number;
    active: number;
    pending_approval: number;
    admins: number;
    members: number;
    active_sessions: number;
    logins_24h: number;
  };
  chats: {
    conversations: number;
    messages: number;
    user_messages: number;
    assistant_messages: number;
    messages_24h: number;
    avg_latency_ms: number | null;
    avg_first_token_ms: number | null;
    not_found_count: number;
    by_intent: Record<string, number>;
    daily_7d: { date: string; messages: number; queries: number }[];
  };
  sites: {
    total: number;
    enabled: number;
    pages_active: number;
    pages_gone: number;
    chunks: number;
    chunks_embedded: number;
    jobs_running: number;
    jobs_queued: number;
    jobs_failed: number;
  };
  databases: {
    total: number;
    enabled: number;
    postgres: number;
    mysql: number;
    by_policy: Record<string, number>;
    queries_total: number;
    queries_ok: number;
    queries_refused: number;
    queries_error: number;
    pending_approvals: number;
  };
};

export type SystemSettings = {
  max_history_chars: number;
  answer_sources: number;
  live_verify_pages: number;
  live_reverify_after_sec: number;
  live_total_budget_sec: number;
  default_max_pages: number;
  default_max_depth: number;
  default_crawl_delay_ms: number;
  answer_system_prompt: string;
  chat_system_prompt: string;
};

export const listAdminUsers = () => api<AdminUser[]>("/admin/users");
export const createAdminUser = (body: AdminUserCreate) =>
  api<AdminUser>("/admin/users", { method: "POST", body: JSON.stringify(body) });
export const updateAdminUser = (id: number, body: AdminUserUpdate) =>
  api<AdminUser>(`/admin/users/${id}`, { method: "PATCH", body: JSON.stringify(body) });
export const approveAdminUser = (id: number) =>
  api<AdminUser>(`/admin/users/${id}/approve`, { method: "POST" });
export const verifyAdminUserEmail = (id: number, approve = true) =>
  api<AdminUser>(`/admin/users/${id}/verify-email`, {
    method: "POST",
    body: JSON.stringify({ approve }),
  });
export const rejectAdminUser = (id: number) =>
  api<void>(`/admin/users/${id}/reject`, { method: "POST" });
export const deleteAdminUser = (id: number) =>
  api<void>(`/admin/users/${id}`, { method: "DELETE" });
export const getAdminMetrics = () => api<AdminMetrics>("/admin/metrics");
export const getSystemSettings = () => api<SystemSettings>("/admin/settings");
export const updateSystemSettings = (body: SystemSettings) =>
  api<SystemSettings>("/admin/settings", { method: "PUT", body: JSON.stringify(body) });

// ---- Sites ----------------------------------------------------------------------------------

export type CrawlJob = {
  id: number;
  type: string;
  status: "queued" | "running" | "done" | "failed";
  pages_seen: number;
  pages_changed: number;
  pages_unchanged: number;
  pages_removed: number;
  pages_failed: number;
  errors: { url: string; error: string }[];
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
};

export type Site = {
  id: number;
  name: string;
  root_url: string;
  description: string | null;
  enabled: boolean;
  render_mode: "auto" | "static" | "browser";
  include_patterns: string[];
  exclude_patterns: string[];
  max_depth: number;
  max_pages: number;
  freshness_max_age_sec: number;
  respect_robots: boolean;
  crawl_delay_ms: number;
  site_search_url: string | null;
  created_at: string;
  pages: number;
  pages_gone: number;
  chunks: number;
  chunks_embedded: number;
  last_fetched_at: string | null;
  last_job: CrawlJob | null;
};

export type SiteInput = Partial<
  Pick<
    Site,
    | "name"
    | "enabled"
    | "render_mode"
    | "include_patterns"
    | "exclude_patterns"
    | "max_depth"
    | "max_pages"
    | "respect_robots"
    | "crawl_delay_ms"
    | "site_search_url"
  >
> & { root_url?: string };

export type PageRow = {
  id: number;
  url: string;
  title: string | null;
  status: "active" | "gone" | "error";
  http_status: number | null;
  version: number;
  fetched_at: string | null;
  changed_at: string | null;
  chunks: number;
  chunks_embedded: number;
};

export type SiteDefaults = {
  max_pages: number;
  max_depth: number;
  crawl_delay_ms: number;
};

export const getSiteDefaults = () => api<SiteDefaults>("/sites/defaults");
export const listSites = () => api<Site[]>("/sites");
export const createSite = (body: SiteInput) =>
  api<Site>("/sites", { method: "POST", body: JSON.stringify(body) });
export const updateSite = (id: number, body: SiteInput) =>
  api<Site>(`/sites/${id}`, { method: "PATCH", body: JSON.stringify(body) });
export const deleteSite = (id: number) => api<void>(`/sites/${id}`, { method: "DELETE" });
export const crawlSite = (id: number) => api<CrawlJob>(`/sites/${id}/crawl`, { method: "POST" });
export const listPages = (id: number, q = "") =>
  api<PageRow[]>(`/sites/${id}/pages?limit=200${q ? `&q=${encodeURIComponent(q)}` : ""}`);

// ---- Databases ------------------------------------------------------------------------------

export type DbKind = "postgres" | "mysql";
export type EditPolicy = "read_only" | "ask" | "auto";
export type SslMode = "disable" | "prefer" | "require" | "verify";

export type DataSource = {
  id: number;
  name: string;
  kind: DbKind;
  host: string;
  port: number;
  database: string;
  username: string;
  has_password: boolean;
  ssl_mode: SslMode;
  edit_policy: EditPolicy;
  row_limit: number;
  timeout_sec: number;
  description: string | null;
  enabled: boolean;
  last_test_at: string | null;
  last_test_ok: boolean | null;
  last_error: string | null;
  server_version: string | null;
  schema_refreshed_at: string | null;
  schema_error: string | null;
  tables: number;
  created_at: string;
};

export type DataSourceInput = Partial<Pick<DataSource,
  "name" | "description" | "host" | "port" | "database" | "username" | "ssl_mode" |
  "edit_policy" | "row_limit" | "timeout_sec" | "enabled">> & { kind?: DbKind; password?: string };

export type SchemaItem = {
  id: number;
  schema_name: string;
  table_name: string;
  column_name: string | null;
  data_type: string | null;
  nullable: boolean | null;
  is_pk: boolean;
  fk_ref: string | null;
  row_estimate: number | null;
  sample_values: string[];
  comment: string | null;
  note: string | null;
  hidden: boolean;
};

export type QueryLogRow = {
  id: number;
  sql: string;
  kind: "read" | "write" | "ddl" | "blocked";
  status: string;
  rows: number | null;
  ms: number | null;
  error: string | null;
  pending_id: number | null;
  created_at: string;
};

export type ResultTable = { columns: string[]; rows: unknown[][] };

export type ChangePreview = {
  rowcount: number | null;
  before: ResultTable | null;
  after: ResultTable | null;
  note: string | null;
};

export type PendingChange = {
  id: number;
  source_id?: number;
  local?: boolean;
  local_source_id?: number;
  source: string;
  sql: string;
  kind: "write" | "ddl";
  reason: string | null;
  preview: ChangePreview;
  status?: "pending" | "applied" | "rejected" | "expired" | "failed";
  result?: { rowcount?: number | null; ms?: number; error?: string } | null;
  expires_at: string;
};

/** One statement Smish ran (or refused / queued) while answering. */
export type QueryRecord = {
  n: number;
  sql: string;
  purpose: string;
  action: "query" | "change";
  kind: "read" | "write" | "ddl" | "blocked";
  status: "ok" | "error" | "refused" | "pending" | "applied";
  columns: string[];
  rows: unknown[][];
  truncated: boolean;
  rowcount: number | null;
  ms: number | null;
  error: string | null;
  pending: PendingChange | null;
};

export type TestResult = { ok: boolean; version: string | null; error: string | null };

export type LocalSchemaItemPayload = {
  schema_name: string;
  table_name: string;
  column_name: string | null;
  ordinal: number;
  data_type: string | null;
  nullable: boolean | null;
  is_pk: boolean;
  fk_ref: string | null;
  row_estimate: number | null;
  sample_values: unknown[];
  comment: string | null;
  note: string | null;
  hidden: boolean;
};

export type LocalSourceInput = {
  id: number;
  name: string;
  kind: DbKind;
  host: string;
  port: number;
  database: string;
  username: string;
  password: string;
  ssl_mode: SslMode;
  edit_policy: EditPolicy;
  row_limit: number;
  timeout_sec: number;
  description: string | null;
  enabled: boolean;
  schema_items: LocalSchemaItemPayload[];
};

export type LocalIntrospectResult = {
  ok: boolean;
  version: string | null;
  error: string | null;
  tables: LocalSchemaItemPayload[];
};

export const listSources = () => api<DataSource[]>("/data-sources");
export const introspectLocalSource = (body: DataSourceInput) =>
  api<LocalIntrospectResult>("/data-sources/local/introspect", {
    method: "POST",
    body: JSON.stringify(body),
  });
export const approveLocalChange = (body: {
  source: LocalSourceInput;
  sql: string;
  kind: "write" | "ddl";
}) =>
  api<{ status: "applied" | "failed"; result: { rowcount?: number | null; ms?: number; error?: string } | null }>(
    "/changes/local/approve",
    { method: "POST", body: JSON.stringify(body) },
  );
export const testSource = (body: DataSourceInput) =>
  api<TestResult>("/data-sources/test", { method: "POST", body: JSON.stringify(body) });
export const createSource = (body: DataSourceInput) =>
  api<DataSource>("/data-sources", { method: "POST", body: JSON.stringify(body) });
export const updateSource = (id: number, body: DataSourceInput) =>
  api<DataSource>(`/data-sources/${id}`, { method: "PATCH", body: JSON.stringify(body) });
export const deleteSource = (id: number) => api<void>(`/data-sources/${id}`, { method: "DELETE" });
export const retestSource = (id: number) =>
  api<TestResult>(`/data-sources/${id}/test`, { method: "POST" });
export const refreshSchema = (id: number) =>
  api<DataSource>(`/data-sources/${id}/refresh-schema`, { method: "POST" });
export const getSchema = (id: number) => api<SchemaItem[]>(`/data-sources/${id}/schema`);
export const patchSchema = (id: number, items: { id: number; note?: string | null; hidden?: boolean }[]) =>
  api<SchemaItem[]>(`/data-sources/${id}/schema`, { method: "PATCH", body: JSON.stringify(items) });
export const getQueryLog = (id: number) => api<QueryLogRow[]>(`/data-sources/${id}/log?limit=50`);
export const getChange = (id: number) => api<PendingChange>(`/changes/${id}`);
export const approveChange = (id: number) =>
  api<PendingChange>(`/changes/${id}/approve`, { method: "POST" });
export const rejectChange = (id: number) =>
  api<PendingChange>(`/changes/${id}/reject`, { method: "POST" });

// ---- Streaming (SSE over POST; EventSource can't send a body) -------------------------------

async function postSSE(
  path: string,
  body: unknown,
  onEvent: (event: string, payload: any) => void,
  signal: AbortSignal,
): Promise<void> {
  const res = await fetch(`/api/v1${path}`, {
    method: "POST",
    credentials: "include",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  noteStatus(res, path);
  if (!res.ok || !res.body) {
    onEvent("error", { message: await errorMessage(res) });
    return;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let sep: number;
    while ((sep = buffer.indexOf("\n\n")) >= 0) {
      const raw = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      let event = "message";
      let data = "";
      for (const line of raw.split("\n")) {
        if (line.startsWith("event: ")) event = line.slice(7);
        else if (line.startsWith("data: ")) data += line.slice(6);
      }
      if (data) onEvent(event, JSON.parse(data));
    }
  }
}

export type ChatTurn = { role: "user" | "assistant"; content: string };

export type ChatDone = {
  latency_ms: number;
  first_token_ms: number | null;
  tokens?: number;
  finish_reason: string | null;
  message_id?: number;
};

export type ChatHandlers = {
  onToken: (text: string) => void;
  onMemory?: (summary: string) => void;
  onDone: (meta: ChatDone) => void;
  onError: (message: string) => void;
};

export function streamChat(
  messages: ChatTurn[],
  h: ChatHandlers,
  signal: AbortSignal,
  opts?: { conversationId?: string; conversationSummary?: string | null; selectedSources?: number[] },
) {
  return postSSE(
    "/chat",
    {
      messages,
      conversation_id: opts?.conversationId,
      conversation_summary: opts?.conversationSummary ?? undefined,
      selected_sources: opts?.selectedSources,
    },
    (event, p) => {
      if (event === "token") h.onToken(p.t);
      else if (event === "memory") h.onMemory?.(p.summary);
      else if (event === "done") h.onDone(p);
      else if (event === "error") h.onError(p.message);
    },
    signal,
  );
}

export type Source = {
  n: number;
  url: string;
  title: string | null;
  site: string;
  site_id: number;
  section: string | null;
  fetched_at: string | null;
  verified_at: string | null;
  live: "verified" | "updated" | "new" | "unverified" | "indexed";
  snippet: string;
  score: number | null;
};

export type LiveStats = {
  checked?: number;
  discovered?: number;
  unchanged?: number;
  recently_verified?: number;
  changed?: number;
  new?: number;
  gone?: number;
  error?: number;
  live_ms?: number;
};

export type AskDone = ChatDone & {
  intent?: string;
  database?: string;
  queries?: number;
  cited: number[];
  invalid_citations: number[];
  not_found?: boolean;
  search?: Record<string, unknown>;
  live?: LiveStats | null;
};

export type AskStatus = {
  stage: string; sites?: number; sources?: number; pages?: number; database?: string; n?: number;
  queries?: number;
} & LiveStats;

export type AskHandlers = {
  onStatus: (s: AskStatus) => void;
  onQuery: (q: QueryRecord) => void;
  onSources: (s: Source[]) => void;
  onToken: (text: string) => void;
  onReplace: (text: string) => void;
  onMemory?: (summary: string) => void;
  onDone: (meta: AskDone) => void;
  onError: (message: string) => void;
};

/** scope: null = every enabled site and database; otherwise just these ids. */
export function streamAsk(
  messages: ChatTurn[],
  scope: { siteIds: number[]; sourceIds: number[] } | null,
  h: AskHandlers,
  signal: AbortSignal,
  opts?: {
    conversationId?: string;
    conversationSummary?: string | null;
    selectedSources?: number[];
    localSources?: LocalSourceInput[];
  },
) {
  return postSSE(
    "/ask",
    {
      messages,
      site_ids: scope ? scope.siteIds : null,
      source_ids: scope ? scope.sourceIds : null,
      conversation_id: opts?.conversationId,
      conversation_summary: opts?.conversationSummary ?? undefined,
      selected_sources: opts?.selectedSources,
      local_sources: opts?.localSources ?? [],
    },
    (event, p) => {
      if (event === "status") h.onStatus(p);
      else if (event === "query") h.onQuery(p);
      else if (event === "sources") h.onSources(p);
      else if (event === "token") h.onToken(p.t);
      else if (event === "replace") h.onReplace(p.t);
      else if (event === "memory") h.onMemory?.(p.summary);
      else if (event === "done") h.onDone(p);
      else if (event === "error") h.onError(p.message);
    },
    signal,
  );
}

export const generateTitle = (messages: ChatTurn[], conversationId?: string) =>
  api<{ title: string }>("/title", {
    method: "POST",
    body: JSON.stringify({ messages, conversation_id: conversationId }),
  });

// ---- helpers --------------------------------------------------------------------------------

export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "never";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 5) return "just now";
  if (s < 60) return `${Math.round(s)} s ago`;
  if (s < 90) return "1 min ago";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 5400) return "1 h ago";
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}
