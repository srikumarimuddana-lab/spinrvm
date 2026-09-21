# Change Impact & Risk Log — rider/driver auth records the Fly proxy IP, not the user's

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-21 |
| Author | mkkreddy52@gmail.com (Claude Code assisted) |
| Surface(s) | backend |
| Domain (Sentry tag) | auth |
| PR / commit link | branch `claude/funny-franklin-157cbt` |
| Related issue or gap ID | none — found while triaging `.env` scanner noise in Fly logs |

## 1. Issue / gap identified

Every rider/driver session and refresh-token row was storing `172.16.x.x` — a Fly.io edge-proxy
address — as the client IP, instead of the real user's IP. Found while explaining a burst of
`GET /.env` 404s in the Fly logs: the uvicorn access lines printed the proxy IP as the peer,
which is the same value `routes/auth.py` was persisting.

## 2. Root cause

`routes/auth.py` derived the IP with slowapi's `get_remote_address(request)`, which returns
`request.client.host` — the socket peer and nothing else. Behind Fly's proxy the peer is always a
`172.16.x.x` edge address.

Uvicorn's `--proxy-headers` rewrite does not save this: it is started as plain
`uvicorn server:app --host :: --port ${PORT}` (`backend/fly.toml` `[processes]`), and uvicorn's
default `forwarded_allow_ips` is `127.0.0.1`. The peer is `172.16.x.x`, so the header is never
trusted and `request.client.host` stays the proxy. Nothing in the repo sets `FORWARDED_ALLOW_IPS`
(grepped: zero hits).

This is a **fork that drifted**, not a novel bug. `routes/admin/auth.py` was fixed earlier — it uses
`get_real_client_ip()` at all 5 of its own call sites, and `tests/test_async_limiter.py::test_admin_auth_does_not_construct_a_sync_slowapi_limiter`
pins it with `assert "get_remote_address(" not in source`. That guard was only ever pointed at the
admin twin, so the rider/driver twin kept the old behavior with nothing to catch it — exactly the
failure mode CLAUDE.md's pre-merge gate 10 describes.

## 3. Fix / remediation

Point the 5 rider/driver call sites at `get_real_client_ip()` from `utils/rate_limiter.py` — the
helper `routes/admin/auth.py` already uses. It prefers `CF-Connecting-IP` (which Cloudflare
overwrites and a client cannot forge), then `X-Real-IP`, then falls back to `get_ipaddr`. Drop the
now-orphaned `from slowapi.util import get_remote_address`. Extend the existing source-guard test to
cover both twins so they cannot drift apart again.

### Alternative considered (gate 10)

Set uvicorn `--forwarded-allow-ips='*'` so `request.client.host` is rewritten fleet-wide — one
infra change, fixes every consumer at once, no code diff. **Rejected:** that trusts the leftmost
`X-Forwarded-For`, which is fully client-supplied. `get_real_client_ip`'s own docstring says it
exists specifically to avoid that spoofable path (P2-7, C5). It would also silently change the
rate limiter's fallback key and the access-log format. Widening a trust boundary to fix an
audit-only field is the wrong trade; the chosen fix keeps the trust model and matches the pattern
already proven in the admin twin.

## 4. Risk & impact on existing functionality

**Blast radius: isolated — backend-only, one file, 5 call sites.**

Greps performed (non-test, repo-wide):
- `get_remote_address` → **only** `routes/auth.py` (import + 5 uses). No other backend module used it.
- `issue_refresh_token` → 10 call sites: 5 in `routes/auth.py` (this fix), 5 in `routes/admin/auth.py`
  (already correct, untouched).
- Readers of `refresh_tokens.ip` → `utils/refresh_tokens.py:416` and `:591`, both writing
  `"replayed_ip": row.get("ip")` into the reuse-detection audit row.
- `admin-dashboard` → no UI reads `refresh_tokens` or renders a session-IP column, so no frontend change.

What this touches, concretely:

- **Refresh-token reuse/replay detection** (`utils/refresh_tokens.py:416`, `:591`). This is the one
  place the value is read back. A token-theft alert previously logged `replayed_ip: 172.16.30.130`
  for every rider/driver — our own proxy — making the field useless for incident response. It will
  now carry the attacker's IP. **Improvement, not a regression**, but note the audit-row *shape* is
  unchanged; only the value gets more accurate.
