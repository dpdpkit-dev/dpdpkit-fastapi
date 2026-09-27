"""The REST contract from dpdpkit-spec's ``openapi.yaml``, implemented on dpdpkit-core.

No ``from __future__ import annotations`` here: FastAPI must evaluate the dependency annotations that
are defined inside :func:`build_router`.
"""

import datetime as dt
from typing import TYPE_CHECKING, Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from dpdpkit._util import as_utc
from dpdpkit.errors import ValidationFailed
from dpdpkit.models import (
    ConsentReceipt,
    ConsentState,
    Notice,
    RequestKind,
    RequestStatus,
    RightsRequest,
    Role,
)

from .http import DpdpRoute, NotImplementedYet

if TYPE_CHECKING:
    from .app import DPDPKit


# --------------------------------------------------------------------------- request bodies


class Decision(BaseModel):
    purpose: str = Field(min_length=1)
    granted: bool


class ConsentDecisionIn(BaseModel):
    notice_version: int | None = None
    locale: str | None = None
    decisions: list[Decision] = Field(min_length=1)


class RequestIn(BaseModel):
    kind: RequestKind
    details: dict[str, Any] = Field(default_factory=dict)


class AdminUpdateIn(BaseModel):
    action: Literal["assign", "start", "verify_identity", "respond", "reject"]
    assignee: str | None = None
    verified: bool | None = None
    method: str | None = None
    message: str | None = None


class ActivityIn(BaseModel):
    principal: str = Field(min_length=1)
    at: dt.datetime | None = None


# --------------------------------------------------------------------------- response shapes


def notice_out(notice: Notice, locales: list[str]) -> dict[str, Any]:
    content = notice.content.model_dump(mode="json")
    return {
        "version": notice.version,
        "locale": notice.locale,
        "content_hash": notice.content_hash,
        "published_at": notice.published_at.isoformat(),
        "available_locales": locales,
        **content,
    }


def state_out(state: ConsentState) -> dict[str, Any]:
    return state.model_dump(mode="json", exclude={"tenant_id", "principal"})


def receipt_out(receipt: ConsentReceipt) -> dict[str, Any]:
    data = receipt.model_dump(mode="json", exclude={"tenant_id"})
    data["consents"] = [state_out(c) for c in receipt.consents]
    return data


def request_out(req: RightsRequest, now: dt.datetime, *, admin: bool = False) -> dict[str, Any]:
    hidden = (
        {"tenant_id", "updated_at"}
        if admin
        else {"tenant_id", "updated_at", "principal", "assignee", "identity_verified"}
    )
    data = req.model_dump(mode="json", exclude=hidden)
    data["overdue"] = req.closed_at is None and now > req.due_at
    return data


# --------------------------------------------------------------------------- router


