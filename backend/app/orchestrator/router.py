"""Decides what a message needs before anything is searched (the plan's "router" role).

  chat           casual talk / questions about Nullish itself: answered conversationally
  sites_overview questions about the configured websites as a whole ("which is most
                 interesting?", "what sites do you have?"): answered from the site catalog
  search         needs facts from the websites' pages: full search + live verification

It also rewrites follow-ups ("and the price?") into a standalone search query.
On any failure it falls back to `search`, the safe default.
"""

import json
import re
from dataclasses import dataclass

import structlog

from app.db.models import Site
from app.llm.chat import ChatTarget

log = structlog.get_logger()

INTENTS = ("chat", "sites_overview", "search")

ROUTER_PROMPT = """You route messages for Nullish, an assistant that answers questions from \
these websites:
{sites}

Read the conversation and decide what the user's LATEST message needs. Reply with JSON only:
{{"intent": "chat" | "sites_overview" | "search", "query": "<standalone search query, or empty>"}}

- "chat": greetings, thanks, small talk, opinions or questions about you (Nullish), general \
chit-chat. Needs no website content.
- "sites_overview": questions about the set of websites as a whole: which sites there are, what \
each one is about, which is most interesting/useful/popular, how they differ in general.
- "search": anything that needs facts from the websites' pages (products, prices, policies, \
services, people, addresses, details). When in doubt, choose "search".

For "search", write "query" as a standalone question that includes context from earlier \
messages. Fix obvious spelling mistakes. For other intents use an empty query.

Examples:
"hello!" -> {{"intent": "chat", "query": ""}}
"tell me a joke" -> {{"intent": "chat", "query": ""}}
"what's your name?" -> {{"intent": "chat", "query": ""}}
"which sites can you answer from?" -> {{"intent": "sites_overview", "query": ""}}
"what's the most interesting site among these?" -> {{"intent": "sites_overview", "query": ""}}
"how are these sites different?" -> {{"intent": "sites_overview", "query": ""}}
"what is each site about?" -> {{"intent": "sites_overview", "query": ""}}
"do any of these sites sell guitars?" -> {{"intent": "search", "query": "guitars for sale"}}
"what is the refund policy?" -> {{"intent": "search", "query": "refund policy"}}
after "How much is Olio?": "is it in stock?" -> \
{{"intent": "search", "query": "Is Olio in stock?"}}"""


@dataclass
class Route:
    intent: str
    query: str
    by: str  # "model" | "fallback"


def site_lines(sites: list[Site]) -> str:
    return "\n".join(
        f"- {s.name} ({s.root_url})" + (f": {s.description}" if s.description else "")
        for s in sites
    )


def parse_route(raw: str) -> tuple[str, str] | None:
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    intent = str(data.get("intent", "")).strip().lower()
    if intent not in INTENTS:
        return None
    return intent, str(data.get("query") or "").strip()


async def route_message(
    chat: ChatTarget, messages: list[dict], sites: list[Site], fallback_query: str
) -> Route:
    convo = "\n".join(
        f"{m['role']}: {m['content'][:600]}" for m in messages[-6:]
    )
    try:
        resp = await chat.client.chat.completions.create(
            model=chat.model,
            temperature=0,
            max_tokens=120,
            messages=[
                {"role": "system", "content": ROUTER_PROMPT.format(sites=site_lines(sites))},
                {"role": "user", "content": f"Conversation:\n{convo}\n\nJSON:"},
            ],
        )
        raw = resp.choices[0].message.content or "" if resp.choices else ""
        parsed = parse_route(raw)
    except Exception as exc:  # noqa: BLE001 - offline model, bad JSON...: search is safe
        log.info("router_failed", error=f"{type(exc).__name__}: {exc}"[:200])
        parsed = None
    if parsed is None:
        return Route("search", fallback_query, "fallback")
    intent, query = parsed
    return Route(intent, query or fallback_query, "model")
