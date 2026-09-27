"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-09-27 20:30:47.602958
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from sqlalchemy.dialects import mysql
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dpdp_activity",
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("principal", sa.String(length=255), nullable=False),
        sa.Column("last_at", sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "principal", name=op.f("pk_dpdp_activity")),
    )
    op.create_table(
        "dpdp_audit",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column("actor", sa.String(length=255), nullable=False),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("subject_type", sa.String(length=64), nullable=False),
        sa.Column("subject_id", sa.String(length=255), nullable=False),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"), nullable=False
        ),
        sa.Column(
            "data", sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"), nullable=False
        ),
        sa.Column("prev_hash", sa.String(length=64), nullable=False),
        sa.Column("hash", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dpdp_audit")),
        sa.UniqueConstraint("tenant_id", "seq", name="uq_dpdp_audit_tenant_seq"),
    )
    with op.batch_alter_table("dpdp_audit", schema=None) as batch_op:
        batch_op.create_index("ix_dpdp_audit_subject", ["tenant_id", "subject_id"], unique=False)

    op.create_table(
        "dpdp_consent_events",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column("principal", sa.String(length=255), nullable=False),
        sa.Column("purpose", sa.String(length=128), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("notice_version", sa.Integer(), nullable=True),
        sa.Column("notice_hash", sa.String(length=64), nullable=True),
        sa.Column("locale", sa.String(length=16), nullable=True),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"), nullable=False
        ),
        sa.Column(
            "meta", sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"), nullable=False
        ),
        sa.Column("prev_hash", sa.String(length=64), nullable=False),
        sa.Column("hash", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dpdp_consent_events")),
        sa.UniqueConstraint("tenant_id", "seq", name="uq_dpdp_consent_events_tenant_seq"),
    )
    with op.batch_alter_table("dpdp_consent_events", schema=None) as batch_op:
        batch_op.create_index(
            "ix_dpdp_consent_events_lookup", ["tenant_id", "principal", "purpose", "seq"], unique=False
        )

    op.create_table(
        "dpdp_deliveries",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"), nullable=False
        ),
        sa.Column("doc", sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dpdp_deliveries")),
    )
    op.create_table(
        "dpdp_erasure_schedules",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("principal", sa.String(length=255), nullable=False),
        sa.Column("purpose", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("warn_at", sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"), nullable=False),
        sa.Column("erase_at", sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"), nullable=False),
        sa.Column("doc", sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dpdp_erasure_schedules")),
    )
    with op.batch_alter_table("dpdp_erasure_schedules", schema=None) as batch_op:
        batch_op.create_index("ix_dpdp_schedules_due", ["tenant_id", "status", "warn_at"], unique=False)
        batch_op.create_index("ix_dpdp_schedules_principal", ["tenant_id", "principal"], unique=False)

    op.create_table(
        "dpdp_legal_holds",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("principal", sa.String(length=255), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("doc", sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dpdp_legal_holds")),
    )
    with op.batch_alter_table("dpdp_legal_holds", schema=None) as batch_op:
        batch_op.create_index("ix_dpdp_holds_principal", ["tenant_id", "principal", "active"], unique=False)

    op.create_table(
        "dpdp_nominees",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("principal", sa.String(length=255), nullable=False),
        sa.Column("doc", sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dpdp_nominees")),
    )
    with op.batch_alter_table("dpdp_nominees", schema=None) as batch_op:
        batch_op.create_index("ix_dpdp_nominees_principal", ["tenant_id", "principal"], unique=False)

    op.create_table(
        "dpdp_notices",
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("locale", sa.String(length=16), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "published_at", sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"), nullable=False
        ),
        sa.Column("doc", sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "version", "locale", name=op.f("pk_dpdp_notices")),
    )
    op.create_table(
        "dpdp_requests",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("principal", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"), nullable=False),
        sa.Column("doc", sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dpdp_requests")),
    )
    with op.batch_alter_table("dpdp_requests", schema=None) as batch_op:
        batch_op.create_index("ix_dpdp_requests_principal", ["tenant_id", "principal"], unique=False)
        batch_op.create_index("ix_dpdp_requests_status", ["tenant_id", "status"], unique=False)


def downgrade() -> None:
    raise RuntimeError("dpdpkit migrations are forward-only; they never drop consent or audit data")
