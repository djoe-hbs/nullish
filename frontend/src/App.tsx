import {
  Activity,
  BookOpen,
  LayoutDashboard,
  LibraryBig,
  MessageCircle,
  Moon,
  Settings as SettingsIcon,
  Sun,
  Users as UsersIcon,
} from "lucide-react";
import { MotionConfig, motion } from "motion/react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { ApiError, getMe, SIGNED_OUT_EVENT, type User } from "./api/client";
import AccountMenu from "./components/AccountMenu";
import LegalFooter from "./components/LegalFooter";
import { type LegalDocId } from "./legal/legalContent";
import { useLegal } from "./legal/useLegal";
import { ForgotPassword, ResetPassword, VerifyEmail } from "./pages/AuthRecovery";
import Chat from "./pages/Chat";
import Dashboard from "./pages/Dashboard";
import Legal from "./pages/Legal";
import Login from "./pages/Login";
import { CHAT_TITLE_EVENT, getChatTitle } from "./pageTitle";
import Register from "./pages/Register";
import Settings from "./pages/Settings";
import Sources from "./pages/Sources";
import Status from "./pages/Status";
import StorageSetup from "./pages/StorageSetup";
import Users from "./pages/Users";

type AppTab = "dashboard" | "chat" | "sites" | "status" | "users" | "settings";
type PublicAuthRoute = "register" | "forgot-password" | "reset-password" | "verify-email";
type Route = AppTab | LegalDocId | PublicAuthRoute;

const ADMIN_ONLY_TABS = new Set<Route>(["dashboard", "status", "users"]);

const isPublicAuth = (r: Route): r is PublicAuthRoute =>
  r === "register" || r === "forgot-password" || r === "reset-password" || r === "verify-email";

const isLegal = (r: Route): r is LegalDocId =>
  r === "privacy" || r === "terms" || r === "disclaimer";

const parseHash = (): Route | null => {
  const raw = window.location.hash.slice(1);
  const qIdx = raw.indexOf("?");
  const h = (qIdx >= 0 ? raw.slice(0, qIdx) : raw).replace(/^\/+/, "");
  if (
    h === "dashboard" ||
    h === "chat" ||
    h === "sites" ||
    h === "status" ||
    h === "users" ||
    h === "settings" ||
    h === "register" ||
    h === "forgot-password" ||
    h === "reset-password" ||
    h === "verify-email" ||
    h === "privacy" ||
    h === "terms" ||
    h === "disclaimer"
  ) {
    return h;
  }
  return null;
};

const extractHashToken = (): string => {
  const hash = window.location.hash;
  const qIdx = hash.indexOf("?");
  if (qIdx >= 0) {
    const t = new URLSearchParams(hash.slice(qIdx + 1)).get("token");
    if (t) return t;
  }
  return new URLSearchParams(window.location.search).get("token") ?? "";
};

async function whoAmI(): Promise<User | null> {
  try {
    return await getMe();
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) return null; // not signed in
    throw e;
  }
}

