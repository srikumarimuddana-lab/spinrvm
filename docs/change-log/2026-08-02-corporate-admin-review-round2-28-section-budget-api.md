# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | corporate |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Corporate + admin portal review, round 2 — "department/section budgets" — API exposure slice |

## 1. Issue / gap identified

Round2-26/27 built the schema and the settlement-time recording hook, but
nothing in the existing sections CRUD API (`routes/corporate_company_bookings.py`)
lets a company set a cap or see the recorded spend.

## 2. Root cause

Never built — see round2-26 for full background.

## 3. Fix / remediation

- `SectionCreate`/`SectionUpdate` gain `monthly_budget_cap: Optional[Decimal]`
  (`ge=0, decimal_places=2`, same validation shape as every other money
  field in this codebase, e.g. `corporate_wallet.py`'s `AdjustRequest`).
- `create_section`: writes `monthly_budget_cap` to the insert row,
  converting `Decimal` → `float` at the write boundary (the established
  convention from item #51's settings fix — PostgREST/Supabase money
  columns take a plain float over the wire, never a `Decimal` object).
- `update_section`: same Decimal→float conversion applied to the patch
  dict when the field is present.
- `list_sections`: now also returns `budget_month` (current UTC calendar
  month) and `budget_spend_used` (a money-string, `"0.00"` when a section
  has no recorded spend this month) per section, via **one batch call**
  to `get_section_spend_map` (round2-26) — not N+1, same pattern as the
  existing `member_count` aggregation two lines above it in this same
  function.
- New `tests/test_corporate_section_budgets.py`, following the exact
  direct-handler-call + explicit-`ctx` pattern already established in
  the sibling `test_corporate_sections.py`. 7 tests covering the
  Decimal→float conversion on both create and update, negative-cap
  schema rejection, the batch spend-map wiring (including the "section
  with zero recorded spend defaults to `0.00`, never dropped or
  KeyErrors" case), and zero-sections handling.

## 4. Risk & impact on existing functionality

- **Blast radius: two Pydantic models gained one new optional field each,
  three existing handler bodies gained additive lines.** Every other
  field, validator, and existing behavior in `SectionCreate`/
  `SectionUpdate`/`create_section`/`update_section`/`list_sections` is
  unchanged — confirmed by diff.
- **Zero risk to the existing `test_corporate_sections.py` suite**:
  `test_list_sections_reports_member_counts` doesn't mock
  `db_supabase.get_section_spend_map`, but this repo's autouse
  `patch_external_dependencies` fixture already globally mocks
  `corporate_repo.py`'s `supabase` binding, so the real (unmocked)
  `get_section_spend_map` call inside `list_sections` executes against
  the mock client and returns `{}` safely — verified by tracing the
  fixture chain, not assumed. That test only asserts on `member_count`,
  which this change never touches.
- `monthly_budget_cap` defaulting to `None` on both models means an
  existing client calling `create_section`/`update_section` without the
  new field sees byte-identical behavior to before this commit.

## 5. User-experience effect

None yet — no UI reads/writes `monthly_budget_cap` or displays
`budget_spend_used` in this commit (round2-29 is the company-portal UI).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/corporate_company_bookings.py` | `monthly_budget_cap` on both section models + write-boundary conversion; `list_sections` returns `budget_month`/`budget_spend_used` via one batch call | Expose the round2-26/27 schema over the existing sections API |
| `backend/tests/test_corporate_section_budgets.py` | New file: 7 tests | Cover the Decimal conversion, schema validation, and batch spend-map wiring |

## 7. Rollback plan

`git revert` the commit. No migration involved — reverting removes the
new fields from API responses/requests; `corporate_sections.monthly_budget_cap`
values already set by a company stay in the database as harmless orphaned
data (the column itself was added in round2-26 and isn't touched by this
revert).

## 8. Verification performed

- [x] `ast.parse` syntax check — clean.
- [x] Confirmed the Decimal→float write-boundary conversion matches
      item #51's established precedent exactly, rather than leaving a
      `Decimal` object headed for a JSON/PostgREST write.
- [x] Traced `patch_external_dependencies` (autouse fixture) to confirm
      the existing `test_list_sections_reports_member_counts` test is
      unaffected by the new unmocked `get_section_spend_map` call it
      doesn't know about — not assumed, checked against the actual
      fixture chain.
- [x] Confirmed `get_section_spend_map([], month)` (the zero-sections
      case) short-circuits inside the repo function itself (round2-26)
      before any DB call — cheap even when called, not a real N+1 risk.
- [x] Did **not** run `pytest` for either file — per this round's
      explicit "don't run tests until everything is developed"
      instruction; deferred to the single end-of-round pass, which will
      also confirm empirically that
      `test_corporate_sections.py::test_list_sections_reports_member_counts`
      is genuinely unaffected, not just reasoned to be.

## 9. Sign-off

- [x] Rollback plan is concrete — `git revert`; any already-set
      `monthly_budget_cap` values are harmless orphaned data, not
      something requiring cleanup
- [x] Blast radius is stated, not assumed — confirmed via diff and by
      tracing the autouse-fixture interaction with the one existing test
      that could have collided
- [x] No silent behavior change to a working flow — every existing field
      and code path in the three touched handlers is unchanged; only new
      optional fields and new response keys were added

## What was NOT verified

Did not run `pytest`, so the "existing sections test suite is
unaffected" claim is reasoned from tracing the autouse fixture chain, not
confirmed by execution — this is a moderately confident but genuinely
unrun claim, flagged rather than silently assumed correct. The
company-portal UI to actually set a cap or see the spend indicator
remains the final follow-up commit.
