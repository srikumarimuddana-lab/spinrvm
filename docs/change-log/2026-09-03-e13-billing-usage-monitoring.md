# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-03 |
| Author | vikas@ngitservices.com (via Claude Code) |
| Surface(s) | backend (docs/CI only — no application code touched) |
| Domain (Sentry tag) | admin (ops/observability tooling, not a runtime domain — same classification as E4/E13's earlier entries) |
| PR / commit link | (filled in on PR) |
| Related issue or gap ID | ACTION_ITEMS.md E13 (follow-up to the same-day entry) |

## 1. Issue / gap identified

E13's first landing (same day) covered subscription-style renewal dates
(Fly, Railway, Supabase, etc.) but explicitly left out usage-based
services — Stripe, Twilio, Google Maps, Firebase — which have no renewal
date at all. Their real failure mode is different: a spend spike or an
unpaid/negative balance silently suspending the account. Twilio in
particular fails **silently** when suspended — OTP SMS sends stop working
with no application-level error, directly breaking rider/driver sign-in
per CLAUDE.md's OTP flow. Nothing checked this.

## 2. Root cause

Same as the parent E13 entry: this class of check never existed in the
repo before 2026-09-03's gap sweep. This is the second half of that sweep,
split out because usage-based billing needs a different API shape
(balance endpoints) than the renewal-calendar's date-based table.

## 3. Fix / remediation

- `.github/workflows/billing-usage-monitor.yml` — daily, checks Stripe
  balance (`GET /v1/balance`) and Twilio balance
  (`GET /Accounts/{sid}/Balance.json`), each independently gated behind
  its own optional GitHub secrets and no-op if unset (same pattern as
  `supabase-capacity-monitor.yml`). Alerts on a negative Stripe balance
  (Stripe will debit the connected bank account) or a low/critical Twilio
  balance (default warn ≤ $20, critical ≤ $5 — configurable via the
  workflow's env block).
- `docs/runbooks/renewal-calendar.md` — Stripe/Twilio rows updated to
  point at the new automated check; a new Google Maps Platform row added
  alongside the existing Firebase row, both explicitly flagged as **not**
  covered by this workflow (GCP Billing Budgets API gap, stated so it
  isn't silently assumed handled).
- `ACTION_ITEMS.md` E13 — follow-up note appended.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to CI/docs.** No application code touched.
  `renewal-calendar.md` and `ACTION_ITEMS.md` got targeted edits to
  existing rows/sections only — no other content changed.
- **No new required secret.** `STRIPE_SECRET_KEY`,
  `TWILIO_ACCOUNT_SID`/`TWILIO_AUTH_TOKEN` are optional; each check
  independently no-ops without them, verified by each step's own gate
  logic (`if [ -z "${VAR}" ]`).
- **Permissions**: `issues: write` + `contents: read` only, same as every
  other monitor in this set — cannot modify code, secrets, or deployment
  state, and cannot move money (this workflow only *reads* balance
  endpoints, never calls a Stripe charge/payout or Twilio send endpoint).
- **Credential scope matters**: the workflow's own comments recommend a
  Stripe *restricted* key (Balance:Read only) rather than the full secret
  key already used elsewhere in this codebase — if a human instead pastes
  the existing full-access Stripe secret key into this new GitHub secret,
  that widens this workflow's blast radius beyond what it needs. Called
  out explicitly in the workflow's setup comment; not something this
  change can enforce, only document.
- **False-positive risk**: a transient Stripe/Twilio API error, or the
  API response shape changing, could produce a misleading alert (handled
  today by an explicit `ERROR:` path that surfaces the raw API error
  message rather than crashing silently — verified in the "What was NOT
  verified" section below, this path was tested with a mocked error
  response).

## 5. User-experience effect

None. No rider, driver, corporate-admin, or internal-admin-facing behavior
changed. Internal ops/observability tooling only.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `.github/workflows/billing-usage-monitor.yml` | New | Daily Stripe + Twilio balance check, both optional/gated |
| `docs/runbooks/renewal-calendar.md` | Edited 3 rows | Points Stripe/Twilio at the new check; adds a Google Maps row and flags both GCP-billed services as an explicit, tracked, NOT-covered gap |
| `ACTION_ITEMS.md` | E13 entry extended | Records this follow-up and the still-open items |
| `docs/change-log/2026-09-03-e13-billing-usage-monitoring.md` | New | This log |

## 7. Before / after

Not applicable — new file plus targeted doc edits that add information,
don't change existing meaning.

## 8. Rollback plan

`git revert` is sufficient and complete: additive new workflow file, doc
edits are additive/informational, no data touched, no vendor
account/credential created (Stripe/Twilio secrets referenced don't exist
yet). Reverting fully undoes this with no follow-up cleanup.

## 9. Verification performed

- [x] Workflow YAML parses cleanly; every `run:` block's bash syntax
      checked with `bash -n` (all passed).
- [x] No `${{ }}` GitHub-context interpolation left inside any `run:`
      block (the same class of finding Semgrep OSS flagged on the parent
      E13 PR) — verified by script, all values routed through `env:`.
- [x] Stripe balance-parsing Python logic tested end-to-end against a
      mocked multi-currency response including a negative-balance
      currency — correctly flagged it and left the positive-balance
      currency clean.
- [x] Twilio balance-parsing Python logic tested end-to-end against three
      mocked responses: below-critical balance, healthy balance, and an
      API error response (`{"code":...}` shape) — all three produced the
      correct output path.
- [x] Endpoint URLs and auth schemes (`Bearer` for Stripe via basic-auth-with-empty-password
      convention, HTTP Basic for Twilio) cross-checked against each
      vendor's documented REST API convention, not guessed from memory
      alone — matches the well-known Stripe (`-u "$KEY:"`) and Twilio
      (`-u "$SID:$TOKEN"`) curl patterns.
- [ ] No automated test suite applies — CI/docs only, no application code.
- [ ] Not run in a live GitHub Actions environment or against a real
      Stripe/Twilio account — this session has no such credentials and
      none were created.

## 10. What was NOT verified

- **No real Stripe or Twilio API call was made.** All logic verification
  used hand-constructed mock JSON matching each vendor's documented
  response shape, not a live account. If either vendor's response schema
  differs from what's documented (undocumented field, deprecated field
  removed), this workflow's parser could fail — it's designed to surface
  that as a Python traceback in the job log rather than silently
  swallowing it, but that's a design intent, not an observed guarantee.
- **Threshold values ($20 warn / $5 critical for Twilio) are a
  reasonable-sounding default, not a number derived from Spinr's actual
  SMS OTP send volume/cost.** A human with real Twilio usage data should
  revisit these once the check is actually wired up with real credentials.
- **Whether a human will actually create a Stripe *restricted* key (as
  recommended) versus reusing the existing full-access key was not and
  cannot be verified from this session.**

## Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no data
      cleanup needed; no vendor account or credential created).
- [x] Blast radius is stated, not assumed: isolated to CI/docs, read-only
      API calls only, no live-tested surface touched.
- [x] No silent behavior change — nothing in this PR changes any
      already-shipped flow; UX effect field states "None" explicitly.
