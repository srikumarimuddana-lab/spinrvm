# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | TeamSpinr (session-assisted) |
| Surface(s) | infra/CI (backend test execution only — no application code) |
| Domain (Sentry tag) | admin (CI/governance, not a runtime domain) |
| PR / commit link | see below |
| Related issue or gap ID | CR #5579 (open, recommended profiling before any structural change); `docs/audit/2026-09-25-contributor-governance-baseline.md` §6 recommendation #2 |

## 1. Issue / gap identified

`ci.yml`'s `backend-test` job has had its timeout raised twice in the last few days (20→30→35 min) because its mocked-DB test tier — run fully sequentially — keeps landing near-misses on unrelated diffs. CR #5579 explicitly recommended profiling with `--durations=50` before deciding on a structural fix; that step had never been done.

## 2. Root cause

~16,500 tests running sequentially in a single pytest invocation, with no test-level slowness outlier (see verification below) — a parallelization gap, not a "slow test" problem.

## 3. Fix / remediation

Added `pytest-xdist` and enabled `-n auto` on the mocked-DB tier's pytest invocation. One file, `tests/test_sms.py`, is carved out into its own sequential invocation immediately after, with `--cov-append` to combine its coverage into the same run — its tests use real threads with a 0.1s timeout budget and are not safe under the CPU contention multiple xdist worker processes introduce (verified below).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to CI test execution.** No application code changed. `ci-guardrails.yml`'s separate `shared-coverage-run` job (which independently re-runs the same suite — a known, separately-tracked duplication, `ACTION_ITEMS.md` C47) is untouched by this change; only `ci.yml`'s own `backend-test` job is affected.
- **Coverage reporting**: switched from one `pytest --cov` invocation to two (`--cov-report=` on the first, suppressing output, then `--cov-append --cov-report=xml --cov-report=term-missing` on the second). Verified locally this combines correctly rather than overwriting (coverage total increased after the second invocation, not reset — see §9).
- **Test isolation**: xdist runs each worker as a separate process, so module-level state is not shared between workers (safer than thread-based parallelism for isolation purposes). The one exception found (`test_sms.py`) is a timing/CPU-contention sensitivity, not a state-sharing bug, and is explicitly excluded from the parallel run.
- **Discovered but explicitly NOT fixed here**: while regenerating `requirements-locked.txt`, found it was already stale relative to `requirements.txt`/`requirements.in` — pinned to `redis[asyncio]==7.4.0` while the source files specify `redis>=8.1.0` (resolving to exactly `8.1.0`). No existing CI gate catches this class of drift (`pip-compile-check.yml` only diffs `requirements.txt` against `requirements.in`, not the locked file against `requirements.txt`). This is a real, separate gap — production has been installing `redis` 7.4.0 while the repo's own dependency declarations say 8.1.0+ — but bundling a `redis` major-version bump into this change would violate the surgical-change discipline and needs its own dedicated review (redis backs rate limiting, OTP lockout, and WS pub/sub — all security/availability-sensitive). Flagged to the repo owner separately; not touched in this diff. The lockfile change here hand-splices in only `pytest-xdist`/`execnet`, preserving every other existing pin byte-for-byte (verified via diff).

## 5. User-experience effect

None. CI-only change; no rider/driver/admin-facing behavior.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `.github/workflows/ci.yml` | Split the mocked-DB tier's single pytest invocation into two: parallel (`-n auto`, excluding `tests/test_sms.py`) then sequential (`tests/test_sms.py` alone, `--cov-append`) | Parallelizes the tier that dominates the job's runtime, while keeping the one timing-sensitive file safe |
| `backend/requirements.in` | Added `pytest-xdist>=3.6.1` | New test dependency |
| `backend/requirements.txt` | Regenerated via the repo's own canonical `pip-compile` command (`--no-upgrade`, Python 3.12, matching `pip-compile-check.yml`) | Adds `pytest-xdist`/`execnet`; diff is these two lines only |
| `backend/requirements-locked.txt` | Hand-spliced `pytest-xdist==3.8.0` and `execnet==2.1.2` (with real SHA-256 hashes obtained from an actual `pip-compile --generate-hashes` run) into the existing file, rather than a full regeneration | A full regeneration re-resolved unrelated packages (`redis` 7.4.0→8.1.0, stripped `psycopg`/`uvicorn` extras) due to the pre-existing drift described above — hand-splicing keeps this change surgical |

## 7. Before / after

```yaml
# Before
pytest --cov=. --cov-report=xml --cov-report=term-missing --timeout=30 \
  --ignore=tests/direct_pool --ignore=tests/rls \
  --ignore=tests/test_schemathesis_fuzz.py -v
```

```yaml
# After
pytest --cov=. --cov-report= --timeout=30 \
  --ignore=tests/direct_pool --ignore=tests/rls \
  --ignore=tests/test_schemathesis_fuzz.py --ignore=tests/test_sms.py \
  -n auto -v
pytest --cov=. --cov-append --cov-report=xml --cov-report=term-missing --timeout=30 \
  tests/test_sms.py -v
```

## 8. Rollback plan

`git-revert-safe`. Reverting `ci.yml` restores the single sequential invocation; reverting the three dependency files removes `pytest-xdist`/`execnet` cleanly (nothing else in the codebase imports or depends on them). No data, migration, or flag involved.

## 9. Verification performed

- [x] Profiled the full mocked-DB tier locally, sequential, no coverage: **971.07s (16m11s)**, matching the job comment's documented ~16m33s. `--durations=50` showed no outlier (slowest single test: 15.00s) — confirms parallelization, not a slow-test fix, is the right lever.
- [x] Ran the same tier under `pytest-xdist -n auto`: **236.31s (3m56s)** — a ~4.1x speedup. All 16,532 non-`test_sms.py` tests passed; all 11 `test_sms.py` tests failed.
- [x] Confirmed `test_sms.py` passes cleanly alone, both via a standalone isolated run (22/22, ~2s) and via the sequential full-suite baseline run above (only 1 of its tests flaked under full-suite *sequential* CPU load, 0 failures when run by itself) — root cause is CPU contention from concurrent worker processes, not a logic bug in the tests or app code.
- [x] Verified the `--cov-append` two-invocation pattern combines coverage correctly on a small-scale sanity check (coverage % increased after the second invocation, not reset).
- [x] Verified the hand-edited `requirements-locked.txt` installs cleanly with `pip install --require-hashes` in a fresh venv — no hash errors, no resolution errors.
- [x] Diffed `requirements-locked.txt` against its pre-change version: confirmed the only changes are the two new package blocks and one `# via` annotation line — no unrelated drift shipped.

## 10. What was NOT verified

- Not verified on an actual GitHub-hosted `ubuntu-latest` runner — all timing numbers are from this session's local container, whose CPU core count may differ from GitHub's runner. The real speedup ratio in CI should be confirmed from the first post-merge run before drawing further conclusions (e.g., before lowering `timeout-minutes`).
- Did not re-run the full two-invocation sequence end-to-end locally a second time (ran each half separately, plus a small-scale combined sanity check) — the first real CI run of this change is effectively that final end-to-end verification and should be watched.
- Did not investigate or fix the pre-existing `requirements-locked.txt`/`requirements.txt` drift (`redis` 7.4.0 vs 8.1.0) — flagged as a separate, real finding for the repo owner, deliberately out of scope here.
