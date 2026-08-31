"""run_migrations.py NEVER_APPLY skip-list mechanism.

ACTION_ITEMS.md G2 ("116 migration files merged to `main` had never been
applied to the live database", "Still open" item 1) found 4 migration files
that must never be run as merged -- applying them would be a security
regression relative to the live schema. They are intentionally left
untracked in `schema_migrations` (not falsely marked "applied"), but before
this fix `run_migrations.py` had no mechanism to actively refuse running
them if the full migration set were replayed against a fresh/different
environment (a new staging DB, a disaster-recovery restore replay).

These tests exercise the skip-list in isolation (no real files, no DB) so
they run at unit speed and don't depend on backend/migrations/'s actual
contents drifting over time -- a separate assertion below pins the 4 real
filenames GC2 named.

Run:
    pytest backend/tests/test_run_migrations_skip_list.py -v
"""

from __future__ import annotations

from pathlib import Path

from backend.scripts.run_migrations import NEVER_APPLY, _classify

# The 4 filenames ACTION_ITEMS.md G2 named as must-never-apply. If this
# assertion ever fails, either G2 was resolved (update this test + the
# runner's NEVER_APPLY together) or someone accidentally dropped an entry.
_G2_FILENAMES = {
    "70_fix_financial_events_rls.sql",
    "78_fix_pii_function_search_path.sql",
    "137_fix_pii_encrypt_pgsodium_perms.sql",
    "26_rls_coverage_gap.sql",
}


def test_g2_files_are_all_registered_in_never_apply():
    assert _G2_FILENAMES <= set(NEVER_APPLY.keys())
    # Every entry must carry a non-trivial, non-empty reason -- not a stub.
    for filename in _G2_FILENAMES:
        assert len(NEVER_APPLY[filename]) > 20


def test_skipped_file_is_never_pending():
    files = [Path("70_fix_financial_events_rls.sql"), Path("71_some_other_migration.sql")]
    pending, already, drifted, skipped = _classify(files, applied={})

    assert [p.name for p in pending] == ["71_some_other_migration.sql"]
    assert already == []
    assert drifted == []
    assert [p.name for p, _ in skipped] == ["70_fix_financial_events_rls.sql"]


def test_skipped_file_is_never_already_applied_either():
    """A skip-listed file gets its own status -- never conflated with 'applied'."""
    files = [Path("78_fix_pii_function_search_path.sql")]
    # Not tracked in schema_migrations at all (the real-world G2 state).
    pending, already, drifted, skipped = _classify(files, applied={})

    assert pending == []
    assert already == []
    assert len(skipped) == 1
    assert skipped[0][0].name == "78_fix_pii_function_search_path.sql"
    assert "vault" in skipped[0][1]


def test_skipped_file_contradiction_logged_loudly(capsys):
    """If a skip-listed file is somehow already tracked as applied, that's a
    contradiction (it ran through some other path despite the skip-list) and
    must be surfaced loudly on stderr, not silently absorbed into either the
    'already applied' or 'skipped' bucket alone."""
    files = [Path("137_fix_pii_encrypt_pgsodium_perms.sql")]
    applied = {"137_fix_pii_encrypt_pgsodium_perms.sql": "deadbeef"}

    pending, already, drifted, skipped = _classify(files, applied)

    assert pending == []
    assert already == []
    assert drifted == []
    assert len(skipped) == 1  # still classified as skipped, not "already"

    err = capsys.readouterr().err
    assert "CONTRADICTION" in err
    assert "137_fix_pii_encrypt_pgsodium_perms.sql" in err
    assert "schema_migrations" in err


def test_skipped_files_never_mixed_with_normal_pending_and_applied():
    files = [
        Path("26_rls_coverage_gap.sql"),  # skip-listed
        Path("30_normal_pending.sql"),  # pending
        Path("31_normal_applied.sql"),  # already applied, checksum matches
    ]

    from backend.scripts.run_migrations import _checksum

    # _checksum reads real bytes off disk; use a real, non-skip-listed file
    # for the "already applied" case so the checksum comparison is real.
    applied_path = Path("backend/migrations") / "24_schema_migrations.sql"
    files[2] = applied_path
    applied = {applied_path.name: _checksum(applied_path)}

    pending, already, drifted, skipped = _classify(files, applied)

    assert [p.name for p in pending] == ["30_normal_pending.sql"]
    assert [p.name for p in already] == [applied_path.name]
    assert drifted == []
    assert [p.name for p, _ in skipped] == ["26_rls_coverage_gap.sql"]


def test_apply_pending_never_receives_skipped_files():
    """End-to-end sanity: even if --apply is effectively requested, a
    skip-listed file can never reach _apply_pending because _classify never
    puts it in `pending` in the first place."""
    files = [Path("70_fix_financial_events_rls.sql"), Path("999_ordinary.sql")]
    pending, already, drifted, skipped = _classify(files, applied={})

    assert all(p.name != "70_fix_financial_events_rls.sql" for p in pending)
    assert any(p.name == "70_fix_financial_events_rls.sql" for p, _ in skipped)
