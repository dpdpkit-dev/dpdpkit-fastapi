"""fastapi-shop: a tiny shop showing dpdpkit-fastapi end to end.

Authentication here is a stand-in: the `X-User` header names the user and `staff` is an admin.
Replace `current_user` and `staff_role` with your real auth.

Run:              uvicorn app:app --reload        (then open http://localhost:8000/docs)
Conformance:      DPDPKIT_CONFORMANCE=1 DPDPKIT_ASGI_APP=app:app dpdpkit-conformance
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import Depends, FastAPI, Request

from dpdpkit import ConsoleNotifier, Contact
from dpdpkit.fastapi import DPDPKit, require_consent

HERE = Path(__file__).parent
CONFORMANCE = os.environ.get("DPDPKIT_CONFORMANCE") == "1"

# Stand-in application data.
USERS: dict[str, dict[str, str]] = {
    "u_42": {"name": "Asha Rao", "email": "asha@example.in", "phone": "+919800000042"},
    "u_43": {"name": "Vikram Shah", "email": "vikram@example.in", "phone": "+919800000043"},
}
ORDERS: dict[str, list[dict[str, object]]] = {"u_42": [{"id": "o1", "total": 499}]}


def current_user(request: Request) -> str | None:
    if CONFORMANCE:  # test-only convention from dpdpkit-spec; never enable in production
        return request.headers.get("X-DPDP-Test-Principal")
    return request.headers.get("X-User")


def staff_role(request: Request) -> str | None:
    if CONFORMANCE:
        return request.headers.get("X-DPDP-Test-Role")
    return "admin" if request.headers.get("X-User") == "staff" else None


def contact_for(principal: str) -> Contact | None:
    user = USERS.get(principal)
    return Contact(email=user["email"], phone=user["phone"]) if user else None


app = FastAPI(title="fastapi-shop (dpdpkit demo)")
dpdp = DPDPKit(
    db_url=os.environ.get("DATABASE_URL", "sqlite://" if CONFORMANCE else f"sqlite:///{HERE / 'shop.db'}"),
    config=HERE / "dpdpkit.yaml",
    principal_resolver=current_user,
    admin_guard=staff_role,
    notifier=ConsoleNotifier(),
    contact_resolver=contact_for,
    signing_key=os.environ.get("DPDPKIT_SIGNING_KEY", "dev-only-signing-key"),
    migrate_on_startup=True,
)
dpdp.install(app, prefix="/dpdp")


@dpdp.kit.retention.register_handler
def erase_user_data(principal: str, purposes: list[str]) -> None:
    if "account" in purposes:
        USERS.pop(principal, None)
        ORDERS.pop(principal, None)


dpdp.kit.export.register_collector("orders", lambda principal: {"orders": ORDERS.get(principal, [])})


@app.get("/me")
def me(request: Request) -> dict[str, object]:
    user = current_user(request)
    return {"user": user, "profile": USERS.get(user or "")}


@app.post("/offers/send", dependencies=[Depends(require_consent("marketing"))])
def send_offer() -> dict[str, bool]:
    return {"sent": True}
