"""Backfill that attaches rider emails to pre-existing Stripe customers.

The admin button and the CLI both call
``services/stripe_customer_email_backfill.py``, so these tests pin the service
— covering both entry points at once.

The negative cases carry the weight. This writes rider PII to a US processor in
bulk, so "dry run really writes nothing" and "a stranded customer is reported,
never repaired" are the two properties that must not rot: re-provisioning
clears ``default_payment_method``, which would break settlement for a rider who
is not in the room.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import stripe

from backend.services import stripe_customer_email_backfill as svc

pytestmark = [pytest.mark.unit, pytest.mark.anyio]

# Concatenated so the repo's pre-commit secret scanner doesn't flag the fixture.
LIVE_KEY = "sk_live_" + "abc123"


def _rows(n: int = 1, **overrides) -> list[dict]:
    out = []
    for i in range(n):
        row = {"id": f"user-{i}", "email": f"rider{i}@example.com", "stripe_customer_id": f"cus_{i}"}
        row.update(overrides)
        out.append(row)
    return out


def _customer(email: str | None = None) -> MagicMock:
    cus = MagicMock()
    cus.email = email
    return cus


class _Harness:
    def __init__(self, rows: list[dict], settings: dict | None = None, retrieve=None, modify=None):
        cfg = {"stripe_secret_key": LIVE_KEY}
        cfg.update(settings or {})
        self.pages = [rows]
        self.queries: list[tuple[dict, dict]] = []

        async def _get_rows(_table, _filters=None, **kw):
            # Record what the service actually asked the DB for — the SQL-side
            # exclusions and the ordering are load-bearing, not incidental.
            self.queries.append((dict(_filters or {}), dict(kw)))
            # One page, then exhausted — the service stops on a short page.
            return self.pages.pop(0) if self.pages else []

        self.retrieve = retrieve if retrieve is not None else MagicMock(return_value=_customer(None))
        self.modify = modify if modify is not None else MagicMock(return_value=_customer())
        self._patches = [
            patch("backend.services.stripe_customer_email_backfill.db_supabase.get_rows", side_effect=_get_rows),
            patch(
                "backend.services.stripe_customer_email_backfill.get_app_settings",
                AsyncMock(return_value=cfg),
            ),
            patch("backend.services.stripe_customer_email_backfill.stripe.Customer.retrieve", self.retrieve),
            patch("backend.services.stripe_customer_email_backfill.stripe.Customer.modify", self.modify),
        ]

    def __enter__(self):
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.stop()
        return False


class TestDryRun:
    async def test_dry_run_writes_nothing_but_reports_the_diff(self):
        with _Harness(_rows(3)) as h:
            result = await svc.backfill_stripe_customer_emails()
        assert result.applied is False
        assert result.updated == 3
        assert result.scanned == 3
        h.modify.assert_not_called()
        assert {c.user_id for c in result.changes} == {"user-0", "user-1", "user-2"}

    async def test_dry_run_is_the_default(self):
        """A caller that forgets the flag must not write."""
        with _Harness(_rows(1)) as h:
            await svc.backfill_stripe_customer_emails()
        h.modify.assert_not_called()

    async def test_change_records_carry_no_email_address(self):
        """PIPEDA: the payload reaches an admin UI — ids only."""
        with _Harness(_rows(1)):
            result = await svc.backfill_stripe_customer_emails()
        assert not any("@" in str(v) for v in vars(result.changes[0]).values())


class TestApply:
    async def test_apply_writes_the_email(self):
        with _Harness(_rows(1)) as h:
            result = await svc.backfill_stripe_customer_emails(apply=True)
        assert result.applied is True
        assert result.updated == 1
        h.modify.assert_called_once()
        assert h.modify.call_args.args[0] == "cus_0"
        assert h.modify.call_args.kwargs["email"] == "rider0@example.com"

    async def test_matching_email_is_unchanged_and_not_rewritten(self):
        """Idempotent: a second pass must write nothing."""
        retrieve = MagicMock(return_value=_customer("rider0@example.com"))
        with _Harness(_rows(1), retrieve=retrieve) as h:
            result = await svc.backfill_stripe_customer_emails(apply=True)
        assert result.unchanged == 1
        assert result.updated == 0
        h.modify.assert_not_called()

    async def test_case_differences_do_not_trigger_a_rewrite(self):
        retrieve = MagicMock(return_value=_customer("RIDER0@Example.com"))
        with _Harness(_rows(1), retrieve=retrieve) as h:
            result = await svc.backfill_stripe_customer_emails(apply=True)
        assert result.unchanged == 1
        h.modify.assert_not_called()

    async def test_existing_different_email_is_flagged_as_a_correction(self):
        retrieve = MagicMock(return_value=_customer("old@example.com"))
        with _Harness(_rows(1), retrieve=retrieve):
            result = await svc.backfill_stripe_customer_emails(apply=True)
        assert result.changes[0].had_email is True


class TestNothingIsSilentlyDropped:
    async def test_rider_without_a_customer_is_counted(self):
        with _Harness(_rows(1, stripe_customer_id=None)) as h:
            result = await svc.backfill_stripe_customer_emails(apply=True)
        assert result.no_customer == 1
        assert result.updated == 0
        h.retrieve.assert_not_called()

    async def test_rider_without_an_email_is_counted_and_never_blanks_stripe(self):
        with _Harness(_rows(1, email="")) as h:
            result = await svc.backfill_stripe_customer_emails(apply=True)
        assert result.no_email == 1
        h.modify.assert_not_called()

    async def test_one_failure_does_not_abort_the_rest(self):
        calls = {"n": 0}

        def _flaky(cid, **_kw):
            calls["n"] += 1
            if cid == "cus_1":
                raise stripe.error.APIConnectionError("stripe down")
            return _customer(None)

        with _Harness(_rows(3), retrieve=MagicMock(side_effect=_flaky)):
            result = await svc.backfill_stripe_customer_emails(apply=True)
        assert result.failed == ["user-1:cus_1"]
        assert result.updated == 2

    async def test_has_more_is_reported_rather_than_truncating_silently(self):
        with _Harness(_rows(5)):
            result = await svc.backfill_stripe_customer_emails(limit=3)
        assert result.has_more is True
        assert len(result.changes) == 3

    async def test_rider_mid_deletion_is_skipped_not_transferred(self):
        """A rider between "delete my account" and the retention purge still
        has an address on their row. It must never reach Stripe."""
        with _Harness(_rows(1, status="pending_deletion")) as h:
            result = await svc.backfill_stripe_customer_emails(apply=True)
        assert result.skipped_deleted == 1
        assert result.updated == 0
        h.retrieve.assert_not_called()
        h.modify.assert_not_called()


class TestResumability:
    """A partial sweep that reports success is worse than one that fails.

    The first version restarted at offset 0 with no ordering, so run 2 re-read
    run 1's page, found everything already correct, and reported "nothing to
    sync" while the rest of the fleet had never been touched.
    """

    async def test_query_is_ordered_so_paging_is_well_defined(self):
        with _Harness(_rows(1)) as h:
            await svc.backfill_stripe_customer_emails()
        _filters, kwargs = h.queries[0]
        assert kwargs.get("order") == "id"

    async def test_query_excludes_deleted_rows_and_riders_without_a_customer(self):
        with _Harness(_rows(1)) as h:
            await svc.backfill_stripe_customer_emails()
        filters, _kwargs = h.queries[0]
        # Deleted riders must not even be read.
        assert filters["deleted_at"] is None
        # Riders with no customer have nothing to repair; including them would
        # burn the per-run budget and starve the ones that do.
        assert filters["stripe_customer_id"] == {"$notnull": True}

    async def test_cursor_becomes_a_greater_than_predicate(self):
        with _Harness(_rows(1)) as h:
            await svc.backfill_stripe_customer_emails(cursor="user-41")
        filters, _kwargs = h.queries[0]
        assert filters["id"]["$gt"] == "user-41"

    async def test_cursor_and_user_ids_compose_so_a_scoped_run_resumes(self):
        with _Harness(_rows(1)) as h:
            await svc.backfill_stripe_customer_emails(user_ids=["a", "b"], cursor="a")
        filters, _kwargs = h.queries[0]
        assert filters["id"] == {"$in": ["a", "b"], "$gt": "a"}

    async def test_next_cursor_points_at_the_last_row_processed(self):
        """Not the last row READ — the next run must resume immediately after
        the final row this one actually handled, with no gap."""
        with _Harness(_rows(5)):
            result = await svc.backfill_stripe_customer_emails(limit=3)
        assert result.has_more is True
        assert result.next_cursor == "user-2"

    async def test_no_cursor_when_the_sweep_reached_the_end(self):
        with _Harness(_rows(2)):
            result = await svc.backfill_stripe_customer_emails(limit=10)
        assert result.has_more is False
        assert result.next_cursor is None


class TestStrandedCustomers:
    async def test_missing_on_key_is_reported_not_repaired(self):
        """Re-provisioning clears default_payment_method — never from a bulk job."""
        missing = MagicMock(
            side_effect=stripe.error.InvalidRequestError(
                "No such customer: 'cus_0'", param=None, code="resource_missing"
            )
        )
        with _Harness(_rows(1), retrieve=missing) as h:
            result = await svc.backfill_stripe_customer_emails(apply=True)
        assert result.missing_on_key == ["user-0:cus_0"]
        assert result.failed == []
        assert result.updated == 0
        h.modify.assert_not_called()


class TestConfiguration:
    async def test_unconfigured_stripe_raises_rather_than_reporting_empty_success(self):
        with _Harness(_rows(1), settings={"stripe_secret_key": ""}):
            with pytest.raises(RuntimeError):
                await svc.backfill_stripe_customer_emails()

    async def test_limit_is_clamped_to_max(self):
        """The previous version of this test asserted `updated == 1`, which is
        true whether or not clamping happens — it proved nothing. Assert the
        cap itself: an absurd limit must not become an unbounded sweep."""
        with _Harness(_rows(svc.MAX_LIMIT + 5)):
            result = await svc.backfill_stripe_customer_emails(limit=10**9)
        assert len(result.changes) == svc.MAX_LIMIT
        assert result.has_more is True

    async def test_key_mode_is_reported_so_the_operator_knows_the_account(self):
        with _Harness(_rows(1)):
            result = await svc.backfill_stripe_customer_emails()
        assert result.key_mode == "live"


class TestPiiDiscipline:
    async def test_a_stripe_error_quoting_the_email_is_redacted_before_logging(self, caplog):
        """Stripe echoes the value it rejected, and here that value IS the
        address — a raw str(exc) in the log would defeat the no-emails rule."""
        boom = MagicMock(
            side_effect=stripe.error.InvalidRequestError("Invalid email address: rider0@example.com", param="email")
        )
        with _Harness(_rows(1), retrieve=boom):
            with caplog.at_level("ERROR"):
                result = await svc.backfill_stripe_customer_emails(apply=True)
        assert result.failed == ["user-0:cus_0"]
        assert "rider0@example.com" not in caplog.text
        assert "[email-redacted]" in caplog.text