- **No security decision consumes this value.** `is_new_device()` (`utils/refresh_tokens.py:200`)
  fingerprints on `user_agent` only, and its docstring states `ip` is deliberately excluded as "too
  unstable on mobile networks." Rate limiting never used this path — `default_limiter` is already
  keyed on `get_real_client_ip`. So no lockout, throttle, or anomaly check changes behavior.
- **Ride state machine / money / wallet / background loops:** untouched. No interaction.

Residual risk: if a request reaches Fly **directly**, bypassing Cloudflare, `CF-Connecting-IP` is
absent and the helper falls through to `X-Real-IP` / `get_ipaddr`. That is the pre-existing
direct-to-origin caveat already called out in `get_real_client_ip`'s docstring and is an infra
control (lock the Fly origin to Cloudflare), not something this diff changes. Worth noting the
scanner traffic that surfaced this is itself evidence origin-locking is not currently enforced.

### PIPEDA note — this does change what PII we retain

Worth stating plainly rather than burying: a Fly proxy address is not personal information; a real
client IP is. This diff therefore *starts* retaining a piece of PII in `refresh_tokens.ip` that was
previously being discarded by accident.

That is defensible under data minimization because the retention is bounded and purpose-tied, not
because the field is harmless:

- **Purpose** — session provenance and refresh-token theft detection, an explicit security purpose,
  and the column already existed for exactly that (`replayed_ip`, `utils/refresh_tokens.py:416`/`:591`).
  It is the accuracy of the value that was broken, not its justification.
- **Retention is NOT bounded for every row — corrected 2026-09-21, see below.** An earlier revision of
  this entry claimed the purge caps IP retention at ~60 days. That claim was wrong and is retracted.
  It is at least true that these rows are **not** caught by the 7-year regulatory trip-record hold,
  which covers ride records, not session rows.

> **⚠ CORRECTION — the retention bound asserted here does not hold (found by `spinr-security-auditor`).**
>
> Migration 50 (`migrations/50_pii_retention_purge.sql:212-223`) purged on **expiry**:
> `DELETE FROM refresh_tokens WHERE expires_at < cutoff` — every token, 30 days after it expired.
> Somewhere between migration 50 and 117 the filter silently changed and has been carried through
> every `CREATE OR REPLACE` since. The **live** definition
> (`migrations/434_fix_audit_logs_delete_trigger_conflict.sql:179-189`, the highest-numbered file
> redefining this function) purges on **revocation**:
> `DELETE FROM refresh_tokens WHERE revoked_at IS NOT NULL AND revoked_at < cutoff`.
>
> `revoked_at` is only ever stamped by an explicit action — rotation
> (`utils/refresh_tokens.py:191`), revoke (`:636`), logout-all (`:680`). Natural expiry does not
> stamp it: `lookup_refresh_token` (`:313-314`) returns `None` on an expired row and writes nothing.
> So a token issued and never used again — a rider who takes one ride and never reopens the app, an
> abandoned install — keeps `revoked_at = NULL` forever and **Step E never deletes it**.
>
> Before this diff that was a harmless bug: the row held a useless constant proxy address. **After
> this diff that same never-purged row holds the user's real, identifying IP with no ceiling.** This
> diff does not cause the gap, but it is what turns it into a live data-minimization problem.
>
> Two docs assert the original, no-longer-implemented policy and are now stale:
> `utils/retention_purge.py:19` and `docs/runbooks/data-retention.md:34`
> ("refresh_tokens hard-deleted at expires_at + 30 days grace").
>
> **Not fixed in this commit** — widening a live `DELETE` to cover rows it has never touched is a
> destructive, irreversible change to production data and is out of scope for an IP-resolution fix.
> Escalated to the repo owner for a decision; tracked as an open blocker on this PR. Do not read
> this entry as claiming the retention story is closed.
- **Not logged** — the value goes to a DB column only. CLAUDE.md's "never in logs/Sentry/analytics"
  list is unaffected; this adds no new log line, Sentry tag, or analytics field.
