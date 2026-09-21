# Change Impact & Risk Log — public trip-share tracking blocked by App Check

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-21 |
| Author | mkkreddy52@gmail.com (via Claude Code) |
| Surface(s) | backend (fixes a rider-app / browser-facing failure) |
| Domain (Sentry tag) | safety |
| PR / commit link | branch `claude/tracking-availability-8ij1ty` |
| Related issue or gap ID | `SPINR_CODE_REVIEW.md` MAJOR finding #3; same bug class as commit `83495f5` (admin quests) |

## 1. Issue / gap identified

Opening a shared live-trip link (`track.spinr.ca/{token}`) rendered
**"Tracking unavailable — Load failed (api-spinr.spinr.ca)"** instead of the map.
Reported from live app testing with a screenshot, reproduced by the reporter in
both the in-app WebView and desktop Chrome. The shareable live-trip link is a
**safety** feature, so it was dead for every recipient in production.

## 2. Root cause

Two things compounding:

1. `GET /api/v1/rides/track/{share_token}` was missing from
   `_APP_CHECK_EXEMPT_PREFIXES` (`backend/core/middleware.py`). The tracking page
   is a Next.js browser surface and **can never** mint an `X-Firebase-AppCheck`
   header, so with `app_check_enforced()` true (defaults to `ENV == production`,
   `backend/core/config.py:459`) every poll was rejected 401 before the handler ran.
2. `FirebaseAppCheckMiddleware` returns a bare `JSONResponse(401)` that
   short-circuits **outside** `CORSMiddleware`. In `init_middleware`, CORS is
   registered first (line ~860) and App Check later (line ~897); in Starlette a
   later `add_middleware` call wraps further out, so CORS sits innermost and never
   sees the short-circuit. The 401 therefore carried no
   `Access-Control-Allow-Origin`, the browser blocked it, and `fetch()` rejected
   as an opaque network error — Safari `"Load failed"`, Chrome `"Failed to fetch"`.

(2) is why this was hard to spot: the page's own `!res.ok` branch
("Tracking link is invalid or has expired") never ran, because the response was
never readable. It looked like an outage, not an auth rejection.

Confirmation the backend was healthy throughout: the same screen had *already*
called `GET /api/v1/rides/{ride_id}/share` successfully to mint the token visible
in the URL bar — that route is also App-Check-enforced, and the mobile app can
attach the header. App worked, browser did not.

## 3. Fix / remediation

Add `"/api/v1/rides/track/"` to `_APP_CHECK_EXEMPT_PREFIXES`. App Check was never
what protected this endpoint: `track_shared_ride` is declared public and is
authorised by an unguessable `secrets.token_urlsafe(32)` share token (~43 URL-safe
chars) that the handler expires after 24 hours
(`backend/routes/rides/sharing.py:230-253`).

**Alternative considered** (CLAUDE.md gate 10): re-mount the handler under an
already-exempt namespace, as commit `83495f5` did for admin quests. Rejected — that
fix moved a *family* of endpoints into `/api/admin/`, which was already exempt and
module-gated. Here a path change would break every share link already delivered by
SMS and push (`share_url = f"{TRACKING_BASE_URL}/{share_token}"`,
`sharing.py:110,175`) and would require a lock-stepped Vercel deploy, since the
deployed frontend hardcodes the `/api/v1/rides/track/` path. The exemption is
purely additive and invalidates no outstanding link.

## 4. Risk & impact on existing functionality

**Blast radius: isolated (single middleware tuple, one route).**

- Blast-radius grep performed on `_APP_CHECK_EXEMPT_PREFIXES`: consumers are
  `backend/core/middleware.py:382` (the only production read) plus six test files
  (`test_appcheck_portal_exempt.py`, `test_p1_cors.py`, `test_branding_route.py`,
  `test_quests.py`, `test_admin_surge_auto_mount.py`, and the new one). Verified
  the existing negative assertions still hold — `test_appcheck_portal_exempt.py`
  asserts `not exempt("/api/v1/rides")` and `test_p1_cors.py` asserts
  `not exempt("/api/v1/drivers/payouts")`; neither matches the new prefix.
- **No auth bypass.** Every route in `backend/routes/rides/` was scanned; `GET
  /track/{share_token}` is the **only** one without `Depends(get_current_user)`.
  App Check is an attestation layer, not an authorisation one.
