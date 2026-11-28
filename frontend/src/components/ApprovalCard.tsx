import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Clock, DatabaseZap, ShieldAlert, X } from "lucide-react";
import { useState } from "react";
import {
  approveChange, approveLocalChange, getChange, rejectChange,
  type PendingChange, type ResultTable, type User,
} from "../api/client";
import { getLocalDbs, toLocalSourcePayload } from "../storage/localStore";

function cell(v: unknown): string {
  if (v === null || v === undefined) return "NULL";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

/** A small, scrollable result table. */
export function ResultGrid({ table, max = 20, truncated = false, highlight }: {
  table: ResultTable; max?: number; truncated?: boolean; highlight?: ResultTable | null;
}) {
  const rows = table.rows.slice(0, max);
  const more = table.rows.length - rows.length;
  return (
    <div className="result-grid">
      <table>
        <thead><tr>{table.columns.map((c) => <th key={c}>{c}</th>)}</tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>{r.map((v, j) => {
              const changed = highlight && cell(highlight.rows[i]?.[j]) !== cell(v);
              return <td key={j} className={changed ? "changed" : undefined}>{cell(v)}</td>;
            })}</tr>
          ))}
        </tbody>
      </table>
      {(more > 0 || truncated) && (
        <p className="result-more">{more > 0 ? `${more} more row(s) not shown` : "More rows exist"}</p>
      )}
    </div>
  );
}

const KIND_TEXT = { write: "Data change", ddl: "Schema change" } as const;

/** A change Smish prepared that waits for the user. Its live status comes from the server, so a
 *  reopened conversation shows whether it was applied, rejected or expired meanwhile. */
export default function ApprovalCard({ change, user }: { change: PendingChange; user?: User }) {
  const qc = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const [localState, setLocalState] = useState<PendingChange | null>(null);
  const isLocal = Boolean(change.local);
  const { data } = useQuery({
    queryKey: ["change", change.id],
    queryFn: () => getChange(change.id),
    initialData: { ...change, status: change.status ?? "pending" },
    initialDataUpdatedAt: 0,
    enabled: !isLocal,
    staleTime: 10_000,
    retry: false,
  });
  const c = isLocal ? (localState ?? change) : (data ?? change);
  const decide = useMutation({
    mutationFn: async (ok: boolean): Promise<PendingChange> => {
      if (isLocal) {
        if (!ok) return { ...c, status: "rejected" };
        if (!user) throw new Error("Missing user context for browser-local database approval.");
        const localDb = getLocalDbs(user.id).find((d) => d.id === change.local_source_id);
        if (!localDb) {
          throw new Error("Local database configuration not found in browser storage.");
        }
        const res = await approveLocalChange({
          source: toLocalSourcePayload(localDb),
          sql: c.sql,
          kind: c.kind,
        });
        return { ...c, status: res.status, result: res.result };
      }
      return ok ? approveChange(change.id) : rejectChange(change.id);
    },
    onSuccess: (res) => {
      setError(null);
      if (isLocal) {
        setLocalState(res);
      } else {
        qc.setQueryData(["change", change.id], res);
      }
    },
    onError: (e: Error) => {
      setError(e.message);
      if (!isLocal) qc.invalidateQueries({ queryKey: ["change", change.id] });
    },
  });
  const status = c.status ?? "pending";
  const expired = status === "pending" && new Date(c.expires_at).getTime() < Date.now();
  const shown = expired ? "expired" : status;
  const p = c.preview;
  const rows = p.rowcount;

  return (
    <div className={`approval ${shown}`} role="group" aria-label="Change waiting for approval">
      <div className="approval-head">
        <span className="approval-icon"><DatabaseZap size={17} /></span>
        <div>
          <strong>{shown === "pending" ? "Approve this change?" : KIND_TEXT[c.kind]}</strong>
          <span className="approval-sub">
            {c.source} · {KIND_TEXT[c.kind]}
            {rows != null && ` · affects ${rows} row${rows === 1 ? "" : "s"}`}
          </span>
        </div>
        <span className={`approval-status ${shown}`}>
          {shown === "pending" && <><Clock size={13} /> Waiting for you</>}
          {shown === "applied" && <><Check size={13} /> Applied{c.result?.rowcount != null &&
            ` · ${c.result.rowcount} row${c.result.rowcount === 1 ? "" : "s"}`}</>}
          {shown === "rejected" && <><X size={13} /> Rejected</>}
          {shown === "expired" && <><Clock size={13} /> Expired</>}
          {shown === "failed" && <><ShieldAlert size={13} /> Failed</>}
        </span>
      </div>
      {c.reason && <p className="approval-reason">{c.reason}</p>}
      <pre className="query-sql">{c.sql}</pre>
      {p.note && <p className="approval-note">{p.note}</p>}
      {p.before && p.after && p.before.rows.length > 0 && (
        <div className="approval-preview">
          <div><span className="preview-label">Before</span><ResultGrid table={p.before} max={5} /></div>
          <div><span className="preview-label">After</span>
            <ResultGrid table={p.after} max={5} highlight={p.before} /></div>
        </div>
      )}
      {p.before && p.before.rows.length > 0 && p.after && p.after.rows.length === 0 && (
        <p className="approval-note">These rows will be deleted.</p>
      )}
      {c.kind === "ddl" && <p className="approval-note warn">
        Schema changes can't be undone from Smish. Check the statement carefully.</p>}
      {shown === "failed" && c.result?.error && <p className="query-error">{c.result.error}</p>}
      {error && <p className="query-error">{error}</p>}
      {shown === "pending" && (
        <div className="approval-actions">
          <button className="approve" disabled={decide.isPending} onClick={() => decide.mutate(true)}>
            <Check size={15} /> {decide.isPending ? "Working…" : "Approve"}
          </button>
          <button className="reject" disabled={decide.isPending} onClick={() => decide.mutate(false)}>
            <X size={15} /> Reject
          </button>
          <span className="approval-expiry">
            Expires {new Date(c.expires_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
          </span>
        </div>
      )}
      {shown === "expired" && <p className="approval-note">Ask again to get a fresh preview.</p>}
    </div>
  );
}
