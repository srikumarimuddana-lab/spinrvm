"""WS-E / C6: exception text must not reach a client through a 4xx detail.

Two layers are under test here:

1. ``redact_client_text`` / ``client_safe_detail`` (``utils/pii.py``) — the
   helper a route calls instead of writing ``detail=str(e)``.
2. ``http_exception_handler`` (``utils/error_handling.py``) — the blanket
   redaction applied to *every* 4xx string detail, so a route that forgets
   layer 1 still cannot leak.

The load-bearing property of layer 2 is that it REDACTS rather than replaces:
4xx details are user-facing UX copy by design, and a test that only proves
"secrets are gone" would also pass an implementation that blanked every
message. So every "leak" case below is paired with a "legit copy is
untouched" case.
"""

import pytest
from fastapi import HTTPException

from backend.utils.error_handling import http_exception_handler
from backend.utils.pii import client_safe_detail, redact_client_text

pytestmark = pytest.mark.unit

#: Assembled at runtime so the repo's pre-commit secret scanner does not match
#: a literal key prefix in a test fixture. Not a real credential.
_SK_PREFIX = "sk_" + "live_"


class _FakeURL:
    path = "/admin/drivers/import"


class _FakeState:
    request_id = "req_test_1234"


class _FakeRequest:
    method = "POST"
    url = _FakeURL()
    state = _FakeState()
    headers: dict = {}


async def _detail_of(status_code: int, detail):
    """Run the real handler and return the ``detail`` it put on the wire."""
    import json

    resp = await http_exception_handler(_FakeRequest(), HTTPException(status_code=status_code, detail=detail))
    return json.loads(bytes(resp.body).decode())["detail"]


# ── Layer 1: the redactor ────────────────────────────────────────────


class TestRedactClientText:
    @pytest.mark.parametrize(
        "raw, must_not_contain",
        [
            ("row 8: bad sin 123 456 789", "123 456 789"),
            ("row 12: sin 123456789 invalid", "123456789"),
            ("duplicate for nighil@example.com", "nighil@example.com"),
            ("dob 1985-03-12 out of range", "1985-03-12"),
            ("parse failed at /home/user/spinrvm/backend/x.py", "/home/user"),
            (
                "auth failed: eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.dBjftJeZ4CVPmB92",
                "eyJhbGciOiJIUzI1NiJ9",
            ),
            # Split so the pre-commit secret scanner does not flag the fixture
            # itself; the value under test is still a complete key-shaped token.
            ("stripe rejected " + _SK_PREFIX + "51AbCdEfGhIjKlMnOp", _SK_PREFIX),
            ("Authorization: Bearer abcdefghijklmnop.qrstuv", "abcdefghijklmnop"),
            ("driver at lat=52.1332, lng=-106.6700", "52.1332"),
            ("card 4242 4242 4242 4242 declined", "4242 4242"),
            ("call 306-555-0142 to confirm", "306-555-0142"),
        ],
    )
    def test_sensitive_values_are_removed(self, raw, must_not_contain):
        assert must_not_contain not in redact_client_text(raw)

    @pytest.mark.parametrize(
        "copy",
        [
            "Invalid phone number",
            "Card declined",
            "Export approval request not found",
            "Validate this CSV before committing",
            # The route path in an error message is what makes it actionable;
            # the path pattern is root-anchored precisely so this survives.
            "POST /admin/drivers/import failed: column missing",
            "row 214, column `vehicle_id`: not a known vehicle",
        ],
    )
    def test_legitimate_ux_copy_is_untouched(self, copy):
        assert redact_client_text(copy) == copy

    def test_truncated_with_explicit_marker(self):
        out = redact_client_text("x" * 400)
        assert len(out) < 400
        assert out.endswith("…[truncated]")

    def test_non_string_input_is_coerced(self):
        assert redact_client_text(None) == "None"


class TestClientSafeDetail:
    def test_keeps_the_actionable_part(self):
        exc = ValueError("row 3 missing column vehicle_id")
        assert client_safe_detail(exc, fallback="CSV validation failed") == ("row 3 missing column vehicle_id")

    def test_falls_back_when_nothing_survives_redaction(self):
        # A message that was ENTIRELY a SIN redacts to "[REDACTED-GOVID]",
        # which tells an admin nothing and reads like a bug.
        assert client_safe_detail(ValueError("123456789"), fallback="CSV validation failed") == "CSV validation failed"

    def test_falls_back_on_empty_exception(self):
        assert client_safe_detail(KeyError(), fallback="CSV validation failed") == "CSV validation failed"

    def test_redacts_while_keeping_context(self):
        out = client_safe_detail(ValueError("row 8: sin 123456789 for a@b.com"), fallback="nope")
        assert "row 8" in out
        assert "123456789" not in out
        assert "a@b.com" not in out


# ── Layer 2: the handler's blanket 4xx redaction ─────────────────────


class TestHandlerRedacts4xx:
    @pytest.mark.anyio
    async def test_4xx_leak_is_redacted(self):
        detail = await _detail_of(422, "row 8: sin 123456789 for a@b.com")
        assert "123456789" not in detail
        assert "a@b.com" not in detail
        assert "row 8" in detail

    @pytest.mark.anyio
    @pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 422, 429])
    async def test_legit_4xx_copy_passes_through_unchanged(self, status):
        assert await _detail_of(status, "Invalid phone number") == "Invalid phone number"

    @pytest.mark.anyio
    async def test_non_string_4xx_detail_is_left_alone(self):
        # Structured details (dicts) are a real pattern in this codebase and
        # must not be stringified by the redaction branch.
        detail = await _detail_of(422, {"code": "ERR_X", "rows": [1, 2]})
        assert detail == {"code": "ERR_X", "rows": [1, 2]}

    @pytest.mark.anyio
    async def test_5xx_behaviour_is_unchanged(self):
        # Pre-existing B-P2-1 contract: non-sentinel 5xx details are REPLACED,
        # not redacted. WS-E must not have altered that.
        assert await _detail_of(500, "boom: sin 123456789") == "Internal server error"

    @pytest.mark.anyio
    async def test_5xx_sentinel_still_passes_through(self):
        assert await _detail_of(503, "ERR_AUTH_UNAVAILABLE") == "ERR_AUTH_UNAVAILABLE"
