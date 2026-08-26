# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-22 |
| Author | vikas@ngitservices.com (via Claude) |
| Surface(s) | backend, CI config |
| Domain (Sentry tag) | payments |
| PR / commit link | see this branch's commit |
| Related issue or gap ID | `ACTION_ITEMS.md` A40's #26/N14 follow-up (`docs/change-log/2026-08-19-receipt-surge-line-item-fix.md`) |

## 1. Issue / gap identified

`backend/utils/receipt_pdf.py` and `backend/utils/email_receipt.py` render money onto a
7-year-retained legal record (the rider receipt) but were never in the `spinr-no-float-in-money`
Semgrep gate's `include` allowlist, so a future float-arithmetic bug in either file would ship
without the blocking gate catching it — flagged as a follow-up by the 2026-08-19 receipt
surge-line-item fix, not fixed in that PR.

## 2. Root cause

The gate's allowlist was built file-by-file as money bugs were found elsewhere; these two files
were never added, not because they're not money-adjacent, but because nothing had yet flagged
them.

## 3. Fix / remediation

Ran the gate's own rule (`.semgrep/spinr-rules.yml`'s `spinr-no-float-in-money`) against both
files **before** adding them, per the rule's own documented discipline ("re-run it before adding
a path here" — a prior pass that skipped this step missed 3 real findings in `fare_service.py`).

- `receipt_pdf.py`: zero `float()` calls, zero findings — added to the `include` list as-is.
- `email_receipt.py`: 2 findings, both `round(float(coverage) * 100)` where `coverage` is
  `coverage_ratio`, a GPS route-quality ratio (0.0–1.0) used for a display percentage — not a
  money value. Same false-positive class the gate's own comments already document for
  `drivers/_shared.py`'s GPS coordinates. Rather than leave the newly-blocking gate red on a
  non-issue, annotated both with an inline `# nosemgrep: spinr-no-float-in-money` + reason, per
  the rule's own message text ("If this is a display label rather than a value, add an inline
  `# nosemgrep` with the reason") — the same pattern already used once in `fare_service.py`.
  Re-ran the gate after annotating: 0 SR-03 findings on either file, 8 pre-existing/unrelated
  findings elsewhere unchanged (background-loop idempotency, ride-state guard, Stripe
  idempotency, PII-in-logs — none touched by this change).

**No functional code change in either file** — `email_receipt.py`'s edits are comment-only
(2 nosemgrep annotations); `receipt_pdf.py` is untouched.

## 4. Risk & impact on existing functionality

- **Blast radius: CI-gate configuration only.** No runtime behavior changed in either file.
  `receipt_pdf.py` has no diff at all; `email_receipt.py`'s only diff is 2 added comment lines.
- Going forward, any future `float()` call added to either file that isn't already covered by the
  gate's `_round()`/`_d()`/`.quantize()` exclusions will now block CI — this is the intended
  effect, not a side effect to mitigate.
- No interaction with any background loop, the ride state machine, or a wallet/allowance delta
  path.

## 5. User-experience effect

None. No rider/driver/admin-facing behavior changed — this is a CI gate scope change plus two
code comments.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `.semgrep/spinr-rules.yml` | Added `backend/utils/receipt_pdf.py` and `backend/utils/email_receipt.py` to `spinr-no-float-in-money`'s `include` list, with a dated comment recording the verification | Close the gap flagged by the 2026-08-19 fix — both files render money onto a legally-retained receipt |
| `backend/utils/email_receipt.py` | Added `# nosemgrep: spinr-no-float-in-money` + reason above the 2 `float(coverage)` calls | Both are GPS route-quality display percentages, not money — prevent the newly-blocking gate from false-positiving on them |
| `ACTION_ITEMS.md` | Marked A40's #26/N14 Semgrep-coverage follow-up done | Tracking |

## 7. Before / after

```python
# Before — .semgrep/spinr-rules.yml, SR-03 include list
include:
  - "backend/routes/fares.py"
  ...
  - "backend/utils/stripe_charge.py"
  # receipt_pdf.py / email_receipt.py NOT listed — a float-arithmetic bug in
  # either would ship without the blocking gate catching it.
```

```python
# After
include:
  - "backend/routes/fares.py"
  ...
  - "backend/utils/stripe_charge.py"
  - "backend/utils/receipt_pdf.py"
  - "backend/utils/email_receipt.py"
```

```python
# Before — backend/utils/email_receipt.py
coverage_text = (
    f"{round(float(coverage) * 100)}% GPS coverage" if coverage is not None else "GPS coverage unavailable"
)
```

```python
# After
coverage_text = (
    # nosemgrep: spinr-no-float-in-money -- coverage_ratio is a GPS route-quality ratio, not money.
    f"{round(float(coverage) * 100)}% GPS coverage" if coverage is not None else "GPS coverage unavailable"
)
```

## 8. Rollback plan

Pure config + comment change, no migration, no data mutation, no runtime behavior change.
Revert is a plain `git revert`.

## 9. Verification performed

- [x] Ran the actual `spinr-no-float-in-money` rule (via a locally-installed `semgrep`, matching
  CI's exact invocation) against the whole repo before and after the change:
  - Before adding the 2 files: 10 total findings repo-wide (none from these 2 files, since they
    weren't in scope yet).
  - After adding without annotations: 12 findings — confirmed the 2 predicted `email_receipt.py`
    false positives, `receipt_pdf.py` contributed 0.
  - After adding the nosemgrep annotations: back to 10 findings, 0 from either newly-added file;
    the other 8 (pre-existing, unrelated) unchanged.
- [x] `ruff check` / `ruff format --check` on `backend/utils/email_receipt.py` — clean.
- [x] Ran all 7 test files touching `email_receipt.py`/receipt rendering
  (`test_admin_send_receipt_email.py`, `test_receipt_route_snapshot.py`,
  `test_all_emails_are_branded.py`, `test_receipt_line_items.py`,
  `test_receipt_shell_snapshot.py`, `test_branded_receipt_flag.py`, `test_email_snapshots.py`) —
  87 passed, 0 failed (expected: comment-only change, no behavior to break).
- [ ] Did not run the full backend suite — scoped to the directly-relevant test files given the
  change is comment/config-only with zero logic touched.
- **No `npm run build` applies** — Python/YAML-only change.

## What was NOT verified

- Did not confirm this Semgrep version (`1.174.0`, installed fresh into a session-local venv) is
  identical to the pinned version CI's `semgrep/semgrep:latest` container image actually runs —
  used the latest PyPI release since no version pin was specified anywhere in this repo's CI
  config. If CI's image resolves a different rule-parsing behavior, this should surface as a CI
  failure on this PR, not a silent gap.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`).
- [x] Blast radius stated: CI-gate config + code comments only, zero runtime behavior change.
- [x] No silent behavior change — nothing here changes what any endpoint returns or what any
  receipt shows a rider.