- **Prefix collision, accepted.** The match is `str.startswith`, so a ride_id of
  the literal string `"track"` also lands inside the prefix. Which handler a
  collision reaches depends on router registration order, and it is **not**
  uniformly the sibling handler — an earlier draft of this entry said it was, and
  a `spinr-security-auditor` pass caught that. The real behaviour
  (`routes/rides/__init__.py` assembles 14 sub-routers; `sharing` is 5th):
  - `GET /rides/track/share` and `/rides/track/shared-contacts` reach the
    **sibling** handler, because `sharing.py` defines `/{ride_id}/share` (line 41)
    and `/{ride_id}/shared-contacts` (line 210) *before* `/track/{share_token}`
    (line 221). Both keep `Depends(get_current_user)` → 401 without a JWT.
  - Every other GET sibling (`receipt`, `chat-status`, `live-route`,
    `navigation-steps`, …) sits in a router registered *after* `sharing`, so
    `/track/{share_token}` matches first and **`track_shared_ride` handles it** —
    looking up a share token literally named `"receipt"`, finding none, 404.
  - POST/PATCH/DELETE siblings never collide: `track` is GET-only.

  Either resolution is safe — nothing unauthenticated becomes reachable that was
  not already — but the distinction is now pinned by tests that resolve these
  paths through the **real router** rather than asserting on source text, so a
  future reordering surfaces as a failing test instead of a silent change.
- No change to the ride state machine, money/wallet deltas, or any background loop.
- Other middleware on this path were checked and need no change: `ForcedUpgrade`
  passes through when `X-App-Platform`/`X-App-Version` are absent
  (`middleware.py:342-345`) and `CSRFMiddleware` skips safe methods
  (`middleware.py:631`) — a browser `GET` clears both. App Check was the sole blocker.

## 5. User-experience effect

- **Rider and their shared contacts:** the shared live-trip link starts working.
  Recipients (who may be neither Spinr users nor app installs) currently see a dead
  page; after this they see the live map, driver card and ETA.
- **Visible mid-session: yes, and that is the point** — the feature is used *during*
  a live ride. A rider mid-trip who shares their trip gets a working link
  immediately after deploy. No client rebuild required; the page re-polls every 5 s.
