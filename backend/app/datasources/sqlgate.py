"""The safety gate every statement passes before it reaches a connected database.

Layer 1 of several (see docs/db-connections-plan.md). Others: read-only transactions for reads,
single-statement execution (prepared statements / no multi-statement flag), timeouts, row caps,
the approval flow, and the database user's own privileges.

Default-deny: only statement types on the allow-list get through, everything else is refused.

  read   SELECT / UNION / ... (no writes anywhere inside, not even in a CTE)
  write  INSERT / UPDATE / DELETE / MERGE (UPDATE and DELETE need a WHERE clause)
  ddl    CREATE / ALTER / DROP / TRUNCATE of tables, views, indexes, schemas, sequences
         (and SELECT ... INTO, which creates a table)
  blocked everything else: GRANT/REVOKE, roles and users, functions/triggers/extensions,
         COPY / LOAD DATA / INTO OUTFILE / file-reading functions, SET, CALL, DO, ...
"""

import hashlib
import re
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

DIALECTS = {"postgres": "postgres", "mysql": "mysql"}

READ_TOPS = (exp.Select, exp.Union, exp.Intersect, exp.Except, exp.Subquery)
WRITE_NODES = (exp.Insert, exp.Update, exp.Delete, exp.Merge)
DDL_TOPS = (exp.Create, exp.Alter, exp.Drop, exp.TruncateTable)
DDL_KINDS = {"TABLE", "VIEW", "INDEX", "SCHEMA", "SEQUENCE", "MATERIALIZED VIEW", "COLUMN"}

# Functions that reach outside the data: files, other servers, server control, or run SQL text
# that the gate never sees (which would bypass it).
BLOCKED_FUNCTIONS = re.compile(
    r"^(pg_read_file|pg_read_binary_file|pg_ls_\w+|pg_stat_file|pg_file_\w+|lo_\w+|dblink\w*|"
    r"pg_terminate_backend|pg_cancel_backend|pg_reload_conf|pg_rotate_logfile|pg_promote|"
    r"pg_switch_wal|pg_create_restore_point|set_config|pg_logical_\w+|pg_replication_\w+|"
    r"query_to_xml\w*|cursor_to_xml\w*|table_to_xml\w*|schema_to_xml\w*|database_to_xml\w*|"
    r"ts_stat|xpath_table|load_file|sys_exec|sys_eval|sys_get|sys_set)$",
    re.I,
)
# Catalogs that hold credentials. The database user normally can't read them anyway.
BLOCKED_TABLES = {"pg_authid", "pg_shadow", "pg_user_mapping", "pg_user_mappings", "user",
                  "pg_largeobject"}
BLOCKED_TABLE_SCHEMAS = {("mysql", "user"), ("pg_catalog", "pg_authid"),
                         ("pg_catalog", "pg_shadow")}


class GateError(ValueError):
    pass


@dataclass
class Hidden:
    """Tables and columns the user has hidden from the model (lower-case names)."""

    tables: set[str] = field(default_factory=set)
    columns: dict[str, set[str]] = field(default_factory=dict)  # table -> hidden columns


@dataclass
class GateResult:
    kind: str  # read | write | ddl | blocked
    sql: str  # the SQL to execute (reads may get a LIMIT added)
    reason: str = ""  # why it was blocked
    tables: list[str] = field(default_factory=list)
    target: str | None = None  # table changed by an UPDATE / DELETE (for previews)
    where: str | None = None  # its WHERE clause, as SQL


def sql_hash(sql: str) -> str:
    return hashlib.sha256(sql.strip().encode()).hexdigest()


def _blocked(sql: str, reason: str) -> GateResult:
    return GateResult("blocked", sql, reason)


def _kind_of(node: exp.Expression) -> str | None:
    kind = node.args.get("kind")
    return str(kind).upper() if kind else None


