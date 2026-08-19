# Change Impact & Risk Log — Legacy/re-consent notice mechanism (backend)

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-19 |
| Author | Session user (vikas@ngitservices.com), implemented by Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | auth |
| PR / commit link | (this branch) |
| Related issue or gap ID | `docs/audit/2026-08-19-legacy-migration-data-quality-audit.md` (consent-basis blocker); LGL-3, `reports/audits/2026-07-22-legal-content-validation-v1.md` |

## 1. Issue / gap identified

Legacy-imported riders/drivers never went through any Spinr consent flow at all — not even the
honest "predates tracking" NULL state, since they never saw a consent screen of any kind.
Separately, and more broadly: no mechanism exists anywhere in the codebase to re-prompt *any*
user (imported or organic) for consent when policy terms materially change, despite CLAUDE.md's
PIPEDA section requiring one (flagged once already, 2026-07-22, as LGL-3, never closed).

## 2. Root cause

`users.consent_version`/`consent_accepted_at` (migration 334, shipped earlier today) are written
only on brand-new signups going forward. Nothing reads them. No endpoint or UI exists to detect
"this user's consent is missing/stale" or to record a fresh acceptance.

## 3. Fix / remediation

Added `backend/routes/legacy_consent.py`: `GET /consent/status` (returns `needs_notice` — true
whenever `users.consent_version != CONSENT_VERSION`, covering both a NULL value and a future
stale-but-present one) and `POST /consent/accept` (stamps the current `CONSENT_VERSION` +
timestamp). Deliberately generic — not "legacy-import-only" — so the same mechanism serves LGL-3
the next time `CONSENT_VERSION` (`routes/auth.py`) bumps for any reason.

**Dark-shipped**: gated on new `app_settings.legacy_consent_notice_enabled` (default `False`).
While off, `GET /consent/status` always reports `needs_notice: false` and `POST /consent/accept`
404s, regardless of the caller's actual consent state — no client can be shown, or record
acceptance of, a notice that isn't live yet.

**This subtask (backend only)**: no mobile UI wired to it yet (subtasks 4–5, separate PRs/commits).
Flipping the flag on today would be a no-op — nothing calls the endpoint.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** New route file, new router mount (`server.py`), one new
  `AppSettings` field (`schemas.py`, matches the existing `driver_discreet_sos_enabled`-style
  flag pattern exactly — no new migration, `settings` is a flexible key-value table).
- **Every other reader of `users.consent_version`/`consent_accepted_at`**: grepped — none exist
  outside `routes/auth.py`'s two write sites (today's earlier fix) and this new route. No
  existing code path can be affected by this endpoint's writes.
- **`app_settings.legacy_consent_notice_enabled`**: a brand-new key; `get_app_settings()`'s
  existing merge-with-defaults logic means every other settings reader is unaffected (extra keys
  are additive, confirmed by reading `settings_loader.py`).
- No migration, no money, no ride state machine, no WebSocket event, no background loop.
- **Not yet reachable from either app** — this PR/commit is backend-only by design (see plan).

## 5. User-experience effect

None yet — no UI calls this endpoint. When subtasks 4–5 ship and the flag is flipped on, a
legacy-imported or pre-tracking rider/driver will see a one-time notice on next app open. Not
mid-session-disruptive by design (checked once at app-load, not polled).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/legacy_consent.py` | New file — `GET /consent/status`, `POST /consent/accept` | New capability |
| `backend/server.py` | Import + mount the new router at `/api/v1/consent/*` | Wire it up |
| `backend/schemas.py` | Added `AppSettings.legacy_consent_notice_enabled: bool = False` | Dark-launch gate |
| `backend/tests/test_legacy_consent_notice.py` | New — 8 tests (flag off/on, null/current/stale version, 404 on missing user, accept stamps version, idempotent re-accept) | Coverage |

## 7. Before / after

Purely additive — no existing function's behavior changed.

## 8. Rollback plan

- **Code**: `git revert` — no other caller exists yet, safe.
- **If the flag is ever flipped on and then needs reverting**: flip `legacy_consent_notice_enabled`
  back to `false` in `app_settings` (no redeploy, per CLAUDE.md's flag-without-redeploy pattern).
  Any `consent_version`/`consent_accepted_at` already stamped by a real user acceptance is a true
  record of what happened and should NOT be rolled back — only the flag (gating future prompts)
  needs reverting, not past acceptances.

## 9. Verification performed

- [x] Automated tests: 8 new unit tests, all pass. Full-app boot smoke test — confirmed
  `/api/v1/consent/status` and `/api/v1/consent/accept` both register on `server.app.routes`.
  Regression check: `test_admin_settings_*.py`, `test_auth*.py`, `test_marketing_consent.py`,
  `test_verify_otp_login_flow.py` (191 tests) all still pass.
- [ ] Manual repro / staging — not performed, no live Supabase access this session.
- [x] Blast-radius grep performed (§4).
- [x] Reviewed against CLAUDE.md conventions: PIPEDA (this endpoint's whole purpose is closing a
  consent gap), dual-import pattern (followed, matches `marketing.py`'s exact shape), Settings-in-DB
  flag pattern (followed exactly).
- [x] Feature-flagged — `legacy_consent_notice_enabled`, default off.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (flag flip, no redeploy).
- [x] Blast radius is stated, not assumed.
- [x] No silent behavior change — nothing is user-visible yet; this commit is backend-only.

**Not yet done, deliberately out of scope for this commit**: mobile UI in rider-app/driver-app
(next subtasks), flipping the flag on in any environment.
