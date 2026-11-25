import {
  ArrowUpRight,
  BookMarked,
  Check,
  ChevronDown,
  Copy,
  Info,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import { Fragment, lazy, Suspense, useState } from "react";
import { timeAgo, type AskDone, type ChatDone, type ChatTurn, type Source } from "../api/client";
import Collapse from "./Collapse";

export type Mode = "sites" | "model";

export type Msg = ChatTurn & {
  mode?: Mode;
  error?: string;
  meta?: ChatDone | AskDone;
  pending?: boolean;
  status?: string;
  sources?: Source[];
};

const AnswerText = lazy(() => import("../AnswerText"));

function shortUrl(url: string) {
  try {
    const u = new URL(url);
    return u.host.replace(/^www\./, "") + (u.pathname === "/" ? "" : u.pathname.replace(/\/$/, ""));
  } catch {
    return url;
  }
}

/** One short freshness phrase per source. */
function freshness(s: Source): { text: string; warn: boolean } {
  switch (s.live) {
    case "verified":
      return { text: `Live · ${timeAgo(s.verified_at)}`, warn: false };
    case "updated":
      return { text: `Updated live · ${timeAgo(s.verified_at)}`, warn: false };
    case "new":
      return { text: `New page · ${timeAgo(s.verified_at)}`, warn: false };
    case "unverified":
      return { text: `Not verified · from ${timeAgo(s.fetched_at)}`, warn: true };
    default:
      return { text: `Indexed · ${timeAgo(s.fetched_at)}`, warn: false };
  }
}

function SourceItem({ s, snippets, muted }: { s: Source; snippets: boolean; muted?: boolean }) {
  const f = freshness(s);
  return (
    <a className="src" href={s.url} target="_blank" rel="noopener noreferrer"
      style={muted ? { opacity: 0.7 } : undefined} title={s.url}>
      <span className="src-num">{s.n}</span>
      <span className="src-title">{s.title || shortUrl(s.url)}</span>
      <ArrowUpRight size={14} />
      <span className="src-meta">
        {s.site} · {shortUrl(s.url)} · <span className={`fresh${f.warn ? " warn" : ""}`}>{f.text}</span>
      </span>
      {snippets && <span className="src-snippet">{s.snippet}</span>}
    </a>
  );
}

function SourcesPanel({ sources, cited }: { sources: Source[]; cited: number[] | null }) {
  const [snippets, setSnippets] = useState(false);
  const [showOthers, setShowOthers] = useState(false);
  const isCited = (n: number) => cited === null || cited.length === 0 || cited.includes(n);
  const used = sources.filter((s) => isCited(s.n));
  const others = sources.filter((s) => !isCited(s.n));
  return (
    <div className="panel">
      <div className="panel-row">
        <span>{cited?.length ? "Pages this answer is based on" : "Pages that were checked"}</span>
        <button className="link-btn" onClick={() => setSnippets((v) => !v)}>
          {snippets ? "Hide excerpts" : "Show excerpts"}
        </button>
      </div>
      <div className="src-list">
        {used.map((s) => <SourceItem key={s.n} s={s} snippets={snippets} />)}
      </div>
      {others.length > 0 && (
        <>
          <button className="link-btn" style={{ marginTop: ".4rem" }} aria-expanded={showOthers}
            onClick={() => setShowOthers((v) => !v)}>
            {others.length} more checked, not used <ChevronDown size={13} className="chev" />
          </button>
          <Collapse open={showOthers}>
            <div className="src-list" style={{ paddingTop: ".3rem" }}>
              {others.map((s) => <SourceItem key={s.n} s={s} snippets={snippets} muted />)}
            </div>
          </Collapse>
        </>
      )}
    </div>
  );
}

function DetailsPanel({ meta }: { meta: ChatDone | AskDone }) {
  const ask = "cited" in meta ? meta : null;
  const live = ask?.live;
  const rows: [string, string][] = [];
  if (live) {
    const checked = (live.checked ?? 0) + (live.discovered ?? 0);
    const parts = [
      live.recently_verified ? `${live.recently_verified} confirmed moments ago` : null,
      live.changed ? `${live.changed} updated` : null,
      live.new ? `${live.new} new` : null,
      live.gone ? `${live.gone} removed` : null,
      live.error ? `${live.error} unreachable` : null,
    ].filter(Boolean);
    rows.push(["Live check", `${checked} pages${parts.length ? ` · ${parts.join(" · ")}` : ""}`]);
    if (live.live_ms != null) rows.push(["Checking took", `${(live.live_ms / 1000).toFixed(1)} s`]);
  }
  if (meta.first_token_ms != null) rows.push(["First words", `${(meta.first_token_ms / 1000).toFixed(1)} s`]);
  rows.push(["Total", `${(meta.latency_ms / 1000).toFixed(1)} s`]);
  if (meta.tokens != null) rows.push(["Length", `${meta.tokens} tokens`]);
  if (meta.finish_reason === "length") rows.push(["Note", "Cut off at the length limit"]);
  if (ask?.invalid_citations.length) rows.push(["Cleaned", `${ask.invalid_citations.length} unsupported reference(s) removed`]);
  return (
    <div className="panel">
      <dl className="details-list">
        {rows.map(([k, v]) => <Fragment key={k}><dt>{k}</dt><dd>{v}</dd></Fragment>)}
      </dl>
    </div>
  );
}

function Footer({ m }: { m: Msg }) {
  const [open, setOpen] = useState<"sources" | "details" | null>(null);
  const [copied, setCopied] = useState(false);
  const grounded = m.mode === "sites";
  const sources = m.sources ?? [];
  const cited = m.meta && "cited" in m.meta ? m.meta.cited : null;
  const used = cited?.length ? sources.filter((s) => cited.includes(s.n)) : [];
  const unverified = used.some((s) => s.live === "unverified");
  // "Verified live" only when every cited page was actually re-checked on the live site
  // (answers about the sites as a whole cite catalog entries, which aren't live-checked).
  const allLive = used.length > 0 && used.every((s) => ["verified", "updated", "new"].includes(s.live));
  const notFound = !!(m.meta && "not_found" in m.meta && m.meta.not_found);
  const toggle = (p: "sources" | "details") => setOpen((o) => (o === p ? null : p));

  async function copy() {
    try {
      await navigator.clipboard.writeText(m.content.replace(/\[(\d{1,2})\]/g, ""));
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch { /* clipboard blocked: nothing to do */ }
  }

  return (
    <>
      <div className="msg-footer">
        {grounded && sources.length > 0 && (
          <button className="chip" aria-expanded={open === "sources"} onClick={() => toggle("sources")}>
            <BookMarked size={14} />
            {used.length ? `${used.length} source${used.length > 1 ? "s" : ""}` : `${sources.length} pages checked`}
            <ChevronDown size={13} className="chev" />
          </button>
        )}
        {grounded && used.length > 0 && !notFound && (unverified || allLive) && (
          unverified
            ? <span className="chip warn" title="Some pages could not be re-checked on the live site">
                <ShieldAlert size={14} /> Partly unverified</span>
            : <span className="chip ok" title="Every page used was confirmed on the live site">
                <ShieldCheck size={14} /> Verified live</span>
        )}
        {!grounded && <span className="chip" title="Not based on your sites"><Sparkles size={13} /> Model only</span>}
        {m.content && (
          <button className="icon-btn" aria-label={copied ? "Copied" : "Copy answer"} title="Copy" onClick={copy}>
            {copied ? <Check size={15} /> : <Copy size={15} />}
          </button>
        )}
        {m.meta && (
          <button className="icon-btn" aria-label="Answer details" title="Details"
            aria-expanded={open === "details"} onClick={() => toggle("details")}>
            <Info size={15} />
          </button>
        )}
      </div>
      <Collapse open={open === "sources"}>
        {open === "sources" && <SourcesPanel sources={sources} cited={cited} />}
      </Collapse>
      <Collapse open={open === "details"}>
        {open === "details" && m.meta && <DetailsPanel meta={m.meta} />}
      </Collapse>
    </>
  );
}

export function Bubble({ m }: { m: Msg }) {
  if (m.role === "user") {
    return <div className="msg user"><div className="msg-user">{m.content}</div></div>;
  }
  const grounded = m.mode === "sites";
  return (
    <div className="msg">
      <div className="msg-assistant">
        <div className="answer-card">
          {m.pending && !m.content && (
            <div className="thinking" role="status">
              <span className="dots" aria-hidden><i /><i /><i /></span>
              {m.status ?? "Thinking…"}
            </div>
          )}
          {m.content && (
            <Suspense fallback={<div className="answer">{m.content}</div>}>
              <AnswerText grounded={grounded} sources={m.sources ?? []} content={m.content} />
            </Suspense>
          )}
          {m.pending && m.content && <span className="caret" aria-hidden />}
          {m.error && <p className="msg-error">{m.error}</p>}
        </div>
        {!m.pending && (m.meta || (m.sources?.length ?? 0) > 0) && <Footer m={m} />}
      </div>
    </div>
  );
}
