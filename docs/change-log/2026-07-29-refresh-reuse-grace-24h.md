# Change Impact & Risk Log — refresh-reuse grace window 10 min → 24 h, gated on chain depth

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-29 |
| Author | Claude Code (session: driver sign-out investigation) |
| Surface(s) | backend |
| Domain (Sentry tag) | `auth` |
| PR / commit link | _(pending — subtask 5 of 6)_ |
| Related issue or gap ID | Driver-app frequent sign-out, **root cause 3 of 3** |
| Policy decision by | Product owner — window value chosen explicitly (24 h), device-scoped cascade deferred |

## 1. Issue / gap identified

A driver whose `/auth/refresh` rotation response is lost in a coverage dead zone
still holds the pre-rotation token. On the next attempt the backend sees a
revoked token and — past the grace window — runs the full theft cascade:
`token_version` bump, `revoke_all_for_user`, and a WebSocket kick. Every device
is signed out.

The window was **600 s**. A driver can be out of coverage, or backgrounded, for
far longer than ten minutes; rural Saskatchewan LTE on a moving vehicle makes this
routine rather than exceptional.

## 2. Root cause

Rotation is single-use: every successful refresh stamps `revoked_at` +
`replaced_by` on the token it rotated from. A legitimate client that never
received the rotation response therefore replays a *revoked* token, which is
indistinguishable — on the `revoked_at` field alone — from a stolen one.

`_is_benign_rotation_replay` disambiguated on two conditions only: `replaced_by`
is set, and `revoked_at` is within 600 s. Outside that window, a benign retry was
classified as theft.

## 3. Fix / remediation

Three changes shipped together, because widening the window alone would be a
net security regression:

1. **`REFRESH_REUSE_GRACE_SECONDS` 600 → 86400**, overridable at runtime via the
   `app_settings` key `refresh_reuse_grace_seconds` (see §8 — that override *is*
   the rollback plan). A non-positive override is ignored rather than obeyed,
   since 0 would disable the window entirely and resurrect the original
   mass-logout incident. Reads fail soft to the constant.

2. **A chain-depth condition — `_successor_is_live`.** Benign now additionally
   requires that the row this token was rotated *into* is itself still un-revoked.
   A lost-response race leaves a client exactly one step behind (`T1` replayed,
   `T2` live). If `T2` is also revoked, the chain moved two or more steps, meaning
   someone completed a further rotation while this client held `T1` — which cannot
   happen for a single honest client, because `refreshTokens` always re-reads the
   freshest persisted token before retrying and the driver app's background task
   no longer refreshes at all.

3. **The benign path now writes an audit row** (`refresh_token_rotation_replay`).
   Previously it emitted only a `logger.warning` — no `audit_logs` entry, no
   Sentry tag. Widening 10 min → 24 h without this would have created a day-long
   hole in the forensic record. The **cascade** is suppressed; the **signal** is
   not.

### Honest limit of the chain-depth gate

It does **not** catch the first step of live-token theft. If an attacker steals a
*live* `T1` and refreshes once, `T1.replaced_by = T2` and `T2` is live — so the
victim's replay is classified benign and no cascade fires. Escalation happens on
the attacker's **second** rotation, when the victim's replay finds a revoked
successor.

So the gate narrows the blind spot from "24 h, unconditionally" to "until the
attacker rotates a second time" — which any attacker actually using the session
will do quickly. The audit row in (3) is what covers the remaining gap, by making
rotation replays visible to security ops even when nothing is locked. Closing it
properly needs per-device token binding, which the product owner deferred; the
`refresh_tokens` rows already carry `user_agent` and `ip`, so that is the natural
next step and does not need a new column to get started.

## 4. Risk & impact on existing functionality

**Blast radius: `lookup_refresh_token` is on the hot path of every
`/auth/refresh` call, for every audience.** `auth_router` is mounted at `/api/v1`,
`/api`, and `/api/portal` (`backend/server.py:352-358`), so rider, driver, **and
the corporate company portal** all traverse this code. Admin refresh uses a
separate router but the same helper for its own audience.

Grep confirmed **no production callers** of the changed helpers outside
`utils/refresh_tokens.py` — `_is_benign_rotation_replay`,
`REFRESH_REUSE_GRACE_SECONDS`, `_successor_is_live` and `_grace_seconds` appear
only there and in tests.

Behaviour changes, and their direction:

- **A rotated-token replay between 10 min and 24 h old, with a live successor:**
  was cascade (all devices signed out), now a clean 401 plus an audit row. This is
  the intended fix.
- **A rotated-token replay with a revoked successor, at any age:** was benign if
  under 10 min, **now cascades**. This is a *tightening*, and the only case where
  this diff makes the system more aggressive than before. It is the theft signal.
