"""Database-portable column types.

The spec targets Postgres (UUID + JSONB). To let the exact same models run on
SQLite for zero-infra local dev, we use small TypeDecorators that map to the
native Postgres types when on Postgres and to portable equivalents on SQLite.
Prod behaviour is unchanged.
"""
from __future__ import annotations

import uuid

from sqlalchemy import CHAR, JSON
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.types import TypeDecorator


class GUID(TypeDecorator):
    """UUID-as-PG-UUID on Postgres, CHAR(36) elsewhere. Values are uuid.UUID."""

    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if dialect.name == "postgresql":
            return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
        return str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(str(value))


# JSONB on Postgres, generic JSON on SQLite.
JSONType = JSON().with_variant(JSONB(), "postgresql")
