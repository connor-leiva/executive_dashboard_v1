"""add metric_record (Go High Level members/subscriptions/registrations)

Revision ID: 0004_metric_record
Revises: 0003_listing_date
Create Date: 2026-07-01
"""
from typing import Sequence, Union

from alembic import op

from app.models import Base, MetricRecord

revision: str = "0004_metric_record"
down_revision: Union[str, None] = "0003_listing_date"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    MetricRecord.__table__.create(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    MetricRecord.__table__.drop(bind=op.get_bind(), checkfirst=True)
