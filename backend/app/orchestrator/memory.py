"""Rolling conversation memory: keeps recent turns verbatim and folds older turns into a
persistent summary so chats stay within model context limits without forgetting earlier context.
"""

import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass

import structlog
from sqlalchemy import or_, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Conversation
from app.llm.chat import ChatTarget

log = structlog.get_logger()

SUMMARY_MAX_CHARS = 2_400
SUMMARY_BATCH_CHARS = 10_000
SUMMARY_OVERHEAD_CHARS = 180
TURN_SNIPPET_CHARS = 2_000
ASK_RECENT_MESSAGES = 12
ASK_HISTORY_CHARS = 10_000

COMPACT_SYSTEM_PROMPT = """You maintain a compact running memory of a conversation so earlier \
details are never forgotten.
Merge the previous summary (if any) with the conversation turns below into one updated summary.
Preserve concrete details that may matter in later turns:
- User's name, role, preferences, instructions, and constraints
- Decisions made, key facts, entities, URLs, numbers, and dates discussed
- Unresolved questions, ongoing tasks, or pending actions
Rules:
- Be concise and factual using short bullet points (stay under 1,800 characters total).
- Never invent facts or drop important specifics mentioned earlier.
- Reply with the updated summary only (no preamble or markdown code fences)."""


@dataclass(slots=True)
class TurnRecord:
    role: str
    content: str
    id: int | None = None


@dataclass(slots=True)
class PreparedConversation:
    conversation_id: str
    summary: str | None
    summary_through_message_id: int | None
    prior_turns: list[TurnRecord]
    user_text: str


@dataclass(slots=True)
class CompactedHistory:
    summary: str | None
    turns: list[dict[str, str]]
    summarized_through_id: int | None = None
    compacted_count: int = 0


def clip_text(text: str, max_chars: int) -> str:
    cleaned = text.strip()
    if len(cleaned) <= max_chars:
        return cleaned
    if max_chars <= 3:
        return cleaned[:max_chars]
    # Prefer cutting at a newline before the limit so bullet points stay intact.
    cut = cleaned.rfind("\n", 0, max_chars - 1)
    if cut >= max_chars // 2:
        return cleaned[:cut].rstrip() + "…"
    return cleaned[: max_chars - 1].rstrip() + "…"


def clean_summary(raw: str, max_chars: int = SUMMARY_MAX_CHARS) -> str:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 2:
            lines = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
            text = "\n".join(lines).strip()
    return clip_text(text, max_chars)


def split_for_compaction(
    prior: Sequence[TurnRecord],
    current_content: str,
    *,
    char_budget: int,
    max_recent_messages: int | None = None,
) -> tuple[list[TurnRecord], list[TurnRecord]]:
    """Split prior turns into `(evicted, kept)` so `kept + [current]` fits within limits.

    When `kept` contains multiple turns and begins with an orphan `assistant` message whose
    prompting `user` message was evicted, that assistant reply is also moved into `evicted` so
    question-answer pairs summarize together and verbatim history opens with a `user` turn.
    """
    if not prior:
        return [], []
    remaining = max(0, char_budget - len(current_content))
    kept_rev: list[TurnRecord] = []
    used = 0
    for row in reversed(prior):
        if max_recent_messages is not None and len(kept_rev) >= max_recent_messages:
            break
        msg_len = len(row.content)
        if remaining == 0:
            break
        if kept_rev and used + msg_len > remaining:
            break
        if not kept_rev and msg_len > remaining:
            break
        kept_rev.append(row)
        used += msg_len
        if used >= remaining:
            break

    kept = list(reversed(kept_rev))
    cutoff = len(prior) - len(kept)
    evicted = list(prior[:cutoff])
    if len(kept) > 1 and kept[0].role == "assistant":
        evicted.append(kept.pop(0))
    return evicted, kept


def budgeted_history(
    messages: Sequence[dict[str, str]],
    *,
    max_messages: int = ASK_RECENT_MESSAGES,
    char_budget: int = 8_000,
    exclude_last: bool = True,
    per_message_cap: int | None = None,
) -> list[dict[str, str]]:
    """Return prior turns (excluding the final message by default) bounded by count and chars."""
    prior = list(messages[:-1] if exclude_last else messages)
    if not prior:
        return []
    kept_rev: list[dict[str, str]] = []
    used = 0
    for m in reversed(prior):
        if len(kept_rev) >= max_messages:
            break
        text = m["content"]
        if per_message_cap is not None and len(text) > per_message_cap:
            text = clip_text(text, per_message_cap)
        if kept_rev and used + len(text) > char_budget:
            break
        kept_rev.append({"role": m["role"], "content": text})
        used += len(text)
    return list(reversed(kept_rev))


