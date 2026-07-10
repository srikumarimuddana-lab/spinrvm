# God-file split: `routes/rides.py` and `routes/drivers.py`

**Date:** 2026-07-10
**Scope:** backend only — pure code motion, zero behaviour change intended.

## Why

The two largest backend modules had grown into god files that dominated the
dependency graph and made every dispatch/payment change a merge-conflict
magnet:

| File | Lines | Endpoints | Top-level defs |
|---|---|---|---|
| `backend/routes/rides.py` | 6,470 | 33 | ~80 |
| `backend/routes/drivers.py` | 8,776 | 67 | ~150 |

Symptoms: unrelated domains (chat, payouts, SOS, subscriptions) edited in the
same file; test patch targets all funneled through two module namespaces;
reviews routinely exceeded the diff-size guideline.

## What changed

Each file became a package of domain submodules with two infrastructure
modules and a facade:

```
backend/routes/rides/                 backend/routes/drivers/
├── __init__.py   ← facade           ├── __init__.py   ← facade
├── _deps.py      ← external imports ├── _deps.py
├── _shared.py    ← shared helpers   ├── _shared.py    (PII vault, state guard)
├── matching.py   (dispatch engine)  ├── profile.py
├── estimates.py                     ├── earnings.py
├── booking.py    (create + preauth) ├── location.py
├── queries.py                       ├── payouts.py    (Stripe Connect)
├── payments.py   (tip + settlement) ├── tax_exports.py (T4A, PIPEDA export)
├── sharing.py                       ├── ride_reads.py
├── rating.py                        ├── ride_flow.py  (accept…start)
├── cancellation.py                  ├── ride_complete.py
├── stops.py                         ├── ride_cancel.py (cancel/noshow/rate)
├── safety.py     (SOS, check-in)    ├── referrals.py
├── chat.py                          ├── subscriptions.py
├── lifecycle.py                     └── status.py     (/{driver_id} — last)
├── receipts.py
├── lost_found.py
└── tracking.py
```

The split was produced by an AST-driven tool (scope-aware name resolution via
locals tracking; relative-import levels bumped one package deeper), not by
hand-editing 15k lines. Route parity was asserted mechanically: identical
(method, path, endpoint) sets before/after, plus a literal-vs-`{param}`
shadowing check on the new registration order.

## Design rules (read before adding code here)

1. **`_deps.py` is the only place external imports live.** It carries the
   single dual-import block (`backend.*` vs top-level mode). Submodules import
   plain names from `_deps`; they never repeat the try/except block.
2. **Patch-seam convention.** Names that unit tests patch *wholesale*
   (`patch("backend.routes.rides.db_supabase", mock)`-style at the old flat
   namespace) are accessed via module attributes so patches land:
   - external deps → `_deps.db_supabase.get_ride(...)`, `_deps.send_push_notification(...)`
   - shared singletons → `_shared.dispatch`
   - cross-submodule calls → `matching.match_driver_to_ride(...)` via
     `from . import matching`
   New tests patch the owning module, e.g.
   `patch("backend.routes.rides._deps.db_supabase.get_ride", ...)` or
   `patch("backend.routes.rides.matching._dispatch_retry", ...)`.
   Deep attribute patches on shared objects (`...db_supabase.get_ride`,
   `...manager.send_personal_message`) work from any path that reaches the
   same object — the facade re-exports keep old spellings resolvable.
3. **Cross-submodule references are always attribute-style**
   (`from . import booking` + `booking._prep_and_dispatch(...)`). This is
   circular-import-safe (late binding) and keeps every function patchable at
   exactly one path.
4. **The facade (`__init__.py`) re-exports every top-level name** of the old
   module, so all external importers (`core/lifespan.py`, `routes/webhooks.py`,
   `utils/scheduled_rides.py`, `services/company_booking_service.py`, admin
   routes, AI tools, tests) keep working. Don't remove re-exports without
   checking importers; prefer importing the owning submodule in new code.
5. **Route registration order is deliberate.** Within a submodule the original
   order is preserved; across submodules the facade includes routers in a
   fixed order with catch-all parameterized routes last (`drivers/status.py`
   owns `GET /{driver_id}` / `PUT /{driver_id}/status`; in rides, the literal
   `/active`, `/history`, `/stats`, `/scheduled` GETs live in `queries.py`
   above `GET /{ride_id}`). If you add a new literal single-segment route, it
   must register before those catch-alls — keep it out of `status.py` and run
   the shadow check in `scripts/` (see PR) if unsure.
6. **State machine, money, insurance-period code moved verbatim.**
   `_require_ride_in_state()` guards, Decimal helpers (`_d`, `_round`, `_f`),
   and insurance-period transitions were not touched — only relocated
   (`rides/_shared.py`, `drivers/_shared.py`).

## External callers updated (call-time submodule access)

These modules lazily imported now-moved functions from the flat namespace and
were repointed at the owning submodule so test patches keep one deterministic
target:

- `utils/scheduled_rides.py` → `rides.booking._preauthorize_ride_card`,
  `rides.matching.match_driver_to_ride` / `ride_search_timeout`
- `services/company_booking_service.py` → `rides.booking._insert_ride_with_code`,
  `_prep_and_dispatch`
- `ai/tools_booking.py` → `rides.estimates.compute_ride_estimates`
- `routes/webhooks.py` → `drivers.subscriptions._activate_subscription`,
  `_cancel_stripe_subscription`, `_record_subscription_payment`,
  `_send_subscription_invoice_email`, `_compute_subscription_tax`
- `routes/admin/subscriptions.py` → `drivers.subscriptions._send_subscription_invoice_email`

Facade-based lazy imports that are *not* patched in tests were left as-is
(`core/lifespan.py`, `routes/admin/drivers.py` referral constants, etc.).

## Test-suite impact

~1,320 patch-target strings across `backend/tests/` were rewritten
mechanically (name → owning submodule), plus 9 `patch.object` sites. Four
patch targets referenced names that never existed on the old module
(`corporate_wallet_service`, `corporate_allowance_service`, `evaluate_policy`,
`_send_offer_to_driver`); the tests using them fail identically before and
after the split and were left untouched (tracked as pre-existing failures).

Verification: full-suite runs on the pre-split and post-split trees produce
identical pass/fail sets (see PR description for counts).

## Follow-ups (out of scope here)

- `routes/auth.py` (71 KB), `routes/webhooks.py` (86 KB) and
  `routes/websocket.py` (68 KB) are next-largest candidates for the same
  treatment; the splitter tool generalizes.
- `drivers/subscriptions.py` (~1,880 lines) is still the largest submodule —
  the Stripe checkout/verify/activate flow could split further once the
  in-flight subscription work settles.
- Several pre-existing test failures (SOS incident table mismatch, corporate
  payment patches on never-existing names) predate this refactor and belong to
  the current sprint's P0 list.
