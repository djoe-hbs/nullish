"""Stateless / browser-local database source helpers.

Builds ephemeral in-memory `DataSource` and `Table` objects from client-supplied configs stored in
the user's browser `localStorage`, so queries and schema introspection run without saving
credentials, schemas, or logs in Postgres.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.datasources.connectors import DEFAULT_PORTS
from app.datasources.schema import Column, Table, default_schema_of, display_name
from app.db.models import DataSource
from app.security.crypto import encrypt


class LocalSchemaItemPayload(BaseModel):
    schema_name: str = Field(min_length=1, max_length=255)
    table_name: str = Field(min_length=1, max_length=255)
    column_name: str | None = Field(None, max_length=255)
    ordinal: int = 0
    data_type: str | None = None
    nullable: bool | None = None
    is_pk: bool = False
    fk_ref: str | None = None
    row_estimate: int | None = None
    sample_values: list[Any] = Field(default_factory=list, max_length=32)
    comment: str | None = None
    note: str | None = Field(None, max_length=2000)
    hidden: bool = False


class LocalSourceIn(BaseModel):
    id: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=200)
    kind: Literal["postgres", "mysql"]
    host: str = Field(min_length=1, max_length=255)
    port: int | None = Field(None, ge=1, le=65535)
    database: str = Field(min_length=1, max_length=255)
    username: str = Field(min_length=1, max_length=255)
    password: str = Field("", max_length=1000)
    ssl_mode: Literal["disable", "prefer", "require", "verify"] = "prefer"
    edit_policy: Literal["read_only", "ask", "auto"] = "read_only"
    row_limit: int = Field(200, ge=1, le=5000)
    timeout_sec: int = Field(15, ge=1, le=300)
    description: str | None = Field(None, max_length=2000)
    enabled: bool = True
    schema_items: list[LocalSchemaItemPayload] = Field(default_factory=list, max_length=10_000)


class LocalIntrospectOut(BaseModel):
    ok: bool
    version: str | None = None
    error: str | None = None
    tables: list[LocalSchemaItemPayload] = Field(default_factory=list)


def to_ephemeral_source(item: LocalSourceIn, user_id: int) -> tuple[DataSource, list[Table]]:
    """Build a detached in-memory DataSource (negative id) and Table list without DB state."""
    src = DataSource(
        id=-abs(item.id),
        user_id=user_id,
        name=item.name.strip()[:200],
        kind=item.kind,
        host=item.host.strip(),
        port=item.port or DEFAULT_PORTS[item.kind],
        database=item.database,
        username=item.username,
        password_encrypted=encrypt(item.password) if item.password else None,
        ssl_mode=item.ssl_mode,
        edit_policy=item.edit_policy,
        row_limit=item.row_limit,
        timeout_sec=item.timeout_sec,
        description=item.description,
        enabled=item.enabled,
    )
    default = default_schema_of(src)
    tables: dict[tuple[str, str], Table] = {}
    sorted_items = sorted(
        item.schema_items,
        key=lambda i: (i.schema_name, i.table_name, i.column_name is not None, i.ordinal),
    )
    for row in sorted_items:
        key = (row.schema_name, row.table_name)
        if row.column_name is None:
            tables[key] = Table(
                schema=row.schema_name,
                name=row.table_name,
                display=display_name(row.schema_name, row.table_name, default),
                kind=row.data_type or "table",
                rows=row.row_estimate,
                comment=row.comment,
                note=row.note,
                hidden=row.hidden,
            )
        elif key in tables:
            tables[key].columns.append(
                Column(
                    name=row.column_name,
                    data_type=row.data_type or "",
                    nullable=True if row.nullable is None else row.nullable,
                    is_pk=row.is_pk,
                    fk_ref=row.fk_ref,
                    comment=row.comment,
                    note=row.note,
                    hidden=row.hidden,
                    samples=list(row.sample_values or []),
                )
            )
    return src, list(tables.values())


def tables_to_schema_payloads(tables: list[Table]) -> list[LocalSchemaItemPayload]:
    out: list[LocalSchemaItemPayload] = []
    for t in tables:
        out.append(
            LocalSchemaItemPayload(
                schema_name=t.schema,
                table_name=t.name,
                column_name=None,
                ordinal=0,
                data_type=t.kind,
                row_estimate=t.rows,
                comment=t.comment,
                note=t.note,
                hidden=t.hidden,
            )
        )
        for idx, c in enumerate(t.columns):
            out.append(
                LocalSchemaItemPayload(
                    schema_name=t.schema,
                    table_name=t.name,
                    column_name=c.name,
                    ordinal=idx,
                    data_type=c.data_type,
                    nullable=c.nullable,
                    is_pk=c.is_pk,
                    fk_ref=c.fk_ref,
                    sample_values=list(c.samples or []),
                    comment=c.comment,
                    note=c.note,
                    hidden=c.hidden,
                )
            )
    return out

