import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ChevronDown,
  CircleCheck,
  CirclePause,
  Database,
  Eye,
  EyeOff,
  HardDrive,
  History,
  KeyRound,
  Loader2,
  Pause,
  Play,
  Plug,
  Plus,
  RefreshCw,
  Table2,
  Trash2,
} from "lucide-react";
import { useEffect, useState } from "react";
import {
  createSource,
  deleteSource,
  getQueryLog,
  getSchema,
  introspectLocalSource,
  listSources,
  patchSchema,
  refreshSchema,
  retestSource,
  testSource,
  timeAgo,
  updateSource,
  type DataSource,
  type DataSourceInput,
  type DbKind,
  type EditPolicy,
  type SchemaItem,
  type SslMode,
  type TestResult,
  type User,
} from "../api/client";
import Collapse from "../components/Collapse";
import {
  getLocalDbs,
  LOCAL_DBS_EVENT,
  nextLocalDbId,
  saveLocalDbs,
  toSchemaItems,
  type LocalDataSource,
} from "../storage/localStore";

const KINDS: { id: DbKind; label: string; port: number }[] = [
  { id: "postgres", label: "PostgreSQL", port: 5432 },
  { id: "mysql", label: "MySQL / MariaDB", port: 3306 },
];
const kindLabel = (k: DbKind) => KINDS.find((x) => x.id === k)?.label ?? k;

export const POLICIES: { id: EditPolicy; label: string; text: string }[] = [
  { id: "read_only", label: "Never edit",
    text: "Smish only reads. Any change is refused, and reads run in read-only transactions." },
  { id: "ask", label: "Ask before editing",
    text: "Smish prepares each change and shows a preview. Nothing changes until you approve it." },
  { id: "auto", label: "Edit automatically",
    text: "Data changes (insert, update, delete) run straight away when you ask for them. Schema " +
      "changes (create, alter, drop) still wait for your approval." },
];

function PolicyPicker({ value, onChange, disabled }: {
  value: EditPolicy; onChange: (p: EditPolicy) => void; disabled?: boolean;
}) {
  const current = POLICIES.find((p) => p.id === value)!;
  return (
    <div className="perm">
      <div className="perm-picker" role="radiogroup" aria-label="Edit permission">
        {POLICIES.map((p) => (
          <button key={p.id} type="button" role="radio" aria-checked={value === p.id}
            disabled={disabled} className={value === p.id ? `on ${p.id}` : undefined}
            onClick={() => onChange(p.id)}>{p.label}</button>
        ))}
      </div>
      <p className={`perm-text ${value}`}>
        {value === "auto" && <AlertTriangle size={13} />} {current.text}
      </p>
    </div>
  );
}

function TestLine({ res }: { res: TestResult | null }) {
  if (!res) return null;
  return res.ok
    ? <p className="test-line ok"><CircleCheck size={14} /> Connected · {res.version?.split(" on ")[0]}</p>
    : <p className="test-line bad"><AlertTriangle size={14} /> {res.error}</p>;
}

function fromLocalSource(s: LocalDataSource, passwordOverride?: string): DataSourceInput {
  return {
    name: s.name,
    description: s.description ?? undefined,
    kind: s.kind,
    host: s.host,
    port: s.port,
    database: s.database,
    username: s.username,
    password: passwordOverride ?? s.password ?? "",
    ssl_mode: s.ssl_mode,
    edit_policy: s.edit_policy,
    row_limit: s.row_limit,
    timeout_sec: s.timeout_sec,
    enabled: s.enabled,
  };
}

// ---- add a database ---------------------------------------------------------------------------

