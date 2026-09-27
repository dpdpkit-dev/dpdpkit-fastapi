"""``SqlAlchemyRepository``: dpdpkit-core's Repository on SQLAlchemy 2.0 (Postgres, MySQL, SQLite)."""

from __future__ import annotations

import contextlib
import datetime as dt
import threading
from collections.abc import Collection, Iterator
from typing import Any, TypeVar

from pydantic import BaseModel
from sqlalchemy import Engine, create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from dpdpkit._util import as_utc
from dpdpkit.errors import SequenceConflict
from dpdpkit.models import (
    ACTIVE_SCHEDULE_STATUSES,
    AuditEntry,
    ConsentAction,
    ConsentEvent,
    DeliveryReceipt,
    ErasureSchedule,
    LegalHold,
    Nominee,
    Notice,
    RequestStatus,
    RightsRequest,
    ScheduleStatus,
)

from .db import (
    ActivityRow,
    AuditRow,
    Base,
    ConsentEventRow,
    DeliveryRow,
    HoldRow,
    NomineeRow,
    NoticeRow,
    RequestRow,
    ScheduleRow,
)

M = TypeVar("M", bound=BaseModel)


def make_engine(url: str, **kwargs: Any) -> Engine:
    """Create an engine with settings dpdpkit relies on (SQLite savepoints, shared in-memory DB)."""
    if url.startswith("sqlite"):
        kwargs.setdefault("connect_args", {"check_same_thread": False})
        if url in ("sqlite://", "sqlite:///:memory:"):
            kwargs.setdefault("poolclass", StaticPool)
    engine = create_engine(url, **kwargs)
    if engine.dialect.name == "sqlite":
        # pysqlite's own transaction handling breaks SAVEPOINT; take control of BEGIN ourselves.
        @event.listens_for(engine, "connect")
        def _connect(dbapi_conn: Any, _record: Any) -> None:
            dbapi_conn.isolation_level = None

        @event.listens_for(engine, "begin")
        def _begin(conn: Any) -> None:
            conn.exec_driver_sql("BEGIN")

    return engine