- **Consistent with the admin twin**, which has been retaining real admin IPs this way already.

## 5. User-experience effect

**Nobody — rider, driver, corporate admin, or internal admin — sees any difference.** No response
body, status code, header, screen, or notification changes. The value is written to a column that no
customer-facing or admin-facing surface renders today. Not visible mid-session to a rider mid-ride
or a driver online. No copy change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/auth.py` | 5× `get_remote_address(request)` → `get_real_client_ip(request)`; added the helper to both dual-import branches; removed the orphaned `from slowapi.util import get_remote_address` | Record the real client IP instead of the Fly edge proxy |
| `backend/tests/test_p1_token_refresh.py` | 6× `patch.object(auth_mod, "get_remote_address", ...)` retargeted to `"get_real_client_ip"` | `patch.object` raises `AttributeError` on a name the module no longer binds — these 6 tests would fail without this |
| `backend/tests/test_async_limiter.py` | New `test_rider_auth_resolves_real_client_ip_not_socket_peer` | Mirrors the existing admin-side guard so the two auth twins cannot drift apart again |

## 7. Before / after

```python
# Before — backend/routes/auth.py
from slowapi.util import get_remote_address
...
client_ip = get_remote_address(request)   # -> "172.16.30.130" (Fly edge proxy)
await issue_refresh_token(user["id"], audience="rider", user_agent=user_agent, ip=client_ip)
```

```python
# After — backend/routes/auth.py
from ..utils.rate_limiter import get_real_client_ip   # (and the top-level twin import)
...
client_ip = get_real_client_ip(request)   # -> CF-Connecting-IP, the real user
await issue_refresh_token(user["id"], audience="rider", user_agent=user_agent, ip=client_ip)
```

Concrete scenario — a stolen refresh token replayed from 203.0.113.9:

```
# Before: audit_logs row
{"action": "refresh_token_reuse", "replayed_ip": "172.16.30.130", ...}   # our own proxy; useless

