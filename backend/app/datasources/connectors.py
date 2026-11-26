"""Connections to user databases (Postgres via asyncpg, MySQL/MariaDB via asyncmy).

Safety at this layer (the SQL gate in sqlgate.py runs first):
- reads run inside READ ONLY transactions, so the database itself refuses any write
- one statement per execution: Postgres uses prepared statements (the extended protocol rejects
  "a; b"), and MySQL connects without the multi-statement flag
- statement timeouts, connect timeouts, and a row cap on every result
- the host was checked by hosts.resolve_db_host and the connection goes to that pinned IP
"""

import asyncio
import datetime as dt
import re
import ssl
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.config import Settings
from app.datasources.hosts import resolve_db_host
from app.db.models import DataSource
from app.security.crypto import decrypt


class DbError(RuntimeError):
    """A problem worth showing to the user / model (connect failure, SQL error, timeout)."""


@dataclass
class QueryResult:
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    truncated: bool = False  # more rows existed than the row limit
    rowcount: int | None = None  # rows changed by a write
    ms: int = 0


@dataclass
class Preview:
    rowcount: int | None
    before: QueryResult | None = None
    after: QueryResult | None = None
    note: str | None = None  # e.g. "can't preview: MySQL schema changes commit immediately"


def to_json_value(v: Any) -> Any:
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, Decimal):
        return format(v, "f")
    if isinstance(v, (dt.datetime, dt.date, dt.time)):
        return v.isoformat()
    if isinstance(v, dt.timedelta):
        return str(v)
    if isinstance(v, (bytes, bytearray, memoryview)):
        return f"<binary, {len(bytes(v))} bytes>"
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, (list, tuple)):
        return [to_json_value(x) for x in v]
    if isinstance(v, dict):
        return {str(k): to_json_value(x) for k, x in v.items()}
    return str(v)


def _ssl_context(mode: str) -> ssl.SSLContext | None:
    if mode == "verify":
        return ssl.create_default_context()
    if mode == "require":
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE  # encrypted, server identity not verified
        return ctx
    return None


def quote_ident(kind: str, name: str) -> str:
    if kind == "mysql":
        return "`" + name.replace("`", "``") + "`"
    return '"' + name.replace('"', '""') + '"'


def _first_line(exc: BaseException) -> str:
    args = getattr(exc, "args", ())
    if len(args) == 2 and isinstance(args[0], int):  # MySQL: (1146, "Table 'x' doesn't exist")
        msg = str(args[1])
    else:
        msg = str(exc).strip() or type(exc).__name__
    return msg.splitlines()[0][:400]


# ---------------------------------------------------------------------------------------------
# Postgres
# ---------------------------------------------------------------------------------------------

PG_TABLES = """
SELECT n.nspname, c.relname, c.relkind, c.reltuples::bigint, obj_description(c.oid, 'pg_class')
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind IN ('r', 'v', 'm', 'p') AND NOT c.relispartition
  AND n.nspname NOT IN ('pg_catalog', 'information_schema') AND n.nspname NOT LIKE 'pg_toast%'
  AND n.nspname NOT LIKE 'pg_temp%' AND has_table_privilege(c.oid, 'SELECT')
ORDER BY 1, 2"""
PG_COLUMNS = """
SELECT n.nspname, c.relname, a.attname, a.attnum, format_type(a.atttypid, a.atttypmod),
       NOT a.attnotnull, col_description(c.oid, a.attnum)
FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE a.attnum > 0 AND NOT a.attisdropped AND c.relkind IN ('r', 'v', 'm', 'p')
  AND NOT c.relispartition AND n.nspname NOT IN ('pg_catalog', 'information_schema')
  AND n.nspname NOT LIKE 'pg_toast%' AND n.nspname NOT LIKE 'pg_temp%'
  AND has_table_privilege(c.oid, 'SELECT')
ORDER BY 1, 2, 4"""
PG_PKS = """
SELECT n.nspname, c.relname, a.attname
FROM pg_index i JOIN pg_class c ON c.oid = i.indrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = ANY(i.indkey)
WHERE i.indisprimary AND n.nspname NOT IN ('pg_catalog', 'information_schema')
  AND n.nspname NOT LIKE 'pg_toast%' AND n.nspname NOT LIKE 'pg_temp%'"""
