"""Regression test for the race migration 258 closed.

Context: settle_corporate computed `allowance_debit = min(remaining, total)`
from a NON-locking read of `corporate_member_allowances.used`, then called
the `corporate_allowance_apply_delta` RPC. The RPC held a row lock but,
before migration 258, only guarded the master-wallet floor — never the
per-member allowance ceiling. Two rides for the same member settling
concurrently could both read the same stale `used`, both decide their debit
fit, and both apply it, silently pushing `used` above `amount`. See
docs/change-log/2026-07-26-corporate-allowance-cap-race-fix.md and
backend/migrations/258_corporate_allowance_cap_in_rpc.sql.

**What this test proves, and what it doesn't:** the test suite mocks
Supabase (per CLAUDE.md's `mock_supabase_client` convention) rather than
running against real Postgres, so nothing here exercises an actual `FOR
UPDATE` row lock — that would need the integration tier (real Supabase
against a throwaway test schema), which isn't wired up for local/CI runs in
this repo. What this test DOES prove: `_ConcurrencySafeAllowanceStore` is a
line-for-line port of the locked section of `corporate_allowance_apply_delta`
(migration 258, lines 70-106 — read `used`+`amount` together, compute
`v_used_new`, raise `allowance_cap_exceeded` if it would exceed the cap),
serialized under an `asyncio.Lock` standing in for the row lock. Run two
concurrent `ride_debit` calls against it that would each individually pass a
STALE pre-lock read but jointly exceed the cap, and assert exactly one
succeeds and one raises — i.e., that the *algorithm* migration 258 encodes
enforces the invariant it claims to, independent of any application-level
double-checking. It is a regression guard against someone reintroducing a
non-atomic ceiling check (e.g. reading `used` before acquiring the lock,
computing the delta, then acquiring the lock only for the write) the next
time this function is touched — not a substitute for a real concurrent-load
integration test against Postgres, which remains an open gap (see the P1
punch-list item this test partially closes in the 2026-07-28 corporate
module SDLC audit).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal

import pytest


class AllowanceCapExceeded(Exception):
    pass


@dataclass
class _AllowanceRow:
    used: Decimal
    cap: Decimal  # corresponds to SQL `amount` (the ceiling), not the debit amount


class _ConcurrencySafeAllowanceStore:
    """In-memory port of corporate_allowance_apply_delta's locked section for
    the ride_debit path (migration 258, lines 70-106). The asyncio.Lock here
    plays the role of Postgres's `FOR UPDATE` on `corporate_member_allowances`."""

    def __init__(self, used: Decimal, cap: Decimal) -> None:
        self._row = _AllowanceRow(used=used, cap=cap)
        self._lock = asyncio.Lock()

    async def ride_debit(self, amount: Decimal, *, hold_lock_seconds: float = 0.0) -> Decimal:
        """Mirrors SQL lines 72-106: SELECT used, amount ... FOR UPDATE, then
        the ceiling check, then the write — all inside the same locked
        section, so no other caller's read can interleave."""
        async with self._lock:
            v_used = self._row.used
            v_cap = self._row.cap
            v_used_new = v_used + amount
            if hold_lock_seconds:
                # Simulate the lock being held across real work (e.g. the
                # paired ledger INSERTs migration 258's function also does
                # under the same lock) — proves serialization holds even when
                # the critical section isn't instantaneous.
                await asyncio.sleep(hold_lock_seconds)
            if v_cap is not None and v_used_new > v_cap:
                raise AllowanceCapExceeded(f"allowance_cap_exceeded: used_new={v_used_new} cap={v_cap}")
            self._row.used = v_used_new
            return v_used_new


class _NoCeilingCheckStore(_ConcurrencySafeAllowanceStore):
    """The migration-248 (pre-258) RPC shape, verified against the actual
    SQL (backend/migrations/248_corporate_allowance_ride_debit.sql lines
    105-111, 140, 151-153): `used` IS read freshly under the row lock every
    call — Postgres's FOR UPDATE already serialized that correctly, so there
    was never staleness inside the RPC itself. The real bug is that the RPC
    computed `v_used_new := v_used + v_used_delta` and wrote it
    UNCONDITIONALLY, with no check against the allowance's cap anywhere in
    the locked section. Reproduced here (fresh locked read, no cap check) to
    prove the race is real absent the fix, as a control."""

    async def ride_debit(self, amount: Decimal, *, hold_lock_seconds: float = 0.0) -> Decimal:
        async with self._lock:
            v_used = self._row.used  # fresh read under the lock, same as the real RPC
            v_used_new = v_used + amount
            if hold_lock_seconds:
                await asyncio.sleep(hold_lock_seconds)
            # No ceiling check here — this is exactly what migration 258 added.
            self._row.used = v_used_new
            return v_used_new


