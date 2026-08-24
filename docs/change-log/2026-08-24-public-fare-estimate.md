# Change Impact & Risk Log — anonymous fare estimate for the website

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-24 |
| Author | Claude (session: srikumarimuddana@gmail.com) |
| Surface(s) | backend (consumed by the separate `desktop_website` repo) |
| Domain (Sentry tag) | payments |
| PR / commit link | branch `claude/spinrvm-faq-legal-api-ioiryg` |
| Related issue or gap ID | — (requested directly: real fares on the website) |

## 1. Issue / gap identified

The spinr.ca trip estimator priced trips in the website's own JavaScript from
hardcoded constants — `MIN_PER_KM 1.2`, `MAX_PER_KM 2.0`, `MIN_FARE 4.0`,
`AIRPORT_SURCHARGE 2.0` — against an OSRM distance. Those numbers are invented.
They know nothing about surge, per-area fees, tax, minimum fares or vehicle
type, so the range shown to a prospective rider had no defensible relationship
to what they would actually be charged.

## 2. Root cause

Not a regression. The website was built with no connection to this backend, and
`POST /rides/estimate` requires an authenticated rider, so there was no way to
quote a real fare to an anonymous visitor. The constants were the workaround.

## 3. Fix / remediation

`POST /api/v1/rides/public-estimate` — unauthenticated, flag-gated, reusing
`compute_ride_estimates`. Sharing the engine is the point: its own docstring
calls it the single fare path for every quoting surface, and a second
implementation on the website would drift from it the first time anyone touched
pricing.

Three differences from the authenticated route, each because the caller has no
account:

- **No surge-lock estimate token.** It is signed against a rider id and only
  means anything to `POST /rides`, which a website visitor cannot call. Minting
  one bound to a placeholder id would be a signed credential with no owner, so
  `compute_ride_estimates` gained an additive `issue_tokens` seam.
- **No price-search funnel row.** That table counts *rider* searches keyed on a
  user id; anonymous traffic would either corrupt it or need a fake id.
- **Live driver supply stripped.** `available` and `eta_minutes` stay (useful,
  and what every competitor shows); `driver_count` and `closest_driver_km` are
  internal operational data. `vehicle_type` is whitelisted rather than
  blocklisted, so a new internal column cannot leak by simply existing.

`PublicEstimateRequest` is deliberately narrower than the internal model: no
stops, no payment method, no corporate context. Those change the quote in ways
that only mean something for a signed-in rider, and accepting them anonymously
would let someone probe corporate pricing from a marketing page.

## 4. Risk & impact on existing functionality

**Blast radius: one shared function, one additive parameter.**

Blast-radius grep on everything touched:

- `compute_ride_estimates` — callers are `routes/rides/estimates.py::estimate_ride`
  (rider app), `ai/tools_booking.py`'s `get_fare_quote` (AI assistant), and now
  this route. The only change to the shared body is `issue_tokens`, which
  defaults True, so both existing callers are byte-identical.
- `sign_estimate_token` — now conditional. Unchanged for any caller that does
  not pass `issue_tokens=False`.
- `settings` table — additive column only.
- `utils/rate_limiter.py` — new limiter added; no existing limiter touched.
- Ride state machine, dispatch, wallets, Stripe, insurance periods and the 18
  background loops: **not touched.** No new loop.

**What could regress:** the honest answer is `compute_ride_estimates` itself —
it is the fare path for the rider app and the AI assistant, and it now has a
third caller. The mitigation is that the anonymous path adds a branch rather
than changing one, and the 683 existing fare/estimate/settings tests pass
alongside 14 new ones.

**Cost and abuse.** This is the first unauthenticated surface with a
per-request paid-API charge. Pricing needs the ROAD distance (crow-flies
undercharges — see `_PRICING_ROUTE_WAIT_S`), so a Google Directions call
happens on every cache miss whether or not a polyline is requested. Bounded by:
the `public_fare_estimate_enabled` kill switch, a 10/minute per-IP limit, and a
180 s cache on a ~110 m coordinate grid so a refresh or a nudged pin does not
re-bill. Per-IP keying is defeatable by rotating IPs — the kill switch and the
cache are the real controls, and the existing `MAPS_DAILY_BUDGET_USD` circuit
breaker is a further backstop on the Maps proxy path.

**Information exposure.** The endpoint publishes real pricing for arbitrary
coordinate pairs inside the service area. That is intentional — it is what a
fare estimator is — but it does make rate cards enumerable by a competitor.
Judged acceptable: the same information is already visible to anyone who
installs the app.

## 5. User-experience effect

**Nobody sees a difference yet** — `public_fare_estimate_enabled` defaults
false, so merging changes nothing until an admin flips it.

- Rider / driver: no change. The in-app quote path is untouched.
- Internal admin: one new toggle in Settings.
- Website visitor (once enabled): `/ride/estimate` shows the real fare per
  vehicle type on a map, instead of an invented range with no map.