def build_router(dk: "DPDPKit") -> APIRouter:
    kit = dk.kit
    router = APIRouter(route_class=DpdpRoute, tags=["dpdpkit"])

    async def principal(request: Request) -> str:
        return await dk.require_principal(request)

    def role(needed: Role) -> Any:
        async def dependency(request: Request) -> str:
            return await dk.require_role(request, needed)

        return Depends(dependency)

    Principal = Annotated[str, Depends(principal)]

    # ------------------------------------------------------------------ notices

    @router.get("/notices/current")
    def current_notice(locale: str | None = None) -> dict[str, Any]:
        notice = kit.notices.current(locale)
        return notice_out(notice, kit.notices.locales(notice.version))

    # ------------------------------------------------------------------ consents

    @router.post("/consents", status_code=201)
    def record_consents(body: ConsentDecisionIn, who: Principal, request: Request) -> dict[str, Any]:
        decisions = {d.purpose: d.granted for d in body.decisions}
        if len(decisions) != len(body.decisions):
            raise ValidationFailed("each purpose may appear only once")
        states = kit.consent.record(
            who,
            decisions,
            notice_version=body.notice_version,
            locale=body.locale,
            source="api",
            metadata=dk.request_metadata(request),
        )
        return {"consents": [state_out(s) for s in states]}

    @router.post("/consents/{purpose}/withdraw")
    def withdraw(purpose: str, who: Principal, request: Request) -> dict[str, Any]:
        return state_out(kit.consent.withdraw(who, purpose, source="api", metadata=dk.request_metadata(request)))

    @router.get("/consents/me")
    def my_consents(who: Principal) -> dict[str, Any]:
        return {"consents": [state_out(s) for s in kit.consent.state(who)]}

    @router.get("/consents/me/receipt")
    def my_receipt(who: Principal) -> dict[str, Any]:
        return receipt_out(kit.consent.receipt(who))

    # ------------------------------------------------------------------ rights

    @router.post("/requests", status_code=201)
    def open_request(body: RequestIn, who: Principal) -> dict[str, Any]:
        return request_out(kit.rights.open(who, body.kind, body.details), kit.now())

    @router.get("/requests")
    def my_requests(who: Principal) -> dict[str, Any]:
        now = kit.now()
        return {"items": [request_out(r, now) for r in kit.rights.list_requests(principal=who)]}

    @router.get("/requests/{request_id}")
    def get_request(request_id: str, who: Principal) -> dict[str, Any]:
        return request_out(kit.rights.get(request_id, principal=who), kit.now())

    @router.get("/me/export", response_model=None)
    def my_export(who: Principal, format: Literal["json", "html"] = "json") -> Response:
        if format == "html":
            return HTMLResponse(kit.export.principal_html(who))
        return JSONResponse(kit.export.principal(who))

    # ------------------------------------------------------------------ admin

    @router.get("/admin/requests", dependencies=[role(Role.VIEWER)])
    def admin_requests(status: RequestStatus | None = None, open: Annotated[bool, Query()] = False) -> dict[str, Any]:
        now = kit.now()
        reqs = kit.rights.list_requests(statuses=[status] if status else None, open_only=open)
        return {"items": [request_out(r, now, admin=True) for r in reqs]}

    @router.get("/admin/requests/{request_id}", dependencies=[role(Role.VIEWER)])
    def admin_request(request_id: str) -> dict[str, Any]:
        return request_out(kit.rights.get(request_id), kit.now(), admin=True)

    @router.patch("/admin/requests/{request_id}")
    def admin_update(request_id: str, body: AdminUpdateIn, actor: Annotated[str, role(Role.HANDLER)]) -> dict[str, Any]:
        rights = kit.rights
        if body.action == "assign":
            if not body.assignee:
                raise ValidationFailed("assignee is required")
            req = rights.assign(request_id, body.assignee, actor=actor)
        elif body.action == "start":
            req = rights.start(request_id, actor=actor)
        elif body.action == "verify_identity":
            if body.verified is None:
                raise ValidationFailed("verified is required")
            req = rights.verify_identity(request_id, body.verified, actor=actor, method=body.method)
        elif body.action == "respond":
            req = rights.respond(request_id, body.message or "", actor=actor)
        else:
            req = rights.reject(request_id, body.message or "", actor=actor)
        return request_out(req, kit.now(), admin=True)

    @router.get("/admin/retention/preview", dependencies=[role(Role.VIEWER)])
    def retention_preview(hours: Annotated[int, Query(ge=1)] = 168) -> dict[str, Any]:
        items = kit.retention.preview(horizon=dt.timedelta(hours=hours))
        attention = kit.retention.needs_attention()
        return {
            "items": [i.model_dump(mode="json") for i in items],
            "needs_attention": [
                {"schedule_id": s.id, "principal": s.principal, "purpose": s.purpose, "reason": s.attention_reason}
                for s in attention
            ],
        }

    @router.get("/admin/incidents", dependencies=[role(Role.VIEWER)])
    def list_incidents() -> None:
        raise NotImplementedYet("the incident register ships in v0.5 (task CORE-24)")

    @router.post("/admin/incidents", dependencies=[role(Role.HANDLER)])
    def create_incident() -> None:
        raise NotImplementedYet("the incident register ships in v0.5 (task CORE-24)")

    @router.get("/admin/incidents/{incident_id}/report", dependencies=[role(Role.HANDLER)])
    def incident_report(incident_id: str) -> None:
        raise NotImplementedYet("incident report drafts ship in v0.5 (task CORE-24)")

    @router.get("/admin/audit/export", dependencies=[role(Role.ADMIN)], response_model=None)
    def audit_export() -> Response:
        stamp = kit.now().strftime("%Y%m%dT%H%M%SZ")
        return Response(
            kit.export.evidence_pack(),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="dpdpkit-evidence-{stamp}.zip"'},
        )

    @router.post("/admin/ledger/verify", dependencies=[role(Role.VIEWER)])
    def verify_ledger() -> dict[str, Any]:
        return kit.ledger.verify_report().model_dump(mode="json", exclude={"tenant_id"})

    # ------------------------------------------------------------------ events

    @router.post("/events/activity", status_code=202, dependencies=[role(Role.HANDLER)])
    def activity(body: ActivityIn) -> dict[str, Any]:
        kit.retention.record_activity(body.principal, as_utc(body.at) if body.at else None)
        return {"accepted": True}

    return router
