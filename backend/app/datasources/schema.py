"""What Smish knows about a connected database's structure (the "retrieval" part of DB RAG).

refresh_schema() reads tables, columns, keys, comments and row estimates, plus a few sample
values for short enum-like text columns (so the model writes WHERE status = 'shipped', not
'Shipped'). The user's notes and hidden flags survive every refresh.

schema_context() picks what goes into the prompt: the whole schema when it's small, otherwise the
tables most relevant to the question plus the tables they reference (so joins still work).
Hidden tables and columns never appear.
"""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.datasources.connectors import DbError, connect, quote_ident
from app.datasources.sqlgate import Hidden
from app.db.models import DataSource, DsSchemaItem
from app.llm.embed import EmbedClient

log = structlog.get_logger()

TEXT_TYPES = re.compile(r"char|text|enum|set\(|citext|name", re.I)
SENSITIVE = re.compile(r"pass|secret|token|e-?mail|phone|mobile|key|hash|salt|ssn|card|iban|"
                       r"address|birth|dob", re.I)
MAX_SAMPLE_COLUMNS = 120
MAX_SAMPLES = 8


@dataclass
class Column:
    name: str
    data_type: str
    nullable: bool = True
    is_pk: bool = False
    fk_ref: str | None = None
    comment: str | None = None
    note: str | None = None
    hidden: bool = False
    samples: list = field(default_factory=list)


@dataclass
class Table:
    schema: str
    name: str
    display: str  # how SQL should refer to it ("orders" or "sales.orders")
    kind: str = "table"  # table | view
    rows: int | None = None
    comment: str | None = None
    note: str | None = None
    hidden: bool = False
    columns: list[Column] = field(default_factory=list)


def display_name(schema: str, table: str, default_schema: str) -> str:
    return table if schema == default_schema else f"{schema}.{table}"


def default_schema_of(src: DataSource) -> str:
    return src.database if src.kind == "mysql" else "public"


async def refresh_schema(db: AsyncSession, src: DataSource, settings: Settings,
                         embedder: EmbedClient | None = None) -> int:
    """Re-reads the database structure. Returns the number of tables."""
    keep = {(i.schema_name, i.table_name, i.column_name): (i.note, i.hidden)
            for i in (await db.execute(
                select(DsSchemaItem).where(DsSchemaItem.source_id == src.id))).scalars()}
    try:
        async with connect(src, settings) as conn:
            raw = await conn.introspect()
            tables = build_tables(raw, conn.default_schema)
            for t in tables:  # restore notes / hidden flags before choosing sample columns
                t.note, t.hidden = keep.get((t.schema, t.name, None), (None, False))
                for c in t.columns:
                    c.note, c.hidden = keep.get((t.schema, t.name, c.name), (None, False))
            await collect_samples(conn, src.kind, tables)
    except DbError as exc:
        src.schema_error = str(exc)
        await db.commit()
        raise

    await db.execute(delete(DsSchemaItem).where(DsSchemaItem.source_id == src.id))
    items: list[DsSchemaItem] = []
    for t in tables:
        items.append(DsSchemaItem(
            source_id=src.id, schema_name=t.schema, table_name=t.name, column_name=None,
            data_type=t.kind, row_estimate=t.rows, comment=t.comment, note=t.note,
            hidden=t.hidden))
        for i, c in enumerate(t.columns):
            items.append(DsSchemaItem(
                source_id=src.id, schema_name=t.schema, table_name=t.name, column_name=c.name,
                ordinal=i, data_type=c.data_type, nullable=c.nullable, is_pk=c.is_pk,
                fk_ref=c.fk_ref, comment=c.comment, note=c.note, hidden=c.hidden,
                sample_values=c.samples))
    db.add_all(items)
    await db.flush()
    if embedder is not None and len(tables) > settings.db_schema_full_tables:
        await embed_tables(db, src.id, tables, embedder)
    src.schema_refreshed_at = datetime.now(UTC)
    src.schema_error = None
    await db.commit()
    log.info("schema_refreshed", source=src.id, tables=len(tables))
    return len(tables)