function AddDatabase({ user }: { user: User }) {
  const qc = useQueryClient();
  const isLocal = user.storage_mode === "local";
  const [kind, setKind] = useState<DbKind>("postgres");
  const [host, setHost] = useState("");
  const [port, setPort] = useState<number | "">("");
  const [database, setDatabase] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [policy, setPolicy] = useState<EditPolicy>("read_only");
  const [more, setMore] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [ssl, setSsl] = useState<SslMode>("prefer");
  const [rowLimit, setRowLimit] = useState(200);
  const [timeout, setTimeoutSec] = useState(15);
  const [test, setTest] = useState<TestResult | null>(null);

  const body = (): DataSourceInput => ({
    kind, host, port: port || KINDS.find((k) => k.id === kind)!.port, database, username, password,
    edit_policy: policy, name: name || undefined, description: description || undefined,
    ssl_mode: ssl, row_limit: rowLimit, timeout_sec: timeout,
  });
  const tryIt = useMutation({ mutationFn: () => testSource(body()), onSuccess: setTest });
  const add = useMutation({
    mutationFn: async () => {
      const b = body();
      if (!isLocal) {
        return await createSource(b);
      }
      const res = await introspectLocalSource(b);
      const list = getLocalDbs(user.id);
      const now = new Date().toISOString();
      const schemaItems = res.ok ? toSchemaItems(res.tables) : [];
      const tableCount = schemaItems.filter((i) => i.column_name === null).length;
      const entry: LocalDataSource = {
        id: nextLocalDbId(list),
        name: (b.name || `${b.database} on ${b.host}`).trim().slice(0, 200),
        kind: b.kind ?? "postgres",
        host: b.host ?? "",
        port: b.port ?? 5432,
        database: b.database ?? "",
        username: b.username ?? "",
        password: b.password ?? "",
        has_password: Boolean(b.password),
        ssl_mode: b.ssl_mode ?? "prefer",
        edit_policy: b.edit_policy ?? "read_only",
        row_limit: b.row_limit ?? 200,
        timeout_sec: b.timeout_sec ?? 15,
        description: b.description ?? null,
        enabled: true,
        last_test_at: now,
        last_test_ok: res.ok,
        last_error: res.ok ? null : (res.error ?? "Connection failed"),
        server_version: res.version,
        schema_refreshed_at: res.ok ? now : null,
        schema_error: res.ok ? null : (res.error ?? null),
        tables: tableCount,
        created_at: now,
        schema_items: schemaItems,
        query_log: [],
      };
      saveLocalDbs(user.id, [...list, entry]);
      return entry;
    },
    onSuccess: () => {
      setHost(""); setPort(""); setDatabase(""); setUsername(""); setPassword(""); setName("");
      setDescription(""); setTest(null); setMore(false);
      if (!isLocal) qc.invalidateQueries({ queryKey: ["data-sources"] });
    },
  });
  const ready = host.trim() && database.trim() && username.trim();

  return (
    <form className="site-card add-site add-db" onSubmit={(e) => { e.preventDefault(); add.mutate(); }}>
      <div className="db-kinds" role="radiogroup" aria-label="Database type">
        {KINDS.map((k) => (
          <button key={k.id} type="button" role="radio" aria-checked={kind === k.id}
            className={kind === k.id ? "on" : undefined} onClick={() => setKind(k.id)}>
            <Database size={14} /> {k.label}
          </button>
        ))}
      </div>
      <div className="db-grid">
        <label className="wide">Host
          <input placeholder="db.example.com or 10.0.0.5" value={host} required
            onChange={(e) => setHost(e.target.value)} autoComplete="off" />
        </label>
        <label>Port
          <input type="number" min={1} max={65535} placeholder={String(KINDS.find((k) => k.id === kind)!.port)}
            value={port} onChange={(e) => setPort(e.target.value ? Number(e.target.value) : "")} />
        </label>
        <label>Database
          <input placeholder="shop" value={database} required onChange={(e) => setDatabase(e.target.value)}
            autoComplete="off" />
        </label>
        <label>User
          <input placeholder="readonly_user" value={username} required autoComplete="off"
            onChange={(e) => setUsername(e.target.value)} />
        </label>
        <label>Password
          <input type="password" value={password} autoComplete="new-password"
            onChange={(e) => setPassword(e.target.value)} />
        </label>
      </div>
      <PolicyPicker value={policy} onChange={setPolicy} />
      <div className="add-more">
        <button type="button" className="link-btn" aria-expanded={more} onClick={() => setMore((v) => !v)}>
          More options <ChevronDown size={13} className="chev" />
        </button>
      </div>
      <Collapse open={more}>
        <div className="add-options">
          <label>Display name
            <input placeholder={database && host ? `${database} on ${host}` : "e.g. Shop database"}
              value={name} onChange={(e) => setName(e.target.value)} />
          </label>
          <label>What&apos;s in it (helps Smish pick the right database)
            <input placeholder="Orders, customers and products of the online shop" value={description}
              onChange={(e) => setDescription(e.target.value)} />
          </label>
          <label>Encryption (TLS)
            <select value={ssl} onChange={(e) => setSsl(e.target.value as SslMode)}>
              <option value="prefer">Use if available</option>
              <option value="require">Required</option>
              <option value="verify">Required + verify certificate</option>
              <option value="disable">Off</option>
            </select>
          </label>
          <label>Max rows per query
            <input type="number" min={1} max={5000} value={rowLimit}
              onChange={(e) => setRowLimit(Number(e.target.value))} />
          </label>
          <label>Query time limit (seconds)
            <input type="number" min={1} max={300} value={timeout}
              onChange={(e) => setTimeoutSec(Number(e.target.value))} />
          </label>
        </div>
        <p className="db-tip"><KeyRound size={13} /> Tip: give Smish its own database user. For
          &quot;Never edit&quot;, a user that can only read (e.g. <code>GRANT SELECT</code>) is the
          strongest protection.</p>
      </Collapse>
      <div className="actions db-submit">
        <button type="button" disabled={!ready || tryIt.isPending} onClick={() => tryIt.mutate()}>
          {tryIt.isPending ? <Loader2 size={14} className="animate-spin" /> : <Plug size={14} />}
          Test connection
        </button>
        <button type="submit" className="primary" disabled={!ready || add.isPending}>
          {add.isPending ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
          {add.isPending ? "Connecting…" : isLocal ? "Save in browser" : "Add database"}
        </button>
      </div>
      <TestLine res={test} />
      {(add.isError || tryIt.isError) && <p className="form-error">{String((add.error ?? tryIt.error)?.message)}</p>}
    </form>
  );
}

// ---- tables: notes and hidden flags ---------------------------------------------------------

function Note({ item, save }: { item: SchemaItem; save: (note: string) => void }) {
  const [v, setV] = useState(item.note ?? "");
  return (
    <input className="schema-note" value={v} placeholder="Add a note for Smish (meaning, units…)"
      aria-label={`Note for ${item.column_name ?? item.table_name}`}
      onChange={(e) => setV(e.target.value)}
      onBlur={() => { if (v !== (item.note ?? "")) save(v); }}
      onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }} />
  );
}

