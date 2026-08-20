# Decisions: Decide and, if warranted, implement a rideless/standalone SOS path

## Decision: Recommend the rider-only, flag-dark "smallest safe version" over full parity across both apps
**Stage:** Stage 1-2 (Ideation & Requirements)
**What was decided:** The chosen candidate approach closes the gap for rider-app only (unblocking the existing home-screen `SOSButton` when no ride is active) and explicitly defers driver-app. It does not attempt full parity in one pass.
**Why:** The two apps' current gaps are not the same size. On rider-app, the SOS button already renders on the home screen and only needs its existing `if (!rideId)` block replaced with a real call — a bounded, additive change to code that already exists. On driver-app, `SOSButton` is not rendered at all outside `navigating_to_pickup`/`arrived_at_pickup`/`trip_in_progress` — closing that gap means adding a brand-new always-visible SOS surface to the idle/online driver screens, a UI-placement and discoverability decision with no existing anchor point, not just an unblock. Bundling both into one "smallest safe version" would make the smaller, well-understood rider fix wait on a genuinely separate, larger design question. Splitting them lets the rider-side gap close without being held hostage to the driver-side design work.
**Alternative(s) considered:** Full-parity build (both apps in one pass) — rejected for the reason above: it inflates the immediate deliverable's risk and scope for a live-tested safety surface without a corresponding increase in how confidently the "smallest safe" bar is met. Do-nothing — rejected because this exact question has already gone through one round of "decided" siblings (B15(a) won't-build, B15(b) built) while (c) sat unresolved since 2026-08-01; recommending inaction again produces no new information and lets a real, documented, asymmetric safety gap keep sitting open.
**Reversible?** Yes, at this stage — this is a scoping recommendation for a build that has not started. If Stage 3/4 (Plan & Design/Architecture, Product+Engineering) later decides full parity is right after all, that's a normal scope change to make before Development starts, not a rollback of anything already shipped.

## Decision: Treat SMS/push copy wording and Trust & Safety's triage-runbook readiness as required human sign-offs, not something this run resolves itself
**Stage:** Stage 1-2 (Ideation & Requirements)
**What was decided:** Acceptance criterion 6 in the Requirements output names two specific open questions — (a) the exact wording sent to emergency contacts for a ride-less alert (it cannot reuse the current "...during a Spinr ride" phrasing, since that would be false), and (b) whether Trust & Safety's triage process needs any adjustment for incidents that carry less context than an in-ride one (no driver ID, no route trace, no second party) — and requires both to be explicitly answered by a human (Product + Trust & Safety) before the feature flag is ever enabled in any environment, dark-launch included.
**Why:** Both are genuine product/policy calls, not technical ones — domain-safety.md's hard rule ("never claim to replace emergency services," "never claim a notification the response does not support") means the exact SMS wording is safety-copy, and CLAUDE.md's own escalation rule ("Escalate, don't silently ship, when in doubt") plus GUARDRAILS.md's "if a stage isn't sure" guidance both point the same direction: I can identify that these questions exist and are load-bearing, but I have no basis — and no standing — to author final SOS copy or to declare a triage process adequate on my own read of the code. Making them explicit, named acceptance-criteria gates (rather than leaving them implicit inside "write good copy" or silently assuming the existing `/safety/report` triage process already covers this) is what keeps this from becoming a silent judgment call baked into a later diff.
**Alternative(s) considered:** Draft the SMS copy myself as part of Requirements and let a human just approve/reject it at Change Review. Rejected — that would make the default path "ship my wording unless someone objects," which inverts the burden GUARDRAILS.md wants for a live safety surface (a human decision come first, not a rubber stamp on generated copy). Assume the existing `/safety/report` triage process already covers the thinner-context case (since that endpoint already produces `ride_id = NULL` incidents today) and skip asking. Rejected because I could not verify, from what I read this session, whether that process is actually documented/followed for that case or just an accidental byproduct of nobody having built the urgent-alert side effects for it yet — asserting either way would be exactly the kind of unverified claim GUARDRAILS.md's "things the pipeline will not pretend it verified" section warns against.
**Reversible?** Yes — these are acceptance-criteria gates on an unbuilt feature, not commitments already acted on. A human answering both questions differently than any implicit default I might have picked simply changes what Stage 3 designs to; nothing needs to be rolled back.

## Decision: Duplicate `trigger_emergency`'s side-effect bundle in a new sibling function rather than extracting a shared helper

