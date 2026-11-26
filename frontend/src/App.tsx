import { Activity, BookOpen, LibraryBig, MessageCircle, Moon, Sun } from "lucide-react";
import { MotionConfig, motion } from "motion/react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { ApiError, getMe, SIGNED_OUT_EVENT, type User } from "./api/client";
import AccountMenu from "./components/AccountMenu";
import Chat from "./pages/Chat";
import Login from "./pages/Login";
import Sites from "./pages/Sites";
import Status from "./pages/Status";

type Tab = "chat" | "sites" | "status";

const tabFromHash = (): Tab => {
  const h = window.location.hash.slice(1);
  return h === "status" || h === "sites" ? h : "chat";
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

  const [tab, setTab] = useState<Tab>(tabFromHash);

  const [dark, setDark] = useState(() => {
    const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    try { return (localStorage.getItem("nullish.theme") ?? (prefersDark ? "dark" : "light")) === "dark"; }
    catch { return prefersDark; }
  });
  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
    try { localStorage.setItem("nullish.theme", dark ? "dark" : "light"); } catch { /* session only */ }
  }, [dark]);

  useEffect(() => {
    const onHash = () => setTab(tabFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const tabs = [
    { id: "chat", label: "Chat", icon: MessageCircle },
    { id: "sites", label: "Site library", icon: LibraryBig },
    { id: "status", label: "Status", icon: Activity },
  ] as const;

  if (me.isLoading) return <div className="shell" aria-busy="true" />;
  if (me.isError)
    return <div className="login-screen"><p className="login-error">
      The Smish API isn't reachable right now. Please try again in a moment.</p></div>;
  if (!me.data) return <MotionConfig reducedMotion="user"><Login /></MotionConfig>;

  return <MotionConfig reducedMotion="user">
    <div className="shell">
      <header className="mast">
        <a href="#chat" className="wordmark" aria-label="Smish home"><span className="brand-tile"><BookOpen /></span>Smish</a>
        <nav aria-label="Main navigation" className="nav-dock">
          {tabs.map(({id, label, icon: Icon}) => <a href={`#${id}`} key={id}
            aria-current={tab === id ? "page" : undefined}>
            {tab === id && <motion.span layoutId="active-tab" className="active-tab"
              transition={{type:"spring", stiffness:460, damping:36}} />}
            <Icon size={17}/><span>{label}</span>
          </a>)}
        </nav>
        <div className="mast-right">
          <button className="theme-switch" aria-label="Dark theme" aria-pressed={dark}
            onClick={() => setDark(!dark)}>{dark ? <Moon size={18}/> : <Sun size={18}/>}
            <span>{dark ? "Dark" : "Light"}</span></button>
          <AccountMenu user={me.data} />
        </div>
      </header>
      <main>
        <div className="h-full" hidden={tab !== "chat"}><Chat /></div>
        {tab !== "chat" && <div className="page-scroll"><motion.div key={tab} className="library-wrap"
          initial={{opacity:0,y:6}} animate={{opacity:1,y:0}} transition={{duration:.2}}>
          {tab === "sites" ? <Sites/> : <Status/>}
        </motion.div></div>}
      </main>
    </div>
  </MotionConfig>;
}