def check(sql: str, dialect_kind: str, *, row_limit: int, hidden: Hidden | None = None
          ) -> GateResult:
    hidden = hidden or Hidden()
    text = (sql or "").strip().rstrip(";").strip()
    if not text:
        return _blocked(sql, "empty statement")
    dialect = DIALECTS[dialect_kind]
    try:
        parsed = [p for p in sqlglot.parse(text, read=dialect) if p is not None]
    except ParseError as exc:
        return _blocked(text, f"couldn't parse the SQL ({str(exc).splitlines()[0][:120]})")
    if len(parsed) != 1:
        return _blocked(text, "exactly one statement per query, please")
    tree = parsed[0]

    # 1. Statement type (default deny).
    if isinstance(tree, READ_TOPS):
        kind = "read"
    elif isinstance(tree, WRITE_NODES):
        kind = "write"
    elif isinstance(tree, DDL_TOPS):
        k = _kind_of(tree)
        if isinstance(tree, (exp.Create, exp.Drop)) and k not in DDL_KINDS:
            return _blocked(text, f"{type(tree).__name__.upper()} {k or ''} isn't allowed "
                                  "(only tables, views, indexes, schemas and sequences)")
        kind = "ddl"
    else:
        name = type(tree).__name__.upper()
        if isinstance(tree, exp.Command):
            name = str(tree.this).upper()
        return _blocked(text, f"{name} statements aren't allowed")

    # 2. Anything dangerous anywhere inside (CTEs, subqueries, expressions).
    for fn in tree.find_all(exp.Func):
        fname = fn.name if isinstance(fn, exp.Anonymous) else fn.sql_name()
        if fname and BLOCKED_FUNCTIONS.match(fname):
            return _blocked(text, f"the function {fname}() isn't allowed")
    for node in tree.find_all(exp.Command, exp.Copy, exp.Grant):
        return _blocked(text, f"{type(node).__name__.upper()} isn't allowed inside a query")
    if any(True for _ in tree.find_all(*DDL_TOPS)) and kind != "ddl":
        return _blocked(text, "schema changes can't be nested inside another statement")
    if tree.find(exp.Into) is not None:
        kind = "ddl"  # SELECT ... INTO new_table creates a table
    elif kind == "read" and any(True for _ in tree.find_all(*WRITE_NODES)):
        kind = "write"  # e.g. WITH x AS (DELETE ... RETURNING *) SELECT ...

    for node in tree.find_all(exp.Update, exp.Delete):
        if node.args.get("where") is None:
            verb = "UPDATE" if isinstance(node, exp.Update) else "DELETE"
            return _blocked(text, f"{verb} without a WHERE clause isn't allowed "
                                  "(it would change every row)")

    # 3. Tables: credentials catalogs and tables the user has hidden.
    tables: list[str] = []
    cte_names = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    for t in tree.find_all(exp.Table):
        name, db = t.name.lower(), (t.db or "").lower()
        if not name:
            continue
        if (db, name) in BLOCKED_TABLE_SCHEMAS or (name in BLOCKED_TABLES and db in
                                                   ("", "pg_catalog", "mysql")):
            if not (name == "user" and db == ""):  # a user table literally named "user" is fine
                return _blocked(text, f"the system table {t.sql(dialect=dialect)} isn't allowed")
        if name in cte_names and not db:
            continue
        if name in hidden.tables:
            return _blocked(text, f"the table {t.name} is hidden by the user")
        tables.append(name)

    hidden_cols = {t: cols for t, cols in hidden.columns.items() if t in tables and cols}
    if hidden_cols:
        names = {c for cols in hidden_cols.values() for c in cols}
        for col in tree.find_all(exp.Column):
            if col.name.lower() in names:
                return _blocked(text, f"the column {col.name} is hidden by the user")
        for star in tree.find_all(exp.Star):
            if not isinstance(star.parent, exp.Count):
                some = ", ".join(sorted(hidden_cols))
                return _blocked(text, f"some columns of {some} are hidden: list the columns you "
                                      "need instead of *")

    result = GateResult(kind, text, tables=sorted(set(tables)))

    # 4. Reads: cap the rows (one extra row tells us the result was cut off).
    if kind == "read" and isinstance(tree, READ_TOPS) and not tree.args.get("limit") \
            and not tree.args.get("locks"):
        result.sql = f"{text}\nLIMIT {row_limit + 1}"
    # Previews of UPDATE / DELETE: which rows they touch.
    if isinstance(tree, (exp.Update, exp.Delete)) and kind == "write":
        target = tree.this if isinstance(tree, exp.Update) else tree.this
        where = tree.args.get("where")
        if isinstance(target, exp.Table) and where is not None and not tree.args.get("from") \
                and not tree.args.get("using") and not tree.args.get("joins"):
            result.target = target.sql(dialect=dialect)
            result.where = where.this.sql(dialect=dialect)
    return result


def decide(kind: str, policy: str) -> str:
    """run | approval | refuse, for a statement kind under a connection's edit permission."""
    if kind == "read":
        return "run"
    if kind == "blocked" or policy == "read_only":
        return "refuse"
    if kind == "ddl":
        return "approval"  # schema changes always need approval, also in automatic mode
    return "run" if policy == "auto" else "approval"
