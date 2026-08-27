# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-27 |
| Author | Claude Code (interactive session), user request: "c45" |
| Surface(s) | backend (production route logic + tests) |
| Domain (Sentry tag) | admin (compliance/tax reporting) |
| PR / commit link | commit following this log |
| Related issue or gap ID | `ACTION_ITEMS.md` C45 (failures 1 and 3) |

## 1. Issue / gap identified

Two `backend-test` failures on `main`:
`tests/test_compliance_reports.py::TestGstPstRows::test_truncation_flag_set_at_row_limit`
and `tests/test_compliance_reports_http.py::test_gst_pst_remittance_docx_format`, both
`Failed: Timeout (>30.0s) from pytest-timeout`.

## 2. Root cause

**Two distinct bugs, one test-only and one real production gap, both found by tracing the
actual hang locally** (reproduced it directly: the process pinned at 99.8% CPU and grew to
3.8GB RSS before being force-killed — a genuine unbounded loop, not "a hung external call"
as originally speculated in C45):

1. **Real production gap (fixed):** `routes/admin/compliance.py`'s `_gst_pst_rows` set
   `truncated = False` once, near the top of the function, and never reassigned it —
   dead code. The GST/PST remittance report (a CRA tax filing) could hit
   `_ROW_LIMIT` (10,000) rows and the caller's already-built "⚠ TRUNCATED" warning
   (`get_gst_pst_remittance`, which already correctly checks `if truncated:` before
   rendering the subtitle) would simply never fire — a filer had no way to know their
   remittance summary covered an unusually large dataset. Not a data-loss bug
   (`_get_all_rows_paginated` genuinely fetches every matching row, no rows are dropped),
   but a missing warning signal on a regulatory-tax-reporting surface, which is exactly the
   class of silent gap CLAUDE.md's "do not silently swallow errors" section exists for.
2. **Test-only bug (the actual cause of the timeout):** the failing test's `get_rows_side`
   mock ignored its `offset`/`limit` kwargs and always returned the full
   `_ROW_LIMIT`-sized fake dataset. `_get_all_rows_paginated`'s pagination loop
   (`while True: ... if len(batch) < page_size: break`) is correct against a real backend
   (which naturally returns shrinking pages as a dataset is exhausted) but never sees a
   short page against this mock — every page comes back exactly `page_size` long, so the
   loop runs forever, re-fetching (and re-accumulating) the same 10,000 rows on every
   iteration until pytest-timeout kills it 30+ seconds later. `test_gst_pst_remittance_docx_
   format` (failure 3) has its own mock returning a single row — no bug of its own — and was
   confirmed to be a **downstream victim** of failure 1's resource exhaustion (leftover CPU/
   memory pressure from the hung previous test, not a cleanly-reaped kill), not an
   independent hang: running both test files together after fixing only failure 1 passes
   84/84, including this one.

## 3. Fix / remediation

- `routes/admin/compliance.py`: `_gst_pst_rows` now computes
  `truncated = len(rides) >= _ROW_LIMIT` (was hardcoded `False`) after the paginated fetch
  completes. One line. The caller's existing warning-rendering logic (`get_gst_pst_
  remittance`, unchanged) starts working correctly the moment this returns a real signal.
- `backend/tests/test_compliance_reports.py`: `test_truncation_flag_set_at_row_limit`'s
  mock now slices `fake_rides` by the `offset`/`limit` it actually receives, correctly
  modeling real pagination — first page returns all 10,000 (a full page), second page
  (offset=10000) returns empty, loop terminates naturally after 2 iterations. Added a
  docstring explaining the infinite-loop mechanism so a future reader doesn't have to
  re-derive it from a timeout stack trace again.
- `test_gst_pst_remittance_docx_format` needed no change — confirmed fixed as a side effect.

## 4. Risk & impact on existing functionality

- **Blast radius, production change:** `_gst_pst_rows` is called from exactly one place
  (`get_gst_pst_remittance`, same file) — grepped the whole backend for other importers of
  `routes.admin.compliance` and found only `driver_distance.py` importing the unrelated
  `_render_tabular_report` helper. `_get_all_rows_paginated` itself is untouched.
- **What changes for a real filer:** a GST/PST remittance report that legitimately spans
  ≥10,000 completed rides will now show the "⚠ TRUNCATED at 10000 rides; narrow the date
  range for a complete filing" warning that was already built and waiting for a real
  signal. A report under that threshold is unaffected — `truncated` stays `False`, matching
  today's behavior exactly (verified: `test_sums_gst_and_pst_separately`,
  `test_hst_is_tracked_not_dropped_into_other`, and every other small-dataset test in the
  file still pass unmodified).
- **No data-loss risk introduced or fixed** — `_get_all_rows_paginated` already fetched
  every matching row before this change and still does; this only makes the *warning* about
  a large dataset accurate. The 10,000-row default itself is unchanged.
- **Test-only change (the mock fix) has zero production impact** — confirmed via ripple-check
  below.

## 5. User-experience effect

Internal admin only (Compliance & Tax Reporting module, `super_admin`-gated). A filer
running a GST/PST remittance over ≥10,000 rides will now see a warning subtitle on the
report they didn't see before — a more honest report, not a behavior regression. No
rider/driver/corporate-admin-facing change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/compliance.py` | `_gst_pst_rows`: `truncated` now computed from real row count instead of hardcoded `False`. | Dead code meant the report's own truncation warning could never fire. |
| `backend/tests/test_compliance_reports.py` | `test_truncation_flag_set_at_row_limit`'s mock now honors `offset`/`limit` like a real paginated backend. | The old mock caused a genuine infinite loop against `_get_all_rows_paginated`'s correct pagination logic. |

