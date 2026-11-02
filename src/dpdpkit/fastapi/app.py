"""``DPDPKit`` — the object a FastAPI developer creates."""

from __future__ import annotations

import contextlib
import inspect
import logging
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING, Any, ParamSpec, TypeVar

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from dpdpkit import Kit, Registry
from dpdpkit.config import kit_from_config, load_config, sync_notice
from dpdpkit.errors import ConfigError, DpdpkitError
from dpdpkit.events import LEDGER_TAMPERED, WebhookEndpoint
from dpdpkit.guardians import GuardianVerifier
from dpdpkit.kit import ContactResolver
from dpdpkit.ledger import RootSink
from dpdpkit.models import Role, RootHash
from dpdpkit.notify import Notifier
from dpdpkit.policy import Policy
from dpdpkit.repository import Repository
from dpdpkit.rights import IdentityVerifier

from .http import ConsentRequiredHTTP, Forbidden, Unauthenticated, consent_required_handler, render_dpdpkit_error
from .repository import SqlAlchemyRepository
from .router import build_router
from .webhooks import WebhookOutbox, endpoints_from_config

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger("dpdpkit.fastapi")

P = ParamSpec("P")
T = TypeVar("T")

PrincipalResolver = Callable[[Request], "str | Awaitable[str | None] | None"]
AdminGuard = Callable[[Request], "Role | str | bool | Awaitable[Role | str | bool | None] | None"]

_default: DPDPKit | None = None


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


