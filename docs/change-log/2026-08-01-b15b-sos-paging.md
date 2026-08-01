# Change Impact & Risk Log — B15(b) SOS on-call paging

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-01 |
| Author | Claude Code (spinr agent) |
| Surface(s) | backend |
| Domain (Sentry tag) | safety |
| PR / commit link | (filled in on PR open) |
| Related issue or gap ID | `ACTION_ITEMS.md` B15(b) |

## 1. Issue / gap identified

A successful rider/driver SOS trigger (`POST /{ride_id}/emergency`) today produces an admin
dashboard WS broadcast, a safety distribution-list email, and a `logger.critical()` line — none of
which reaches an on-call human who isn't actively watching the admin dashboard or a log stream.
There is no real on-call paging integration (PagerDuty/Opsgenie or equivalent) anywhere in the
backend.

## 2. Root cause

Never built. `.claude/context/domain-safety.md` previously described a PagerDuty fallback that
never existed in code (corrected 2026-08-01 in a separate doc-only change, `docs/change-log/2026-08-01-b15-doc-cleanup.md`,
PR #2991) — this is the follow-up that closes the actual capability gap the doc had incorrectly
claimed was already covered.

## 3. Fix / remediation

Product decision (relayed via engineering, not directly reviewed against design docs): **build
it**, shipped dark/disabled by default.

- New helper `backend/utils/safety_paging.py::page_on_call(incident)` — a single best-effort HTTP
  POST to a provider webhook, PagerDuty Events API v2 shape by default
  (`{"routing_key", "event_action": "trigger", "payload": {...}}`), provider-agnostic enough that
  pointing `sos_paging_webhook_url` at an Opsgenie (or other) webhook accepting/adapting that same
  shape is a config change, not a rewrite. Never raises; a failure returns `False` and is logged.
- Config lives in `app_settings` (`sos_paging_webhook_url`, `sos_paging_routing_key`) — same
  pattern as Stripe/Twilio/Meta credentials (CLAUDE.md "Settings in DB"), so it rotates without a
  redeploy. **Defaults to disabled**: an empty `sos_paging_webhook_url` (the shipped default, since
  no real PagerDuty/Opsgenie account exists yet to configure it against) makes `page_on_call` log
  at debug and return `False` with zero HTTP calls.
- Wired into `trigger_emergency` (`backend/routes/rides/safety.py`) as a new best-effort,
  non-blocking side effect, in its own try/except, placed immediately after the existing
  `notify_safety_team` call (which is where the WS-broadcast/email/log fan-out already lives) and
  before the emergency-contact SMS loop — matching that function's existing error-handling posture
  for every other SOS side effect (a failure here is logged and swallowed, never re-raised, never
  blocks the response).
- **Deliberately not wired into `notify_safety_team` (`backend/features.py`)** even though that is
  where the WS/email/log trio actually lives, because `notify_safety_team` is also called by
  `routes/safety.py`'s non-urgent `/safety/report` endpoint and by
  `utils/safety_checkin_loop.py`'s missed-check-in escalation. Putting paging there would silently
  extend on-call paging to every filed incident report and every missed check-in escalation, which
  is a broader behavior change than "page on a triggered SOS" and was not part of this decision —
  see Risk section.
- `app_settings` schema (`backend/schemas.py::AppSettings`) and the admin settings API
  (`backend/routes/admin/settings.py`) gained the two new fields: `sos_paging_webhook_url` is
  plain (visible on GET), `sos_paging_routing_key` is masked like other credentials
  (`_CREDENTIAL_FIELDS`). Changing either field is gated `super_admin`-only
  (`_SUPER_ADMIN_ONLY_FIELDS`) — mirrors the `lms_api_base_url`/`lms_api_key` gate, since
  repointing the webhook destination is an SSRF + exfiltration risk (the payload carries ride_id,
  reported_by_user_id, and a geohashed area). `sos_paging_webhook_url` also requires `https://`
  (localhost excepted), mirroring the `lms_api_base_url` validator.
- `.claude/context/domain-safety.md` updated to describe the new channel as a step in the SOS flow,
  explicitly marked dark/disabled until an admin configures real credentials, plus a note that
  B15(a) (non-DB-dependent fallback) was decided **not** to be built.

## 4. Risk & impact on existing functionality

- **Blast radius: contained to the SOS-trigger endpoint.** `page_on_call` is a new module with no
  existing callers to break. The only existing code path touched is `trigger_emergency` in
  `backend/routes/rides/safety.py`, where one new best-effort try/except block was added; no
  existing statement in that function was modified or reordered relative to each other (the new
  block is inserted, not interleaved).
- **Grep performed for other consumers of the touched surfaces:**
  - `notify_safety_team` (the function I deliberately did *not* add paging to) is called from 3
    places: `backend/routes/rides/safety.py::trigger_emergency` (this endpoint),
    `backend/routes/safety.py`'s `POST /safety/report` (non-urgent incident reports), and
    `backend/utils/safety_checkin_loop.py`'s escalation path (missed ride check-in). None of these
    other two call sites are touched by this change — they still produce exactly the same
    WS/email/log trio as before, with no paging. This was a deliberate scope decision (see
    Fix/remediation), not an oversight.
  - `backend/routes/rides/_deps.py` and `backend/routes/rides/__init__.py` re-export a large flat
    namespace of names for the `rides` package (dual-import + backward-compat pattern already used
    by every other cross-cutting helper in this package, e.g. `notify_safety_team`,
    `sign_offer_card_token`). `page_sos_on_call` was added to both files' import lists and both
    `__all__`/import blocks, following the existing pattern exactly — grepped for every other name
    added this way (`notify_safety_team`, `sign_offer_card_token`) to confirm the two-file pattern
    is universal in this package before replicating it.
  - `backend/schemas.py::AppSettings` is read by `settings_loader.get_app_settings()`, which is the
    single source of truth used across dispatch, fare, corporate, AI, and now safety_paging code.
    Adding two new optional string fields with empty-string defaults is additive only — every
    existing reader that does `settings.get("some_other_key")` is unaffected; nothing reads
    `sos_paging_*` today except the new module itself.
  - `backend/routes/admin/settings.py::SettingsUpdateRequest` is the admin-dashboard settings save
    contract; it already uses `extra="ignore"` and `model_dump(exclude_none=True)`, so an admin
    frontend that doesn't yet have a UI field for `sos_paging_webhook_url`/`sos_paging_routing_key`
    simply never sends them — no behavior change for any existing settings-page save until the
    admin-dashboard UI (not part of this change) adds inputs for them.
- **Could this regress a flow that currently works?** No currently-working flow calls
  `page_on_call` — it is net-new code with a single net-new call site. The pre-existing SOS
  response shape (`{success, incident_id, contacts_notified, ...}`) is unchanged; `page_on_call`'s
  return value is not surfaced in the response at all (deliberately — it's an internal
  best-effort side channel, matching how `notify_safety_team`'s return value is also not surfaced).
- **Background loops / state machine / money:** none touched. This is a pure additive
  notification side effect with no read/write against `rides`, `safety_incidents` beyond the
  pre-existing insert (unchanged), driver/rider state, or any wallet/Stripe path.
- **Failure mode if paging itself misbehaves:** worst case with `sos_paging_webhook_url`
  unconfigured (the shipped default) is zero behavior change — `page_on_call` returns immediately
  without an HTTP call. Worst case once configured and the provider is unreachable/slow: the 5s
  httpx timeout inside `page_on_call`'s own try/except plus the outer try/except in
  `trigger_emergency` bound the worst-case added latency to ~5s on the SOS request itself, since
  the call is currently awaited synchronously in-line rather than backgrounded — see "What was NOT
  verified" below, this is a known, accepted latency trade-off for a dark-shipped first cut, not
  fixed in this change.

## 5. User-experience effect

None visible to rider, driver, or corporate admin — this is a backend-only, admin/on-call-facing
change. No app copy, no new UI, no notification the rider/driver sees. The only visible effect is
to an internal on-call responder, and only once an admin configures real credentials (which nobody
has done yet — this ships dark). Not visible mid-session to anyone using the rider/driver apps.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/safety_paging.py` | New file: `page_on_call`, `get_config`, `SosPagingConfig` | New best-effort on-call paging helper |
| `backend/routes/rides/safety.py` | Added `page_sos_on_call` import from `_deps`; added one new try/except block in `trigger_emergency` calling it after `notify_safety_team` | Wire paging into the SOS trigger path |
| `backend/routes/rides/_deps.py` | Added dual-import (`try`/`except ImportError`) for `page_on_call as page_sos_on_call` | Follow this package's existing dependency-injection pattern |
| `backend/routes/rides/__init__.py` | Added `page_sos_on_call` to the `_deps` re-export list and `__all__` | Match how every other `_deps` symbol (e.g. `notify_safety_team`) is re-exported for backward compat |
| `backend/schemas.py` | Added `sos_paging_webhook_url: str = ""`, `sos_paging_routing_key: str = ""` to `AppSettings` | New app_settings-backed config, defaults to disabled |
| `backend/routes/admin/settings.py` | Added both fields to `SettingsUpdateRequest`; added `sos_paging_routing_key` to `_CREDENTIAL_FIELDS`; added both to `_SUPER_ADMIN_ONLY_FIELDS`; added an `https://`-required validator for `sos_paging_webhook_url` | Admin-dashboard settings API surface + credential masking + privilege gate |
| `.claude/context/domain-safety.md` | Documented the new paging step (marked dark/disabled by default); updated the B15(a) hard-rule note to record the "not building it" decision | Keep the domain doc accurate per the same standard B15's earlier doc-cleanup enforced |
| `backend/tests/test_sos_paging.py` | New file: unit tests for `safety_paging.py` + integration tests for `trigger_emergency`'s new call | Coverage per CLAUDE.md testing conventions |
| `backend/tests/test_admin_settings_lms_gate.py` | Added SOS-paging-field tests mirroring the existing LMS-field tests in the same file | Coverage for the new masking/privilege-gate/validator behavior |

## 7. Before / after

`trigger_emergency` (`backend/routes/rides/safety.py`) — additive only, no existing statement's
behavior changed:

```python
# Before
    try:
        await notify_safety_team(incident)
    except Exception:
        logger.error(...)

    # Notify emergency contacts via SMS ...
```

```python
# After
    try:
        await notify_safety_team(incident)
    except Exception:
        logger.error(...)

    try:
        await page_sos_on_call(incident)
    except Exception:
        logger.error(...)

    # Notify emergency contacts via SMS ...
```

## 8. Rollback plan

**Config-only, no deploy needed for the primary risk:** the feature is already shipped disabled
(`sos_paging_webhook_url` empty). If it is later configured and misbehaves (e.g. floods a provider,
leaks something unexpected, or a provider outage adds latency to the SOS endpoint), a `super_admin`
clears `sos_paging_webhook_url` back to empty via the admin settings API — takes effect within the
existing 60s `app_settings` cache TTL (`settings_loader.py`), no redeploy, no migration.

If the code itself needs to come out (e.g. a bug in `page_on_call` beyond what the try/except
protects against): `git revert` is sufficient here specifically because this change writes no data
and moves no money — the only "state" involved is the optional `app_settings` row, which is
additive and inert until configured. This is the explicit exception CLAUDE.md's rollback guidance
allows ("acceptable only for genuinely isolated, low-risk changes"): no Stripe charge, wallet
delta, or ride-state transition is ever touched by this code path.

## 9. Verification performed

- [x] Automated tests run — unit + integration, `/tmp/spinr-venv` (pre-existing venv with
      `backend/requirements.txt` installed):
  - `pytest backend/tests/test_sos_paging.py backend/tests/test_p2_sos.py -q --no-cov` → 24 passed
  - `pytest backend/tests/test_e2e_sos_flow.py backend/tests/test_sos_expired_token.py backend/tests/test_safety_checkin_loop.py backend/tests/test_safety_notify_import.py backend/tests/test_p3_addresses_favorites_safety_disputes.py -q --no-cov` → 63 passed
  - `pytest backend/tests/test_admin_settings_lms_gate.py backend/tests/test_admin_business_logic.py backend/tests/test_ai_admin_settings.py -q --no-cov` → 68 passed
  - `pytest backend/tests/test_schema_contract.py -q --no-cov` → part of the above, passed
  - Full `backend/tests/` suite run before push — see PR description / final verification note for
    the pass/fail summary (recorded there once it completes, this file predates that run finishing).
- [ ] Manual repro steps followed in staging — **not performed**, see "What was NOT verified."
- [x] Blast-radius grep performed — listed in full under "Risk & impact" above (`notify_safety_team`
  call sites, `_deps.py`/`__init__.py` re-export pattern, `AppSettings` readers,
  `SettingsUpdateRequest` consumers).
- [x] Reviewed against relevant CLAUDE.md conventions: "Settings in DB" (app_settings pattern for
  Stripe/Twilio/Meta credentials, followed exactly), "Do not silently swallow errors" (this module
  is a deliberate, narrow, documented exception to that rule for the same reason
  `utils/meta_capi.py` already is — a non-critical side channel that must never break a safety-
  critical primary flow — and says so in its own module docstring), PIPEDA logging rules (IDs +
  geohash only, verified in `test_posts_pagerduty_shaped_payload_when_configured`).
- [x] Feature-flagged: yes, via `app_settings.sos_paging_webhook_url` (empty = disabled), per
  CLAUDE.md's existing app_settings-as-flag convention. Ships dark.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (clear `sos_paging_webhook_url`, no deploy; or
  `git revert`, safe because no data/money is written by this code path).
- [x] Blast radius is stated, not assumed (see section 4).
- [x] No silent behavior change to an already-shipped flow — the change is additive-only and, in
  its shipped (unconfigured) state, produces zero observable difference in the SOS flow's runtime
  behavior; the "User-experience effect" field above states this explicitly rather than leaving it
  implied.

## What was NOT verified

- **No real PagerDuty or Opsgenie account exists to test against.** Only the HTTP call shape
  (PagerDuty Events API v2 JSON structure) and mocked httpx responses (2xx / non-2xx / transport
  exception) were verified in `backend/tests/test_sos_paging.py`. The payload has never been sent
  to a real PagerDuty endpoint, so there is no confirmation a real PagerDuty integration would
  actually accept it, page correctly, or that the `dedup_key` behaves as expected against a live
  service.
- **Not tested against live Supabase** — `app_settings` reads/writes were exercised only through
  the `mock_supabase_client`-style patching already used throughout this test suite (direct
  `AsyncMock` patches of `get_app_settings`), not a real Supabase instance.
- **No staging deploy or manual repro was performed** — this PR is opened as a draft specifically
  so it is not merged/deployed without further review, per the task's explicit instruction not to
  merge.
- **Latency impact of a slow/hanging provider on the SOS response was not load-tested.** The 5s
  httpx timeout bounds it in theory; it was not measured under a simulated slow endpoint (only an
  instant-exception transport failure was exercised in tests, not a slow-but-eventually-responding
  one).
- **No visual/UI verification** — there is no admin-dashboard UI for the two new settings fields in
  this change (only the backend API surface). An admin would need to set them via direct API call
  or a future dashboard UI addition; this was not built or verified here.
