# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | corporate |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Corporate + admin portal review, round 2 — invoicing — route wiring slice |

## 1. Issue / gap identified

The round2-21 generator exists but nothing serves it over HTTP for either
audience the product decision named: "available to company admins in the
portal and internal admins."

## 2. Root cause

Never built.

## 3. Fix / remediation

- `routes/corporate_company.py` (company-portal): new
  `_fetch_all_month_rows` + `build_full_month_statement` helpers —
  **purely additive**, extracted so the new PDF route doesn't duplicate
  the "page through every row for a month" loop a third time (it already
  appears inline in both `billing_summary` and `billing_statement`); those
  two existing functions are **not touched** to avoid any risk to their
  existing tests. New `GET /billing/statements/{month}/pdf`
  (`require_company_admin`) calls `build_full_month_statement` +
  `generate_corporate_statement_pdf`, audits via `log_user_action`, and
  streams the bytes with `Content-Disposition: attachment` — same
  `Response(content=..., media_type="application/pdf", headers={...})`
  pattern as `routes/admin/driver_statements.py::download_driver_statement`.
- `routes/corporate_accounts.py` (internal admin): mirror endpoint
  `GET /{company_id}/billing/statements/{month}/pdf`
  (`get_current_admin`, this file's existing admin dependency alias),
  lazily importing `build_full_month_statement` from
  `routes.corporate_company` (matching this codebase's existing
  cross-route-file import convention — `webhooks.py`/`faqs.py`/`users.py`
  all do lazy sibling-module imports inside the function body, not at
  module top level) so a Spinr admin and the company's own admin render
  **byte-identical documents** from one shared aggregation, not two
  independently-computed ones that could drift.
- Same `Response(...)` streaming pattern as the existing
  `admin_view_kyb_document` endpoint already in this file (binary content
  precedent already established here).

## 4. Formatter-caused import corruption caught before commit

While wiring these routes, the repo's auto-formatter twice stripped a
dual-import name from the `except ImportError:` branch in
`corporate_company.py` before the code using it existed yet — a known,
previously-documented gotcha in this codebase, but this time it collapsed
`except ImportError: from utils.money import dollars_to_cents` down to
**`except ImportError: pass`**, silently deleting `dollars_to_cents`
from the bare-module import path even though it was already used by the
round2-18 self-serve top-up endpoint two commits ago. Caught by
re-grepping every dual-import block after each edit rather than trusting
the diff — both the `dollars_to_cents` regression and a second,
identical `Response` strip were fixed before this commit. **This is
flagged explicitly because it's a real, reproducible bug the formatter
introduced in previously-shipped code, not something this round's new
code did wrong** — a repo-level tooling risk worth a standing note (see
`ACTION_ITEMS.md` candidate) that any future dual-import edit must be
followed by a grep-verify pass, not just a syntax check.

## 5. Risk & impact on existing functionality

- **Blast radius: two route files, additive endpoints only.**
  `corporate_company.py`'s existing `billing_summary`/`billing_statement`/
  `billing_transactions` bodies are byte-for-byte unchanged (confirmed by
  diff) — the new helpers are appended after `billing_statement`, not
  inserted into it.
- `corporate_accounts.py`'s existing `admin_view_kyb_document` (the
  binary-content precedent) and every other endpoint in that file are
  unmodified — new endpoint appended after it.
- Grepped both files for path collisions: `/billing/statements/{month}/pdf`
  (company-portal) and `/{company_id}/billing/statements/{month}/pdf`
  (internal admin) don't overlap any existing route in either file.
- The formatter-corruption incident above was caught **before** commit —
  zero risk to production, but it's now a documented, repeatable failure
  mode for this session's remaining work: every dual-import edit in this
  round gets a post-edit grep-verify, not just `ast.parse`.

## 6. User-experience effect

**Backend-only in this commit — no UI wired yet** (round2-24 adds the
company-portal download button). A company admin or Spinr admin hitting
either endpoint directly gets a downloadable PDF today; no dashboard
button exists for either audience until the next commit for the
company-portal side. **The internal-admin endpoint has no dashboard UI
button planned this round** — no existing admin-dashboard screen shows a
company's billing/statement to Spinr staff at all (confirmed via earlier
research this round), so building one is out of scope creep beyond what
was asked; the endpoint is reachable directly (support tooling, `curl`,
or a future dashboard addition) exactly like several other admin
endpoints shipped API-first earlier this round (e.g. item #56's
wallet-portfolio, item #61's MFA-reset).

## 7. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/corporate_company.py` | New `_fetch_all_month_rows`, `build_full_month_statement`, `GET /billing/statements/{month}/pdf`; fixed 2 formatter-stripped imports (`dollars_to_cents`, `Response`) | Company-portal PDF download + repair a latent formatter bug |
| `backend/routes/corporate_accounts.py` | New `GET /{company_id}/billing/statements/{month}/pdf` mirror endpoint | Internal-admin PDF download, satisfying "available to... internal admins" |

## 8. Rollback plan

`git revert` the commit. No migration, no data written — pure new
endpoints reading already-existing data.

## 9. Verification performed

- [x] `ast.parse` syntax check on both files — clean.
- [x] Re-grepped every dual-import `try`/`except` block in both files
      after each edit, specifically because this commit caught the
      formatter silently corrupting one (see section 4) — this is now
      the standing verification step for any dual-import touch in this
      round, not just a one-off fix.
- [x] Confirmed via diff that neither `billing_summary`,
      `billing_statement`, `billing_transactions`
      (`corporate_company.py`), nor `admin_view_kyb_document`/any other
      existing endpoint (`corporate_accounts.py`) had a single line
      changed.
- [x] Confirmed the cross-route-file import style (`from .corporate_company
      import build_full_month_statement` inside the function body) matches
      the established pattern in `webhooks.py`/`faqs.py`/`users.py`
      exactly, rather than inventing a new import convention.
- [x] Did **not** run `pytest` for either file — per this round's
      explicit "don't run tests until everything is developed"
      instruction; dedicated HTTP-level tests for both new endpoints land
      in the very next commit.

## 10. Sign-off

- [x] Rollback plan is concrete — `git revert`
- [x] Blast radius is stated, not assumed — confirmed via diff that no
      existing endpoint body changed
- [x] No silent behavior change to a working flow — purely additive
      endpoints; the formatter-caused import bug was caught and fixed
      before it ever reached a commit, not shipped and discovered later

## What was NOT verified

Did not run `pytest` for either route file. The claim that the internal-
admin and company-portal PDFs are byte-identical (both call the same
`build_full_month_statement`/`generate_corporate_statement_pdf`) is
verified by code inspection (both call sites pass the same function with
the same arguments), not by an actual side-by-side PDF diff. No
admin-dashboard UI exists or is planned this round for the internal-admin
endpoint — stated as a real, standing gap rather than silently left
unaddressed.
