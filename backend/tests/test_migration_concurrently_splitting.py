"""A migration containing CONCURRENTLY is split on semicolons by the runner.

`scripts/migrate.py::apply_migration` routes any file whose text contains the
string "CONCURRENTLY" — including in a comment — to
`_apply_migration_autocommit`, which cannot use a transaction and so does
`sql.split(";")`, executing each chunk after stripping its *leading* `--` lines.

That splitter has two failure modes:

1. **A mid-line semicolon in a prose comment.** A line-terminal one is harmless
   (the whole next chunk is still comments, strips to empty, is skipped) — but a
   mid-line one splits inside the comment, and the rest of that line becomes the
   first line of the next chunk. It is not a comment, so the strip loop stops
   and the runner hands prose to Postgres as SQL.
2. **A `$$`-quoted function body.** Every semicolon inside `BEGIN … END` is a
   split point, so the body is shredded into fragments.

Both are defects in the runner, not in the migrations. This test pins the
property for migrations we add now, and freezes the pre-existing breakage in
`_KNOWN_UNSPLITTABLE` so the debt is visible in code rather than rediscovered.
See ACTION_ITEMS.md — the real fix is a comment/dollar-quote-aware splitter in
`scripts/migrate.py`, which is deliberately out of scope here: it governs how
every future migration applies and should not be rewritten as a side effect of
an unrelated change.
"""

from __future__ import annotations

import pathlib

import pytest

_MIGRATIONS = pathlib.Path(__file__).resolve().parent.parent / "migrations"

# Every statement keyword the runner can legitimately be handed. A chunk
# starting with anything else is prose or a function-body fragment.
_SQL_STARTS = (
    "ALTER",
    "COMMENT",
    "CREATE",
    "DO",
    "DROP",
    "GRANT",
    "INSERT",
    "REVOKE",
    "SELECT",
    "SET",
    "UPDATE",
    "WITH",
)

# Pre-existing, already-merged migrations that the runner's splitter mangles.
# DO NOT ADD TO THIS LIST — a new entry means a migration that will fail on
# apply. Reword the offending comment (or drop the stray "CONCURRENTLY" from it,
# which is what routes a function-body migration into the naive splitter at all).
_KNOWN_UNSPLITTABLE = frozenset(
    {
        "50_pii_retention_purge.sql",
        "55_drivers_dispatch_partial_index.sql",
        "69a_lost_and_found_repair_schema.sql",
        "69b_lost_and_found_concurrent_indexes.sql",
        "114_ride_history_stable_cursor_index.sql",
        "142_fix_rls_financial_tables.sql",
        "143_ride_offers_one_accepted_index.sql",
        "147_drivers_stale_intent_index.sql",
        "151_subscription_payments_ledger.sql",
        "152_driver_subscriptions_one_pending_index.sql",
        "153_driver_subscriptions_one_pending_index.sql",
        "156_ride_preauth_columns.sql",
        "157_driver_availability_claimed_at.sql",
        "159_payouts_overview_aggregates_fn.sql",
        "161_ride_money_rollup_fn.sql",
        "163_earnings_overview_aggregates_fn.sql",
        "167_users_account_status.sql",
        "168_users_updated_at_trigger.sql",
        "170_drivers_location_geog_surge.sql",
        "177_ride_stripe_invoice.sql",
        "178_sub_expiry_warned_3d.sql",
        "196_wallet_apply_credit.sql",
        "197_wallet_txn_reference_index.sql",
        "199_wallet_txn_type_referral_bonus.sql",
        "206_corporate_sections.sql",
        "207_guest_bookings.sql",
        "225_rides_event_version.sql",
        "239_trip_location_route_indexes.sql",
        "244_vehicle_vin_plaintext_at_rest.sql",
        "247_rides_distance_reconciled_at.sql",
        "251_drivers_period1_accum_pending_index.sql",
        "258_corporate_allowance_cap_in_rpc.sql",
        "260_provinces_backfill_and_fk.sql",
        "268_rides_legacy_import_metadata.sql",
    }
)


def _runner_statements(sql: str) -> list[str]:
    """Exact reimplementation of _apply_migration_autocommit's splitter."""
    out = []
    for chunk in [s.strip() for s in sql.split(";") if s.strip()]:
        lines = chunk.splitlines()
        while lines and (not lines[0].strip() or lines[0].strip().startswith("--")):
            lines.pop(0)
        executable = "\n".join(lines).strip()
        if executable:
            out.append(executable)
    return out


def _concurrently_migrations() -> list[pathlib.Path]:
    return sorted(p for p in _MIGRATIONS.glob("*.sql") if "CONCURRENTLY" in p.read_text().upper())


def _checked() -> list[pathlib.Path]:
    return [p for p in _concurrently_migrations() if p.name not in _KNOWN_UNSPLITTABLE]


def test_there_is_something_to_check():
    """Guard the guard — a glob that silently matched nothing, or an allowlist
    that swallowed every file, would make the parametrized cases vacuous."""
    assert _checked()


def test_allowlist_has_no_stale_entries():
    """Every frozen name must still exist and still contain CONCURRENTLY. A
    stale entry would silently exempt nothing — or worse, mask a file that was
    renamed rather than fixed."""
    names = {p.name for p in _concurrently_migrations()}
    assert _KNOWN_UNSPLITTABLE <= names, f"stale allowlist entries: {sorted(_KNOWN_UNSPLITTABLE - names)}"


@pytest.mark.parametrize("path", _checked(), ids=lambda p: p.name)
def test_no_prose_or_body_fragment_leaks_out(path: pathlib.Path):
    for stmt in _runner_statements(path.read_text()):
        first_word = stmt.split(None, 1)[0].upper().lstrip("(")
        assert first_word.startswith(_SQL_STARTS), (
            f"{path.name}: scripts/migrate.py would hand this to Postgres as a statement:\n\n"
            f"{stmt[:300]}\n\n"
            "Usually a semicolon in the middle of a comment line — the text after "
            "it becomes the first line of the next chunk, so the runner's "
            "leading-comment strip does not remove it. Reword the comment."
        )