export default function App() {
  const qc = useQueryClient();
  const me = useQuery({ queryKey: ["me"], queryFn: whoAmI, retry: 1, staleTime: Infinity });
  // Any API call answering 401 (session expired / signed out elsewhere) -> sign-in page.
  useEffect(() => {
    const onSignedOut = () => qc.setQueryData(["me"], null);
    window.addEventListener(SIGNED_OUT_EVENT, onSignedOut);
    return () => window.removeEventListener(SIGNED_OUT_EVENT, onSignedOut);
  }, [qc]);

  const signedIn = Boolean(me.data);
  const isAdmin = me.data?.role === "admin";
  const defaultTab: AppTab = isAdmin ? "dashboard" : "chat";

  const [hashRoute, setHashRoute] = useState<Route | null>(parseHash);
  const [hashToken, setHashToken] = useState<string>(extractHashToken);
  const [lastAppTab, setLastAppTab] = useState<AppTab>("chat");
  const [lastPublicPage, setLastPublicPage] = useState<"login" | "register" | "forgot-password">(
    () => {
      const r = parseHash();
      return r === "register" || r === "forgot-password" ? r : "login";
    },
  );

  const resolvedRoute: Route = (() => {
    const r = hashRoute ?? defaultTab;
    if (signedIn && isPublicAuth(r) && r !== "reset-password" && r !== "verify-email") {
      return defaultTab;
    }
    if (!isAdmin && ADMIN_ONLY_TABS.has(r)) return "chat";
    return r;
  })();

  // Light by default; the dark theme is used only after the user switches to it.
  const [dark, setDark] = useState(() => {
    try { return localStorage.getItem("nullish.theme") === "dark"; }
    catch { return false; }
  });
  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
    try { localStorage.setItem("nullish.theme", dark ? "dark" : "light"); } catch { /* session only */ }
  }, [dark]);

  useEffect(() => {
    const onHash = () => {
      const next = parseHash();
      setHashRoute(next);
      setHashToken(extractHashToken());
      if (next === "register" || next === "forgot-password") {
        setLastPublicPage(next);
      } else if (next === null && window.location.hash === "#login") {
        setLastPublicPage("login");
      }
      if (next && !isPublicAuth(next) && !isLegal(next)) {
        setLastAppTab(next);
      }
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const [chatTitle, setChatTitleState] = useState(getChatTitle);
  const { docs: legalDocs } = useLegal();
  useEffect(() => {
    const sync = () => setChatTitleState(getChatTitle);
    window.addEventListener(CHAT_TITLE_EVENT, sync);
    return () => window.removeEventListener(CHAT_TITLE_EVENT, sync);
  }, []);

  useEffect(() => {
    const needsSetup = Boolean(me.data && !me.data.storage_mode);
    const page = hashRoute === "verify-email"
      ? "Verify email"
      : hashRoute === "reset-password"
        ? "Reset password"
        : isLegal(resolvedRoute)
          ? legalDocs[resolvedRoute].title
          : !signedIn
            ? hashRoute === "register"
              ? "Create account"
              : hashRoute === "forgot-password"
                ? "Forgot password"
                : "Sign in"
            : needsSetup
              ? "Choose data storage"
              : resolvedRoute === "dashboard"
                ? "Dashboard"
                : resolvedRoute === "chat"
                  ? chatTitle || "New chat"
                  : resolvedRoute === "sites"
                    ? "Sources"
                    : resolvedRoute === "status"
                      ? "Status"
                      : resolvedRoute === "users"
                        ? "Users"
                        : "Settings";
    document.title = `${page} · Smish`;
  }, [signedIn, me.data, resolvedRoute, hashRoute, chatTitle, legalDocs]);

  const adminTabs = [
    { id: "dashboard", label: "Dashboard", icon: LayoutDashboard },
    { id: "chat", label: "Chat", icon: MessageCircle },
    { id: "sites", label: "Sources", icon: LibraryBig },
    { id: "status", label: "Status", icon: Activity },
    { id: "users", label: "Users", icon: UsersIcon },
    { id: "settings", label: "Settings", icon: SettingsIcon },
  ] as const;

  const userTabs = [
    { id: "chat", label: "Chat", icon: MessageCircle },
    { id: "sites", label: "Sources", icon: LibraryBig },
    { id: "settings", label: "Settings", icon: SettingsIcon },
  ] as const;

  const tabs = isAdmin ? adminTabs : userTabs;

  const themeToggle = (
    <button
      className="theme-switch"
      aria-label="Dark theme"
      aria-pressed={dark}
      onClick={() => setDark(!dark)}
    >
      {dark ? <Moon size={18} /> : <Sun size={18} />}
      <span>{dark ? "Dark" : "Light"}</span>
    </button>
  );

  if (!signedIn && hashRoute && isLegal(hashRoute)) {
    const pubHref = `#${lastPublicPage}`;
    const pubLabel = lastPublicPage === "register" ? "Back to registration" : "Back to sign in";
    return (
      <MotionConfig reducedMotion="user">
        <div className="shell">
          <header className="mast">
            <a href="#login" className="wordmark" aria-label="Smish sign in">
              <span className="brand-tile"><BookOpen /></span>Smish
            </a>
            <div />
            <div className="mast-right">
              {themeToggle}
              <a href="#login" className="legal-signin-link">Sign in</a>
            </div>
          </header>
          <main>
            <div className="page-scroll">
              <motion.div
                key={hashRoute}
                className="library-wrap"
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.2 }}
              >
                <Legal docId={hashRoute} backHref={pubHref} backLabel={pubLabel} />
              </motion.div>
            </div>
          </main>
          <LegalFooter active={hashRoute} />
        </div>
      </MotionConfig>
    );
  }

  if (hashRoute === "verify-email") {
    return (
      <MotionConfig reducedMotion="user">
        <VerifyEmail token={hashToken} />
      </MotionConfig>
    );
  }
  if (hashRoute === "reset-password") {
    return (
      <MotionConfig reducedMotion="user">
        <ResetPassword token={hashToken} />
      </MotionConfig>
    );
  }
  if (me.isLoading) return <div className="shell" aria-busy="true" />;
  if (me.isError)
    return (
      <div className="login-screen">
        <p className="login-error">
          The Smish API isn't reachable right now. Please try again in a moment.
        </p>
      </div>
    );
  if (!me.data) {
    return (
      <MotionConfig reducedMotion="user">
        {hashRoute === "register" ? (
          <Register />
        ) : hashRoute === "forgot-password" ? (
          <ForgotPassword />
        ) : (
          <Login />
        )}
      </MotionConfig>
    );
  }

  // Require the user to pick local browser vs secure DB storage before entering the workspace.
  if (!me.data.storage_mode) {
    return (
      <MotionConfig reducedMotion="user">
        <div className="shell">
          <header className="mast">
            <a href="#setup" className="wordmark" aria-label="Smish home">
              <span className="brand-tile"><BookOpen /></span>Smish
            </a>
            <div />
            <div className="mast-right">
              {themeToggle}
              <AccountMenu user={me.data} />
            </div>
          </header>
          <main>
            <div className="page-scroll">
              <motion.div
                key={isLegal(resolvedRoute) ? resolvedRoute : "storage-setup"}
                className="library-wrap"
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.2 }}
              >
                {isLegal(resolvedRoute) ? (
                  <Legal
                    docId={resolvedRoute}
                    backHref="#setup"
                    backLabel="Back to storage setup"
                  />
                ) : (
                  <StorageSetup user={me.data} />
                )}
              </motion.div>
            </div>
          </main>
          <LegalFooter active={isLegal(resolvedRoute) ? resolvedRoute : null} />
        </div>
      </MotionConfig>
    );
  }

  const backTab = !isAdmin && ADMIN_ONLY_TABS.has(lastAppTab) ? "chat" : lastAppTab;
  const backLabelMap: Record<AppTab, string> = {
    dashboard: "Back to Dashboard",
    chat: "Back to Chat",
    sites: "Back to Sources",
    status: "Back to Status",
    users: "Back to Users",
    settings: "Back to Settings",
  };

  return (
    <MotionConfig reducedMotion="user">
      <div className="shell">
        <header className="mast">
          <a href={`#${defaultTab}`} className="wordmark" aria-label="Smish home">
            <span className="brand-tile"><BookOpen /></span>Smish
          </a>
          <nav aria-label="Main navigation" className="nav-dock">
            {tabs.map(({ id, label, icon: Icon }) => (
              <a
                href={`#${id}`}
                key={id}
                aria-current={resolvedRoute === id ? "page" : undefined}
              >
                {resolvedRoute === id && (
                  <motion.span
                    layoutId="active-tab"
                    className="active-tab"
                    transition={{ type: "spring", stiffness: 460, damping: 36 }}
                  />
                )}
                <Icon size={17} />
                <span>{label}</span>
              </a>
            ))}
          </nav>
          <div className="mast-right">
            {themeToggle}
            <AccountMenu user={me.data} />
          </div>
        </header>
        <main>
          <div className="h-full" hidden={resolvedRoute !== "chat"}>
            <Chat isAdmin={isAdmin} user={me.data} />
          </div>
          {resolvedRoute !== "chat" && (
            <div className="page-scroll">
              <motion.div
                key={resolvedRoute}
                className="library-wrap"
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.2 }}
              >
                {resolvedRoute === "dashboard" ? (
                  <Dashboard />
                ) : resolvedRoute === "sites" ? (
                  <Sources user={me.data} />
                ) : resolvedRoute === "status" ? (
                  <Status />
                ) : resolvedRoute === "users" ? (
                  <Users />
                ) : resolvedRoute === "settings" ? (
                  <Settings user={me.data} dark={dark} onToggleTheme={() => setDark(!dark)} />
                ) : isLegal(resolvedRoute) ? (
                  <Legal
                    docId={resolvedRoute}
                    backHref={`#${backTab}`}
                    backLabel={backLabelMap[backTab]}
                    canEdit={isAdmin}
                  />
                ) : null}
              </motion.div>
            </div>
          )}
        </main>
        <LegalFooter active={isLegal(resolvedRoute) ? resolvedRoute : null} />
      </div>
    </MotionConfig>
  );
}
