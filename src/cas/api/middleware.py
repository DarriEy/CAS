# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""HTTP middleware and exception handlers for the CAS API.

Provides a request-ID + structured access log middleware and a consistent
JSON error envelope so every error response has the shape::

    {"error": {"type": ..., "message": ..., "request_id": ...}}
"""

from __future__ import annotations

import time
from uuid import uuid4

import structlog
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from cas.api.metrics import REQUEST_LATENCY, REQUESTS
from cas.api.security import AuthError, RateLimitExceededError
from cas.core.config import get_settings
from cas.core.exceptions import ConnectorError, ExtractionError, RequestLimitError

logger = structlog.get_logger()

REQUEST_ID_HEADER = "X-Request-ID"


def _request_id(request: Request) -> str:
    rid = getattr(request.state, "request_id", None)
    return rid or "unknown"


def _error_response(
    status_code: int,
    error_type: str,
    message: str,
    request_id: str,
    detail: object | None = None,
) -> JSONResponse:
    error: dict[str, object] = {
        "type": error_type,
        "message": message,
        "request_id": request_id,
    }
    if detail is not None:
        error["detail"] = detail
    return JSONResponse(
        status_code=status_code,
        content={"error": error},
        headers={REQUEST_ID_HEADER: request_id},
    )


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assign a request id, bind it to structlog, log access, echo the header."""

    async def dispatch(self, request: Request, call_next):
        request_id = uuid4().hex[:12]
        request.state.request_id = request_id
        structlog.contextvars.bind_contextvars(request_id=request_id)
        start = time.monotonic()
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.unbind_contextvars("request_id")
        elapsed = time.monotonic() - start
        response.headers[REQUEST_ID_HEADER] = request_id
        # Use the matched route template (not the raw path) to bound label cardinality.
        route = request.scope.get("route")
        path = getattr(route, "path", request.url.path)
        REQUESTS.labels(request.method, path, str(response.status_code)).inc()
        REQUEST_LATENCY.labels(request.method, path).observe(elapsed)
        logger.info(
            "request",
            method=request.method,
            path=path,
            status=response.status_code,
            elapsed_ms=int(elapsed * 1000),
        )
        return response


def register_middleware(app: FastAPI) -> None:
    """Attach CORS + request-context middleware and error handlers."""
    settings = get_settings()

    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(RequestValidationError)
    async def _on_validation(request: Request, exc: RequestValidationError):
        # Re-shape FastAPI's default {"detail": [...]} into the CAS envelope,
        # preserving the structured per-field errors under "detail".
        return _error_response(
            422,
            "validation_error",
            "Request validation failed",
            _request_id(request),
            detail=jsonable_encoder(exc.errors()),
        )

    @app.exception_handler(RequestLimitError)
    async def _on_request_limit(request: Request, exc: RequestLimitError):
        return _error_response(422, "request_limit", str(exc), _request_id(request))

    @app.exception_handler(AuthError)
    async def _on_auth(request: Request, exc: AuthError):
        return _error_response(401, "unauthorized", str(exc), _request_id(request))

    @app.exception_handler(RateLimitExceededError)
    async def _on_rate_limit(request: Request, exc: RateLimitExceededError):
        resp = _error_response(429, "rate_limited", str(exc), _request_id(request))
        resp.headers["Retry-After"] = str(exc.retry_after)
        return resp

    @app.exception_handler(ConnectorError)
    @app.exception_handler(ExtractionError)
    async def _on_upstream(request: Request, exc: Exception):
        logger.warning("upstream_error", error=str(exc), path=request.url.path)
        return _error_response(502, "upstream_error", str(exc), _request_id(request))

    @app.exception_handler(Exception)
    async def _on_unhandled(request: Request, exc: Exception):
        logger.error("unhandled_error", error=str(exc), path=request.url.path)
        return _error_response(
            500, "internal_error", "Internal server error", _request_id(request),
        )
