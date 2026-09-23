"""Static guard: SECURITY DEFINER functions must not stay client-executable.

Background (migrations 354 and 450): Postgres grants EXECUTE to PUBLIC on
CREATE FUNCTION, and Supabase's default ACL additionally grants anon /
authenticated. The pattern

    REVOKE EXECUTE ON FUNCTION f(...) FROM anon, authenticated;

is a NO-OP for access control: anon/authenticated keep EXECUTE through PUBLIC.
Migrations 380-395 shipped 15 admin_* SECURITY DEFINER aggregates that way,
after 354's one-shot sweep had already run, and production had them callable
via /rest/v1/rpc with the public anon key until migration 450.

This test replays backend/migrations in runner order (numeric prefix) and
fails if any SECURITY DEFINER function (non-trigger) is created/replaced after
354 without a later ``REVOKE ... FROM PUBLIC`` for that function name, or if a
function's last anon-related privilege statement is a GRANT to anon/PUBLIC
(unless explicitly allow-listed with a reason).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from run_migrations import migration_sort_key  # noqa: E402

pytestmark = pytest.mark.unit

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"
SWEEP_354 = "354_revoke_public_execute_on_security_definer_fns.sql"
MIGRATION_450 = MIGRATIONS_DIR / "450_revoke_client_exec_admin_rpcs.sql"

# Functions that may legitimately stay executable by anon. Keep empty unless a
# function is genuinely needed by unauthenticated RLS evaluation; say why.
ANON_EXEC_ALLOWLIST: dict[str, str] = {}

_CREATE_FN = re.compile(
    r"CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+(?:public\.)?\"?(\w+)\"?\s*\((.*?)\)\s*RETURNS(.*?)\$\w*\$",
    re.I | re.S,
)
_REVOKE_FN = re.compile(
    r"REVOKE\s+(?:EXECUTE|ALL(?:\s+PRIVILEGES)?)\s+ON\s+FUNCTION\s+(?:public\.)?\"?(\w+)\"?"
    r"\s*(?:\([^;]*?\))?\s+FROM\s+([^;]+);",
    re.I | re.S,
)
_GRANT_FN = re.compile(
    r"GRANT\s+(?:EXECUTE|ALL(?:\s+PRIVILEGES)?)\s+ON\s+FUNCTION\s+(?:public\.)?\"?(\w+)\"?"
    r"\s*(?:\([^;]*?\))?\s+TO\s+([^;]+);",
    re.I | re.S,
)


def _strip_sql_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.S)
    return re.sub(r"--[^\n]*", "", sql)


def _roles(clause: str) -> set[str]:
    return {r.strip().strip('"').lower() for r in clause.split(",") if r.strip()}


def _ordered_migrations() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("*.sql"), key=lambda p: migration_sort_key(p.name))


def _scan(sources: list[tuple[str, str]]):
    """Return (secdef_created, public_revokes, anon_events) from ordered sources.

    secdef_created: fn -> (index, filename) of the LAST SECURITY DEFINER create.
    public_revokes: fn -> [index, ...] of REVOKE ... FROM PUBLIC statements.
    anon_events:    fn -> [(index, "grant"|"revoke"), ...] touching anon/PUBLIC.
    """
    created: dict[str, tuple[int, str]] = {}
    public_revokes: dict[str, list[int]] = {}
    anon_events: dict[str, list[tuple[int, str]]] = {}
    for idx, (name, raw) in enumerate(sources):
        sql = _strip_sql_comments(raw)
        # Statement order inside a file matters for anon events; collect with offsets.
        events: list[tuple[int, str, str]] = []
        for m in _CREATE_FN.finditer(sql):
            header = "RETURNS" + m.group(3)
            if re.search(r"SECURITY\s+DEFINER", header, re.I) and not re.search(
                r"RETURNS\s+(event_)?trigger\b", header, re.I
            ):
                created[m.group(1).lower()] = (idx, name)
        for m in _REVOKE_FN.finditer(sql):
            fn, roles = m.group(1).lower(), _roles(m.group(2))
            if "public" in roles:
                public_revokes.setdefault(fn, []).append(idx)
            if roles & {"public", "anon"}:
                events.append((m.start(), fn, "revoke"))
        for m in _GRANT_FN.finditer(sql):
            fn, roles = m.group(1).lower(), _roles(m.group(2))
            if roles & {"public", "anon"}:
                events.append((m.start(), fn, "grant"))
        for _, fn, kind in sorted(events):
            anon_events.setdefault(fn, []).append((idx, kind))
    return created, public_revokes, anon_events


def _violations(sources: list[tuple[str, str]], sweep_name: str | None = SWEEP_354) -> list[str]:
    created, public_revokes, anon_events = _scan(sources)
    names = [n for n, _ in sources]
    sweep_idx = names.index(sweep_name) if sweep_name in names else -1
    problems = []
    for fn, (idx, fname) in sorted(created.items(), key=lambda kv: kv[1][0]):
        covered_by_sweep = idx <= sweep_idx
        revoked_later = any(r >= idx for r in public_revokes.get(fn, []))
        if not (covered_by_sweep or revoked_later):
            problems.append(
                f"{fname}: SECURITY DEFINER {fn}() never revoked FROM PUBLIC (FROM anon, authenticated alone is a no-op)"
            )
    for fn, evts in anon_events.items():
        if fn in created and evts and evts[-1][1] == "grant" and fn not in ANON_EXEC_ALLOWLIST:
            problems.append(f"{names[evts[-1][0]]}: SECURITY DEFINER {fn}() left GRANTed to anon/PUBLIC")
    return problems


def _repo_sources() -> list[tuple[str, str]]:
    return [(p.name, p.read_text(encoding="utf-8", errors="replace")) for p in _ordered_migrations()]


# --------------------------------------------------------------------------
# Repo-wide guard
# --------------------------------------------------------------------------


def test_no_secdef_function_left_client_executable():
    problems = _violations(_repo_sources())
    assert not problems, "\n".join(problems)


def test_admin_secdef_functions_revoked_from_public():
    """Narrower, explicit version for admin_* (the class the advisor flagged)."""
    problems = [p for p in _violations(_repo_sources()) if " admin_" in p]
    assert not problems, "\n".join(problems)


def test_guard_would_have_caught_380_to_395_without_450():
    sources = [s for s in _repo_sources() if s[0] != MIGRATION_450.name]
    flagged = {
        line.split("SECURITY DEFINER ")[1].split("(")[0] for line in _violations(sources) if "FROM PUBLIC" in line
    }
    assert flagged == {
        "admin_audit_actor_stats",
        "admin_cloud_message_stats_rollup",
        "admin_daily_ride_stats",
        "admin_dispute_stats_rollup",
        "admin_driver_bonus_summary",
        "admin_driver_referral_board",
        "admin_driver_ride_summary",
        "admin_email_log_stats",
        "admin_incentive_claims_sum",
        "admin_mrr_at_cutoff",
        "admin_payout_period_snapshot",
        "admin_payout_window_stats",
        "admin_promo_stats",
        "admin_referred_user_count",
        "admin_subscription_stats_rollup",
    }
    anon_left = [line for line in _violations(sources) if "left GRANTed" in line]
    assert any("is_party_to_lost_and_found_case" in line for line in anon_left)


# --------------------------------------------------------------------------
# Detector self-tests on synthetic SQL
# --------------------------------------------------------------------------

_FN = """
CREATE OR REPLACE FUNCTION public.admin_x(p int)
RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public
AS $$ SELECT '{}'::jsonb $$;
"""


def test_detector_flags_noop_anon_authenticated_revoke():
    src = [("500_x.sql", _FN + "REVOKE EXECUTE ON FUNCTION public.admin_x(int) FROM anon, authenticated;")]
    assert _violations(src, sweep_name=None)


def test_detector_accepts_public_revoke():
    src = [("500_x.sql", _FN + "REVOKE EXECUTE ON FUNCTION public.admin_x(int) FROM PUBLIC, anon, authenticated;")]
    assert _violations(src, sweep_name=None) == []


def test_detector_accepts_revoke_in_later_migration():
    src = [
        ("500_x.sql", _FN),
        ("501_y.sql", "REVOKE ALL ON FUNCTION admin_x(int) FROM PUBLIC;"),
    ]
    assert _violations(src, sweep_name=None) == []


def test_detector_flags_explicit_anon_grant():
    src = [
        (
            "500_x.sql",
            _FN
            + "REVOKE ALL ON FUNCTION admin_x(int) FROM PUBLIC;\nGRANT EXECUTE ON FUNCTION admin_x(int) TO anon, service_role;",
        )
    ]
    assert any("left GRANTed" in p for p in _violations(src, sweep_name=None))


def test_detector_ignores_commented_revoke():
    src = [("500_x.sql", _FN + "-- REVOKE EXECUTE ON FUNCTION admin_x(int) FROM PUBLIC;")]
    assert _violations(src, sweep_name=None)


def test_detector_ignores_security_invoker_and_triggers():
    invoker = "CREATE FUNCTION admin_y() RETURNS int LANGUAGE sql AS $$ SELECT 1 $$;"
    trig = "CREATE FUNCTION trg_z() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER AS $$ BEGIN RETURN NEW; END $$;"
    assert _violations([("500_x.sql", invoker + trig)], sweep_name=None) == []


# --------------------------------------------------------------------------
# Migration 450 contract
# --------------------------------------------------------------------------

EXPECTED_ADMIN_SIGNATURES = {
    "admin_audit_actor_stats(timestamptz, integer)",
    "admin_cloud_message_stats_rollup()",
    "admin_daily_ride_stats(timestamptz, timestamptz, text[])",
    "admin_dispute_stats_rollup()",
    "admin_driver_bonus_summary(text, timestamptz)",
    "admin_driver_referral_board(integer)",
    "admin_driver_ride_summary(text, timestamptz, timestamptz)",
    "admin_email_log_stats(timestamptz)",
    "admin_incentive_claims_sum(timestamptz, timestamptz)",
    "admin_mrr_at_cutoff(timestamptz)",
    "admin_payout_period_snapshot(timestamptz, timestamptz)",
    "admin_payout_window_stats(timestamptz, timestamptz, timestamptz, timestamptz, text)",
    "admin_promo_stats(timestamptz, timestamptz)",
    "admin_referred_user_count(text, text, text)",
    "admin_subscription_stats_rollup(timestamptz, timestamptz, text[])",
}


def _450() -> str:
    return _strip_sql_comments(MIGRATION_450.read_text(encoding="utf-8"))


def test_450_revokes_each_admin_signature_and_regrants_service_role():
    sql = _450()
    for sig in EXPECTED_ADMIN_SIGNATURES:
        esc = re.escape(f"public.{sig}")
        assert re.search(rf"REVOKE EXECUTE ON FUNCTION {esc} FROM PUBLIC, anon, authenticated;", sql), sig
        assert re.search(rf"GRANT\s+EXECUTE ON FUNCTION {esc} TO service_role;", sql), sig


def test_450_keeps_authenticated_on_rls_helper_but_drops_anon():
    sql = _450()
    assert "REVOKE EXECUTE ON FUNCTION public.is_party_to_lost_and_found_case(text) FROM PUBLIC, anon;" in sql
    assert re.search(
        r"GRANT\s+EXECUTE ON FUNCTION public\.is_party_to_lost_and_found_case\(text\) TO authenticated, service_role;",
        sql,
    )
    # authenticated must NOT be revoked: lfm_select / lfm_insert RLS policies call it.
    assert not re.search(r"is_party_to_lost_and_found_case\(text\)[^;]*FROM[^;]*authenticated", sql)


def test_450_is_privileges_only_and_reloads_postgrest():
    sql = _450()
    upper = sql.upper()
    for forbidden in (
        "CREATE OR REPLACE FUNCTION",
        "DROP ",
        "ALTER TABLE",
        "INSERT ",
        "UPDATE ",
        "DELETE ",
        "ALTER DEFAULT PRIVILEGES",
    ):
        assert forbidden not in upper, forbidden
    assert "NOTIFY pgrst, 'reload schema';" in sql
    assert "post-condition failed" in sql


def test_450_has_rollback_comment():
    assert "-- Rollback" in MIGRATION_450.read_text(encoding="utf-8")