- **A token revoked without rotation** (explicit logout, prior cascade):
  unchanged — always cascades, short-circuits before any DB read.
- **Past the window:** unchanged — cascades.

**One extra DB read per revoked-token replay.** `_successor_is_live` does a single
`find_one` on `refresh_tokens` by primary key. It runs only on the replay path,
never on a normal refresh, so the hot path is untouched and the
`/auth/refresh` P95 budget is unaffected.

**`_grace_seconds` adds no per-call DB round-trip** — `get_app_settings()` caches
in-process (`backend/settings_loader.py:26-55`).

**Interaction with the WebSocket kick:** unchanged code, but it now fires in
strictly fewer situations (fewer false cascades) and in one new one (depth-2
replay). No change to `ws_manager.kick_user` itself.

Not touched: ride state machine, money/wallet arithmetic, the 16 background
loops, RLS policies, migrations. No new PII — the audit `details` payload carries
the same fields the existing reuse-detection audit row already stores
(`user_agent`, `ip`, row ids), consistent with the 7-year forensic record.

## 5. User-experience effect

- **Driver:** stops being signed out of every device after a dead-zone rotation
  loss. This is the last of the three root causes behind the reported symptom.
- **Rider:** same protection; rarely triggered.
- **Corporate admin:** same protection on the `/api/portal` mount.
- **Internal admin:** a new `audit_logs` action, `refresh_token_rotation_replay`,
  will start appearing. It is **informational, not an incident** — it means a
  client replayed a just-rotated token, which is normal on flaky mobile networks.
  Anyone triaging the audit log needs to know the difference between it and
  `refresh_token_reuse_detected`, which remains the real alarm. Expect
  non-trivial volume from a driver fleet on rural LTE.
- **Visible mid-session:** yes, in the sense that a driver mid-shift who would
  have been signed out no longer is. No UI, copy, or notification change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/refresh_tokens.py` | `REFRESH_REUSE_GRACE_SECONDS` → 86400; added `_grace_seconds` (app_settings override, fail-soft, rejects non-positive), `_successor_is_live` (chain-depth), `_audit_rotation_replay`; `_is_benign_rotation_replay` is now `async` and requires all three conditions; benign branch writes the audit row | Make a 24 h window safe rather than a 24 h blind spot, and keep it tunable without a redeploy |
| `backend/tests/test_refresh_token_reuse_detection.py` | Added `_find_one_router` (answers both lookups); 8 new cases: depth-2 cascades, missing successor benign, successor-lookup failure benign, audit-row schema, audit-failure tolerance, app_settings override honoured, non-positive override ignored, settings-unavailable fallback. Updated 3 existing cases for the second DB read | Pin every branch of the new condition, in both directions |
| `backend/tests/test_refresh_tokens_lifecycle.py` | Made the direct `_is_benign_rotation_replay` test async; added a case asserting the no-`replaced_by` path short-circuits before any DB read | The helper's signature changed |

## 7. Before / after

```python
# Before
def _is_benign_rotation_replay(row: dict) -> bool:
    if not row.get("replaced_by"):
        return False
    revoked_at = _parse_iso_dt(row.get("revoked_at"))
    if not revoked_at:
        return False
    age = (datetime.now(timezone.utc) - revoked_at).total_seconds()
    return 0 <= age <= REFRESH_REUSE_GRACE_SECONDS      # 600

    # caller:
    if _is_benign_rotation_replay(row):
        logger.warning(...)          # no audit row, no Sentry tag
        return None
```

```python
# After
async def _is_benign_rotation_replay(row: dict) -> bool:
    replaced_by = row.get("replaced_by")
    if not replaced_by:
        return False
    revoked_at = _parse_iso_dt(row.get("revoked_at"))
    if not revoked_at:
        return False
    age = (datetime.now(timezone.utc) - revoked_at).total_seconds()
    if not (0 <= age <= await _grace_seconds()):        # 86400, app_settings-tunable
        return False
    return await _successor_is_live(str(replaced_by))   # ← chain depth

    # caller:
    if await _is_benign_rotation_replay(row):
        logger.warning(...)
        await _audit_rotation_replay(row)   # suppress the cascade, NOT the signal
        return None
