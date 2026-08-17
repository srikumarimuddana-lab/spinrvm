# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-17 |
| Author | Claude Code (spinr platform) |
| Surface(s) | backend |
| Domain (Sentry tag) | drivers |
| PR / commit link | (this PR) |
| Related issue or gap ID | CR-2026-(assign) — [CR] G1 · Bandit flags a false-positive weak-hash finding in heatmap_config.py, red on every PR |

## 1. Issue / gap identified

`G1 · Bandit (Python SAST)` (`.github/workflows/security-gates.yml`, `--severity-level high --confidence-level high`, `continue-on-error: false`) fails on **every** PR to `main`, regardless of what the PR touches, because of a single pre-existing finding unrelated to any specific diff.

## 2. Root cause

`backend/utils/heatmap_config.py`'s `config_fingerprint()` builds a short cache-key fingerprint (for Redis-cached heatmap cells) using `hashlib.sha1(...)`. Bandit's B324 rule flags any `hashlib.sha1()` call as a "weak hash for security" regardless of purpose, unless the call opts out with `usedforsecurity=False` (Python 3.9+ stdlib flag). This use is not security-sensitive — no password, token, or signature involved, purely a cache-invalidation key — so it's a textbook false positive of exactly the class that flag exists to silence. Confirmed pre-existing and diff-independent: reproduced identically against unmodified `main` and an unrelated PR branch (#4057) on 2026-08-17.

## 3. Fix / remediation

Added `usedforsecurity=False` to the one `hashlib.sha1()` call. No algorithm change, no output change — see verification below.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** `config_fingerprint()` has exactly one caller in the whole codebase: `backend/routes/drivers/profile.py:444`, which builds a Redis cache key (`spinr:heatmap:{area_id}:{cache_version}:{config_fingerprint(hm_cfg)}`). Grepped `backend/` for all non-test references to `config_fingerprint` — confirmed no other reader/writer.
- **No behavior change**: `usedforsecurity=False` only affects FIPS/OpenSSL-provider selection metadata on some platforms, not the digest algorithm or its output. Verified locally that `hashlib.sha1(x).hexdigest()` and `hashlib.sha1(x, usedforsecurity=False).hexdigest()` produce byte-identical results for the same input — so the Redis cache key this function produces is unchanged, meaning no unintended cache-invalidation event for any driver's already-cached heatmap cells.
- No interaction with background loops, ride state machine, or money/wallet deltas — this function is not on any of those paths.

## 5. User-experience effect

None. Backend-only, cache-key-generation internals; no rider/driver/corporate-admin/internal-admin-visible change, and nothing visible mid-session since the produced value is unchanged.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/heatmap_config.py` | `hashlib.sha1(canonical.encode("utf-8"))` → `hashlib.sha1(canonical.encode("utf-8"), usedforsecurity=False)`, plus an explanatory comment | Silence Bandit B324 false positive without changing behavior |

## 7. Before / after

```python
# Before
canonical = json.dumps({k: config[k] for k in sorted(config)}, separators=(",", ":"))
return hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:12]
```

```python
# After
canonical = json.dumps({k: config[k] for k in sorted(config)}, separators=(",", ":"))
# usedforsecurity=False: this is a cache-key fingerprint, not a security
# hash (no password, token, or signature involved) -- silences Bandit's
# B324 false positive (CR-2026-(assign), see .github/ISSUE_TEMPLATE
# ci_change_request.yml issue) without changing the digest at all.
return hashlib.sha1(canonical.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]
```

## 8. Rollback plan

`git revert` this commit. No data migration, no schema, no runtime/cache state to remediate — the produced cache-key digest is identical before and after, so there is no cache-invalidation concern in either direction. A code-only revert is a complete rollback here.

## 9. Verification performed

- [x] Automated tests run: `backend/tests/test_heatmap_config_resolution.py` (44 cases covering `config_fingerprint`'s consumers) — 44 passed, unit tier
- [x] Digest-stability check: confirmed `hashlib.sha1(x)` and `hashlib.sha1(x, usedforsecurity=False)` produce identical hex output for the same input, run locally
- [x] Blast-radius grep performed: `grep -rn "config_fingerprint" backend --include="*.py"` (excluding tests) — exactly one caller, `routes/drivers/profile.py:444`
- [x] Re-ran `bandit -r backend/ --severity-level high --confidence-level high` locally — 0 findings (down from 1)
- [ ] Manual repro steps followed in staging — not done, no staging access in this session
- [x] Reviewed against relevant `CLAUDE.md` convention — none apply beyond the general "don't silently swallow/change behavior" rule, which this change respects (byte-identical output)
- [x] Feature-flagged if user-visible and non-trivial — n/a, not user-visible

## What was NOT verified

Not run against a live/staging environment — this session has no Supabase/staging access, per the standing limitation noted throughout this session's other PRs. The digest-identity check and the existing 44-case test suite are the full extent of verification; there is no live Redis cache to observe directly.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no data-level remediation needed)
- [x] Blast radius is stated, not assumed (one caller, grepped)
- [x] No silent behavior change to an already-shipped flow (digest verified byte-identical)
