import { Pencil, Trash2 } from "lucide-react";
import { ArrowDown, ArrowUp, ArrowUpRight, ChevronDown, Compass, Database, Globe2, HardDrive, History, LibraryBig, ListTree, MessageCircle, Plus, Sparkles, Square, Tag } from "lucide-react";
import { motion } from "motion/react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import type React from "react";
import { setChatTitle } from "../pageTitle";
import { Bubble, type Mode, type Msg } from "../components/Message";
import {
  deleteConversation,
  fetchHealth,
  generateTitle,
  getConversation,
  importConversations,
  listConversations,
  listSites,
  listSources,
  streamAsk,
  streamChat,
  updateConversation,
  type AskDone,
  type AskStatus,
  type ChatDone,
  type ChatTurn,
  type ConversationSummary,
  type LiveStats,
  type QueryRecord,
  type Source,
  type StoredMessage,
  type User,
} from "../api/client";
import {
  appendLocalQueryLogs,
  deleteLocalChat,
  getLocalChat,
  getLocalChats,
  getLocalDbs,
  LOCAL_CHATS_EVENT,
  LOCAL_DBS_EVENT,
  toLocalChatSummaries,
  toLocalSourcePayload,
  upsertLocalChat,
  type LocalConversation,
  type LocalDataSource,
} from "../storage/localStore";

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

function statusText(s: AskStatus) {
  if (s.stage === "understanding") return "Thinking…";
  if (s.stage === "reading_schema") return `Reading the structure of ${s.database ?? "the database"}…`;
  if (s.stage === "querying") return s.n ? `Running query ${s.n}…` : `Querying ${s.database ?? "the database"}…`;
  if (s.stage === "writing" && s.queries !== undefined) return "Writing the answer…";
  if (s.stage === "searching") return `Searching ${plural(s.sites, "site")}…`;
  if (s.stage === "verifying") return `Checking ${plural(s.pages, "page")} on the live site…`;
  if (s.stage === "verified") return `${liveSummary(s)}…`;
  return `Writing from ${plural(s.sources, "source")}…`;
}

const LEGACY_PREFIX = "nullish.chat.";

function newChatId() {
  return Array.from(crypto.getRandomValues(new Uint32Array(4)), (n) =>
    n.toString(16).padStart(8, "0"),
  ).join("");
}

type LegacyConv = {
  id: string;
  title: string;
  updated: number;
  messages: Msg[];
  mode: Mode;
  selected: number[];
  titleSource?: "auto" | "llm" | "user";
};

function readLegacyLocalChats(): { key: string; conv: LegacyConv }[] {
  const out: { key: string; conv: LegacyConv }[] = [];
  try {
    for (const key of Object.keys(localStorage).filter((k) => k.startsWith(LEGACY_PREFIX))) {
      try {
        const c = JSON.parse(localStorage.getItem(key) || "null") as LegacyConv | null;
        if (c && Array.isArray(c.messages) && c.messages.length > 0) {
          out.push({ key, conv: c });
        }
      } catch {
        /* ignore malformed entry */
      }
    }
  } catch {
    /* localStorage unavailable */
  }
  return out;
}

function toMsg(m: StoredMessage): Msg {
  return {
    role: m.role,
    content: m.content,
    mode: m.mode ?? undefined,
    error: m.error ?? undefined,
    sources: m.sources ?? undefined,
    queries: m.queries ?? undefined,
    meta: m.meta ?? undefined,
  };
}

