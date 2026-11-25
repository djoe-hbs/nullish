import { useQueryClient } from "@tanstack/react-query";
import { Check, KeyRound, Loader2, LogOut } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { changePassword, signOut, type User } from "../api/client";
import Collapse from "./Collapse";

export default function AccountMenu({ user }: { user: User }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [changing, setChanging] = useState(false);
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  // close on outside click / Escape
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  async function doSignOut() {
    try { await signOut(); } catch { /* signing out locally anyway */ }
    qc.removeQueries({ predicate: (q) => q.queryKey[0] !== "me" });
    qc.setQueryData(["me"], null);
  }

  async function doChange(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setMsg(null);
    try {
      await changePassword(current, next);
      setMsg({ ok: true, text: "Password changed. Other devices were signed out." });
      setCurrent("");
      setNext("");
      setChanging(false);
    } catch (err) {
      setMsg({ ok: false, text: (err as Error).message });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="account" ref={box}>
      <button className="account-btn" aria-haspopup="menu" aria-expanded={open}
        aria-label={`Account: ${user.email}`} onClick={() => setOpen((v) => !v)}>
        {user.email[0]?.toUpperCase()}
      </button>
      {open && (
        <div className="account-pop" role="menu">
          <div className="account-who">
            <small>Signed in as</small>
            <strong title={user.email}>{user.email}</strong>
          </div>
          <button role="menuitem" className="account-item" aria-expanded={changing}
            onClick={() => { setChanging((v) => !v); setMsg(null); }}>
            <KeyRound size={15} /> Change password
          </button>
          <Collapse open={changing}>
            <form className="account-form" onSubmit={doChange}>
              <input type="password" autoComplete="current-password" placeholder="Current password"
                aria-label="Current password" value={current} onChange={(e) => setCurrent(e.target.value)} />
              <input type="password" autoComplete="new-password" placeholder="New password (8+ characters)"
                aria-label="New password" value={next} onChange={(e) => setNext(e.target.value)} />
              <button type="submit" disabled={busy || !current || next.length < 8}>
                {busy ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />} Save
              </button>
            </form>
          </Collapse>
          {msg && <p className={msg.ok ? "account-ok" : "account-err"}>{msg.text}</p>}
          <button role="menuitem" className="account-item" onClick={doSignOut}>
            <LogOut size={15} /> Sign out
          </button>
        </div>
      )}
    </div>
  );
}
