"""
Enhanced Error Handling Patterns for Spinr
Provides structured error handling, custom exceptions, and error middleware.
"""

import re
import traceback
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from loguru import logger

# B-P2-1: short ALL-CAPS sentinels (e.g. ERR_AUTH_UNAVAILABLE,
# ERR_OTP_LOCKED) are vetted by the route author and let mobile clients
# branch UI without parsing English. Anything else on a 5xx response is
# treated as a possible exception leak and replaced with a generic
# message — full detail still hits the server log paired with the
# request_id. See docs/runbooks/error-responses.md.
_SENTINEL_DETAIL_RE = re.compile(r"^ERR_[A-Z0-9_]+$")


def _resolve_request_id(request: Request) -> str:
    """Read the request_id set by RequestIDMiddleware (core/middleware.py).

    Falls back to a fresh UUID only if the middleware didn't run, which
    happens in test harnesses that mount handlers without the full
    middleware stack. In production every request has a state-bound id
    that's also bound to the loguru context — so the request_id in the
    error body MATCHES the request_id in every server log line for that
    request lifecycle. Without this, support tickets quoting a client-
    side request_id couldn't be cross-referenced against logs.
    """
    rid = getattr(getattr(request, "state", None), "request_id", None)
    if isinstance(rid, str) and rid:
        return rid
    return uuid.uuid4().hex[:12]


def _should_sanitize_5xx_detail(detail: object) -> bool:
    """True if a 5xx HTTPException detail looks like an exception leak
    rather than a vetted sentinel.

    Pass-through criteria: short ALL-CAPS ``ERR_*`` sentinel.
    Sanitize criteria: anything else — non-string detail, free text,
    interpolated exception strings, etc.

    The single rule (sentinel-or-sanitize) is deliberately strict so
    a future contributor can't accidentally leak by writing a
    plausible-sounding sentence. Routes that need to convey 5xx info
    should either (a) raise a ``SpinrException`` (structured fields,
    explicit message) or (b) introduce a new ``ERR_*`` sentinel.
    """
    if not isinstance(detail, str):
        return True
    return not bool(_SENTINEL_DETAIL_RE.match(detail))


class ErrorCode(Enum):
    """Standardized error codes for the application."""

    # Authentication errors (1000-1999)
    AUTH_REQUIRED = 1001
    AUTH_INVALID_TOKEN = 1002
    AUTH_TOKEN_EXPIRED = 1003
    AUTH_INVALID_CREDENTIALS = 1004
    AUTH_INSUFFICIENT_PERMISSIONS = 1005
    AUTH_ACCOUNT_DISABLED = 1006
    AUTH_OTP_EXPIRED = 1007
    AUTH_OTP_INVALID = 1008

    # Validation errors (2000-2999)
    VALIDATION_ERROR = 2001
    VALIDATION_INVALID_FORMAT = 2002
    VALIDATION_MISSING_FIELD = 2003
    VALIDATION_INVALID_RANGE = 2004

    # Resource errors (3000-3999)
    RESOURCE_NOT_FOUND = 3001
    RESOURCE_ALREADY_EXISTS = 3002
    RESOURCE_CONFLICT = 3003
    RESOURCE_LOCKED = 3004

    # Ride errors (4000-4999)
    RIDE_NOT_FOUND = 4001
    RIDE_INVALID_STATUS = 4002
    RIDE_ALREADY_CANCELLED = 4003
    RIDE_NO_DRIVERS_AVAILABLE = 4004
    RIDE_PRICE_MISMATCH = 4005

    # Driver errors (5000-5999)
    DRIVER_NOT_FOUND = 5001
    DRIVER_NOT_AVAILABLE = 5002
    DRIVER_OFFLINE = 5003
    DRIVER_DOCUMENTS_PENDING = 5004
    DRIVER_DOCUMENTS_REJECTED = 5005
    DRIVER_QUOTA_EXCEEDED = 5006

    # Payment errors (6000-6999)
    PAYMENT_FAILED = 6001
    PAYMENT_METHOD_INVALID = 6002
    PAYMENT_INSUFFICIENT_FUNDS = 6003
    PAYMENT_REFUND_FAILED = 6004
    PAYMENT_UNPAID_RIDE_BLOCK = 6005

    # System errors (9000-9999)
    INTERNAL_ERROR = 9001
    SERVICE_UNAVAILABLE = 9002
    RATE_LIMIT_EXCEEDED = 9003
    EXTERNAL_SERVICE_ERROR = 9004
    DATABASE_ERROR = 9005
    DUPLICATE_RECORD = 9006


