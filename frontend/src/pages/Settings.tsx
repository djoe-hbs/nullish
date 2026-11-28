import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Check,
  Database,
  HardDrive,
  KeyRound,
  Loader2,
  LogOut,
  MailCheck,
  MailWarning,
  Monitor,
  Moon,
  Settings as SettingsIcon,
  ShieldCheck,
  Sliders,
  Sun,
  User as UserIcon,
} from "lucide-react";
import { useEffect, useState } from "react";
import {
  changePassword,
  createSource,
  getConversation,
  getSchema,
  getSystemSettings,
  importConversations,
  listConversations,
  listSessions,
  listSources,
  patchSchema,
  revokeOtherSessions,
  revokeSession,
  setStorageMode,
  timeAgo,
  updateProfile,
  updateSystemSettings,
  type StorageMode,
  type SystemSettings,
  type User,
} from "../api/client";
import {
  clearLocalChats,
  clearLocalDbs,
  getLocalChats,
  getLocalDbs,
  saveLocalChats,
  saveLocalDbs,
  type LocalDataSource,
} from "../storage/localStore";
import LegalSettingsSection from "../components/LegalSettingsSection";

function formatUA(ua: string | null) {
  if (!ua) return "Unknown browser";
  const browser = ua.includes("Edg/") ? "Edge"
    : ua.includes("Chrome/") ? "Chrome"
    : ua.includes("Firefox/") ? "Firefox"
    : ua.includes("Safari/") ? "Safari"
    : "Browser";
  const os = ua.includes("Windows") ? "Windows"
    : ua.includes("Mac OS") ? "macOS"
    : ua.includes("Linux") ? "Linux"
    : ua.includes("Android") ? "Android"
    : ua.includes("iPhone") ? "iOS"
    : "";
  return os ? `${browser} on ${os}` : browser;
}

function ProfileSection({ user }: { user: User }) {
  const qc = useQueryClient();
  const canEditIdentity = user.role === "admin";
  const [name, setName] = useState(user.name);
  const [username, setUsername] = useState(user.username ?? "");
  const [email, setEmail] = useState(user.email);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    setName(user.name);
    setUsername(user.username ?? "");
    setEmail(user.email);
  }, [user]);

  const save = useMutation({
    mutationFn: () =>
      updateProfile(canEditIdentity ? { name, username, email } : { name }),
    onSuccess: (updated) => {
      qc.setQueryData(["me"], updated);
      setSaved(true);
      window.setTimeout(() => setSaved(false), 2500);
    },
  });

  return (
    <form
      className="site-card add-db"
      onSubmit={(e) => {
        e.preventDefault();
        setSaved(false);
        save.mutate();
      }}
    >
      <div className="user-form-head">
        <UserIcon size={17} />
        <strong>Profile</strong>
        <small>
          {canEditIdentity
            ? "Your account name, username (used for sign-in), and email address."
            : "You can update your display name here. Username and email address are managed by an administrator."}
        </small>
      </div>
      <div className="add-options" style={{ padding: 0 }}>
        <label>
          Full name
          <input required value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label>
          Username
          <input
            required={canEditIdentity}
            minLength={3}
            disabled={!canEditIdentity}
            value={username}
            onChange={(e) => setUsername(e.target.value)}
          />
        </label>
        <label>
          Email address
          <input
            type="email"
            required={canEditIdentity}
            disabled={!canEditIdentity}
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </label>
      </div>
      <div className="actions db-submit" style={{ justifyContent: "space-between", alignItems: "center" }}>
        <span className={`chip ${user.email_verified_at ? "ok" : "warn"}`}>
          {user.email_verified_at ? <MailCheck size={12} /> : <MailWarning size={12} />}
          {user.email_verified_at ? "Email verified" : "Email unverified"}
        </span>
        <button type="submit" className="primary" disabled={save.isPending}>
          {save.isPending ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
          {save.isPending ? "Saving…" : "Save profile"}
        </button>
      </div>
      {saved && <p className="test-line ok"><Check size={14} /> Profile updated.</p>}
      {save.isError && <p className="form-error">{String((save.error as Error).message)}</p>}
    </form>
  );
}