**Stage:** Stage 3-4 (Plan & Design / Architecture)
**What was decided:** The new ride-less endpoint will be a separate function in `backend/routes/rides/safety.py` that re-implements the same sequence of side effects (safety_incidents insert, admin WS broadcast, `notify_safety_team`, `page_sos_on_call`, confirmation push, emergency-contact SMS) rather than factoring the shared logic out of `trigger_emergency` into a common helper both functions call.
**Why:** `trigger_emergency` is a live, heavily-commented, incident-scarred function (its own comments reference two prior production bugs: the WS-broadcast client_id bug from PR #117, and the migration-315 idempotency gap) — CLAUDE.md's pre-merge gates ask for a stated blast radius and prefer additive changes on a live-tested surface, and "isolated, no other callers" is a strictly stronger claim than "refactored, verified callers still behave identically." Duplication means `trigger_emergency`'s diff for this whole piece of work is zero, so nothing about the in-ride SOS path needs re-verification at all. The cost is that a future change to one side-effect (e.g. new SMS provider) has to be made in two places — an accepted, named tradeoff, not an oversight.
**Alternative(s) considered:** Extract a private helper (e.g. `_dispatch_sos_side_effects(incident, ...)`) called by both `trigger_emergency` and the new function. Rejected for this stage: it is the more conventional refactor and would reduce future drift risk, but it necessarily changes `trigger_emergency`'s body — even as "pure code motion, no behavior change" (the same framing this very file already uses for its god-file-split heritage), that's still a diff on a function CLAUDE.md and GUARDRAILS.md both single out for extra caution, for a benefit (avoiding duplication) that doesn't rise to a requirement. If Stage 5 finds the duplication genuinely unwieldy once the real diff exists, revisiting this is a normal in-flight scope call, not a reversal of anything already shipped.
**Reversible?** Yes — this is an implementation-shape decision for code that doesn't exist yet. Stage 5 could choose the helper-extraction approach instead without needing to undo anything; the acceptance criteria don't mandate one shape over the other.

## Decision: Mount the new endpoint at `POST /rides/emergency` and widen `isSosUrl` in `shared/api/client.ts`, rather than putting the endpoint under `/safety/*`

**Stage:** Stage 3-4 (Plan & Design / Architecture)
**What was decided:** The new backend route lives in `backend/routes/rides/safety.py` (mounted under the `/rides` prefix, resolving to `POST /rides/emergency`) rather than in the top-level `backend/routes/safety.py` (prefix `/safety`, home to the existing ride-less `/safety/report`). The client-side `isSosUrl` regex in `shared/api/client.ts` is widened to also match this new bare path.
**Why:** Two things point the same direction. First, every side effect the new endpoint needs (`manager` for WS broadcast, `page_sos_on_call`, `send_push_notification`, `spawn`, `send_sms`, `get_app_settings`, `ride_action_limit`, `get_current_user_allow_expired`) is already imported once into `backend/routes/rides/_deps.py` and available to any function in the `rides` package for free; the top-level `backend/routes/safety.py` currently imports none of these and would need a second, parallel import block, duplicating plumbing for no benefit. Second, and more load-bearing: `shared/api/client.ts`'s 401-refresh-interceptor exemption (`isSosUrl`) is keyed on a URL-shape regex, not on a semantic "is this SOS" flag — whatever path this endpoint gets, that regex has to be taught about it, or acceptance criterion 1 ("never gated behind token refresh") silently fails for the new path while looking done everywhere else. Keeping the new route under the same `/rides/...emergency` family keeps the regex change small and easy to reason about (`/^\/rides\/(?:[^/]+\/)?emergency$/`) instead of adding a second, differently-shaped pattern to match a `/safety/*` path too.
**Alternative(s) considered:** `POST /safety/sos` or `POST /safety/emergency` under the existing ride-less `/safety/report` file. Rejected — semantically arguably cleaner ("this is a safety-reporting concern, not a rides concern"), but it would require duplicating the `_deps`-style import block, AND would require `isSosUrl` to match two unrelated URL shapes (`/rides/.../emergency` and `/safety/...`) instead of one family, which is a larger and more error-prone edit to security-relevant shared code for a naming preference.
**Reversible?** Yes, but with real cost if reversed after Stage 5 ships: once client code is calling a specific path and `isSosUrl` is matching it, moving the route later means coordinating a client route change with the interceptor regex again. Worth getting right at Architecture time rather than after Development, which is why it's decided now rather than left to Stage 5.

## Decision: Wire the new client-side flag through `rider-app/app/_layout.tsx`'s existing narrow-settings-fetch pattern, not through `shared/hooks/useSafetyPanelConfig.ts`

**Stage:** Stage 3-4 (Plan & Design / Architecture)
**What was decided:** `rideless_sos_enabled` will be read by extending the existing `GET /settings` fetch + React Context pattern already in `rider-app/app/_layout.tsx` (used today for `stripe_publishable_key`/`track_base_url`), rather than by calling the shared `useSafetyPanelConfig` hook from the home screen.
**Why:** `useSafetyPanelConfig` already fetches `/settings` and already exposes boolean tile-toggle flags in the same family (`showShareTrip`, `showReportIssue`), which made it the obvious first candidate. But it's designed around resolving a service area from `(lat, lng)` for the Safety-panel's local-authority row, isn't called from `app/(tabs)/index.tsx` today, and pulling it in only to read one unrelated boolean would add a second settings fetch plus a service-area/point-in-polygon dependency the home screen has no other reason to carry. The existing `_layout.tsx` pattern already does exactly one thing this flag needs — read `/settings` once at app start and make one value available lower in the tree — with no unrelated coupling.
**Alternative(s) considered:** Extend `useSafetyPanelConfig`'s return shape with `ridelessSosEnabled` and call the hook from `app/(tabs)/index.tsx`. Rejected for the reason above. Also considered a brand-new, purpose-built hook/store just for this one flag — rejected as needless proliferation when an existing, structurally-identical pattern in `_layout.tsx` already does the job.
**Reversible?** Yes — this is plumbing-only; if Stage 5 finds the Context-threading heavier than expected, switching to `useSafetyPanelConfig` (or a new dedicated hook) later doesn't touch the backend contract or the acceptance criteria at all.

## Decision: Implement `triggerRidelessEmergency` as a rideStore action calling `api.post` directly, not as a new function in `shared/api/client.ts`

**Stage:** Stage 5 (Development)
**What was decided:** The new client-side call to `POST /rides/emergency` is a new action inside `rider-app/store/rideStore.ts` (`triggerRidelessEmergency`), calling `api.post('/rides/emergency', payload)` directly — the same shape as the existing `triggerEmergency` action. It is not a new exported function inside `shared/api/client.ts`, even though Stage 3-4's file list described it that way ("new function, e.g. `triggerRidelessEmergency(...)`, POST /rides/emergency" under the `shared/api/client.ts` bullet).
**Why:** On opening `shared/api/client.ts` to add the function, `triggerEmergency` itself turned out not to exist there at all — it's implemented directly inside `rideStore.ts`, and `client.ts` only exports the generic `api` object (`get`/`post`/`put`/`patch`/`delete`) plus the `isSosUrl` exemption logic. Stage 3-4's design was correct about everything that actually matters functionally (the wire contract `POST /rides/emergency`, the `isSosUrl` regex widening, the retry-ladder ownership staying in `SOSButton`) — this is purely about which file one small function lives in. Matching the existing, established pattern (a store action calling `api.post` directly) keeps the two SOS actions symmetric and avoids introducing a second, different pattern for the exact same kind of call with no benefit.
**Alternative(s) considered:** Add a new exported wrapper function to `shared/api/client.ts` as Stage 3-4's file list literally described, then have `rideStore.ts` call that wrapper instead of `api.post` directly. Rejected — this would make `triggerRidelessEmergency` and `triggerEmergency` inconsistent with each other for no functional reason (one going through a `client.ts` wrapper, the other calling `api.post` directly), and would be new precedent-setting shared-client-code for a call shape that isn't shared-client-code anywhere else in the SOS surface today.
**Reversible?** Yes, trivially — this only affects which file a few lines of glue code live in. Nothing else in the design (the endpoint, the flag, `isSosUrl`, the `SOSButton` prop contract) depends on this choice either way.

## Decision: Draft placeholder SMS/push copy for the ride-less alert, clearly marked DRAFT pending required sign-off

**Stage:** Stage 5 (Development)
**What was decided:** `trigger_emergency_rideless`'s emergency-contact SMS body reads `"URGENT: {user_name} triggered an emergency alert via the Spinr app.{location_text} Call them or emergency services immediately."` (corrected from `trigger_emergency`'s `"...during a Spinr ride"`, which would be false for this path). The confirmation push copy is left byte-identical to `trigger_emergency`'s, since it never claimed anything ride-specific. Both are marked DRAFT in code comments, in the migration's `COMMENT ON COLUMN`, and in `progress-report.md`.
**Why:** Stage 1-2's decisions.md entry requires Product + Trust & Safety sign-off on this exact copy before the `rideless_sos_enabled` flag is ever turned on anywhere, dark-launch included — I have no standing to author final safety copy myself, matching that decision's own reasoning. But the endpoint has to send *some* string to compile, run, and be testable in its (permanently, until sign-off) flag-off dark-launched state — leaving a `TODO` placeholder or raising `NotImplementedError` would make the endpoint untestable and would violate CLAUDE.md's "do not silently swallow / half-implement" posture just as much as shipping unreviewed copy would violate the sign-off requirement. Writing a good-faith DRAFT that is clearly labeled as such, is safety-copy-compliant (never claims to replace 911, never claims more than the actual side effects deliver), and is trivially swappable via a one-line string change resolves both constraints: the code is complete and tested, and nobody can mistake this string for approved final copy.
**Alternative(s) considered:** Leave the SMS/push send unimplemented (stub/TODO) until sign-off lands. Rejected — this would make roughly a third of the new endpoint's side-effect bundle untestable in this stage, contradicting the "duplicate the full side-effect bundle" design Stage 3-4 already committed to, for a problem (unapproved copy shipping to production) that the flag-off default already fully prevents regardless of what the string says. Copy the in-ride SMS wording as-is despite the false "during a Spinr ride" claim. Rejected outright — that's exactly the inaccuracy Stage 1-2 flagged as the reason this needs a real copy pass in the first place.
**Reversible?** Yes, trivially — this is a string literal, not a structural choice. Changing it before the flag is ever turned on requires no migration, no client change, and no re-verification of anything else in the design.

