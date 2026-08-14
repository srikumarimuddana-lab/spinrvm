# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-14 |
| Author | Claude (agent) for vikas@ngitservices.com |
| Surface(s) | backend (migrations, production schema), admin-dashboard (company portal) |
| Domain (Sentry tag) | corporate / dispatch (fare/address) |
| PR / commit link | branch `claude/portal-otp-bypass-testing-60bqjz` |
| Related issue or gap ID | "book a ride in the corporate portal ... database operation failed" + "address should be sorted with minimum latency ... based on the customer location" |

## 1. Issue / gap identified

Two independent, unrelated problems in the corporate portal's ride-booking
flow:

1. Every booking attempt failed with "database operation failed."
2. Address autocomplete (pickup/dropoff) searched unrestricted across all of
   Canada — typing "airport" from a Regina-based company could surface
   Saskatoon's, Calgary's, or Toronto's airport ahead of (or instead of)
   Regina's, since no location context was ever sent to the search.

## 2. Root cause

**(1) — genuinely broken production schema, not a code bug.** Traced the
exact INSERT payload `services/company_booking_service.py::create_company_guest_booking`
builds and confirmed via direct production queries that two columns it sets
— `rides.corporate_member_id` and `rides.shared_trip_token_created_at` —
did not exist on the live `rides` table:
- `corporate_member_id`: migration `36_rides_corporate_member_id.sql`
  exists in-repo (committed long ago) and should have added it, but never
  actually applied to this production database — the same class of gap as
  `ACTION_ITEMS.md` C22 (found in an earlier session: `scripts/migrate.py`'s
  bookkeeping table doesn't match what's live, so the runner can't reliably
  track/apply migrations against this project).
- `shared_trip_token_created_at`: **no migration for this column exists
  anywhere in the repo at all** — a genuine authoring gap, not a drift gap.
  Three call sites (`company_booking_service.py`, `guest_notification_service.py`,
  `routes/rides/sharing.py`) read/write it assuming it exists. The sibling
  column `shared_trip_token` DOES exist live with no creating migration
  either — both were evidently added to production out-of-band at some
  point, and whoever did it missed this one.

Either missing column alone causes PostgREST to reject the INSERT outright
(schema-cache mismatch), which `run_sync`'s wrapper turns into the generic
`DatabaseError("Database operation failed")` sentinel — exactly the
reported symptom. Both are set on **every** corporate guest booking
(`corporate_member_id` always; `shared_trip_token_created_at` on every
immediate, non-scheduled booking), so this was a hard, 100%-reproducible
failure, not intermittent.

**(2) — a real, closeable gap, not a design flaw.** The backend's rider-
facing maps proxy (`routes/maps_proxy.py::places_autocomplete`) already
supports a `location`/`radius` param that Google's Places API (New) turns
into a **hard `locationRestriction`** (not just ranking bias — see
`utils/google_places_new.py::build_autocomplete_payload`), plus
`includedRegionCodes: ["ca"]` and distance re-sorting. The rider app
(`rider-app/app/search-destination.tsx`) already uses this exact pattern —
its own code comment documents the precise failure mode being fixed here
("no location= param and Google returns Canada-wide results"). The
corporate portal's booking page (`admin-dashboard/.../book/page.tsx`) simply
never adopted it — its autocomplete call sent `input` only, no location
context at all.

## 3. Decision

For (1): user confirmed applying both missing columns directly to
production (same pattern as an earlier session's fix), given they're
additive/nullable/reversible and the migration runner can't be trusted
against this project right now (C22).

For (2): implemented the fix directly — this is a clear, scoped,
low-risk UX gap matching an already-shipped, proven pattern elsewhere in
the codebase, not a new design requiring product sign-off. See §10 for my
assessment of the broader "service area region" restriction idea, offered
as requested but not implemented in this pass.

## 4. Fix / remediation

**(1) Database schema:**
- Re-applied migration `36_rides_corporate_member_id.sql`'s SQL directly to
  production (`ADD COLUMN IF NOT EXISTS corporate_member_id UUID REFERENCES
  corporate_members(id) ON DELETE SET NULL` + partial index). File itself
  unchanged (already committed).
- Authored and applied new migration `313_rides_shared_trip_token_created_at.sql`
  (`ADD COLUMN IF NOT EXISTS shared_trip_token_created_at TIMESTAMPTZ`) —
  the column genuinely never had a migration; this is the first one.

**(2) Address autocomplete bias (`admin-dashboard/.../book/page.tsx`):**
- `AddressPicker` now accepts a `bias: {lat, lng, radiusMeters} | null` prop,
  forwarded as `location=`/`radius=` query params on the autocomplete call.
- Pickup is biased by the booker's browser geolocation (requested once on
  page load, non-blocking, silently unbiased if denied/unavailable — no
  permission-prompt gating of typing).