PG_FKS = """
SELECT n.nspname, c.relname, a.attname, fn.nspname, fc.relname, fa.attname
FROM pg_constraint k
JOIN pg_class c ON c.oid = k.conrelid JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_class fc ON fc.oid = k.confrelid JOIN pg_namespace fn ON fn.oid = fc.relnamespace
CROSS JOIN LATERAL unnest(k.conkey, k.confkey) AS u(ck, fk)
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = u.ck
JOIN pg_attribute fa ON fa.attrelid = fc.oid AND fa.attnum = u.fk
WHERE k.contype = 'f'"""


class PostgresConn:
    kind = "postgres"
    default_schema = "public"

    def __init__(self, conn, timeout_sec: int):
        self.conn = conn
        self.timeout_ms = int(timeout_sec * 1000)

    @classmethod
    async def open(cls, src: DataSource, ip: str, password: str, settings: Settings):
        import asyncpg

        mode = src.ssl_mode
        ssl_arg: Any = {"disable": False, "prefer": "prefer"}.get(mode) or _ssl_context(mode)
        try:
            conn = await asyncpg.connect(
                host=src.host if mode == "verify" else ip,  # verify needs the real name (TLS)
                port=src.port, user=src.username, password=password, database=src.database,
                ssl=ssl_arg, timeout=settings.db_connect_timeout_sec,
                server_settings={"application_name": "smish"}, statement_cache_size=0,
            )
        except Exception as exc:  # noqa: BLE001
            raise DbError(f"couldn't connect: {_first_line(exc)}") from exc
        return cls(conn, src.timeout_sec)

    async def close(self) -> None:
        await self.conn.close(timeout=3)

    async def version(self) -> str:
        return str(await self.conn.fetchval("SELECT version()"))[:200]

    async def _timeout(self) -> None:
        await self.conn.execute(f"SET LOCAL statement_timeout = {self.timeout_ms}")

    async def read(self, sql: str, limit: int) -> QueryResult:
        t = time.perf_counter()
        try:
            async with self.conn.transaction(readonly=True):
                await self._timeout()
                stmt = await self.conn.prepare(sql)
                cols = [a.name for a in stmt.get_attributes()]
                cur = await stmt.cursor()
                rows = await cur.fetch(limit + 1)
        except Exception as exc:  # noqa: BLE001
            raise DbError(_first_line(exc)) from exc
        return QueryResult(cols, [[to_json_value(v) for v in r] for r in rows[:limit]],
                           len(rows) > limit, None, round((time.perf_counter() - t) * 1000))

    async def _run_write(self, sql: str) -> QueryResult:
        stmt = await self.conn.prepare(sql)
        cols = [a.name for a in stmt.get_attributes()]
        rows = await stmt.fetch()
        m = re.search(r"(\d+)\s*$", stmt.get_statusmsg() or "")
        return QueryResult(cols, [[to_json_value(v) for v in r] for r in rows[:50]],
                           len(rows) > 50, int(m.group(1)) if m else None)

    async def execute(self, sql: str) -> QueryResult:
        t = time.perf_counter()
        try:
            async with self.conn.transaction():
                await self._timeout()
                res = await self._run_write(sql)
        except Exception as exc:  # noqa: BLE001
            raise DbError(_first_line(exc)) from exc
        res.ms = round((time.perf_counter() - t) * 1000)
        return res

    async def preview(self, sql: str, kind: str, target: str | None, where: str | None,
                      sample: int = 5) -> Preview:
        """Runs the change inside a transaction that is always rolled back."""
        tr = self.conn.transaction()
        await tr.start()
        try:
            await self._timeout()
            before = await self._sample(target, where, sample) if target else None
            res = await self._run_write(sql)
            after = await self._sample(target, where, sample) if target else None
            return Preview(res.rowcount, before, after)
        except Exception as exc:  # noqa: BLE001
            raise DbError(_first_line(exc)) from exc
        finally:
            await tr.rollback()

    async def _sample(self, target: str, where: str, n: int) -> QueryResult:
        stmt = await self.conn.prepare(f"SELECT * FROM {target} WHERE {where} LIMIT {n}")
        rows = await stmt.fetch()
        return QueryResult([a.name for a in stmt.get_attributes()],
                           [[to_json_value(v) for v in r] for r in rows])

    async def introspect(self) -> dict:
        try:
            async with self.conn.transaction(readonly=True):
                await self._timeout()
                return {
                    "tables": [tuple(r) for r in await self.conn.fetch(PG_TABLES)],
                    "columns": [tuple(r) for r in await self.conn.fetch(PG_COLUMNS)],
                    "pks": [tuple(r) for r in await self.conn.fetch(PG_PKS)],
                    "fks": [tuple(r) for r in await self.conn.fetch(PG_FKS)],
                }
        except Exception as exc:  # noqa: BLE001
            raise DbError(f"couldn't read the tables: {_first_line(exc)}") from exc


