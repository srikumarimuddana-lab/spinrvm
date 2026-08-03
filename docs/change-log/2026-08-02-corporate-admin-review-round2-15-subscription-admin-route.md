# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | corporate, payments, admin |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Corporate + admin portal review, round 2 — "no pricing/fee mechanism exists for the corporate product" (business decision: flat SaaS subscription, full Stripe automation) — admin route slice |

## 1. Issue / gap identified

Fourth slice of the corporate subscription-billing build: `assign_subscription`/
`cancel_subscription` (round2-13) exist but nothing exposes them over HTTP —
no admin can actually start or stop a company's subscription yet.

## 2. Root cause

Never built — see round2-12 for full background.

## 3. Fix / remediation

New `backend/routes/corporate_subscriptions.py`, mounted at the same
`/admin/corporate-accounts` prefix and under the same
`require_module("corporate_accounts")` gate as `routes/corporate_wallet.py`
(matching this domain's existing convention exactly, not a new
`routes/admin/` file — verified via grep that money-moving corporate admin
endpoints already live at this prefix, e.g. `/wallet/adjust`):

- `GET /subscription-plans` — list active plans.
- `GET /{company_id}/subscription` — current + full history for a company.
- `POST /{company_id}/subscription` — assign a plan. **Gated behind a new
  `corporate_subscription_billing_enabled` app_settings flag, default
  unset/false (ships dark)** per CLAUDE.md's pre-merge gate #3 ("feature-
  flag anything user-visible and non-trivial... ship dark, verify in
  staging, then flip on") — this starts a real recurring Stripe charge, the
  single highest-risk action in this entire build, and cannot be verified
  against live Stripe in this session.
- `POST /{company_id}/subscription/cancel` — cancel (`at_period_end`
  default `true`). **Deliberately NOT gated behind the flag** — an admin
  must always be able to stop an existing live charge regardless of
  rollout state; only starting new ones is held back.
- `CorporateSubscriptionError` (from the service layer) is mapped to the
  correct HTTP status per failure reason (404/409/422/503) via a small
  lookup table, rather than a blanket 400 — a caller (the admin dashboard)
  can distinguish "plan doesn't exist" from "already has a subscription"
  from "no card on file."
- The route stays deliberately thin: no business logic, no Stripe calls,
  no audit logging here — all of that already lives in and is already
  tested from round2-13's service layer. This route only translates HTTP
  in/out.
- New `corporate_subscription_billing_enabled: Optional[bool]` field on
  `SettingsUpdateRequest` (`routes/admin/settings.py`), following item
  #51's exact precedent (`corporate_wallet_admin_adjust_daily_cap`) for
  adding an `app_settings`-backed control without a migration.
- Mounted at both existing include points in `server.py`
  (`v1_api_router` and the `/api`-prefixed legacy mount), matching
  `corporate_wallet_router`'s two-mount pattern exactly.

## 4. Risk & impact on existing functionality

- **Blast radius: one new route file + two new import/include lines in
  server.py + one new optional field in settings.py.** No existing route,
  endpoint, or settings field was modified.
- Grepped `server.py` for every other router mounted at this exact
  prefix (`corporate_accounts_router`, `corporate_wallet_router`) to
  confirm the new router doesn't collide on any path — no shared path
  segments (`/subscription-plans`, `/{company_id}/subscription`,
  `/{company_id}/subscription/cancel` vs. the existing `/{company_id}/wallet`,
  `/{company_id}/kyb-*` — all distinct).
- Because `assign_subscription` ships dark (flag default false), **no admin
  can start a real Stripe charge through this route until the flag is
  explicitly turned on** — the highest-consequence action in this build is
  the one held back for staged verification, per the pre-merge gate rules.
- `get_admin_user` (base admin gate, any admin with the `corporate_accounts`
  module grant) is used, not a stricter role — this matches every other
  endpoint in `corporate_wallet.py` including its own money-moving
  `/wallet/adjust`, so this is consistency with an established precedent
  in this exact domain, not a new judgment call.

## 5. User-experience effect

**Internal admin-facing only, and inert until the flag is flipped.** No
rider, driver, or corporate-admin-facing change. Once `corporate_subscription_billing_enabled`
is turned on in staging/production, a Spinr admin gains the ability to
start/view/cancel a company's flat-fee subscription — no dashboard UI
exists yet to drive this (follow-up commit).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/corporate_subscriptions.py` | New file: list/get/assign/cancel endpoints | Expose the round2-13 service over HTTP |
| `backend/server.py` | Import + 2 `include_router` calls for the new router | Mount it alongside the existing corporate admin routers |
| `backend/routes/admin/settings.py` | New `corporate_subscription_billing_enabled` field | Ships-dark flag gating new subscription creation |

## 7. Rollback plan

`git revert` the commit — removes the route entirely; nothing else
references it yet (no UI, no other route). The flag itself defaults to
unset/false, so even without a revert, leaving it off is a complete kill
switch for the one state-changing action (`assign`) that matters.

## 8. Verification performed

- [x] `ast.parse` syntax check on all three modified/new files — clean.
- [x] Confirmed via grep that the new router's paths don't collide with
      any existing route at the same `/admin/corporate-accounts` prefix.
- [x] Confirmed the `require_module("corporate_accounts")` mount pattern
      and `get_admin_user` dependency choice both match the existing
      `corporate_wallet_router` precedent exactly, rather than inventing
      a new access-control scheme for this domain.
- [x] Manually traced the `CorporateSubscriptionError` → HTTP status
      mapping against every `raise CorporateSubscriptionError(...)` call
      site in `corporate_subscription_service.py` (round2-13) — all 7
      reason strings covered.
- [x] Did **not** run `pytest` for this file — per this round's explicit
      "don't run tests until everything is developed" instruction;
      access-control + happy-path tests land in the very next commit
      (round2-16).

## 9. Sign-off

- [x] Rollback plan is concrete — `git revert`, plus the flag itself is
      an independent kill switch
- [x] Blast radius is stated, not assumed — confirmed via grep for path
      collisions and precedent-matching, not guessed
- [x] No silent behavior change to a working flow — new, additive route;
      nothing existing calls or depends on it
- [x] Feature-flagged per CLAUDE.md pre-merge gate #3 — the one
      real-money action (assign) ships dark by default

## What was NOT verified

Did not run `pytest`, did not start the server, and did not exercise this
route against a live app or real Stripe test-mode account — no live
Stripe calls or running server are possible in this session. Access
control (module gate + flag gate) and the happy/error paths are verified
by a dedicated HTTP-level test suite in the very next commit, but that
suite itself is also unrun this round per the standing instruction — all
of it will be exercised together in the single end-of-round `pytest` pass.