function PasswordSection() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [msg, setMsg] = useState<string | null>(null);

  const update = useMutation({
    mutationFn: () => changePassword(current, next),
    onSuccess: () => {
      setCurrent("");
      setNext("");
      setMsg("Password updated. Other devices were signed out.");
    },
  });

  return (
    <form
      className="site-card add-db"
      onSubmit={(e) => {
        e.preventDefault();
        setMsg(null);
        update.mutate();
      }}
    >
      <div className="user-form-head">
        <KeyRound size={17} />
        <strong>Password</strong>
        <small>Changing your password keeps this browser signed in and revokes all other sessions.</small>
      </div>
      <div className="add-options" style={{ padding: 0 }}>
        <label>
          Current password
          <input
            type="password"
            autoComplete="current-password"
            required
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
          />
        </label>
        <label>
          New password (8+ characters)
          <input
            type="password"
            autoComplete="new-password"
            required
            minLength={8}
            value={next}
            onChange={(e) => setNext(e.target.value)}
          />
        </label>
      </div>
      <div className="actions db-submit">
        <button type="submit" className="primary" disabled={update.isPending || !current || next.length < 8}>
          {update.isPending ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
          Change password
        </button>
      </div>
      {msg && <p className="test-line ok"><Check size={14} /> {msg}</p>}
      {update.isError && <p className="form-error">{String((update.error as Error).message)}</p>}
    </form>
  );
}

function SessionsSection() {
  const qc = useQueryClient();
  const { data: sessions, isLoading } = useQuery({
    queryKey: ["auth-sessions"],
    queryFn: listSessions,
  });

  const revokeOne = useMutation({
    mutationFn: (id: number) => revokeSession(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["auth-sessions"] }),
  });

  const revokeAllOthers = useMutation({
    mutationFn: () => revokeOtherSessions(),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["auth-sessions"] }),
  });

  const hasOthers = (sessions ?? []).some((s) => !s.is_current);

  return (
    <section className="site-card add-db">
      <div className="user-form-head">
        <Monitor size={17} />
        <strong>Active sessions</strong>
        <small>Browsers and devices currently signed in to your account.</small>
      </div>
      {isLoading ? (
        <p className="muted-line">Loading sessions…</p>
      ) : (
        <div className="user-source-list">
          {sessions?.map((s) => (
            <div key={s.id} className="user-source-item">
              <Monitor size={15} />
              <div>
                <strong>
                  {formatUA(s.user_agent)} {s.is_current && <span className="tag">Current</span>}
                </strong>
                <small>
                  {s.ip ? `${s.ip} · ` : ""}Signed in {timeAgo(s.created_at)}
                </small>
              </div>
              {!s.is_current && (
                <button
                  type="button"
                  className="chip"
                  disabled={revokeOne.isPending}
                  onClick={() => revokeOne.mutate(s.id)}
                >
                  <LogOut size={12} /> Sign out
                </button>
              )}
            </div>
          ))}
        </div>
      )}
      {hasOthers && (
        <div className="actions db-submit">
          <button
            type="button"
            disabled={revokeAllOthers.isPending}
            onClick={() => revokeAllOthers.mutate()}
          >
            <LogOut size={14} /> Sign out all other devices
          </button>
        </div>
      )}
    </section>
  );
}

