"""Every operator in a filter predicate must be applied, not just the first.

`_apply_filters` used an if/elif chain, so the ordinary way to express "inside
this period" — ``{"$gte": start, "$lt": end}`` — compiled to the LOWER BOUND
ALONE and the query silently returned everything from `start` onward.

That was not academic. It produced wrong money on live surfaces:

- Every weekly/monthly driver statement summed payouts from its period start
  to the present, so all periods reported the same "paid out" total (observed
  in production as six consecutive statements all reading $115.70).
- T4A year windows pulled later years into an earlier slip, over-reporting
  driver income to the CRA.

These tests pin the compiled operator list rather than the query result, so a
regression shows up as a missing bound instead of a plausible-looking number.
"""

from __future__ import annotations

import pytest


class _FakeQuery:
    """Records every PostgREST operator applied, in order."""

    def __init__(self):
        self.ops: list[tuple[str, str, object]] = []

    def _record(self, op, col, val):
        self.ops.append((op, col, val))
        return self

    def eq(self, c, v):
        return self._record("eq", c, v)

    def neq(self, c, v):
        return self._record("neq", c, v)

    def gt(self, c, v):
        return self._record("gt", c, v)

    def gte(self, c, v):
        return self._record("gte", c, v)

    def lt(self, c, v):
        return self._record("lt", c, v)

    def lte(self, c, v):
        return self._record("lte", c, v)

    def in_(self, c, v):
        return self._record("in", c, list(v))

    def is_(self, c, v):
        return self._record("is", c, v)

    def like(self, c, v):
        return self._record("like", c, v)

    def ilike(self, c, v):
        return self._record("ilike", c, v)

    @property
    def not_(self):
        return _FakeNot(self)


class _FakeNot:
    def __init__(self, q):
        self._q = q

    def is_(self, c, v):
        return self._q._record("not.is", c, v)

    def in_(self, c, v):
        return self._q._record("not.in", c, list(v))


def test_two_sided_range_applies_both_bounds():
    """The regression that broke driver statements and T4A year windows."""
    from backend.repositories._base import _apply_filters

    q = _FakeQuery()
    _apply_filters(
        q,
        {
            "driver_id": "drv-1",
            "created_at": {"$gte": "2026-07-01T00:00:00+00:00", "$lt": "2026-08-01T00:00:00+00:00"},
        },
    )
    assert q.ops == [
        ("eq", "driver_id", "drv-1"),
        ("gte", "created_at", "2026-07-01T00:00:00+00:00"),
        ("lt", "created_at", "2026-08-01T00:00:00+00:00"),
    ]


def test_gt_lte_range_applies_both_bounds():
    from backend.repositories._base import _apply_filters

    q = _FakeQuery()
    _apply_filters(q, {"amount": {"$gt": 0, "$lte": 100}})
    assert q.ops == [("gt", "amount", 0), ("lte", "amount", 100)]


def test_in_combined_with_a_range_applies_all_three():
    from backend.repositories._base import _apply_filters

    q = _FakeQuery()
    _apply_filters(q, {"status": {"$in": ["a", "b"]}, "created_at": {"$gte": "x", "$lt": "y"}})
    assert q.ops == [
        ("in", "status", ["a", "b"]),
        ("gte", "created_at", "x"),
        ("lt", "created_at", "y"),
    ]


def test_and_workaround_still_compiles_to_both_bounds():
    """Callers that hand-split a range with $and (routes/admin/analytics.py)
    predate the fix and must keep working unchanged."""
    from backend.repositories._base import _apply_filters

    q = _FakeQuery()
    _apply_filters(q, {"$and": [{"created_at": {"$gte": "s"}}, {"created_at": {"$lte": "e"}}]})
    assert q.ops == [("gte", "created_at", "s"), ("lte", "created_at", "e")]


def test_unsupported_operator_raises_rather_than_being_dropped():
    """A dropped predicate matches MORE rows, and _apply_filters is shared
    with update/delete — so an unknown operator must surface loudly."""
    from backend.repositories._base import _apply_filters

    with pytest.raises(ValueError, match=r"unsupported operator"):
        _apply_filters(_FakeQuery(), {"created_at": {"$between": ["a", "b"]}})


def test_regex_options_is_a_modifier_not_an_unsupported_operator():
    from backend.repositories._base import _apply_filters

    q = _FakeQuery()
    _apply_filters(q, {"name": {"$regex": "ann", "$options": "i"}})
    assert q.ops == [("ilike", "name", "%ann%")]


def test_in_with_non_list_raises():
    from backend.repositories._base import _apply_filters

    with pytest.raises(ValueError, match=r"\$in expects a list"):
        _apply_filters(_FakeQuery(), {"id": {"$in": "not-a-list"}})


def test_or_leaf_rejects_multi_operator_predicate():
    """One or() leaf can express one operator; emitting just the first would
    silently widen the OR (and an $or is shared with update/delete)."""
    from backend.repositories._base import _build_or_clause_term

    with pytest.raises(ValueError, match=r"multiple operators"):
        _build_or_clause_term("created_at", {"$gte": "s", "$lt": "e"})


def test_or_leaf_still_accepts_regex_with_options():
    from backend.repositories._base import _build_or_clause_term

    assert _build_or_clause_term("name", {"$regex": "ann", "$options": "i"}).startswith("name.ilike.")
