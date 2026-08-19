# Decision write-ups — 2026-08-19

Drafted memos for every item still open in `docs/audit/2026-08-18-full-fleet-whole-app-audit.md`'s
ranked-blocker register (#13) and decision log, after the ranked register's other 29 rows were
resolved and merged (see the register itself for the fix trail). Each memo is grounded in a
dedicated read-only research pass over the actual code (file:line citations below) — no memo here
changes any code; each is a recommendation for the named human owner to approve, amend, or reject.

None of these were resolved unilaterally. Two (#13, the emergency-contact encryption question, and
the surge-cap question) are explicitly gated behind business/legal review per CLAUDE.md's own rules;
the rest are engineering-judgment calls that still cross a live-tested surface (admin RBAC, money
paths, migrations) and are being surfaced for a decision rather than shipped silently.

---

## 1. Ranked blocker #13 — Emergency contacts stored plaintext

**Owner:** Privacy/Legal · **Due:** 2026-08-25

### Context

`emergency_contacts` (`backend/migrations/08_complete_schema.sql:328-336`, re-affirmed
`120_ensure_emergency_contacts_and_gps_column.sql:22-34`) stores `name`, `phone`, `relationship` as
plain `TEXT` — no column-level encryption. RLS is owner-scoped (`SELECT`/`INSERT`/`DELETE` on
`auth.uid()::text = user_id`) but that only restricts *application-level* row access; DB/backup
access sees plaintext. Read by the SOS flow (`backend/routes/rides/safety.py:315`, SMS-blasts name +
location link to up to 5 contacts) and by the rider's own contact-management screen
(`backend/routes/users.py:768-835`). No admin endpoint touches this table.

Compare to `drivers.license_number`/`vehicle_vin`, which **are** encrypted today via pgsodium +
Supabase Vault (`backend/migrations/32_encrypt_sensitive_fields.sql:38-90`) — an
`encrypt_driver_pii()`/`decrypt_driver_pii()` RPC pair, `service_role`-only, with `REVOKE SELECT` on
the raw columns from `anon`/`authenticated`. This is the repo's actual working pattern; there is no
`pgcrypto` extension enabled anywhere in this repo despite the decision log's phrasing — the
existing precedent to reuse is pgsodium+Vault, not literal `pgcrypto`.

`backend/utils/crypto.py` is OTP-hashing only (one-way HMAC) and cannot serve a "show the rider
their own contact back" reversible-encryption need as-is.

### Options

**A — Encrypt at rest (mirror the migration-32 pgsodium/Vault pattern for `name`+`phone`)**
- New migration: ciphertext columns via pgsodium/Vault, backfill existing rows in a batched dual-read
  window (per `backend/migrations/CLAUDE.md`'s no-long-blocking-`ALTER` rule).
- Touch ~3 call sites: `routes/users.py` GET/POST/DELETE, `routes/rides/safety.py:315`'s SOS read.
- SOS is safety-critical — any added RPC latency/failure mode on the decrypt-before-SMS path needs
  the same "never silently drop an SOS" discipline as the rest of `domain-safety.md`.
- Sized **M** (one proven template to copy, not a new mechanism) — realistically 1-2 migrations +
  3 call sites + tests, comparable to the F13 gap-analysis's own effort classification for adjacent
  safety-toolkit work.

**B — Accept plaintext with compensating controls, documented**
- RLS is already shipped (migration 120) — this option is nearly free.
- Would still want DB/backup access logging on this table, which doesn't exist today (small add).
- Does **not** close the PIPEDA exposure: the actual field content (a third party's name and phone
  number, collected without that third party's own consent) stays readable by anyone with
  DB/backup-level access, unlike the hashed-at-rest OTP or vault-encrypted driver PII.

### Recommendation

**Lean A**, on the specific grounds that a working, already-proven-in-production template exists
(migration 32) — this isn't "invent pgcrypto from nothing," it's "copy what already works for
`drivers`." But this is explicitly a Privacy/Legal call per the decision log's own framing (data
content, not implementation mechanism, is the actual question), and there's a real second gap
worth folding into the same decision: `emergency_contacts` also has **no consent/OTP handshake**
before a rider's SOS event SMS-blasts a third party's phone (tracked separately as gap-analysis
finding F13, effort M, PIPEDA-motivated) — encrypting storage without addressing that the contact
never consented to receiving these texts in the first place only closes half of the actual PIPEDA
exposure on this table. Recommend Privacy/Legal decide both together rather than resolving storage
encryption in isolation.

**What NOT to do:** don't introduce a second, inconsistent encryption mechanism (raw `pgcrypto`) when
pgsodium/Vault is already the proven, running pattern for the same class of problem in `drivers`.

---

## 2. `compliance` admin module: add to `AVAILABLE_MODULES` or switch to `require_super_admin`

**Owner:** Eng lead / Admin owner · **Due:** 2026-08-29 (carried from 08-15)

### Context

`routes/admin/__init__.py:249` gates the whole `compliance_router`
(`/api/admin/compliance/*` — GST/PST remittance, SGI insurance billing, T4A filer handoff, airport
report) behind `require_module("compliance")`. But `"compliance"` is **absent** from
`AVAILABLE_MODULES` (`routes/admin/staff.py:48-67`) and from every `ROLE_PRESETS` entry — meaning no
non-super-admin can ever be granted it, because the staff create/update handlers filter submitted
modules against `AVAILABLE_MODULES` before persisting (`staff.py:169-172`). This was a **deliberate**
2026-08-14 removal from the frontend's `ALL_MODULES` picker
(`admin-dashboard/src/app/dashboard/staff/page.tsx:34-40`), with an explicit comment flagging it as
an unresolved product decision, not an oversight.

**Confirmed not urgent from a security angle**: `require_module` auto-passes `super_admin`
(`dependencies/__init__.py:680-681`), so this is a **lockout** (nobody but super_admin can ever reach
it), not a **leak** (no unintended admin can reach it). No emergency here — this is a functional gap
sitting exactly where the code's own comments left it.

### Options

**A — Add `"compliance"` to `AVAILABLE_MODULES` + an appropriate preset (e.g. `finance`)**
- Restores the frontend picker entry, lets product delegate GST/PST/T4A/insurance-billing reporting
  to a non-super role.
- Requires deciding *which* preset(s) should include it — likely `finance` per the existing
  `ROLE_PRESETS` shape (`staff.py:87-99`), but that's itself a scoping call: compliance reports touch
  regulatory/tax surfaces, arguably narrower than general `finance` access.

**B — Replace the gate with `Depends(require_super_admin)`, make the restriction explicit**
- Matches the pattern already used for Data Transfer/Bulk Operations/Export Approvals and
  `ai-console` (`sidebar.tsx:150-158`) — i.e. there's existing precedent in this codebase for
  "some admin surfaces are intentionally super-admin-only, stated plainly."
- Zero functional change from today's actual behavior — just removes the dead
  `require_module("compliance")` call that currently reads as "grantable" when it never has been,
  and updates `sidebar.tsx`/`records/page.tsx`'s client-side module checks to match.

### Recommendation

This is a scoping question only product/eng-lead can answer: **does any non-super-admin role need
GST/PST/T4A/insurance-billing report access?** If yes → Option A (with the preset scoping decided
alongside). If no → Option B, which is the lower-risk default (codifies the status quo explicitly,
removes a confusing "looks grantable, isn't" dead code path) and can ship today with no further
decision needed. Recommend **B as the default** unless product actively wants to delegate this,
since it requires zero new access-scoping judgment calls to get right.

---

## 3. Inert `surge`/`pricing` grantable module strings — retire or wire up

**Owner:** Eng lead / Admin owner · **Due:** 2026-08-29 (carried from 08-15)

### Context

Both `"surge"` and `"pricing"` are present and grantable in `AVAILABLE_MODULES`
(`staff.py:55,58`) and included in the `operations`/`finance` presets respectively
(`staff.py:93,98`) — but **no route anywhere in the backend checks either string**
(confirmed via full grep of every `require_module(...)` call site across
`routes/admin/*`). The actual surge/pricing admin capability
(`PUT /service-areas/{area_id}/surge`, `GET /surge/status`, general service-area
`PUT` with surge fields) is entirely gated by `require_module("service_areas")`
(`routes/admin/__init__.py:150`).

Net effect today, both directions:
- An admin granted `"surge"`/`"pricing"` **without** `"service_areas"` believes they have surge
  control (checkbox is on) but every call 403s — false sense of *granted* access.
- An admin **denied** `"surge"`/`"pricing"` but holding `"service_areas"` has full surge/pricing
  control anyway — false sense of *restricted* access.

This is the exact same class of bug already remediated once for a `"heatmap"` module string
(precedent documented at `staff.py:70-85`) — that one was retired rather than wired up.

### Options

**A — Retire both strings** (remove from `AVAILABLE_MODULES`, `ROLE_PRESETS`, frontend
`ALL_MODULES`, update `test_admin_module_list_parity.py`) — matches the `"heatmap"` precedent
exactly. Any admin who currently holds `"surge"`/`"pricing"` on their `modules` array loses a
no-op grant (they never had real capability from it); nothing regresses functionally, since the
real gate (`"service_areas"`) is untouched.

**B — Wire them up**: split `service_areas_router`'s surge/pricing sub-routes onto their own
`require_module("surge")`/`require_module("pricing")` gates, separate from the broader
`service_areas` grant — lets an org grant surge-only or pricing-only access without full
service-area edit rights.

### Recommendation

**A**, following the already-established `"heatmap"` precedent directly — same bug shape, same
fix, and the codebase already has the test (`test_admin_module_list_parity.py`) and the removal
pattern ready to reuse. Option B is a legitimate finer-grained-permissions feature, but nothing in
the audit or this research found a stated business need for surge-only/pricing-only admin scoping
distinct from `service_areas` — building it now would be solving a problem nobody's asked for. If
that need surfaces later, B can be revisited as a fresh feature request, not bundled into this
cleanup.

---

## 4. `payments.py` coverage: 86.1% measured vs. 90% documented target

**Owner:** Eng lead / QA owner · **Due:** 2026-09-05

### Context

Freshly re-measured (2026-08-19, this session): `backend/routes/payments.py` — 447 statements, 62
missed, **86.13%** covered. The `money-path-coverage-floor-gate` CI job (merged PR #4267) enforces
only a *floor* of `measured - 5` rounded down (80%) to stop further erosion — it does not represent
the CLAUDE.md-documented 90% target and was explicitly designed not to be read as "coverage is fine
now" (see `docs/change-log/2026-08-19-money-path-coverage-floor-gate-fix.md`).

The 62 uncovered lines, classified against a full read of every range (a second, independent
line-by-line pass over the same file confirms this breakdown and adds detail beyond the summary
below):

- **Lines 11-25** — the `except ImportError:` half of the repo's intentional dual-import pattern.
  Structurally untestable in a single test process that always takes one branch — **not a real
  gap**, ~15 of the 62 lines.
- **Lines 159-241, 288, 351-354, 378-407** — `_reprovision_stripe_customer`,
  `sync_stripe_customer_email`, and the `with_customer_repair` retry branch (Stripe test↔live
  mode-drift repair + best-effort email sync). **Already covered indirectly** by
  `test_stripe_customer_email_mapping.py`'s dedicated suite — just not counted when coverage is run
  scoped to only the payments-tagged test files. Not a real gap in the full-suite number; a
  measurement artifact when scoped narrowly.
- **Lines 601-606** — the mock-payment-intent-rejected-in-production safety guard inside
  `confirm_payment` — **genuine gap**, worth closing: a production-safety rail (rejects
  `pi_mock_*` intents outside dev/review).
- **Lines 631-636** — the C-3 idempotency early-return in `confirm_payment` (already-settled ride
  short-circuits to `{"status": "already_processed"}`) — **genuine gap**, worth closing: the exact
  Stripe-idempotency guard CLAUDE.md's conventions call out as a must-test surface. Gates whether a
  ride gets marked `paid` without re-running the underpayment check — a correctness/state-machine
  risk (double-settle or free-ride potential), not a Decimal-precision one.
- **Lines 473, 506, 565-573** — `create_payment_intent`'s ride-id-only idempotency-key branch, the
  3DS `requires_action` 402 shape, and the generic `StripeError`/catch-all `Exception` handlers
  (only `CardError`/`RateLimitError` are currently tested) — **genuine gaps**, each small (1-8
  lines).
- **Lines 663, 682, 687** — `confirm_payment`'s ownership-mismatch 403s (mock-path ride mismatch,
  non-mock intent-metadata `user_id` mismatch, non-mock ride ownership) — **genuine gaps**,
  security-relevant (unauthorized settlement-confirmation attempts).
- **Lines 1090-1091, 1130, 1150, 1222, 1325** — `add_card`'s generic `StripeError` handler, and
  `set_default_card`/`delete_card`'s card-not-found 404 ownership checks (WS-18) — **genuine gaps**,
  security-relevant (cross-account card manipulation guards).
- **Lines 785, 834, 907, 1095, 1139, 1358** — bare `raise` re-propagation inside
  `except HTTPException:` blocks — defensive/trivial pass-through control flow, low value to chase
  in isolation (each already gets exercised transitively once the 4xx-raising branch above it in
  the same function is covered).

**Confirmed: zero uncovered lines touch money arithmetic.** The actual dollar-computation code
(`_q2`, `_authoritative_ride_charge`, `dollars_to_cents` calls, the underpayment-guard comparison
of `owed_cents` vs. `intent.amount_received`) is fully covered. The highest-priority true gaps are
correctness/idempotency/ownership branches, not Decimal-precision risk.

### Options

**A — Write tests to close the gap to 90%+**
- Roughly **8-10 targeted unit tests** close essentially all genuine (non-measurement-artifact,
  non-defensive) gaps, prioritized: (1) `confirm_payment`'s production mock-guard + C-3 idempotency
  early-return — 2 tests, highest priority per CLAUDE.md's idempotency convention; (2)
  `confirm_payment`'s three ownership-mismatch 403s — 2-3 tests, security-relevant; (3)
  `create_payment_intent`'s StripeError/Exception handlers — 2 tests; (4)
  `set_default_card`/`delete_card`/`add_card` card-ownership 404s and StripeError handler — 3
  tests. A smaller subset (~5-6, focused on 1-2) is likely enough to cross the 90% line on its own.
- Separately, fix the coverage *measurement*: confirm the reprovision/email-sync lines
  (159-241, 288, 351-354, 378-407 — the single largest chunk of the "missing" total) are actually
  counted when the full suite runs `test_stripe_customer_email_mapping.py` alongside this file,
  rather than assuming they need new tests — some of the apparent 86.1% may already be closer to
  correct once that's confirmed, separate from the ~15-line import-fallback exclusion.
- Estimated size: **S** — the true remaining gap is real but narrow; none of it requires new
  money-arithmetic test scaffolding, just Stripe-error-branch and ownership/idempotency-branch
  coverage.

**B — Revise the 90% target for this file**
- Only defensible if the target itself is wrong for this file's actual risk profile — it isn't;
  `payments.py` is squarely in CLAUDE.md's ≥90% tier alongside `fare_service.py`/`crypto.py`
  precisely because it's a money-arithmetic surface. Lowering the target here would be adjusting
  the goalpost to match the miss rather than closing it.

### Recommendation

**A.** The gap is small (86.1% → 90% is ~15-20 lines of genuine coverage, most of it 2 concrete,
well-scoped test cases: the mock-payment-production-guard and the C-3 idempotency short-circuit),
and both flagged real gaps are exactly the kind of branch CLAUDE.md's own testing conventions
prioritize (idempotency, production-safety guards). Recommend excluding the dual-import fallback
block from the coverage denominator (if not already handled via `# pragma: no cover` elsewhere in
this codebase) rather than writing a dead-branch test just to inflate the percentage — that would
be coverage theater, not real risk reduction.

---

## 5. Migration numbering collisions: accept as convention or invest in a merge-queue / stronger CI check

**Owner:** Eng lead · **Due:** 2026-08-29

### Context

The decision log's own count ("7 duplicate prefixes, up from 3") **significantly understates**
reality — `ls backend/migrations | sort -V` shows **64 distinct prefixes with 2+ files today**, some
with 3-4 (`51`, `307` have 4 each). `migration-check.yml`'s CHECK B (hard-fail since CR #4187,
`.github/workflows/migration-check.yml:146-172`) only catches a collision **visible within one PR's
own diff against its merge-base** — it structurally cannot see a true cross-PR race where two
branches both fork before either merges (each sees the slot as free). `run_migrations.py` keys
idempotency by full filename (not prefix), so both files in a collision *do* apply — but a real
2026-04-28 production incident (`docs/runbooks/migration-conflict-detection.md:5-11,142-160`)
already occurred from exactly this shape: two same-prefix migrations where the alphabetically-later
file's `CREATE OR REPLACE FUNCTION` silently overwrote the first's body, disabling the PIPEDA/SK
retention purge for ~45 minutes. No GitHub-native merge queue is enabled on this repo
(no `merge_group:` trigger anywhere in `.github/workflows/`).

Given the number of concurrent Claude sessions visibly operating on this repo (25+ worktree
directories found under `.claude/worktrees/agent-*`, each computing "next free slot" from its own
stale local view), this is a **structurally frequent** friction point, not a rare edge case — this
session alone hit it 3 times and had to renumber.

### Options

**A — Accept as documented convention (status quo)**
- Zero engineering cost, but leaves the CR #4187-documented residual gap open indefinitely, and
  the `CREATE OR REPLACE` semantic-collision risk (the actual production-incident cause)
  **completely unaddressed** by any existing check.

**B — Enable GitHub's native merge queue** (`on: merge_group` trigger + repo setting)
- Serializes merges to `main`, so CI (including `migration-check.yml`) runs against the *actual*
  would-be post-merge state instead of each PR's stale fork point — closes the CR #4187 cross-PR
  race gap structurally, no new tooling required (a repo setting + a workflow trigger addition).
- Does **not** by itself catch the `CREATE OR REPLACE` semantic collision (two colliding-prefix
  migrations that both modify the same object differently) — that's a separate, harder problem.

**C — Build a post-merge/push-to-main check** (already scoped in `ACTION_ITEMS.md` C36, previously
deferred "unless option (a) alone proves insufficient in practice")
- Re-validates prefix uniqueness against the *full* directory after every merge to `main`, catching
  the true cross-PR race without requiring GitHub's merge queue feature.
- New tooling (a new `push`-triggered workflow), more implementation surface than B.

### Recommendation

**B**, and treat "option (a) proves insufficient" (the condition ACTION_ITEMS.md set for building C)
as **already met** — the evidence above (64 live collisions, a real production incident, 3
renumbers in this session alone) is exactly the "proves insufficient in practice" signal that
deferred decision was waiting on. B is also the lower-effort path (settings + trigger, no new
script) and directly closes the specific gap CR #4187 left open. Separately and regardless of A/B/C,
recommend flagging the `CREATE OR REPLACE` semantic-collision detector (`docs/runbooks/
migration-conflict-detection.md:133-138`'s "future enhancement... tracked separately, not yet
built") as its own follow-up — it's the mechanism that actually caused the one real incident on
record, and neither B nor C touches it.

---

## 6. Corporate payment-source cascade: correct the doc, or build the literal cascade

**Owner:** Product/Eng lead · **Due:** 2026-08-29

### Context

`.claude/context/domain-payments.md:107-110` states flatly: "Payment source priority: rider wallet
→ corporate allowance → master wallet → rider card" and "if a ride exceeds cap, fall through to next
payment source, not over-spend" — read naturally, this describes a single-transaction, four-source
automatic fallback.

The actual code (`backend/routes/rides/payments.py:558-577` and mirrored dispatch logic in
`cancellation.py:247-295,613-636`, `drivers/ride_cancel.py:368-369`) makes **one upfront choice per
ride** — `ride.payment_method ∈ {"card", "wallet", "company_allowance"}`, set at booking time — and
settlement dispatches on that single field with **no cross-method fallback**. If `settle_wallet`
hits insufficient funds, it returns `success=False` and marks the ride `payment_status="pending"` —
it does not query allowance, master wallet, or card. The **only** real in-transaction cascade that
exists is **allowance → master wallet**, and it's scoped strictly inside rides already tagged
`payment_method="company_allowance"` (`services/payment_service.py:1019-1133`); if master is also
exhausted, that path fails hard (503, ride left pending) rather than reaching the rider's card —
contradicting the doc's "→ rider card" tail entirely.

Concrete divergence: a corporate rider with `payment_method="wallet"` selected and an $8 wallet
balance against a $15 fare gets a failed/pending ride today, full stop — not the doc's implied
automatic recovery through allowance → master → card.

### Options

**A — Correct the doc** to describe payment-method *selection* (upfront, rider/company-policy
determined) plus the narrower allowance→master-wallet fallback that genuinely exists only for
`company_allowance` rides.
- Effort: trivial, doc-only.
- Risk: none — this is describing what already ships.

**B — Build the literal four-source cascade the doc currently claims**
- This is a real, non-trivial new feature (retry settlement across payment methods within one ride,
  new state handling for partial-cascade failures, new UX for "your ride settled via X after Y
  failed"), not a bug fix.
- Not something to build opportunistically off a doc-correction task — needs its own product
  scoping (does the business actually want automatic cross-method fallback? what does a rider see
  mid-cascade? how does this interact with corporate policy's `allowed_payment_source` gate?).

### Recommendation

**A now, B only if product explicitly wants it as a new feature.** The doc mismatch is not
cosmetic — an engineer reading only the doc could reasonably build a support tool or a new flow
assuming automatic recovery that doesn't exist — so it should be corrected promptly regardless of
whether B is ever pursued. B is a legitimate feature idea (guaranteed-settlement UX is generally
good for riders) but is out of scope for a documentation-accuracy fix and should go through normal
product scoping if wanted.

---

## 7. Admin surge override >2.5x accepted (1.0-10.0) but always clamped to 2.5x at fare-calc

**Owner:** Product/Legal · **Due:** 2026-08-29

### Context

`PUT /api/admin/service-areas/{area_id}` accepts `surge_multiplier` 1.0-10.0
(`routes/admin/service_areas.py:203`) and **enforces** (not just UI-convention) a
`surge_justification` requirement for any value >2.5 (`:639-667`), audit-logging a
`surge_override_above_cap` entry on success. The value is stored **verbatim** in
`service_areas.surge_multiplier` — no clamp at write time.

But **every** fare-calc read site clamps to `SURGE_CAP = 2.5` before the value ever reaches a rider:
`fare_service.py:494-497`, `routes/fares.py:241`, `features.py:844-845` — confirmed exhaustively, no
gap, no bypass. An admin submitting 4.0x with justification gets it stored, audit-logged, and
visible in admin surge-status/history — but it **never** multiplies a rider's actual fare above
2.5x. The code's own comment (`service_areas.py:630-638`) states this is deliberate: the wide input
range exists to let an admin **record and justify** an above-cap decision for audit purposes,
without that record automatically becoming a live rider charge. A sibling, currently-dead endpoint
(`PUT .../surge`, not called by any frontend) is hard-capped at 2.5 by Pydantic itself, with no
justification gate needed — consistent with this being an intentional two-tier design rather than
an oversight to unify.

### Options

**A — Tighten input validation to 1.0-2.5, remove the >2.5 acceptance entirely**
- Simplifies the code (removes the justification branch, the audit-log special case), matches the
  dead sibling endpoint.
- **Destroys the audit/record-keeping capability** if that's actually wanted (e.g., "we considered
  and rejected a 4.0x for event X, here's the documented decision trail") — need to confirm this
  isn't in active use before removing it.

**B — Wire admin-approved >2.5x through to fare-calc**
- Explicitly the higher-risk option per CLAUDE.md's own guardrail ("Never suggest raising it without
  explicit business + legal review," "Not a surge-first product... Never introduce 'dynamic
  pricing' that behaves like unbounded surge"). Also a likely SK Transportation Act / municipal
  fare-cap regulatory risk, since 2.5x is already described as *tighter than* the provincial cap.
  Not something to build without the business+legal review CLAUDE.md itself calls for.

**C — No code change; clarify CLAUDE.md's SLA/surge section to state explicitly that the >2.5
override is record/audit-only and inert at the fare layer today**
- Zero risk, zero behavior change — closes the actual gap here, which is documentation clarity
  (CLAUDE.md's current wording is technically accurate but doesn't say the override never reaches a
  rider), not a code defect.

### Recommendation

**C**, with A as a fallback if product/legal confirms the audit-trail use case isn't actually
needed. This is not a bug — every fare-calc site correctly and consistently enforces the 2.5x cap,
confirmed with no gaps — so B should not be pursued absent an explicit business+legal decision to
loosen the cap itself (a much bigger decision than this write-up scope), and A risks losing a
capability that may be intentionally in use for audit/record purposes without first confirming
that. Recommend closing this purely as a documentation clarification unless Product/Legal has a
specific reason to want A or B.

---

## 8. Fare-estimate 3.5s Directions-wait: accept as permanent SLA exception, or add a hard ceiling

**Owner:** Product/Eng lead · **Due:** 2026-08-25

### Context

Already documented in CLAUDE.md (this session, ranked blocker #24) — `_PRICING_ROUTE_WAIT_S =
DIRECTIONS_TIMEOUT_S + 0.5` = 3.5s worst case in `backend/routes/rides/estimates.py`, widened from
1.5s after a 2026-07-29 incident where a too-short wait let a trip re-price between quote and
confirm ($30.92 → $39.44 on the same trip, 12.12km → 16.46km measured distance). This decision item
is the still-open half: was the 3.5s wait the right permanent trade-off, or should there be a hard
latency ceiling instead (accepting the undercharge-risk tail that motivated widening it in the first
place)?

### Options

**A — Accept 3.5s as a permanent, documented SLA exception (status quo, now documented)**
- Already effectively in place; this option is "make no further change."
- Keeps the anti-undercharge protection the 2026-07-29 incident showed was necessary.
- Rider-facing cost: up to 3.5s wait on fare estimate in the worst case (Directions API slow/timeout),
  vs. the 300ms P95 target for every other estimate path.

**B — Add a hard latency ceiling below 3.5s, accept the undercharge-risk tail it reintroduces**
- Improves P95 responsiveness for the tail of requests where Directions is slow.
- Reopens exactly the risk the 07-29 incident demonstrated: haversine-based pricing when a road
  route was actually available undercharges the rider, and the driver keeps 100% of that
  undercharged fare (Spinr's 0%-commission model means this isn't recoverable via a platform cut
  later) — a real revenue/driver-fairness cost, not just a rounding nuisance.

### Recommendation

No new research changes the calculus here beyond what's already in CLAUDE.md's own note — this
write-up doesn't have new information to add over what the code comment and the 07-29 incident
already state. Restating for the decision owner: **A is the safer default** given the incident that
motivated the current wait is concrete and already occurred once; B would need a defined ceiling
value and an explicit acceptance of the resulting undercharge tail before shipping, which is a
Product call on risk tolerance, not an engineering one. No action recommended without Product/Eng
lead's explicit sign-off either way — that's the actual ask of this decision item.

---

## 9. Confirm whether the silently-failed `purge_pii_retention()` job ever produced a loud alert

**Owner:** Eng — Observability · **Due:** 2026-08-22

### Context

Post-fix (migrations 323/324, C22), `retention_purge.py`'s failure path does `logger.exception`/
`logger.error` only — **no Sentry capture anywhere in the file** (confirmed via grep). The outer
loop's `_metric_inc(...)` call is a documented **no-op stub**
(`retention_purge.py:271-272`, "wire to real metrics system when Prometheus/OpenMetrics lands").
Critically, `_record_heartbeat("retention_purge (24h)")` fires **unconditionally after the
try/except** (line 312) — so a failing tick still refreshes the loop watchdog's heartbeat. The
watchdog (`core/lifespan.py:609-680` → `loop_alert.py` → `loop_monitor.py`) only detects a loop
**stopping entirely**, not an individual run failing while ticking on schedule — exactly the failure
mode that let the original bug go undetected. The sibling loop added the same day,
`retention_guard_monitor.py`, **does** do a CRITICAL log + Sentry `fatal` capture + an `audit_logs`
row on failure — `purge_pii_retention()` has no equivalent. The C22 change-log doc
(`docs/change-log/2026-08-17-c22-purge-pii-retention-broken-and-fixed.md:26,107`) already
self-diagnoses this exact gap and leaves it as an explicit open follow-up.

### Bottom line (this is the "confirm" the decision item asks for)

**Confirmed: nobody gets paged.** If tonight's run fails again, the only trace is one ERROR-level
log line an engineer would have to go looking for — no Sentry event, no working metric, and the
watchdog reports healthy because the heartbeat still fires. This is not a hypothetical; it's the
exact mechanism that let the original Step D/Step F bugs run undetected before the 08-17
investigation found them via an unrelated migration-tracking audit.

### Recommendation (not a decision item requiring business judgment — this is a straightforward fix)

Mirror the `retention_guard_monitor.py` pattern already sitting right next to it: on failure, add a
Sentry `fatal`-level capture tagged `domain="admin"` (per that sibling's own precedent — "closest
fit, this is a database-security/regulatory-posture control, not a product domain") alongside the
existing `logger.exception`, and move `_record_heartbeat(...)` inside the success path only (or add
a distinct failure-aware heartbeat) so the watchdog can actually detect a stuck-but-still-ticking
failure mode. This is a small, well-scoped observability fix with an exact template to copy — should
be picked up as a normal engineering task rather than left open as a "decision," but flagged here
since it was in the decision log as posed.

---

## Summary table

| # | Item | Recommendation | Needs |
|---|---|---|---|
| 13 | Emergency-contact plaintext | Lean encrypt (mirror migration-32 pattern), decide together with the unaddressed SOS-consent gap (F13) | Privacy/Legal decision |
| — | `compliance` module | Default to `require_super_admin` (Option B) unless product wants delegation | Eng lead confirms |
| — | `surge`/`pricing` inert modules | Retire both, matching the `"heatmap"` precedent | Eng lead approves |
| — | `payments.py` coverage 86.1%→90% | Write 2-4 targeted tests (mock-payment guard, C-3 idempotency); exclude dual-import fallback from denominator | Eng/QA picks up as normal work |
| — | Migration numbering collisions | Enable GitHub merge queue (closes CR #4187 gap); separately track the `CREATE OR REPLACE` semantic-collision detector | Eng lead approves + implements |
| — | Corporate payment cascade doc | Correct `domain-payments.md` now; treat literal cascade as a separate feature ask | Doc fix ships now; B needs product scoping if wanted |
| — | Surge override >2.5x inert | Clarify CLAUDE.md wording (record-only, inert at fare layer) — no code change | Low-risk, can ship now |
| 24/N9 | Fare-estimate 3.5s wait | No new info beyond what's documented; A (accept) is the safer default given the 07-29 incident | Product/Eng lead sign-off |
| — | `purge_pii_retention` silent failure | Straightforward fix: mirror `retention_guard_monitor.py`'s Sentry+heartbeat pattern | Not a decision — normal eng task |
