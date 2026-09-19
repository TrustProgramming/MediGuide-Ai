"""Centralised error handling.

One place decides what a failure looks like to the caller. JSON routes get a
consistent envelope; UI routes get a readable page. Internal details - stack
traces, driver messages, file paths - are logged, never returned.

    {"error": {"code": "VALIDATION_FAILED", "message": "...", "details": [...]}}
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)

# Status code -> stable machine-readable code.
STATUS_CODES = {
    400: "BAD_REQUEST",
    401: "NOT_AUTHENTICATED",
    403: "NOT_AUTHORISED",
    404: "NOT_FOUND",
    409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    422: "VALIDATION_FAILED",
    429: "RATE_LIMITED",
    500: "INTERNAL_ERROR",
    503: "SERVICE_UNAVAILABLE",
}

SAFE_MESSAGES = {
    500: "Something went wrong on our side. The problem has been logged.",
    503: "A service this request depends on is unavailable. Please try again shortly.",
}


def _wants_html(request: Request) -> bool:
    if request.url.path.startswith("/ui"):
        return True
    return "text/html" in request.headers.get("accept", "")


def _envelope(code: str, message: str, *, details: Any = None, reference: str = "") -> Dict[str, Any]:
    error: Dict[str, Any] = {"code": code, "message": message}
    if details:
        error["details"] = details
    if reference:
        error["reference"] = reference
    return {"error": error}


def _html_page(status: int, code: str, message: str, reference: str = "") -> HTMLResponse:
    """A plain, self-contained error page. No app imports, so it cannot fail."""
    ref = (
        f"<p style='color:#5b7671;font-size:.85rem'>Reference: <code>{reference}</code></p>"
        if reference else ""
    )
    body = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{code} | MediGuide</title>
<style>body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#f2f7f5;
color:#0d2320;font-family:'DM Sans',system-ui,sans-serif;padding:24px}}
.box{{max-width:520px;background:#fff;border:1px solid #dde9e5;border-radius:18px;padding:32px;
box-shadow:0 4px 12px rgba(16,42,38,.07)}}h1{{margin:0 0 8px;font-size:1.4rem}}
p{{color:#5b7671;line-height:1.6}}a{{color:#0f7a63;font-weight:600}}</style></head>
<body><main class="box"><h1>{message}</h1>
<p>Error code: <strong>{code}</strong></p>{ref}
<p><a href="/ui">Return to MediGuide</a></p></main></body></html>"""
    return HTMLResponse(body, status_code=status)


def register_error_handlers(app) -> None:
    """Attach the handlers. Called once during application start-up."""

    # Unmatched routes raise Starlette's HTTPException, which is not the same
    # class FastAPI routes raise, so both are registered.
    @app.exception_handler(StarletteHTTPException)
    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        code = STATUS_CODES.get(exc.status_code, "ERROR")
        detail = exc.detail
        details = None

        # Routes that already raise a structured detail keep their own code.
        if isinstance(detail, dict):
            code = str(detail.get("error", code))
            message = str(detail.get("message", ""))
            details = detail.get("reasons") or detail.get("details")
        else:
            message = str(detail)

        if _wants_html(request):
            return _html_page(exc.status_code, code, message or code)
        return JSONResponse(
            _envelope(code, message, details=details),
            status_code=exc.status_code,
            headers=getattr(exc, "headers", None) or {},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # Field-level feedback is safe to return; it describes the request, not us.
        details = [
            {"field": ".".join(str(p) for p in err.get("loc", []) if p != "body"),
             "problem": err.get("msg", "invalid value")}
            for err in exc.errors()
        ]
        if _wants_html(request):
            return _html_page(422, "VALIDATION_FAILED", "Some of the details you entered are not valid.")
        return JSONResponse(
            _envelope("VALIDATION_FAILED", "The request contains invalid values.", details=details),
            status_code=422,
        )

    @app.exception_handler(IntegrityError)
    async def integrity_error(request: Request, exc: IntegrityError):
        reference = uuid.uuid4().hex[:12]
        # The driver message can name columns and values; log it, never return it.
        logger.warning("Database constraint violated [%s] on %s", reference, request.url.path, exc_info=True)
        message = "That change conflicts with information already stored."
        if _wants_html(request):
            return _html_page(409, "CONFLICT", message, reference)
        return JSONResponse(_envelope("CONFLICT", message, reference=reference), status_code=409)

    @app.exception_handler(OperationalError)
    async def database_unavailable(request: Request, exc: OperationalError):
        reference = uuid.uuid4().hex[:12]
        logger.error("Database unavailable [%s] on %s", reference, request.url.path, exc_info=True)
        message = SAFE_MESSAGES[503]
        if _wants_html(request):
            return _html_page(503, "DATABASE_UNAVAILABLE", message, reference)
        return JSONResponse(_envelope("DATABASE_UNAVAILABLE", message, reference=reference), status_code=503)

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, exc: SQLAlchemyError):
        reference = uuid.uuid4().hex[:12]
        logger.error("Database error [%s] on %s", reference, request.url.path, exc_info=True)
        message = SAFE_MESSAGES[500]
        if _wants_html(request):
            return _html_page(500, "DATABASE_ERROR", message, reference)
        return JSONResponse(_envelope("DATABASE_ERROR", message, reference=reference), status_code=500)

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        reference = uuid.uuid4().hex[:12]
        logger.exception("Unhandled error [%s] on %s", reference, request.url.path)
        message = SAFE_MESSAGES[500]
        if _wants_html(request):
            return _html_page(500, "INTERNAL_ERROR", message, reference)
        return JSONResponse(_envelope("INTERNAL_ERROR", message, reference=reference), status_code=500)