def format_recent_transcript(
    messages: Sequence[dict[str, str]],
    *,
    max_messages: int = 8,
    char_budget: int = 4_000,
    per_message_cap: int = 1_200,
) -> str:
    """Format the most recent conversation turns for routing within a character budget."""
    slice_ = list(messages[-max_messages:])
    lines_rev: list[str] = []
    used = 0
    for m in reversed(slice_):
        text = m["content"].strip()
        if len(text) > per_message_cap:
            text = clip_text(text, per_message_cap)
        line = f"{m['role']}: {text}"
        if lines_rev and used + len(line) > char_budget:
            break
        lines_rev.append(line)
        used += len(line) + 1
    return "\n".join(reversed(lines_rev))


def format_memory_block(summary: str | None, *, grounded_mode: bool = False) -> str:
    if not summary or not summary.strip():
        return ""
    if grounded_mode:
        return (
            "Earlier conversation context (summary of older turns in this chat — use this only "
            "to remember user preferences, names, constraints, and follow-up references; do NOT "
            "cite this summary or treat it as a website/database fact):\n"
            f"{summary.strip()}"
        )
    return (
        "Earlier conversation memory (summary of older turns prior to the messages below):\n"
        f"{summary.strip()}"
    )


def build_fallback_summary(
    existing_summary: str | None,
    evicted: Sequence[TurnRecord],
    *,
    max_chars: int = SUMMARY_MAX_CHARS,
) -> str | None:
    """Deterministic condensed fallback used only in memory when the chat model is unavailable."""
    snippets: list[str] = []
    if existing_summary and existing_summary.strip():
        snippets.append(existing_summary.strip())
    for row in evicted[-12:]:
        compact = " ".join(row.content.split())
        if compact:
            snippets.append(f"- {row.role}: {clip_text(compact, 220)}")
    if not snippets:
        return None
    return clip_text("\n".join(snippets), max_chars)


def _batch_evicted(
    evicted: Sequence[TurnRecord], batch_chars: int = SUMMARY_BATCH_CHARS
) -> list[list[TurnRecord]]:
    batches: list[list[TurnRecord]] = []
    cur: list[TurnRecord] = []
    used = 0
    for row in evicted:
        text = clip_text(row.content, TURN_SNIPPET_CHARS)
        entry_len = len(row.role) + len(text) + 4
        if cur and used + entry_len > batch_chars:
            batches.append(cur)
            cur = []
            used = 0
        cur.append(row)
        used += entry_len
    if cur:
        batches.append(cur)
    return batches