function SystemSettingsSection() {
  const qc = useQueryClient();
  const { data, isLoading, error } = useQuery({
    queryKey: ["admin-settings"],
    queryFn: getSystemSettings,
  });
  const [form, setForm] = useState<SystemSettings | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (data) setForm(data);
  }, [data]);

  const save = useMutation({
    mutationFn: (body: SystemSettings) => updateSystemSettings(body),
    onSuccess: (updated) => {
      qc.setQueryData(["admin-settings"], updated);
      qc.invalidateQueries({ queryKey: ["site-defaults"] });
      setSaved(true);
      window.setTimeout(() => setSaved(false), 2500);
    },
  });

  if (isLoading) return <p className="text-sm text-zinc-500">Loading system settings…</p>;
  if (error) return <p className="form-error">{String((error as Error).message)}</p>;
  if (!form) return null;

  const num = (key: keyof SystemSettings, val: number) =>
    setForm((prev) => (prev ? { ...prev, [key]: val } : prev));

  return (
    <form
      className="site-card add-db"
      onSubmit={(e) => {
        e.preventDefault();
        setSaved(false);
        save.mutate(form);
      }}
    >
      <div className="user-form-head">
        <Sliders size={17} />
        <strong>System settings (Admin)</strong>
        <small>Global defaults for chat history, live verification, crawler limits, and system prompts.</small>
      </div>
      <div className="add-options" style={{ padding: 0 }}>
        <label>
          Model chat history budget (chars)
          <input
            type="number"
            min={4000}
            max={120000}
            step={1000}
            value={form.max_history_chars}
            onChange={(e) => num("max_history_chars", Number(e.target.value))}
          />
        </label>
        <label>
          Sources per grounded answer
          <input
            type="number"
            min={1}
            max={12}
            value={form.answer_sources}
            onChange={(e) => num("answer_sources", Number(e.target.value))}
          />
        </label>
        <label>
          Pages checked live per question
          <input
            type="number"
            min={0}
            max={12}
            value={form.live_verify_pages}
            onChange={(e) => num("live_verify_pages", Number(e.target.value))}
          />
        </label>
        <label>
          Live verification cache (seconds)
          <input
            type="number"
            min={0}
            max={86400}
            value={form.live_reverify_after_sec}
            onChange={(e) => num("live_reverify_after_sec", Number(e.target.value))}
          />
        </label>
        <label>
          Live check budget (seconds)
          <input
            type="number"
            min={2}
            max={60}
            step={0.5}
            value={form.live_total_budget_sec}
            onChange={(e) => num("live_total_budget_sec", Number(e.target.value))}
          />
        </label>
        <label>
          Default max pages per site
          <input
            type="number"
            min={1}
            max={5000}
            value={form.default_max_pages}
            onChange={(e) => num("default_max_pages", Number(e.target.value))}
          />
        </label>
        <label>
          Default crawl depth
          <input
            type="number"
            min={0}
            max={10}
            value={form.default_max_depth}
            onChange={(e) => num("default_max_depth", Number(e.target.value))}
          />
        </label>
        <label>
          Default crawl delay (ms)
          <input
            type="number"
            min={0}
            max={30000}
            value={form.default_crawl_delay_ms}
            onChange={(e) => num("default_crawl_delay_ms", Number(e.target.value))}
          />
        </label>
      </div>

      <div className="settings-prompts">
        <label>
          Grounded answer system prompt
          <textarea
            rows={5}
            minLength={10}
            maxLength={4000}
            value={form.answer_system_prompt}
            onChange={(e) => setForm({ ...form, answer_system_prompt: e.target.value })}
          />
        </label>
        <label>
          Model chat system prompt
          <textarea
            rows={3}
            minLength={10}
            maxLength={4000}
            value={form.chat_system_prompt}
            onChange={(e) => setForm({ ...form, chat_system_prompt: e.target.value })}
          />
        </label>
      </div>

      <div className="actions db-submit">
        <button type="submit" className="primary" disabled={save.isPending}>
          {save.isPending ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
          {save.isPending ? "Saving…" : "Save system settings"}
        </button>
      </div>
      {saved && <p className="test-line ok"><Check size={14} /> System settings saved.</p>}
      {save.isError && <p className="form-error">{String((save.error as Error).message)}</p>}
    </form>
  );
}

