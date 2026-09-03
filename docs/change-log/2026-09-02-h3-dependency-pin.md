# 2026-09-02 — Pin missing `h3` dependency (ACTION_ITEMS.md C51)

## Issue/gap identified

`backend-test` failed at pytest collection on `main` (base-branch-red, not
introduced by any specific PR) with `ModuleNotFoundError: No module named
'h3'`, taking down 4 test files and cascading into 2 downstream coverage-floor
CI checks.

## Root cause

`backend/utils/h3_cells.py` does `import h3` (Uber's H3 geospatial-indexing
library) for dispatch-radius lookup and heatmap aggregation, and
`backend/utils/h3_location_index.py` imports from it too — but the `h3`
package was never added to `backend/requirements.in`/`requirements.txt`/
`requirements-locked.txt`. The only similarly-named package present,
`mmh3==5.2.1`, is an unrelated MurmurHash library pulled in transitively via
`pyiceberg`.

## Fix/remediation

Added `h3>=4,<5` to `requirements.in` (the code's own calls —
`h3.latlng_to_cell`, `h3.grid_disk`, `h3.cell_to_latlng`,
`h3.cell_to_boundary`, `h3.is_valid_cell` — are v4 API names, not v3),
compiled the resulting exact pin (`h3==4.5.0`) into `requirements.txt`, and
inserted just the `h3` entry (version + SHA-256 hashes) into
`requirements-locked.txt` by hand-splicing the block `pip-compile
--generate-hashes` produced into the existing file, rather than accepting a
full regeneration.

**Why hand-spliced, not a full lockfile regen:** running
`pip-compile --generate-hashes` straight over the *existing*
`requirements-locked.txt` picked up ~13 unrelated package version bumps
(twilio, stripe, boto3, pandas, certifi, requests, …) — not because this
session's h3 addition caused drift, but because `requirements-locked.txt` was
**already stale relative to `requirements.txt`** on `main` before this session
touched anything (confirmed via `git show origin/main:...` — e.g.
`requirements.txt` already had `stripe==15.5.1` while
`requirements-locked.txt` still had `stripe==15.1.0`). That drift is real and
is now flagged as its own gap (see "What was NOT verified" below) — but
resolving it means re-verifying every bumped package (money-adjacent ones,
`stripe`/`twilio`/`boto3`, included) per CLAUDE.md's pre-merge gate #8, which
is out of scope for a one-line dependency-pin fix. Splicing in only the new
`h3` block keeps this change strictly additive and re-verifiable in isolation.

## Risk & impact on existing functionality

- **Blast radius:** `requirements.in`/`.txt`/`-locked.txt` are the only files
  changed. No application code changed — `h3_cells.py`/`h3_location_index.py`
  were already written correctly against the v4 API; they just had no
  installable dependency backing them.
- Grepped `routes/`, `services/`, `core/` for other consumers of `h3` beyond
  `h3_cells.py`/`h3_location_index.py`: none. `h3_location_index.py`'s only
  consumer, `services/dispatch_candidates.py`, is itself not yet imported by
  any router/service — this dependency is not on the live request path today.
- **Second, separate finding surfaced by this fix** (not caused by it): once
  the `h3` import error was cleared, `h3_location_index.py` immediately hit a
  new `ImportError` — it imports 6 Redis helper functions
  (`redis_eval`, `redis_hgetall`, `redis_hset`, `redis_zadd`,
  `redis_zrangebyscore_many`, `redis_zrem`) that don't exist in
  `utils/redis_client.py`. This means the module has never successfully
  imported since it was added (commit `fc6f922`), in any environment. Tracked
  separately as ACTION_ITEMS.md C52 — deliberately not fixed here (implementing
  6 new Redis primitives correctly is well beyond a dependency-pin fix, and
  the module isn't wired into live dispatch yet regardless).

## User experience effect

None. CI-only change; no application code touched, no runtime behavior for
riders/drivers/admins changes.

## Files modified

| File | What changed | Why |
|---|---|---|
| `backend/requirements.in` | Added `h3>=4,<5` under Utilities, with a comment explaining the v4-API-name requirement | Source-of-truth floor pin |
| `backend/requirements.txt` | Added `h3==4.5.0  # via -r requirements.in` | Compiled exact pin |
| `backend/requirements-locked.txt` | Added the `h3==4.5.0` block (version + SHA-256 hashes) at its alphabetical position; nothing else changed | Hash-pinned install source (`pip install --require-hashes`) |
| `ACTION_ITEMS.md` | C51 marked resolved for the pin itself, noted the still-open C52 finding; added C52 | Backlog tracking |

## Before/after snippet

```diff
+h3>=4,<5   # requirements.in — dispatch-radius lookup / heatmap aggregation (utils/h3_cells.py)
```
```diff
+h3==4.5.0                 # via -r requirements.in   (requirements.txt)
```
```diff
+h3==4.5.0 \
+    --hash=sha256:0b17cf24... (34 hash lines)
+    # via -r requirements.txt   (requirements-locked.txt)
```

## Rollback plan

`git revert` is safe — pure dependency-manifest change, no migration, no data
mutation, no flag. Reverting restores the pre-existing base-branch-red state
(CI already broken before this change), so a revert cannot make things worse
than they already were on `main`.

## Verification performed

- `pip install "h3>=4,<5"` in the backend venv → resolved `h3==4.5.0`,
  matching the pin.
- `pytest tests/test_h3_cells.py tests/test_h3_heatmap.py -q` → **22/22
  passed.** Confirms the `h3` pin itself is correct and sufficient for those
  two files.
- `pytest tests/test_h3_location_index.py tests/test_dispatch_candidates.py`
  → still fails, but now at a *different* import (`redis_eval` missing from
  `redis_client.py`) — confirms the h3 pin fix is complete and correct for
  what it targets; the remaining failure is the separate C52 finding.
- `grep -c -- '--hash=sha256:' requirements-locked.txt` → 3215 (well above the
  CI gate's ≥100 threshold; `security-posture-gate`'s hash-presence check
  should still pass).
- Confirmed via `git diff --stat` that only the intended lines changed in all
  three requirements files (44 lines total across `requirements.in`/`.txt`,
  plus 35 additive lines in the locked file) — no unrelated package bumps
  leaked in from the hand-splice approach.
- No production build applicable — backend-only dependency change, no
  `admin-dashboard`/`rider-app`/`driver-app` files touched.

## What was NOT verified

- **Not run against CI directly** — verified locally against this session's
  sandboxed backend venv, not the actual GitHub Actions runner image. The
  pinned SHA-256 hashes are for the wheels this venv's pip resolved
  (`manylinux2014`/`cp311`); if CI's runner uses a different Python/platform
  combination than expected, `pip install --require-hashes` could need
  additional wheel hashes not captured here — the pip-compile invocation used
  its default (non-cross-platform) hash generation, matching how the rest of
  this lockfile appears to have been generated.
- **The pre-existing `requirements.txt`/`requirements-locked.txt` drift is
  flagged, not fixed** — `main`'s lockfile was already stale for stripe,
  twilio, boto3, and ~10 other packages before this session touched anything.
  Not resolved here (see "Why hand-spliced" above); worth its own tracked
  item and its own verification pass given money-adjacent packages
  (stripe/twilio) are involved.
- **C52 (missing Redis helpers) is flagged only, not fixed** — see above.
- Did not check whether other, not-yet-discovered import chains in the
  codebase also depend on packages missing from requirements — this fix
  targeted only the specific `h3` gap already identified.