# After
{"action": "refresh_token_reuse", "replayed_ip": "203.0.113.9", ...}     # the attacker
```

## 8. Rollback plan

`git revert` is sufficient and complete here, and this is the "genuinely isolated, low-risk" case
the template allows it for:

- **No live data is mutated or migrated.** No schema change, no backfill, no rewrite of existing
  rows. `refresh_tokens.ip` is write-on-insert; historical rows keep whatever they already had.
- **The column is nullable and length-capped at the writer** (`(ip or "")[:64] or None`,
  `utils/refresh_tokens.py:177`), so neither value shape can violate a constraint.
- **No money, ride-state, or insurance-period rows are involved**, so there is no data-level
  remediation to plan for.
- Reverting restores the previous value on subsequent logins only; rows written while the fix was
  live remain correct and are strictly more useful than what they replaced.

No feature flag: flagging this would mean carrying two IP-resolution paths through the auth hot path
to toggle an audit-only field that no user-visible surface reads — more risk than the change itself.

## 9. Verification performed

- [x] **Blast-radius grep performed** — `get_remote_address`, `issue_refresh_token`,
      `refresh_tokens.ip` readers, `FORWARDED_ALLOW_IPS`/`proxy_headers`, and an `admin-dashboard`
      sweep for any session/IP UI. All enumerated in §4.
- [x] **`ruff check`** — passed on all 3 changed files.
- [x] **`ruff format --check`** — "3 files already formatted".
- [x] **`python -m py_compile`** — all changed files plus `utils/rate_limiter.py` parse.
- [x] **Static verification script** (stdlib-only, since pytest could not be installed — see §10).
      7/7 pass: new guard assertions hold; the pre-existing admin guard still holds (not regressed);
      `get_real_client_ip` is a real module-level `def`; it is bound in **both** dual-import branches
      per the CLAUDE.md dual-import convention; the orphaned import is gone; all 6 test patch targets
      retargeted; exactly 5 production call sites converted.
- [x] **Reviewed against CLAUDE.md conventions** — dual-import pattern preserved; PIPEDA §"never in
      logs" is unaffected (the IP goes to a DB column, not a log line or Sentry event, and IP is not
      in that section's prohibited list); JWT trust model untouched; no error-swallowing introduced.
- [x] **CI executed the test suite — this PR's tests pass.** `backend-test` on `3f166f0`:
      `3 failed, 15210 passed`. `main`'s own tip at `38a3826` (this branch's base):
      `3 failed, 15209 passed`, with the **identical three failures** —
      `test_settings_loader_last_known.py::TestFailedReadDoesNotClobber` ×2 (tracked as ACTION_ITEMS
      C130) and `test_webhooks_main.py::TestStripeWebhookEventLogLevel::test_ignored_lifecycle_event_logs_debug_not_warning`
      (**untracked** — flagged on the PR). So `backend-test` is red on the base branch for reasons
      unrelated to this diff, and **this change introduces zero new failures**. The `15209 → 15210`
      delta is exactly the new guard test `test_rider_auth_resolves_real_client_ip_not_socket_peer`.
      That confirms what the static checks could only infer: the guard ran and passed, and the 6
      retargeted `patch.object(auth_mod, "get_real_client_ip", ...)` calls resolve — they would have
      raised `AttributeError` on a wrong patch target.
- [x] **`spinr-security-auditor`** run against the actual diff (gate 10). **Verdict: FIX BLOCKERS.**
      It confirmed the `routes/auth.py` change itself is sound, that `client_ip` feeds no security
      decision on any of the 5 paths, and that no other backend surface still resolves IP from the
      socket peer. It raised one BLOCKER (the retention-bound claim, retracted in §4 above — the
      audit was right and this entry was wrong) and one WARNING (origin-lock, §4 residual risk).
      Both are recorded below as open items; neither is a defect in this diff's code.
- [ ] **Manual repro in staging** — not done, see §10.
- [x] **Feature flag** — deliberately omitted; justified in §8.

## 10. What was NOT verified

- ~~**The pytest suite was never executed.**~~ **RESOLVED by CI — see §9.** It remains true that the
  suite could not be run in the authoring environment (pypi.org returns 403 at the agent proxy;
  `pip install -r requirements.txt` failed on `aiohappyeyeballs==2.6.1` with "from versions: none"),
  so every test claim in this entry was static until CI ran. CI has now executed it and both test
  files pass.
- **Not exercised against a live request.** No staging deploy, no real Cloudflare→Fly round trip. The
  claim that `CF-Connecting-IP` is present in production rests on `get_real_client_ip`'s docstring and
  on `routes/admin/auth.py` already depending on it in production — not on an observed header capture.
  If Cloudflare is somehow not in front of a given path, this silently falls back to `get_ipaddr`.
- **No production build run** — backend-only change, no `admin-dashboard`/`rider-app`/`driver-app`
  files touched, so the CLAUDE.md production-build requirement does not apply.
- **No visual-regression consideration** — no UI surface touched.
- **Two open items remain before merge, neither fixed here:**
  1. **BLOCKER — the `purge_pii_retention()` Step E gap** (§4 correction). Never-revoked refresh
     tokens are retained indefinitely, now holding real IPs. Needs a follow-up migration widening
     Step E (e.g. `OR (revoked_at IS NULL AND expires_at < cutoff)`) plus correcting the two stale
     doc references. Deliberately not bundled here: it is a destructive change to live production
     data and warrants its own review, dry-run, and rollback plan.
  2. **WARNING — Cloudflare-only origin lock is unverified.** `get_real_client_ip` trusts
     `CF-Connecting-IP`, which is only authoritative if the Fly origin refuses non-Cloudflare
     traffic. No allowlist exists in `backend/fly.toml` and no ACTION_ITEMS entry tracks it. This
     is pre-existing (`default_limiter` and admin auth already depend on it), but this diff extends
     the same assumption to `refresh_tokens.ip`, so a replay attacker could inject a false IP into
     their own incident record. Narrow, but new. Needs a tracked action item.
- **Historical rows not backfilled.** Every `refresh_tokens.ip` written before this deploy still holds
  a `172.16.x.x` proxy address. Any incident response reading rows older than this deploy must treat
  that column as unreliable. No backfill is possible — the real IP was never captured.

## 11. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow without the UX field filled in
