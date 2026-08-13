# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-12 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | n/a (test infrastructure only) |
| PR / commit link | (this branch: `claude/n12-email-snapshot-tooling`) |
| Related issue or gap ID | ACTION_ITEMS.md N12 |

## 1. Issue / gap identified

No visual/snapshot regression tooling exists for Spinr's transactional
emails. `tests/test_email_layout.py` and `tests/test_receipt_shell_snapshot.py`
each assert specific substrings (brand colour present, GST line present,
logo URL present) but nothing pins the **whole** rendered document — a
change that breaks the surrounding markup in a way none of those targeted
assertions happens to touch (an unclosed `<tr>`, a dropped
`role="presentation"`, a style attribute silently removed by a refactor)
passes clean today.

## 2. Root cause

Every existing email test was written substring-first because that's what
the specific bug being fixed at the time needed pinned — none of them were
written as a deliberate whole-document regression net, and no shared
snapshot-diffing helper existed to make that convenient. This is a genuine
tooling gap, not a bug in any shipped email.

## 3. Fix / remediation

Test-only change. Added:
- `backend/tests/_html_snapshot.py` — a small golden-file snapshot helper
  (`assert_snapshot(name, content)`). Missing snapshot → writes it and
  passes (new file shows up in the PR diff for review, same as any new
  fixture). Existing snapshot, content differs → fails with a unified diff
  and points at `SPINR_UPDATE_EMAIL_SNAPSHOTS=1` for a deliberate update.
  Never auto-updates by default.
- `backend/tests/test_email_snapshots.py` — 6 snapshot tests covering the
  two real, currently-shipping HTML-template generators:
  - `utils/email_layout.py`'s `render_email` (minimal shape: heading + one
    paragraph; full shape: every optional slot filled — greeting, subtitle,
    multiple paragraphs, CTA, footnote, explicit preheader) and its
    `render_from_text` bridge (used by KYB decisions, ops alerts, admin
    broadcasts, driver statements, tax/DSAR exports).
  - `utils/email_receipt.py`'s `generate_receipt_html`/`generate_receipt_text`,
    both shells (legacy pre-retrofit and the branded shell gated by
    `branded_receipt_enabled`), using the same fixed ride/rider/driver
    fixture as `test_receipt_shell_snapshot.py` (kept in sync deliberately
    so a real regression correlates between the two files).
- `backend/tests/snapshots/email/*.html` / `*.txt` — 9 committed golden
  files (generated once via `SPINR_UPDATE_EMAIL_SNAPSHOTS=1`, reviewed by
  reading their content, not just trusting the generator).

No application code changed.

## 4. Risk & impact on existing functionality

- Blast radius: **isolated, test infrastructure only**. `_html_snapshot.py`
  is a new, self-contained helper with no imports from application code.
  `test_email_snapshots.py` only reads `render_email`/`render_from_text`/
  `generate_receipt_html`/`generate_receipt_text` — the same public
  functions `test_email_layout.py` and `test_receipt_shell_snapshot.py`
  already exercise — it does not call anything those files don't already
  call, and calls nothing with side effects (no DB, no network, no email
  send).
- Grepped `backend/tests/` for any other file importing `_html_snapshot` or
  reading `tests/snapshots/`: none — this is the first and only consumer,
  so there is no existing behavior for this change to disturb.
- No interaction with money, ride state, auth, or any live-tested surface —
  this only reads already-deterministic template output.

## 5. User-experience effect

None — test-only change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/_html_snapshot.py` | New: golden-file snapshot assertion helper | Reusable diffing primitive for N12's regression net |
| `backend/tests/test_email_snapshots.py` | New: 6 whole-document snapshot tests | Closes the "nothing catches a layout that renders badly" gap N12 names |
| `backend/tests/snapshots/email/*.html`, `*.txt` (9 files) | New: committed golden fixtures | The regression baseline itself |
| `docs/change-log/2026-08-12-n12-email-snapshot-tooling.md` | New change-log entry | Required per CLAUDE.md |
| `ACTION_ITEMS.md` | N12 marked closed | Track progress |

## 7. Before / after

Not applicable — purely additive test/fixture files, no existing behavior-changing diff.

## 8. Rollback plan

`git revert` — pure test/fixture addition, no live-data footprint, no
application code touched, no migration.

## 9. Verification performed

- [x] `SPINR_UPDATE_EMAIL_SNAPSHOTS=1 pytest tests/test_email_snapshots.py -q --no-cov` — 6 passed, generated the 9 golden files.
- [x] Re-ran without the update flag: `pytest tests/test_email_snapshots.py -q --no-cov` — 6 passed against the committed snapshots (confirms deterministic rendering — no timestamps/randomness leaking into the fixed-fixture output).
- [x] Manually corrupted one golden file and re-ran the corresponding test to confirm the failure path actually fires with a readable diff, then restored it — confirmed working before relying on it as a regression net.
- [x] Full backend suite run: `pytest tests/ -q --no-cov` — see PR body for final pass/fail counts.
- [x] Blast-radius grep performed: no other file imports `_html_snapshot` or reads `tests/snapshots/`.
- [x] Reviewed against CLAUDE.md conventions: no money/auth/ride-state interaction; this is pure test infrastructure.
- [ ] Feature-flagged — not applicable, test-only.

## 10. What was NOT verified

- Does not verify actual rendering in a real mail client (Gmail, Outlook,
  Apple Mail) — that remains a real, separate gap (N12's own text already
  says so); building a per-client renderer/screenshot pipeline is out of
  scope for this pass. What this closes is the byte-level regression net
  for the HTML/text we generate and control, which is the layer most
  regressions (a broken merge, an accidental style removal, a template edit
  that reflows content) actually happen in — not client-specific rendering
  quirks.
- Only covers the two generator modules that produce full HTML documents
  (`email_layout.py`, `email_receipt.py`). Other email-adjacent code
  (`subscription_invoice.py`, which builds a PDF + kwargs dict rather than
  raw HTML directly, and is DB-dependent) was not brought into this net —
  scoped out as a different shape of problem, not an oversight.
