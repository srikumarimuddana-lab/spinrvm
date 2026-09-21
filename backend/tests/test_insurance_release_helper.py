"""`release_driver_and_close_period` — the single definition of "release a
driver from an offer and close their open Period 2".

Why this module exists at all
-----------------------------
The release-and-close pattern was hand-mirrored at five call sites
(`_release_loser` and `decline_ride` in `routes/drivers/ride_flow.py`,
`routes/rides/cancellation.py`, and both `_offer_timeout_handler` and
`process_expired_offer` in `routes/rides/matching.py`) and had already drifted
into two different behaviours before anyone noticed. CLAUDE.md's 2026-09-12
audit finding is exactly that shape — a fix proven in one copy that never
reached its sibling — so the decision now lives in one tested place and the
call sites only delegate.

What is actually being protected
--------------------------------
Every driver reaching one of those five sites holds an **open Period 2**,
opened at claim/offer time. `driver_insurance_periods` is append-only and
regulator-facing (SGI / TNC commercial cover), so getting this wrong is a
compliance defect, not a bug report:

* recording Period 1 unconditionally (three of the five sites' original
  behaviour) asserts TNC contingent commercial cover over a driver who went
  offline mid-offer and is really on personal auto — and nothing ever closes
  that row;
* recording *nothing* leaves the Period 2 open, which claims **primary**
  commercial cover — worse than the mislabel it replaces.

2026-09-20 review, Phase 1 item 12.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

try:
    from backend.utils import insurance_periods as mod
except ImportError:  # pragma: no cover - dual-import per CLAUDE.md
    from utils import insurance_periods as mod  # type: ignore

pytestmark = pytest.mark.anyio

DRIVER = "drv-release-1"
RIDE = "ride-release-1"


def _released(**overrides) -> dict:
    """A row as set_driver_available returns it: the FULL driver row, with
    is_available already clamped to the driver's is_online state."""
    row = {"id": DRIVER, "user_id": "u-1", "is_online": True, "is_available": True}
    row.update(overrides)
    return row


class TestPeriodRecorded:
    async def test_online_driver_is_closed_out_to_period_1(self):
        record = AsyncMock()
        with (
            patch.object(mod.db_supabase, "set_driver_available", AsyncMock(return_value=_released())),
            patch.object(mod, "record_period_transition", record),
        ):
            result = await mod.release_driver_and_close_period(DRIVER, reason="lost_race", ride_id=RIDE)

        assert result == 1
        record.assert_awaited_once_with(DRIVER, 1)

    async def test_offline_driver_is_closed_out_to_period_0(self):
        """They went offline mid-offer, so set_driver_available clamped
        is_available→False. Period 1 would assert commercial cover over a
        driver on personal auto; silence would leave their Period 2 open."""
        record = AsyncMock()
        row = _released(is_online=False, is_available=False)
        with (
            patch.object(mod.db_supabase, "set_driver_available", AsyncMock(return_value=row)),
            patch.object(mod, "record_period_transition", record),
        ):
            result = await mod.release_driver_and_close_period(DRIVER, reason="offer_declined")

        assert result == 0
        record.assert_awaited_once_with(DRIVER, 0)

    async def test_no_row_returned_writes_nothing(self):
        """set_driver_available returns None with no Supabase client
        (_write_skipped) or when the update matched no row. "Offline" and
        "unknown" are different; a guessed row in an append-only regulatory
        log is worse than a missing one."""
        record = AsyncMock()
        with (
            patch.object(mod.db_supabase, "set_driver_available", AsyncMock(return_value=None)),
            patch.object(mod, "record_period_transition", record),
        ):
            result = await mod.release_driver_and_close_period(DRIVER, reason="offer_timeout")

        assert result is None
        record.assert_not_awaited()

    async def test_row_without_is_online_falls_back_to_the_clamp(self):
        """A projection that omits is_online must not silently read as
        offline — is_available can only be true if is_online was."""
        record = AsyncMock()
        row = {"id": DRIVER, "is_available": True}
        with (
            patch.object(mod.db_supabase, "set_driver_available", AsyncMock(return_value=row)),
            patch.object(mod, "record_period_transition", record),
        ):
            assert await mod.release_driver_and_close_period(DRIVER, reason="rider_cancelled") == 1


class TestRideIdIsNeverWrittenToThePeriodRow:
    async def test_period_0_1_rows_do_not_name_a_ride(self):
        """A Period 2/3 row must name its ride; Period 0/1 must not (see
        routes/drivers/status.py). `ride_id` is accepted for the log line only,
        so passing it through to record_period_transition would produce a
        malformed audit record."""
        record = AsyncMock()
        with (
            patch.object(mod.db_supabase, "set_driver_available", AsyncMock(return_value=_released())),
            patch.object(mod, "record_period_transition", record),
        ):
            await mod.release_driver_and_close_period(DRIVER, reason="lost_race", ride_id=RIDE)

        assert record.await_args.args == (DRIVER, 1)
        assert "ride_id" not in record.await_args.kwargs


class TestReasonIsALabelNotASwitch:
    async def test_every_reason_produces_the_same_period_for_the_same_state(self):
        """The moment `reason` changes what gets written, the five copies have
        been rebuilt inside one function."""
        seen = set()
        for reason in sorted(mod.RELEASE_REASONS):
            with (
                patch.object(mod.db_supabase, "set_driver_available", AsyncMock(return_value=_released())),
                patch.object(mod, "record_period_transition", AsyncMock()),
            ):
                seen.add(await mod.release_driver_and_close_period(DRIVER, reason=reason))
        assert seen == {1}

    async def test_unknown_reason_raises(self):
        """Programmer error, same class as record_period_transition's period
        validation — surfaced rather than emitting an unlabelled metric."""
        with pytest.raises(ValueError, match="reason must be one of"):
            await mod.release_driver_and_close_period(DRIVER, reason="because")

    async def test_unknown_reason_does_not_touch_the_driver_row(self):
        release = AsyncMock()
        with patch.object(mod.db_supabase, "set_driver_available", release):
            with pytest.raises(ValueError):
                await mod.release_driver_and_close_period(DRIVER, reason="typo")
        release.assert_not_awaited()


class TestSignatureIsKeywordOnly:
    async def test_reason_cannot_be_passed_positionally(self):
        """Keyword-only on purpose, matching derive_insurance_period: these are
        regulatory classifications and a transposed positional would silently
        misstate SGI commercial coverage."""
        with pytest.raises(TypeError):
            await mod.release_driver_and_close_period(DRIVER, "lost_race")  # type: ignore[misc]


class TestDelegatesTheZeroOneChoice:
    async def test_period_comes_from_derive_insurance_period(self):
        """The 0-vs-1 choice is NOT re-derived inline — that inline
        `1 if available else 0` at two call sites is what this helper replaced.
        derive_insurance_period owns the Period 0-3 table."""
        derive = patch.object(mod, "derive_insurance_period", return_value=0)
        with (
            patch.object(mod.db_supabase, "set_driver_available", AsyncMock(return_value=_released())),
            patch.object(mod, "record_period_transition", AsyncMock()) as record,
            derive as derive_mock,
        ):
            # An online driver would normally yield 1; the stub proves the
            # helper takes its answer from derive_insurance_period, not from
            # its own reading of the row.
            assert await mod.release_driver_and_close_period(DRIVER, reason="lost_race") == 0
            record.assert_awaited_once_with(DRIVER, 0)

        kwargs = derive_mock.call_args.kwargs
        assert kwargs["is_online"] is True
        assert kwargs["has_live_offer"] is False
        assert kwargs["ride_status"] is None