def _doc(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json")


def _load(cls: type[M], doc: dict[str, Any]) -> M:
    return cls.model_validate(doc)


class SqlAlchemyRepository:
    def __init__(self, engine: Engine | str) -> None:
        self.engine = make_engine(engine) if isinstance(engine, str) else engine
        self.Session = sessionmaker(self.engine, expire_on_commit=False)
        self._local = threading.local()

    def create_all(self) -> None:
        """Create tables directly (tests and demos). Production installs use ``dpdpkit migrate``."""
        Base.metadata.create_all(self.engine)

    # ------------------------------------------------------------------ sessions

    @contextlib.contextmanager
    def transaction(self) -> Iterator[None]:
        if getattr(self._local, "session", None) is not None:
            yield
            return
        session = self.Session()
        self._local.session = session
        try:
            yield
            session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()
            self._local.session = None

    @contextlib.contextmanager
    def _session(self) -> Iterator[Session]:
        current: Session | None = getattr(self._local, "session", None)
        if current is not None:
            yield current
            return
        with self.Session() as session:
            try:
                yield session
                session.commit()
            except BaseException:
                session.rollback()
                raise

    def _insert(self, row: Any, what: str) -> None:
        with self._session() as s:
            try:
                with s.begin_nested():
                    s.add(row)
            except IntegrityError as exc:
                raise SequenceConflict(f"{what} sequence conflict") from exc

    # ------------------------------------------------------------------ consent ledger

    @staticmethod
    def _event(r: ConsentEventRow) -> ConsentEvent:
        return ConsentEvent(
            tenant_id=r.tenant_id,
            id=r.id,
            seq=r.seq,
            principal=r.principal,
            purpose=r.purpose,
            action=ConsentAction(r.action),
            notice_version=r.notice_version,
            notice_hash=r.notice_hash,
            locale=r.locale,
            source=r.source,
            occurred_at=as_utc(r.occurred_at),
            metadata=r.meta or {},
            prev_hash=r.prev_hash,
            hash=r.hash,
        )

    def append_consent_event(self, e: ConsentEvent) -> None:
        self._insert(
            ConsentEventRow(
                id=e.id,
                tenant_id=e.tenant_id,
                seq=e.seq,
                principal=e.principal,
                purpose=e.purpose,
                action=e.action.value,
                notice_version=e.notice_version,
                notice_hash=e.notice_hash,
                locale=e.locale,
                source=e.source,
                occurred_at=as_utc(e.occurred_at),
                meta=e.metadata,
                prev_hash=e.prev_hash,
                hash=e.hash,
            ),
            "consent ledger",
        )

    def last_consent_event(self, tenant_id: str) -> ConsentEvent | None:
        with self._session() as s:
            row = s.scalars(
                select(ConsentEventRow)
                .where(ConsentEventRow.tenant_id == tenant_id)
                .order_by(ConsentEventRow.seq.desc())
                .limit(1)
            ).first()
            return self._event(row) if row else None

    def latest_consent_event(self, tenant_id: str, principal: str, purpose: str) -> ConsentEvent | None:
        with self._session() as s:
            row = s.scalars(
                select(ConsentEventRow)
                .where(
                    ConsentEventRow.tenant_id == tenant_id,
                    ConsentEventRow.principal == principal,
                    ConsentEventRow.purpose == purpose,
                )
                .order_by(ConsentEventRow.seq.desc())
                .limit(1)
            ).first()
            return self._event(row) if row else None

    def list_consent_events(
        self, tenant_id: str, *, principal: str | None = None, after_seq: int = 0, limit: int | None = None
    ) -> list[ConsentEvent]:
        q = select(ConsentEventRow).where(ConsentEventRow.tenant_id == tenant_id, ConsentEventRow.seq > after_seq)
        if principal is not None:
            q = q.where(ConsentEventRow.principal == principal)
        q = q.order_by(ConsentEventRow.seq)
        if limit is not None:
            q = q.limit(limit)
        with self._session() as s:
            return [self._event(r) for r in s.scalars(q)]

    # ------------------------------------------------------------------ audit

    @staticmethod
    def _audit(r: AuditRow) -> AuditEntry:
        return AuditEntry(
            tenant_id=r.tenant_id,
            id=r.id,
            seq=r.seq,
            actor=r.actor,
            action=r.action,
            subject_type=r.subject_type,
            subject_id=r.subject_id,
            occurred_at=as_utc(r.occurred_at),
            data=r.data or {},
            prev_hash=r.prev_hash,
            hash=r.hash,
        )

    def append_audit(self, a: AuditEntry) -> None:
        self._insert(
            AuditRow(
                id=a.id,
                tenant_id=a.tenant_id,
                seq=a.seq,
                actor=a.actor,
                action=a.action,
                subject_type=a.subject_type,
                subject_id=a.subject_id,
                occurred_at=as_utc(a.occurred_at),
                data=a.data,
                prev_hash=a.prev_hash,
                hash=a.hash,
            ),
            "audit",
        )

    def last_audit(self, tenant_id: str) -> AuditEntry | None:
        with self._session() as s:
            row = s.scalars(
                select(AuditRow).where(AuditRow.tenant_id == tenant_id).order_by(AuditRow.seq.desc()).limit(1)
            ).first()
            return self._audit(row) if row else None

    def list_audit(
        self, tenant_id: str, *, subject_id: str | None = None, after_seq: int = 0, limit: int | None = None
    ) -> list[AuditEntry]:
        q = select(AuditRow).where(AuditRow.tenant_id == tenant_id, AuditRow.seq > after_seq)
        if subject_id is not None:
            q = q.where(AuditRow.subject_id == subject_id)
        q = q.order_by(AuditRow.seq)
        if limit is not None:
            q = q.limit(limit)
        with self._session() as s:
            return [self._audit(r) for r in s.scalars(q)]

    # ------------------------------------------------------------------ notices

    def save_notice(self, notice: Notice) -> None:
        with self._session() as s:
            s.merge(
                NoticeRow(
                    tenant_id=notice.tenant_id,
                    version=notice.version,
                    locale=notice.locale,
                    content_hash=notice.content_hash,
                    published_at=as_utc(notice.published_at),
                    doc=_doc(notice),
                )
            )

    def list_notices(self, tenant_id: str, *, version: int | None = None) -> list[Notice]:
        q = select(NoticeRow).where(NoticeRow.tenant_id == tenant_id)
        if version is not None:
            q = q.where(NoticeRow.version == version)
        with self._session() as s:
            return [_load(Notice, r.doc) for r in s.scalars(q.order_by(NoticeRow.version, NoticeRow.locale))]

    # ------------------------------------------------------------------ rights

    def save_request(self, req: RightsRequest) -> None:
        with self._session() as s:
            s.merge(
                RequestRow(
                    id=req.id,
                    tenant_id=req.tenant_id,
                    principal=req.principal,
                    status=req.status.value,
                    opened_at=as_utc(req.opened_at),
                    doc=_doc(req),
                )
            )

    def get_request(self, tenant_id: str, request_id: str) -> RightsRequest | None:
        with self._session() as s:
            row = s.get(RequestRow, request_id)
            return _load(RightsRequest, row.doc) if row and row.tenant_id == tenant_id else None

    def list_requests(
        self,
        tenant_id: str,
        *,
        principal: str | None = None,
        statuses: Collection[RequestStatus] | None = None,
    ) -> list[RightsRequest]:
        q = select(RequestRow).where(RequestRow.tenant_id == tenant_id)
        if principal is not None:
            q = q.where(RequestRow.principal == principal)
        if statuses is not None:
            q = q.where(RequestRow.status.in_([st.value for st in statuses]))
        with self._session() as s:
            return [_load(RightsRequest, r.doc) for r in s.scalars(q.order_by(RequestRow.opened_at))]

    def save_nominee(self, nominee: Nominee) -> None:
        with self._session() as s:
            s.merge(
                NomineeRow(id=nominee.id, tenant_id=nominee.tenant_id, principal=nominee.principal, doc=_doc(nominee))
            )

    def list_nominees(self, tenant_id: str, principal: str) -> list[Nominee]:
        q = select(NomineeRow).where(NomineeRow.tenant_id == tenant_id, NomineeRow.principal == principal)
        with self._session() as s:
            return [_load(Nominee, r.doc) for r in s.scalars(q)]

    # ------------------------------------------------------------------ retention

    def save_schedule(self, sch: ErasureSchedule) -> None:
        with self._session() as s:
            s.merge(
                ScheduleRow(
                    id=sch.id,
                    tenant_id=sch.tenant_id,
                    principal=sch.principal,
                    purpose=sch.purpose,
                    status=sch.status.value,
                    warn_at=as_utc(sch.warn_at),
                    erase_at=as_utc(sch.erase_at),
                    doc=_doc(sch),
                )
            )

    def get_schedule(self, tenant_id: str, schedule_id: str) -> ErasureSchedule | None:
        with self._session() as s:
            row = s.get(ScheduleRow, schedule_id)
            return _load(ErasureSchedule, row.doc) if row and row.tenant_id == tenant_id else None

    def list_schedules(
        self,
        tenant_id: str,
        *,
        principal: str | None = None,
        statuses: Collection[ScheduleStatus] | None = None,
    ) -> list[ErasureSchedule]:
        q = select(ScheduleRow).where(ScheduleRow.tenant_id == tenant_id)
        if principal is not None:
            q = q.where(ScheduleRow.principal == principal)
        if statuses is not None:
            q = q.where(ScheduleRow.status.in_([st.value for st in statuses]))
        with self._session() as s:
            return [_load(ErasureSchedule, r.doc) for r in s.scalars(q.order_by(ScheduleRow.erase_at, ScheduleRow.id))]

    def due_erasures(self, tenant_id: str, now: dt.datetime) -> list[ErasureSchedule]:
        now = as_utc(now)
        q = select(ScheduleRow).where(
            ScheduleRow.tenant_id == tenant_id,
            ScheduleRow.status.in_([st.value for st in ACTIVE_SCHEDULE_STATUSES]),
            (ScheduleRow.warn_at <= now) | (ScheduleRow.erase_at <= now),
        )
        with self._session() as s:
            return [_load(ErasureSchedule, r.doc) for r in s.scalars(q.order_by(ScheduleRow.erase_at))]

    def save_hold(self, hold: LegalHold) -> None:
        with self._session() as s:
            s.merge(
                HoldRow(
                    id=hold.id,
                    tenant_id=hold.tenant_id,
                    principal=hold.principal,
                    active=hold.released_at is None,
                    doc=_doc(hold),
                )
            )

    def list_holds(self, tenant_id: str, *, principal: str | None = None, active_only: bool = True) -> list[LegalHold]:
        q = select(HoldRow).where(HoldRow.tenant_id == tenant_id)
        if principal is not None:
            q = q.where(HoldRow.principal == principal)
        if active_only:
            q = q.where(HoldRow.active.is_(True))
        with self._session() as s:
            return [_load(LegalHold, r.doc) for r in s.scalars(q)]

    def record_activity(self, tenant_id: str, principal: str, at: dt.datetime) -> None:
        with self._session() as s:
            row = s.get(ActivityRow, (tenant_id, principal))
            if row is None:
                s.add(ActivityRow(tenant_id=tenant_id, principal=principal, last_at=as_utc(at)))
            elif as_utc(row.last_at) < as_utc(at):
                row.last_at = as_utc(at)

    def last_activity(self, tenant_id: str, principal: str) -> dt.datetime | None:
        with self._session() as s:
            row = s.get(ActivityRow, (tenant_id, principal))
            return as_utc(row.last_at) if row else None

    # ------------------------------------------------------------------ notifications

    def save_delivery(self, receipt: DeliveryReceipt) -> None:
        with self._session() as s:
            s.merge(
                DeliveryRow(
                    id=receipt.id, tenant_id=receipt.tenant_id, created_at=as_utc(receipt.created_at), doc=_doc(receipt)
                )
            )

    def get_delivery(self, tenant_id: str, receipt_id: str) -> DeliveryReceipt | None:
        with self._session() as s:
            row = s.get(DeliveryRow, receipt_id)
            return _load(DeliveryReceipt, row.doc) if row and row.tenant_id == tenant_id else None