function StorageSecuritySection({ user }: { user: User }) {
  const qc = useQueryClient();
  const currentMode: StorageMode = user.storage_mode === "local" ? "local" : "db";
  const [targetMode, setTargetMode] = useState<StorageMode>(currentMode);
  const [importLocal, setImportLocal] = useState(true);
  const [exportToLocal, setExportToLocal] = useState(true);
  const [purgeServer, setPurgeServer] = useState(false);
  const [statusMsg, setStatusMsg] = useState<string | null>(null);

  useEffect(() => {
    setTargetMode(user.storage_mode === "local" ? "local" : "db");
  }, [user.storage_mode]);

  const localChats = getLocalChats(user.id);
  const localDbs = getLocalDbs(user.id);
  const hasLocalData = localChats.length > 0 || localDbs.length > 0;
  const changing = targetMode !== currentMode;

  const apply = useMutation({
    mutationFn: async () => {
      if (targetMode === "db") {
        const updated = await setStorageMode("db", false);
        if (importLocal && hasLocalData) {
          if (localChats.length > 0) {
            await importConversations(
              localChats.map((c) => ({
                id: c.id,
                name: c.name,
                title_source: c.title_source,
                mode: c.mode,
                selected_sources: c.selected_sources ?? [],
                updated_ms: new Date(c.updated_at).getTime(),
                messages: c.messages,
              })),
            );
          }
          for (const d of localDbs) {
            let created;
            try {
              created = await createSource({
                name: d.name,
                description: d.description ?? undefined,
                kind: d.kind,
                host: d.host,
                port: d.port,
                database: d.database,
                username: d.username,
                password: d.password,
                ssl_mode: d.ssl_mode,
                edit_policy: d.edit_policy,
                row_limit: d.row_limit,
                timeout_sec: d.timeout_sec,
                enabled: d.enabled,
              });
            } catch {
              continue; // e.g. source with the same name already exists in server DB
            }
            const annotated = d.schema_items.filter((i) => i.hidden || i.note);
            if (annotated.length > 0) {
              try {
                const srvItems = await getSchema(created.id);
                const byKey = new Map(
                  annotated.map((i) => [
                    `${i.schema_name}\0${i.table_name}\0${i.column_name ?? ""}`,
                    i,
                  ]),
                );
                const patches = srvItems
                  .map((s) => {
                    const match = byKey.get(
                      `${s.schema_name}\0${s.table_name}\0${s.column_name ?? ""}`,
                    );
                    return match
                      ? { id: s.id, note: match.note, hidden: match.hidden }
                      : null;
                  })
                  .filter((p): p is { id: number; note: string | null; hidden: boolean } =>
                    Boolean(p),
                  );
                if (patches.length > 0) await patchSchema(created.id, patches);
              } catch {
                /* ignore if schema not readable right now */
              }
            }
          }
          clearLocalChats(user.id);
          clearLocalDbs(user.id);
        }
        return updated;
      } else {
        if (exportToLocal) {
          const [srvChats, srvDbs] = await Promise.all([listConversations(), listSources()]);
          if (srvChats.length > 0) {
            const details = await Promise.all(srvChats.map((c) => getConversation(c.id)));
            saveLocalChats(
              user.id,
              details.map((d) => ({
                id: d.id,
                name: d.name,
                title_source: d.title_source,
                mode: d.mode,
                selected_sources: d.selected_sources ?? [],
                created_at: d.created_at,
                updated_at: d.updated_at,
                messages: d.messages,
              })),
            );
          }
          if (srvDbs.length > 0) {
            const schemas = await Promise.all(
              srvDbs.map((s) => getSchema(s.id).catch(() => [])),
            );
            const localEntries: LocalDataSource[] = srvDbs.map((s, idx) => ({
              ...s,
              password: "",
              has_password: false,
              schema_items: schemas[idx] ?? [],
              query_log: [],
            }));
            saveLocalDbs(user.id, localEntries);
          }
        }
        return await setStorageMode("local", purgeServer);
      }
    },
    onSuccess: (updated) => {
      qc.setQueryData(["me"], updated);
      qc.invalidateQueries({ queryKey: ["conversations"] });
      qc.invalidateQueries({ queryKey: ["data-sources"] });
      qc.invalidateQueries({ queryKey: ["admin-users"] });
      qc.invalidateQueries({ queryKey: ["admin-metrics"] });
      setStatusMsg(
        updated.storage_mode === "local"
          ? "Switched to Browser Local Storage."
          : "Switched to Secure Server Database.",
      );
    },
  });

  return (
    <section className="site-card add-db">
      <div className="user-form-head">
        <ShieldCheck size={17} />
        <strong>Data storage &amp; security</strong>
        <small>
          Choose whether chat history and database credentials are saved persistently in Postgres
          or strictly inside this browser&apos;s storage.
        </small>
      </div>

      <div className="setup-grid" role="radiogroup" aria-label="Storage security mode">
        <button
          type="button"
          role="radio"
          aria-checked={targetMode === "db"}
          className={`setup-option${targetMode === "db" ? " selected" : ""}`}
          onClick={() => {
            setStatusMsg(null);
            setTargetMode("db");
          }}
        >
          <div className="setup-option-top">
            <span className="setup-option-icon db">
              <Database size={18} />
            </span>
            <div>
              <strong>Secure Server Database</strong>
              <span className="tag ok">Persistent · Encrypted</span>
            </div>
          </div>
          <p>
            Saves encrypted database credentials and full conversation history in Postgres across
            all devices. Clearing browser data never loses your work.
          </p>
        </button>

        <button
          type="button"
          role="radio"
          aria-checked={targetMode === "local"}
          className={`setup-option${targetMode === "local" ? " selected" : ""}`}
          onClick={() => {
            setStatusMsg(null);
            setTargetMode("local");
          }}
        >
          <div className="setup-option-top">
            <span className="setup-option-icon local">
              <HardDrive size={18} />
            </span>
            <div>
              <strong>Browser Local Storage Only</strong>
              <span className="tag">Ephemeral</span>
            </div>
          </div>
          <p>
            Keeps chats and DB credentials strictly in this browser (sent transiently only while
            querying). Clearing browser data or changing devices wipes them.
          </p>
        </button>
      </div>

      {changing && targetMode === "db" && hasLocalData && (
        <label className="text-sm flex items-start gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={importLocal}
            onChange={(e) => setImportLocal(e.target.checked)}
          />
          <span>
            Import {localChats.length} local chat{localChats.length === 1 ? "" : "s"} and{" "}
            {localDbs.length} local database{localDbs.length === 1 ? "" : "s"} from this browser
            into the server database and clear browser copies.
          </span>
        </label>
      )}

      {changing && targetMode === "local" && (
        <div className="space-y-2">
          <div className="storage-banner" role="note">
            <AlertTriangle size={16} />
            <span>
              Switching to Browser Local Storage means clearing browser data or switching devices
              will wipe your chats and database configs. Server-encrypted DB passwords cannot be
              exported automatically — re-enter them locally after switching.
            </span>
          </div>
          <label className="text-sm flex items-start gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={exportToLocal}
              onChange={(e) => setExportToLocal(e.target.checked)}
            />
            <span>
              Copy existing server conversations and database connection settings (without passwords)
              to this browser before switching.
            </span>
          </label>
          <label className="text-sm flex items-start gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={purgeServer}
              onChange={(e) => setPurgeServer(e.target.checked)}
            />
            <span>Also delete my saved conversations and database configs from the server DB.</span>
          </label>
        </div>
      )}

      <div className="actions db-submit">
        <button
          type="button"
          className="primary"
          disabled={!changing || apply.isPending}
          onClick={() => apply.mutate()}
        >
          {apply.isPending ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
          {apply.isPending ? "Applying…" : changing ? "Apply storage mode" : "Current setting active"}
        </button>
      </div>
      {statusMsg && (
        <p className="test-line ok">
          <Check size={14} /> {statusMsg}
        </p>
      )}
      {apply.isError && <p className="form-error">{String((apply.error as Error).message)}</p>}
    </section>
  );
}

export default function Settings({
  user,
  dark,
  onToggleTheme,
}: {
  user: User;
  dark: boolean;
  onToggleTheme: () => void;
}) {
  return (
    <section className="space-y-4">
      <div className="page-heading">
        <span className="page-icon"><SettingsIcon /></span>
        <div>
          <h1>Settings</h1>
          <p>
            Manage your profile, password, active sessions, and appearance
            {user.role === "admin" ? ", plus system-wide defaults." : "."}
          </p>
        </div>
      </div>

      <ProfileSection user={user} />

      <StorageSecuritySection user={user} />

      <section className="site-card add-db">
        <div className="user-form-head">
          {dark ? <Moon size={17} /> : <Sun size={17} />}
          <strong>Appearance</strong>
          <small>Choose light or dark theme for this browser.</small>
        </div>
        <div className="actions">
          <button type="button" onClick={onToggleTheme}>
            {dark ? <Sun size={14} /> : <Moon size={14} />}
            Switch to {dark ? "Light" : "Dark"} theme
          </button>
        </div>
      </section>

      <PasswordSection />
      <SessionsSection />

      {user.role === "admin" && (
        <>
          <SystemSettingsSection />
          <LegalSettingsSection />
        </>
      )}
    </section>
  );
}

