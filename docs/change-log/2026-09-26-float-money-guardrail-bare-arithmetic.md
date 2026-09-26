# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-26 |
| Author | Claude Code |
| Surface(s) | backend (CI tooling: `.semgrep/spinr-rules.yml`, `.claude/hooks/pre-commit`) |
| Domain (Sentry tag) | payments (guard-rail change, not a runtime code path) |
| PR / commit link | (this branch, fix/5817-float-money-guardrails) |
| Related issue or gap ID | #5817 |

## 1. Issue / gap identified

Two float→Decimal bugs landed on `main` on 2026-09-25 (`d5714cc2eb`, `6d974a5fe5`) with the shape `round(subtotal + fees_result["fees_total"] + ...)` and `tips += r["tip_amount"]` — arithmetic between two already-numeric values pulled straight from a dict/DB row, no literal float and no `float()` cast anywhere. Neither existing guard rail catches this shape:
- The pre-commit hook's check 6 only matches a money-word adjacent to a **literal decimal number** (`fare * 1.5`).
- The merge-blocking semgrep rule `spinr-no-float-in-money` (SR-03) only matches `float($X)`, `* 1.0`, `/ 1.0`.
- `backend/routes/rides/receipts.py` and `backend/routes/rides/_shared.py` aren't in SR-03's `paths.include` at all.

## 2. Root cause

Both guard rails were built around the assumption that a float bug always involves either a literal float constant or an explicit `float()` cast. The actual bug class this window (and the one the `_shared.py` fare-calc code still has un-triaged) is arithmetic directly on dict/subscript values with no such marker — syntactically indistinguishable from correct code unless you also check whether the operands are wrapped in `_d()`/`Decimal()`.

## 3. Fix / remediation