function Schema({ user, source }: { user: User; source: DataSource | LocalDataSource }) {
  const qc = useQueryClient();
  const isLocal = user.storage_mode === "local";
  const key = ["schema", source.id];
  const { data: serverData, isLoading } = useQuery({
    queryKey: key,
    queryFn: () => getSchema(source.id),
    enabled: !isLocal,
  });
  const [open, setOpen] = useState<string | null>(null);
  const patch = useMutation({
    mutationFn: async (items: { id: number; note?: string | null; hidden?: boolean }[]) => {
      if (!isLocal) return await patchSchema(source.id, items);
      const list = getLocalDbs(user.id);
      const idx = list.findIndex((d) => d.id === source.id);
      if (idx < 0) return [];
      const patchMap = new Map(items.map((p) => [p.id, p]));
      const updatedItems = list[idx].schema_items.map((it) => {
        const p = patchMap.get(it.id);
        if (!p) return it;
        return {
          ...it,
          note: "note" in p ? ((p.note ?? "").trim() || null) : it.note,
          hidden: p.hidden !== undefined ? p.hidden : it.hidden,
        };
      });
      list[idx] = { ...list[idx], schema_items: updatedItems };
      saveLocalDbs(user.id, list);
      return updatedItems;
    },
    onSuccess: (items) => {
      if (!isLocal) qc.setQueryData(key, items);
    },
  });
  if (!isLocal && isLoading) return <p className="muted-line">Loading tables…</p>;
  const data = isLocal ? ((source as LocalDataSource).schema_items ?? []) : (serverData ?? []);
  const tables = data.filter((i) => i.column_name === null);
  if (!tables.length) return <p className="muted-line">No tables found (or no permission to read them).</p>;

  return (
    <div className="schema">
      <p className="muted-line">Hidden tables and columns are never shown to the model or queried.
        Notes help Smish understand what the data means.</p>
      {tables.map((t) => {
        const id = `${t.schema_name}.${t.table_name}`;
        const cols = data.filter((c) => c.column_name !== null && c.table_name === t.table_name
          && c.schema_name === t.schema_name);
        const isOpen = open === id;
        return (
          <div key={t.id} className={`schema-table${t.hidden ? " hidden-item" : ""}`}>
            <div className="schema-row">
              <button className="schema-name" aria-expanded={isOpen} onClick={() => setOpen(isOpen ? null : id)}>
                <Table2 size={14} /> <strong>{t.table_name}</strong>
                {t.data_type === "view" && <small>view</small>}
                {t.row_estimate ? <small>~{t.row_estimate.toLocaleString()} rows</small> : null}
                <small>{cols.length} columns</small>
                <ChevronDown size={13} className="chev" />
              </button>
              <button className="icon-btn" title={t.hidden ? "Show to Smish" : "Hide from Smish"}
                aria-label={t.hidden ? `Show ${t.table_name}` : `Hide ${t.table_name}`}
                onClick={() => patch.mutate([{ id: t.id, hidden: !t.hidden }])}>
                {t.hidden ? <EyeOff size={15} /> : <Eye size={15} />}
              </button>
            </div>
            <Collapse open={isOpen}>
              {isOpen && (
                <div className="schema-cols">
                  <Note item={t} save={(note) => patch.mutate([{ id: t.id, note }])} />
                  {cols.map((c) => (
                    <div key={c.id} className={`schema-col${c.hidden ? " hidden-item" : ""}`}>
                      <span className="col-name">
                        {c.column_name}
                        {c.is_pk && <em className="tag">key</em>}
                        {c.fk_ref && <em className="tag">→ {c.fk_ref}</em>}
                      </span>
                      <span className="col-type">{c.data_type}</span>
                      {c.sample_values.length > 0 && (
                        <span className="col-samples" title="Values Smish saw in this column">
                          {c.sample_values.join(" · ")}</span>
                      )}
                      <Note item={c} save={(note) => patch.mutate([{ id: c.id, note }])} />
                      <button className="icon-btn" title={c.hidden ? "Show to Smish" : "Hide from Smish"}
                        aria-label={c.hidden ? `Show ${c.column_name}` : `Hide ${c.column_name}`}
                        onClick={() => patch.mutate([{ id: c.id, hidden: !c.hidden }])}>
                        {c.hidden ? <EyeOff size={14} /> : <Eye size={14} />}
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </Collapse>
          </div>
        );
      })}
      {patch.isError && <p className="form-error">{String(patch.error.message)}</p>}
    </div>
  );
}

const LOG_STATUS: Record<string, string> = {
  ok: "ran", error: "failed", refused: "refused", pending: "waiting", applied: "applied",
  rejected: "rejected",
};

function QueryLog({ user, source }: { user: User; source: DataSource | LocalDataSource }) {
  const isLocal = user.storage_mode === "local";
  const { data: serverData, isLoading } = useQuery({
    queryKey: ["db-log", source.id],
    queryFn: () => getQueryLog(source.id),
    refetchInterval: 15_000,
    enabled: !isLocal,
  });
  const data = isLocal ? ((source as LocalDataSource).query_log ?? []) : serverData;
  if (!isLocal && isLoading) return <p className="muted-line">Loading…</p>;
  if (!data?.length) return <p className="muted-line">Nothing has run on this database yet.</p>;
  return (
    <div className="page-table">
      <table>
        <thead><tr><th>When</th><th>Statement</th><th>Result</th></tr></thead>
        <tbody>
          {data.map((r) => (
            <tr key={r.id}>
              <td>{timeAgo(r.created_at)}</td>
              <td><code title={r.sql} className="log-sql">{r.sql}</code></td>
              <td title={r.error ?? undefined}>
                <span className={`query-status ${r.status}`}>{LOG_STATUS[r.status] ?? r.status}</span>
                {r.rows != null && ` · ${r.rows} row${r.rows === 1 ? "" : "s"}`}
                {r.ms != null && ` · ${r.ms} ms`}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---- one database -----------------------------------------------------------------------------

function EditConnection({ user, source, done }: {
  user: User; source: DataSource | LocalDataSource; done: () => void;
}) {
  const qc = useQueryClient();
  const isLocal = user.storage_mode === "local";
  const [host, setHost] = useState(source.host);
  const [port, setPort] = useState(source.port);
  const [database, setDatabase] = useState(source.database);
  const [username, setUsername] = useState(source.username);
  const [password, setPassword] = useState("");
  const [name, setName] = useState(source.name);
  const [description, setDescription] = useState(source.description ?? "");
  const [ssl, setSsl] = useState<SslMode>(source.ssl_mode);
  const save = useMutation({
    mutationFn: async () => {
      if (!isLocal) {
        return await updateSource(source.id, {
          host, port, database, username, name, description, ssl_mode: ssl,
          ...(password ? { password } : {}),
        });
      }
      const list = getLocalDbs(user.id);
      const idx = list.findIndex((d) => d.id === source.id);
      if (idx < 0) throw new Error("Local database entry not found.");
      const cur = list[idx];
      const nextPw = password || cur.password;
      const updated: LocalDataSource = {
        ...cur,
        host,
        port,
        database,
        username,
        name: name.trim() || cur.name,
        description: description || null,
        ssl_mode: ssl,
        password: nextPw,
        has_password: Boolean(nextPw),
      };
      const res = await testSource(fromLocalSource(updated, nextPw));
      updated.last_test_at = new Date().toISOString();
      updated.last_test_ok = res.ok;
      updated.last_error = res.error;
      if (res.version) updated.server_version = res.version;
      list[idx] = updated;
      saveLocalDbs(user.id, list);
      return updated;
    },
    onSuccess: () => {
      if (!isLocal) qc.invalidateQueries({ queryKey: ["data-sources"] });
      done();
    },
  });
  return (
    <form className="add-options edit-db" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
      <label>Display name<input value={name} onChange={(e) => setName(e.target.value)} /></label>
      <label>What&apos;s in it<input value={description} onChange={(e) => setDescription(e.target.value)} /></label>
      <label>Host<input value={host} onChange={(e) => setHost(e.target.value)} /></label>
      <label>Port<input type="number" value={port} onChange={(e) => setPort(Number(e.target.value))} /></label>
      <label>Database<input value={database} onChange={(e) => setDatabase(e.target.value)} /></label>
      <label>User<input value={username} onChange={(e) => setUsername(e.target.value)} /></label>
      <label>New password
        <input type="password" placeholder={source.has_password ? "Unchanged" : "None"} value={password}
          autoComplete="new-password" onChange={(e) => setPassword(e.target.value)} />
      </label>
      <label>Encryption (TLS)
        <select value={ssl} onChange={(e) => setSsl(e.target.value as SslMode)}>
          <option value="prefer">Use if available</option>
          <option value="require">Required</option>
          <option value="verify">Required + verify certificate</option>
          <option value="disable">Off</option>
        </select>
      </label>
      <div className="actions">
        <button type="submit" className="primary" disabled={save.isPending}>Save and reconnect</button>
        <button type="button" onClick={done}>Cancel</button>
      </div>
      {save.isError && <p className="form-error">{String(save.error.message)}</p>}
    </form>
  );
}

function dbState(s: DataSource) {
  if (!s.enabled) return { cls: "", icon: CirclePause, text: "Paused" };
  if (s.last_test_ok === false) return { cls: "bad", icon: AlertTriangle, text: "Can't connect" };
  if (s.schema_error) return { cls: "warn", icon: AlertTriangle, text: "Couldn't read tables" };
  if (s.last_test_ok) return { cls: "ok", icon: CircleCheck, text: "Connected" };
  return { cls: "", icon: Plug, text: "Not tested" };
}

function DatabaseCard({ user, source }: { user: User; source: DataSource | LocalDataSource }) {
  const qc = useQueryClient();
  const isLocal = user.storage_mode === "local";
  const [open, setOpen] = useState(false);
  const [panel, setPanel] = useState<"tables" | "log" | "edit" | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [test, setTest] = useState<TestResult | null>(null);
  const refresh = () => {
    if (!isLocal) qc.invalidateQueries({ queryKey: ["data-sources"] });
  };
  const patchLocal = (fn: (cur: LocalDataSource) => LocalDataSource) => {
    const list = getLocalDbs(user.id);
    const idx = list.findIndex((d) => d.id === source.id);
    if (idx < 0) return null;
    list[idx] = fn(list[idx]);
    saveLocalDbs(user.id, list);
    return list[idx];
  };
  const policy = useMutation({
    mutationFn: async (edit_policy: EditPolicy) =>
      isLocal ? patchLocal((c) => ({ ...c, edit_policy })) : await updateSource(source.id, { edit_policy }),
    onSettled: refresh,
  });
  const retest = useMutation({
    mutationFn: async () => {
      if (!isLocal) return await retestSource(source.id);
      const localSrc = source as LocalDataSource;
      const res = await testSource(fromLocalSource(localSrc));
      patchLocal((c) => ({
        ...c,
        last_test_at: new Date().toISOString(),
        last_test_ok: res.ok,
        last_error: res.error,
        server_version: res.version ?? c.server_version,
      }));
      return res;
    },
    onSuccess: setTest,
    onSettled: refresh,
  });
  const reread = useMutation({
    mutationFn: async () => {
      if (!isLocal) return await refreshSchema(source.id);
      const localSrc = source as LocalDataSource;
      const res = await introspectLocalSource(fromLocalSource(localSrc));
      if (!res.ok) {
        patchLocal((c) => ({ ...c, schema_error: res.error ?? "Couldn't read tables" }));
        throw new Error(res.error ?? "Couldn't read tables");
      }
      const nextItems = toSchemaItems(res.tables, localSrc.schema_items);
      const tableCount = nextItems.filter((i) => i.column_name === null).length;
      return patchLocal((c) => ({
        ...c,
        schema_items: nextItems,
        tables: tableCount,
        schema_refreshed_at: new Date().toISOString(),
        schema_error: null,
        server_version: res.version ?? c.server_version,
      }));
    },
    onSettled: () => {
      refresh();
      if (!isLocal) qc.invalidateQueries({ queryKey: ["schema", source.id] });
    },
  });
  const toggle = useMutation({
    mutationFn: async () =>
      isLocal
        ? patchLocal((c) => ({ ...c, enabled: !c.enabled }))
        : await updateSource(source.id, { enabled: !source.enabled }),
    onSettled: refresh,
  });
  const remove = useMutation({
    mutationFn: async () => {
      if (!isLocal) return await deleteSource(source.id);
      saveLocalDbs(user.id, getLocalDbs(user.id).filter((d) => d.id !== source.id));
    },
    onSettled: refresh,
  });
  const state = dbState(source);
  const StateIcon = state.icon;
  const show = (p: typeof panel) => setPanel((v) => (v === p ? null : p));
  const perm = POLICIES.find((p) => p.id === source.edit_policy)!;

  return (
    <div className={`site-card site db-card${source.enabled ? "" : " paused"}`}>
      <button className="site-head" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        <span className="site-avatar db" aria-hidden><Database size={18} /></span>
        <span className="site-title">
          <strong>{source.name}</strong>
          <small>{kindLabel(source.kind)} · {source.database} on {source.host} · {source.tables} table{source.tables === 1 ? "" : "s"}</small>
        </span>
        <span className={`chip perm-chip ${source.edit_policy}`} title={perm.text}>{perm.label}</span>
        <span className={`chip ${state.cls}`}><StateIcon size={13} /> {state.text}</span>
        <ChevronDown size={18} className="chev" />
      </button>
      <Collapse open={open}>
        <div className="site-body">
          <div className="facts">
            {source.server_version && <span>{source.server_version.split(" on ")[0].split(",")[0]}</span>}
            <span>Tested {timeAgo(source.last_test_at)}</span>
            <span>Tables read {timeAgo(source.schema_refreshed_at)}</span>
            <span>Up to {source.row_limit} rows · {source.timeout_sec} s per query</span>
          </div>
          {source.last_test_ok === false && source.last_error && <p className="form-error" style={{ margin: 0 }}>{source.last_error}</p>}
          {source.schema_error && <p className="form-error" style={{ margin: 0 }}>{source.schema_error}</p>}
          <PolicyPicker value={source.edit_policy} disabled={policy.isPending}
            onChange={(p) => p !== source.edit_policy && policy.mutate(p)} />
          <div className="actions">
            <button aria-expanded={panel === "tables"} onClick={() => show("tables")}>
              <Table2 size={14} /> {panel === "tables" ? "Hide tables" : "Tables"}
            </button>
            <button aria-expanded={panel === "log"} onClick={() => show("log")}>
              <History size={14} /> Query log
            </button>
            <button disabled={reread.isPending} onClick={() => reread.mutate()}>
              <RefreshCw size={14} className={reread.isPending ? "animate-spin" : undefined} /> Re-read tables
            </button>
            <button disabled={retest.isPending} onClick={() => retest.mutate()}>
              <Plug size={14} /> Test
            </button>
            <button aria-expanded={panel === "edit"} onClick={() => show("edit")}>Edit connection</button>
            <button disabled={toggle.isPending} onClick={() => toggle.mutate()}>
              {source.enabled ? <Pause size={14} /> : <Play size={14} />}
              {source.enabled ? "Pause" : "Resume"}
            </button>
            {confirmDelete ? (
              <>
                <button className="danger confirm" disabled={remove.isPending} onClick={() => remove.mutate()}>
                  <Trash2 size={14} /> Remove connection
                </button>
                <button onClick={() => setConfirmDelete(false)}>Cancel</button>
              </>
            ) : (
              <button className="danger" onClick={() => setConfirmDelete(true)}><Trash2 size={14} /> Remove</button>
            )}
          </div>
          {confirmDelete && <p className="muted-line">This removes the connection, notes and query log from Smish.
            The database itself isn&apos;t touched.</p>}
          <TestLine res={test} />
          {(policy.isError || reread.isError || toggle.isError || remove.isError) && (
            <p className="form-error" style={{ margin: 0 }}>
              {String((policy.error ?? reread.error ?? toggle.error ?? remove.error)?.message)}
            </p>
          )}
          {panel === "tables" && <Schema user={user} source={source} />}
          {panel === "log" && <QueryLog user={user} source={source} />}
          {panel === "edit" && <EditConnection user={user} source={source} done={() => setPanel(null)} />}
        </div>
      </Collapse>
    </div>
  );
}

export default function Databases({ user }: { user: User }) {
  const isLocal = user.storage_mode === "local";
  const [localDbs, setLocalDbs] = useState<LocalDataSource[]>(() =>
    isLocal ? getLocalDbs(user.id) : [],
  );
  useEffect(() => {
    if (!isLocal) return;
    const sync = () => setLocalDbs(getLocalDbs(user.id));
    sync();
    window.addEventListener(LOCAL_DBS_EVENT, sync);
    return () => window.removeEventListener(LOCAL_DBS_EVENT, sync);
  }, [isLocal, user.id]);

  const { data: serverDbs, isLoading, error } = useQuery({
    queryKey: ["data-sources"],
    queryFn: listSources,
    enabled: !isLocal,
  });
  const rows = isLocal ? localDbs : serverDbs;

  return (
    <section className="space-y-4">
      {isLocal && (
        <div className="storage-banner" role="note">
          <HardDrive size={16} />
          <span>
            <strong>Browser local storage active:</strong> Database credentials, tables, and query
            logs stay strictly in this browser and are never saved on the server. Clearing browser
            site data or changing devices will wipe them.{" "}
            <a href="#settings" style={{ color: "inherit", textDecoration: "underline" }}>
              Manage in Settings
            </a>
          </span>
        </div>
      )}
      <AddDatabase user={user} />
      {!isLocal && isLoading && <p className="text-sm text-zinc-500">Loading databases…</p>}
      {!isLocal && error && <p className="form-error">{String(error.message)}</p>}
      {rows?.length === 0 && (
        <p className="text-sm text-zinc-500">No databases yet. Connect one above, then ask about its data in Chat.</p>
      )}
      <div className="site-list">
        {rows?.map((s) => <DatabaseCard key={s.id} user={user} source={s} />)}
      </div>
    </section>
  );
}