```

## 8. Rollback plan

**Instant, no deploy:** set `refresh_reuse_grace_seconds` in the `app_settings`
table to the old value (`600`). `_grace_seconds` reads it through
`get_app_settings()`, which caches in-process, so it takes effect within the
settings TTL on every replica with no restart. This is exactly the
flag-without-redeploy pattern CLAUDE.md's Critical Conventions describe, and it
satisfies gates #3 and #7 for the part of this change that carries policy risk.

What the override does **not** roll back: the chain-depth condition and the audit
row. Those need a code revert + deploy. Both are deliberately additive —
chain-depth only ever *tightens* detection, and the audit row only adds a record —
so neither is a plausible cause of a driver-facing incident. If they must go, the
revert is a clean single-file change with no data migration and nothing written to
live state that needs unwinding.

No migration. `app_settings` needs no schema change: `get_app_settings()` merges
the DB row over defaults and passes through unknown keys, so the key can simply be
added by an admin.

## 9. Verification performed

- [x] **Existing tests broke first, and that was the signal.** Adding the
      chain-depth condition made two pre-existing benign-replay tests fail
      immediately, because they stubbed `db.find_one` with a single return value —
      so the successor lookup received the *replayed* row, whose `revoked_at` is
      set, reading as depth-2. Fixed by giving the tests a key-aware router rather
      than by weakening the condition.
- [x] **`pytest tests/test_refresh_token_reuse_detection.py`** → **26 passed**
      (18 pre-existing + 8 new).
- [x] **All three refresh suites together** —
      `test_refresh_token_reuse_detection.py`, `test_refresh_tokens_lifecycle.py`,
      `test_p1_token_refresh.py` → **60 passed**.
- [x] **Broader auth suites** — `test_auth.py`, `test_cookie_auth.py`,
      `test_p1_auth_hardening.py`, `test_account_deletion_auth.py`,
      `test_websocket_token_revocation.py` → **71 passed**.
- [x] **`ruff check` + `ruff format`** on all three changed files → clean
      (two files were reformatted by `ruff format`, then re-tested: still 60 passed).
- [x] **Blast-radius grep performed** — `_is_benign_rotation_replay`,
      `REFRESH_REUSE_GRACE_SECONDS`, `_successor_is_live`, `_grace_seconds` across
      all `backend/**/*.py`: **zero production callers** outside the module itself;
      `auth_router` mount points re-checked in `server.py`.
- [x] **Reviewed against `CLAUDE.md` conventions** — errors are not swallowed:
      `_grace_seconds` and `_successor_is_live` fail soft *by design* and each logs
      at `warning` with the reason, which is the documented treatment for
      degraded-but-recovered, not for a DB/auth error being masked (the auth
      decision itself still resolves and the client still gets a clean 401). Audit
      writes follow the security-event convention (audit table + log). No money
      arithmetic, no state machine, no RLS, no migration.
- [x] **Feature-flag / rollback decision** — see §8; the policy-bearing number is
      runtime-tunable, which is stronger than a boolean flag here.

### What was NOT verified

- **No staging or production run.** Every test mocks `db.find_one` /
  `db.insert_one`; nothing has been exercised against a real `refresh_tokens`
  table. In particular the `_successor_is_live` query has not been checked against
  the live schema, though it is a primary-key lookup on a column already used by
  `issue_refresh_token`.
- **The `app_settings` override has not been exercised end-to-end.** The unit
  tests patch `settings_loader.get_app_settings`; nobody has put
  `refresh_reuse_grace_seconds` into the real settings row and observed the
  behaviour change. **The rollback plan in §8 is therefore untested** — worth a
  staging check before relying on it under pressure.
- **The audit-log volume increase is unestimated.** Every benign rotation replay
  now writes a row. On a fleet with poor coverage this could be a material
  increase in `audit_logs` writes, and the table carries a 7-year retention
  obligation. Not measured; worth watching after deploy.
- **No dry run against `mock_supabase_client` fixtures.** Gate #4 requires one for
  state-machine and money changes; this is neither, so the fixture-based dry run
  was skipped deliberately rather than overlooked.
- **The deferred item is still deferred:** the cascade remains **account-wide**.
  A confirmed depth-2 replay still signs the user out of every device. Per-device
  scoping was explicitly deferred by the product owner and is the remaining gap
  described at the end of §3.
- **The first step of live-token theft is not detected** — see §3. This is a known
  and accepted limit of the chain-depth approach, not an oversight.

## 10. Sign-off

- [x] Rollback plan is concrete and testable — and, unusually for this series,
      **instant and deploy-free** for the part that carries policy risk. Flagged
      above as untested end-to-end.
- [x] Blast radius is stated, not assumed — all three router mounts named, zero
      production callers confirmed by grep, and the one case where this change is
      *more* aggressive than before called out explicitly rather than buried.
- [x] No silent behavior change to an already-shipped flow without the UX field
      filled in — §5 names the new `audit_logs` action and warns that it is
      informational, so it is not mistaken for the existing theft alarm.
