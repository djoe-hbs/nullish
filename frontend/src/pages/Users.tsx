import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Check,
  ChevronDown,
  CircleCheck,
  CirclePause,
  Clock,
  Database,
  Eye,
  EyeOff,
  Globe,
  KeyRound,
  Loader2,
  Lock,
  MailCheck,
  MailWarning,
  MessageCircle,
  Shield,
  Trash2,
  Unlock,
  UserCheck,
  UserPlus,
  Users as UsersIcon,
  UserX,
  X,
} from "lucide-react";
import { useState } from "react";
import {
  approveAdminUser,
  createAdminUser,
  deleteAdminUser,
  getMe,
  listAdminUsers,
  rejectAdminUser,
  timeAgo,
  updateAdminUser,
  verifyAdminUserEmail,
  type AdminUser,
  type AdminUserCreate,
} from "../api/client";
import Collapse from "../components/Collapse";

const POLICY_LABEL: Record<string, string> = {
  read_only: "Never edit",
  ask: "Ask before editing",
  auto: "Edit automatically",
};

function AddUserForm() {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPw, setShowPw] = useState(false);
  const [role, setRole] = useState<"member" | "admin">("member");

  const add = useMutation({
    mutationFn: (body: AdminUserCreate) => createAdminUser(body),
    onSuccess: () => {
      setName("");
      setUsername("");
      setEmail("");
      setPassword("");
      setRole("member");
      qc.invalidateQueries({ queryKey: ["admin-users"] });
      qc.invalidateQueries({ queryKey: ["admin-metrics"] });
    },
  });

  const ready = name.trim() && username.trim().length >= 3 && email.trim() && password.length >= 8;

  return (
    <form
      className="site-card add-site add-db"
      onSubmit={(e) => {
        e.preventDefault();
        add.mutate({ name, username, email, password, role });
      }}
    >
      <div className="user-form-head">
        <UserPlus size={17} />
        <strong>Create user</strong>
        <small>Users sign in with their email or username and manage their own isolated sources.</small>
      </div>
      <div className="add-options" style={{ padding: 0 }}>
        <label>
          Full name
          <input
            required
            placeholder="Asha Rao"
            value={name}
            onChange={(e) => setName(e.target.value)}
            autoComplete="off"
          />
        </label>
        <label>
          Username
          <input
            required
            minLength={3}
            maxLength={64}
            placeholder="asha.rao"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="off"
          />
        </label>
        <label>
          Email address
          <input
            type="email"
            required
            placeholder="asha@example.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoComplete="off"
          />
        </label>
        <label>
          Password (8+ characters)
          <span className="pw-field">
            <input
              type={showPw ? "text" : "password"}
              required
              minLength={8}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="new-password"
            />
            <button
              type="button"
              className="icon-btn"
              onClick={() => setShowPw((v) => !v)}
              aria-label={showPw ? "Hide password" : "Show password"}
            >
              {showPw ? <EyeOff size={15} /> : <Eye size={15} />}
            </button>
          </span>
        </label>
        <label>
          Role
          <select value={role} onChange={(e) => setRole(e.target.value as "member" | "admin")}>
            <option value="member">User (Chat, Sources, Settings)</option>
            <option value="admin">Admin (Full management + Dashboard)</option>
          </select>
        </label>
      </div>
      <div className="actions db-submit">
        <button type="submit" className="primary" disabled={!ready || add.isPending}>
          {add.isPending ? <Loader2 size={14} className="animate-spin" /> : <UserPlus size={14} />}
          {add.isPending ? "Creating…" : "Create user"}
        </button>
      </div>
      {add.isError && <p className="form-error">{String((add.error as Error).message)}</p>}
    </form>
  );
}

