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
  // The dev proxy answers 500/502/504 with no JSON body when the API container is down.
  if (res.status >= 500 && !res.headers.get("content-type")?.includes("json")) return API_DOWN;
  try {
    const body = await res.json();
    const d = body?.detail;
    if (typeof d === "string") return d;
    if (Array.isArray(d)) return d.map((e) => e.msg ?? JSON.stringify(e)).join("; ");
  } catch {
    /* fall through */
  }
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

// ---- Account ------------------------------------------------------------------------------

export type User = { id: number; email: string; role: string };

export const getMe = () => api<User>("/auth/me");
export const signIn = (email: string, password: string) =>
  api<User>("/auth/login", { method: "POST", body: JSON.stringify({ email, password }) });
export const signOut = () => api<void>("/auth/logout", { method: "POST" });
export const changePassword = (current_password: string, new_password: string) =>
  api<void>("/auth/change-password", {
    method: "POST",
    body: JSON.stringify({ current_password, new_password }),
  });

export const getJSON = <T>(path: string) => api<T>(path);
export const fetchHealth = () => getJSON<Health>("/health");

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

export const listSources = () => api<DataSource[]>("/data-sources");
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
};

export type ChatHandlers = {
  onToken: (text: string) => void;
  onDone: (meta: ChatDone) => void;
  onError: (message: string) => void;
};

export function streamChat(messages: ChatTurn[], h: ChatHandlers, signal: AbortSignal) {
  return postSSE(
    "/chat",
    { messages },
    (event, p) => {
      if (event === "token") h.onToken(p.t);
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
  // verified: unchanged on the live site · updated: changed, new text used · new: found live
  // unverified: live check failed (content may be outdated) · indexed: not re-checked
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
  /** The server cleaned the streamed answer (unsupported sentences removed, or withdrawn when
   *  nothing was validly cited): show `text` instead of what streamed. */
  onReplace: (text: string) => void;
  onDone: (meta: AskDone) => void;
  onError: (message: string) => void;
};

/** scope: null = every enabled site and database; otherwise just these ids. */
export function streamAsk(
  messages: ChatTurn[],
  scope: { siteIds: number[]; sourceIds: number[] } | null,
  h: AskHandlers,
  signal: AbortSignal,
) {
  return postSSE(
    "/ask",
    { messages, site_ids: scope ? scope.siteIds : null, source_ids: scope ? scope.sourceIds : null },
    (event, p) => {
      if (event === "status") h.onStatus(p);
      else if (event === "query") h.onQuery(p);
      else if (event === "sources") h.onSources(p);
      else if (event === "token") h.onToken(p.t);
      else if (event === "replace") h.onReplace(p.t);
      else if (event === "done") h.onDone(p);
      else if (event === "error") h.onError(p.message);
    },
    signal,
  );
}

export const generateTitle = (messages: ChatTurn[]) =>
  api<{ title: string }>("/title", { method: "POST", body: JSON.stringify({ messages }) });

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