---

## Change Impact & Risk Log

**Stage:** Stage 8 (Change Review)
**Filed because:** this change touches `safety` — one of CLAUDE.md's named live-tested surfaces (rides, dispatch, payments, auth, corporate, safety) — so a Change Impact & Risk Log entry is mandatory, not optional. See "Decision on whether this entry was required" below for the reasoning in full.

### Summary

| Field | Value |
|---|---|
| Date | 2026-08-20 |
| Author | Stage 8 (Change Review) pipeline run, on behalf of vikas@ngitservices.com |
| Surface(s) | backend, rider-app, shared (library code consumed by rider-app and driver-app) |
| Domain (Sentry tag) | safety |
| PR / commit link | commit `c162d9e80` on branch `claude/rideshare-team-roles-w8wazs` (no PR opened yet — Stage 8 precedes Release, which is where the draft PR gets opened) |
| Related issue or gap ID | `ACTION_ITEMS.md` B15(c) |

### 1. Issue / gap identified

A rider or driver who feels unsafe while *not* on an active ride (pre-booking, or after a ride has ended) has no in-app SOS path today — `trigger_emergency` requires a real `ride_id` and 404s without one, so the home-screen `SOSButton` falls into an `if (!rideId)` block that only tells the user to call 911 directly.

### 2. Root cause

