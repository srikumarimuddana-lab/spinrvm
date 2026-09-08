# Change Impact & Risk Log — F09 (partial): ownership-safe conversation lock

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-08 |
| Author | Claude Code (agent), for the PR #5138 remediation plan |
| Surface(s) | backend |
| Domain (Sentry tag) | ai |
| PR / commit link | branch `claude/pr-5138-implementation-27zn2l` |
| Related issue or gap ID | F09 (**partial** — one of six sub-items), `docs/security/2026-09-08-ai-security-assessment.md` (PR #5138), remediation order 4 |

**This closes one part of F09 and explicitly leaves the rest open.** See §10
for exactly what is not done — the finding should not be marked resolved on
the strength of this change.

## 1. Issue / gap identified

The AI conversation lock (`ai:conv_lock:{conversation_id}`, 90s TTL) was
acquired with a constant value `"1"` and released with an unconditional
`redis_delete`. The assessment: *"its 90-second TTL is neither renewed nor
released using a unique ownership token."*

Two concrete defects follow:

1. **A release can delete another turn's lock.** If turn A outlives the TTL,
   the key expires, turn B acquires it, and then A's `finally` deletes B's
   lock — so turn C can start alongside B. The release therefore makes the
   concurrent-turn interleaving the lock exists to prevent *more* likely, not
   less.
2. **The fail-open path released a lock it never held.** On a Redis error the
   code sets `acquired = True` and proceeds without the lock, but the `finally`
   still ran `redis_delete` — deleting whatever a concurrent turn legitimately
   held.

## 2. Root cause

The classic distributed-lock omission: a lock with a TTL needs a fencing/
ownership token, because the holder cannot know its lease is still valid at
release time. The code treated release as "clean up the key I created" rather
than "release the lease if I still hold it". Defect 2 is a narrower slip — the
`finally` was not conditioned on the acquisition path that reached it.

## 3. Fix / remediation

- Each turn generates `lock_token = uuid.uuid4().hex` and stores it as the
  lock's value instead of `"1"`.
- Release goes through `_release_conversation_lock`, a **Lua compare-and-delete**
  (`redis_eval`) that deletes only on a token match. Lua because a
  GET-then-DELETE can still delete another turn's lock in the gap between the
  two calls — the atomicity is the point, not an optimisation.
- A separate `lock_held` flag records whether `redis_set_nx` actually returned
  `True`. The fail-open path leaves it `False`, so no release is attempted.
- When `REDIS_URL` is unset, `redis_eval` raises `RuntimeError` by design (no
  Lua interpreter in the in-process fallback). That case falls back to a
  non-atomic compare-and-delete, which is *exact* there: a single in-process
  dict has no other replica to race with.
- Release never raises. A failed release is not worth failing a turn over —
  the rider has already seen the reply stream, and the lock carries a TTL, so
  the worst case is the conversation staying locked for the remainder of it.

The documented **fail-open on acquisition** posture is unchanged, deliberately:
`utils/redis_client` and the existing test both record that blocking every AI
conversation on a Redis blip is worse than occasionally racing two turns.

## 4. Risk & impact on existing functionality

**Blast radius: the AI conversation lock only.** No other caller uses this key.

- `grep -rn "conv_lock" backend/` → `ai/orchestrator.py` and its tests.
- `grep -rn "redis_eval" backend/` → previously one caller
  (`utils/h3_location_index.py`), which already establishes the
  `RuntimeError`-when-unconfigured fallback pattern this change follows.
- The lock is only taken when `conversation_id` is not `None`; a brand-new
  conversation still skips it entirely (existing test unchanged).

Regressions considered:

- **Could the token change break mutual exclusion?** No — `redis_set_nx` still
  gates acquisition on key absence; only the stored *value* changed.
- **Could a failed release strand a conversation?** For at most the remaining
  TTL (≤90s), after which the key expires and the next turn proceeds. That is
  the pre-existing worst case for a release that never ran at all.
- **Does this change acquisition behaviour on a Redis outage?** No. Same
  fail-open, same log, same metric. Only the release side changed.
- **Existing test `test_conversation_lock_error_fails_open`** patches
  `redis_delete`; that patch is now unnecessary but harmless, and the test's
  assertions still hold. A new test covers the same path from the release
  angle.

No ride-state, money, dispatch, auth or insurance-period interaction.

## 5. User-experience effect

**None expected.** The lock is invisible to riders except through the
`conversation_busy` error, and this change does not alter when that is emitted.

The one behavioural difference is a strict improvement: a rider who previously
could have had a second turn's lock silently dropped by a first turn's release
(allowing two replies to interleave and produce out-of-order chat history) no
longer can. Not visible mid-ride; this is a chat surface. No copy change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/ai/orchestrator.py` | `lock_token` + `lock_held`; `_RELEASE_IF_OWNER_LUA`; `_release_conversation_lock()`; `finally` releases only when held; `redis_eval`/`redis_get` imported. | The fix. |
| `backend/tests/test_ai_orchestrator.py` | +5 tests: token-checked release, the clobber scenario, fail-open releases nothing, no-Lua fallback, release never raises. | Regression cover. |

## 7. Before / after

```python
# Before
acquired = await redis_set_nx(lock_key, "1", _CONV_LOCK_TTL_SECONDS)
...
finally:
    await redis_delete(lock_key)      # deletes whoever's lock is there now
```

```python
# After
lock_token = uuid.uuid4().hex
lock_held = False
acquired = await redis_set_nx(lock_key, lock_token, _CONV_LOCK_TTL_SECONDS)
lock_held = acquired
...
finally:
    if lock_held:
        await _release_conversation_lock(lock_key, lock_token)   # CAS on the token
```

## 8. Rollback plan

`git revert` is a complete rollback: no data is written beyond a Redis key that
already expires on its own TTL, no migration, no schema change. Any lock left
behind by a mixed-version deploy expires within 90 seconds by construction, so
there is no cleanup step and no need to drain before rolling back.

`ai_assistant_enabled` false in `app_settings` (no redeploy) is the operational
lever if the lock misbehaves in a way that blocks conversations.

## 9. Verification performed

- [x] **Offline probe** (`probe_f09_lock.py`) modelling the release semantics:
      first reproduced the **old** behaviour clobbering turn B's lock, then
      confirmed the new compare-and-delete leaves it intact, that B's own
      release still works, that 1000 tokens are distinct, and that mutual
      exclusion still holds while the lock is held. **All pass.**
- [x] **Blast-radius grep performed** — `conv_lock` and `redis_eval` across
      `backend/`; the `RuntimeError`-when-unconfigured fallback follows the
      pattern `utils/h3_location_index.py` already established rather than
      inventing one.
- [x] Existing lock tests audited: `test_conversation_lock_error_fails_open`
      and `test_new_conversation_skips_the_lock` both still hold unchanged.
- [x] `ruff check` + `ruff format --check` clean on all changed files.
- [x] Reviewed against CLAUDE.md: the documented fail-open-on-acquisition
      posture is preserved verbatim; the release failure is logged at `error`
      with context rather than swallowed silently.

## 10. What was NOT verified — and what F09 still needs

**F09 is NOT closed by this change.** The assessment's fix list has six parts;
this addresses one:

| F09 sub-item | Status |
|---|---|
| Ownership-safe lock release | **done here** |
| Lock TTL renewal / a suitable deadline strategy | **not done** — a turn outliving 90s still loses its lease. With the token, the consequence is now bounded (a race, not a cascade), but renewal needs a heartbeat task. |
| Global provider-spend / token budgets | **not done** |
| Concurrent-generation limits | **not done** |
| Bounded whole-turn deadline + cancellation cleanup | **not done** |
| Explicit distributed-store outage policy | **not done** — the current per-call fail-open/bounded-fallback behaviour is unchanged and still undocumented as a single policy |
| Public-chat server-verifiable abuse controls; direct-origin restriction check | **not done** |

Also not verified:

- **The pytest suite was not run.** PyPI is unreachable (gateway 403), so
  backend dependencies cannot be installed. The 5 new tests are **unrun** and
  must go green in CI.
- **The Lua script was never executed against a real Redis.** The probe models
  its semantics in Python; the script text itself (`redis.call('get', KEYS[1])
  == ARGV[1]`) is unexercised. It is a two-line, widely-used idiom, but "read
  carefully" is not "ran it". A single manual acquire/release against a dev
  Redis would settle it.
- **Argument marshalling to `redis_eval` was not verified against the real
  client** — `redis_eval(script, 1, lock_key, token)` assumes `numkeys=1` then
  keys then args, which matches the wrapper's docstring and the
  `h3_location_index` call site, but was not run.
- **No concurrency test with two real overlapping turns.** The clobber scenario
  is verified as a modelled sequence, not as two racing coroutines against a
  live Redis.
