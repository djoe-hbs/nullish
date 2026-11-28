"""Decides what a message needs before anything is searched (the plan's "router" role).

  chat           casual talk / questions about Smish itself: answered conversationally
  sites_overview questions about the configured websites as a whole ("which is most
                 interesting?", "what sites do you have?"): answered from the site catalog
  search         needs facts from the websites' pages: full search + live verification
  database       needs data from a connected database (counts, totals, records, or a change
                 the user asks for): answered by querying it (app/datasources/agent.py)

It also rewrites follow-ups ("and the price?") into a standalone search query.
On any failure it falls back to `search`, the safe default.
"""

import json
import re
from dataclasses import dataclass

import structlog

from app.db.models import DataSource, Site
from app.llm.chat import ChatTarget
from app.orchestrator.memory import format_recent_transcript

log = structlog.get_logger()

INTENTS = ("chat", "sites_overview", "search", "database")

ROUTER_PROMPT = """You route messages for Smish, an assistant that answers questions from \
these websites:
{sites}
{databases}
Read the conversation and decide what the user's LATEST message needs. Reply with JSON only:
{{"intent": "chat" | "sites_overview" | "search"{db_intent}, \
"query": "<standalone search query, or empty>"{db_fields}}}

- "chat": greetings, thanks, small talk, opinions or questions about you (Smish), general \
chit-chat. Needs no website content.
- "sites_overview": questions about the set of websites as a whole: which sites there are, what \
each one is about, which is most interesting/useful/popular, how they differ in general.
- "search": anything that needs facts from the websites' pages (products, prices, policies, \
services, people, addresses, details). When in doubt, choose "search".{db_rules}

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
{{"intent": "search", "query": "Is Olio in stock?"}}{db_examples}"""

DB_BLOCK = """and these connected databases:
{lines}
"""

DB_RULES = """
- "database": anything answered by querying a connected database: counts, totals, averages, \
lists of records, "how many / which / show me / top N" about the data in its tables, or a \
request to add, change or delete data or tables. Set "database" to the database's name and \
"query" to a standalone version of the request (with context from earlier messages). Set \
"change" to true ONLY when the user's own latest message asks to add/insert, update/change/\
mark/set, delete/remove, create, alter or drop something. Questions are false."""

DB_EXAMPLES = """
"how many orders are pending?" -> {{"intent": "database", "query": "How many orders are \
pending?", "database": "{name}", "change": false}}
"mark order 5 as shipped" -> {{"intent": "database", "query": "Mark order 5 as shipped", \
"database": "{name}", "change": true}}"""


@dataclass
class Route:
    intent: str
    query: str
    by: str  # "model" | "fallback"
    database: str | None = None  # intent "database": the database name the model chose
    change: bool = False  # the user asked to change data or schema


def site_lines(sites: list[Site]) -> str:
    return "\n".join(
        f"- {s.name} ({s.root_url})" + (f": {s.description}" if s.description else "")
        for s in sites
    )


def db_lines(dbs: list[DataSource]) -> str:
    return "\n".join(
        f"- {d.name} ({'PostgreSQL' if d.kind == 'postgres' else 'MySQL'} database)"
        + (f": {d.description}" if d.description else "")
        for d in dbs
    )


def router_prompt(sites: list[Site], dbs: list[DataSource] | None = None) -> str:
    if not dbs:
        return ROUTER_PROMPT.format(sites=site_lines(sites) or "(none)", databases="",
                                    db_intent="", db_fields="", db_rules="", db_examples="")
    return ROUTER_PROMPT.format(
        sites=site_lines(sites) or "(none)",
        databases=DB_BLOCK.format(lines=db_lines(dbs)),
        db_intent=' | "database"',
        db_fields=', "database": "<database name>", "change": true | false',
        db_rules=DB_RULES,
        db_examples=DB_EXAMPLES.replace("{name}", dbs[0].name.replace('"', "'")))


def parse_route_full(raw: str) -> dict | None:
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    intent = str(data.get("intent", "")).strip().lower()
    if intent not in INTENTS:
        return None
    change = data.get("change")
    return {
        "intent": intent,
        "query": str(data.get("query") or "").strip(),
        "database": str(data.get("database") or "").strip() or None,
        "change": change is True or str(change).lower() == "true",
    }


def parse_route(raw: str) -> tuple[str, str] | None:
    data = parse_route_full(raw)
    return (data["intent"], data["query"]) if data else None


async def route_message(
    chat: ChatTarget, messages: list[dict], sites: list[Site], fallback_query: str,
    dbs: list[DataSource] | None = None, *, summary: str | None = None,
) -> Route:
    convo = format_recent_transcript(
        messages, max_messages=8, char_budget=4_000, per_message_cap=1_200
    )
    summary_prefix = (
        f"Earlier conversation summary:\n{summary.strip()}\n\n"
        if summary and summary.strip()
        else ""
    )
    try:
        resp = await chat.client.chat.completions.create(
            model=chat.model,
            temperature=0,
            max_tokens=160,
            messages=[
                {"role": "system", "content": router_prompt(sites, dbs)},
                {"role": "user", "content": f"{summary_prefix}Conversation:\n{convo}\n\nJSON:"},
            ],
        )
        raw = resp.choices[0].message.content or "" if resp.choices else ""
        parsed = parse_route_full(raw)
    except Exception as exc:  # noqa: BLE001 - offline model, bad JSON...: search is safe
        log.info("router_failed", error=f"{type(exc).__name__}: {exc}"[:200])
        parsed = None
    if parsed is None or (parsed["intent"] == "database" and not dbs):
        # No usable answer: search the sites (or, with only databases, query one).
        if not sites and dbs:
            return Route("database", fallback_query, "fallback")
        return Route("search", fallback_query, "fallback")
    if parsed["intent"] in ("search", "sites_overview") and not sites and dbs:
        parsed["intent"] = "database"  # no websites to search: the databases are the sources
    return Route(parsed["intent"], parsed["query"] or fallback_query, "model",
                 parsed["database"], parsed["change"] and parsed["intent"] == "database")
