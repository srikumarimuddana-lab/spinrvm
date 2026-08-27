# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-27 |
| Author | Claude Code (interactive session) — found via `backend-test` red on PR #4630 (a docs-only PR that couldn't have caused this) |
| Surface(s) | backend (admin Stripe-events route) |
| Domain (Sentry tag) | payments |
| PR / commit link | commit following this log |
| Related issue or gap ID | none tracked before this — found and fixed same day |

## 1. Issue / gap identified

`backend-test` failed on PR #4630 (`ACTION_ITEMS.md` C47's own closure PR — docs-only, touches
only `ACTION_ITEMS.md`, so the diff itself cannot have caused this): `tests/test_dual_import_
parity.py::test_fallback_import_branches_mirror_try_branches` — `routes/admin/stripe_events.py`'s
`except ImportError` fallback branches were missing 4 names its `try` branches bind:
`claim_stripe_event`, `db_supabase`, `get_admin_user`, `get_app_settings`.

## 2. Root cause

Reproduced locally on `main` (via the merged-branch-restart branch, i.e. `origin/main` plus only
my one docs commit) before touching anything — confirmed red on `main` itself, not caused by any
PR in flight. Two distinct issues in `routes/admin/stripe_events.py`'s import block:

1. **Real gap, now fixed**: the fourth `try/except ImportError` block's fallback branch imported
   `DatabaseError`, `mark_stripe_event_processed`, `unclaim_stripe_event` from `db_supabase`, but
   not `claim_stripe_event` (present in the `try` branch and actually used at line 229) — a latent
   `NameError` if this module is ever imported in top-level mode (`python -m backend.server`
   imports `backend.x`; bare top-level mode imports `x` — CLAUDE.md's dual-import convention
   exists exactly to keep both working). Same gap for `get_app_settings` (used at line 237): the
   `try` branch imports it from `...settings_loader`, the fallback branch never imported it at
   all.
2. **Dead code, removed rather than mirrored**: `get_admin_user` (first block) and the bare
   `db_supabase` module import (two separate `try: ...; except ImportError: pass` blocks) were
   never referenced anywhere else in the file — confirmed via grep (`require_super_admin` is used
   5 times via `Depends(...)`; `get_admin_user` and bare `db_supabase.` zero times). Rather than
   adding matching-but-still-dead names to the fallback branch, removed them from the `try` branch
   instead — the correct fix per CLAUDE.md's simplicity-first principle, and it makes both
   branches trivially mirror each other with no dead imports in either.

## 3. Fix / remediation

- `routes/admin/stripe_events.py`:
  - Removed `get_admin_user` from the first `try` block's import (unused; `require_super_admin`
    already correctly mirrored in both branches).
  - Removed the two vestigial `try: from ..._base_imports import db_supabase` / `try: from ... import
    db_supabase` blocks entirely (both `except ImportError: pass`, both binding a name never
    referenced anywhere in the file).
  - Fourth block's fallback branch: added `claim_stripe_event` to the `db_supabase` import list
    (now matches the `try` branch exactly) and added `from settings_loader import get_app_settings`
    (previously absent from the fallback branch entirely).

## 4. Risk & impact on existing functionality

- **Blast radius:** one file. Grepped the whole backend for other importers of
  `routes.admin.stripe_events` — none (it's a leaf route module, only ever imported by
  `routes/admin/__init__.py`'s router-mount list, itself unchanged).
- **No behavior change in the import mode every real deployment actually uses.** This repo runs
  `python -m backend.server` (package-mode imports, the `try` branch) — confirmed via CLAUDE.md's
  own "Commands" section and the dual-import pattern's own stated intent ("`python -m
  backend.server` vs top-level"). The fallback (top-level) branch is exercised by
  `test_dual_import_parity.py` for correctness, not by the running application. This fix closes a
  latent bug that could only ever fire in a mode the production app doesn't use — no live-traffic
  risk, but a real correctness gap the test exists specifically to catch.
- **Removed imports were genuinely dead** — confirmed by grep, not assumed: `get_admin_user` and
  bare `db_supabase` (module-level, as opposed to the specific functions imported from it in block
  four) appear nowhere else in the file's ~300 lines.

## 5. User-experience effect

None — internal admin-only route (`super_admin`-gated Stripe-event visibility/replay tooling), and
the fixed code path only matters in an import mode this deployment doesn't use.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/stripe_events.py` | Removed 2 dead imports (`get_admin_user`, bare `db_supabase`) from the `try` branch; added `claim_stripe_event` + `get_app_settings` to the fallback branch to match the `try` branch. | Close a real dual-import-parity gap (`test_dual_import_parity.py`) red on `main`. |

## 7. Before / after

```python
# Before
try:
    from ...dependencies import get_admin_user, require_super_admin   # get_admin_user unused
    from ...utils.audit_logger import log_admin_action
except ImportError:
    from dependencies import require_super_admin
    from utils.audit_logger import log_admin_action

try:
    from ..._base_imports import db_supabase   # unused, and no fallback binds it
except ImportError:
    pass

try:
    from ... import db_supabase                # same unused name, second attempt
except ImportError:
    pass  # type: ignore

try:
    from ...db_supabase import (
        DatabaseError, claim_stripe_event, mark_stripe_event_processed, unclaim_stripe_event,
    )
    from ...settings_loader import get_app_settings
except ImportError:
    from db_supabase import (  # type: ignore
        DatabaseError, mark_stripe_event_processed, unclaim_stripe_event,   # claim_stripe_event missing
    )
    # get_app_settings never imported in the fallback branch at all
```

```python
# After
try:
    from ...dependencies import require_super_admin
    from ...utils.audit_logger import log_admin_action
except ImportError:
    from dependencies import require_super_admin
    from utils.audit_logger import log_admin_action

try:
    from ...db_supabase import (
        DatabaseError, claim_stripe_event, mark_stripe_event_processed, unclaim_stripe_event,
    )
    from ...settings_loader import get_app_settings
except ImportError:
    from db_supabase import (  # type: ignore
        DatabaseError, claim_stripe_event, mark_stripe_event_processed, unclaim_stripe_event,
    )
    from settings_loader import get_app_settings  # type: ignore
```

## 8. Rollback plan

`git revert` is complete and sufficient — no data, schema, or migration component, pure import
statements.

## 9. Verification performed

- [x] **Reproduced on `main` before fixing** — confirmed red on the base branch, not caused by any
      in-flight PR (the discovering PR is docs-only).
- [x] `pytest tests/test_dual_import_parity.py` — 3/3 pass (was 1 failed, 2 passed).
- [x] Confirmed both removed names (`get_admin_user`, bare `db_supabase`) are genuinely unused
      elsewhere in the file via grep, not assumed.
- [x] Confirmed both added names (`claim_stripe_event`, `get_app_settings`) are actually used in
      the file body (lines 229, 237) — the fallback fix isn't adding dead code either.
- [x] `ruff check` and `ruff format --check` clean on the modified file.
- [x] Broader collateral sweep: `pytest -k "stripe or admin_stripe or dual_import"` — 815 passed,
      1 pre-existing skip, no new failures. `pytest -k "admin_module or router_mount or app_factory
      or server_startup"` — 12 passed (router-mount parity tests included).
- [x] Blast-radius grep performed (§4) — one importer (the router mount list), unchanged.

## What was NOT verified

- Not exercised in actual top-level import mode against a live server (would require running
  `stripe_events.py` as `python routes/admin/stripe_events.py`-style, outside how this repo is
  ever actually deployed) — verified via the AST-based parity test and manual code reading
  instead, consistent with what that test exists to guarantee without needing a live top-level-mode
  run.