- Nothing is visible mid-session to anyone already using the app.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/rides/estimates.py` | Added `issue_tokens` seam; new `PublicEstimateRequest`, `_public_estimate_view`, cache key helper and `POST /public-estimate` | Share the fare engine without minting ownerless tokens |
| `backend/routes/rides/_deps.py` | Re-export `public_estimate_limit` | Dual-import pattern |
| `backend/utils/rate_limiter.py` | New `public_estimate_limit` (10/min per IP) | Only bound available without an account |
| `backend/routes/admin/settings.py`, `backend/schemas.py` | New `public_fare_estimate_enabled` | Kill switch independent of the AI flag |
| `backend/migrations/364_settings_add_public_fare_estimate.sql` | `ADD COLUMN IF NOT EXISTS … DEFAULT false` | Without the column the first Settings save 503s (PGRST204) |
| `backend/tests/test_admin_settings_write_allowlist_drift.py` | Added the column to the snapshot | Required same-PR by that test's maintenance note |
| `backend/tests/test_public_fare_estimate.py` | New, 14 cases | Pin the public/internal boundary |

## 7. Before / after

The only behaviour-changing diff in the shared engine. Everything else is
additive.

```python
# Before — always signed, for every caller
estimate_token = _deps.sign_estimate_token(
    rider_id=rider_id, vehicle_type_id=vt_id, ...
)
```

```python
# After — the anonymous caller asks for none
estimate_token = None
if issue_tokens:
    estimate_token = _deps.sign_estimate_token(
        rider_id=rider_id, vehicle_type_id=vt_id, ...
    )
```

`issue_tokens` defaults True, so the rider app and the AI assistant take the
same branch they always did.

## 8. Rollback plan

**Flip `public_fare_estimate_enabled` to false** in admin → Settings. No
deploy, effective within the 60 s `settings_loader` TTL. The endpoint then
returns 503 before constructing a request or spending a Directions call, and
the website renders its "no price right now" state.

No data-level remediation is possible or needed: this path writes nothing
except a Redis cache entry that expires in 180 s. No ride, no money, no rows.

Migration 364 rollback, if ever wanted (not expected — the flag is the real
rollback):

```sql
ALTER TABLE settings DROP COLUMN IF EXISTS public_fare_estimate_enabled;
```

## 9. Verification performed

- [x] Automated tests: `tests/test_public_fare_estimate.py` (14 new, all pass);
      `pytest -k "estimate or fare or settings"` → **683 passed, 1 skipped**.
      `ruff check` + `ruff format` clean on every touched file.
- [x] Route registration verified against the live OpenAPI schema —
      `/api/v1/rides/public-estimate` is present.
- [x] Blast-radius grep: `compute_ride_estimates`, `sign_estimate_token`,
      `issue_tokens`, `track_search`, `include_polyline`, `_need_route`,
      settings write allowlist.
- [x] Reviewed against CLAUDE.md: money stays Decimal end-to-end (amounts are
      serialised by the existing `_money_str`, never re-derived); surge cap
      untouched; no float arithmetic added; dual-import pattern; observability
      (`spinr_public_estimate_total`, `spinr_fare_calc_duration_ms`);
      additive-over-destructive; ship-dark flag.
- [x] Feature-flagged: `public_fare_estimate_enabled`, default false.
- [x] Website side driven end to end over HTTP against a stub
      (`scripts/verify-spinr-integration.mjs` in `desktop_website`): 68 passed,
      0 failed, covering real price rendering, out-of-area, disabled flag, zero
      vehicle types, missing road route, out-of-range coordinates.
- [ ] Manual repro against live Supabase / real Google Directions — **not done**.

## 10. What was NOT verified

- **No real Google Directions call was ever made**, and no real fare was
  computed. Every test stubs the engine or the backend. The projection, the
  caching, the flag and the bounds are covered; the actual accuracy of a quote
  against a live service area is not.
- **Not run against live Supabase.** No real `service_areas`, `vehicle_types`
  or `fare_configs` rows were read, and migration 364 is committed, not applied.
- **The cost model is reasoned, not measured.** Cache hit rate at real traffic
  is unknown, so the per-day Directions spend once enabled is an estimate. Worth
  watching `spinr_public_estimate_total{outcome}` after flipping the flag.
- **No load test.** The 10/minute per-IP limit is a judgement call, not a
  number derived from observed abuse.
- **Rate-limit storage caveat applies.** With `RATE_LIMIT_REDIS_URL` unset the
  limiter is per-process, so on a multi-replica deploy the effective limit is
  10/minute × replicas. Pre-existing behaviour, but it matters more on a path
  that spends money.
- **A pre-existing product contradiction surfaced and is NOT resolved here:**
  the website's own copy says "No surge pricing" while this backend runs a
  surge engine with a 2.5× cap and returns `surge_multiplier` on every
  estimate. The website now states an elevated quote when there is one and
  stays silent otherwise, which is honest but is not an answer. Someone needs
  to decide which claim is true.