`trigger_emergency` (`backend/routes/rides/safety.py`) was designed ride-scoped from the start: it takes `ride_id` as a required path parameter, derives the caller's role (`is_rider`/`is_driver`) from ride membership rather than from the user's own account, and links the resulting `safety_incidents` row to that ride. No urgent-alert side-effect bundle (SMS to emergency contacts, on-call paging, admin WS broadcast) previously existed for a ride-less trigger — the only ride-less path, `POST /safety/report`, is a deliberately non-urgent report endpoint with none of those side effects.

### 3. Fix / remediation

Added a new sibling endpoint, `trigger_emergency_rideless` on `POST /rides/emergency`, that duplicates `trigger_emergency`'s full side-effect bundle (safety_incidents insert with `ride_id=NULL`, admin WS broadcast, `notify_safety_team`, `page_sos_on_call`, confirmation push, emergency-contact SMS with corrected non-ride-specific copy) rather than modifying or refactoring the existing endpoint. Role is derived from the caller's own `is_driver` account flag instead of ride membership, since there is no ride. The endpoint is dark-launched behind a new `AppSettings.rideless_sos_enabled` flag (migration `350_rideless_sos_enabled_flag.sql`, default `false`), checked server-side first and fail-closed (404 when off) — not just gated by the client not calling it. Rider-app (via `RiderSOS`/`SOSButton`/`rideStore.ts`/`_layout.tsx`) was wired to call the new endpoint when the flag is on; driver-app and admin-dashboard were confirmed untouched. The flag ships **off** in this commit and nothing in the diff turns it on anywhere.

