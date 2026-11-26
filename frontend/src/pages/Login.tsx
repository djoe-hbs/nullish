import { useQueryClient } from "@tanstack/react-query";
import { BookOpen, Eye, EyeOff, Loader2, LogIn } from "lucide-react";
import { useState } from "react";
import { signIn } from "../api/client";

export default function Login() {
  const qc = useQueryClient();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const user = await signIn(email, password);
      // Drop data from any previous session, but keep the "me" query the app is watching
      // (clearing it too detached the app from it, so the screen never left this page).
      qc.removeQueries({ predicate: (q) => q.queryKey[0] !== "me" });
      qc.setQueryData(["me"], user);
    } catch (err) {
      setError((err as Error).message);
      setPassword("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-screen">
      <form className="login-card" onSubmit={submit}>
        <div className="login-brand">
          <span className="brand-tile"><BookOpen /></span>
          <span className="wordmark">Smish</span>
        </div>
        <h1>Welcome back</h1>
        <p className="login-sub">Sign in to ask your sites.</p>

        <label>
          Email
          <input type="email" autoComplete="username" required autoFocus value={email}
            onChange={(e) => setEmail(e.target.value)} />
        </label>
        <label>
          Password
          <span className="pw-field">
            <input type={show ? "text" : "password"} autoComplete="current-password" required
              value={password} onChange={(e) => setPassword(e.target.value)} />
            <button type="button" className="icon-btn" onClick={() => setShow((v) => !v)}
              aria-label={show ? "Hide password" : "Show password"}>
              {show ? <EyeOff size={16} /> : <Eye size={16} />}
            </button>
          </span>
        </label>

        {error && <p className="login-error" role="alert">{error}</p>}

        <button type="submit" className="login-submit" disabled={busy || !email || !password}>
          {busy ? <Loader2 size={17} className="animate-spin" /> : <LogIn size={17} />}
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
