# Database connections ("ask your database")

Status: **built (v1): PostgreSQL and MySQL/MariaDB.** Later: SQL Server, mixed site + database
answers, charts, CSV export.

## What it does
Users add their own databases next to their websites (**Sources → Databases**), then ask in plain
language: "how many orders shipped last week?", "top 3 customers by revenue". Smish writes the
SQL, runs it, and answers from the real results. Each fact cites the query it came from as
**[Q1]**, **[Q2]**. The SQL and the result rows are under the answer (**N queries** chip).

Each connection has an **edit permission**:

| Permission | Questions | Data changes (INSERT/UPDATE/DELETE) | Schema changes (CREATE/ALTER/DROP/TRUNCATE) |
|---|---|---|---|
| **Never edit** (default) | ✔ | refused | refused |
| **Ask before editing** | ✔ | approval card | approval card |
| **Edit automatically** | ✔ | run right away, no row cap | approval card |

**Always refused, under every permission** (server and security level, not data):
- `GRANT`/`REVOKE`, roles and users, functions, triggers, extensions, `DROP DATABASE`;
- file and OS access (`COPY … PROGRAM`, `LOAD DATA`, `INTO OUTFILE`, `pg_read_file`, `lo_*`,
  `load_file`);
- functions that run SQL text the gate can't see (`query_to_xml`, `dblink`, …), and `SET`,
  `CALL`, `DO`;
- more than one statement per query;
- UPDATE or DELETE without a WHERE clause.

## How a question flows

```
question
  → router (app/orchestrator/router.py): chat | sites_overview | search | database
        + which database, and change: true only when the USER asked for a change
  → schema context (app/datasources/schema.py)
        small schemas (≤ 40 tables) in full; larger: most relevant tables (vectors, or keywords
        when the GPU is off) + the tables they reference. Hidden items never appear.
  → agent loop (app/datasources/agent.py), up to 6 steps; the model replies with one JSON action:
        {"action":"query","sql":…}  {"action":"describe","table":…}
        {"action":"change","sql":…,"reason":…}   (only offered when the user asked for a change)
        {"action":"answer"}
     every statement → safety gate → run / approval card / refused → audit log
     database errors go back to the model so it can fix its query
  → streamed answer from the query results, citing [Q1], [Q2]
```

JSON actions are used instead of native tool calling, so any chat model works and the GPU server
needs no extra flags.

## Safety layers
1. **SQL gate** (`app/datasources/sqlgate.py`, built on sqlglot, default-deny):
   - classifies each statement as read, write, ddl or blocked;
   - a write hidden inside a read (a CTE `DELETE … RETURNING`) counts as a write;
   - `SELECT … INTO` counts as a schema change;
   - refuses hidden tables and columns, including inside subqueries, and `SELECT *` on a table
     with hidden columns;
   - adds a row cap to reads.
2. **The database enforces it too:**
   - reads run in `READ ONLY` transactions, so a write that slipped past the gate still fails;
   - one statement per execution: Postgres uses prepared statements, and MySQL connects without
     the multi-statement flag;
   - statement timeouts and a row cap per connection.
3. **Approval cards:**
   - each change is first tried in a transaction that is rolled back, giving a row count and a
     before/after preview;
   - an approval is tied to the exact SQL (by hash), expires after 10 minutes, and is checked
     again against the *current* permission and hidden tables when approved;
   - MySQL can't roll back schema changes or non-InnoDB tables, so those get no trial run and
     say so.
4. **Only the user can trigger a change.** Text read from the database can't trigger one: write
   actions are offered only when the router saw the user's own message ask for a change.
5. **Audit log** (`ds_query_log`): every statement that was run, refused, queued, applied or
   rejected, with the user, rows and duration. It's shown on the database card under
   **Query log**.
6. **Credentials:** passwords are encrypted at rest (Fernet, keyed from `ENCRYPTION_KEY`),
   write-only in the API, and never sent to the model.
7. **Network:**
   - Smish's own Postgres and Redis are always refused;
   - private and internal addresses need `ALLOW_PRIVATE_DB_HOSTS=true` (off in production by
     default);
   - the host is resolved once and the connection goes to that IP.
8. **Sample values:** only for category-like text columns, where a handful of short values repeat
   across rows. Values that are each unique (names, emails) are never stored. Column names that
   look sensitive (password, email, phone, token, …) are never sampled.

Recommended in addition: give Smish its own database user, read-only (`GRANT SELECT`) for
"Never edit" connections.

## Code map
| Where | What |
|---|---|
| `app/datasources/connectors.py` | asyncpg / asyncmy: read, execute, preview (rolled back), introspect |
| `app/datasources/sqlgate.py` | the gate: `check()` and `decide(kind, policy)` |
| `app/datasources/schema.py` | introspection, sample values, notes/hidden, schema context |
| `app/datasources/agent.py` | the agent loop and the streamed answer |
| `app/datasources/hosts.py` | which hosts may be connected to |
| `app/api/datasources.py` | `/data-sources` CRUD, test, schema, log; `/changes/{id}` approve/reject |
| `app/security/crypto.py` | encryption at rest |
| `frontend/src/pages/Databases.tsx`, `components/ApprovalCard.tsx` | UI |
| `testdbs/`, compose profile `testdbs` | sample shop databases (Postgres + MySQL) |

Tests:
- `tests/unit/test_sqlgate.py`: 62 cases, including the attacks above.
- `tests/integration/test_datasources.py`: run against both test databases with a scripted
  model. Covers answers, self-correction, that a question can never write, each permission,
  approve/reject/expiry/tampering, hidden tables, the host guard, and that passwords are never
  returned.

## Models
The 4B test model is enough for single-table questions. Joins and multi-step questions need the
prod 30B model.