class SpinrException(Exception):
    """Base exception for Spinr application."""

    def __init__(
        self,
        message: str,
        error_code: ErrorCode = ErrorCode.INTERNAL_ERROR,
        status_code: int = 500,
        details: Optional[Dict[str, Any]] = None,
        should_log: bool = True,
        message_key: Optional[str] = None,
        action_hint: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        self.message = message
        self.error_code = error_code
        self.status_code = status_code
        self.details = details or {}
        self.should_log = should_log
        self.timestamp = datetime.now(timezone.utc).isoformat()
        # i18n lookup key resolved client-side (e.g. "errors.auth.account_suspended").
        # Mobile apps prefer this over `message` so the user sees the localised
        # string. Backwards compatible: callers that don't set it omit the
        # field entirely (see to_dict).
        self.message_key = message_key
        # Short user-action hint shown alongside the error (e.g. "Renew documents
        # in Profile"). Optional; mobile apps render it as a secondary line in
        # Alert dialogs when present.
        self.action_hint = action_hint
        # Extra HTTP response headers (e.g. Retry-After, RateLimit-* for 429s).
        # Merged into the JSONResponse headers after CORS + X-Request-ID, so
        # route-supplied values win over defaults for everything except the
        # correlation id (X-Request-ID is re-pinned after the merge).
        self.headers: Dict[str, str] = headers or {}

        super().__init__(self.message)

    def to_dict(self) -> Dict[str, Any]:
        """Convert exception to dictionary for JSON response."""
        error: Dict[str, Any] = {
            "code": self.error_code.value,
            "message": self.message,
            "details": self.details if self.details else None,
            "timestamp": self.timestamp,
        }
        if self.message_key is not None:
            error["message_key"] = self.message_key
        if self.action_hint is not None:
            error["action_hint"] = self.action_hint
        return {"success": False, "error": error}


# Authentication exceptions
class AuthenticationException(SpinrException):
    """Base authentication exception."""

    def __init__(self, message: str = "Authentication required", **kwargs):
        # Subclasses (TokenExpiredException, InvalidOTPException, …) pass
        # their own ``error_code`` / ``status_code`` via super().__init__(…,
        # **kwargs) — so honour those when present rather than hard-coding
        # the AUTH_REQUIRED / 401 defaults, which previously caused a
        # ``multiple values for keyword argument`` TypeError.
        kwargs.setdefault("error_code", ErrorCode.AUTH_REQUIRED)
        kwargs.setdefault("status_code", 401)
        super().__init__(message=message, **kwargs)


class InvalidTokenException(AuthenticationException):
    """Invalid authentication token."""

    def __init__(self, message: str = "Invalid authentication token", **kwargs):
        super().__init__(
            message=message,
            error_code=ErrorCode.AUTH_INVALID_TOKEN,
            status_code=401,
            **kwargs,
        )


class TokenExpiredException(AuthenticationException):
    """Expired authentication token."""

    def __init__(self, message: str = "Token has expired", **kwargs):
        super().__init__(
            message=message,
            error_code=ErrorCode.AUTH_TOKEN_EXPIRED,
            status_code=401,
            **kwargs,
        )


class InvalidCredentialsException(AuthenticationException):
    """Invalid login credentials."""

    def __init__(self, message: str = "Invalid credentials", **kwargs):
        super().__init__(
            message=message,
            error_code=ErrorCode.AUTH_INVALID_CREDENTIALS,
            status_code=401,
            **kwargs,
        )


class InsufficientPermissionsException(AuthenticationException):
    """User lacks required permissions."""

    def __init__(self, message: str = "Insufficient permissions", **kwargs):
        super().__init__(
            message=message,
            error_code=ErrorCode.AUTH_INSUFFICIENT_PERMISSIONS,
            status_code=403,
            **kwargs,
        )


class AccountDisabledException(AuthenticationException):
    """User account is disabled."""

    def __init__(self, message: str = "Account is disabled", **kwargs):
        super().__init__(
            message=message,
            error_code=ErrorCode.AUTH_ACCOUNT_DISABLED,
            status_code=403,
            **kwargs,
        )


class OTPExpiredException(AuthenticationException):
    """OTP code has expired."""

    def __init__(self, message: str = "OTP code has expired", **kwargs):
        super().__init__(
            message=message,
            error_code=ErrorCode.AUTH_OTP_EXPIRED,
            status_code=401,
            **kwargs,
        )


class InvalidOTPException(AuthenticationException):
    """Invalid OTP code."""

    def __init__(self, message: str = "Invalid OTP code", **kwargs):
        super().__init__(
            message=message,
            error_code=ErrorCode.AUTH_OTP_INVALID,
            status_code=401,
            **kwargs,
        )


# Validation exceptions
class ValidationException(SpinrException):
    """Validation error."""

    def __init__(self, message: str = "Validation error", **kwargs):
        kwargs.setdefault("error_code", ErrorCode.VALIDATION_ERROR)
        kwargs.setdefault("status_code", 400)
        super().__init__(message=message, **kwargs)


class InvalidFormatException(ValidationException):
    """Invalid data format."""

    def __init__(self, message: str = "Invalid format", **kwargs):
        super().__init__(
            message=message,
            error_code=ErrorCode.VALIDATION_INVALID_FORMAT,
            status_code=400,
            **kwargs,
        )


class MissingFieldException(ValidationException):
    """Missing required field."""

    def __init__(self, field: str, message: Optional[str] = None):
        super().__init__(
            message=message or f"Missing required field: {field}",
            error_code=ErrorCode.VALIDATION_MISSING_FIELD,
            status_code=400,
            details={"field": field},
        )


class InvalidRangeException(ValidationException):
    """Value outside valid range."""

    def __init__(
        self,
        field: str,
        min_val: Optional[float] = None,
        max_val: Optional[float] = None,
    ):
        range_str = []
        if min_val is not None:
            range_str.append(f">= {min_val}")
        if max_val is not None:
            range_str.append(f"<= {max_val}")

        super().__init__(
            message=f"{field} must be {' and '.join(range_str)}",
            error_code=ErrorCode.VALIDATION_INVALID_RANGE,
            status_code=400,
            details={"field": field, "min": min_val, "max": max_val},
        )


# Resource exceptions
class ResourceNotFoundException(SpinrException):
    """Requested resource not found."""

    def __init__(self, resource_type: str, resource_id: str, **kwargs):
        super().__init__(
            message=f"{resource_type} not found: {resource_id}",
            error_code=ErrorCode.RESOURCE_NOT_FOUND,
            status_code=404,
            details={"resource_type": resource_type, "resource_id": resource_id},
            **kwargs,
        )


class ResourceAlreadyExistsException(SpinrException):
    """Resource already exists."""

    def __init__(self, resource_type: str, field: str, value: str, **kwargs):
        super().__init__(
            message=f"{resource_type} already exists with {field}: {value}",
            error_code=ErrorCode.RESOURCE_ALREADY_EXISTS,
            status_code=409,
            details={"resource_type": resource_type, "field": field, "value": value},
            **kwargs,
        )


class ResourceConflictException(SpinrException):
    """Resource conflict."""

    def __init__(self, message: str = "Resource conflict", **kwargs):
        super().__init__(
            message=message,
            error_code=ErrorCode.RESOURCE_CONFLICT,
            status_code=409,
            **kwargs,
        )


# Ride exceptions
class RideNotFoundException(ResourceNotFoundException):
    """Ride not found."""

    def __init__(self, ride_id: str, **kwargs):
        super().__init__("Ride", ride_id, **kwargs)


class RideInvalidStatusException(SpinrException):
    """Invalid ride status transition."""

    def __init__(self, current_status: str, requested_status: str, **kwargs):
        super().__init__(
            message=f"Cannot transition from {current_status} to {requested_status}",
            error_code=ErrorCode.RIDE_INVALID_STATUS,
            status_code=400,
            details={
                "current_status": current_status,
                "requested_status": requested_status,
            },
            **kwargs,
        )


class RideStateError(SpinrException):
    """Ride is in an invalid state for the requested operation."""

    def __init__(self, message: str, **kwargs):
        super().__init__(
            message=message,
            error_code=ErrorCode.RIDE_INVALID_STATUS,
            status_code=422,
            **kwargs,
        )


class RideNoDriversAvailableException(SpinrException):
    """No drivers available for ride."""

    def __init__(self, location: Optional[Dict[str, float]] = None, **kwargs):
        super().__init__(
            message="No drivers available in your area",
            error_code=ErrorCode.RIDE_NO_DRIVERS_AVAILABLE,
            status_code=404,
            details={"location": location},
            **kwargs,
        )


# Driver exceptions
class DriverNotFoundException(ResourceNotFoundException):
    """Driver not found."""

    def __init__(self, driver_id: str, **kwargs):
        super().__init__("Driver", driver_id, **kwargs)


class DriverNotAvailableException(SpinrException):
    """Driver not available."""

    def __init__(self, driver_id: str, **kwargs):
        super().__init__(
            message="Driver is not available",
            error_code=ErrorCode.DRIVER_NOT_AVAILABLE,
            status_code=400,
            details={"driver_id": driver_id},
            **kwargs,
        )


class DriverOfflineException(SpinrException):
    """Driver is offline."""

    def __init__(self, driver_id: str, **kwargs):
        super().__init__(
            message="Driver is currently offline",
            error_code=ErrorCode.DRIVER_OFFLINE,
            status_code=400,
            details={"driver_id": driver_id},
            **kwargs,
        )


# Payment exceptions
class PaymentException(SpinrException):
    """Payment processing error."""

    def __init__(self, message: str = "Payment failed", **kwargs):
        kwargs.setdefault("error_code", ErrorCode.PAYMENT_FAILED)
        kwargs.setdefault("status_code", 400)
        super().__init__(message=message, **kwargs)


class PaymentMethodInvalidException(PaymentException):
    """Invalid payment method."""

    def __init__(self, message: str = "Invalid payment method", **kwargs):
        super().__init__(message=message, **kwargs)


class InsufficientFundsException(PaymentException):
    """Insufficient funds."""

    def __init__(self, required: float, available: float, **kwargs):
        super().__init__(
            message=f"Insufficient funds. Required: ${required:.2f}, Available: ${available:.2f}",
            details={"required": required, "available": available},
            **kwargs,
        )


# System exceptions
class InternalErrorException(SpinrException):
    """Internal server error."""

    def __init__(self, message: str = "Internal server error", **kwargs):
        super().__init__(
            message=message,
            error_code=ErrorCode.INTERNAL_ERROR,
            status_code=500,
            **kwargs,
        )


class ServiceUnavailableException(SpinrException):
    """Service temporarily unavailable."""

    def __init__(self, service_name: str = "", **kwargs):
        message = "Service temporarily unavailable"
        if service_name:
            message += f": {service_name}"
        super().__init__(
            message=message,
            error_code=ErrorCode.SERVICE_UNAVAILABLE,
            status_code=503,
            **kwargs,
        )


class RateLimitExceededException(SpinrException):
    """Rate limit exceeded."""

    def __init__(self, limit: int, retry_after: int, **kwargs):
        super().__init__(
            message="Rate limit exceeded",
            error_code=ErrorCode.RATE_LIMIT_EXCEEDED,
            status_code=429,
            details={"limit": limit, "retry_after": retry_after},
            **kwargs,
        )


class ExternalServiceException(SpinrException):
    """External service error."""

    def __init__(self, service_name: str, message: str, **kwargs):
        super().__init__(
            message=f"{service_name} error: {message}",
            error_code=ErrorCode.EXTERNAL_SERVICE_ERROR,
            status_code=502,
            details={"service": service_name},
            **kwargs,
        )


class DatabaseError(SpinrException):
    """Database operation failed (transient or permanent)."""

    def __init__(
        self,
        message: str = "Database operation failed",
        details: Optional[Dict[str, Any]] = None,
        **kwargs,
    ):
        super().__init__(
            message=message,
            error_code=ErrorCode.DATABASE_ERROR,
            status_code=503,
            details=details or {},
            **kwargs,
        )


class DuplicateRecordError(SpinrException):
    """Unique constraint violation — record already exists."""

    def __init__(
        self,
        message: str = "Record already exists",
        details: Optional[Dict[str, Any]] = None,
        **kwargs,
    ):
        super().__init__(
            message=message,
            error_code=ErrorCode.DUPLICATE_RECORD,
            status_code=409,
            details=details or {},
            **kwargs,
        )


def pg_error_code(exc: Exception) -> str:
    """Return the SQLSTATE / PostgREST error code for a DB exception, or ''.

    db_supabase.run_sync wraps PostgREST errors in DatabaseError/
    DuplicateRecordError; the original postgrest-py APIError (which carries
    ``.code`` — e.g. "23505" unique_violation, "PGRST204" schema-cache miss) is
    on the ``__cause__`` chain. Prefer the structured code over fragile message
    substrings.
    """
    code = getattr(exc, "code", None)
    if not code:
        cause = getattr(exc, "__cause__", None)
        code = getattr(cause, "code", None)
    return str(code or "")


def db_error_text(exc: Exception) -> str:
    """Best-effort lowercased underlying DB error text for branching.

    ``str(exc)`` on a wrapped DatabaseError/DuplicateRecordError is a generic
    sentinel ("Database operation failed" / "Record already exists") — the real
    message with the SQLSTATE and constraint name lives in
    ``details['original']`` and the ``__cause__`` chain. Matching ``str(exc)``
    alone silently misses constraint-specific handling; this joins all three so
    callers can reliably detect e.g. a specific unique-index violation.
    """
    parts = [str(exc)]
    details = getattr(exc, "details", None)
    if isinstance(details, dict) and details.get("original"):
        parts.append(str(details["original"]))
    cause = getattr(exc, "__cause__", None)
    if cause is not None:
        parts.append(str(cause))
    return " ".join(parts).lower()


# Error handling middleware
async def spinr_exception_handler(request: Request, exc: SpinrException) -> JSONResponse:
    """Handle SpinrException and return formatted JSON response."""
    request_id = _resolve_request_id(request)

    if exc.should_log:
        # Per CLAUDE.md: DB / auth / payment errors must surface loudly with
        # the underlying cause — `exc.message` on DatabaseError is the
        # generic "Database operation failed" sentinel; the real Postgres /
        # supabase-py exception string lives in `exc.details['original']`.
        # 5xx are logged at ERROR with that original cause so the root cause
        # is visible; 4xx stay at WARNING.
        original = (exc.details or {}).get("original")
        level = "ERROR" if exc.status_code >= 500 else "WARNING"
        parts = [
            f"[{request_id}] {request.method} {request.url.path}",
            f"SpinrException: {exc.error_code.name} - {exc.message}",
            f"error_code={exc.error_code.value}",
        ]
        if original:
            parts.append(f"original={original}")
        if exc.details:
            parts.append(f"details={exc.details}")
        # opt(raw=True) disables loguru's format-template parsing — APIError
        # `original` strings often contain literal '{' / '}' from dict reprs
        # (e.g. "{'message': ..., 'code': '22P02'}") which otherwise trip
        # KeyError inside `.format()` and take down the error handler itself.
        # Matches the pattern used by general_exception_handler below.
        try:
            logger.opt(raw=True).log(level, " | ".join(parts) + "\n")
        except Exception:  # noqa: S110 - never let logging crash the handler
            pass

    content = exc.to_dict()
    if isinstance(content.get("error"), dict):
        content["error"]["request_id"] = request_id
    # Top-level `detail` mirrors the shape of http_exception_handler — the
    # mobile fetch wrapper, several backend tests, and ad-hoc clients read
    # `errorData.detail` directly. Without this, migrating an HTTPException
    # raise site to a SpinrException would silently drop the detail field
    # and break the client's error message rendering.
    content["detail"] = exc.message
    response_headers: Dict[str, str] = {
        **_cors_headers_for(request),
        "X-Request-ID": request_id,
    }
    if exc.headers:
        # Merge route-supplied headers (e.g. Retry-After, RateLimit-*) last so
        # they take precedence, then re-pin X-Request-ID so a buggy raise site
        # can't accidentally corrupt the correlation id.
        response_headers.update(exc.headers)
        response_headers["X-Request-ID"] = request_id
    return JSONResponse(
        status_code=exc.status_code,
        content=content,
        headers=response_headers,
    )


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Handle validation errors and return formatted response."""
    errors = []
    for error in exc.errors():
        errors.append(
            {
                "field": ".".join(str(x) for x in error.get("loc", [])),
                "message": error.get("msg", "Invalid value"),
                "type": error.get("type", "value_error"),
            }
        )

    # Loguru treats the first positional arg as a format template, so embedding
    # `errors` directly (which contains dict reprs with '{' characters) raises
    # KeyError/ValueError during the format pass. opt(raw=True) disables
    # template parsing, matching the defensive pattern in
    # general_exception_handler below. Previously every 422 bubbled up as a
    # 500 because this handler crashed while handling the validation error.
    request_id = _resolve_request_id(request)

    try:
        logger.opt(raw=True).warning(
            f"[{request_id}] Validation error at {request.method} {request.url.path}: {errors}\n"
        )
    except Exception:  # noqa: S110
        # Never let logging take down the error handler itself.
        pass

    return JSONResponse(
        status_code=422,
        content={
            "success": False,
            "error": {
                "code": ErrorCode.VALIDATION_ERROR.value,
                "message": "Validation error",
                "request_id": request_id,
                "details": {"errors": errors},
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        },
        headers={
            **_cors_headers_for(request),
            "X-Request-ID": request_id,
        },
    )


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Handle HTTPException and return formatted response.

    The response body contains both the nested `error.message` shape and a
    top-level `detail` field so the mobile client's fetch wrapper (which
    reads `errorData.detail`) shows the real server message instead of
    the generic "Request failed" fallback.

    B-P2-1: 5xx ``detail`` strings are sanitized unless they match the
    ``ERR_*`` sentinel pattern. Routes that did
    ``raise HTTPException(500, detail=str(e))`` would previously leak
    Stripe charge IDs, Supabase constraint names, JWT library errors,
    etc. straight to the client. The full detail still hits the server
    log paired with the request_id so ops can correlate via the id the
    user quotes. 4xx pass through unchanged (those messages are
    intended user-facing UX — "Invalid phone number", "Card declined").

    Also propagates ``exc.headers``: routes that raise HTTPException
    with `Retry-After` / `RateLimit-*` (e.g. the OTP lockout path,
    B-P1-8) had their headers silently dropped by the previous
    implementation. The propagation order is `exc.headers` LAST so a
    route can override `X-Request-ID` if it really wants to (no
    current callers do; defensive).
    """
    request_id = _resolve_request_id(request)

    detail = exc.detail
    sanitized = False
    if exc.status_code >= 500 and _should_sanitize_5xx_detail(detail):
        try:
            logger.opt(raw=True).error(
                f"[{request_id}] Sanitised 5xx HTTPException detail at "
                f"{request.method} {request.url.path}: status={exc.status_code} "
                f"detail={str(detail)[:500]}"
            )
        except Exception:  # noqa: S110
            # Never let logging take down the error handler itself.
            pass
        detail = "Internal server error"
        sanitized = True

    error_obj: Dict[str, Any] = {
        "code": exc.status_code,
        "message": detail,
        "request_id": request_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if sanitized:
        # Explicit signal so a future contributor reading the response
        # in dev tools knows the message was scrubbed (vs the route
        # genuinely returning "Internal server error").
        error_obj["sanitised"] = True

    headers: Dict[str, str] = {
        **_cors_headers_for(request),
        "X-Request-ID": request_id,
    }
    if exc.headers:
        # Last-write-wins: route-supplied headers override our defaults
        # for everything except request_id, which we re-pin below so a
        # buggy route can't corrupt the correlation id.
        headers.update(exc.headers)
        headers["X-Request-ID"] = request_id

    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "detail": detail,  # legacy/compat — what the mobile client reads
            "error": error_obj,
        },
        headers=headers,
    )


