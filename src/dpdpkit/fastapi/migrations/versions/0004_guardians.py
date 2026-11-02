"""age assertions and guardian verifications

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-01 21:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from sqlalchemy.dialects import mysql
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

_ts = sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql")
_doc = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "dpdp_age_assertions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("principal", sa.String(length=255), nullable=False),
        sa.Column("doc", _doc, nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dpdp_age_assertions")),
        sa.UniqueConstraint("tenant_id", "principal", name="uq_dpdp_age_assertions_tenant_principal"),
    )
    op.create_table(
        "dpdp_guardian_verifications",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("principal", sa.String(length=255), nullable=False),
        sa.Column("created_at", _ts, nullable=False),
        sa.Column("doc", _doc, nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dpdp_guardian_verifications")),
    )
    with op.batch_alter_table("dpdp_guardian_verifications", schema=None) as batch_op:
        batch_op.create_index("ix_dpdp_guardian_verifications_principal", ["tenant_id", "principal"], unique=False)


def downgrade() -> None:
    raise RuntimeError("dpdpkit migrations are forward-only; they never drop consent or audit data")
