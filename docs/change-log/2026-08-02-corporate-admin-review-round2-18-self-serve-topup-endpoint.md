# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | corporate, payments |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Corporate + admin portal review, round 2 — "corporate wallet funding is Spinr-admin-only, no self-serve path" (business decision: self-serve top-up via Stripe) |

## 1. Issue / gap identified

Company admins have no way to fund their own wallet — the only top-up
path is `routes/corporate_wallet.py::manual_topup`, gated behind
Spinr-internal-admin auth (`get_admin_user` + `require_module`). A
company that wants to add funds must contact Spinr support.

## 2. Root cause

Never built — only the internal-admin-initiated path existed.

## 3. Fix / remediation

New `POST /company/{company_id}/wallet/topup` in `routes/corporate_company.py`
(the existing company-admin-facing router, auth via `require_company_admin`
— a rider-session JWT + active owner/admin membership check, NOT the
Spinr staff admin JWT). Deliberately mirrors `corporate_wallet.py::manual_topup`
almost line-for-line rather than inventing new logic:

- Same amount bounds ($100–$10,000 CAD).
- Same Stripe PaymentIntent metadata shape
  (`scope: "corporate_topup", company_id, wallet_id`) — **this is the key
  design choice**: because the existing `payment_intent.succeeded` webhook
  handler (`routes/webhooks.py`) dispatches purely on
  `metadata.scope == "corporate_topup"` with no check on who created the
  PaymentIntent, it credits the wallet identically for a self-serve charge
  with **zero webhook changes needed**. `initiated_by` carries the company
  admin's own user id (not a Spinr staff id), so `apply_topup`'s ledger
  row and the new `log_user_action` audit entry both correctly attribute
  the charge to the company, not Spinr.
- Same idempotency-key scheme (client-supplied, or a 1-minute time-bucket
  keyed on wallet + amount), same off-session confirm shape.
- One deliberate difference from the admin path: a payment method is
  **required** — either client-supplied (`body.payment_method_id`, e.g.
  from Stripe Elements at charge time — not built in this slice, see
  round2-19) or falls back to `get_default_payment_method` (the same
  helper already used for corporate auto-topup). If neither exists, a
  clear 422 rather than letting Stripe reject an incomplete PaymentIntent.
- Audited via `log_user_action` (rider/company-admin actor), not
  `log_admin_action` (Spinr staff actor) — this file's existing
  convention throughout (see every other write endpoint in this file).

## 4. Risk & impact on existing functionality

- **Blast radius: one new endpoint + import additions in one file.** No
  existing function or endpoint in `corporate_company.py` was modified —
  confirmed by diff: the only changes to existing code are import-block
  additions (`get_default_payment_method`, `stripe`/`asyncio`/`time`/
  `pydantic`), every existing route body is untouched.
- **Zero changes to `routes/webhooks.py`.** Grepped the existing
  `payment_intent.succeeded` handler (round2 background, also read for
  item #63's webhook work) to confirm it keys purely on
  `metadata.scope`/`wallet_id`, never on an admin-vs-company distinction
  — the existing `apply_topup` call path handles this new charge source
  with no code change and no new test coverage gap in that file.
- Reused, not duplicated: `get_default_payment_method`
  (`corporate_repo.py`, already used by auto-topup),
  `dollars_to_cents`/`get_app_settings`/`log_user_action` (all already
  imported in this file for other endpoints).
- Grepped every other consumer of `corporate_company.py`'s router: only
  `server.py`'s `include_router(corporate_company_router)` calls (both
  the bare and `/api`-prefixed mounts) — unaffected, no router-level
  config change needed since this file's router already carries no extra
  `dependencies=[...]` (auth is per-route via `require_company_admin`).
- **Money risk**: this is a genuinely new way real money moves (a company
  admin, not Spinr staff, can now trigger an off-session Stripe charge up
  to $10,000). Mitigated by: (a) the same amount cap already proven safe
  on the admin path, (b) `require_company_admin` restricting this to
  active owner/admin members of *that* company only (enforced by
  `list_active_memberships_for_user` filtering on `company_id` from the
  URL path), (c) the company must already be `active` status and have a
  Stripe customer + payment method on file — a suspended/closed company
  or one with no payment history cannot use this path at all.

## 5. User-experience effect

**Corporate-admin (company-side) facing, new capability.** A company
admin can now fund their own wallet directly rather than contacting Spinr
support. No existing screen or flow changes behavior — this is purely
additive; no UI wires to it yet in this commit (round2-19).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/corporate_company.py` | New `SelfServeTopUpRequest` model + `POST /wallet/topup` endpoint; import additions (`get_default_payment_method`, `stripe`, `asyncio`, `time`, pydantic) | Self-serve corporate wallet funding |

## 7. Rollback plan

`git revert` the commit. No migration, no data written by adding the
route itself — the only "written" data is real Stripe charges made
through it going forward, so the actual rollback lever is removing the
route (or, less drastically, letting the existing `manual_topup` remain
as the fallback funding path it always was).

## 8. Verification performed

- [x] `ast.parse` syntax check — clean.
- [x] Confirmed via grep that the two dual-import branches
      (`try`/`except ImportError`) both carry `get_default_payment_method`
      — this repo's formatter strips "unused" dual-import names until the
      code that uses them exists, and it did strip `stripe`/`asyncio`/
      `time`/pydantic on the first pass; re-verified they're present after
      the endpoint using them was in place.
- [x] Traced the `payment_intent.succeeded` webhook handler
      (`routes/webhooks.py`) to confirm it needs zero changes for this new
      charge source — dispatches purely on `metadata.scope`, not caller
      identity.
- [x] Compared every field of the new endpoint's `intent_kwargs` against
      `manual_topup`'s, confirming the metadata shape (`scope`,
      `company_id`, `wallet_id`) matches exactly so the webhook's existing
      `apply_topup` call path applies unmodified.
- [x] Did **not** run `pytest` for this file — per this round's explicit
      "don't run tests until everything is developed" instruction;
      dedicated tests land in the very next commit (round2-19).

## 9. Sign-off

- [x] Rollback plan is concrete — `git revert`
- [x] Blast radius is stated, not assumed — confirmed via diff that no
      existing endpoint or webhook handler was modified
- [x] No silent behavior change to a working flow — purely additive;
      `manual_topup` (the existing admin path) is untouched and remains
      available as before

## What was NOT verified

Did not run `pytest`, and did not exercise this against a live or
sandbox Stripe account — no live Stripe calls are possible in this
session, same limitation as every Stripe-touching commit this round. The
webhook-reuse claim (zero webhook changes needed) is verified by reading
the handler's dispatch logic, not by an end-to-end PaymentIntent → webhook
→ wallet-credit trace against real Stripe. Access control
(`require_company_admin` correctly scoping to the caller's own company)
is reasoned from the existing, already-used dependency — not re-verified
independently here; dedicated HTTP-level tests for this exact concern
land in round2-19.