- Dropoff is biased by the **selected pickup's coordinates** once chosen
  (falling back to geolocation before a pickup is picked) — mirrors
  rider-app's "search near the other leg" pattern exactly.
- Added a request-sequence guard so a slow, superseded autocomplete response
  can't overwrite a newer one's results (out-of-order network replies on a
  fast typer).
- Fixed an adjacent UX gap found while in this code: a fare-estimate
  failure (most commonly a pickup/dropoff outside every configured service
  area) previously cleared silently with no explanation — the spinner just
  disappeared. Now shows a specific message distinguishing "outside service
  area" from a generic estimate failure.

## 5. Risk & impact on existing functionality

**Schema fixes:**
- Both columns are nullable, no default, no backfill — zero risk to
  existing `rides` rows or any other read path. Verified (via
  `spinr-migration-reviewer`) that no code parses a full `rides` row through
  a strict/`extra=forbid` Pydantic model — reads are plain dicts via
  PostgREST, which is permissive to schema growth by construction. No other
  code path can break from these columns now existing.
- Spot-checked every other key `company_booking_service.py` sets on the
  same insert (`planned_distance_km`, `area_fees_breakdown`,
  `area_fees_total`, `tax_amount`, `tax_breakdown`, `grand_total`,
  `guest_booking`, `service_area_id`, `corporate_account_id`) against both
  live schema (this session) and in-repo migration history
  (`spinr-migration-reviewer`) — all confirmed present. No further schema
  gaps found in this specific insert path.

**Portal address-bias fix:**
- Isolated to one page component (`AddressPicker` has no other consumer —
  grepped). No backend change, no API contract change — `location`/`radius`
  were already accepted, optional params on the existing endpoint.
- Geolocation is browser-permission-gated and silently degrades to
  unbiased search on denial/timeout — never blocks typing, never shows a
  permission-prompt-related error state.
- No PII/security concern: the booker's own coarse location, used only to
  scope a same-request autocomplete call, never persisted, never sent
  anywhere but this one proxied Google Places call the backend already
  makes.

## 6. User-experience effect

**Corporate-admin/booker facing, immediate on both fixes.**
- Booking a ride in the corporate portal now succeeds instead of failing
  outright.
- Typing an address now surfaces geographically relevant results first
  (Regina airport for a Regina-anchored search, not Canada-wide).
- A pickup/dropoff outside the service area now shows an explanatory
  message instead of a silently-vanishing spinner.

## 7. Before / after

```python
# Before — migration 36 exists in-repo but was never live; this column
# genuinely didn't exist on production at all:
# (no migration for shared_trip_token_created_at exists anywhere)
```

```sql
-- After
ALTER TABLE rides ADD COLUMN IF NOT EXISTS corporate_member_id UUID
    REFERENCES corporate_members(id) ON DELETE SET NULL;               -- (re-applied 36)
ALTER TABLE rides ADD COLUMN IF NOT EXISTS shared_trip_token_created_at
    TIMESTAMPTZ;                                                        -- (new, 313)
```

```typescript
// Before
`/api/v1/maps/places/autocomplete?input=${encodeURIComponent(text)}`

// After
const qs = new URLSearchParams({ input: text });
if (bias) {
    qs.set("location", `${bias.lat},${bias.lng}`);
    qs.set("radius", String(Math.round(bias.radiusMeters)));
}
`/api/v1/maps/places/autocomplete?${qs.toString()}`
```

## 8. Rollback plan

Schema: `ALTER TABLE rides DROP COLUMN IF EXISTS corporate_member_id;` /
`... DROP COLUMN IF EXISTS shared_trip_token_created_at;` — safe at any
time, both are purely additive and nothing else depends on their existence
beyond the call sites that already null-guard them.

Frontend: `git revert` — pure client code, no data mutation.

## 9. Verification performed

- [x] Independently confirmed both columns now exist live (`information_schema.columns`,
  `corporate_member_id` → `uuid`/`udt_name: uuid`; `shared_trip_token_created_at` → present)
  via direct Supabase queries after applying.