- No copy changes. No change to what data the endpoint returns — payload is
  byte-identical, only reachability changes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/core/middleware.py` | Added `"/api/v1/rides/track/"` to `_APP_CHECK_EXEMPT_PREFIXES`, with a comment covering the CORS-ordering failure mode and the prefix-safety argument | The browser tracking page cannot mint an App Check token; the 401 was killing a safety feature |
| `backend/tests/test_appcheck_public_tracking_exempt.py` | New regression test | Pins that a token-less GET to the tracking path reaches the handler under enforcement, and that every sibling rides route stays enforced |

## 7. Before / after

```python
# Before — /api/v1/rides/* fell through to the default: App Check required.
_APP_CHECK_EXEMPT_PREFIXES = (
    ...
    "/api/v1/auth/send-otp",
    "/api/v1/auth/verify-otp",
)
# GET /api/v1/rides/track/<token>  ->  401 {"detail": "App Check token required"}
# ...emitted outside CORSMiddleware, so no Access-Control-Allow-Origin ->
#    browser reports an opaque network error, page shows "Tracking unavailable".
```

```python
# After
_APP_CHECK_EXEMPT_PREFIXES = (
    ...
    "/api/v1/auth/send-otp",
    "/api/v1/auth/verify-otp",
    # (comment block: browser surface, share-token auth, CORS-ordering, prefix safety)
    "/api/v1/rides/track/",
)
# GET /api/v1/rides/track/<token>  ->  reaches track_shared_ride, which enforces
#    token validity + the 24h expiry and 404s otherwise (a 404 IS readable by the
#    browser, because route-level exceptions pass back out through CORSMiddleware).
```

## 8. Rollback plan

Revert the one tuple entry and redeploy. This is acceptable as the sole rollback
path here, and the "no second deploy" bar does not apply, because:

- The change writes **no data** — no migration, no Stripe call, no wallet delta, no
  ride-state or insurance-period row. There is nothing applied to live data to
  remediate.
- It is a pure reachability widening of one read-only `GET`. Reverting restores the
  exact prior behaviour (the endpoint becomes unreachable from browsers again),
  with no residue.

Faster mitigation if the endpoint itself ever needs to go dark without a deploy:
point `app_settings.track_base_url` away (or blank it), which makes the rider app
stop generating links — `fetchTrackingUrl` fails closed with "Live trip tracking is
not set up yet" (`rider-app/app/ride-tracking-webview.tsx:180-184`). That does not
invalidate already-sent links, so it is a partial mitigation, not a true rollback.

## 9. Verification performed

- [x] **Blast-radius grep performed** — `_APP_CHECK_EXEMPT_PREFIXES` consumers (6
      test files + 1 production read); every route under `backend/routes/rides/`
      scanned for a missing auth dependency; `ForcedUpgradeMiddleware` and
      `CSRFMiddleware` dispatch paths read to confirm neither blocks a browser GET.
- [x] **Exemption logic verified standalone** — an AST-based script extracted the
      real tuple from source and replayed the middleware's exact matching
      expression (`any(path.startswith(p) for p in ...)`): the tracking path is
      exempt; `/api/v1/rides`, `/api/v1/rides/active`, `/api/v1/rides/history`,
      `/api/v1/rides/{id}`, `/api/v1/rides/{id}/share`, `/api/v1/rides/{id}/cancel`,
      `/api/v1/rides/{id}/receipt`, `/api/v1/rides/tracking`, `/api/v1/rides/track`,
      `/api/v1/auth/refresh`, `/api/v1/auth/logout` and `/api/v1/drivers/payouts`
      all remain enforced.
- [x] **Reviewed against CLAUDE.md conventions** — auth/JWT trust model, PIPEDA
      (payload unchanged), observability (no new error path), and the
      "escalate, don't silently ship" gate.
- [x] **Adversarial review** — `spinr-security-auditor` run against the actual diff.
- [ ] Automated tests run — **NOT RUN**, see below.
- [ ] Manual repro in staging — **NOT DONE**, see below.
- [ ] Feature-flagged — not applicable; restores intended behaviour of an existing
      shipped feature rather than introducing new UX. Flagging a middleware
      allowlist entry would add a config branch to an auth path for no benefit.

## 10. What was NOT verified

Stating the boundary explicitly, per CLAUDE.md:

- **The test suite was never executed.** `pytest` is not installed in this session's
  container and `pip install` cannot reach PyPI (network egress is proxy-blocked),
  so `backend/tests/test_appcheck_public_tracking_exempt.py` is **written but
  unrun**. Its prefix-matching assertions were independently confirmed by the
  standalone AST script above, but the `FirebaseAppCheckMiddleware.dispatch()`
  behavioural assertions (the `_dispatch_without_token` helper) have **not** been
  executed even once. **CI must be green before this is trusted.**
- **No production or staging probe.** Outbound HTTPS to `api-spinr.spinr.ca` is
  blocked by this environment's proxy (403 on CONNECT), so the live 401 was never
  observed directly. The diagnosis is from code plus the reporter's screenshot.
  Post-deploy, confirm in Chrome DevTools → Network that
  `GET /api/v1/rides/track/{token}` returns **200** (it returned 401 with no
  `access-control-allow-origin` before).
- **The deployed `track.spinr.ca` build is ahead of `main`.** Its error string
  appends the failing host (`"Load failed (api-spinr.spinr.ca)"`); neither `main`
  nor this branch does — both render bare `err.message`
  (`admin-dashboard/src/app/track/[rideId]/page.tsx:104`). Vercel is serving a
  build not represented in the repo. That does not affect this fix, but the drift
  is unexplained and worth a separate look.
- **No frontend build was run** — this diff touches no `admin-dashboard` /
  `rider-app` / `driver-app` code, so no production build was applicable. The
  6 seeded admin-dashboard visual-regression baselines are unaffected.

## 11. Adjacent observations (NOT fixed — out of scope)

Noted per the "mention, don't delete" rule:

- **`track_shared_ride` has no rate limit at all** (`sharing.py:221-222`) — no
  `@api_rate_limit`/`@ride_read_limit` decorator, and no `request: Request`
  parameter for one to key on. Confirmed there is no blanket fallback: the
  project registers `app.state.limiter` but never adds `SlowAPIMiddleware`, so an
  undecorated route is genuinely unlimited. This is **pre-existing**, but this
  diff is what makes it reachable — App Check was previously blocking 100% of
  browser traffic to this endpoint, which incidentally masked the gap. The
  closest precedent in the codebase, `routes/offer_card.py:57-58`, is also
  App-Check-exempt and token-authorised and *does* carry
  `@default_limiter.limit("60/minute")` for exactly this reason.
  Brute-forcing the token itself is not the concern — `secrets.token_urlsafe(32)`
  is 256 bits — but unauthenticated unlimited polling is a DB-load surface, and
  no index on `rides.shared_trip_token` was found in `backend/migrations/`.
  **Deliberately not fixed in this PR** (it widens the diff into `sharing.py` and
  `rate_limiter.py`, and sizing the limit wrong would break legitimate
  multi-viewer tracking on one link); raised to the repo owner as a follow-up
  decision. Surfaced by the `spinr-security-auditor` pass.
- `backend/routes/rides/sharing.py:241-243` — a malformed
  `shared_trip_token_created_at` **fails open** (access allowed, error logged)
  rather than closed. Pre-existing, unchanged by this diff, but it is an
  expiry-bypass path on a safety surface and deserves its own decision.
- The CORS-ordering problem in root cause (2) is **not fixed here**. Every App
  Check 401 remains unreadable to browsers, so the next endpoint that hits this
  will present as a network outage too. Fixing it means either moving
  `CORSMiddleware` outermost or attaching CORS headers to App Check's
  short-circuit — an auth-path change with its own blast radius, which does not
  belong in a hotfix for a dead safety link. Recommend tracking separately.