# Origins that are always permitted for cross-origin requests. These are
# echoed manually on error responses because the generic Exception handler
# runs in Starlette's ServerErrorMiddleware — which sits OUTSIDE
# CORSMiddleware — so without this, 500s come back without CORS headers
# and surface in the browser as a CORS error instead of a real 500.
#
# Sourced from the same `settings.ALLOWED_ORIGINS` env var that
# `core/middleware.py` reads, plus the same hardcoded always-allowed
# list, so the two code paths can't drift. Resolved once at import
# time since app settings don't change between restarts.
_ALWAYS_ALLOWED = {
    "https://admin-spinr.spinr.ca",
    "http://localhost:3000",
    "http://localhost:3001",
}


def _resolve_cors_origins() -> set[str]:
    try:
        from core.config import settings  # local import to avoid a hard

        # dependency if utils is imported
        # before the settings module is
        # available (e.g. test harness).
    except Exception:
        return set(_ALWAYS_ALLOWED)

    raw = settings.ALLOWED_ORIGINS or ""
    env_origins = {o.strip() for o in raw.split(",") if o.strip()}
    return env_origins | _ALWAYS_ALLOWED


_CORS_ALLOWED_ORIGINS = _resolve_cors_origins()
_WILDCARD = "*" in _CORS_ALLOWED_ORIGINS


