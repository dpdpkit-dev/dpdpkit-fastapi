"""SQLAlchemy 2.0 tables. Ledger and audit rows use real columns; mutable aggregates store their
model as a JSON document next to the indexed columns used for lookups."""

from __future__ import annotations

import datetime as dt
from typing import Any, ClassVar

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Index, Integer, MetaData, String, UniqueConstraint
from sqlalchemy.dialects import mysql, postgresql
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# Microsecond precision everywhere: ledger hashes include timestamps and must survive a round trip.
Timestamp = DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql")
Json = JSON().with_variant(postgresql.JSONB(), "postgresql")


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)
    type_annotation_map: ClassVar[dict[Any, Any]] = {dt.datetime: Timestamp, dict[str, Any]: Json}


class ConsentEventRow(Base):
    __tablename__ = "dpdp_consent_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "seq", name="uq_dpdp_consent_events_tenant_seq"),
        Index("ix_dpdp_consent_events_lookup", "tenant_id", "principal", "purpose", "seq"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    seq: Mapped[int] = mapped_column(BigInteger)
    principal: Mapped[str] = mapped_column(String(255))
    purpose: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(16))
    notice_version: Mapped[int | None] = mapped_column(Integer)
    notice_hash: Mapped[str | None] = mapped_column(String(64))
    locale: Mapped[str | None] = mapped_column(String(16))
    source: Mapped[str] = mapped_column(String(64))
    occurred_at: Mapped[dt.datetime]
    meta: Mapped[dict[str, Any]]
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))


class AuditRow(Base):
    __tablename__ = "dpdp_audit"
    __table_args__ = (
        UniqueConstraint("tenant_id", "seq", name="uq_dpdp_audit_tenant_seq"),
        Index("ix_dpdp_audit_subject", "tenant_id", "subject_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    seq: Mapped[int] = mapped_column(BigInteger)
    actor: Mapped[str] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(128))
    subject_type: Mapped[str] = mapped_column(String(64))
    subject_id: Mapped[str] = mapped_column(String(255))
    occurred_at: Mapped[dt.datetime]
    data: Mapped[dict[str, Any]]
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))


class NoticeRow(Base):
    __tablename__ = "dpdp_notices"

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    locale: Mapped[str] = mapped_column(String(16), primary_key=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    published_at: Mapped[dt.datetime]
    doc: Mapped[dict[str, Any]]


class RequestRow(Base):
    __tablename__ = "dpdp_requests"
    __table_args__ = (
        Index("ix_dpdp_requests_principal", "tenant_id", "principal"),
        Index("ix_dpdp_requests_status", "tenant_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    principal: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32))
    opened_at: Mapped[dt.datetime]
    doc: Mapped[dict[str, Any]]


class NomineeRow(Base):
    __tablename__ = "dpdp_nominees"
    __table_args__ = (Index("ix_dpdp_nominees_principal", "tenant_id", "principal"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    principal: Mapped[str] = mapped_column(String(255))
    doc: Mapped[dict[str, Any]]


class ScheduleRow(Base):
    __tablename__ = "dpdp_erasure_schedules"
    __table_args__ = (
        Index("ix_dpdp_schedules_due", "tenant_id", "status", "warn_at"),
        Index("ix_dpdp_schedules_principal", "tenant_id", "principal"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    principal: Mapped[str] = mapped_column(String(255))
    purpose: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32))
    warn_at: Mapped[dt.datetime]
    erase_at: Mapped[dt.datetime]
    doc: Mapped[dict[str, Any]]


class HoldRow(Base):
    __tablename__ = "dpdp_legal_holds"
    __table_args__ = (Index("ix_dpdp_holds_principal", "tenant_id", "principal", "active"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    principal: Mapped[str] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean)
    doc: Mapped[dict[str, Any]]


class ActivityRow(Base):
    __tablename__ = "dpdp_activity"

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    principal: Mapped[str] = mapped_column(String(255), primary_key=True)
    last_at: Mapped[dt.datetime]


class DeliveryRow(Base):
    __tablename__ = "dpdp_deliveries"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[dt.datetime]
    doc: Mapped[dict[str, Any]]