async def summarize_turns(
    target: ChatTarget,
    existing_summary: str | None,
    evicted: Sequence[TurnRecord],
    *,
    max_chars: int = SUMMARY_MAX_CHARS,
) -> tuple[str | None, int | None, bool]:
    """Fold `evicted` turns into `existing_summary` via `target` in bounded batches.

    Returns `(updated_summary, last_summarized_message_id, all_batches_succeeded)`.
    """
    if not evicted:
        return existing_summary, None, True

    running = existing_summary.strip() if existing_summary and existing_summary.strip() else None
    last_id: int | None = None
    summary_cap = min(max_chars, SUMMARY_MAX_CHARS)

    for batch in _batch_evicted(evicted):
        transcript = "\n".join(
            f"{r.role.capitalize()}: {clip_text(r.content, TURN_SNIPPET_CHARS)}" for r in batch
        )
        if running:
            user_prompt = (
                f"Previous summary:\n{running}\n\n"
                f"New conversation turns to fold into memory:\n{transcript}"
            )
        else:
            user_prompt = f"Conversation turns to summarize into memory:\n{transcript}"
        try:
            resp = await target.client.chat.completions.create(
                model=target.model,
                temperature=0.1,
                max_tokens=550,
                messages=[
                    {"role": "system", "content": COMPACT_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
            )
            raw = resp.choices[0].message.content or "" if resp.choices else ""
        except Exception as exc:  # noqa: BLE001 - non-fatal fallback if GPU unavailable
            log.info("conversation_compact_failed", error=f"{type(exc).__name__}: {exc}"[:200])
            return running, last_id, False

        cleaned = clean_summary(raw, max_chars=summary_cap)
        if not cleaned:
            return running, last_id, False
        running = cleaned
        if batch[-1].id is not None:
            last_id = batch[-1].id

    return running, last_id, True


async def _persist_summary(
    *,
    db: AsyncSession | None,
    sessions: async_sessionmaker[AsyncSession] | None,
    conversation_id: str,
    summary: str,
    through_message_id: int,
) -> None:
    stmt = (
        update(Conversation)
        .where(
            Conversation.id == conversation_id,
            or_(
                Conversation.summary_through_message_id.is_(None),
                Conversation.summary_through_message_id < through_message_id,
            ),
        )
        .values(summary=summary, summary_through_message_id=through_message_id)
    )
    if db is not None:
        await db.execute(stmt)
        await db.commit()
    elif sessions is not None:
        async with sessions() as session:
            await session.execute(stmt)
            await session.commit()


def _effective_char_budget(char_budget: int, summary: str | None) -> int:
    reserve = (min(len(summary), SUMMARY_MAX_CHARS) + SUMMARY_OVERHEAD_CHARS) if summary else 0
    return max(min(char_budget, 1_200), char_budget - reserve)


async def compact_prepared_conversation(
    prepared: PreparedConversation,
    *,
    target: ChatTarget | None,
    db: AsyncSession | None = None,
    sessions: async_sessionmaker[AsyncSession] | None = None,
    char_budget: int,
    max_recent_messages: int | None = None,
) -> CompactedHistory:
    """Split a loaded Postgres conversation into rolling `summary` + recent `turns`."""
    trigger_budget = _effective_char_budget(char_budget, prepared.summary)
    evicted, kept = split_for_compaction(
        prepared.prior_turns,
        prepared.user_text,
        char_budget=trigger_budget,
        max_recent_messages=max_recent_messages,
    )
    if not evicted:
        turns = [{"role": r.role, "content": r.content} for r in kept]
        turns.append({"role": "user", "content": prepared.user_text})
        return CompactedHistory(
            summary=prepared.summary,
            turns=turns,
            summarized_through_id=prepared.summary_through_message_id,
            compacted_count=0,
        )

    # Reserve headroom when compacting so subsequent turns do not re-summarize on every message.
    summary_reserve = min(SUMMARY_MAX_CHARS, max(600, char_budget // 4))
    slack = min(2_000, char_budget // 8)
    compact_char_budget = max(min(char_budget, 1_200), char_budget - summary_reserve - slack)
    compact_max_msgs = (
        max(4, max_recent_messages - 4) if max_recent_messages is not None else None
    )
    compact_evicted, compact_kept = split_for_compaction(
        prepared.prior_turns,
        prepared.user_text,
        char_budget=compact_char_budget,
        max_recent_messages=compact_max_msgs,
    )
    if compact_evicted:
        evicted, kept = compact_evicted, compact_kept

    summary_out: str | None = prepared.summary
    through_id = prepared.summary_through_message_id
    compacted_count = 0

    if target is not None:
        new_summary, last_id, ok = await summarize_turns(target, prepared.summary, evicted)
        if new_summary and last_id is not None:
            summary_out = new_summary
            through_id = last_id
            compacted_count = len([r for r in evicted if r.id is not None and r.id <= last_id])
            if db is not None or sessions is not None:
                await _persist_summary(
                    db=db,
                    sessions=sessions,
                    conversation_id=prepared.conversation_id,
                    summary=new_summary,
                    through_message_id=last_id,
                )
        if not ok:
            remaining_evicted = (
                [r for r in evicted if r.id is None or last_id is None or r.id > last_id]
            )
            summary_out = build_fallback_summary(summary_out, remaining_evicted)
    else:
        summary_out = build_fallback_summary(prepared.summary, evicted)

    turns = [{"role": r.role, "content": r.content} for r in kept]
    turns.append({"role": "user", "content": prepared.user_text})
    return CompactedHistory(
        summary=summary_out,
        turns=turns,
        summarized_through_id=through_id,
        compacted_count=compacted_count,
    )


async def compact_stateless_messages(
    messages: Sequence[dict[str, str]],
    *,
    target: ChatTarget | None,
    char_budget: int,
    max_recent_messages: int | None = None,
    existing_summary: str | None = None,
) -> CompactedHistory:
    """Compact an in-memory message list when a caller omits `conversation_id`."""
    if not messages:
        return CompactedHistory(summary=existing_summary, turns=[])
    current = messages[-1]["content"]
    prior = [TurnRecord(role=m["role"], content=m["content"]) for m in messages[:-1]]
    trigger_budget = _effective_char_budget(char_budget, existing_summary)
    evicted, kept = split_for_compaction(
        prior, current, char_budget=trigger_budget, max_recent_messages=max_recent_messages
    )
    if not evicted:
        turns = [{"role": r.role, "content": r.content} for r in kept]
        turns.append({"role": "user", "content": current})
        return CompactedHistory(summary=existing_summary, turns=turns)

    summary_out: str | None = existing_summary
    if target is not None:
        new_summary, _, ok = await summarize_turns(target, existing_summary, evicted)
        summary_out = new_summary if (ok and new_summary) else build_fallback_summary(
            new_summary or existing_summary, evicted
        )
    else:
        summary_out = build_fallback_summary(existing_summary, evicted)

    turns = [{"role": r.role, "content": r.content} for r in kept]
    turns.append({"role": "user", "content": current})
    return CompactedHistory(summary=summary_out, turns=turns, compacted_count=len(evicted))


async def append_memory_sse(
    stream: AsyncIterator[str], summary: str | None, *, emit: bool
) -> AsyncIterator[str]:
    """Emit a `memory` SSE event right before `done` when stateless compaction updated memory."""
    if not emit or not summary:
        async for raw in stream:
            yield raw
        return
    sent = False
    async for raw in stream:
        if not sent and raw.startswith("event: done\n"):
            sent = True
            payload = json.dumps({"summary": summary}, ensure_ascii=False)
            yield f"event: memory\ndata: {payload}\n\n"
        yield raw
