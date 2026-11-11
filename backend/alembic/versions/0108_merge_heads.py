"""Merge the two heads left by the ULRG reporting port.

Both chains branch from 0103_sb_round2: the lead-source reports (0104_lead_source_groups ->
0105_lead_source_facts -> 0106_lead_source_digest) and the ads work (0104_sb_title_case ->
0105_ad_weekly_budget -> 0106_ad_writes -> 0107_ad_digest). With two heads, `alembic upgrade head`
refuses to run. This revision joins them and changes no schema.

Revision ID: a4de17c29596
Revises: 0106_lead_source_digest, 0107_ad_digest
"""

revision = "a4de17c29596"
down_revision = ("0106_lead_source_digest", "0107_ad_digest")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
