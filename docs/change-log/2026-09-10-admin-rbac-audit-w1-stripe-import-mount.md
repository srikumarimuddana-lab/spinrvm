# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-10 |
| Author | Claude Code (admin portal security/RBAC audit, requested by user) |
| Surface(s) | backend |
| Domain (Sentry tag) | admin |
| Related issue or gap ID | `docs/audit/2026-09-10-admin-portal-security-rbac-audit.md`, finding W1 |

## 1. Issue / gap identified

`stripe_import_router` was mounted with `require_module("drivers")`, but
every one of its 5 handlers independently requires `super_admin` per-handler
(the file's own docstring: "module grants are not enough... the super_admin
check is the gate"). The mount stated a weaker boundary than the code
actually relies on.

## 2. Root cause

Inverted this repo's established "mount = primary/structural gate,
per-handler = redundant backup" pattern (the same reasoning
`routes/admin/__init__.py:149-159` gives for why `ai_console_router` is
mounted `require_super_admin`). Net effect today was still correctly
super_admin-only (redundant per-handler checks caught it), but the mount
was one accidental "the mount already covers this, let's trim the
redundant check" edit away from silently reopening a driver
Stripe-payout-redirect write to any `"drivers"`-grant admin.

## 3. Fix / remediation

`backend/routes/admin/__init__.py`: `stripe_import_router` mount changed
from `dependencies=[Depends(require_module("drivers"))]` to
`dependencies=[Depends(require_super_admin)]`, matching
`stripe_payout_sync_router`/`stripe_connect_ledger_router`/
`tax_id_import_router`'s posture. The 5 per-handler `_require_super_admin`
checks are unchanged (kept as the documented redundant layer).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to this one mount line.** No behavior change for
  any caller that was already passing the per-handler check (i.e., every
  legitimate caller today, since they'd already have to be super_admin to
  get past the handler check). The only callers affected are ones that
  previously held `"drivers"` but not `super_admin` — those were already
  being rejected by the per-handler check with a 403, just one layer later.
- **What could regress:** nothing — checked `tests/test_admin_stripe_import.py`
  (already asserts a plain `"admin"` role gets 403) and
  `tests/test_stripe_account_discovery.py`/`tests/test_no_raw_exception_in_4xx_detail.py`
  (grepped, neither depends on the mount-level gate specifically) — all
  still pass.

## 5. User-experience effect

None — no observable change, since the actual access boundary (super_admin
only) was already enforced; this closes a latent fragility, not a live gap.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/__init__.py` | `stripe_import_router` mount dependency changed from `require_module("drivers")` to `require_super_admin`; comment updated | Make the mount match the actual enforcement, removing the single-point-of-failure fragility |

## 7. Before / after

```python
# Before
admin_router.include_router(stripe_import_router, dependencies=[Depends(require_module("drivers"))])

# After
admin_router.include_router(stripe_import_router, dependencies=[Depends(require_super_admin)])
```

## 8. Rollback plan

`git revert`-safe. No data or schema involved — pure dependency-wiring change.

## 9. Verification performed

- [x] `pytest tests/test_admin_stripe_import.py -q` — 12 passed, 0 failed.
- [x] `ruff check` + `ruff format --check` — clean.
- [x] Grepped for other test files referencing `stripe_import` to confirm
  none assumed the old, weaker mount-level gate.

## What was NOT verified

- Full backend test suite not run in this pass; deferred to the final
  combined verification pass across all 9 audit fixes.