1. **New semgrep rule SR-12** (`spinr-bare-dict-arithmetic-in-money-file`, merge-blocking, same enforcement tier as SR-03): flags `round($A + $B)`, `$DICT[$K] +/- $X` (either operand order), `$DICT.get($K) +/- $X` (either operand order), and `$X += $DICT[$K]`/`$X += $DICT.get($K)`. Scoped via `paths.include` to the same file list SR-03 already covers, **plus** `backend/routes/rides/receipts.py` and `backend/routes/rides/_shared.py` (the two files #5817 flagged as missing from SR-03's own list) — verified 0 findings for *this* rule specifically on both before adding them.
2. **Pre-commit hook check 6 widened** (still non-blocking — see §4 for why): the regex now also matches a money-suggestive word directly adjacent to `+=`/`-=`, to a bare variable (not just a literal float), and `round(...)` containing a `+`/`-`. This is a local, immediate hint at commit time; the semgrep rule is the actual enforcement.

## 4. Risk & impact on existing functionality

- **Blast radius**: two CI/tooling config files only. No application code changed, no runtime behavior changed.
- **Verified the new semgrep rule produces zero new findings anywhere in the repo** (`semgrep --config .semgrep/spinr-rules.yml .` — full repo scan, 594 files, 4 pre-existing findings from *other*, unrelated rules (`spinr-background-loop-needs-idempotency` ×2, `spinr-ride-state-needs-guard` ×1, `spinr-stripe-idempotency-static` ×1) — none from the new rule).
- **Verified it would have caught both original bugs**: reconstructed both bug shapes (`round(subtotal + fees_total + tax_amount, 2)` and `tips += r["tip_amount"]`) plus two additional variants (`round(subtotal + fees_result["fees_total"])`, `a.get("x") + b.get("y")`) in an isolated test file — the new rule fires on all four.
- **Deliberately did NOT add `receipts.py`/`_shared.py` to SR-03's own `paths.include`**: ran SR-03's underlying pattern (with its `paths` filter stripped, to see what it would find) against those two files and got **19 pre-existing, untriaged findings**, all in `_shared.py`. Adding them to SR-03's live include list right now would immediately break CI. This needs the same fix-or-annotate triage pass `earnings.py`/`features.py`/`referral_payout.py` got in the 2026-09-25 `float-money-writes` change (each finding reviewed: converted to `_f()` or annotated `# nosemgrep` with a reason) — that is real code-review work on a live-tested money surface, not a CI-config change, and is out of scope for this guard-rail fix. Flagging as a follow-up rather than rushing 19 untriaged findings through in the same change.
- **Pre-commit widening stays non-blocking, deliberately**: a bash regex cannot distinguish `Decimal` arithmetic from `float` arithmetic by syntax alone — both look identical (`subtotal + fees_total` is correct if both are already `Decimal`, wrong if both are `float`). Making this check blocking would false-positive on every correct Decimal computation containing a money word. The semgrep rule (AST-aware, still can't do full type inference either, but scoped to files where the convention is known and tested for 0 false positives) is the real enforcement; the pre-commit hook is a same-second local hint, not a gate.

## 5. User-experience effect

None. This changes CI/dev-tooling only, not any user-facing behavior, endpoint, or data.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `.semgrep/spinr-rules.yml` | Added SR-12 (`spinr-bare-dict-arithmetic-in-money-file`), merge-blocking | Catch the bare dict/subscript-arithmetic bug shape SR-03 misses |
| `.claude/hooks/pre-commit` | Widened check 6's regex (still non-blocking) to also match `+=`/`-=` and bare-variable arithmetic, and `round(...)` containing `+`/`-` | Give a local hint at commit time for the same bug shape |
| `docs/change-log/2026-09-26-float-money-guardrail-bare-arithmetic.md` | This entry | CLAUDE.md gate |

## 7. Before / after

```yaml
# Before: SR-03 only
- id: spinr-no-float-in-money
  patterns:
    - pattern-either:
        - pattern: float($X)
        - pattern: $AMOUNT * 1.0
        - pattern: $AMOUNT / 1.0
```

```yaml
# After: SR-03 unchanged, plus new SR-12
- id: spinr-bare-dict-arithmetic-in-money-file
  patterns:
    - pattern-either:
        - pattern: round($A + $B)
        - pattern: $DICT[$K] + $Y
        - pattern: $X + $DICT[$K]
        - pattern: $DICT.get($K) + $Y
        - pattern: $X += $DICT[$K]
        # (+ subtraction/get() variants)
```

## 8. Rollback plan

`git revert` — pure CI-config change, no schema/migration/runtime-state touched. Reverting restores the prior (narrower) guard-rail coverage; no data-level remediation needed.

## 9. Verification performed

- [x] New semgrep rule YAML validated (`python3 -c "import yaml; yaml.safe_load(...)"`).
- [x] Full-repo semgrep run (`semgrep --config .semgrep/spinr-rules.yml .`) — 594 files scanned, 0 new findings from the new rule, 4 pre-existing findings from unrelated rules untouched by this change.
- [x] New rule tested in isolation against reconstructed versions of both original 2026-09-25 bug shapes (plus 2 additional variants) — catches all 4.
- [x] SR-03's own underlying pattern (paths filter stripped) tested against `receipts.py`/`_shared.py` before deciding not to add them to SR-03's live list — found 19 pre-existing findings, confirming that would require separate triage work, not a same-PR addition.
- [x] Pre-commit regex widening manually tested against the same 4 reconstructed bug shapes — catches 3 of 4 (misses the money-word-free `a.get("x") + b.get("y")` variant, which the semgrep rule catches precisely since it's scoped to money files rather than word-matching).
- [x] **Adversarial review**: dispatched `spinr-money-auditor` against the diff per CLAUDE.md gate 10. It independently re-ran semgrep (confirmed 0 findings, matching this log's own claim) and found one real gap: the rule's single-arg `.get($K)` patterns missed the two-arg `.get($K, default)` idiom (`fees_result.get("fees_total", 0) + subtotal`), which is likely the *more* common real-world form since it avoids `KeyError` — that was the exact bug class this fix exists to catch, missed by the fix's own first draft. Fixed by adding `$DICT.get($K, $DEFAULT)` variants for all five `+`/`-`/`+=` patterns; re-verified both bypass cases (`fees_result.get("fees_total", 0) + subtotal`, `tips += r.get("tip_amount", 0)`) are now caught, and re-ran the full-repo scan to confirm still 0 new findings. The review also confirmed two accepted, documented limitations (no `*`/`/` coverage, bypassable via an intermediate variable) and flagged that `_shared.py`/`receipts.py` now have SR-12 coverage only, not SR-03 coverage — both now stated explicitly in the rule's own comment (see §3) rather than left implied.

## What was NOT verified

- Did not run the real `.claude/hooks/pre-commit` script end-to-end (would require staging a real commit) — verified the regex logic directly against test content instead.
- Did not attempt the 19-finding SR-03 triage for `_shared.py`/`receipts.py` — explicitly out of scope, flagged as a follow-up in §4.
- Did not verify against a live CI run (`security-gates.yml`'s "Money-safety gate" step) — reasoned from the same `semgrep` CLI invocation that step uses, not from an actual GitHub Actions run.
- The reviewer's two accepted limitations (no `*`/`/` coverage; bypassable by extracting the dict access to an intermediate variable first) were not fixed — they're the same ceiling any AST-pattern rule has, documented in the rule's own comment rather than chased further.
