"""Error rendering and the route class that turns dpdpkit errors into the contract's ``Error`` body."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from dpdpkit.errors import DpdpkitError


class Unauthenticated(DpdpkitError):
    code = "unauthenticated"
    http_status = 401


class Forbidden(DpdpkitError):
    code = "forbidden"
    http_status = 403


class NotImplementedYet(DpdpkitError):
    code = "not_implemented"
    http_status = 501


class ConsentRequiredHTTP(HTTPException):
    """Raised by :func:`require_consent`. Rendered as the contract's Error body once the kit is installed
    on the app; otherwise FastAPI renders it as ``{"detail": {...}}`` with the same code."""

    def __init__(self, purpose: str) -> None:
        super().__init__(
            status_code=403,
            detail={
                "code": "consent_required",
                "message": f"consent required for purpose {purpose!r}",
                "details": {"purpose": purpose},
            },
        )


def error_response(status: int, code: str, message: str, details: dict[str, Any] | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status, content={"error": {"code": code, "message": message, "details": details or {}}}
    )


def render_dpdpkit_error(exc: DpdpkitError) -> JSONResponse:
    status = exc.http_status if exc.http_status != 500 else 400
    return error_response(status, exc.code, exc.message, exc.details)


def render_validation_error(exc: RequestValidationError) -> JSONResponse:
    errors = [
        {"loc": [str(p) for p in e.get("loc", ())], "msg": str(e.get("msg", "")), "type": str(e.get("type", ""))}
        for e in exc.errors()
    ]
    return error_response(422, "invalid_request", "request body or parameters are invalid", {"errors": errors})


async def consent_required_handler(_request: Request, exc: Exception) -> Response:
    assert isinstance(exc, ConsentRequiredHTTP)
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})


class DpdpRoute(APIRoute):
    """Every dpdpkit endpoint answers errors with ``{"error": {code, message, details}}``."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def route(request: Request) -> Response:
            try:
                return await handler(request)
            except DpdpkitError as exc:
                return render_dpdpkit_error(exc)
            except RequestValidationError as exc:
                return render_validation_error(exc)

        return route
