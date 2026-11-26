import { Pencil, Trash2 } from "lucide-react";
import { ArrowDown, ArrowUp, ArrowUpRight, ChevronDown, Compass, Globe2, History, LibraryBig, ListTree, MessageCircle, Plus, Sparkles, Square, Tag } from "lucide-react";
import { motion } from "motion/react";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import type React from "react";
import { Bubble, type Mode, type Msg } from "../components/Message";
import {
  fetchHealth,
  generateTitle,
  listSites,
  streamAsk,
  streamChat,
  type ChatTurn,
  type LiveStats,
} from "../api/client";

const SUGGESTIONS: Record<Mode, string[]> = {
  sites: [
    "Give me an overview of my sites.",
    "What is the return or refund policy?",
    "Find pages that mention pricing.",
  ],
  model: [
    "Explain a difficult idea with a simple example.",
    "Help me plan a creative project.",
  ],
};



const plural = (n: number | undefined, word: string) => `${n ?? 0} ${word}${n === 1 ? "" : "s"}`;

function liveSummary(s: LiveStats): string {
  const parts = [
    `${plural((s.checked ?? 0) + (s.discovered ?? 0), "page")} checked live`,
    s.changed ? `${s.changed} updated` : null,
    s.new ? `${s.new} new found` : null,
    s.gone ? `${s.gone} removed` : null,
    s.error ? `${s.error} unreachable` : null,
  ].filter(Boolean);
  return parts.join(" · ");
}

function statusText(s: { stage: string; sites?: number; sources?: number; pages?: number } & LiveStats) {
  if (s.stage === "understanding") return "Thinking…";
  if (s.stage === "searching") return `Searching ${plural(s.sites, "site")}…`;
  if (s.stage === "verifying") return `Checking ${plural(s.pages, "page")} on the live site…`;
  if (s.stage === "verified") return `${liveSummary(s)}…`;
  return `Writing from ${plural(s.sources, "source")}…`;
}

// titleSource: "auto" = first question (placeholder), "llm" = written by the model after the
// first answer, "user" = renamed by hand (never overwritten).
type Conversation = { id: string; title: string; updated: number; messages: Msg[];
  mode: Mode; selected: number[]; titleSource?: "auto" | "llm" | "user" };

function loadChat(id: string): Conversation | null {
  try { return JSON.parse(localStorage.getItem(PREFIX + id) || "null"); } catch { return null; }
}
const PREFIX = "nullish.chat.";
function newChatId() {
  return Array.from(crypto.getRandomValues(new Uint32Array(4)), n =>
    n.toString(16).padStart(8, "0")).join("");
}
function readHistory(): Conversation[] {
  const chats: Conversation[] = [];
  try {
    for (const key of Object.keys(localStorage).filter(k => k.startsWith(PREFIX))) {
      try {
        const c = JSON.parse(localStorage.getItem(key) || "null") as Conversation;
        if (c && Array.isArray(c.messages) && Array.isArray(c.selected)) {
          c.messages = c.messages.map(m => m.pending
            ? { ...m, pending: false, error: "Response interrupted. You can continue below." } : m);
          chats.push(c);
        }
      } catch { /* Other conversations remain accessible. */ }
    }
  } catch { /* Storage may be unavailable in private browsing. */ }
  return chats.sort((a,b) => b.updated - a.updated);
}