### 4. Risk & impact on existing functionality

- **`trigger_emergency` itself has a zero-line diff** — confirmed directly by reading the full function in Stage 6/7, not just trusting Stage 5's claim. The existing in-ride SOS path (the one actually exercised by live users today) is unmodified. Isolated.
- **`app_settings`/`settings` table and `AppSettings` schema** — new boolean column with a safe default. Every other reader (the 18 background loops in `core/lifespan.py`, dispatch, surge engine, corporate loops) reads keys it already knows about via `.get(key, default)` and is blind to an unrelated new key. This exact addition shape has shipped before (`driver_discreet_sos_enabled`, `idle_location_v2_enabled`, others) with no regression. Isolated.
- **`safety_incidents.category`** — new free-text value `sos_button_rideless`; confirmed no CHECK/enum constraint on that column. Other writers/readers checked directly: `backend/features.py`, `utils/safety_checkin_loop.py`, `utils/safety_paging.py`, `routes/admin/settings.py`, `routes/admin/safety.py`, `routes/safety.py`, the admin Safety-queue UI, and the admin manual-incident form — none branch on the set of valid category values, and the admin UI already renders a `—` placeholder for a falsy `ride_id` (proven by the pre-existing `/safety/report` path already producing `ride_id=NULL` rows). Isolated.
- **Migration-315 `sos_idempotency_key` unique index** — confirmed to have no `ride_id` component (`(reported_by_user_id, sos_idempotency_key) WHERE sos_idempotency_key IS NOT NULL`), so it is reused unmodified with no NULL-uniqueness gotcha. Isolated.
- **`shared/api/client.ts`'s `isSosUrl` regex — the single genuinely shared/security-relevant touch in this diff.** Widened from `/^\/rides\/[^/]+\/emergency$/` to `/^\/rides\/(?:[^/]+\/)?emergency$/` to keep the new bare path exempt from the 401-refresh interceptor (required — SOS must never be gated behind a token refresh). This is a **non-isolated** change in the sense that it is genuinely shared code, but its blast radius was checked directly: `isSosUrl` is a private, non-exported `const` used only inside this one file's `handleApiError`; its current beneficiaries are rider-app's `RiderSOS`/`rideStore.triggerEmergency` and driver-app's `useDriverSafetyTrigger.ts`, both already hitting the ride-scoped path (unaffected — still matches). Grepped the full route table and confirmed no other route in the codebase is shaped `/rides/.../emergency` or `/rides/emergency`, so the exemption set grows by exactly the one new real endpoint, not unboundedly. Flagged by all three of Stage 5/6/7 as the highest-care item in the diff.
- **`shared/components/SOSButton.tsx`** — two new optional props (`ridelessSosEnabled`, `onTriggerRideless`), additive. Every current mount site was enumerated: driver-app's direct mount and three of rider-app's four `RiderSOS`-mounting screens (`ride-in-progress.tsx` ×2, `driver-arriving.tsx`, `driver-arrived.tsx`) pass neither prop and are confirmed byte-identical — the pre-existing `riderSosNoInlineModal.test.tsx` test (which never touches the new props) still passes unchanged, proving this in practice rather than by inspection alone.
- **Blast radius, stated explicitly: cross-surface but bounded.** Backend (new route + flag plumbing across `schemas.py`/`routes/admin/settings.py`/`routes/settings.py`) + shared library (`isSosUrl`, `SOSButton`) + rider-app (`RiderSOS`, `_layout.tsx`, `(tabs)/index.tsx`, `rideStore.ts`). Driver-app and admin-dashboard were grepped independently in three separate stages (Dev, QA, Security) for every new symbol name and got zero hits each time — confirmed untouched, not assumed.
- **No interaction with money/wallet deltas, Stripe, or the ride state machine** — confirmed by grep (no `Decimal`/`float(`/`stripe` references in the new code) and by design (there is no `ride_id`, so no ride-state transition occurs).
- **No interaction with insurance-period classification** — the four periods are derived from ride state; this path has none, and the diff contains zero references to `driver_insurance_periods`. Correctly out of scope, not overlooked.
- **Real risk worth naming: fraud/abuse-surface shift.** Triggering the full urgent-alert side-effect bundle (SMS to a rider's own emergency contacts, on-call paging, admin broadcast) previously required an active ride to exist first; the new endpoint only requires being logged in (`get_current_user_allow_expired`), bounded by the same per-user 20/minute rate limit `trigger_emergency` already uses. This is inherent to the feature's stated purpose (serving a rider with *no* active ride) and was reviewed and accepted, not overlooked, by Stage 7 Security — but it is a genuine widening of who can cause the side-effect bundle to fire, and is exactly why the flag must stay off until Trust & Safety has assessed triage readiness for the "thinner context per incident" case (no driver ID, no route trace, no second party) named in acceptance criterion 6(b).
- **No dedicated per-endpoint rate-limit test exists** for `POST /rides/emergency` (confirmed as a real, named gap, shared with the sibling `trigger_emergency` test file — not new to this change, but not closed by it either).

### 5. User-experience effect

- **Nobody sees a difference today.** The flag defaults to `false` in the migration, the Pydantic schema, the admin PATCH model, the public `/settings` response, and the rider-app Context's initial state — confirmed by Stage 7 that every single read of the flag across the diff resolves to `false`/`False` and that no line anywhere in the diff sets it to `true`. Nothing in this commit turns it on in any environment.
- **If/when a human later enables the flag:** a rider on the home screen with no active ride would gain a working SOS button in place of today's "No Active Ride — Call 911 directly" block. This is additive to the home screen, not a modification of any in-progress-ride SOS behavior (`ride-in-progress.tsx`, `driver-arriving.tsx`, `driver-arrived.tsx` are all unaffected — they always have a real `rideId` and never receive the new props). It would not be a change visible mid-session to someone already using an existing SOS surface, since the SOS surfaces that exist today for an active ride are untouched.
- **Copy status:** the SMS body sent to emergency contacts is a labeled DRAFT ("URGENT: {user_name} triggered an emergency alert via the Spinr app...") — corrected from the in-ride endpoint's false-for-this-case "...during a Spinr ride" wording, but **not yet reviewed or approved by Product + Trust & Safety**. This is explicitly named in `decisions.md` as a required human sign-off gate before the flag may be enabled anywhere, dark-launch included. The confirmation push copy is unchanged from the existing, already-shipped in-ride wording (it never claimed anything ride-specific).
- Driver-app and admin-dashboard: no UX change, confirmed no code touched either surface.

### 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/350_rideless_sos_enabled_flag.sql` | New migration: `ADD COLUMN rideless_sos_enabled BOOLEAN NOT NULL DEFAULT false` on `settings` | New dark-launch flag, additive column |
| `backend/schemas.py` | Added `rideless_sos_enabled: bool = False` to `AppSettings` | Schema parity with the new column |
| `backend/routes/admin/settings.py` | Added `rideless_sos_enabled: Optional[bool] = None` to the admin PATCH model | Lets the flag be toggled without redeploy (existing `app_settings` pattern) |
| `backend/routes/settings.py` | `get_public_settings()` now returns the new flag | Client needs to read the flag value |
| `backend/routes/rides/safety.py` | New `RidelessEmergencyRequest` model + `trigger_emergency_rideless` on `POST /emergency` | The new endpoint itself; `trigger_emergency` unchanged |
| `backend/routes/rides/__init__.py` | Added two new names to the existing `from .safety import (...)` tuple | Route mounting |
| `backend/tests/test_sos_rideless.py` | New, 9 cases | Coverage for the new endpoint |
| `shared/api/client.ts` | Widened `isSosUrl` regex to also match the bare `/rides/emergency` path | Keeps new endpoint exempt from 401-refresh interceptor |
| `shared/components/SOSButton.tsx` | Added two optional props, extended retry-ladder branch | Lets the button call the new path when enabled |
| `rider-app/components/RiderSOS.tsx` | Forwards the two new optional props | Real integration point (home screen mounts this, not `SOSButton` directly) |
| `rider-app/app/_layout.tsx` | New `RidelessSosEnabledContext`, extended existing `/settings` fetch | Threads the flag value down to the home screen |
| `rider-app/app/(tabs)/index.tsx` | Consumes the new context, passes new props into the existing `<RiderSOS>` mount | Only site that gets the new capability |
| `rider-app/store/rideStore.ts` | New `triggerRidelessEmergency` action | Client call to the new endpoint |
| `rider-app/__tests__/SOSButton.test.tsx` | Extended with 4 new cases | Coverage for the new button branch |
| `rider-app/store/__tests__/rideStore.sos.test.ts` | Extended with 4 new cases | Coverage for the new store action |
| `agents/runs/sos-rideless-path/{progress-report,decisions,challenges-and-issues}.md` | Pipeline paper trail | Required by `PIPELINE_DESIGN.md` |

### 7. Before / after

Behavior-changing only when the two new `SOSButton` props are actually passed — which, in production, happens at exactly one call site (`rider-app/app/(tabs)/index.tsx`) and only takes a different code path once a human also turns the flag on. Every other caller is unaffected (props default to falsy/undefined).

```
# Before (shared/components/SOSButton.tsx, simplified)
if (!rideId) {
  showAlert("Emergency alert requires an active ride. Call 911 directly.");
  return;
}
// ...existing retry ladder calls onTrigger(rideId)
```

```
# After
const canTriggerRideless = !rideId && ridelessSosEnabled && !!onTriggerRideless;
if (!rideId && !canTriggerRideless) {
  showAlert("Emergency alert requires an active ride. Call 911 directly.");
  return;
}
// ...same retry ladder now calls onTriggerRideless() when canTriggerRideless,
// otherwise onTrigger(rideId) exactly as before
```

```
# Before (shared/api/client.ts)
const isSosUrl = (url: string) => /^\/rides\/[^/]+\/emergency$/.test(url);
```

```
# After
const isSosUrl = (url: string) => /^\/rides\/(?:[^/]+\/)?emergency$/.test(url);
```

### 8. Rollback plan

- **Primary path — flag is already off, no action needed.** `rideless_sos_enabled` defaults to `false` everywhere it's read (DB default, Pydantic default, public-settings response, rider-app Context initial state), and nothing in this commit sets it to `true`. If it is later turned on by a human and needs to come back off: `PATCH /api/admin/settings {"rideless_sos_enabled": false}` — no redeploy, matches this codebase's existing `app_settings`-in-DB dark-launch pattern (CLAUDE.md, "Settings in DB"). The endpoint itself re-checks the flag server-side first and 404s immediately, so flipping it off stops all side effects (SMS/paging/broadcast) on the very next request — this is a real, immediate rollback, not just a client-side change.
- **If the migration itself needs to be undone:** `ALTER TABLE public.settings DROP COLUMN rideless_sos_enabled` — safe to drop since it is a new, unreferenced-elsewhere column (confirmed no other code path reads it besides the ones added in this diff) and, while the flag stays off, no production data has ever been written that depends on the column existing.
- **If the whole feature needs to be pulled from the branch/PR:** `git revert` of commit `c162d9e80` is sufficient for the *code* — unlike a payments/wallet change, nothing in this diff writes to Stripe or a wallet balance, so there is no live-data remediation step beyond the code revert. The one caveat: if the flag were ever turned on in any environment and real `safety_incidents` rows were inserted before a revert, those rows are safety records and must **not** be deleted (append-only, matches the regulatory retention rule for safety incidents) — a code revert does not and must not touch already-inserted incident rows. This does not apply today since the flag has never been on.
- No scenario here requires a "redeploy is the only path" fallback — the flag-flip path covers the live-risk case, and the migration is a plain reversible additive column.

### 9. Verification performed

- [x] Automated tests run — unit only. 103 backend tests (`test_sos_rideless.py` + 5 neighboring SOS/settings files) independently re-run in Stage 6 and again referenced in Stage 7; 22 rider-app Jest tests (`SOSButton.test.tsx`, `riderSosNoInlineModal.test.tsx`, `rideStore.sos.test.ts`) re-run in Stage 6. No integration tests (real Supabase) and no e2e run.
- [ ] Manual repro steps followed in staging — **not done.** No staging deploy exists for this change; nothing was exercised against a real environment.
- [x] Blast-radius grep performed — see section 4 above and Stage 3-4/6/7 of `progress-report.md` for the specific grep commands and their results (`app_settings` readers, `safety_incidents.category` readers/writers, `isSosUrl` callers, `SOSButton` mount sites, driver-app/admin-dashboard symbol search).
- [x] Reviewed against relevant CLAUDE.md conventions — state machine (n/a, no ride state touched, confirmed), money (n/a, confirmed by grep), JWT trust model (verified: role derived from DB-backed `current_user["is_driver"]` on every `get_current_user*` path, never a JWT claim), PIPEDA/PII (Stage 7 reviewed every `logger.*` call site and the WS broadcast payload; no raw PII in logs). **Not explicitly reviewed:** RLS policy behavior for the new/adjacent tables (no stage's record shows an RLS-specific check), and whether any Sentry alert rule or Prometheus dashboard needs a new rule for the `sos_button_rideless` category (Stage 3-4 flagged this as not exhaustively ruled out).
- [x] Feature-flagged, user-visible and non-trivial — yes, `rideless_sos_enabled`, default off, fail-closed 404 server-side as defense in depth beyond the client gate.

### What was NOT verified

(Per CLAUDE.md's Change Impact Log field and GUARDRAILS.md's "things the pipeline will not pretend it verified" — carried forward from Stages 5/6/7, not re-derived, since Stage 8 did not re-run the suite itself.)

- **No production build** (`npm run build`/EAS equivalent) was run for rider-app or driver-app. `tsc --noEmit` + `jest` + `eslint` passing is stated explicitly as not equivalent, per CLAUDE.md's own pre-merge gate language. Admin-dashboard needed no code change so no build applies there.
- **No manual/simulator/device QA** of the actual SOS button in either flag state. This is a real gap for a customer-facing safety control on a screen with no existing automated visual-regression tooling (none exists in this repo for rider-app) — stated, not implied to be covered.
- **No live Supabase.** All tests mock `db_supabase`/`get_app_settings`/`send_sms`/`manager`. Migration 350 was never applied to any real database — no `DATABASE_URL` was available in this environment, and per GUARDRAILS.md the pipeline cannot apply migrations to production regardless. It was checked against migration 349's already-applied template and picked up correctly by the static `test_settings_column_parity.py` parser, which is not the same claim as a live `run_migrations.py --dry-run`.
- **No live Twilio/FCM.** SMS/push sends are mocked in every test; the DRAFT copy strings were read and reasoned about against `domain-safety.md`'s rules, not received on a real device.
- **No dedicated rate-limit test** against the new route specifically (21st-request-in-a-minute case) — a pre-existing gap shared with the sibling `trigger_emergency` test file, not newly introduced, but not closed either.
- **Whether the DRAFT SMS/push copy would pass real Product + Trust & Safety review**, and **whether the triage-runbook readiness question (acceptance criterion 6(b)) is resolved**, are both open human questions no stage in this pipeline has the standing to answer. Both remain open going into Stage 8's human-gate decision below.
- This Stage 8 review itself did not independently re-run the test suite or re-read the full diff line-by-line a fourth time — it relied on Stage 6 (QA) and Stage 7 (Security)'s independent re-verifications (each of which re-ran the tests itself rather than trusting Stage 5's numbers) plus a direct confirmation that commit `c162d9e80` exists on the named branch with the exact 18-file, line-count-matching file list `progress-report.md` claims (`git show --stat`, run directly in this stage). It did not re-run `pytest`/`tsc`/`jest` itself.

### 10. Sign-off

- [x] Rollback plan is concrete and testable — flag flip (already off by default; `PATCH /api/admin/settings` to force off if ever changed) plus a plain reversible `DROP COLUMN` migration rollback; no Stripe/wallet data is touched by this feature so a code revert is sufficient for the code itself.
- [x] Blast radius is stated, not assumed — see section 4; the one genuinely shared-code touch (`isSosUrl`) got the most explicit treatment across three independent stages.
- [x] No silent behavior change to an already-shipped flow without the UX field filled in — section 5 states plainly that nobody sees any difference today (flag off by construction) and describes exactly what would change if a human later enables it.

### Decision on whether this entry was required

**Required — and filed above.** CLAUDE.md's Change Impact Log mandate applies to "any commit or PR that fixes a bug, closes a gap, or changes existing behavior" touching "a live-tested surface (rides, dispatch, payments, auth, corporate, safety)." This change adds a new safety-domain endpoint, a new safety-adjacent settings flag, and new safety-UI wiring — it is unambiguously a `safety` surface change, so the entry is mandatory regardless of the flag defaulting off. A dark-launched, flag-off change is not exempt from the Log requirement — CLAUDE.md's pre-merge gates explicitly ask for a Change Impact Log entry *before* the feature-flag rollout question is even reached (gate 3 assumes flagging as the *mitigation*, not a reason to skip the Log). Filed per `docs/templates/CHANGE_IMPACT_LOG.md`'s exact field set above.
