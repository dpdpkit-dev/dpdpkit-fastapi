"""Durable outbound webhooks: a database outbox with retries, backoff and a dead-letter state.

Publishing an event only inserts one row per matching endpoint, so request handlers never wait on
the network. :meth:`WebhookOutbox.deliver_due` (run by the scheduler) sends due rows, reschedules
failures with exponential backoff, and marks a row ``dead`` after ``max_attempts``. Dead rows stay
until someone redelivers them; nothing is dropped silently.

Receivers verify ``X-DPDPKit-Signature`` with :func:`dpdpkit.events.verify_signature` and should treat
the event ``id`` as an idempotency key: a delivery can arrive more than once.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, field_validator
from sqlalchemy import func, select, update

from dpdpkit._util import as_utc, new_id
from dpdpkit.errors import ConfigError, NotFound
from dpdpkit.events import EVENT_HEADER, SIGNATURE_HEADER, Event, EventBus, Sender, WebhookEndpoint, sign_payload
from dpdpkit.events import urllib_sender as default_sender

from .db import WebhookDeliveryRow
from .repository import SqlAlchemyRepository

log = logging.getLogger("dpdpkit.fastapi.webhooks")

PENDING, DELIVERED, DEAD = "pending", "delivered", "dead"
DELIVERY_HEADER = "X-DPDPKit-Delivery"


class WebhookDelivery(BaseModel):
    id: str
    tenant_id: str
    endpoint_id: str
    event_id: str
    event_type: str
    status: str
    attempts: int
    next_attempt_at: dt.datetime
    last_error: str | None
    created_at: dt.datetime
    delivered_at: dt.datetime | None

    @field_validator("next_attempt_at", "created_at", "delivered_at")
    @classmethod
    def _utc(cls, value: dt.datetime | None) -> dt.datetime | None:
        return as_utc(value) if value is not None else None  # SQLite drops tzinfo


@dataclass
class DeliveryRun:
    delivered: int = 0
    retrying: int = 0
    dead: int = 0


def endpoints_from_config(items: Iterable[Mapping[str, Any]]) -> list[WebhookEndpoint]:
    """Build endpoints from the ``webhooks:`` list in dpdpkit.yaml. Secrets come from the environment
    (``secret_env``) so they never sit in the config file."""
    out = []
    for item in items:
        env = item.get("secret_env")
        if not env:
            raise ConfigError(f"webhook {item.get('id')!r} needs secret_env (an environment variable name)")
        secret = os.environ.get(str(env))
        if not secret:
            raise ConfigError(f"webhook {item.get('id')!r}: environment variable {env} is not set")
        out.append(
            WebhookEndpoint(
                id=str(item["id"]),
                url=str(item["url"]),
                secret=secret,
                events=list(item.get("events", ["*"])),
                active=bool(item.get("active", True)),
            )
        )
    return out


def _body(event: Event) -> str:
    return json.dumps(event.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class WebhookOutbox:
    """``max_attempts`` sends spaced ``base_delay * 2**n`` apart (capped at ``max_delay``): with the
    defaults, 10 attempts over roughly 4 hours. ``lease`` stops two workers sending the same row."""

    def __init__(
        self,
        repository: SqlAlchemyRepository,
        endpoints: Iterable[WebhookEndpoint],
        *,
        clock: Callable[[], dt.datetime],
        sender: Sender = default_sender,
        max_attempts: int = 10,
        base_delay: dt.timedelta = dt.timedelta(seconds=30),
        max_delay: dt.timedelta = dt.timedelta(hours=2),
        lease: dt.timedelta = dt.timedelta(minutes=5),
        batch_size: int = 100,
    ) -> None:
        if max_attempts < 1:
            raise ConfigError("max_attempts must be at least 1")
        self.repository = repository
        self.endpoints = {e.id: e for e in endpoints}
        self.clock = clock
        self.sender = sender
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.lease = lease
        self.batch_size = batch_size

    def attach(self, bus: EventBus) -> None:
        bus.subscribe("*", self._on_event)

    def _on_event(self, event: Event) -> None:
        self.enqueue(event)

    # ------------------------------------------------------------------ enqueue

    def enqueue(self, event: Event) -> int:
        """Store one pending delivery per endpoint that wants this event. Returns how many."""
        wanted = [e for e in self.endpoints.values() if e.wants(event.type)]
        if not wanted:
            return 0
        now = as_utc(self.clock())
        body = _body(event)
        with self.repository._session() as s:
            for endpoint in wanted:
                s.add(
                    WebhookDeliveryRow(
                        id=new_id("wh"),
                        tenant_id=event.tenant_id,
                        endpoint_id=endpoint.id,
                        event_id=event.id,
                        event_type=event.type,
                        body=body,
                        status=PENDING,
                        attempts=0,
                        next_attempt_at=now,
                        last_error=None,
                        created_at=now,
                        delivered_at=None,
                    )
                )
        return len(wanted)

    # ------------------------------------------------------------------ delivery

    def backoff(self, attempts: int) -> dt.timedelta:
        """Delay before the next try, after ``attempts`` failed sends."""
        return min(self.base_delay * (2 ** max(attempts - 1, 0)), self.max_delay)

    def deliver_due(self, now: dt.datetime | None = None) -> DeliveryRun:
        """Send every due delivery once. Safe to run from several processes at the same time."""
        now = as_utc(now or self.clock())
        run = DeliveryRun()
        with self.repository.Session() as s:
            due = s.scalars(
                select(WebhookDeliveryRow)
                .where(WebhookDeliveryRow.status == PENDING, WebhookDeliveryRow.next_attempt_at <= now)
                .order_by(WebhookDeliveryRow.next_attempt_at)
                .limit(self.batch_size)
            ).all()
        for row in due:
            if not self._claim(row, now):
                continue  # another worker took it
            outcome = self._send(row, now)
            setattr(run, outcome, getattr(run, outcome) + 1)
        if run.dead:
            log.warning("%d webhook deliveries moved to dead letters", run.dead)
        return run

    def _claim(self, row: WebhookDeliveryRow, now: dt.datetime) -> bool:
        with self.repository.Session() as s, s.begin():
            result = s.execute(
                update(WebhookDeliveryRow)
                .where(
                    WebhookDeliveryRow.id == row.id,
                    WebhookDeliveryRow.status == PENDING,
                    WebhookDeliveryRow.attempts == row.attempts,
                )
                .values(attempts=row.attempts + 1, next_attempt_at=now + self.lease)
            )
            return bool(getattr(result, "rowcount", 0) == 1)

    def _send(self, row: WebhookDeliveryRow, now: dt.datetime) -> str:
        attempts = row.attempts + 1
        endpoint = self.endpoints.get(row.endpoint_id)
        if endpoint is None or not endpoint.active:
            return self._finish(
                row.id, DEAD, attempts, now, f"endpoint {row.endpoint_id} is not configured or inactive"
            )
        body = row.body.encode()
        headers = {
            "Content-Type": "application/json",
            EVENT_HEADER: row.event_type,
            DELIVERY_HEADER: row.id,
            SIGNATURE_HEADER: sign_payload(endpoint.secret, body),
        }
        try:
            status = self.sender(endpoint.url, body, headers)
            error = None if 200 <= status < 300 else f"HTTP {status}"
        except Exception as exc:
            error = repr(exc)
        if error is None:
            return self._finish(row.id, DELIVERED, attempts, now, None)
        if attempts >= self.max_attempts:
            log.warning("webhook %s to %s gave up after %d attempts: %s", row.id, endpoint.id, attempts, error)
            return self._finish(row.id, DEAD, attempts, now, error)
        return self._finish(row.id, PENDING, attempts, now + self.backoff(attempts), error)

    def _finish(self, row_id: str, status: str, attempts: int, at: dt.datetime, error: str | None) -> str:
        values: dict[str, Any] = {"status": status, "attempts": attempts, "last_error": error and error[:1000]}
        if status == DELIVERED:
            values["delivered_at"] = at
        else:
            values["next_attempt_at"] = at
        with self.repository.Session() as s, s.begin():
            s.execute(update(WebhookDeliveryRow).where(WebhookDeliveryRow.id == row_id).values(**values))
        return {DELIVERED: "delivered", PENDING: "retrying", DEAD: "dead"}[status]

    # ------------------------------------------------------------------ inspection and recovery

    @staticmethod
    def _model(row: WebhookDeliveryRow) -> WebhookDelivery:
        return WebhookDelivery.model_validate(row, from_attributes=True)

    def deliveries(self, status: str | None = None, *, limit: int = 100) -> list[WebhookDelivery]:
        query = select(WebhookDeliveryRow).order_by(WebhookDeliveryRow.created_at.desc()).limit(limit)
        if status:
            query = query.where(WebhookDeliveryRow.status == status)
        with self.repository.Session() as s:
            return [self._model(r) for r in s.scalars(query)]

    def dead_letters(self, *, limit: int = 100) -> list[WebhookDelivery]:
        return self.deliveries(DEAD, limit=limit)

    def counts(self) -> dict[str, int]:
        with self.repository.Session() as s:
            rows = s.execute(select(WebhookDeliveryRow.status, func.count()).group_by(WebhookDeliveryRow.status)).all()
        return {PENDING: 0, DELIVERED: 0, DEAD: 0, **{str(k): int(v) for k, v in rows}}

    def redeliver(self, delivery_id: str | None = None) -> int:
        """Move one dead delivery (or all of them) back to pending with a fresh attempt budget."""
        query = update(WebhookDeliveryRow).where(WebhookDeliveryRow.status == DEAD)
        if delivery_id is not None:
            query = query.where(WebhookDeliveryRow.id == delivery_id)
        with self.repository.Session() as s, s.begin():
            result = s.execute(query.values(status=PENDING, attempts=0, next_attempt_at=as_utc(self.clock())))
            count = int(getattr(result, "rowcount", 0))
        if delivery_id is not None and count == 0:
            raise NotFound(f"no dead webhook delivery {delivery_id}")
        return count