class DPDPKit:
    """Wire dpdpkit into a FastAPI app.

    ``webhooks`` are delivered through a database outbox (see :mod:`dpdpkit.fastapi.webhooks`); when
    omitted they are read from the ``webhooks:`` list in the config, with secrets from ``secret_env``.

    ``guardian_verifiers`` back ``POST /guardian/verify/*``: each verifier's ``name`` is the ``method`` a client
    may ask for (``existing_kyc``, ``digilocker_token``, ``partner_token``, ``court_order``). With none registered
    the start endpoint answers 422.

    ``principal_resolver(request)`` returns a stable, non-personal reference for the signed-in user
    (for example the user's primary key), or ``None``. ``admin_guard(request)`` returns a role
    (``viewer``/``handler``/``admin``), ``True`` for admin, or ``None``/``False`` for no access.
    """

    def __init__(
        self,
        *,
        principal_resolver: PrincipalResolver,
        admin_guard: AdminGuard | None = None,
        db_url: str | None = None,
        engine: Any = None,
        repository: Repository | None = None,
        config: str | Path | Mapping[str, Any] | None = None,
        policy: str | Policy | None = None,
        overlays: Iterable[str] = (),
        registry: Registry | None = None,
        notifier: Notifier | None = None,
        contact: Mapping[str, str] | None = None,
        contact_resolver: ContactResolver | None = None,
        identity_verifier: IdentityVerifier | None = None,
        signing_key: bytes | str | None = None,
        tenant_id: str = "default",
        webhooks: Iterable[WebhookEndpoint] | None = None,
        guardian_verifiers: Iterable[GuardianVerifier] = (),
        migrate_on_startup: bool = False,
        create_tables: bool = False,
        **kit_kwargs: Any,
    ) -> None:
        if repository is None:
            if not (db_url or engine):
                raise ConfigError("pass db_url, engine or repository")
            repository = SqlAlchemyRepository(engine if engine is not None else db_url)
        self.repository = repository
        if migrate_on_startup:
            self.migrate()
        elif create_tables and isinstance(repository, SqlAlchemyRepository):
            repository.create_all()

        common: dict[str, Any] = {
            "notifier": notifier,
            "contact_resolver": contact_resolver,
            "identity_verifier": identity_verifier,
            "signing_key": signing_key,
            "tenant_id": tenant_id,
            **kit_kwargs,
        }
        self.config: dict[str, Any] | None = None
        if config is not None:
            self.config = load_config(config) if isinstance(config, (str, Path)) else dict(config)
            self.kit = kit_from_config(self.config, repository, registry=registry, contact=contact, **common)
            sync_notice(self.kit, self.config)
        else:
            if policy is None:
                raise ConfigError("pass config= (a dpdpkit.yaml) or policy=")
            self.kit = Kit(repository, policy, overlays=overlays, registry=registry, contact=contact, **common)

        for verifier in guardian_verifiers:
            self.kit.guardians.register_verifier(verifier)
        self.principal_resolver = principal_resolver
        self.admin_guard = admin_guard
        self.router = build_router(self)
        self._scheduler: Any = None

        if webhooks is None and self.config is not None:
            webhooks = endpoints_from_config(self.config.get("webhooks") or [])
        self.webhooks: WebhookOutbox | None = None
        if isinstance(repository, SqlAlchemyRepository):
            self.webhooks = WebhookOutbox(repository, webhooks or [], clock=self.kit.now)
            self.webhooks.attach(self.kit.events)
        elif webhooks:
            raise ConfigError("durable webhooks need the SQLAlchemy repository")

        global _default
        _default = self

    # ------------------------------------------------------------------ identity

    async def resolve_principal(self, request: Request) -> str | None:
        value = await _maybe_await(self.principal_resolver(request))
        return str(value) if value else None

    async def require_principal(self, request: Request) -> str:
        principal = await self.resolve_principal(request)
        if not principal:
            raise Unauthenticated("sign in to use this endpoint")
        return principal

    async def resolve_role(self, request: Request) -> Role | None:
        if self.admin_guard is None:
            return None
        value = await _maybe_await(self.admin_guard(request))
        if value is True:
            return Role.ADMIN
        if not value:
            return None
        try:
            return Role(value)
        except ValueError:
            log.warning("admin_guard returned unknown role %r; treating as no access", value)
            return None

    async def require_role(self, request: Request, needed: Role) -> str:
        """Check the caller's admin role; returns an actor label for the audit trail."""
        role = await self.resolve_role(request)
        if role is None:
            principal = await self.resolve_principal(request)
            if principal is None:
                raise Unauthenticated("sign in to use this endpoint")
            raise Forbidden("an admin role is required")
        if not role.allows(needed):
            raise Forbidden(f"the {needed.value} role is required", role=role.value)
        principal = await self.resolve_principal(request)
        return f"{role.value}:{principal}" if principal else role.value

    @staticmethod
    def request_metadata(request: Request) -> dict[str, Any]:
        """Evidence stored with consent events. No IP address or personal data by default."""
        return {"channel": "web", "user_agent_present": "user-agent" in request.headers}

    # ------------------------------------------------------------------ async

    async def run(self, fn: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
        """Call a kit method from ``async def`` code without blocking the event loop::

        allowed = await dpdp.run(dpdp.kit.consent.check, user_id, "marketing")
        """
        return await run_in_threadpool(fn, *args, **kwargs)

    def bind(self, session: Session) -> contextlib.AbstractContextManager[Session]:
        """Run kit calls inside the app's sync ``Session``; see :meth:`SqlAlchemyRepository.bind`."""
        return self._sql_repository().bind(session)

    def _sql_repository(self) -> SqlAlchemyRepository:
        if not isinstance(self.repository, SqlAlchemyRepository):
            raise ConfigError("binding an app session needs the SqlAlchemyRepository")
        return self.repository

    async def run_in_session(self, session: AsyncSession, fn: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
        """Call a kit method inside the app's ``AsyncSession``, so its writes commit or roll back with the
        app's own work::

            async with AsyncSession(engine) as s, s.begin():
                s.add(Order(...))
                await dpdp.run_in_session(s, dpdp.kit.consent.grant, user_id, "marketing")

        dpdpkit never commits the session. The call runs through ``AsyncSession.run_sync`` on the event
        loop, so keep it to database work; event listeners see the event before the app commits."""
        repository = self._sql_repository()

        def call(sync_session: Session) -> T:
            with repository.bind(sync_session):
                return fn(*args, **kwargs)

        return await session.run_sync(call)

    # ------------------------------------------------------------------ consent dependency

    def require_consent(self, purpose: str) -> Callable[[Request], Awaitable[None]]:
        async def dependency(request: Request) -> None:
            principal = await self.require_principal(request)
            allowed = await run_in_threadpool(self.kit.consent.check, principal, purpose)
            if not allowed:
                raise ConsentRequiredHTTP(purpose)

        return dependency

    # ------------------------------------------------------------------ app wiring

    def install(
        self,
        app: FastAPI,
        *,
        prefix: str = "/dpdp",
        admin_path: str | None = None,
        track_activity: bool = True,
    ) -> None:
        """Include the router, error handlers, activity middleware and the admin dashboard."""
        app.include_router(self.router, prefix=prefix)
        self.install_error_handlers(app)
        if track_activity:
            app.add_middleware(ActivityMiddleware, dpdpkit=self)
        self.mount_admin(app, path=admin_path or f"{prefix}/admin/ui")

    def install_error_handlers(self, app: FastAPI) -> None:
        app.state.dpdpkit = self

        async def dpdpkit_error(_request: Request, exc: Exception) -> Response:
            assert isinstance(exc, DpdpkitError)
            return render_dpdpkit_error(exc)

        app.add_exception_handler(DpdpkitError, dpdpkit_error)
        app.add_exception_handler(ConsentRequiredHTTP, consent_required_handler)

    def mount_admin(self, app: FastAPI, path: str = "/dpdp/admin/ui") -> None:
        """Serve the prebuilt admin dashboard bundle. Until it ships (v0.4) a placeholder page is served."""
        if not any(getattr(r, "path", None) == path for r in app.routes):
            directory = resources.files("dpdpkit.fastapi") / "admin_static"
            app.mount(path, StaticFiles(directory=str(directory), html=True), name="dpdpkit-admin")
        if not hasattr(app.state, "dpdpkit"):
            self.install_error_handlers(app)

    # ------------------------------------------------------------------ jobs and schema

    def start_scheduler(
        self,
        *,
        retention_minutes: int = 15,
        verify_hour: int = 2,
        root_export_hour: int | None = 3,
        root_sinks: Iterable[RootSink] = (),
        webhook_seconds: int = 30,
    ) -> Any:
        """Run retention every ``retention_minutes``, ledger verification daily at ``verify_hour`` and
        a root-hash export daily at ``root_export_hour`` UTC (``None`` disables it) (APScheduler).

        The export writes to each of ``root_sinks`` and publishes ``ledger.root_exported``; subscribe a
        webhook to that event to hand a copy to a third party. Due webhook deliveries are sent every
        ``webhook_seconds`` when any endpoint is configured."""
        try:
            from apscheduler.schedulers.background import BackgroundScheduler  # type: ignore[import-untyped]
        except ImportError as exc:  # pragma: no cover
            raise ConfigError('install the scheduler extra: pip install "dpdpkit-fastapi[scheduler]"') from exc
        scheduler = BackgroundScheduler(timezone="UTC")
        scheduler.add_job(
            self.kit.retention.run,
            "interval",
            minutes=retention_minutes,
            id="dpdpkit-retention",
            max_instances=1,
            coalesce=True,
        )
        scheduler.add_job(
            self.verify_ledger_job, "cron", hour=verify_hour, id="dpdpkit-ledger-verify", max_instances=1, coalesce=True
        )
        if root_export_hour is not None:
            sinks = tuple(root_sinks)
            scheduler.add_job(
                lambda: self.export_root_job(*sinks),
                "cron",
                hour=root_export_hour,
                id="dpdpkit-ledger-root-export",
                max_instances=1,
                coalesce=True,
            )
        if self.webhooks is not None and self.webhooks.endpoints:
            scheduler.add_job(
                self.webhooks.deliver_due,
                "interval",
                seconds=webhook_seconds,
                id="dpdpkit-webhooks",
                max_instances=1,
                coalesce=True,
            )
        scheduler.start()
        self._scheduler = scheduler
        return scheduler

    def verify_ledger_job(self) -> bool:
        report = self.kit.ledger.verify_report()
        if not report.ok:
            log.error(
                "dpdpkit ledger verification FAILED at row %s (%s): %s", report.failed_row, report.chain, report.reason
            )
            self.kit.events.publish(LEDGER_TAMPERED, self.kit.tenant_id, report.model_dump(mode="json"))
        return report.ok

    def export_root_job(self, *sinks: RootSink) -> RootHash | None:
        """Export the ledger root hash. A failing sink is logged and the job reports ``None``."""
        try:
            return self.kit.ledger.export_root(*sinks)
        except Exception:
            log.exception("dpdpkit root-hash export failed")
            return None

    def migrate(self, revision: str = "head") -> None:
        from .migrations import upgrade

        if not isinstance(self.repository, SqlAlchemyRepository):
            raise ConfigError("migrate() needs the SQLAlchemy repository")
        upgrade(self.repository.engine, revision)


def default_kit(request: Request | None = None) -> DPDPKit:
    if request is not None:
        found = getattr(request.app.state, "dpdpkit", None)
        if isinstance(found, DPDPKit):
            return found
    if _default is None:
        raise ConfigError("create a DPDPKit before using require_consent()")
    return _default


def require_consent(purpose: str) -> Callable[[Request], Awaitable[None]]:
    """FastAPI dependency: 403 ``consent_required`` unless the caller has consented to ``purpose``."""

    async def dependency(request: Request) -> None:
        await default_kit(request).require_consent(purpose)(request)

    return dependency


class ActivityMiddleware(BaseHTTPMiddleware):
    """Records principal activity (which restarts inactivity clocks and cancels warned erasures).
    Throttled per principal so busy users do not write on every request."""

    def __init__(self, app: Any, dpdpkit: DPDPKit, min_interval_seconds: float = 300) -> None:
        super().__init__(app)
        self.dpdpkit = dpdpkit
        self.min_interval = min_interval_seconds
        self._seen: dict[str, float] = {}

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        if response.status_code >= 400:
            return response
        try:
            principal = await self.dpdpkit.resolve_principal(request)
        except Exception:
            return response
        if principal:
            now = time.monotonic()
            if now - self._seen.get(principal, -1e12) >= self.min_interval:
                self._seen[principal] = now
                try:
                    await run_in_threadpool(self.dpdpkit.kit.retention.record_activity, principal)
                except Exception:
                    log.exception("failed to record activity for a principal")
        return response
