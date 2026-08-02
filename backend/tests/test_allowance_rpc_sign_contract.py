"""Sign contract for corporate_allowance_apply_delta (migration 248, finding 16;
grant semantics corrected in migration 277, corporate + admin portal review
High #2).

The original bug was invisible to the test suite because every settlement test
mocked `apply_rollback`, so the RPC's actual delta signs were never exercised.
This test reads the migration SQL and asserts the type→(master delta, used delta)
mapping directly, so a future edit that flips a sign fails here even though no
database is involved.

Ground truth being locked in:

    type                 master delta   used delta
    allowance_grant       0             -amount     <- pure limit raise (277 fix)
    allowance_reset       0             -used
    allowance_rollback   +amount        +amount
    ride_debit           -amount        +amount     <- the fix
    ride_debit_reversal  +amount        -amount     <- exact inverse of ride_debit
"""

import pathlib
import re

import pytest

pytestmark = pytest.mark.unit

_MIGRATION = (
    pathlib.Path(__file__).resolve().parents[1] / "migrations" / "277_corporate_allowance_grant_no_master_debit.sql"
)


def _delta_branches() -> dict[str, tuple[str, str]]:
    """Parse the IF/ELSIF chain into {p_type: (master_delta, used_delta)}."""
    sql = _MIGRATION.read_text()
    # Body of the mapping chain: from the first IF on p_type to the END IF that
    # closes it (the assignments to v_master_delta / v_used_delta).
    start = sql.index("IF p_type = 'allowance_grant'")
    end = sql.index("v_master_new :=", start)
    chain = sql[start:end]

    branches: dict[str, tuple[str, str]] = {}
    # Split on IF/ELSIF/ELSE keeping the guard with its body.
    parts = re.split(r"\b(?:ELSIF|IF|ELSE)\b", chain)
    guards = re.findall(r"\b(?:ELSIF|IF)\s+p_type\s*=\s*'([a-z_]+)'", chain)
    # The trailing ELSE branch is allowance_rollback (the documented default).
    bodies = [p for p in parts if "v_master_delta" in p]
    names = guards + ["allowance_rollback"]
    for name, body in zip(names, bodies):
        m = re.search(r"v_master_delta\s*:=\s*([^;]+);", body)
        u = re.search(r"v_used_delta\s*:=\s*([^;]+);", body)
        assert m and u, f"could not parse deltas for {name}"
        branches[name] = (m.group(1).strip(), u.group(1).strip())
    return branches


def test_ride_debit_charges_the_company():
    """The whole point of the fix: a ride must DEBIT master, never credit it."""
    b = _delta_branches()
    assert "ride_debit" in b, "migration 248 must define a ride_debit branch"
    master, used = b["ride_debit"]
    assert master == "-p_amount", (
        f"ride_debit master delta is {master!r} — must be -p_amount. A positive "
        "master delta credits the company on every allowance-covered ride."
    )
    assert used == "p_amount", f"ride_debit used delta is {used!r} — must be +p_amount"


def test_ride_debit_reversal_is_exact_inverse_of_ride_debit():
    b = _delta_branches()
    debit_master, debit_used = b["ride_debit"]
    rev_master, rev_used = b["ride_debit_reversal"]
    assert rev_master == debit_master.lstrip("-") or rev_master == f"-{debit_master}".replace("--", "")
    assert rev_master == "p_amount"
    assert rev_used == "-p_amount"
    # Inverse means the pair sums to zero on both axes.
    assert {debit_master, rev_master} == {"-p_amount", "p_amount"}
    assert {debit_used, rev_used} == {"p_amount", "-p_amount"}


def test_rollback_still_means_undo_a_grant():
    """allowance_rollback keeps its original semantics and is no longer the
    settlement path."""
    b = _delta_branches()
    assert b["allowance_rollback"] == ("p_amount", "p_amount")


def test_grant_is_a_pure_limit_raise_no_master_debit():
    """Corporate + admin portal review, High #2: a grant must not move real
    money — only ride_debit may debit master. Before migration 277, grant
    debited master AND ride_debit debited master again for the same
    grant-funded ride, double-charging the company."""
    b = _delta_branches()
    master, used = b["allowance_grant"]
    assert master == "0", (
        f"allowance_grant master delta is {master!r} — must be 0. A nonzero master "
        "delta here double-charges the company once the grant-funded ride later "
        "debits master again via ride_debit."
    )
    assert used == "-p_amount", f"allowance_grant used delta is {used!r} — must be -p_amount"


def test_settlement_does_not_call_apply_rollback():
    """Regression guard on the call site itself."""
    payment_service = (pathlib.Path(__file__).resolve().parents[1] / "services" / "payment_service.py").read_text()
    assert "apply_ride_debit(" in payment_service
    assert "corporate_allowance_service.apply_rollback(" not in payment_service, (
        "settle_corporate must not use apply_rollback — its master delta credits the company"
    )
