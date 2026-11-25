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

const API_DOWN = "The Nullish API isn't reachable. Is the api container running?";

export class ApiError extends Error {
  constructor(message: string, readonly status = 0) {
    super(message);
  }
}

/** Fired when the server says the session is gone (401): the app shows the sign-in page. */
export const SIGNED_OUT_EVENT = "nullish:signed-out";
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
  cited: number[];
  invalid_citations: number[];
  not_found?: boolean;
  search?: Record<string, unknown>;
  live?: LiveStats | null;
};

export type AskHandlers = {
  onStatus: (s: { stage: string; sites?: number; sources?: number; pages?: number } & LiveStats) => void;
  onSources: (s: Source[]) => void;
  onToken: (text: string) => void;
  /** The server cleaned the streamed answer (unsupported sentences removed, or withdrawn when
   *  nothing was validly cited): show `text` instead of what streamed. */
  onReplace: (text: string) => void;
  onDone: (meta: AskDone) => void;
  onError: (message: string) => void;
};

export function streamAsk(
  messages: ChatTurn[],
  siteIds: number[],
  h: AskHandlers,
  signal: AbortSignal,
) {
  return postSSE(
    "/ask",
    { messages, site_ids: siteIds.length ? siteIds : null },
    (event, p) => {
      if (event === "status") h.onStatus(p);
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