## 7. Before / after

```python
# Before -- production
rides = await _get_all_rows_paginated(...)
truncated = False
```

```python
# After -- production
rides = await _get_all_rows_paginated(...)
truncated = len(rides) >= _ROW_LIMIT
```

```python
# Before -- test mock (ignores offset/limit entirely)
async def get_rows_side(table, filters=None, **kw):
    return fake_rides
```

```python
# After -- test mock (pagination-aware)
async def get_rows_side(table, filters=None, limit=None, offset=0, **kw):
    return fake_rides[offset : offset + limit] if limit else fake_rides[offset:]
```

## 8. Rollback plan

`git revert` is complete and sufficient for both changes — no data, schema, or migration
component. The production line is a pure read/compute (no write path touched); reverting
it returns `_gst_pst_rows` to its prior (silently-never-warns) behavior with no cleanup
needed.

## 9. Verification performed

- [x] **Reproduced the hang directly, not just from the CI log** — ran the failing test in
      the background with output redirected to a file, observed the live process (99.8%
      CPU, `R` state, 3.8GB RSS and climbing) via `ps`, confirmed it was a genuine unbounded
      loop rather than a blocked-on-I/O hang before forming any hypothesis.
- [x] **Root cause traced by reading the actual production code path**
      (`_get_all_rows_paginated`'s pagination-termination logic), not guessed from the
      stack trace alone.
- [x] **Both fixes verified in isolation and together**: the fixed test alone (0.17s, was
      timing out); the full `test_compliance_reports.py` +
      `test_compliance_reports_http.py` together (84/84 pass in 24s, including failure 3
      with zero changes to its own test) — confirming failure 3 was a downstream victim of
      failure 1, not an independent bug.
- [x] **Broader ripple-check**: `test_compliance_reports.py` + `test_compliance_reports_
      http.py` + `test_compliance_export_script.py` + `test_compliance_rate_limit.py`
      together — 106/106 pass in 20s.
- [x] **Blast-radius grep performed** (§4) — `_gst_pst_rows` has exactly one caller;
      `_get_all_rows_paginated` itself untouched.
- [x] `ruff check` clean on both modified files. `ruff format --check` flags one
      pre-existing, unrelated formatting line in each file (confirmed via `git diff`
      hunk ranges — neither is inside this commit's actual diff) — left alone per
      surgical-changes convention, not introduced or touched by this fix.
- [x] Reviewed against `CLAUDE.md`'s "do not silently swallow errors" convention — this fix
      is a direct instance of that principle: a real-but-silent gap on a regulatory
      reporting surface, closed.

## 10. Sign-off

- [x] Rollback plan is concrete (plain revert, no data-layer component).
- [x] Blast radius is stated, not assumed — one caller, grepped and confirmed, full ripple
      test run across four related test files.
- [x] No silent behavior change to an already-shipped, previously-correct flow — the
      caller's warning-rendering was already built and already correct; this only supplies
      the real signal it was always meant to receive. Below-10,000-row reports are
      byte-for-byte unaffected (verified via the file's other passing tests).

## What was NOT verified

- No live/staging run against a real GST/PST dataset that actually reaches 10,000+ rows —
  verified via the existing unit-test harness only (mocked `get_rows`), consistent with
  this module's existing test conventions (`test_compliance_reports.py`'s own docstring:
  "Mirrors the direct-patch style... since these tests exercise the route module's own
  aggregation logic, not the Supabase query builder").