def _cors_headers_for(request: Request) -> Dict[str, str]:
    origin = request.headers.get("origin", "")
    if not origin:
        return {}
    if origin in _CORS_ALLOWED_ORIGINS:
        return {
            "Access-Control-Allow-Origin": origin,
            # Wildcard + credentials is forbidden by the CORS spec; mirror
            # core/middleware.py which disables credentials whenever the
            # allow list contains "*".
            **({"Access-Control-Allow-Credentials": "true"} if not _WILDCARD else {}),
            "Vary": "Origin",
        }
    if _WILDCARD:
        return {
            "Access-Control-Allow-Origin": "*",
        }
    return {}


async def general_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle unexpected exceptions and return formatted response."""
    # B-P2-1: read the middleware-bound request_id so the response the
    # client sees matches the id that loguru contextualize() stamped
    # into every log line for this request lifecycle. Without this,
    # the handler generated a fresh UUID and a support ticket quoting
    # that id couldn't be cross-referenced against the request logs.
    request_id = _resolve_request_id(request)

    # Loguru treats the first positional arg as a format template, so
    # embedding the traceback directly (which frequently contains '{'
    # characters from dict reprs, type hints, or formatted strings)
    # raises ValueError: unmatched '{' in format spec. Using opt(raw=True)
    # disables template parsing so arbitrary content can be logged safely.
    # Previously this handler crashed WHILE handling exceptions, which is
    # why 500s came back as plain text "Internal Server Error" from
    # Starlette's default fallback — the user never saw our JSON body.
    try:
        tb_text = traceback.format_exc()
        logger.opt(raw=True).error(
            f"[{request_id}] Unhandled exception at {request.method} {request.url.path}: "
            f"{type(exc).__name__}: {exc}\n{tb_text}\n"
        )
    except Exception:  # noqa: S110
        # Never let logging take down the error handler itself.
        pass

    error_body: Dict[str, Any] = {
        "code": ErrorCode.INTERNAL_ERROR.value,
        "message": "An unexpected error occurred",
        "request_id": request_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    return JSONResponse(
        status_code=500,
        content={"success": False, "error": error_body},
        headers={
            **_cors_headers_for(request),
            "X-Request-ID": request_id,
        },
    )


def register_exception_handlers(app):
    """Register all exception handlers with the FastAPI app."""
    app.add_exception_handler(SpinrException, spinr_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(Exception, general_exception_handler)


# Error response helpers
def error_response(
    message: str,
    error_code: ErrorCode = ErrorCode.INTERNAL_ERROR,
    status_code: int = 500,
    details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Create a standardized error response dictionary."""
    return {
        "success": False,
        "error": {
            "code": error_code.value,
            "message": message,
            "details": details,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    }


def success_response(data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Create a standardized success response dictionary."""
    response = {"success": True}
    if data is not None:
        response["data"] = data
    return response
