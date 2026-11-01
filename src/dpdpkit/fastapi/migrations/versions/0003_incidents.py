"""incident register and principal breach notices

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-01 20:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from sqlalchemy.dialects import mysql
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

_ts = sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql")
_doc = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "dpdp_incidents",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("aware_at", _ts, nullable=False),
        sa.Column("doc", _doc, nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dpdp_incidents")),
    )
    with op.batch_alter_table("dpdp_incidents", schema=None) as batch_op:
        batch_op.create_index("ix_dpdp_incidents_status", ["tenant_id", "status"], unique=False)
    op.create_table(
        "dpdp_principal_notices",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("incident_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", _ts, nullable=False),
        sa.Column("doc", _doc, nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dpdp_principal_notices")),
    )
    with op.batch_alter_table("dpdp_principal_notices", schema=None) as batch_op:
        batch_op.create_index("ix_dpdp_principal_notices_incident", ["tenant_id", "incident_id"], unique=False)


def downgrade() -> None:
    raise RuntimeError("dpdpkit migrations are forward-only; they never drop consent or audit data")
