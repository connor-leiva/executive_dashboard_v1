"""init — create all tables from the model metadata

Portable across Postgres (prod) and SQLite by building DDL straight from
SQLAlchemy metadata rather than hand-written per-column ops. Once the schema
stabilises, switch to `alembic revision --autogenerate` for incremental changes.

Revision ID: 0001_init
Revises:
Create Date: 2026-06-30
"""
from typing import Sequence, Union

from alembic import op

from app.models import Base

revision: str = "0001_init"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
