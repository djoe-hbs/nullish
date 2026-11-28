import type {
  ConversationSummary,
  DataSource,
  LocalSchemaItemPayload,
  LocalSourceInput,
  QueryLogRow,
  QueryRecord,
  SchemaItem,
  StoredMessage,
} from "../api/client";

export type LocalConversation = {
  id: string;
  name: string;
  title_source: "auto" | "llm" | "user";
  mode: "sites" | "model";
  selected_sources: number[];
  created_at: string;
  updated_at: string;
  summary?: string | null;
  messages: StoredMessage[];
};

export type LocalDataSource = DataSource & {
  password: string;
  schema_items: SchemaItem[];
  query_log: QueryLogRow[];
};

export const LOCAL_CHATS_EVENT = "smish:local-chats-changed";
export const LOCAL_DBS_EVENT = "smish:local-dbs-changed";

const chatsKey = (userId: number) => `smish.local.chats.v1.${userId}`;
const dbsKey = (userId: number) => `smish.local.dbs.v1.${userId}`;

function safeRead<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return fallback;
    const parsed = JSON.parse(raw);
    return parsed ?? fallback;
  } catch {
    return fallback;
  }
}

function safeWrite(key: string, value: unknown, eventName: string) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
    window.dispatchEvent(new Event(eventName));
  } catch {
    /* quota/storage blocked */
  }
}

// ---- Conversations -------------------------------------------------------------------------

export function getLocalChats(userId: number): LocalConversation[] {
  const list = safeRead<LocalConversation[]>(chatsKey(userId), []);
  return Array.isArray(list)
    ? [...list].sort(
        (a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime(),
      )
    : [];
}

export function getLocalChat(userId: number, id: string): LocalConversation | undefined {
  return getLocalChats(userId).find((c) => c.id === id);
}

export function saveLocalChats(userId: number, list: LocalConversation[]): void {
  safeWrite(chatsKey(userId), list, LOCAL_CHATS_EVENT);
}

export function upsertLocalChat(userId: number, conv: LocalConversation): void {
  const list = getLocalChats(userId).filter((c) => c.id !== conv.id);
  list.unshift(conv);
  saveLocalChats(userId, list);
}

export function deleteLocalChat(userId: number, id: string): void {
  saveLocalChats(
    userId,
    getLocalChats(userId).filter((c) => c.id !== id),
  );
}

export function clearLocalChats(userId: number): void {
  try {
    localStorage.removeItem(chatsKey(userId));
    window.dispatchEvent(new Event(LOCAL_CHATS_EVENT));
  } catch {
    /* ignore */
  }
}

export function toLocalChatSummaries(list: LocalConversation[]): ConversationSummary[] {
  return list.map((c) => ({
    id: c.id,
    name: c.name,
    title_source: c.title_source,
    mode: c.mode,
    selected_sources: c.selected_sources ?? [],
    created_at: c.created_at,
    updated_at: c.updated_at,
    message_count: c.messages.length,
  }));
}

// ---- Databases & credentials ---------------------------------------------------------------

export function getLocalDbs(userId: number): LocalDataSource[] {
  const list = safeRead<LocalDataSource[]>(dbsKey(userId), []);
  return Array.isArray(list) ? list : [];
}

export function saveLocalDbs(userId: number, list: LocalDataSource[]): void {
  safeWrite(dbsKey(userId), list, LOCAL_DBS_EVENT);
}

export function clearLocalDbs(userId: number): void {
  try {
    localStorage.removeItem(dbsKey(userId));
    window.dispatchEvent(new Event(LOCAL_DBS_EVENT));
  } catch {
    /* ignore */
  }
}

export function nextLocalDbId(list: LocalDataSource[]): number {
  const max = list.reduce((acc, d) => Math.max(acc, d.id), 0);
  return max + 1;
}

export function toSchemaItems(
  rows: LocalSchemaItemPayload[],
  existing: SchemaItem[] = [],
): SchemaItem[] {
  const prevMap = new Map(
    existing.map((i) => [`${i.schema_name}\0${i.table_name}\0${i.column_name ?? ""}`, i]),
  );
  return rows.map((r, idx) => {
    const prev = prevMap.get(`${r.schema_name}\0${r.table_name}\0${r.column_name ?? ""}`);
    return {
      id: idx + 1,
      schema_name: r.schema_name,
      table_name: r.table_name,
      column_name: r.column_name,
      data_type: r.data_type,
      nullable: r.nullable,
      is_pk: r.is_pk,
      fk_ref: r.fk_ref,
      row_estimate: r.row_estimate,
      sample_values: (r.sample_values ?? []).map(String),
      comment: r.comment,
      note: prev?.note ?? r.note ?? null,
      hidden: prev?.hidden ?? r.hidden ?? false,
    };
  });
}

export function toLocalSourcePayload(d: LocalDataSource): LocalSourceInput {
  return {
    id: d.id,
    name: d.name,
    kind: d.kind,
    host: d.host,
    port: d.port,
    database: d.database,
    username: d.username,
    password: d.password ?? "",
    ssl_mode: d.ssl_mode,
    edit_policy: d.edit_policy,
    row_limit: d.row_limit,
    timeout_sec: d.timeout_sec,
    description: d.description ?? null,
    enabled: d.enabled,
    schema_items: (d.schema_items ?? []).map((s, idx) => ({
      schema_name: s.schema_name,
      table_name: s.table_name,
      column_name: s.column_name,
      ordinal: idx,
      data_type: s.data_type,
      nullable: s.nullable,
      is_pk: s.is_pk,
      fk_ref: s.fk_ref,
      row_estimate: s.row_estimate,
      sample_values: s.sample_values ?? [],
      comment: s.comment,
      note: s.note,
      hidden: s.hidden,
    })),
  };
}

export function appendLocalQueryLogs(
  userId: number,
  sourceName: string,
  queries: QueryRecord[],
): void {
  if (!queries.length) return;
  const dbs = getLocalDbs(userId);
  const idx = dbs.findIndex((d) => d.name === sourceName);
  if (idx < 0) return;
  const now = new Date().toISOString();
  const cur = dbs[idx];
  const baseId = cur.query_log.reduce((m, r) => Math.max(m, r.id), 0);
  const added: QueryLogRow[] = queries
    .filter((q) => q.sql)
    .map((q, i) => ({
      id: baseId + i + 1,
      sql: q.sql,
      kind: q.kind,
      status: q.status,
      rows: q.rowcount ?? q.rows.length,
      ms: q.ms,
      error: q.error,
      pending_id: q.pending?.id ?? null,
      created_at: now,
    }));
  if (!added.length) return;
  const next = [...added.reverse(), ...(cur.query_log ?? [])].slice(0, 100);
  const updated = [...dbs];
  updated[idx] = { ...cur, query_log: next };
  saveLocalDbs(userId, updated);
}