function HistoryRow({
  chat,
  current,
  disabled,
  onOpen,
  onRename,
  onDelete,
}: {
  chat: ConversationSummary;
  current: boolean;
  disabled: boolean;
  onOpen: () => void;
  onRename: (name: string) => void;
  onDelete: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [confirming, setConfirming] = useState(false);
  useEffect(() => {
    if (!confirming) return;
    const t = window.setTimeout(() => setConfirming(false), 4000);
    return () => window.clearTimeout(t);
  }, [confirming]);
  const [draft, setDraft] = useState(chat.name);
  const start = () => {
    setDraft(chat.name);
    setEditing(true);
  };
  const finish = (save: boolean) => {
    if (save && draft.trim() && draft.trim() !== chat.name) onRename(draft);
    setEditing(false);
  };
  return (
    <div
      className={`history-row${confirming ? " confirming" : ""}`}
      aria-current={current ? "page" : undefined}
      onMouseLeave={() => setConfirming(false)}
    >
      {editing ? (
        <input
          className="history-rename"
          autoFocus
          value={draft}
          maxLength={120}
          aria-label="Conversation name"
          onFocus={(e) => e.currentTarget.select()}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={() => finish(true)}
          onKeyDown={(e) => {
            if (e.key === "Enter") finish(true);
            if (e.key === "Escape") finish(false);
          }}
        />
      ) : (
        <>
          <button
            className="history-item"
            disabled={disabled}
            onClick={onOpen}
            onDoubleClick={start}
            title={chat.name}
          >
            <strong>{chat.name}</strong>
          </button>
          {confirming ? (
            <button
              className="history-confirm"
              disabled={disabled}
              onClick={onDelete}
              aria-label={`Confirm: delete ${chat.name}`}
              autoFocus
              onKeyDown={(e) => e.key === "Escape" && setConfirming(false)}
            >
              <Trash2 size={13} /> Delete?
            </button>
          ) : (
            <>
              <button
                className="history-edit"
                aria-label={`Rename ${chat.name}`}
                title="Rename"
                onClick={start}
              >
                <Pencil size={14} />
              </button>
              <button
                className="history-edit history-delete"
                aria-label={`Delete ${chat.name}`}
                title="Delete"
                disabled={disabled}
                onClick={() => setConfirming(true)}
              >
                <Trash2 size={14} />
              </button>
            </>
          )}
        </>
      )}
    </div>
  );
}

export default function Chat({ isAdmin = false, user }: { isAdmin?: boolean; user: User }) {
  const qc = useQueryClient();
  const isLocal = user.storage_mode === "local";
  const [active, setActive] = useState(newChatId);
  const [messages, setMessages] = useState<Msg[]>([]);
  const [loadingThread, setLoadingThread] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [filter, setFilter] = useState("");
  const historyButton = useRef<HTMLButtonElement>(null);
  const initializedRef = useRef(false);
  const prevStorageRef = useRef(user.storage_mode);

  const [localChats, setLocalChats] = useState<LocalConversation[]>(() =>
    isLocal ? getLocalChats(user.id) : [],
  );
  const [localDbs, setLocalDbs] = useState<LocalDataSource[]>(() =>
    isLocal ? getLocalDbs(user.id) : [],
  );

  useEffect(() => {
    if (!isLocal) return;
    const syncChats = () => setLocalChats(getLocalChats(user.id));
    const syncDbs = () => setLocalDbs(getLocalDbs(user.id));
    syncChats();
    syncDbs();
    window.addEventListener(LOCAL_CHATS_EVENT, syncChats);
    window.addEventListener(LOCAL_DBS_EVENT, syncDbs);
    return () => {
      window.removeEventListener(LOCAL_CHATS_EVENT, syncChats);
      window.removeEventListener(LOCAL_DBS_EVENT, syncDbs);
    };
  }, [isLocal, user.id]);

  const { data: serverSaved = [] } = useQuery({
    queryKey: ["conversations"],
    queryFn: listConversations,
    enabled: !isLocal,
  });
  const saved = isLocal ? toLocalChatSummaries(localChats) : serverSaved;

  // One-time migration of any older localStorage chats into Postgres when in Server DB mode.
  useEffect(() => {
    if (isLocal) return;
    const legacy = readLegacyLocalChats();
    if (!legacy.length) return;
    let cancelled = false;
    (async () => {
      try {
        await importConversations(
          legacy.map(({ conv }) => ({
            id: conv.id,
            name: (conv.title || conv.messages[0]?.content || "New chat").slice(0, 120),
            title_source: conv.titleSource ?? "auto",
            mode: conv.mode === "model" ? "model" : "sites",
            selected_sources: Array.isArray(conv.selected) ? conv.selected : [],
            updated_ms: conv.updated,
            messages: conv.messages.map((m) => ({
              role: m.role,
              content: m.content || "",
              mode: m.mode,
              error: m.error,
              sources: m.sources,
              queries: m.queries,
              meta: m.meta,
            })),
          })),
        );
        for (const { key } of legacy) {
          try {
            localStorage.removeItem(key);
          } catch {
            /* ignore */
          }
        }
        if (!cancelled) {
          qc.invalidateQueries({ queryKey: ["conversations"] });
        }
      } catch {
        /* keep local entries if import fails temporarily */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [qc, isLocal]);

  useEffect(() => {
    if (prevStorageRef.current !== user.storage_mode) {
      prevStorageRef.current = user.storage_mode;
      initializedRef.current = false;
      setActive(newChatId());
      setMessages([]);
      setInput("");
    }
  }, [user.storage_mode]);

  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [mode, setMode] = useState<Mode>("sites");
  const [selected, setSelected] = useState<number[]>([]);
  const abortRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // Open the most recent saved conversation on first load if user hasn't started one yet.
  useEffect(() => {
    if (initializedRef.current || !saved.length) return;
    initializedRef.current = true;
    if (messages.length === 0 && !input.trim()) {
      void openChat(saved[0]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [saved]);

  // Update browser tab title for the open conversation.
  const openTitle = saved.find((c) => c.id === active)?.name ?? "";
  useEffect(() => {
    setChatTitle(openTitle);
  }, [openTitle]);

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

  const { data: health, isError: apiDown } = useQuery({
    queryKey: ["health"],
    queryFn: fetchHealth,
    refetchInterval: 15_000,
    retry: false,
  });
  const { data: sites } = useQuery({ queryKey: ["sites"], queryFn: listSites, retry: false });
  const { data: serverDbs } = useQuery({
    queryKey: ["data-sources"],
    queryFn: listSources,
    enabled: !isLocal,
    retry: false,
  });
  const dbs = isLocal ? localDbs : serverDbs;
  const enabledSites = (sites ?? []).filter((s) => s.enabled);
  const enabledDbs = (dbs ?? []).filter((d) => d.enabled);
  const scopeItems = [
    ...enabledSites.map((s) => ({ key: s.id, name: s.name, db: false })),
    ...enabledDbs.map((d) => ({ key: -d.id, name: d.name, db: true })),
  ];
  const chat = health?.services.chat;
  const online = chat?.ok ?? false;
  const knownOffline = health !== undefined && !online;
  const noSites =
    sites !== undefined &&
    dbs !== undefined &&
    enabledSites.length === 0 &&
    enabledDbs.length === 0;

  const scrollRef = useRef<HTMLDivElement>(null);
  const [atBottom, setAtBottom] = useState(true);
  const stick = useRef(true);
  const touchY = useRef(0);
  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    const near = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    if (near) stick.current = true;
    setAtBottom(near || stick.current);
  };
  const leaveBottom = () => {
    stick.current = false;
  };
  const gestures = {
    onWheel: (e: React.WheelEvent) => {
      if (e.deltaY < 0) leaveBottom();
    },
    onTouchStart: (e: React.TouchEvent) => {
      touchY.current = e.touches[0].clientY;
    },
    onTouchMove: (e: React.TouchEvent) => {
      if (e.touches[0].clientY > touchY.current + 4) leaveBottom();
    },
    onKeyDown: (e: React.KeyboardEvent) => {
      if (["PageUp", "ArrowUp", "Home"].includes(e.key)) leaveBottom();
    },
  };
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
    initializedRef.current = true;
    const history: ChatTurn[] = [
      ...messages
        .filter((m) => m.content && !m.error)
        .slice(-198)
        .map(({ role, content }) => ({ role, content })),
      { role: "user", content: text },
    ];
    const turnMode = mode;
    const chatId = active;
    const existingConv = saved.find((c) => c.id === chatId);
    const localExisting = isLocal ? getLocalChat(user.id, chatId) : undefined;
    const nowIso = new Date().toISOString();
    let answer = "";
    let failed = false;
    let streamErr: string | undefined;
    let collectedSources: Source[] | undefined;
    const collectedQueries: QueryRecord[] = [];
    let finalMeta: (ChatDone | AskDone) | undefined;
    let updatedSummary = localExisting?.summary ?? null;

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

    if (isLocal) {
      upsertLocalChat(user.id, {
        id: chatId,
        name: localExisting?.name ?? (text.slice(0, 70) || "New chat"),
        title_source: localExisting?.title_source ?? "auto",
        mode: turnMode,
        selected_sources: selected,
        created_at: localExisting?.created_at ?? nowIso,
        updated_at: nowIso,
        summary: updatedSummary,
        messages: [...messages.map((m) => ({ ...m })), { role: "user", content: text }],
      });
    } else if (!existingConv) {
      // Optimistically reflect new conversation in sidebar right away.
      qc.setQueryData<ConversationSummary[]>(["conversations"], (prev = []) => [
        {
          id: chatId,
          name: text.slice(0, 70) || "New chat",
          title_source: "auto",
          mode: turnMode,
          selected_sources: selected,
          created_at: nowIso,
          updated_at: nowIso,
          message_count: 1,
        },
        ...prev.filter((c) => c.id !== chatId),
      ]);
    }

    const ctrl = new AbortController();
    abortRef.current = ctrl;
    const common = {
      onToken: (t: string) => {
        answer += t;
        updateLast((m) => ({ ...m, content: m.content + t }));
      },
      onMemory: (sum: string) => {
        updatedSummary = sum;
      },
      onError: (error: string) => {
        failed = true;
        streamErr = error;
        updateLast((m) => ({ ...m, error, pending: false }));
      },
    };
    try {
      if (turnMode === "sites") {
        const scope = selected.length
          ? {
              siteIds: selected.filter((k) => k > 0),
              sourceIds: selected.filter((k) => k < 0).map((k) => -k),
            }
          : null;
        const localSourcesPayload = isLocal
          ? localDbs
              .filter(
                (d) =>
                  d.enabled && (!scope || scope.sourceIds.includes(d.id)),
              )
              .map(toLocalSourcePayload)
          : undefined;
        await streamAsk(
          history,
          scope,
          {
            ...common,
            onQuery: (q) => {
              collectedQueries.push(q);
              updateLast((m) => ({ ...m, queries: [...(m.queries ?? []), q] }));
            },
            onStatus: (s) =>
              updateLast((m) => ({
                ...m,
                status: statusText(s),
              })),
            onSources: (sources) => {
              collectedSources = sources;
              updateLast((m) => ({ ...m, sources }));
            },
            onReplace: (replaced) => {
              answer = replaced;
              updateLast((m) => ({ ...m, content: replaced }));
            },
            onDone: (meta) => {
              finalMeta = meta;
              updateLast((m) => ({ ...m, meta, pending: false }));
            },
          },
          ctrl.signal,
          {
            conversationId: isLocal ? undefined : chatId,
            conversationSummary: isLocal ? updatedSummary : undefined,
            selectedSources: selected,
            localSources: localSourcesPayload,
          },
        );
      } else {
        await streamChat(
          history,
          {
            ...common,
            onDone: (meta) => {
              finalMeta = meta;
              updateLast((m) => ({ ...m, meta, pending: false }));
            },
          },
          ctrl.signal,
          {
            conversationId: isLocal ? undefined : chatId,
            conversationSummary: isLocal ? updatedSummary : undefined,
            selectedSources: selected,
          },
        );
      }
    } catch (e) {
      failed = true;
      const stopped = (e as Error).name === "AbortError";
      streamErr = stopped ? (answer ? "Stopped." : "Stopped before the first token.") : String(e);
      updateLast((m) => ({
        ...m,
        error: streamErr,
      }));
    } finally {
      updateLast((m) => ({ ...m, pending: false }));
      setBusy(false);
      abortRef.current = null;
      inputRef.current?.focus();
      if (isLocal) {
        const latestConv = getLocalChat(user.id, chatId);
        const doneIso = new Date().toISOString();
        const savedAssistant: StoredMessage = {
          role: "assistant",
          content: answer,
          mode: turnMode,
          ...(streamErr ? { error: streamErr } : {}),
          ...(collectedSources ? { sources: collectedSources } : {}),
          ...(collectedQueries.length ? { queries: collectedQueries } : {}),
          ...(finalMeta ? { meta: finalMeta } : {}),
        };
        upsertLocalChat(user.id, {
          id: chatId,
          name: latestConv?.name ?? (text.slice(0, 70) || "New chat"),
          title_source: latestConv?.title_source ?? "auto",
          mode: turnMode,
          selected_sources: selected,
          created_at: latestConv?.created_at ?? nowIso,
          updated_at: doneIso,
          summary: updatedSummary,
          messages: [
            ...messages.map((m) => ({ ...m })),
            { role: "user", content: text },
            savedAssistant,
          ],
        });
        if (finalMeta && "database" in finalMeta && finalMeta.database && collectedQueries.length) {
          appendLocalQueryLogs(user.id, finalMeta.database, collectedQueries);
        }
      } else {
        qc.invalidateQueries({ queryKey: ["conversations"] });
      }
      if (!failed && answer.trim()) {
        void nameChat(chatId, [...history, { role: "assistant", content: answer }]);
      }
    }
  }

  async function nameChat(id: string, turns: ChatTurn[]) {
    const cur = saved.find((c) => c.id === id) ?? (isLocal ? getLocalChat(user.id, id) : undefined);
    if (cur && cur.title_source !== "auto") return;
    try {
      const { title } = await generateTitle(turns.slice(0, 4), isLocal ? undefined : id);
      if (isLocal) {
        const lc = getLocalChat(user.id, id);
        if (lc && lc.title_source === "auto") {
          upsertLocalChat(user.id, { ...lc, name: title, title_source: "llm" });
        }
      } else {
        qc.setQueryData<ConversationSummary[]>(["conversations"], (list = []) =>
          list.map((c) =>
            c.id === id && c.title_source === "auto"
              ? { ...c, name: title, title_source: "llm" }
              : c,
          ),
        );
      }
    } catch {
      /* model offline: first question stays as the conversation name */
    }
  }

  async function handleDeleteChat(id: string) {
    if (busy) return;
    if (isLocal) {
      deleteLocalChat(user.id, id);
      if (id === active) {
        setActive(newChatId());
        setMessages([]);
        setInput("");
      }
      return;
    }
    try {
      await deleteConversation(id);
      qc.setQueryData<ConversationSummary[]>(["conversations"], (list = []) =>
        list.filter((c) => c.id !== id),
      );
      if (id === active) {
        setActive(newChatId());
        setMessages([]);
        setInput("");
      }
    } catch {
      /* keep visible on network error */
    }
  }

  async function handleRenameChat(id: string, title: string) {
    const clean = title.trim().slice(0, 120);
    if (!clean) return;
    if (isLocal) {
      const lc = getLocalChat(user.id, id);
      if (lc) upsertLocalChat(user.id, { ...lc, name: clean, title_source: "user" });
      return;
    }
    try {
      const updated = await updateConversation(id, { name: clean, title_source: "user" });
      qc.setQueryData<ConversationSummary[]>(["conversations"], (list = []) =>
        list.map((c) => (c.id === id ? updated : c)),
      );
    } catch {
      /* ignore transient error */
    }
  }

  async function openChat(c?: ConversationSummary) {
    if (busy) return;
    initializedRef.current = true;
    setFilter("");
    setInput("");
    setHistoryOpen(false);
    if (!c) {
      setActive(newChatId());
      setMessages([]);
      setMode("sites");
      setSelected([]);
      inputRef.current?.focus();
      return;
    }
    setActive(c.id);
    setMode(c.mode);
    setSelected(c.selected_sources ?? []);
    if (isLocal) {
      const lc = getLocalChat(user.id, c.id);
      setMessages((lc?.messages ?? []).map(toMsg));
      if (lc) {
        setMode(lc.mode);
        setSelected(lc.selected_sources ?? []);
      }
      inputRef.current?.focus();
      return;
    }
    setLoadingThread(true);
    try {
      const detail = await getConversation(c.id);
      setMessages(detail.messages.map(toMsg));
      setMode(detail.mode);
      setSelected(detail.selected_sources ?? []);
    } catch {
      setMessages([]);
    } finally {
      setLoadingThread(false);
      inputRef.current?.focus();
    }
  }

  const syncScopeToServer = (nextMode: Mode, nextSelected: number[]) => {
    if (isLocal) {
      const lc = getLocalChat(user.id, active);
      if (lc) upsertLocalChat(user.id, { ...lc, mode: nextMode, selected_sources: nextSelected });
      return;
    }
    if (!saved.some((c) => c.id === active)) return;
    void updateConversation(active, { mode: nextMode, selected_sources: nextSelected })
      .then((updated) =>
        qc.setQueryData<ConversationSummary[]>(["conversations"], (list = []) =>
          list.map((c) => (c.id === active ? updated : c)),
        ),
      )
      .catch(() => {});
  };

  const toggleSite = (id: number) => {
    const next = selected.includes(id) ? selected.filter((x) => x !== id) : [...selected, id];
    setSelected(next);
    syncScopeToServer(mode, next);
  };

  const changeMode = (nextMode: Mode) => {
    setMode(nextMode);
    syncScopeToServer(nextMode, selected);
  };

  const modeBtn = (m: Mode, label: string) => (
    <button
      type="button"
      disabled={busy}
      aria-pressed={mode === m}
      onClick={() => changeMode(m)}
      className={`rounded-md px-2.5 py-1 ${
        mode === m
          ? "bg-white font-medium shadow-sm dark:bg-zinc-700"
          : "text-zinc-600 dark:text-zinc-400"
      }`}
    >
      {m === "sites" ? <Globe2 size={15} /> : <Sparkles size={15} />} {label}
    </button>
  );

  const modelBadge = (
    <>
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
    </>
  );

  return (
    <div className="workspace">
      <button
        ref={historyButton}
        aria-controls="saved-history"
        className="history-toggle"
        aria-expanded={historyOpen}
        onClick={() => setHistoryOpen(!historyOpen)}
      >
        <History size={16} /> Conversations · {saved.length}
        <ChevronDown size={15} />
      </button>
      <aside
        id="saved-history"
        aria-label="Saved conversations"
        className={historyOpen ? "history expanded" : "history"}
      >
        <h2>
          <History size={17} /> Conversations
        </h2>
        <button className="new-chat" disabled={busy} onClick={() => void openChat()}>
          <Plus size={17} /> New chat
        </button>
        <a
          href="#settings"
          className="storage-mode-badge"
          title={
            isLocal
              ? "Stored only in this browser. Clearing browser data or changing devices will wipe chats."
              : "Stored persistently and privately in Postgres."
          }
        >
          {isLocal ? (
            <>
              <HardDrive size={12} /> Browser local · Ephemeral
            </>
          ) : (
            <>
              <Database size={12} /> Server DB · Persistent
            </>
          )}
        </a>
        {saved.length === 0 && (
          <div className="empty-history">
            <MessageCircle size={22} />
            <p>Your saved chats will appear here.</p>
          </div>
        )}
        {saved.length > 3 && (
          <input
            className="history-search"
            type="search"
            aria-label="Find a conversation"
            value={filter}
            placeholder="Find a conversation"
            onChange={(e) => setFilter(e.target.value)}
          />
        )}
        {filter && !saved.some((c) => c.name.toLowerCase().includes(filter.toLowerCase())) && (
          <p>No matching conversations.</p>
        )}
        {saved
          .filter((c) => c.name.toLowerCase().includes(filter.toLowerCase()))
          .map((c) => (
            <HistoryRow
              key={c.id}
              chat={c}
              current={active === c.id}
              disabled={busy}
              onOpen={() => void openChat(c)}
              onRename={(t) => void handleRenameChat(c.id, t)}
              onDelete={() => void handleDeleteChat(c.id)}
            />
          ))}
        <a className="rail-library" href="#sites">
          <LibraryBig size={18} /> Sources <ArrowUpRight size={15} />
        </a>
      </aside>
      <section className="min-w-0 min-h-0 flex flex-col">
        <div className="controls">
          <div className="mode-picker">
            {modeBtn("sites", "Your sources")}
            {modeBtn("model", "Model chat")}
          </div>
          {mode === "sites" && scopeItems.length > 0 && (
            <details className="scope">
              <summary>
                <LibraryBig size={15} />
                {selected.length ? selected.length + " selected" : "All sources"}{" "}
                <ChevronDown size={14} />
              </summary>
              <div className="scope-list">
                {scopeItems.map((s) => (
                  <button
                    key={s.key}
                    type="button"
                    title={s.db ? "Database" : "Website"}
                    disabled={busy}
                    aria-pressed={selected.includes(s.key)}
                    onClick={() => toggleSite(s.key)}
                    className={`scope-chip rounded-full border px-2 py-0.5 ${
                      selected.includes(s.key)
                        ? "border-zinc-900 bg-zinc-900 text-white dark:border-zinc-100 dark:bg-zinc-100 dark:text-zinc-900"
                        : "border-zinc-300 text-zinc-600 hover:border-zinc-500 dark:border-zinc-700 dark:text-zinc-400"
                    }`}
                  >
                    {s.db ? <Database size={12} /> : <Globe2 size={12} />} {s.name}
                  </button>
                ))}
              </div>
            </details>
          )}
          {isAdmin ? (
            <a href="#status" className="model-status">
              {modelBadge}
            </a>
          ) : (
            <span className="model-status">{modelBadge}</span>
          )}
        </div>

        <div className="thread-wrap">
          <div className="thread-scroll" ref={scrollRef} onScroll={onScroll} {...gestures}>
            <div className="thread space-y-5">
              {loadingThread && messages.length === 0 && (
                <p className="text-sm text-zinc-500">Loading conversation…</p>
              )}
              {!loadingThread && messages.length === 0 && (
                <div className="welcome">
                  <div className="welcome-card">
                    <div className="hero-art" aria-hidden="true" />
                    <div className="welcome-copy">
                      <h1>{mode === "sites" ? "Ask your library." : "Start a conversation."}</h1>
                      <p>
                        {mode === "model"
                          ? "Explore an idea or work through a question with the model."
                          : enabledSites.length && enabledDbs.length
                            ? "Ask about your websites and databases: live answers, with links to the pages and the queries behind them."
                            : enabledDbs.length
                              ? "Ask about your databases. Smish queries them and shows exactly what it ran."
                              : enabledSites.length
                                ? "Explore your saved websites, with live answers and links to the original pages."
                                : "Add a website or a database to start asking questions."}
                      </p>
                      {mode === "sites" && !enabledSites.length && !enabledDbs.length && (
                        <a href="#sites" className="hero-link">
                          <Plus size={16} /> Add a source
                        </a>
                      )}
                    </div>
                  </div>
                  <h2 className="starters-heading">Conversation starters</h2>
                  <div className="starters">
                    {SUGGESTIONS[mode].map((q, i) => {
                      const Icon = [Compass, ListTree, Tag][i];
                      return (
                        <motion.button
                          key={q}
                          whileHover={{ y: -3 }}
                          whileTap={{ scale: 0.98 }}
                          className="starter"
                          onClick={() => {
                            setInput(q);
                            inputRef.current?.focus();
                          }}
                        >
                          <span className="starter-icon">
                            <Icon size={21} />
                          </span>
                          <span>{q}</span>
                          <ArrowUpRight size={14} />
                        </motion.button>
                      );
                    })}
                  </div>
                </div>
              )}
              {messages.map((m, i) => (
                <motion.div
                  key={active + "-" + i}
                  initial={{ opacity: 0, y: 6 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.22, ease: "easeOut" }}
                >
                  <Bubble m={m} user={user} />
                </motion.div>
              ))}
              <div ref={bottomRef} />
            </div>
          </div>
          {!atBottom && messages.length > 0 && (
            <button
              type="button"
              className="jump-bottom"
              onClick={() => {
                stick.current = true;
                setAtBottom(true);
                toBottom();
              }}
            >
              <ArrowDown size={14} /> Latest
            </button>
          )}
        </div>

        <form
          onSubmit={(e) => {
            e.preventDefault();
            void send(input);
          }}
          className="composer"
        >
          <div className="compose-box">
            <textarea
              aria-label="Message"
              maxLength={20000}
              ref={inputRef}
              value={input}
              rows={Math.min(6, Math.max(1, input.split("\n").length))}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  void send(input);
                }
              }}
              placeholder={
                mode === "sites"
                  ? enabledDbs.length
                    ? "Ask about your sites or data…"
                    : "Ask about your sites…"
                  : "Message the model…"
              }
              className="min-w-0 flex-1 resize-none rounded-lg border border-zinc-300 bg-white px-3 py-2 outline-none focus:border-zinc-500 dark:border-zinc-700 dark:bg-zinc-900"
            />
            {busy ? (
              <button
                type="button"
                onClick={() => abortRef.current?.abort()}
                className="rounded-lg border border-zinc-300 px-4 py-2 text-sm font-medium hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-800"
              >
                <Square size={14} /> Stop
              </button>
            ) : (
              <button
                type="submit"
                disabled={!input.trim() || knownOffline || (mode === "sites" && noSites)}
                className="rounded-lg bg-zinc-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-40 dark:bg-zinc-100 dark:text-zinc-900"
              >
                <ArrowUp size={19} /> Send
              </button>
            )}
          </div>
        </form>
      </section>
    </div>
  );
}