# ---------------------------------------------------------------------------------------------
# MySQL / MariaDB
# ---------------------------------------------------------------------------------------------

MY_TABLES = """
SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE, TABLE_ROWS, TABLE_COMMENT, ENGINE
FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() ORDER BY TABLE_NAME"""
MY_COLUMNS = """
SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, ORDINAL_POSITION, COLUMN_TYPE,
       IS_NULLABLE = 'YES', COLUMN_COMMENT, COLUMN_KEY = 'PRI'
FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE()
ORDER BY TABLE_NAME, ORDINAL_POSITION"""
MY_FKS = """
SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, REFERENCED_TABLE_SCHEMA, REFERENCED_TABLE_NAME,
       REFERENCED_COLUMN_NAME
FROM information_schema.KEY_COLUMN_USAGE
WHERE TABLE_SCHEMA = DATABASE() AND REFERENCED_TABLE_NAME IS NOT NULL"""


class MySQLConn:
    kind = "mysql"

    def __init__(self, conn, timeout_sec: int, database: str):
        self.conn = conn
        self.timeout_sec = timeout_sec
        self.default_schema = database

    @classmethod
    async def open(cls, src: DataSource, ip: str, password: str, settings: Settings):
        import asyncmy

        mode = src.ssl_mode
        try:
            conn = await asyncio.wait_for(asyncmy.connect(
                host=src.host if mode == "verify" else ip, port=src.port, user=src.username,
                password=password, database=src.database, charset="utf8mb4", autocommit=False,
                connect_timeout=int(settings.db_connect_timeout_sec),
                ssl=_ssl_context(mode) if mode in ("require", "verify") else None,
            ), settings.db_connect_timeout_sec + 2)
        except Exception as exc:  # noqa: BLE001
            raise DbError(f"couldn't connect: {_first_line(exc)}") from exc
        self = cls(conn, src.timeout_sec, src.database)
        try:
            await self._session_limits(src.timeout_sec)
        except Exception as exc:  # noqa: BLE001
            conn.close()
            raise DbError(f"couldn't connect: {_first_line(exc)}") from exc
        return self

    async def _session_limits(self, timeout_sec: int) -> None:
        """Statement time limit. MySQL calls it max_execution_time (ms, SELECT only), MariaDB
        max_statement_time (seconds, all statements); servers without either still get the
        client-side limit in _guarded()."""
        async with self.conn.cursor() as cur:
            await cur.execute("SELECT VERSION()")
            version = str((await cur.fetchone())[0]).lower()
            options = [f"SET SESSION max_statement_time = {timeout_sec}",
                       f"SET SESSION max_execution_time = {timeout_sec * 1000}"]
            if "mariadb" not in version:
                options.reverse()
            for stmt in options:
                try:
                    await cur.execute(stmt)
                    break
                except Exception as exc:  # noqa: BLE001
                    if getattr(exc, "args", (None,))[0] != 1193:  # 1193 = unknown variable
                        raise
            for stmt in (f"SET SESSION innodb_lock_wait_timeout = {max(1, timeout_sec)}",
                         f"SET SESSION lock_wait_timeout = {max(1, timeout_sec)}"):
                try:
                    await cur.execute(stmt)
                except Exception as exc:  # noqa: BLE001
                    if getattr(exc, "args", (None,))[0] != 1193:
                        raise

    async def close(self) -> None:
        self.conn.close()

    async def version(self) -> str:
        async with self.conn.cursor() as cur:
            await cur.execute("SELECT VERSION()")
            return str((await cur.fetchone())[0])[:200]

    async def _guarded(self, coro):
        try:
            return await asyncio.wait_for(coro, self.timeout_sec + 2)
        except TimeoutError as exc:
            self.conn.close()  # a statement still running is abandoned with the connection
            raise DbError(f"the query took longer than {self.timeout_sec} s") from exc
        except DbError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise DbError(_first_line(exc)) from exc

    async def read(self, sql: str, limit: int) -> QueryResult:
        t = time.perf_counter()

        async def run():
            async with self.conn.cursor() as cur:
                await cur.execute("START TRANSACTION READ ONLY")
                try:
                    await cur.execute(sql)
                    cols = [d[0] for d in cur.description or []]
                    rows = await cur.fetchmany(limit + 1)
                finally:
                    await cur.execute("ROLLBACK")
                return cols, rows

        cols, rows = await self._guarded(run())
        return QueryResult(cols, [[to_json_value(v) for v in r] for r in rows[:limit]],
                           len(rows) > limit, None, round((time.perf_counter() - t) * 1000))

    async def _rows(self, cur) -> tuple[list[str], list[list[Any]]]:
        if not cur.description:
            return [], []
        rows = await cur.fetchmany(51)
        return [d[0] for d in cur.description], [[to_json_value(v) for v in r] for r in rows]

    async def execute(self, sql: str) -> QueryResult:
        t = time.perf_counter()

        async def run():
            async with self.conn.cursor() as cur:
                await cur.execute("START TRANSACTION")
                try:
                    count = await cur.execute(sql)
                    cols, rows = await self._rows(cur)
                    await cur.execute("COMMIT")
                except Exception:
                    await cur.execute("ROLLBACK")
                    raise
                return QueryResult(cols, rows[:50], len(rows) > 50, count)

        res = await self._guarded(run())
        res.ms = round((time.perf_counter() - t) * 1000)
        return res

    async def _transactional(self, target: str | None) -> bool:
        """MySQL rolls back only InnoDB (and similar) tables; MyISAM changes are permanent."""
        if not target:
            return False
        name = target.strip("`").split("`.`")[-1].split(".")[-1].strip("`")
        async with self.conn.cursor() as cur:
            await cur.execute("SELECT ENGINE FROM information_schema.TABLES "
                              "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s", (name,))
            row = await cur.fetchone()
        return bool(row and str(row[0]).lower() in ("innodb", "ndbcluster", "rocksdb"))

    async def preview(self, sql: str, kind: str, target: str | None, where: str | None,
                      sample: int = 5) -> Preview:
        if kind == "ddl":
            return Preview(None, note="MySQL applies schema changes immediately, so this one "
                                      "can't be tried out first.")
        if not await self._guarded(self._transactional(target)):
            return Preview(None, note="This table can't be changed in a trial run "
                                      "(non-transactional storage engine), so there's no preview.")

        async def run():
            async with self.conn.cursor() as cur:
                await cur.execute("START TRANSACTION")
                try:
                    q = f"SELECT * FROM {target} WHERE {where} LIMIT {sample}"
                    await cur.execute(q)
                    before = QueryResult(*await self._rows(cur))
                    count = await cur.execute(sql)
                    await cur.execute(q)
                    after = QueryResult(*await self._rows(cur))
                finally:
                    await cur.execute("ROLLBACK")
                return Preview(count, before, after)

        return await self._guarded(run())

    async def introspect(self) -> dict:
        async def run():
            out = {}
            async with self.conn.cursor() as cur:
                await cur.execute("START TRANSACTION READ ONLY")
                for key, q in (("tables", MY_TABLES), ("columns", MY_COLUMNS), ("fks", MY_FKS)):
                    await cur.execute(q)
                    out[key] = [tuple(r) for r in await cur.fetchall()]
                await cur.execute("ROLLBACK")
            out["pks"] = [(c[0], c[1], c[2]) for c in out["columns"] if c[7]]
            out["tables"] = [(t[0], t[1], "v" if "VIEW" in str(t[2]) else "r", t[3], t[4])
                             for t in out["tables"]]
            out["columns"] = [c[:7] for c in out["columns"]]
            return out

        return await self._guarded(run())


CONNECTORS = {"postgres": PostgresConn, "mysql": MySQLConn}
DEFAULT_PORTS = {"postgres": 5432, "mysql": 3306}


@asynccontextmanager
async def connect(src: DataSource, settings: Settings, *, password: str | None = None):
    """Opens one connection to a data source (checked host, decrypted password)."""
    ip = await resolve_db_host(src.host, settings)
    if password is None:
        password = decrypt(src.password_encrypted) if src.password_encrypted else ""
    conn = await CONNECTORS[src.kind].open(src, ip, password, settings)
    try:
        yield conn
    finally:
        try:
            await conn.close()
        except Exception:  # noqa: BLE001, S110 - closing a broken connection
            pass