function HistoryRow({ chat, current, disabled, onOpen, onRename, onDelete }: {
  chat: Conversation; current: boolean; disabled: boolean;
  onOpen: () => void; onRename: (title: string) => void; onDelete: () => void;
}) {
  const [editing, setEditing] = useState(false);
  // Two-step delete without a pop-up: first click asks, second click deletes.
  // The question disappears again after a few seconds or when the pointer leaves the row.
  const [confirming, setConfirming] = useState(false);
  useEffect(() => {
    if (!confirming) return;
    const t = window.setTimeout(() => setConfirming(false), 4000);
    return () => window.clearTimeout(t);
  }, [confirming]);
  const [draft, setDraft] = useState(chat.title);
  const start = () => { setDraft(chat.title); setEditing(true); };
  const finish = (save: boolean) => {
    if (save && draft.trim() && draft.trim() !== chat.title) onRename(draft);
    setEditing(false);
  };
  return (
    <div className={`history-row${confirming ? " confirming" : ""}`}
      aria-current={current ? "page" : undefined} onMouseLeave={() => setConfirming(false)}>
      {editing ? (
        <input className="history-rename" autoFocus value={draft} maxLength={80}
          aria-label="Conversation title" onFocus={(e) => e.currentTarget.select()}
          onChange={(e) => setDraft(e.target.value)} onBlur={() => finish(true)}
          onKeyDown={(e) => {
            if (e.key === "Enter") finish(true);
            if (e.key === "Escape") finish(false);
          }} />
      ) : (
        <>
          <button className="history-item" disabled={disabled} onClick={onOpen}
            onDoubleClick={start} title={chat.title}>
            <strong>{chat.title}</strong>
          </button>
          {confirming ? (
            <button className="history-confirm" disabled={disabled} onClick={onDelete}
              aria-label={`Confirm: delete ${chat.title}`} autoFocus
              onKeyDown={(e) => e.key === "Escape" && setConfirming(false)}>
              <Trash2 size={13} /> Delete?
            </button>
          ) : (
            <>
              <button className="history-edit" aria-label={`Rename ${chat.title}`} title="Rename"
                onClick={start}><Pencil size={14} /></button>
              <button className="history-edit history-delete" aria-label={`Delete ${chat.title}`}
                title="Delete" disabled={disabled} onClick={() => setConfirming(true)}>
                <Trash2 size={14} /></button>
            </>
          )}
        </>
      )}
    </div>
  );
}

