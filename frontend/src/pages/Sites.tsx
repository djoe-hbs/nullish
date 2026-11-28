import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ChevronDown,
  CircleCheck,
  CirclePause,
  Clock,
  FileText,
  Globe,
  Loader2,
  Pause,
  Play,
  Plus,
  RefreshCw,
  Trash2,
} from "lucide-react";
import { useEffect, useState } from "react";
import {
  crawlSite,
  createSite,
  deleteSite,
  fetchHealth,
  getSiteDefaults,
  listPages,
  listSites,
  timeAgo,
  updateSite,
  type Site,
  type SiteInput,
} from "../api/client";
import Collapse from "../components/Collapse";

const lines = (s: string) =>
  s.split(/[\n,]/).map((x) => x.trim()).filter(Boolean);

const host = (url: string) => {
  try {
    return new URL(url).host.replace(/^www\./, "");
  } catch {
    return url;
  }
};

// ---- add a site: one line, options on request ---------------------------------------------

function AddSite() {
  const qc = useQueryClient();
  const [more, setMore] = useState(false);
  const [url, setUrl] = useState("");
  const [name, setName] = useState("");
  const [maxPages, setMaxPages] = useState(200);
  const [depth, setDepth] = useState(3);
  const [include, setInclude] = useState("");
  const [exclude, setExclude] = useState("");
  const [render, setRender] = useState<"auto" | "static" | "browser">("auto");
  const [robots, setRobots] = useState(true);
  const [delay, setDelay] = useState(500);
  const [search, setSearch] = useState("");
  const { data: defaults } = useQuery({ queryKey: ["site-defaults"], queryFn: getSiteDefaults });
  useEffect(() => {
    if (!defaults || more) return;
    setMaxPages(defaults.max_pages);
    setDepth(defaults.max_depth);
    setDelay(defaults.crawl_delay_ms);
  }, [defaults, more]);

  const add = useMutation({
    mutationFn: (body: SiteInput) => createSite(body),
    onSuccess: () => {
      setUrl("");
      setName("");
      setMore(false);
      qc.invalidateQueries({ queryKey: ["sites"] });
    },
  });

  return (
    <form
      className="site-card add-site"
      onSubmit={(e) => {
        e.preventDefault();
        add.mutate({
          root_url: url,
          name: name || undefined,
          max_pages: maxPages,
          max_depth: depth,
          include_patterns: lines(include),
          exclude_patterns: lines(exclude),
          render_mode: render,
          respect_robots: robots,
          crawl_delay_ms: delay,
          site_search_url: search.trim() || undefined,
        });
      }}
    >
      <div className="add-row">
        <Globe size={18} />
        <input aria-label="Website address" placeholder="Paste a website address, e.g. example.com"
          value={url} required onChange={(e) => setUrl(e.target.value)} />
        <button type="submit" disabled={add.isPending || !url.trim()}>
          {add.isPending ? <Loader2 size={16} className="animate-spin" /> : <Plus size={16} />}
          {add.isPending ? "Adding…" : "Add"}
        </button>
      </div>
      <div className="add-more">
        <button type="button" className="link-btn" aria-expanded={more} aria-controls="add-options"
          onClick={() => setMore((v) => !v)}>
          More options <ChevronDown size={13} className="chev" />
        </button>
      </div>
      <Collapse open={more} id="add-options">
        <div className="add-options">
          <label>Display name
            <input placeholder="Taken from the address if empty" value={name}
              onChange={(e) => setName(e.target.value)} />
          </label>
          <label>Rendering
            <select value={render} onChange={(e) => setRender(e.target.value as typeof render)}>
              <option value="auto">Automatic (browser only when needed)</option>
              <option value="static">Plain HTML only (fastest)</option>
              <option value="browser">Always use a browser</option>
            </select>
          </label>
          <label>Max pages
            <input type="number" min={1} max={5000} value={maxPages}
              onChange={(e) => setMaxPages(Number(e.target.value))} />
          </label>
          <label>Link depth
            <input type="number" min={0} max={10} value={depth}
              onChange={(e) => setDepth(Number(e.target.value))} />
          </label>
          <label>Only these paths (one per line, e.g. /blog/*)
            <textarea rows={2} value={include} onChange={(e) => setInclude(e.target.value)} />
          </label>
          <label>Skip these paths (e.g. /cart*)
            <textarea rows={2} value={exclude} onChange={(e) => setExclude(e.target.value)} />
          </label>
          <label>Site search URL (optional, helps find new pages)
            <input placeholder="https://example.com/search?q={query}" value={search}
              onChange={(e) => setSearch(e.target.value)} />
          </label>
          <label>Pause between requests (ms)
            <input type="number" min={0} max={30000} value={delay}
              onChange={(e) => setDelay(Number(e.target.value))} />
          </label>
          <label className="check">
            <input type="checkbox" checked={robots} onChange={(e) => setRobots(e.target.checked)} />
            Respect robots.txt
          </label>
        </div>
      </Collapse>
      {add.isError && <p className="form-error">{String(add.error.message)}</p>}
    </form>
  );
}

// ---- one status chip per site ---------------------------------------------------------------

function siteState(site: Site, gpuUp: boolean) {
  const j = site.last_job;
  if (!site.enabled) return { cls: "", icon: CirclePause, text: "Paused" };
  if (j?.status === "running")
    return { cls: "warn", icon: Loader2, spin: true, text: `Crawling · ${j.pages_seen} pages` };
  if (j?.status === "queued") return { cls: "warn", icon: Clock, text: "Queued" };
  if (j?.status === "failed") return { cls: "bad", icon: AlertTriangle, text: "Needs attention" };
  if (site.chunks > site.chunks_embedded) {
    const pct = Math.round((site.chunks_embedded / site.chunks) * 100);
    return gpuUp
      ? { cls: "warn", icon: Loader2, spin: true, text: `Indexing ${pct}%` }
      : { cls: "warn", icon: Clock, text: "Waiting for GPU" };
  }
  if (!j) return { cls: "", icon: Clock, text: "Not crawled" };
  return { cls: "ok", icon: CircleCheck, text: "Ready" };
}

function Pages({ siteId }: { siteId: number }) {
  const [q, setQ] = useState("");
  const { data, isLoading } = useQuery({
    queryKey: ["pages", siteId, q],
    queryFn: () => listPages(siteId, q),
  });
  return (
    <div style={{ display: "grid", gap: ".5rem" }}>
      <input className="page-filter" placeholder="Filter pages by address or title" value={q}
        onChange={(e) => setQ(e.target.value)} />
      <div className="page-table">
        {isLoading ? (
          <p style={{ padding: ".6rem" }}>Loading…</p>
        ) : (
          <table>
            <thead><tr><th>Page</th><th>Version</th><th>Indexed</th><th>Checked</th></tr></thead>
            <tbody>
              {data?.map((p) => (
                <tr key={p.id}>
                  <td><a href={p.url} target="_blank" rel="noopener noreferrer" title={p.url}>
                    {p.title || p.url}</a></td>
                  <td>{p.status === "active" ? `v${p.version}` : p.status}</td>
                  <td>{p.chunks ? `${Math.round((p.chunks_embedded / p.chunks) * 100)}%` : "–"}</td>
                  <td>{timeAgo(p.fetched_at)}</td>
                </tr>
              ))}
              {data?.length === 0 && (
                <tr><td colSpan={4} style={{ textAlign: "center" }}>No pages yet.</td></tr>
              )}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

function SiteCard({ site, gpuUp }: { site: Site; gpuUp: boolean }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [showPages, setShowPages] = useState(false);
  const [showErrors, setShowErrors] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const refresh = () => qc.invalidateQueries({ queryKey: ["sites"] });

  const crawl = useMutation({ mutationFn: () => crawlSite(site.id), onSettled: refresh });
  const toggle = useMutation({
    mutationFn: () => updateSite(site.id, { enabled: !site.enabled }),
    onSettled: refresh,
  });
  const remove = useMutation({ mutationFn: () => deleteSite(site.id), onSettled: refresh });

  const j = site.last_job;
  const busy = j?.status === "queued" || j?.status === "running";
  const pct = site.chunks ? Math.round((site.chunks_embedded / site.chunks) * 100) : 0;
  const errors = j?.errors ?? [];
  const state = siteState(site, gpuUp);
  const StateIcon = state.icon;
  const bodyId = `site-${site.id}`;

  return (
    <div className={`site-card site${site.enabled ? "" : " paused"}`}>
      <button className="site-head" aria-expanded={open} aria-controls={bodyId}
        onClick={() => setOpen((v) => !v)}>
        <span className="site-avatar" aria-hidden>{site.name.trim()[0]?.toUpperCase() ?? "?"}</span>
        <span className="site-title">
          <strong>{site.name}</strong>
          <small>{host(site.root_url)} · {site.pages} page{site.pages === 1 ? "" : "s"}</small>
        </span>
        <span className={`chip ${state.cls}`}>
          <StateIcon size={13} className={state.spin ? "animate-spin" : undefined} /> {state.text}
        </span>
        <ChevronDown size={18} className="chev" />
      </button>

      <Collapse open={open} id={bodyId}>
        <div className="site-body">
          {site.chunks > 0 && (
            <div>
              <div className={`progress${pct < 100 ? " busy" : ""}`}><span style={{ width: `${pct}%` }} /></div>
              <div className="facts" style={{ marginTop: ".35rem" }}>
                <span>{pct === 100 ? "Fully searchable" : `${pct}% searchable by meaning (keywords work already)`}</span>
              </div>
            </div>
          )}

          <div className="facts">
            {j ? (
              <span>
                {busy ? "Crawl in progress" : j.status === "failed" ? "Last crawl failed" : "Last crawl"}
                {!busy && j.finished_at ? ` ${timeAgo(j.finished_at)}` : ""} · {j.pages_seen} checked
                {j.pages_changed ? ` · ${j.pages_changed} new or changed` : ""}
                {j.pages_removed ? ` · ${j.pages_removed} removed` : ""}
              </span>
            ) : (
              <span>Not crawled yet</span>
            )}
            <span>Up to {site.max_pages} pages, {site.max_depth} links deep</span>
            {site.pages_gone > 0 && <span>{site.pages_gone} pages gone from the site</span>}
          </div>

          <div className="actions">
            <button disabled={busy || crawl.isPending} onClick={() => crawl.mutate()}>
              <RefreshCw size={14} className={busy ? "animate-spin" : undefined} />
              {busy ? "Crawling…" : "Crawl now"}
            </button>
            <button aria-expanded={showPages} onClick={() => setShowPages((v) => !v)}>
              <FileText size={14} /> {showPages ? "Hide pages" : "Pages"}
            </button>
            <button disabled={toggle.isPending} onClick={() => toggle.mutate()}>
              {site.enabled ? <Pause size={14} /> : <Play size={14} />}
              {site.enabled ? "Pause" : "Resume"}
            </button>
            {confirmDelete ? (
              <>
                <button className="danger confirm" disabled={remove.isPending} onClick={() => remove.mutate()}>
                  <Trash2 size={14} /> Delete site and its index
                </button>
                <button onClick={() => setConfirmDelete(false)}>Cancel</button>
              </>
            ) : (
              <button className="danger" onClick={() => setConfirmDelete(true)}>
                <Trash2 size={14} /> Delete
              </button>
            )}
          </div>

          {errors.length > 0 && (
            <div>
              <button className="link-btn" aria-expanded={showErrors} onClick={() => setShowErrors((v) => !v)}
                style={{ color: "var(--danger)" }}>
                {errors.length} problem{errors.length > 1 ? "s" : ""} during the last crawl
                <ChevronDown size={13} className="chev" />
              </button>
              <Collapse open={showErrors}>
                <ul className="errors" style={{ marginTop: ".35rem" }}>
                  {errors.map((e, i) => (
                    <li key={i} title={`${e.url} — ${e.error}`}>{e.error} — {e.url}</li>
                  ))}
                </ul>
              </Collapse>
            </div>
          )}

          {(crawl.isError || toggle.isError || remove.isError) && (
            <p className="form-error" style={{ margin: 0 }}>
              {String((crawl.error ?? toggle.error ?? remove.error)?.message)}
            </p>
          )}

          {showPages && <Pages siteId={site.id} />}
        </div>
      </Collapse>
    </div>
  );
}

export default function Sites() {
  const { data: health } = useQuery({
    queryKey: ["health"],
    queryFn: fetchHealth,
    refetchInterval: 15_000,
    retry: false,
  });
  const { data: sites, isLoading, error } = useQuery({
    queryKey: ["sites"],
    queryFn: listSites,
    // Poll fast while something is crawling or embedding.
    refetchInterval: (q) =>
      q.state.data?.some(
        (s) =>
          s.last_job?.status === "queued" ||
          s.last_job?.status === "running" ||
          s.chunks_embedded < s.chunks,
      )
        ? 3000
        : 15000,
  });
  const gpuUp = health?.services.embedding?.ok ?? false;

  return (
    <section className="space-y-4">
      <AddSite />
      {isLoading && <p className="text-sm text-zinc-500">Loading sites…</p>}
      {error && <p className="form-error">{String(error.message)}</p>}
      {sites?.length === 0 && (
        <p className="text-sm text-zinc-500">No sites yet. Paste a website address above to begin.</p>
      )}
      <div className="site-list">
        {sites?.map((s) => <SiteCard key={s.id} site={s} gpuUp={gpuUp} />)}
      </div>
    </section>
  );
}
