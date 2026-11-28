import { useQueryClient } from "@tanstack/react-query";
import { BookOpen, Eye, EyeOff, Loader2, LogIn, Mail } from "lucide-react";
import { useState } from "react";
import { resendVerification, signIn } from "../api/client";
import LegalFooter from "../components/LegalFooter";

export default function Login() {
  const qc = useQueryClient();
  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [resending, setResending] = useState(false);
  const [resentMsg, setResentMsg] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setResentMsg(null);
    try {
      const user = await signIn(identifier, password);
      qc.removeQueries({ predicate: (q) => q.queryKey[0] !== "me" });
      qc.setQueryData(["me"], user);
    } catch (err) {
      setError((err as Error).message);
      setPassword("");
    } finally {
      setBusy(false);
    }
  }

  async function handleResend() {
    if (!identifier.trim()) return;
    setResending(true);
    setResentMsg(null);
    try {
      const res = await resendVerification(identifier.trim());
      setResentMsg(res.message);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setResending(false);
    }
  }

  const needsEmailVerify = Boolean(error && error.toLowerCase().includes("verify your email"));

  return (
    <div className="login-screen">
      <div className="login-stack">
        <form className="login-card" onSubmit={submit}>
          <div className="login-brand">
            <span className="brand-tile"><BookOpen /></span>
            <span className="wordmark">Smish</span>
          </div>
          <h1>Welcome back</h1>
          <p className="login-sub">Sign in with your email or username.</p>

          <label>
            Email or username
            <input
              type="text"
              name="identifier"
              autoComplete="username"
              required
              autoFocus
              placeholder="you@example.com or username"
              value={identifier}
              onChange={(e) => setIdentifier(e.target.value)}
            />
          </label>
          <label>
            <span className="login-label-row">
              <span>Password</span>
              <a href="#forgot-password" className="login-forgot">Forgot ID or password?</a>
            </span>
            <span className="pw-field">
              <input
                type={show ? "text" : "password"}
                name="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
              <button
                type="button"
                className="icon-btn"
                onClick={() => setShow((v) => !v)}
                aria-label={show ? "Hide password" : "Show password"}
              >
                {show ? <EyeOff size={16} /> : <Eye size={16} />}
              </button>
            </span>
          </label>

          {error && (
            <div className="login-error-box">
              <p className="login-error" role="alert">{error}</p>
              {needsEmailVerify && (
                <button
                  type="button"
                  className="resend-link-btn"
                  disabled={resending}
                  onClick={handleResend}
                >
                  {resending ? <Loader2 size={14} className="animate-spin" /> : <Mail size={14} />}
                  {resending ? "Sending link…" : "Resend verification email"}
                </button>
              )}
            </div>
          )}

          {resentMsg && <p className="register-note">{resentMsg}</p>}

          <button type="submit" className="login-submit" disabled={busy || !identifier.trim() || !password}>
            {busy ? <Loader2 size={17} className="animate-spin" /> : <LogIn size={17} />}
            {busy ? "Signing in…" : "Sign in"}
          </button>

          <p className="login-switch">
            New user? <a href="#register">Create an account</a>
          </p>
        </form>
        <LegalFooter variant="login" />
      </div>
    </div>
  );
}