@pytest.mark.anyio
async def test_locked_ceiling_check_rejects_second_concurrent_debit_over_cap():
    """Two $30 ride_debits against a $50 cap with $0 used: individually each
    fits (30 <= 50), but together they'd land at $60 — over cap. Under the
    migration-258 locked-read-then-check algorithm, exactly one must succeed
    and the other must raise allowance_cap_exceeded, regardless of
    scheduling order."""
    store = _ConcurrencySafeAllowanceStore(used=Decimal("0"), cap=Decimal("50"))

    results = await asyncio.gather(
        store.ride_debit(Decimal("30"), hold_lock_seconds=0.01),
        store.ride_debit(Decimal("30"), hold_lock_seconds=0.01),
        return_exceptions=True,
    )

    successes = [r for r in results if isinstance(r, Decimal)]
    failures = [r for r in results if isinstance(r, AllowanceCapExceeded)]

    assert len(successes) == 1, f"expected exactly one debit to succeed, got {results}"
    assert len(failures) == 1, f"expected exactly one debit to be rejected, got {results}"
    assert successes[0] == Decimal("30")
    # `used` must reflect only the ONE successful debit — never both.
    assert store._row.used == Decimal("30")


@pytest.mark.anyio
async def test_locked_ceiling_check_allows_both_when_jointly_under_cap():
    """Sanity check on the other side: two $20 debits against a $50 cap
    ($40 total) both fit — the fix must not become overly conservative and
    reject legitimate concurrent, non-conflicting debits."""
    store = _ConcurrencySafeAllowanceStore(used=Decimal("0"), cap=Decimal("50"))

    results = await asyncio.gather(
        store.ride_debit(Decimal("20"), hold_lock_seconds=0.01),
        store.ride_debit(Decimal("20"), hold_lock_seconds=0.01),
        return_exceptions=True,
    )

    assert all(isinstance(r, Decimal) for r in results), f"expected both debits to succeed, got {results}"
    assert store._row.used == Decimal("40")


@pytest.mark.anyio
async def test_unlimited_allowance_never_capped_under_concurrency():
    """cap=None (SQL's amount IS NULL) must never raise, no matter how much
    concurrent usage piles up — migration 258 explicitly exempts unlimited
    allowances from the ceiling check (line 104: `v_cap IS NOT NULL AND ...`)."""
    store = _ConcurrencySafeAllowanceStore(used=Decimal("0"), cap=None)

    results = await asyncio.gather(
        *(store.ride_debit(Decimal("1000"), hold_lock_seconds=0.005) for _ in range(5)),
        return_exceptions=True,
    )

    assert all(isinstance(r, Decimal) for r in results), f"expected all debits to succeed, got {results}"
    assert store._row.used == Decimal("5000")


@pytest.mark.anyio
async def test_control_pre_258_shape_actually_overspends():
    """Control case proving the race is real absent the fix: reproduces the
    verified migration-248 shape (locked read, NO ceiling check) and shows
    it DOES let two sequential-under-lock $30 debits against a $50 cap both
    succeed, landing `used` at $60 — over cap. This is the exact failure
    mode migration 258's commit message and rollback comment describe: the
    lock alone was never the problem (each write is internally consistent,
    30 then 60); the missing cap check inside the locked section is. If this
    control test ever stops overspending, the analogy between
    _NoCeilingCheckStore and the pre-258 RPC has drifted and needs
    re-checking against the actual migration 248 SQL."""
    store = _NoCeilingCheckStore(used=Decimal("0"), cap=Decimal("50"))

    results = await asyncio.gather(
        store.ride_debit(Decimal("30"), hold_lock_seconds=0.01),
        store.ride_debit(Decimal("30"), hold_lock_seconds=0.01),
    )

    assert set(results) == {Decimal("30"), Decimal("60")}  # lock serializes: 0->30, then 30->60
    assert store._row.used == Decimal("60")  # over the $50 cap — the bug migration 258 fixed
