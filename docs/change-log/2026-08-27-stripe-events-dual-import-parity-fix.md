# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-27 |
| Author | Claude, self-initiated — found via a `backend-test` CI failure on unrelated PR #4629 (docs-only heatmap airport-enable change) |
| Surface(s) | backend |
| Domain (Sentry tag) | payments, admin |
| PR / commit link | commit following this log |
| Related issue or gap ID | CI failure: `tests/test_dual_import_parity.py::test_fallback_import_branches_mirror_try_branches` on `main`, surfaced via PR #4629's CI run; reported as unrelated in a comment on that PR before being fixed here in its own PR |

## 1. Issue / gap identified

`backend/routes/admin/stripe_events.py` (introduced by commit `5d5762ad4`) violates the
mandatory dual-import pattern (CLAUDE.md: "every backend module uses `try: from .routes.x
import y / except ImportError: from x import y` ... intentional ... do not simplify away").
Three of its four import blocks had names bound in the `try` branch (package-relative, used
by `python -m backend.server`) that were missing from the `except ImportError` branch
(top-level, used when `backend/` itself is on `sys.path`) — a latent `NameError` in top-level
import mode, caught by the repo's own `test_dual_import_parity.py` guard test.

## 2. Root cause

Three separate parity gaps in the same file, likely from incremental edits that added a
name to one branch without mirroring it to the other:

- **Block 1** (auth deps): `try` imported `get_admin_user, require_super_admin`; `except`
  imported only `require_super_admin`. Turned out moot — see below.
- **Block 2** (`db_supabase`): two redundant, broken try/except pairs. The first tried a
  nonexistent module `..._base_imports` (grepped the repo — no such module exists anywhere)
  and silently `pass`ed on failure with **no fallback at all**. The second tried `from ... import
  db_supabase` and also silently `pass`ed on `ImportError`, again with no top-level-mode
  fallback.
- **Block 3** (`db_supabase` named exports + `settings_loader`): `try` imported
  `DatabaseError, claim_stripe_event, mark_stripe_event_processed, unclaim_stripe_event` plus
  `get_app_settings`; `except` imported only `DatabaseError, mark_stripe_event_processed,
  unclaim_stripe_event` — missing `claim_stripe_event` and `get_app_settings` entirely. Both
  are actually called in `replay_event()` (lines ~229 and ~237), so in top-level import mode
  this was a real reachable `NameError`, not just a lint nit.

While fixing block 1, grepped the whole file for actual usage and found `get_admin_user` and
the bare `db_supabase` module import (block 2) are **not referenced anywhere in this file** —
every endpoint gates on `require_super_admin`, and every `db_supabase.*` call goes through the
named imports in block 3 instead. Both were already-dead imports on `main`, not something this
fix needed to preserve; keeping them paired across both branches just to satisfy the parity
test would have kept dead code alive for no reason, so per CLAUDE.md's "Surgical changes /
simplicity first" they were dropped instead of duplicated.

## 3. Fix / remediation

- Block 1: dropped the unused `get_admin_user` import from the `try` branch (nothing in this
  file calls it); `require_super_admin` and `log_admin_action` now match in both branches.
- Block 2: removed entirely — collapsed into block 3 (see below), since the only thing this
  file actually needs from `db_supabase` is the four named symbols already imported there.
- Block 3: added the two missing names (`claim_stripe_event`, `get_app_settings`) to the
  `except` branch so both branches import the identical name set.

Net effect: three import blocks (auth deps, `db_supabase` named exports + `settings_loader`,
`repositories._base`), each with exact name parity between `try` and `except`, matching every
other module's pattern in this codebase.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to this one file's import block.** `stripe_events.py` is mounted
  once, in `backend/routes/admin/__init__.py`, as `router` — grepped for any other importer of
  this module or its private helpers (`_query_stuck_events`, `_get_stuck_event`); none exist
  outside this file.
- **Which mode was actually broken:** in `python -m backend.server` mode (the way production
  runs, per CLAUDE.md's Commands section), the `try` branch always succeeded, so the missing
  `except`-branch names were never reached — this is why the bug shipped unnoticed. It's a
  live risk only in top-level import mode (`backend/` on `sys.path` directly, e.g. some test
  harnesses or scripts) — if the `try` branch ever fails there, `replay_event()` would hit
  `NameError: name 'claim_stripe_event' is not defined` on any admin attempt to replay a stuck
  Stripe webhook event, mid-incident.
- **No behavior change in the mode that actually serves production traffic.** Every symbol
  used by any endpoint in this file (`require_super_admin`, `log_admin_action`, `DatabaseError`,
  `claim_stripe_event`, `mark_stripe_event_processed`, `unclaim_stripe_event`,
  `get_app_settings`, `run_sync`, `supabase`) is imported identically to before in the `try`
  branch — only the `except` branch and the two genuinely-unused names changed.
- **Removing `get_admin_user` / bare `db_supabase`:** confirmed via grep of the full file body
  that neither symbol is referenced anywhere outside the import statements themselves. No
  endpoint, helper, or type annotation in this file used either name.

## 5. User-experience effect

None directly — this is an admin-only, ops-facing endpoint (`/admin/stripe-events/*`, gated by
`require_super_admin`) for inspecting/replaying stuck Stripe webhook events. No rider, driver,
or corporate-admin-visible surface is touched. The practical effect is that an on-call admin
attempting to replay a stuck Stripe event via this endpoint, in the specific (currently
unused-in-prod) top-level import mode, would no longer hit a `NameError` mid-incident.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/stripe_events.py` | Fixed dual-import name parity across all three import blocks; removed two dead imports (`get_admin_user`, bare `db_supabase` module) instead of duplicating them into the fallback branch | CLAUDE.md-mandated dual-import pattern; caught by `test_dual_import_parity.py` on CI |
| `docs/change-log/2026-08-27-stripe-events-dual-import-parity-fix.md` | This log | Change touches an admin/payments-adjacent surface (Stripe webhook event resolution) |

## 7. Before / after

```python
# Before — block 3, the reachable-NameError gap
try:
    from ...db_supabase import (
        DatabaseError,
        claim_stripe_event,
        mark_stripe_event_processed,
        unclaim_stripe_event,
    )
    from ...settings_loader import get_app_settings
except ImportError:
    from db_supabase import (  # type: ignore
        DatabaseError,
        mark_stripe_event_processed,
        unclaim_stripe_event,
    )
    # claim_stripe_event and get_app_settings missing here —
    # both are called in replay_event()
```

```python
# After
try:
    from ...db_supabase import (
        DatabaseError,
        claim_stripe_event,
        mark_stripe_event_processed,
        unclaim_stripe_event,
    )
    from ...settings_loader import get_app_settings
except ImportError:
    from db_supabase import (  # type: ignore
        DatabaseError,
        claim_stripe_event,
        mark_stripe_event_processed,
        unclaim_stripe_event,
    )
    from settings_loader import get_app_settings  # type: ignore
```

## 8. Rollback plan

`git revert` is complete and sufficient — pure import-statement fix, no data, migration, or
runtime-behavior component in the currently-serving import mode.

## 9. Verification performed

- [x] `pytest tests/test_dual_import_parity.py -q` (from `backend/`) — 3/3 pass, including
      the exact test that failed on CI (`test_fallback_import_branches_mirror_try_branches`).
- [x] `pytest tests/ -k stripe_events -q --no-cov` — 9 passed, 1 skipped (pre-existing,
      unrelated to this file — no dedicated endpoint-level test file exists for
      `routes/admin/stripe_events.py`; noted as a standing gap, not introduced by this fix).
- [x] `ruff check routes/admin/stripe_events.py` and `ruff format --check` — clean.
- [x] Manually re-derived the fixed file's every used name against both import branches by
      grep — confirmed no remaining parity gap in any of the three blocks.
- [ ] Did not run a real production build/boot of `backend.server` in top-level import mode —
      the local sandbox environment lacks `JWT_SECRET`/`ADMIN_PASSWORD` env vars needed for
      `core.config.Settings()` to construct, so a full app-boot smoke test wasn't possible
      here; relied on the dual-import-parity static-analysis test instead, which is the
      purpose-built guard for exactly this class of bug.

## What was NOT verified

- Did not exercise the `/admin/stripe-events/{event_id}/replay` endpoint end-to-end (real or
  mocked Stripe event) — this fix is import-statement-only and doesn't touch that endpoint's
  logic; the pre-existing lack of endpoint-level tests for this file is a standing gap, not
  something this fix closes.
- Did not confirm whether top-level import mode is actually exercised anywhere in this repo's
  current CI/deploy pipeline (production per CLAUDE.md's Commands section always runs
  `python -m backend.server`) — the fix closes the gap regardless of whether it's currently
  reachable, consistent with why the dual-import pattern and its guard test exist at all.

## 10. Sign-off

- [x] Rollback plan is concrete (plain `git revert`, no data-layer component).
- [x] Blast radius is stated, not assumed — isolated to one file's import block, no other
      importer of this module's private helpers exists.
- [x] No silent behavior change to an already-shipped flow in the import mode that actually
      serves production traffic — only the previously-unreachable-in-prod fallback branch and
      two genuinely dead imports changed.
