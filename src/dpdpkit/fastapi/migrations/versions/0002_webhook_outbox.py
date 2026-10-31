"""webhook outbox

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-31 23:30:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from sqlalchemy.dialects import mysql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

_ts = sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql")


def upgrade() -> None:
    op.create_table(
        "dpdp_webhook_deliveries",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("endpoint_id", sa.String(length=64), nullable=False),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", _ts, nullable=False),
        sa.Column("last_error", sa.String(length=1000), nullable=True),
        sa.Column("created_at", _ts, nullable=False),
        sa.Column("delivered_at", _ts, nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dpdp_webhook_deliveries")),
        sa.UniqueConstraint("endpoint_id", "event_id", name="uq_dpdp_webhook_deliveries_endpoint_event"),
    )
    with op.batch_alter_table("dpdp_webhook_deliveries", schema=None) as batch_op:
        batch_op.create_index("ix_dpdp_webhook_deliveries_due", ["status", "next_attempt_at"], unique=False)


def downgrade() -> None:
    raise RuntimeError("dpdpkit migrations are forward-only; they never drop consent or audit data")
