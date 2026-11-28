import { useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  BookOpen,
  CheckCircle2,
  Eye,
  EyeOff,
  KeyRound,
  Loader2,
  Mail,
  MailCheck,
  Send,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { forgotPassword, resendVerification, resetPassword, verifyEmail } from "../api/client";
import LegalFooter from "../components/LegalFooter";

export function ForgotPassword() {
  const [identifier, setIdentifier] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sentMsg, setSentMsg] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await forgotPassword(identifier.trim());
      setSentMsg(res.message);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-screen">
      <div className="login-stack">
        {sentMsg ? (
          <div className="login-card register-success">
            <div className="login-brand">
              <span className="brand-tile"><BookOpen /></span>
              <span className="wordmark">Smish</span>
            </div>
            <div className="register-badge">
              <MailCheck size={20} />
              <span>Email sent</span>
            </div>
            <h1>Check your inbox</h1>
            <p className="register-copy">{sentMsg}</p>
            <a href="#login" className="login-submit" style={{ textDecoration: "none" }}>
              Back to sign in
            </a>
          </div>
        ) : (
          <form className="login-card" onSubmit={submit}>
            <div className="login-brand">
              <span className="brand-tile"><BookOpen /></span>
              <span className="wordmark">Smish</span>
            </div>
            <h1>Reset password or find ID</h1>
            <p className="login-sub">
              Enter your email address or username. We will send a password reset link along with
              your sign-in username.
            </p>

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

            {error && <p className="login-error" role="alert">{error}</p>}

            <button type="submit" className="login-submit" disabled={busy || !identifier.trim()}>
              {busy ? <Loader2 size={17} className="animate-spin" /> : <Send size={17} />}
              {busy ? "Sending link…" : "Send reset link"}
            </button>

            <p className="login-switch">
              Remembered your credentials? <a href="#login">Back to sign in</a>
            </p>
          </form>
        )}
        <LegalFooter variant="login" />
      </div>
    </div>
  );
}

