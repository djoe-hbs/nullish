import { BookOpen, CheckCircle2, Clock, Eye, EyeOff, Loader2, Mail, UserPlus } from "lucide-react";
import { useState } from "react";
import { registerAccount, resendVerification, type RegisterOut } from "../api/client";
import LegalFooter from "../components/LegalFooter";

export default function Register() {
  const [name, setName] = useState("");
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [show, setShow] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<RegisterOut | null>(null);
  const [resending, setResending] = useState(false);
  const [resendStatus, setResendStatus] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (password !== confirm) {
      setError("Passwords do not match.");
      return;
    }
    setBusy(true);
    setError(null);
    setResendStatus(null);
    try {
      const res = await registerAccount({ name, username, email, password });
      setDone(res);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function handleResend() {
    if (!done) return;
    setResending(true);
    setResendStatus(null);
    try {
      const res = await resendVerification(done.email);
      setResendStatus(res.message);
    } catch (err) {
      setResendStatus((err as Error).message);
    } finally {
      setResending(false);
    }
  }

  return (
    <div className="login-screen">
      <div className={done ? "login-stack" : "login-stack register"}>
        {done ? (
          <div className="login-card register-success">
            <div className="login-brand">
              <span className="brand-tile"><BookOpen /></span>
              <span className="wordmark">Smish</span>
            </div>
            <div className="register-badge">
              <CheckCircle2 size={20} />
              <span>{done.email_sent ? "Verification email sent" : "Registration submitted"}</span>
            </div>
            <h1>{done.email_sent ? "Verify your email" : "Pending verification & approval"}</h1>
            <p className="register-copy">
              Your account <strong>@{done.username}</strong> ({done.email}) has been created.{" "}
              {done.message}
            </p>
            <button
              type="button"
              className="resend-link-btn"
              disabled={resending}
              onClick={handleResend}
            >
              {resending ? <Loader2 size={14} className="animate-spin" /> : <Mail size={14} />}
              {resending ? "Sending email…" : "Resend verification email"}
            </button>
            {resendStatus && <p className="register-copy">{resendStatus}</p>}
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
            <h1>Create account</h1>
            <p className="login-sub">Register to request access to Smish.</p>

            <div className="register-pair">
              <label>
                Full name
                <input
                  type="text"
                  name="name"
                  autoComplete="name"
                  required
                  autoFocus
                  placeholder="Asha Rao"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </label>
              <label>
                Username
                <input
                  type="text"
                  name="username"
                  autoComplete="username"
                  required
                  minLength={3}
                  maxLength={64}
                  placeholder="asha.rao"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                />
              </label>
            </div>
            <label>
              Email address
              <input
                type="email"
                name="email"
                autoComplete="email"
                required
                placeholder="asha@example.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            </label>
            <div className="register-pair">
              <label>
                Password (8+ chars)
                <span className="pw-field">
                  <input
                    type={show ? "text" : "password"}
                    name="password"
                    autoComplete="new-password"
                    required
                    minLength={8}
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
                Confirm password
                <input
                  type={show ? "text" : "password"}
                  name="confirmPassword"
                  autoComplete="new-password"
                  required
                  minLength={8}
                  value={confirm}
                  onChange={(e) => setConfirm(e.target.value)}
                />
              </label>
            </div>

            <p className="register-note">
              <Clock size={14} />
              <span>
                New accounts require email verification and administrator approval before sign-in.
                By registering, you agree to our <a href="#terms">Terms</a> and{" "}
                <a href="#privacy">Privacy Policy</a>.
              </span>
            </p>

            {error && <p className="login-error" role="alert">{error}</p>}

            <button
              type="submit"
              className="login-submit"
              disabled={busy || !name.trim() || username.trim().length < 3 || !email.trim() || password.length < 8 || !confirm}
            >
              {busy ? <Loader2 size={17} className="animate-spin" /> : <UserPlus size={17} />}
              {busy ? "Creating account…" : "Register"}
            </button>

            <p className="login-switch">
              Already have an account? <a href="#login">Sign in</a>
            </p>
          </form>
        )}
        <LegalFooter variant="login" />
      </div>
    </div>
  );
}