function UserCard({ user, selfId }: { user: AdminUser; selfId?: number }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [resetPw, setResetPw] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const [name, setName] = useState(user.name);
  const [username, setUsername] = useState(user.username ?? "");
  const [email, setEmail] = useState(user.email);
  const [role, setRole] = useState<"admin" | "member">(user.role);
  const [newPassword, setNewPassword] = useState("");
  const [msg, setMsg] = useState<string | null>(null);

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["admin-users"] });
    qc.invalidateQueries({ queryKey: ["admin-metrics"] });
  };

  const patch = useMutation({
    mutationFn: (body: Parameters<typeof updateAdminUser>[1]) => updateAdminUser(user.id, body),
    onSuccess: () => {
      setEditing(false);
      setResetPw(false);
      setNewPassword("");
      setMsg("Saved.");
      refresh();
    },
    onError: (err) => setMsg((err as Error).message),
  });

  const approve = useMutation({
    mutationFn: () => approveAdminUser(user.id),
    onSuccess: () => {
      setMsg("Account approved.");
      refresh();
    },
    onError: (err) => setMsg((err as Error).message),
  });

  const verifyEmailAction = useMutation({
    mutationFn: (alsoApprove: boolean) => verifyAdminUserEmail(user.id, alsoApprove),
    onSuccess: (_u, alsoApprove) => {
      setMsg(alsoApprove ? "Email verified and account approved." : "Email marked as verified.");
      refresh();
    },
    onError: (err) => setMsg((err as Error).message),
  });

  const remove = useMutation({
    mutationFn: () => deleteAdminUser(user.id),
    onSuccess: refresh,
    onError: (err) => setMsg((err as Error).message),
  });

  const isSelf = user.id === selfId;
  const isLocked = Boolean(user.locked_until && new Date(user.locked_until).getTime() > Date.now());
  const displayName = user.name || user.username || user.email;

  return (
    <div className={`site-card site${user.is_active ? "" : " paused"}`}>
      <button className="site-head" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        <span className="site-avatar" aria-hidden>{displayName[0]?.toUpperCase() ?? "?"}</span>
        <span className="site-title">
          <strong>
            {displayName} {isSelf && <small className="tag">You</small>}
          </strong>
          <small>
            {user.username ? `@${user.username} · ` : ""}
            {user.email} · {user.sites.length} site{user.sites.length === 1 ? "" : "s"} ·{" "}
            {user.databases.length} DB{user.databases.length === 1 ? "" : "s"}
          </small>
        </span>
        <span className={`chip ${user.role === "admin" ? "perm-chip auto" : ""}`}>
          <Shield size={12} /> {user.role === "admin" ? "Admin" : "User"}
        </span>
        {!user.email_verified && (
          <span className="chip warn"><MailWarning size={12} /> Unverified email</span>
        )}
        {isLocked ? (
          <span className="chip warn"><Lock size={12} /> Locked</span>
        ) : user.pending_approval ? (
          <span className="chip warn"><Clock size={12} /> Pending approval</span>
        ) : user.is_active ? (
          <span className="chip ok"><CircleCheck size={12} /> Active</span>
        ) : (
          <span className="chip"><CirclePause size={12} /> Deactivated</span>
        )}
        <ChevronDown size={18} className="chev" />
      </button>

      <Collapse open={open}>
        <div className="site-body">
          <div className="facts">
            <span>Joined {timeAgo(user.created_at)}</span>
            <span>Last sign-in: {timeAgo(user.last_login_at)}</span>
            <span>Email: {user.email_verified ? "Verified" : "Unverified"}</span>
            <span>
              Storage:{" "}
              {user.storage_mode === "local"
                ? "Browser local only"
                : user.storage_mode === "db"
                  ? "Server DB (encrypted)"
                  : "Pending setup"}
            </span>
            <span>
              <MessageCircle size={12} style={{ display: "inline", verticalAlign: "-1px" }} />{" "}
              {user.conversation_count} private conversation{user.conversation_count === 1 ? "" : "s"}
            </span>
          </div>

          <div className="actions">
            {user.pending_approval && !user.email_verified && (
              <button
                className="primary"
                disabled={verifyEmailAction.isPending || approve.isPending}
                onClick={() => verifyEmailAction.mutate(true)}
              >
                <MailCheck size={14} /> Verify &amp; approve
              </button>
            )}
            {user.pending_approval && (
              <button
                className={user.email_verified ? "primary" : ""}
                disabled={approve.isPending || verifyEmailAction.isPending}
                onClick={() => approve.mutate()}
              >
                <Check size={14} /> {user.email_verified ? "Approve account" : "Approve only"}
              </button>
            )}
            {!user.pending_approval && !user.email_verified && (
              <button
                className="primary"
                disabled={verifyEmailAction.isPending}
                onClick={() => verifyEmailAction.mutate(false)}
              >
                <MailCheck size={14} /> Verify email
              </button>
            )}
            <button onClick={() => { setEditing((v) => !v); setResetPw(false); setMsg(null); }}>
              Edit user
            </button>
            <button onClick={() => { setResetPw((v) => !v); setEditing(false); setMsg(null); }}>
              <KeyRound size={14} /> Reset password
            </button>
            {isLocked && (
              <button disabled={patch.isPending} onClick={() => patch.mutate({ unlock: true })}>
                <Unlock size={14} /> Unlock
              </button>
            )}
            {!isSelf && !user.pending_approval && (
              <button
                disabled={patch.isPending}
                onClick={() => patch.mutate({ is_active: !user.is_active })}
              >
                {user.is_active ? <UserX size={14} /> : <UserCheck size={14} />}
                {user.is_active ? "Deactivate" : "Reactivate"}
              </button>
            )}
            {!isSelf && (
              confirmDelete ? (
                <>
                  <button
                    className="danger confirm"
                    disabled={remove.isPending}
                    onClick={() => remove.mutate()}
                  >
                    <Trash2 size={14} /> Confirm delete user
                  </button>
                  <button onClick={() => setConfirmDelete(false)}>Cancel</button>
                </>
              ) : (
                <button className="danger" onClick={() => setConfirmDelete(true)}>
                  <Trash2 size={14} /> Delete
                </button>
              )
            )}
          </div>

          {editing && (
            <form
              className="add-options edit-db"
              onSubmit={(e) => {
                e.preventDefault();
                patch.mutate({ name, username, email, role });
              }}
            >
              <label>
                Full name
                <input required value={name} onChange={(e) => setName(e.target.value)} />
              </label>
              <label>
                Username
                <input required minLength={3} value={username} onChange={(e) => setUsername(e.target.value)} />
              </label>
              <label>
                Email
                <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
              </label>
              <label>
                Role
                <select
                  value={role}
                  disabled={isSelf}
                  onChange={(e) => setRole(e.target.value as "admin" | "member")}
                >
                  <option value="member">User</option>
                  <option value="admin">Admin</option>
                </select>
              </label>
              <div className="actions">
                <button type="submit" className="primary" disabled={patch.isPending}>Save changes</button>
                <button type="button" onClick={() => setEditing(false)}>Cancel</button>
              </div>
            </form>
          )}

          {resetPw && (
            <form
              className="add-options edit-db"
              onSubmit={(e) => {
                e.preventDefault();
                patch.mutate({ password: newPassword });
              }}
            >
              <label>
                New password (8+ characters; signs out user's active sessions)
                <input
                  type="password"
                  required
                  minLength={8}
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  autoComplete="new-password"
                />
              </label>
              <div className="actions">
                <button type="submit" className="primary" disabled={patch.isPending || newPassword.length < 8}>
                  Update password
                </button>
                <button type="button" onClick={() => setResetPw(false)}>Cancel</button>
              </div>
            </form>
          )}

          {msg && (
            <p
              className={
                patch.isError || remove.isError || verifyEmailAction.isError || approve.isError
                  ? "form-error"
                  : "test-line ok"
              }
              style={{ margin: 0 }}
            >
              {msg}
            </p>
          )}

          {/* User's sources inventory (no credentials, logs, or chat contents) */}
          <div className="user-sources">
            <h3>Configured sources ({user.sites.length + user.databases.length})</h3>
            {user.sites.length === 0 && user.databases.length === 0 ? (
              <p className="muted-line">No websites or databases configured by this user yet.</p>
            ) : (
              <div className="user-source-list">
                {user.sites.map((s) => (
                  <div key={`site-${s.id}`} className="user-source-item">
                    <Globe size={14} />
                    <div>
                      <strong>{s.name}</strong>
                      <small>{s.root_url} · {s.pages} pages · {s.chunks} chunks</small>
                    </div>
                    <span className={`chip ${s.enabled ? "ok" : ""}`}>
                      {s.enabled ? (s.last_job_status ?? "Enabled") : "Paused"}
                    </span>
                  </div>
                ))}
                {user.databases.map((d) => (
                  <div key={`db-${d.id}`} className="user-source-item">
                    <Database size={14} />
                    <div>
                      <strong>{d.name}</strong>
                      <small>
                        {d.kind === "postgres" ? "PostgreSQL" : "MySQL/MariaDB"} · {d.tables} tables ·{" "}
                        {POLICY_LABEL[d.edit_policy] ?? d.edit_policy}
                      </small>
                    </div>
                    <span className={`chip ${d.last_test_ok === false ? "bad" : d.last_test_ok ? "ok" : ""}`}>
                      {d.last_test_ok === false ? (
                        <><AlertTriangle size={12} /> Error</>
                      ) : d.last_test_ok ? "Connected" : "Untested"}
                    </span>
                  </div>
                ))}
              </div>
            )}
            <p className="muted-line">
              Credentials, host details, schemas, SQL query logs, and chat histories remain encrypted/private to this user.
            </p>
          </div>
        </div>
      </Collapse>
    </div>
  );
}