def build_tables(raw: dict, default_schema: str) -> list[Table]:
    pks = {(s, t, c) for s, t, c in raw["pks"]}
    fks = {(s, t, c): display_name(fs, ft, default_schema) + "." + fc
           for s, t, c, fs, ft, fc in raw["fks"]}
    by_key: dict[tuple[str, str], Table] = {}
    for schema, name, relkind, rows, comment, *_ in raw["tables"]:
        by_key[(schema, name)] = Table(
            schema, name, display_name(schema, name, default_schema),
            "view" if relkind in ("v", "m") else "table",
            int(rows) if rows is not None and int(rows) >= 0 else None, comment or None)
    for schema, table, col, _pos, dtype, nullable, comment in raw["columns"]:
        t = by_key.get((schema, table))
        if t is None:
            continue
        t.columns.append(Column(col, str(dtype), bool(nullable), (schema, table, col) in pks,
                                fks.get((schema, table, col)), comment or None))
    return list(by_key.values())


async def collect_samples(conn, kind: str, tables: list[Table]) -> None:
    done = 0
    for t in tables:
        if t.hidden or t.kind != "table":  # row estimates can read 0 on fresh tables
            continue
        for c in t.columns:
            if done >= MAX_SAMPLE_COLUMNS:
                return
            if c.hidden or c.is_pk or c.fk_ref or not TEXT_TYPES.search(c.data_type) \
                    or SENSITIVE.search(c.name):
                continue
            done += 1
            col = quote_ident(kind, c.name)
            tbl = ".".join(quote_ident(kind, p) for p in (t.schema, t.name))
            try:
                # Sampled from the first 10,000 rows so big tables stay quick.
                res = await conn.read(
                    f"SELECT v, COUNT(*) FROM (SELECT {col} AS v FROM {tbl} WHERE {col} IS NOT "
                    f"NULL LIMIT 10000) s GROUP BY v ORDER BY 2 DESC LIMIT {MAX_SAMPLES + 1}",
                    MAX_SAMPLES + 1)
            except DbError:
                continue
            values = [r[0] for r in res.rows]
            repeats = any(int(r[1]) > 1 for r in res.rows)
            # Only category-like columns: a handful of short values that repeat across rows.
            # Values that are each unique (people's names, titles, emails) are never stored.
            if 0 < len(values) <= MAX_SAMPLES and repeats and all(
                    isinstance(v, str) and len(v) <= 40 for v in values):
                c.samples = sorted(values)


def table_text(t: Table) -> str:
    cols = ", ".join(c.name + (f" ({c.note})" if c.note else "") for c in t.columns
                     if not c.hidden)
    extra = " ".join(x for x in (t.comment, t.note) if x)
    return f"Table {t.display}. {extra}\nColumns: {cols}"


async def embed_tables(db: AsyncSession, source_id: int, tables: list[Table],
                       embedder: EmbedClient) -> None:
    visible = [t for t in tables if not t.hidden]
    try:
        vectors = await embedder.embed([table_text(t) for t in visible])
    except Exception as exc:  # noqa: BLE001 - GPU off: keyword ranking is used instead
        log.info("schema_embed_skipped", source=source_id, error=str(exc)[:120])
        return
    rows = {(i.schema_name, i.table_name): i for i in (await db.execute(
        select(DsSchemaItem).where(DsSchemaItem.source_id == source_id,
                                   DsSchemaItem.column_name.is_(None)))).scalars()}
    for t, v in zip(visible, vectors, strict=True):
        item = rows.get((t.schema, t.name))
        if item is not None:
            item.embedding = v


async def load_schema(db: AsyncSession, src: DataSource) -> list[Table]:
    items = (await db.execute(
        select(DsSchemaItem).where(DsSchemaItem.source_id == src.id)
        .order_by(DsSchemaItem.schema_name, DsSchemaItem.table_name,
                  DsSchemaItem.column_name.isnot(None), DsSchemaItem.ordinal))).scalars().all()
    default = default_schema_of(src)
    tables: dict[tuple[str, str], Table] = {}
    for i in items:
        key = (i.schema_name, i.table_name)
        if i.column_name is None:
            tables[key] = Table(i.schema_name, i.table_name,
                                display_name(i.schema_name, i.table_name, default),
                                i.data_type or "table", i.row_estimate, i.comment, i.note,
                                i.hidden)
        elif key in tables:
            tables[key].columns.append(Column(
                i.column_name, i.data_type or "", bool(i.nullable), i.is_pk, i.fk_ref,
                i.comment, i.note, i.hidden, list(i.sample_values or [])))
    return list(tables.values())