export function ResetPassword({ token }: { token: string }) {
  const qc = useQueryClient();
  const [manualToken, setManualToken] = useState(token);
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [doneMsg, setDoneMsg] = useState<string | null>(null);

  useEffect(() => {
    if (token) setManualToken(token);
  }, [token]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (password !== confirm) {
      setError("Passwords do not match.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const res = await resetPassword(manualToken.trim(), password);
      qc.setQueryData(["me"], null);
      qc.removeQueries({ predicate: (q) => q.queryKey[0] !== "me" });
      setDoneMsg(res.message);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-screen">
      <div className="login-stack">
        {doneMsg ? (
          <div className="login-card register-success">
            <div className="login-brand">
              <span className="brand-tile"><BookOpen /></span>
              <span className="wordmark">Smish</span>
            </div>
            <div className="register-badge">
              <CheckCircle2 size={20} />
              <span>Password updated</span>
            </div>
            <h1>All set</h1>
            <p className="register-copy">{doneMsg}</p>
            <a href="#login" className="login-submit" style={{ textDecoration: "none" }}>
              Sign in
            </a>
          </div>
        ) : (
          <form className="login-card" onSubmit={submit}>
            <div className="login-brand">
              <span className="brand-tile"><BookOpen /></span>
              <span className="wordmark">Smish</span>
            </div>
            <h1>Set a new password</h1>
            <p className="login-sub">Choose a strong password (at least 8 characters).</p>

            {!token && (
              <label>
                Reset token
                <input
                  type="text"
                  required
                  placeholder="Paste reset token from your email"
                  value={manualToken}
                  onChange={(e) => setManualToken(e.target.value)}
                />
              </label>
            )}

            <label>
              New password (8+ chars)
              <span className="pw-field">
                <input
                  type={show ? "text" : "password"}
                  name="newPassword"
                  autoComplete="new-password"
                  required
                  minLength={8}
                  autoFocus={Boolean(token)}
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

            <label>
              Confirm new password
              <input
                type={show ? "text" : "password"}
                name="confirmNewPassword"
                autoComplete="new-password"
                required
                minLength={8}
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
              />
            </label>

            {error && <p className="login-error" role="alert">{error}</p>}

            <button
              type="submit"
              className="login-submit"
              disabled={busy || !manualToken.trim() || password.length < 8 || !confirm}
            >
              {busy ? <Loader2 size={17} className="animate-spin" /> : <KeyRound size={17} />}
              {busy ? "Updating password…" : "Update password"}
            </button>

            <p className="login-switch">
              Link expired? <a href="#forgot-password">Request a new reset link</a>
            </p>
          </form>
        )}
        <LegalFooter variant="login" />
      </div>
    </div>
  );
}

const _attemptedVerifyTokens = new Set<string>();

export function VerifyEmail({ token }: { token: string }) {
  const qc = useQueryClient();
  const [status, setStatus] = useState<"verifying" | "success" | "error">(
    token ? "verifying" : "error",
  );
  const [message, setMessage] = useState<string>(
    token ? "Verifying your email address…" : "Verification token is missing from this link.",
  );
  const [identifier, setIdentifier] = useState("");
  const [resending, setResending] = useState(false);
  const [resendMsg, setResendMsg] = useState<string | null>(null);
  const [resendErr, setResendErr] = useState<string | null>(null);
  const lastTokenRef = useRef<string>("");

  useEffect(() => {
    if (!token) {
      setStatus("error");
      setMessage("Verification token is missing from this link.");
      return;
    }
    if (lastTokenRef.current === token || _attemptedVerifyTokens.has(token)) {
      return;
    }
    lastTokenRef.current = token;
    _attemptedVerifyTokens.add(token);
    setStatus("verifying");
    setMessage("Verifying your email address…");
    verifyEmail(token)
      .then((res) => {
        setStatus("success");
        setMessage(res.message);
        qc.invalidateQueries({ queryKey: ["me"] });
      })
      .catch((err) => {
        setStatus("error");
        setMessage((err as Error).message);
      });
  }, [token, qc]);

  async function handleResend(e: React.FormEvent) {
    e.preventDefault();
    setResending(true);
    setResendErr(null);
    setResendMsg(null);
    try {
      const res = await resendVerification(identifier.trim());
      setResendMsg(res.message);
    } catch (err) {
      setResendErr((err as Error).message);
    } finally {
      setResending(false);
    }
  }

  return (
    <div className="login-screen">
      <div className="login-stack">
        <div className="login-card register-success">
          <div className="login-brand">
            <span className="brand-tile"><BookOpen /></span>
            <span className="wordmark">Smish</span>
          </div>

          {status === "verifying" && (
            <>
              <div className="register-badge">
                <Loader2 size={20} className="animate-spin" />
                <span>Verifying email</span>
              </div>
              <h1>Confirming your email…</h1>
              <p className="register-copy">{message}</p>
            </>
          )}

          {status === "success" && (
            <>
              <div className="register-badge">
                <MailCheck size={20} />
                <span>Email verified</span>
              </div>
              <h1>Email confirmed</h1>
              <p className="register-copy">{message}</p>
              <a href="#login" className="login-submit" style={{ textDecoration: "none" }}>
                Go to sign in
              </a>
            </>
          )}

          {status === "error" && (
            <>
              <div className="register-badge warn">
                <AlertCircle size={20} />
                <span>Verification failed</span>
              </div>
              <h1>Link invalid or expired</h1>
              <p className="register-copy">{message}</p>

              <form onSubmit={handleResend} className="auth-inline-form">
                <label>
                  Resend verification email
                  <input
                    type="text"
                    required
                    placeholder="Enter your email or username"
                    value={identifier}
                    onChange={(e) => setIdentifier(e.target.value)}
                  />
                </label>
                <button
                  type="submit"
                  className="login-submit"
                  disabled={resending || !identifier.trim()}
                >
                  {resending ? <Loader2 size={16} className="animate-spin" /> : <Mail size={16} />}
                  {resending ? "Sending…" : "Send new verification link"}
                </button>
              </form>
              {resendMsg && <p className="register-copy">{resendMsg}</p>}
              {resendErr && <p className="login-error" role="alert">{resendErr}</p>}
              <p className="login-switch">
                <a href="#login">Back to sign in</a>
              </p>
            </>
          )}
        </div>
        <LegalFooter variant="login" />
      </div>
    </div>
  );
}

