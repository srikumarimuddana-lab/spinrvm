# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-18 |
| Author | vikas@ngitservices.com (via Claude Code) |
| Surface(s) | docs (backend endpoints referenced, not modified) |
| Domain (Sentry tag) | admin (closest fit — this is ops/observability tooling, not a runtime domain) |
| PR / commit link | (filled in on PR) |
| Related issue or gap ID | ACTION_ITEMS.md E4 — "Synthetic monitoring + SLO alerting" |

## 1. Issue / gap identified

Nothing external probes the Spinr platform today. A total outage (Cloudflare
misroute, both Fly.io and Railway down, DNS failure) is currently discovered
by users opening the app, not by any monitoring system or alert.

## 2. Root cause

Synthetic monitoring was never set up — no vendor account, no check
definitions, no alert routing exist for this. This is a build gap, not a
regression: the platform simply has no external liveness/SLA probing layer
yet.

## 3. Fix / remediation

This PR is **scaffolding only** — it adds no running monitoring, creates no
vendor account, and calls no real external endpoint. It produces the
vendor-agnostic specification a human needs to wire up real monitoring:

- `docs/runbooks/synthetic-monitoring.md` — runbook specifying the three
  probes (`GET /health`, `POST /api/v1/auth/refresh` expecting 401,
  `GET /api/v1/fares?lat=52.1332&lng=-106.6700` expecting 200), probe
  frequency (every 1 minute, per E4's own text), down-vs-degraded semantics,
  and SLA thresholds quoted verbatim from CLAUDE.md's Performance SLAs and
  KPI Targets tables (fare estimate P95 < 300 ms, auth refresh P95 < 200 ms).
  Explicitly vendor-agnostic — lists Checkly/UptimeRobot/Grafana Synthetic
  Monitoring as options without picking one.
- `monitoring/synthetic-checks.yaml` — a flat, self-documented declarative
  spec (name/method/path/expected-status/timeout/frequency/SLA-threshold per
  probe) that a human translates into whichever vendor's actual config once
  chosen. Not tied to any vendor's config format.
- `ACTION_ITEMS.md` E4 updated (kept open, `- [ ]`) noting these two new
  artifacts and that real external monitoring still requires a human to pick
  a vendor, create an account, and wire the actual checks.

The three probed endpoints were located by grepping `backend/routes/` rather
than guessed:
- `GET /health` — `backend/server.py` (readiness probe, checks DB via
  `_db_ready()`)
- `POST /api/v1/auth/refresh` — `backend/routes/auth.py`, mounted via
  `auth_router` at `/api/v1/auth` in `backend/server.py`. Chosen over
  `/auth/send-otp` (real Twilio SMS cost + consumes the 6/minute rate limit)
  and `/auth/verify-otp` (needs a live OTP the probe can't obtain); calling
  `/refresh` with no token deterministically returns 401 for free.
- `GET /api/v1/fares` — `backend/routes/fares.py`, mounted via
  `v1_api_router` at `/api/v1` in `backend/server.py`.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated, docs/spec only, no running code, no real external
  account touched.** No application code, route handler, migration, or
  config file was modified. `backend/routes/auth.py` and
  `backend/routes/fares.py` were only read (grepped) to find the real paths
  — not edited.
- No other consumer reads `monitoring/synthetic-checks.yaml` or
  `docs/runbooks/synthetic-monitoring.md` today — both are new files with no
  existing callers, so there is nothing to regress.
- `ACTION_ITEMS.md` E4's checkbox stays `- [ ]` (not closed) — this PR does
  not claim the gap is resolved, only that scaffolding for it now exists.
- No Stripe, Supabase, Redis, Twilio, PagerDuty, Checkly, UptimeRobot, or
  Grafana account or endpoint was created, configured, or called during this
  work. The YAML's `alert_route: pagerduty-placeholder-integration-key` is a
  literal placeholder string, not a real credential.

## 5. User-experience effect

None. No rider, driver, corporate-admin, or internal-admin-facing behavior
changed. This is internal ops/documentation tooling only.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `docs/runbooks/synthetic-monitoring.md` | New runbook | Specifies probes, SLA thresholds, down/degraded semantics, alert routing, vendor options for E4 |
| `monitoring/synthetic-checks.yaml` | New declarative check spec | Vendor-agnostic source of truth a human translates into a real vendor config |
| `ACTION_ITEMS.md` | E4 entry appended with a dated note | Records the scaffolding artifacts without closing the item |
| `docs/change-log/2026-08-18-e4-synthetic-monitoring-scaffolding.md` | New Change Impact Log | Required per CLAUDE.md for any commit touching a tracked gap |

## 7. Before / after

Not applicable — purely additive new files plus an appended note on an
existing open checklist item; no existing behavior-changing diff.

## 8. Rollback plan

`git revert` is sufficient and complete here: every change is additive
(two new files, one appended paragraph in `ACTION_ITEMS.md`), nothing was
applied to live data, no migration ran, no vendor account or credential was
created. Reverting the commit fully undoes this change with no follow-up
cleanup required.

## 9. Verification performed

- [x] `python3 -c "import yaml; yaml.safe_load(open('monitoring/synthetic-checks.yaml'))"` — parses cleanly, all 3 checks present.
- [x] Endpoint paths verified by grepping `backend/routes/auth.py`,
      `backend/routes/fares.py`, and `backend/server.py`'s `include_router`
      calls (not guessed) — confirmed `/health` (root-mounted),
      `POST /api/v1/auth/refresh`, and `GET /api/v1/fares`.
- [x] SLA numbers cross-checked word-for-word against CLAUDE.md's
      "Performance SLAs" table (fare estimate < 300 ms, auth token refresh
      < 200 ms) and "KPI Targets" table (P95 fare calc latency < 300 ms) —
      no numbers invented.
- [ ] No automated test suite applies — this PR changes no application code,
      only docs and a YAML spec. No test was written or needed, per the task
      instructions.
- [ ] Not run: `npm run build` / equivalent for `admin-dashboard`,
      `rider-app`, `driver-app` — not applicable, no frontend code touched.

## 10. What was NOT verified

- No real HTTP request was made to `/health`, `/api/v1/auth/refresh`, or
  `/api/v1/fares` from this session — the task explicitly prohibits hitting
  any real endpoint. The expected-status assumptions (200 / 401 / 200) are
  based on reading the route handler source, not on an observed live
  response.
- The pinned Saskatoon coordinate (52.1332, -106.6700) was not checked
  against the live `app_settings`/service-area table — it is asserted as
  "central Saskatoon, inside an active service area as of this writing"
  based on general knowledge of the launch market, not a DB query. The
  runbook flags this explicitly and points at
  `docs/runbooks/saskatoon-launch.md` for whoever wires up the real check.
- No vendor (Checkly/UptimeRobot/Grafana) was evaluated hands-on; the
  "Vendor options" section in the runbook is informational only, not a
  trialled recommendation.
- No PagerDuty service or integration key was created or tested.

## Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no data
      cleanup needed).
- [x] Blast radius is stated, not assumed: isolated, docs/spec only, no
      running code, no real external account touched.
- [x] No silent behavior change — nothing in this PR changes any
      already-shipped flow; UX effect field states "None" explicitly.