function PendingApprovals({ pending }: { pending: AdminUser[] }) {
  const qc = useQueryClient();
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["admin-users"] });
    qc.invalidateQueries({ queryKey: ["admin-metrics"] });
  };
  const approve = useMutation({
    mutationFn: (id: number) => approveAdminUser(id),
    onSuccess: refresh,
  });
  const verifyAndApprove = useMutation({
    mutationFn: (id: number) => verifyAdminUserEmail(id, true),
    onSuccess: refresh,
  });
  const reject = useMutation({
    mutationFn: (id: number) => rejectAdminUser(id),
    onSuccess: refresh,
  });

  if (!pending.length) return null;

  const busy = approve.isPending || verifyAndApprove.isPending || reject.isPending;

  return (
    <section className="site-card pending-box">
      <div className="user-form-head">
        <Clock size={17} style={{ color: "var(--warn)" }} />
        <strong>Pending account approvals ({pending.length})</strong>
        <small>
          Self-registered users must verify their email and receive administrator approval, unless
          you verify &amp; approve them directly.
        </small>
      </div>
      <div className="user-source-list">
        {pending.map((u) => (
          <div key={u.id} className="pending-row">
            <div>
              <strong>
                {u.name || u.username}{" "}
                <span className={`chip ${u.email_verified ? "ok" : "warn"}`} style={{ marginLeft: ".35rem" }}>
                  {u.email_verified ? <MailCheck size={11} /> : <MailWarning size={11} />}
                  {u.email_verified ? "Email verified" : "Email unverified"}
                </span>
              </strong>
              <small>@{u.username} · {u.email} · requested {timeAgo(u.created_at)}</small>
            </div>
            <div className="actions">
              {!u.email_verified && (
                <button
                  className="primary"
                  disabled={busy}
                  onClick={() => verifyAndApprove.mutate(u.id)}
                >
                  <MailCheck size={14} /> Verify &amp; approve
                </button>
              )}
              <button
                className={u.email_verified ? "primary" : ""}
                disabled={busy}
                onClick={() => approve.mutate(u.id)}
              >
                <Check size={14} /> {u.email_verified ? "Approve" : "Approve only"}
              </button>
              <button
                className="danger"
                disabled={busy}
                onClick={() => reject.mutate(u.id)}
              >
                <X size={14} /> Reject
              </button>
            </div>
          </div>
        ))}
      </div>
      {(approve.isError || verifyAndApprove.isError || reject.isError) && (
        <p className="form-error">
          {String(((approve.error ?? verifyAndApprove.error ?? reject.error) as Error).message)}
        </p>
      )}
    </section>
  );
}

export default function Users() {
  const { data: me } = useQuery({ queryKey: ["me"], queryFn: getMe, staleTime: Infinity });
  const { data: users, isLoading, error } = useQuery({
    queryKey: ["admin-users"],
    queryFn: listAdminUsers,
  });
  const pending = (users ?? []).filter((u) => u.pending_approval);

  return (
    <section className="space-y-4">
      <div className="page-heading">
        <span className="page-icon"><UsersIcon /></span>
        <div>
          <h1>User management</h1>
          <p>Approve registrations, create accounts, and inspect each user's source inventory without exposing credentials or chats.</p>
        </div>
      </div>

      <PendingApprovals pending={pending} />
      <AddUserForm />

      {isLoading && <p className="text-sm text-zinc-500">Loading users…</p>}
      {error && <p className="form-error">{String((error as Error).message)}</p>}
      <div className="site-list">
        {users?.map((u) => <UserCard key={u.id} user={u} selfId={me?.id} />)}
      </div>
    </section>
  );
}

