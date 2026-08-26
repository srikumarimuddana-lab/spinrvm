# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-20 |
| Author | Claude (agent session), decision by product owner |
| Surface(s) | backend |
| Domain (Sentry tag) | auth |
| PR / commit link | (no PR opened per task instructions — pushed directly to `claude/spinr-mongodb-migration-u9y6iz`) |
| Related issue or gap ID | ACTION_ITEMS.md A41 (consent-basis gap); `docs/runbooks/legacy-migration-playbook.md` item #1 |

## 1. Issue / gap identified

A legal-sufficiency investigation
(`docs/audit/2026-08-20-legacy-consent-legal-sufficiency-factsheet.md`) found no evidence any
old-app user ever affirmatively accepted any legal text (no consent/acceptance field exists
anywhere in the old-app export's schema), and that the old app's legal text materially diverges
from Spinr's current text: a 1.3× surge cap in the old text vs. today's real 2.5×, different GPS
retention terms (old: 7yr full route; current: 90-day full trail / 3yr pickup-dropoff only), and
undisclosed subprocessors (Gemini, LogRocket appear in neither document). The product owner
decided, directly in this session, to re-run consent for both existing and new users rather than
wait on the legal-sufficiency judgment itself (which remains open, a counsel/business call).

## 2. Root cause

Not a bug — a deliberate legal/product decision responding to the fact-finding above. The
mechanism to act on that decision (`backend/routes/legacy_consent.py`'s `GET/POST
/consent/status|accept`) already existed, dark-shipped, gated on
`consent_version != CONSENT_VERSION` and on `app_settings.legacy_consent_notice_enabled` (default
`False`). The gate never fires until the backend-owned `CONSENT_VERSION` constant
(`backend/routes/auth.py`) is bumped to a value that genuinely differs from every currently-stored
`users.consent_version` — that bump had not happened yet.

## 3. Fix / remediation

Bumped `backend/routes/auth.py`'s `CONSENT_VERSION` constant:

```
consumer-tos-2026-01-draft  ->  consumer-tos-2026-08-v1
```

**Value reasoning:** kept the existing `<audience>-<doctype>-<year>-<month>-<qualifier>` naming
shape. Changed the qualifier from `draft` to `v1` because the underlying text is no longer a
draft — `docs/legal/terms-of-service.md` and `docs/legal/privacy-policy.md` were published live to
the `legal_documents` table (rider/tos, driver/tos, rider/privacy, driver/privacy rows, version 1)
on 2026-08-17, per `docs/legal/legal-text-publication-checklist.md`. `2026-08` ties the constant to
that real publication month (and to this session's 2026-08-20 re-consent decision, which is the
same month). This is a pure constant-value change — no new code paths, no new endpoints, no
migration.

**This alone does not put a re-consent prompt in front of any user.** Two other pieces gate the
actual user-visible effect and were deliberately **not** touched by this change:
1. `app_settings.legacy_consent_notice_enabled` — still `False` in the live DB; flipping it is a
   separate action, by a different actor, after this merges and deploys. Not a database write in
   scope for this task, and none was made.
2. A new-signup consent checkbox on the mobile signup screens (`rider-app/app/login.tsx`,
   driver-app equivalent) — reported to be a separate, parallel task on this same shared branch.
   Not touched here.

## 4. Risk & impact on existing functionality

**Blast-radius grep performed** — every reader/writer of `CONSENT_VERSION` and `consent_version`
across `backend/`, `rider-app/`, and `driver-app/` (31 files matched `consent_version`):

- **`backend/routes/auth.py`** — defines the constant; stamps it into the `new_user` dict at
  signup in two places (`verify_otp`'s new-rider branch, `firebase_auth_login`'s new-driver
  branch). Both are opaque writes — no parsing of the string's shape.
- **`backend/routes/legacy_consent.py`** — imports `CONSENT_VERSION` from `auth.py` (dual-import
  pattern), compares it for equality against `users.consent_version` in `_needs_notice()`, and
  writes it back on `POST /consent/accept`. Confirmed opaque-token usage only.
- **Tests** — `backend/tests/test_legacy_consent_notice.py`,
  `test_auth_remaining_endpoints.py`, `test_verify_otp_login_flow.py` all import and compare
  against the `CONSENT_VERSION` symbol, never a hardcoded literal of the old value — so no test
  edits were needed; all still pass against the new value.
- **`backend/schemas.py:585`** — a comment reference only, no code dependency.
- **`backend/migrations/334_users_consent_version.sql`** — the migration that added the
  `users.consent_version` column; mentions the old value only as a descriptive example in a SQL
  comment. Not edited (migrations are append-only per `backend/migrations/CLAUDE.md`; a merged
  migration's historical comment is not touched retroactively).
- **Two genuinely separate `CONSENT_VERSION` constants exist and were confirmed NOT to be the
  same one, per the task's explicit instruction to check:**
  - `backend/routes/marketing.py`'s `CONSENT_VERSION = "1"` gates the CASL marketing-channel
    opt-in consent version (`marketing_consents` table via `services/marketing_consent.py`) — a
    different legal basis (CASL, not PIPEDA ToS/Privacy) and a different column entirely. **Not
    touched.**
  - `backend/routes/drivers/crc_consent.py` / `backend/services/driver_crc_consent.py` use an
    integer-valued `consent_version` for CRC/Vulnerable-Sector-Check consent — also a fully
    separate system (background-check consent, not ToS/Privacy). **Not touched.**
- **Mobile apps** — `rider-app/app/otp.tsx` and `driver-app/app/otp.tsx` reference
  `consent_version` only in a comment ("a brand-new signup already gets a current
  `consent_version` stamped"); neither reads or depends on the literal string value. No mobile
  file was touched.

**Blast radius is isolated to `backend/routes/auth.py` (the constant definition and its two
signup call sites) plus the pre-existing consumer in `legacy_consent.py`.** No other code path
treats the specific string value as meaningful beyond an equality check — confirmed for every
reader found.

**Confirmed the new value genuinely differs from every currently-possible stored value:** the only
non-NULL `users.consent_version` values that can exist today are `NULL` (pre-migration-334 or
legacy-imported rows) or the old constant `consumer-tos-2026-01-draft` (every user whose signup
already ran through the pre-existing code). `consumer-tos-2026-08-v1` matches neither, so
`_needs_notice()` will correctly evaluate `True` for every existing user once the flag flips.

**Interaction with the 3-piece rollout:** this change is inert by itself. It becomes
user-visible only in combination with the separate `app_settings` flag flip (out of scope here,
by design) — see Section 8 for exactly how that combination behaves.

## 5. User-experience effect

**Not visible to any user from this change alone.** New signups get the new `CONSENT_VERSION`
value stamped silently at account creation — no UI difference, since the mobile apps already had
no client-supplied version to send and this has always been a backend-only stamp. Existing users
see nothing different until `app_settings.legacy_consent_notice_enabled` is separately flipped to
`true` (a different actor's action, after this merges/deploys) — at that point, on their next
fresh login / cold start / profile-setup completion, they will see the existing (already-built,
already-shipped) `legacy-consent-notice.tsx` screen in both apps, per
`docs/change-log/2026-08-19-legacy-consent-notice-mobile.md` and `-mobile-completion.md`. That
screen and its wiring are unchanged by this PR.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/auth.py` | `CONSENT_VERSION` constant value bumped `consumer-tos-2026-01-draft` -> `consumer-tos-2026-08-v1`; added a dated bump-history comment | Product owner's re-consent decision; ties to the real 2026-08-17 legal-text publication event |
| `ACTION_ITEMS.md` | Added a sub-bullet under A41's consent-basis-gap entry recording the decision, the version bump, and the two still-separate pieces (flag flip, signup checkbox) | Keep the production-readiness backlog current per repo convention |
| `docs/runbooks/legacy-migration-playbook.md` | Added a `[RE-VERIFIED 2026-08-20, LATER SAME DAY]` annotation under item #1 recording the same three-piece split | Follow existing annotation convention for this checklist item |
| `docs/change-log/2026-08-20-consent-version-bump-re-consent-rollout.md` | New file (this document) | Mandatory Change Impact & Risk Log for a user-visible, PIPEDA-relevant auth-surface change |

No test files required changes (see Section 9).

## 7. Before / after

```python
# Before (backend/routes/auth.py)
CONSENT_VERSION = "consumer-tos-2026-01-draft"
```

```python
# After (backend/routes/auth.py)
CONSENT_VERSION = "consumer-tos-2026-08-v1"
```

Effect on `_needs_notice()` in `backend/routes/legacy_consent.py` (unchanged code, changed input):

```python
def _needs_notice(user: dict) -> bool:
    return (user.get("consent_version") or None) != CONSENT_VERSION
```

- Before this bump: a user with `consent_version = "consumer-tos-2026-01-draft"` (i.e. everyone
  who already signed up under the old constant) evaluated `False` — no notice needed.
- After this bump: that same user now evaluates `True` — notice needed, once the separate flag is
  on. A brand-new user signing up today gets `consent_version = "consumer-tos-2026-08-v1"` stamped
  atomically at insert and evaluates `False` (correctly, no notice needed) immediately.

## 8. Rollback plan

Two independent ways to stop the user-visible effect, and they are not equivalent — be precise
about which is live at rollback time:

- **If `app_settings.legacy_consent_notice_enabled` is still `False` (the state immediately after
  this merges):** this commit's effect is completely inert. No rollback action is needed at all —
  simply not flipping the flag is sufficient to keep this dark. A `git revert` of this commit is
  optional cleanup, not an urgent rollback.
- **If the flag has since been flipped to `True` and existing users are actively being
  re-prompted, and a rollback is wanted:** revert `CONSENT_VERSION` back to
  `consumer-tos-2026-01-draft` in `backend/routes/auth.py` and redeploy. This makes
  `_needs_notice()` evaluate `False` again for every user who already had that old value stamped
  (the overwhelming majority — everyone who signed up before this bump), so the prompt stops
  appearing for them. It does **not** retroactively un-stamp any user who already tapped
  "Accept" under the new version in the interim — `consent_accepted_at`/`consent_version` rows
  written via `POST /consent/accept` are a genuine record of real user action and must not be
  mutated or deleted (no delete/mutate path exists for this reason, matching the append-only
  spirit already used for `driver_insurance_periods`). A revert only stops prompting people who
  have not yet accepted; it does not erase acceptances already collected.
- Flipping `app_settings.legacy_consent_notice_enabled` back to `False` is the faster, config-only
  rollback lever (no redeploy) if the concern is the notice itself rather than the specific
  version string, and is preferred over reverting this commit for anything urgent — it is the same
  flag this task was explicitly told not to touch, and remains the fastest kill switch regardless
  of which actor flips it.

## 9. Verification performed

- [x] Automated tests run: `backend/tests/test_legacy_consent_notice.py` (8),
  `test_auth_remaining_endpoints.py` (25), `test_verify_otp_login_flow.py` (14),
  `test_marketing_preferences.py` (2), `test_marketing_consent.py` (9),
  `test_routes_marketing_coverage.py` (21), `test_driver_crc_consent.py` (8) — **91 passed, 0
  failed** (`python3 -m pytest ... -q --no-cov`).
- [x] `ruff check backend/routes/auth.py` — all checks passed.
- [ ] Manual repro / staging check — **not performed**. See Section 10.
- [x] Blast-radius grep performed: `CONSENT_VERSION` (all definitions/imports) and
  `consent_version` (all readers/writers) across `backend/`, `rider-app/`, `driver-app/` — see
  Section 4 for the full file list and per-file disposition.
- [x] Reviewed against relevant `CLAUDE.md` conventions: PIPEDA ("consent language version is
  stored on signup. Material changes require re-consent"), dual-import pattern (unaffected —
  `legacy_consent.py`'s existing try/except import of `CONSENT_VERSION` needed no change), and the
  pre-merge release-gate rules (blast-radius check, additive-over-destructive, feature-flag
  already in place and left untouched).
- [x] Feature-flagged: yes, via the pre-existing `app_settings.legacy_consent_notice_enabled` —
  this change does not introduce a new flag because the flag it needs already exists and was
  deliberately left off.

## 10. What was NOT verified

- **This change alone does nothing visible.** No manual repro of the actual re-consent screen was
  performed or was possible from this task, because the gating flag
  (`app_settings.legacy_consent_notice_enabled`) is still `False` and this task was explicitly
  instructed not to touch it or any `app_settings`/database row. The only verified effect is at
  the unit-test level: `_needs_notice()` now returns `True` for a user carrying the old stamped
  version, confirmed by the existing test suite's use of the live `CONSENT_VERSION` symbol.
- **No simulator/device visual verification exists for the actual re-consent screen a user will
  eventually see.** This is not a new gap — it is the same standing gap already recorded in
  `docs/change-log/2026-08-19-legacy-consent-notice-mobile.md` and reconfirmed in
  `docs/runbooks/legacy-migration-playbook.md` item #1's prior `[RE-VERIFIED]` passes ("no
  simulator/device available this session — screens are unverified visually"). This task did not
  touch the screen and did not have simulator/device access either, so that gap remains exactly as
  wide as before.
- **Not tested against live Supabase** — only against `mock_supabase_client`-backed unit tests, per
  repo convention for this tier of change.
- **The new-signup consent checkbox's actual current state on the shared branch was not
  confirmed** — as of this change's base commit no checkbox markup exists in
  `rider-app/app/login.tsx`, but a parallel session is reported to be building it concurrently on
  the same branch and may have pushed since. Stated as unconfirmed rather than assumed either way.
- **The underlying legal-sufficiency judgment itself remains a business/counsel call**, unaffected
  by this change — this PR implements the product owner's rollout decision, not a legal
  determination that old-app consent was or wasn't sufficient.

## Sign-off

- [x] Rollback plan is concrete and testable (Section 8)
- [x] Blast radius is stated, not assumed (Section 4)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (Section
  5 — states explicitly that nothing changes for any live user until the separate flag flip)
