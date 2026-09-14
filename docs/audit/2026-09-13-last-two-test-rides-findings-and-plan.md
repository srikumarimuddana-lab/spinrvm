# Last two test rides: reviewed findings and plan

**PR:** [#5348](https://github.com/srikumarimuddana-lab/spinrvm/pull/5348)

**Date:** 2026-09-13 (Regina)
**Status:** code review and local verification complete; release/device follow-ups remain.

## Ride evidence

| Evidence | SPR-5BNURH | SPR-BCPJPV |
|---|---|---|
| Status | completed | completed |
| Planned distance | 2.06 km | 2.37 km |
| Stored actual distance | 2.796 km | 2.37 km |
| Stored GPS rows | 189 | 5 |
| In-progress points used at completion | healthy trace | 4 |
| Route gaps | 2 resolved | 1 resolved, 2 unresolved at completion |
| Insurance attribution | no identified defect | missing its own Period 2 |

Production queries were read-only. The cancelled intervening ride SPR-HLYUDM was
batch-offered but never accepted; the original report's “never dispatched” claim
was incorrect. Exact coordinates, addresses and personal identifiers are omitted.

## Findings and implemented fixes

**F1 — iOS process death: cause remains unknown.** Cold-start markers and the GPS
gap establish a restart. A serious thermal state is correlation, not causation.
Similar symptoms predate September 13 map changes, so no evidence supports
reverting those changes. Native Sentry capture and its build plugin already exist;
the original “no native capture” claim was incorrect. Installed build DSN, symbols,
release/update mapping and physical-device event delivery still require validation.
No explicit app reload call was found; native Expo error recovery is not excluded.

**F2 — session recovery: concrete defects fixed, incident causality inferred.**
The prior refresh token remained valid when a fresh login occurred. This does not
prove whether the installed client lost, could not read, or never persisted it.
PR fixes distinguish unreadable SecureStore from confirmed absence; preserve
credentials during pre-initialization 401 handling; require persistence before
publishing access/CSRF state; settle loading flags on unexpected refresh rejection;
and isolate auth rejection from unrelated startup steps in both mobile layouts.
Logout reports marker-write failure while clearing local identity and running
teardown. Cache cleanup failure still rejects after teardown; not all logout
failures are suppressed. Token-write failure normally resolves as a failed refresh,
not a rejection escaping initialization. Review corrected misleading comments.

**F3 — fallback distance is confirmed; a GPS-induced charge change is not.**
With fewer than five trip points, trip_distance.py intentionally uses planned
distance. Completion stores that value as actual distance. A review query confirmed
public.settings.fare_lock_enabled is currently true; the fare-lock branch retains
the booking-time fare. This current setting is not historical proof of its value
at completion. The evidence does not establish overcharging from this GPS loss.
Measurement provenance/disclosure needs a product decision; no fare, wallet,
Stripe, corporate billing or receipt implementation changed in this PR.

**F4 — insurance attribution: root cause fixed by migration 421.** Migration 253
treated any repeated period as a no-op even when ride identity changed. A new
Period 2/ride B now closes Period 2/ride A and appends B's interval. Null-safe
comparison preserves ordinary retries. Signature, service-only grants, retention
trigger and existing unique index remain intact. Historical rows are not reassigned.
Review found a real CI failure: main already contained migrations 419 and 420.
The unapplied migration was renumbered from 419 to 421 with its fixture references.
A timezone-dependent test assertion was corrected to compare aware datetime
instants. The unique index prevents multiple open intervals but does not order
delayed competing callers; that limitation predates this PR.

**F5 — unusual accuracy values: no interpolation in the traced ingestion path.**
The recorder copies native coords.accuracy and breadcrumbs persist the submitted
value. This supports a native/client-payload origin, not a spoofing conclusion;
all possible historical writers have not been excluded.

**F6 — token audience: taxonomy/policy gap remains.** Generic OTP issuance uses
rider audience even for driver-app login; the dedicated Firebase path differs.
Refresh accepts both while backend roles are re-read. Do not infer client surface
from role or change enforcement without a compatible client-binding design.

## Verification and review corrections

- Driver store/session teardown: **59 passed**, including eight added recovery,
  partial-persistence, CSRF and callback cases.
- Shared/rider API refresh: **20 passed**, including pre-init unreadable storage.
- Disposable PostgreSQL 17 direct-pool suite: **36 passed, 1 skipped** (optional
  psycopg3 dependency). Seven insurance tests exercise real constraints/retention.
- Original SQL repro failed against 253 and passed against the corrected body.
- Independent security and migration reviews found no introduced runtime blocker.
- Ruff checks and formatting passed for changed backend test files.
- [CI run 34799821238](https://github.com/srikumarimuddana-lab/spinrvm/actions/runs/34799821238)
  exported production Android/iOS JS for both apps at d1621b8f1.
- Required PR fields and safety/regulatory declarations are corrected in the PR
  publishing step; current final CI status is authoritative on the PR.

Local Postgres tests require a disposable TEST_DATABASE_URL; Windows used
PYTHONUTF8=1, PGCLIENTENCODING=UTF8 and PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 to avoid
unrelated global Python plugins and the cluster's ASCII client encoding.
No full backend dependency install, signed native build, device ride test or
visual comparison was performed during this review.

## Remaining release plan

- [x] Implement and test auth recovery/persistence fixes.
- [x] Review insurance fix and resolve migration sequencing.
- [x] Save findings and impact logs; continue on existing PR #5348.
- [ ] Apply migration 421 through the normal reviewed deployment process.
- [ ] Validate native crash delivery/symbolication on a non-production device,
  map the installed Expo update to source, and reproduce a monitored ride.
- [ ] Decide how to disclose/reconcile GPS-starved measurements; require separate
  money review if settlement behavior changes.
- [ ] Design compatible client-bound audience issuance/enforcement.
- [ ] Separately review an append-only historical insurance correction for
  SPR-BCPJPV; never reattribute the original interval.
- [ ] Complete final CI and device/release gates before merge/release.

No production write, deployment, live payment or historical correction was made.
