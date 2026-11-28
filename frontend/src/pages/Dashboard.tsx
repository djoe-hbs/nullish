import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  Cpu,
  Database,
  Globe,
  LayoutDashboard,
  MessageCircle,
  Server,
  ShieldCheck,
  Users,
} from "lucide-react";
import { fetchHealth, getAdminMetrics } from "../api/client";

const INTENT_LABELS: Record<string, string> = {
  search: "Website Q&A",
  database: "Database SQL",
  sites_overview: "Site overview",
  chat: "Small talk",
  model: "Model chat",
};

const POLICY_LABELS: Record<string, string> = {
  read_only: "Never edit",
  ask: "Ask before editing",
  auto: "Edit automatically",
};

function fmtMs(ms: number | null) {
  if (ms == null) return "–";
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${ms} ms`;
}

export default function Dashboard() {
  const { data: m, isLoading, error } = useQuery({
    queryKey: ["admin-metrics"],
    queryFn: getAdminMetrics,
    refetchInterval: 15_000,
  });
  const { data: health } = useQuery({
    queryKey: ["health"],
    queryFn: fetchHealth,
    refetchInterval: 15_000,
    retry: false,
  });

  if (isLoading) return <p className="text-sm text-zinc-500">Loading dashboard metrics…</p>;
  if (error) return <p className="form-error">{String((error as Error).message)}</p>;
  if (!m) return null;

  const embedPct = m.sites.chunks
    ? Math.round((m.sites.chunks_embedded / m.sites.chunks) * 100)
    : 100;
  const maxDaily = Math.max(
    1,
    ...m.chats.daily_7d.map((d) => Math.max(d.messages, d.queries)),
  );
  const intentEntries = Object.entries(m.chats.by_intent).sort((a, b) => b[1] - a[1]);
  const totalIntent = intentEntries.reduce((acc, [, n]) => acc + n, 0) || 1;

  return (
    <section className="space-y-5">
      <div className="page-heading">
        <span className="page-icon"><LayoutDashboard /></span>
        <div>
          <h1>Admin dashboard</h1>
          <p>Aggregate usage, indexing, database safety, and infrastructure metrics across the system.</p>
        </div>
      </div>

      {/* Primary KPI cards */}
      <div className="dash-grid">
        <article className="site-card dash-kpi">
          <div className="dash-kpi-head">
            <span className="dash-kpi-icon"><Users size={18} /></span>
            <span className="dash-kpi-label">Users & sessions</span>
          </div>
          <strong className="dash-kpi-val">{m.users.total}</strong>
          <div className="dash-kpi-sub">
            <span>
              {m.users.active} active · {m.users.admins} admin · {m.users.members} user
              {m.users.pending_approval > 0 && (
                <> · <a href="#users" className="link-btn" style={{ padding: 0 }}>{m.users.pending_approval} pending approval</a></>
              )}
            </span>
            <span>{m.users.active_sessions} open session{m.users.active_sessions === 1 ? "" : "s"} · {m.users.logins_24h} login{m.users.logins_24h === 1 ? "" : "s"} (24h)</span>
          </div>
        </article>

        <article className="site-card dash-kpi">
          <div className="dash-kpi-head">
            <span className="dash-kpi-icon"><MessageCircle size={18} /></span>
            <span className="dash-kpi-label">Conversations & replies</span>
          </div>
          <strong className="dash-kpi-val">{m.chats.conversations}</strong>
          <div className="dash-kpi-sub">
            <span>{m.chats.messages} messages ({m.chats.messages_24h} in last 24h)</span>
            <span>Avg latency {fmtMs(m.chats.avg_latency_ms)} · First token {fmtMs(m.chats.avg_first_token_ms)}</span>
          </div>
        </article>

        <article className="site-card dash-kpi">
          <div className="dash-kpi-head">
            <span className="dash-kpi-icon"><Globe size={18} /></span>
            <span className="dash-kpi-label">Websites & index</span>
          </div>
          <strong className="dash-kpi-val">{m.sites.total}</strong>
          <div className="dash-kpi-sub">
            <span>{m.sites.enabled} enabled · {m.sites.pages_active} active pages · {m.sites.pages_gone} gone</span>
            <span>
              {m.sites.chunks.toLocaleString()} chunks · {embedPct}% embedded
              {(m.sites.jobs_running > 0 || m.sites.jobs_queued > 0) &&
                ` · ${m.sites.jobs_running} running, ${m.sites.jobs_queued} queued`}
            </span>
          </div>
        </article>

        <article className="site-card dash-kpi">
          <div className="dash-kpi-head">
            <span className="dash-kpi-icon"><Database size={18} /></span>
            <span className="dash-kpi-label">Databases & SQL audit</span>
          </div>
          <strong className="dash-kpi-val">{m.databases.total}</strong>
          <div className="dash-kpi-sub">
            <span>{m.databases.postgres} Postgres · {m.databases.mysql} MySQL/MariaDB</span>
            <span>
              {m.databases.queries_total} queries ({m.databases.queries_ok} ok · {m.databases.queries_refused} refused · {m.databases.pending_approvals} pending)
            </span>
          </div>
        </article>
      </div>

      {/* Activity + Intent / Policies */}
      <div className="dash-panels">
        <section className="site-card dash-panel">
          <div className="dash-panel-head">
            <h2>7-day activity</h2>
            <div className="dash-legend">
              <span className="legend-dot msg" /> User prompts
              <span className="legend-dot sql" /> SQL queries
            </div>
          </div>
          <div className="dash-bars" role="img" aria-label="7 day prompt and SQL query activity">
            {m.chats.daily_7d.map((d) => {
              const msgH = Math.round((d.messages / maxDaily) * 100);
              const sqlH = Math.round((d.queries / maxDaily) * 100);
              const label = new Date(d.date + "T00:00:00Z").toLocaleDateString(undefined, {
                month: "short",
                day: "numeric",
                timeZone: "UTC",
              });
              return (
                <div key={d.date} className="dash-bar-col" title={`${label}: ${d.messages} prompts, ${d.queries} SQL queries`}>
                  <div className="dash-bar-track">
                    <span className="dash-bar msg" style={{ height: `${Math.max(msgH, d.messages ? 6 : 0)}%` }} />
                    <span className="dash-bar sql" style={{ height: `${Math.max(sqlH, d.queries ? 6 : 0)}%` }} />
                  </div>
                  <small>{label}</small>
                </div>
              );
            })}
          </div>
        </section>

        <section className="site-card dash-panel">
          <div className="dash-panel-head">
            <h2>Answer modes & intents</h2>
            {m.chats.not_found_count > 0 && (
              <span className="chip">{m.chats.not_found_count} not found</span>
            )}
          </div>
          {intentEntries.length === 0 ? (
            <p className="muted-line">No assistant responses recorded yet.</p>
          ) : (
            <ul className="dash-breakdown">
              {intentEntries.map(([intent, count]) => (
                <li key={intent}>
                  <div className="dash-row-label">
                    <span>{INTENT_LABELS[intent] ?? intent}</span>
                    <strong>{count}</strong>
                  </div>
                  <div className="progress">
                    <span style={{ width: `${Math.round((count / totalIntent) * 100)}%` }} />
                  </div>
                </li>
              ))}
            </ul>
          )}

          <div className="dash-sub-block">
            <h3>Database edit policies</h3>
            <div className="dash-chips">
              {(["read_only", "ask", "auto"] as const).map((p) => (
                <span key={p} className={`chip perm-chip ${p}`}>
                  {POLICY_LABELS[p]}: {m.databases.by_policy[p] ?? 0}
                </span>
              ))}
            </div>
          </div>
        </section>
      </div>

      {/* Infrastructure summary & quick actions */}
      <section className="site-card dash-panel">
        <div className="dash-panel-head">
          <h2>Infrastructure health</h2>
          <div className="dash-links">
            <a href="#status" className="link-btn"><Activity size={14} /> Full status</a>
            <a href="#users" className="link-btn"><Users size={14} /> Manage users</a>
          </div>
        </div>
        <div className="dash-health-row">
          {health ? (
            Object.values(health.services).map((s) => {
              const isGpu = s.name === "chat" || s.name === "embedding" || s.name === "reranker";
              return (
                <div key={s.name} className="dash-health-pill">
                  {isGpu ? <Cpu size={14} /> : <Server size={14} />}
                  <span className="capitalize">{s.name}</span>
                  <span className={`chip ${s.ok ? "ok" : isGpu ? "" : "bad"}`}>
                    <ShieldCheck size={12} /> {s.ok ? "Online" : isGpu ? "Offline" : "Down"}
                  </span>
                </div>
              );
            })
          ) : (
            <p className="muted-line">Checking services…</p>
          )}
        </div>
        <p className="muted-line">
          Privacy guard: dashboard metrics show system totals only. Individual user chat messages,
          crawled contents, and encrypted database credentials are never displayed here.
        </p>
      </section>
    </section>
  );
}

