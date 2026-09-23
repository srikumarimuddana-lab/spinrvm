"""Migration 454 keeps adjustment rows out of fleet cash payout metrics."""

from pathlib import Path

SQL = (Path(__file__).resolve().parents[1] / "migrations" / "454_admin_cash_payout_reporting.sql").read_text()
STATS = SQL.split("CREATE OR REPLACE FUNCTION public.admin_payout_stats()")[1].split(
    "CREATE OR REPLACE FUNCTION public.admin_payout_window_stats"
)[0]
WINDOW = SQL.split("CREATE OR REPLACE FUNCTION public.admin_payout_window_stats")[1]


def test_all_status_admin_stats_exclude_adjustment_rows():
    assert "FROM payouts\n    WHERE payout_type IS DISTINCT FROM 'clawback'" in STATS
    assert "'total_paid'" in STATS
    assert "'payout_count'" in STATS


def test_window_cash_totals_and_counts_exclude_clawbacks():
    assert "status, payout_type, amount" in WINDOW
    assert WINDOW.count("payout_type IS DISTINCT FROM 'clawback'") >= 8
    assert "'completed' AND payout_type IS DISTINCT FROM 'clawback'" in WINDOW
    assert "COUNT(*) FILTER (WHERE payout_type IS DISTINCT FROM 'clawback') AS total_count" in WINDOW


def test_read_only_security_grants_remain_restricted():
    for body, signature in (
        (STATS, "public.admin_payout_stats()"),
        (
            WINDOW,
            "public.admin_payout_window_stats(",
        ),
    ):
        normalized = " ".join(body.split())
        assert "STABLE" in body
        assert "SECURITY DEFINER" in body
        assert "SET search_path = public, pg_catalog" in body
        assert f"REVOKE EXECUTE ON FUNCTION {signature}" in normalized
        assert "FROM PUBLIC, anon, authenticated" in normalized
        assert f"GRANT EXECUTE ON FUNCTION {signature}" in normalized
        assert "TO service_role" in normalized
    assert "timestamptz, timestamptz, timestamptz, timestamptz, text )" in " ".join(WINDOW.split())
