import { useQueryClient } from "@tanstack/react-query";
import { LogOut, Settings as SettingsIcon } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { signOut, type User } from "../api/client";

export default function AccountMenu({ user }: { user: User }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
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

  const label = user.name || user.username || user.email;

  return (
    <div className="account" ref={box}>
      <button
        className="account-btn"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`Account: ${label}`}
        onClick={() => setOpen((v) => !v)}
      >
        {label[0]?.toUpperCase() ?? "?"}
      </button>
      {open && (
        <div className="account-pop" role="menu">
          <div className="account-who">
            <div className="account-who-row">
              <strong title={label}>{label}</strong>
              <span className={`role-badge ${user.role}`}>
                {user.role === "admin" ? "Admin" : "User"}
              </span>
            </div>
            {user.username && <small>@{user.username}</small>}
            <small title={user.email}>{user.email}</small>
          </div>
          <a
            href="#settings"
            role="menuitem"
            className="account-item"
            onClick={() => setOpen(false)}
          >
            <SettingsIcon size={15} /> Settings
          </a>
          <button role="menuitem" className="account-item" onClick={doSignOut}>
            <LogOut size={15} /> Sign out
          </button>
        </div>
      )}
    </div>
  );
}