export default function Chat() {
  const [saved, setSaved] = useState(readHistory);
  const [active, setActive] = useState(() => saved[0]?.id ?? newChatId());
  const [messages, setMessages] = useState<Msg[]>(saved[0]?.messages ?? []);
  const [saveError, setSaveError] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [filter, setFilter] = useState("");
  const historyButton = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!historyOpen) return;
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setHistoryOpen(false);
        historyButton.current?.focus();
      }
    };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [historyOpen]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [mode, setMode] = useState<Mode>(saved[0]?.mode ?? "sites");
  const [selected, setSelected] = useState<number[]>(saved[0]?.selected ?? []);
  const abortRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const { data: health, isError: apiDown } = useQuery({
    queryKey: ["health"],
    queryFn: fetchHealth,
    refetchInterval: 15_000,
    retry: false,
  });
  const { data: sites } = useQuery({ queryKey: ["sites"], queryFn: listSites, retry: false });
  const enabledSites = (sites ?? []).filter((s) => s.enabled);
  const chat = health?.services.chat;
  const online = chat?.ok ?? false;
  // Block sending only when we KNOW it can't work; while the first checks are still loading,
  // let the question through (the server answers with a clear error if something is down).
  const knownOffline = health !== undefined && !online;
  const noSites = sites !== undefined && enabledSites.length === 0;

  const scrollRef = useRef<HTMLDivElement>(null);
  const [atBottom, setAtBottom] = useState(true);
  // Follow streaming text unless the READER moves up. Only real gestures stop following:
  // programmatic scrolls and content reflow also fire scroll events and must not.
  const stick = useRef(true);
  const touchY = useRef(0);
  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    const near = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    if (near) stick.current = true; // back at the bottom: resume following
    setAtBottom(near || stick.current);
  };
  const leaveBottom = () => {
    stick.current = false;
  };
  const gestures = {
    onWheel: (e: React.WheelEvent) => { if (e.deltaY < 0) leaveBottom(); },
    onTouchStart: (e: React.TouchEvent) => { touchY.current = e.touches[0].clientY; },
    onTouchMove: (e: React.TouchEvent) => { if (e.touches[0].clientY > touchY.current + 4) leaveBottom(); },
    onKeyDown: (e: React.KeyboardEvent) => {
      if (["PageUp", "ArrowUp", "Home"].includes(e.key)) leaveBottom();
    },
  };
  // Streaming text follows instantly (smooth scrolling lags behind fast tokens);
  // the "Latest" button glides.
  const toBottom = (smooth = true) =>
    bottomRef.current?.scrollIntoView({ block: "end", behavior: smooth ? "smooth" : "instant" });
  useEffect(() => {
    if (messages.length && stick.current) toBottom(false);
  }, [messages]);

  const updateLast = (fn: (m: Msg) => Msg) =>
    setMessages((prev) => {
      const next = prev.slice();
      next[next.length - 1] = fn(next[next.length - 1]);
      return next;
    });

  async function send(text: string) {
    text = text.trim();
    if (!text || busy || knownOffline || (mode === "sites" && noSites)) return;
    const history: ChatTurn[] = [
      ...messages.filter((m) => m.content && !m.error).slice(-198).map(({ role, content }) => ({ role, content })),
      { role: "user", content: text },
    ];
    const turnMode = mode;
    const chatId = active;
    let answer = "";
    let failed = false;
    setMessages((prev) => [
      ...prev,
      { role: "user", content: text },
      {
        role: "assistant",
        content: "",
        pending: true,
        mode: turnMode,
        status: turnMode === "sites" ? "Thinking…" : undefined,
      },
    ]);
    setInput("");
    stick.current = true;
    setBusy(true);
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    const common = {
      onToken: (t: string) => {
        answer += t;
        updateLast((m) => ({ ...m, content: m.content + t }));
      },
      onError: (error: string) => {
        failed = true;
        updateLast((m) => ({ ...m, error, pending: false }));
      },
    };
    try {
      if (turnMode === "sites") {
        await streamAsk(
          history,
          selected,
          {
            ...common,
            onStatus: (s) =>
              updateLast((m) => ({
                ...m,
                status: statusText(s),
              })),
            onSources: (sources) => updateLast((m) => ({ ...m, sources })),
            onReplace: (text) => {
              answer = text;
              updateLast((m) => ({ ...m, content: text }));
            },
            onDone: (meta) => updateLast((m) => ({ ...m, meta, pending: false })),
          },
          ctrl.signal,
        );
      } else {
        await streamChat(
          history,
          { ...common, onDone: (meta) => updateLast((m) => ({ ...m, meta, pending: false })) },
          ctrl.signal,
        );
      }
    } catch (e) {
      failed = true;
      const stopped = (e as Error).name === "AbortError";
      updateLast((m) => ({
        ...m,
        error: stopped ? (m.content ? "Stopped." : "Stopped before the first token.") : String(e),
      }));
    } finally {
      updateLast((m) => ({ ...m, pending: false }));
      setBusy(false);
      abortRef.current = null;
      inputRef.current?.focus();
      if (!failed && answer.trim()) {
        void nameChat(chatId, [...history, { role: "assistant", content: answer }]);
      }
    }
  }

  useEffect(() => {
    if (!messages.length) return;
    const persist = () => {
      const prev = loadChat(active);
      const sameMessages = !!prev && JSON.stringify(prev.messages) === JSON.stringify(messages);
      if (sameMessages && prev!.mode === mode
          && JSON.stringify(prev!.selected) === JSON.stringify(selected)) {
        return; // just opened or re-rendered: nothing to save, keep its place in the list
      }
      const keepTitle = prev?.titleSource === "llm" || prev?.titleSource === "user";
      const chat: Conversation = {
        id: active, messages, mode, selected,
        title: keepTitle ? prev!.title : messages[0].content.slice(0, 70),
        titleSource: keepTitle ? prev!.titleSource : "auto",
        // Only new messages move a conversation to the top; switching mode or scope doesn't.
        updated: sameMessages ? prev!.updated : Date.now(),
      };
      try {
        localStorage.setItem(PREFIX + active, JSON.stringify(chat));
        setSaved(list => [chat, ...list.filter(c => c.id !== active)]
          .sort((a, b) => b.updated - a.updated));
        setSaveError(false);
      } catch { setSaveError(true); }
    };
    if (!busy) persist();
    const timer = busy ? window.setTimeout(persist, 250) : undefined;
    window.addEventListener("pagehide", persist);
    return () => { clearTimeout(timer); window.removeEventListener("pagehide", persist); };
  }, [active, messages, mode, selected, busy]);

  function patchChat(id: string, patch: Partial<Conversation>) {
    const c = loadChat(id);
    if (!c) return;
    const next = { ...c, ...patch };
    try {
      localStorage.setItem(PREFIX + id, JSON.stringify(next));
      setSaved(list => list.map(x => (x.id === id ? next : x)));
    } catch { setSaveError(true); }
  }

  /** After the first answer, let the model name the conversation (once; never over a rename). */
  async function nameChat(id: string, turns: ChatTurn[]) {
    const current = loadChat(id);
    if (current && current.titleSource && current.titleSource !== "auto") return;
    try {
      const { title } = await generateTitle(turns.slice(0, 4));
      const latest = loadChat(id);
      if (latest && (latest.titleSource ?? "auto") === "auto") {
        patchChat(id, { title, titleSource: "llm" });
      }
    } catch { /* model offline: the first question stays as the title */ }
  }

  // Older chats (saved before model titles existed) get a proper title once, quietly, in the
  // background: one request at a time, only chats that already have an answer, never renamed ones.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const unnamed = readHistory().filter(c => (c.titleSource ?? "auto") === "auto"
        && c.messages.some(m => m.role === "assistant" && m.content && !m.error)).slice(0, 20);
      for (const c of unnamed) {
        if (cancelled) return;
        const turns = c.messages.filter(m => m.content && !m.error).slice(0, 4)
          .map(({ role, content }) => ({ role, content }));
        await nameChat(c.id, turns);
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function deleteChat(id: string) {
    if (busy) return;
    try { localStorage.removeItem(PREFIX + id); } catch { /* storage unavailable */ }
    setSaved(list => list.filter(c => c.id !== id));
    if (id === active) {
      // The open conversation is gone: start a fresh one (without re-saving the deleted chat).
      setActive(newChatId());
      setMessages([]);
      setInput("");
    }
  }

  function renameChat(id: string, title: string) {
    const clean = title.trim().slice(0, 80);
    if (clean) patchChat(id, { title: clean, titleSource: "user" });
  }

  function openChat(c?: Conversation) {
    if (busy) return;
    setFilter("");
    setActive(c?.id ?? newChatId());
    setMessages(c?.messages ?? []);
    setMode(c?.mode ?? "sites");
    setSelected(c?.selected ?? []);
    setInput("");
    setHistoryOpen(false);
    inputRef.current?.focus();
  }

  const toggleSite = (id: number) =>
    setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));

  const modeBtn = (m: Mode, label: string) => (
    <button
      type="button"
      disabled={busy} aria-pressed={mode === m}
      onClick={() => setMode(m)}
      className={`rounded-md px-2.5 py-1 ${
        mode === m
          ? "bg-white font-medium shadow-sm dark:bg-zinc-700"
          : "text-zinc-600 dark:text-zinc-400"
      }`}
    >
      {m === "sites" ? <Globe2 size={15}/> : <Sparkles size={15}/>} {label}
    </button>
  );

  return (
    <div className="workspace">
      <button ref={historyButton} aria-controls="saved-history" className="history-toggle" aria-expanded={historyOpen}
        onClick={() => setHistoryOpen(!historyOpen)}><History size={16}/> Conversations · {saved.length}<ChevronDown size={15}/></button>
      <aside id="saved-history" aria-label="Saved conversations" className={historyOpen ? "history expanded" : "history"}>
        <h2><History size={17}/> Conversations</h2>
        <button className="new-chat" disabled={busy} onClick={() => openChat()}><Plus size={17}/> New chat</button>
        {saved.length === 0 && <div className="empty-history"><MessageCircle size={22}/><p>Your saved chats will appear here.</p></div>}
        {saved.length > 3 && <input className="history-search" type="search" aria-label="Find a conversation"
          value={filter} placeholder="Find a conversation" onChange={e => setFilter(e.target.value)}/>}
        {filter && !saved.some(c => c.title.toLowerCase().includes(filter.toLowerCase())) && <p>No matching conversations.</p>}
        {saved.filter(c => c.title.toLowerCase().includes(filter.toLowerCase())).map(c =>
          <HistoryRow key={c.id} chat={c} current={active === c.id} disabled={busy}
            onOpen={() => openChat(c)} onRename={(t) => renameChat(c.id, t)}
            onDelete={() => deleteChat(c.id)} />)}
        <a className="rail-library" href="#sites"><LibraryBig size={18}/> Site library <ArrowUpRight size={15}/></a>
      </aside>
      <section className="min-w-0 min-h-0 flex flex-col">
      {saveError && <p role="alert">Browser storage is full or unavailable. This chat could not be saved.</p>}
      <div className="controls">
        <div className="mode-picker">
          {modeBtn("sites", "Live sites")}
          {modeBtn("model", "Model chat")}
        </div>
        {mode === "sites" && enabledSites.length > 0 && (
          <details className="scope"><summary><LibraryBig size={15}/>{selected.length ? selected.length + " selected" : "All sites"} <ChevronDown size={14}/></summary><div className="scope-list">

            {enabledSites.map((s) => (
              <button
                key={s.id}
                type="button"
                disabled={busy} aria-pressed={selected.includes(s.id)} onClick={() => toggleSite(s.id)}
                className={`rounded-full border px-2 py-0.5 ${
                  selected.includes(s.id)
                    ? "border-zinc-900 bg-zinc-900 text-white dark:border-zinc-100 dark:bg-zinc-100 dark:text-zinc-900"
                    : "border-zinc-300 text-zinc-600 hover:border-zinc-500 dark:border-zinc-700 dark:text-zinc-400"
                }`}
              >
                {s.name}
              </button>
            ))}
          </div></details>
        )}
        <a href="#status" className="model-status">
          <span
            className={`h-2 w-2 shrink-0 rounded-full ${online ? "bg-emerald-500" : "bg-zinc-400"}`}
            aria-hidden
          />
          <span className="truncate">
            {apiDown
              ? "Smish API unreachable"
              : health === undefined
                ? "Checking model…"
                : online
                  ? "Model online"
                  : "Model offline"}
          </span>
        </a>
      </div>

      <div className="thread-wrap">
      <div className="thread-scroll" ref={scrollRef} onScroll={onScroll} {...gestures}>
        <div className="thread space-y-5">
          {messages.length === 0 && (
            <div className="welcome">
              <div className="welcome-card">
                <div className="hero-art" aria-hidden="true"/>
                <div className="welcome-copy">
                  <h1>{mode === "sites" ? "Ask your library." : "Start a conversation."}</h1>
                  <p>{mode === "model" ? "Explore an idea or work through a question with the model."
                    : enabledSites.length ? "Explore your saved websites, with live answers and links to the original pages."
                    : "Add your first website to start asking questions about its pages."}</p>
                  {mode === "sites" && !enabledSites.length &&
                    <a href="#sites" className="hero-link"><Plus size={16}/> Add a website</a>}
                </div>
              </div>
              <h2 className="starters-heading">Conversation starters</h2>
              <div className="starters">{SUGGESTIONS[mode].map((q,i) => {
                const Icon = [Compass, ListTree, Tag][i];
                return <motion.button key={q} whileHover={{y:-3}} whileTap={{scale:.98}}
                  className="starter" onClick={() => {setInput(q); inputRef.current?.focus();}}>
                  <span className="starter-icon"><Icon size={21}/></span>
                  <span>{q}</span><ArrowUpRight size={14}/>
                </motion.button>;
              })}</div>
            </div>
          )}
          {messages.map((m, i) => (
            <motion.div key={active + "-" + i} initial={{opacity:0,y:6}} animate={{opacity:1,y:0}}
              transition={{duration:.22, ease:"easeOut"}}><Bubble m={m}/></motion.div>
          ))}
          <div ref={bottomRef} />
        </div>
      </div>
      {!atBottom && messages.length > 0 && (
        <button type="button" className="jump-bottom"
          onClick={() => { stick.current = true; setAtBottom(true); toBottom(); }}>
          <ArrowDown size={14}/> Latest
        </button>
      )}
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          send(input);
        }}
        className="composer"
      >
        <div className="compose-box">
          <textarea
            aria-label="Message" maxLength={20000}
            ref={inputRef}
            value={input}
            rows={Math.min(6, Math.max(1, input.split("\n").length))}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                send(input);
              }
            }}
            placeholder={mode === "sites" ? "Ask about your sites…" : "Message the model…"}
            className="min-w-0 flex-1 resize-none rounded-lg border border-zinc-300 bg-white px-3 py-2 outline-none focus:border-zinc-500 dark:border-zinc-700 dark:bg-zinc-900"
          />
          {busy ? (
            <button
              type="button"
              onClick={() => abortRef.current?.abort()}
              className="rounded-lg border border-zinc-300 px-4 py-2 text-sm font-medium hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-800"
            >
              <Square size={14}/> Stop
            </button>
          ) : (
            <button
              type="submit"
              disabled={!input.trim() || knownOffline || (mode === "sites" && noSites)}
              className="rounded-lg bg-zinc-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-40 dark:bg-zinc-100 dark:text-zinc-900"
            >
              <ArrowUp size={19}/> Send
            </button>
          )}
        </div>
      </form>
      </section>
    </div>
  );
}