- [x] `pytest backend/tests/test_company_guest_booking.py -q --no-cov` → 9
  passed (schema-only change, mocked-Supabase tests unaffected — confirms
  the application code's contract was always correct; only the live schema
  was missing the columns).
- [x] New frontend test `book/page.test.tsx` (2 tests) — proves the pickup
  search sends the booker's geolocation as `location=`/`radius=`, and the
  dropoff search is anchored on the **selected pickup's coordinates**
  (not just geolocation) once chosen.
- [x] Independently verified both new tests fail on pre-fix `page.tsx` with
  the exact predicted "no location param sent" failure, then pass post-fix.
- [x] Full `admin-dashboard` vitest suite: `256 passed` (28 files) — no
  regressions in any other `AddressPicker`/`companyRequest` consumer.
- [x] `npx tsc --noEmit` → clean. `npx eslint` on the changed page → same 7
  pre-existing warnings, 0 errors, 0 new warnings introduced (confirmed by
  diffing lint output before/after).
- [x] **Full production build** (`npm run build`) → succeeded.
- [x] **`spinr-migration-reviewer` review** (schema side) — verdict SAFE TO
  MERGE for migration 313. Independently confirmed both columns are
  genuinely referenced in the insert payload, confirmed no strict/typed
  Pydantic model parses a full `rides` row (so the new columns can't break
  any read path), and spot-checked the other insert keys against in-repo
  migration history. Two process notes, both addressed here: (a) the PR
  should say explicitly the SQL was already applied out-of-band ahead of
  merge (see §3/§4 above); (b) the reviewer's own sandbox had no DB access
  to re-verify live column existence itself — already independently
  confirmed by this session directly against production (see first bullet
  above).

## 10. My thoughts on the proposed location-bias / service-area-restriction approach

You asked specifically for my view on: bias by customer location so a
partial match like "airport" resolves to the locally relevant one, and
restrict "any other address type" to the service area region rather than
other cities.

**What I implemented matches the first half almost exactly**, and reuses
machinery already proven in production (rider-app). A `location`+`radius`
param isn't just a ranking hint here — `build_autocomplete_payload` turns it
into Google's `locationRestriction`, a **hard geographic filter**, capped
at Google's 50km maximum. For Saskatchewan's current city-by-city
footprint (Regina and Saskatoon are ~250km apart), a 50km circle around the
booker already excludes the wrong city's airport entirely, not just
ranks it lower — which is most of what "restrict to the service area, not
other cities" is actually asking for, achieved with zero new backend work.

**Where a 50km circle and a real service-area boundary can still diverge**
(worth knowing, not urgent): a circle isn't a service-area polygon. In a
city with an irregular or oddly-shaped service boundary, a 50km circle
could technically include a pocket outside where Spinr actually operates,
or (less likely at 50km) exclude a served address right at the edge. Today
this is a non-issue at Spinr's scale, but if the service area ever grows to
overlapping/adjacent regions, or the operating boundary becomes deliberately
irregular, a truer fix would restrict autocomplete to the **actual
`service_areas` polygon/bounds** for whichever area the booker's location
resolves to (the backend already has this concept — `features.py`'s
`get_service_area_polygon` and the parent/child airport-zone model — just
not wired into Places autocomplete today). That's a real v2, not something
I'd recommend building speculatively before there's a concrete case it's
needed for.

**One nuance specific to corporate booking** I want to flag rather than
silently resolve: "the customer's location" and "the booker's location"
aren't always the same person. A desk employee booking a ride *for* a
customer standing at the airport isn't physically there themselves. What I
built biases by the **booker's own** location (their browser's geolocation)
— the same assumption rider-app makes for a self-booking rider, which is
correct there since booker == rider. For the corporate case this is a
reasonable default (a company's bookings cluster around its own city in
practice) but not perfectly correct in the "booking for someone in a
different city" case. I did not build a "pick the customer's city first"
selector — that's a real product decision (extra step in every booking vs.
correctness in an edge case) I'd want you to weigh in on rather than assume.

## What was NOT verified

- Not tested end-to-end against a real browser + real Google Places
  response for either fix — verified at the unit/integration-mock level.
- Whether any *other* insert path in the codebase (beyond
  `company_booking_service.py`'s specific corporate guest-booking flow) also
  references either missing column in a way this session didn't check —
  the three call sites for `shared_trip_token_created_at` and the one for
  `corporate_member_id` were the only ones grepped; scoped intentionally to
  the reported bug's actual code path.
- The deeper service-area-polygon restriction discussed in §10 — explicitly
  not implemented, offered as an assessment only per the request.
