"""Regression test: tests/direct_pool/conftest.py's skip hook must only touch
items that live under tests/direct_pool/.

Found 2026-09-03 while driving PR #4883 to green. `pytest_collection_modifyitems`
receives the WHOLE session's item list, and the hook in
`tests/direct_pool/conftest.py` (landed with #4873, T11) marked every one of
them skipped whenever psycopg2 or TEST_DATABASE_URL/DATABASE_URL was missing.
Any `pytest` run from backend/ without a DSN -- ci-guardrails'
shared-coverage-run, a developer's local run -- reported the entire suite
(13.8k tests) as skipped in ~70 s, and the coverage.json the guard-rail gates
read measured an all-skipped suite (~21 % total, routes/rides/ at 12.2 %
against an 80 % floor). ci.yml's backend-test job was unaffected only because
it always exports a DATABASE_URL, which makes the hook a no-op there.

This test loads that conftest as a plain module (under a private name, so it
never collides with pytest's own copy), forces the skip condition on, and
feeds the hook one item inside the directory and one outside it. No database
is needed.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_TESTS_DIR = Path(__file__).resolve().parent
_DIRECT_POOL_CONFTEST = _TESTS_DIR / "direct_pool" / "conftest.py"


class _FakeItem:
    """The two attributes the hook uses: `path` and `add_marker`."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.markers: list = []

    def add_marker(self, marker) -> None:
        self.markers.append(marker)


def _load_direct_pool_conftest():
    spec = importlib.util.spec_from_file_location("_direct_pool_conftest_under_test", _DIRECT_POOL_CONFTEST)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.unit
def test_skip_marker_applies_only_to_items_under_direct_pool(monkeypatch):
    mod = _load_direct_pool_conftest()
    monkeypatch.setattr(mod, "_SKIP_CONDITION", True)

    inside = _FakeItem(_TESTS_DIR / "direct_pool" / "test_claim_batch.py")
    outside = _FakeItem(_TESTS_DIR / "test_dispatch_claim_parity.py")
    sibling_dir = _FakeItem(_TESTS_DIR / "rls" / "test_rides_rls.py")

    mod.pytest_collection_modifyitems(config=None, items=[inside, outside, sibling_dir])

    assert [m.name for m in inside.markers] == ["skip"]
    assert outside.markers == [], "an item outside tests/direct_pool/ must not be skipped"
    assert sibling_dir.markers == [], "a sibling directory's items must not be skipped"


@pytest.mark.unit
def test_hook_is_a_no_op_when_precondition_is_met(monkeypatch):
    mod = _load_direct_pool_conftest()
    monkeypatch.setattr(mod, "_SKIP_CONDITION", False)

    inside = _FakeItem(_TESTS_DIR / "direct_pool" / "test_claim_batch.py")
    mod.pytest_collection_modifyitems(config=None, items=[inside])

    assert inside.markers == []


@pytest.mark.unit
def test_item_without_a_path_is_left_alone(monkeypatch):
    mod = _load_direct_pool_conftest()
    monkeypatch.setattr(mod, "_SKIP_CONDITION", True)

    class _Pathless:
        def __init__(self) -> None:
            self.markers: list = []

        def add_marker(self, marker) -> None:
            self.markers.append(marker)

    odd = _Pathless()
    mod.pytest_collection_modifyitems(config=None, items=[odd])

    assert odd.markers == []
