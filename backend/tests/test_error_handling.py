"""Tests for error handling utilities (tasks 10-3 through 10-6)."""


import pytest
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from utils.error_handling import (
    DatabaseError,
    DuplicateRecordError,
    ErrorCode,
    SpinrException,
    spinr_exception_handler,
    validation_exception_handler,
)


def _make_request(origin: str = "http://localhost:3000") -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/test",
        "headers": [(b"origin", origin.encode())],
        "query_string": b"",
    }
    return Request(scope)


# ---------------------------------------------------------------------------
# DatabaseError / DuplicateRecordError class tests (task 10-6)
# ---------------------------------------------------------------------------

class TestDatabaseErrorClass:
    def test_database_error_status(self):
        exc = DatabaseError()
        assert exc.status_code == 503

    def test_database_error_code(self):
        exc = DatabaseError()
        assert exc.error_code == ErrorCode.DATABASE_ERROR

    def test_database_error_custom_message(self):
        exc = DatabaseError(message="connection pool exhausted")
        assert "connection pool exhausted" in exc.message

    def test_database_error_details(self):
        exc = DatabaseError(details={"original": "some pg error"})
        assert exc.details["original"] == "some pg error"


class TestDuplicateRecordErrorClass:
    def test_duplicate_record_status(self):
        exc = DuplicateRecordError()
        assert exc.status_code == 409

    def test_duplicate_record_code(self):
        exc = DuplicateRecordError()
        assert exc.error_code == ErrorCode.DUPLICATE_RECORD

    def test_duplicate_record_custom_message(self):
        exc = DuplicateRecordError(message="phone already registered")
        assert "phone already registered" in exc.message


# ---------------------------------------------------------------------------
# run_sync wrapping tests (task 10-6)
# ---------------------------------------------------------------------------

class TestRunSyncErrorWrapping:
    @pytest.mark.asyncio
    async def test_run_sync_wraps_generic_error_as_database_error(self):
        from db_supabase import run_sync

        def _fail():
            raise RuntimeError("unexpected DB failure")

        with pytest.raises(DatabaseError):
            await run_sync(_fail)

    @pytest.mark.asyncio
    async def test_run_sync_wraps_duplicate_key_as_duplicate_record_error(self):
        from db_supabase import run_sync

        def _fail():
            raise Exception("duplicate key value violates unique constraint")

        with pytest.raises(DuplicateRecordError):
            await run_sync(_fail)

    @pytest.mark.asyncio
    async def test_run_sync_wraps_unique_constraint_as_duplicate_record_error(self):
        from db_supabase import run_sync

        def _fail():
            raise Exception("ERROR: unique constraint violation on users_phone_key")

        with pytest.raises(DuplicateRecordError):
            await run_sync(_fail)


# ---------------------------------------------------------------------------
# CORS headers on spinr_exception_handler (task 10-4)
# ---------------------------------------------------------------------------

class TestSpinrExceptionHandlerError:
    @pytest.mark.asyncio
    async def test_spinr_handler_has_cors_headers(self):
        request = _make_request("http://localhost:3000")
        exc = SpinrException(message="oops", status_code=400)
        response = await spinr_exception_handler(request, exc)
        assert "access-control-allow-origin" in response.headers

    @pytest.mark.asyncio
    async def test_spinr_handler_has_request_id_header(self):
        request = _make_request()
        exc = SpinrException(message="oops", status_code=400)
        response = await spinr_exception_handler(request, exc)
        assert response.headers.get("x-request-id") is not None

    @pytest.mark.asyncio
    async def test_spinr_handler_includes_request_id_in_body(self):
        import json
        request = _make_request()
        exc = SpinrException(message="oops", status_code=400)
        response = await spinr_exception_handler(request, exc)
        body = json.loads(response.body)
        assert body["error"]["request_id"] is not None


# ---------------------------------------------------------------------------
# CORS headers on validation_exception_handler (task 10-3)
# ---------------------------------------------------------------------------

class TestValidationExceptionHandlerError:
    @pytest.mark.asyncio
    async def test_validation_handler_has_cors_headers(self):
        from pydantic import BaseModel

        class M(BaseModel):
            x: int

        try:
            M(x="bad")  # type: ignore[arg-type]
        except ValidationError as ve:
            raw_exc = RequestValidationError(errors=ve.errors())

        request = _make_request("http://localhost:3000")
        response = await validation_exception_handler(request, raw_exc)
        assert "access-control-allow-origin" in response.headers

    @pytest.mark.asyncio
    async def test_validation_handler_has_request_id_header(self):
        from pydantic import BaseModel

        class M(BaseModel):
            x: int

        try:
            M(x="bad")  # type: ignore[arg-type]
        except ValidationError as ve:
            raw_exc = RequestValidationError(errors=ve.errors())

        request = _make_request()
        response = await validation_exception_handler(request, raw_exc)
        assert response.headers.get("x-request-id") is not None

    @pytest.mark.asyncio
    async def test_validation_handler_status_422(self):
        from pydantic import BaseModel

        class M(BaseModel):
            x: int

        try:
            M(x="bad")  # type: ignore[arg-type]
        except ValidationError as ve:
            raw_exc = RequestValidationError(errors=ve.errors())

        request = _make_request()
        response = await validation_exception_handler(request, raw_exc)
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# Supabase connection pool timeout (task 10-5)
# ---------------------------------------------------------------------------

class TestSupabaseClientTimeout:
    def test_explicit_timeout_configured(self):
        import httpx

        import supabase_client as sc

        if sc.supabase is None:
            pytest.skip("Supabase not configured in test env")

        try:
            client = sc.supabase.postgrest._client
        except AttributeError:
            pytest.skip("SyncPostgrestClient._client not exposed in this supabase-py version")

        assert isinstance(client.timeout, httpx.Timeout)
        assert client.timeout.connect == 5.0
        assert client.timeout.read == 30.0
        assert client.timeout.write == 10.0
        assert client.timeout.pool == 5.0