def hidden_of(tables: list[Table]) -> Hidden:
    h = Hidden()
    for t in tables:
        if t.hidden:
            h.tables.add(t.name.lower())
        cols = {c.name.lower() for c in t.columns if c.hidden}
        if cols:
            h.columns[t.name.lower()] = cols
    return h


def render_table(t: Table) -> str:
    head = f"{t.kind.capitalize()} {t.display}"
    if t.rows:  # estimates; MySQL often reports 0 for small, fresh tables
        head += f" (~{t.rows} rows)"
    desc = " — ".join(x for x in (t.comment, t.note) if x)
    lines = [head + (f": {desc}" if desc else "")]
    for c in t.columns:
        if c.hidden:
            continue
        bits = [c.name, c.data_type]
        if c.is_pk:
            bits.append("PK")
        if c.fk_ref:
            bits.append(f"-> {c.fk_ref}")
        line = "  " + " ".join(bits)
        if c.samples:
            line += "  values: " + ", ".join(repr(v) for v in c.samples)
        info = " — ".join(x for x in (c.comment, c.note) if x)
        if info:
            line += f"  ({info})"
        lines.append(line)
    return "\n".join(lines)


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2}


def _stem(w: str) -> str:
    return w[:-1] if w.endswith("s") and len(w) > 3 else w


async def pick_tables(tables: list[Table], question: str, settings: Settings, *,
                      db: AsyncSession | None = None, source_id: int | None = None,
                      embedder: EmbedClient | None = None) -> list[Table]:
    visible = [t for t in tables if not t.hidden]
    if len(visible) <= settings.db_schema_full_tables:
        return visible
    ranked: list[Table] = []
    if embedder is not None and db is not None and source_id is not None:
        try:
            qv = await embedder.embed_query(question)
            rows = (await db.execute(
                select(DsSchemaItem.schema_name, DsSchemaItem.table_name)
                .where(DsSchemaItem.source_id == source_id, DsSchemaItem.column_name.is_(None),
                       DsSchemaItem.hidden.is_(False), DsSchemaItem.embedding.isnot(None))
                .order_by(DsSchemaItem.embedding.cosine_distance(qv))
                .limit(settings.db_schema_top_tables))).all()
            by_key = {(t.schema, t.name): t for t in visible}
            ranked = [by_key[k] for k in map(tuple, rows) if k in by_key]
        except Exception as exc:  # noqa: BLE001 - fall back to keyword ranking
            log.info("schema_vector_rank_failed", error=str(exc)[:120])
    if not ranked:
        q = {_stem(w) for w in _words(question)}

        def score(t: Table) -> int:
            names = {_stem(w) for w in _words(t.name)}
            cols = {_stem(w) for c in t.columns if not c.hidden
                    for w in _words(f"{c.name} {c.note or ''} {' '.join(map(str, c.samples))}")}
            other = {_stem(w) for w in _words(f"{t.comment or ''} {t.note or ''}")}
            return 3 * len(q & names) + len(q & cols) + len(q & other)

        ranked = sorted(visible, key=score, reverse=True)[:settings.db_schema_top_tables]
    picked = {(t.schema, t.name) for t in ranked}
    by_display = {t.display: t for t in visible}
    for t in list(ranked):  # tables they reference, so joins can be written
        for c in t.columns:
            if c.fk_ref and not c.hidden:
                ref = by_display.get(c.fk_ref.rsplit(".", 1)[0])
                if ref is not None and (ref.schema, ref.name) not in picked:
                    picked.add((ref.schema, ref.name))
                    ranked.append(ref)
    return ranked


def table_names(tables: list[Table]) -> str:
    return ", ".join(t.display for t in tables if not t.hidden)
