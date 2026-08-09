# Change Impact & Risk Log — code-review fixes on PR #3465 (deploy coupling, alert path, metric cardinality)

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-08 |
| Author | Claude Code (session: postgres-scaling-supabase) |
| Surface(s) | backend, infra |
| Domain (Sentry tag) | admin (observability), rides |
| PR / commit link | PR #3465, branch `claude/postgres-scaling-supabase-ypnwiy` |
| Related issue or gap ID | Code-review findings #2, #3, #4 on PR #3465 (finding #1 has its own log: `2026-08-08-ride-read-limit-never-registered.md`) |

## 1. Issue / gap identified

A code review of this branch surfaced three defects **introduced by the branch
itself**, all of which would have shipped:

1. **Concurrency limits would have deployed without the machines to absorb
   them.** `fly.toml`'s raise to 750/1000 ships automatically via
   `deploy-fly.yml` on push to `main`, but `flyctl scale count 8` lived only in
   the manually-triggered `bootstrap-fly.yml`.
2. **The DB-saturation alert ran through the database.** The watchdog's email
   channel used `send_transactional_email`, which performs three DB operations
   per send.
3. **The 429 metric had unbounded label cardinality**, and the new watchdog
   scans that map every 60 s.

## 2. Root cause

1. **Split ownership of one decision.** Machine count and connection limits are
   a single capacity decision, but lived in two files on two different delivery
   paths — one automatic, one manual. The earlier work documented "run bootstrap
   manually" as a prerequisite without noticing that the *other half* shipped on
   its own.
2. **Reused a helper without reading what it costs.** `send_transactional_email`
   is the right call for a receipt and the wrong one for an infrastructure
   alert: it loads `app_settings`, queries `email_suppressions`, and INSERTs
   `email_send_log`. The module docstring's "no DB writes" claim — which was
   also the stated replay-safety justification — was written from intent rather
   than from the call graph.
3. **Pre-existing label choice, newly consequential.** `request.url.path` as a
   metric label predates this branch. Adding a loop that scans that map every
   60 s turned a latent memory issue into an active one.

## 3. Fix / remediation

| # | Fix |
|---|---|
| 2 | Added `flyctl scale count 8` to `deploy-fly.yml` immediately after the deploy step. `scale count` is idempotent, so it is a no-op once the pool exists and simply keeps the halves in sync. `bootstrap-fly.yml` is now only for first-time app creation. |
| 3 | New `send_ops_alert_email()` in `utils/email_provider.py` — same SES/Resend path, but credentials come from a cache primed at loop startup, and it skips the suppression check and the `email_send_log` insert. Watchdog switched to it; docstrings corrected. |
| 4 | New `_metric_path_label()` in `utils/rate_limiter.py` returns the matched route's **template** (`/rides/{ride_id}/cancel`) instead of the live URL. Unmatched requests get a literal `"unmatched"`. |

### Trade-offs accepted on fix #3, stated explicitly

- **No `email_send_log` row for ops alerts.** That table exists for user-facing
  mail and PIPEDA auditability; an internal capacity alert is neither. Delivery
  outcome goes to stdout instead.
- **Suppression list not consulted.** Recipients are internal ops addresses.
  Silently dropping an infrastructure alert because the inbox once hard-bounced
  is a worse failure mode than mailing a dead address.
- **A cold cache still costs one DB read** (first alert after a restart). One is
  strictly better than three, and it fails closed — reporting undelivered rather
  than raising — if the DB is down at that moment.

## 4. Risk & impact on existing functionality

**Blast radius by fix:**

- **#2 — infra, affects all traffic.** This *reduces* risk: it removes a state
  where production could run raised limits on an unscaled fleet. The new step
  runs on every deploy to `main`; if `FLY_API_TOKEN` lacks scale permission the
  deploy job fails loudly after a successful deploy, rather than silently
  leaving the fleet mis-sized. Verified the workflow YAML parses and that
  `FLY_APP` is job-level, matching the existing `flyctl secrets` step.
- **#3 — alerting only.** `send_transactional_email` is **unchanged**, so
  receipts, DSAR exports, driver statements, corporate invoices, and marketing
  mail are untouched. Confirmed by grep: the new function has exactly one caller
  (the watchdog). The new `_ops_settings_cache` is module-level state read only
  by the ops path.
- **#4 — cross-cutting but observability-only.** `spinr_rate_limit_violation_total`
  is consumed by the capacity watchdog and by operators reading `/metrics`.
  **Existing dashboards or alerts keyed on the raw-path label will stop
  matching** — that is the intended change, and it is the one thing to be aware
  of if anything external queries this metric. Nothing in-repo does beyond the
  watchdog, which sums across all label sets and is unaffected.

**Ride state machine, money paths, dispatch, auth:** untouched by all three.

**Could any of these regress a working flow?**

- #4's helper runs inside the 429 exception handler, where an exception would
  convert a rate-limit response into a 500. This was caught during the fix: the
  first implementation raised `AttributeError` on a mock request. It is now
  narrowed to `(AttributeError, TypeError)` with a debug log, and covered by
  tests for malformed and missing route objects.
- #3 changes which function the watchdog calls; if `send_ops_alert_email` were
  broken, alerts would fail while everything else kept working. Covered by a
  test that poisons every DB entry point and asserts the alert still reaches the
  provider.

## 5. User-experience effect

- **Riders / drivers / corporate admins: none.** No endpoint, response body, or
  copy changes in any of the three fixes.
- **Visible mid-session?** No.
- **Internal / on-call:** capacity alerts now arrive even when the database is
  the thing that is broken — which is the case they exist for. Metric rows are
  per-endpoint rather than per-ride, so the runbook's "check which path" step
  returns a usable list instead of thousands of one-hit rows.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `.github/workflows/deploy-fly.yml` | Added an idempotent `flyctl scale count 8` step after deploy | Machine count and concurrency limits must not ship on different paths |
| `docs/runbooks/capacity-scaling.md` | §2 records that the pool now ships automatically; bootstrap is first-time-only | The runbook previously carried a manual prerequisite that no longer applies |
| `backend/utils/email_provider.py` | Added `prime_ops_email_settings()` + `send_ops_alert_email()`; `_ops_settings_cache` | A DB-free alert path, kept inside the email module rather than reaching into its private helpers from the loop |
| `backend/utils/capacity_watchdog.py` | Switched to `send_ops_alert_email`; primes credentials at loop start; corrected the false "no DB writes" docstring | The alert must not depend on the subsystem it reports on |
| `backend/utils/rate_limiter.py` | Added `_metric_path_label()`; violation counter now labelled with the route template | Bounded cardinality; usable triage signal |
| `backend/tests/test_capacity_watchdog.py` | Repointed to the new function; 2 new tests including the DB-free guarantee | The guarantee is the point of the fix and must be pinned |
| `backend/tests/test_rate_limit_metric_cardinality.py` | New: 6 tests | Cardinality regressions are invisible until they hurt |
| `backend/tests/test_rate_limit_response_shape.py` | Updated the SOC gap #46 test for the new label; added a collapse-to-one-label case | The old test asserted the raw-path label |

## 7. Before / after

```yaml
# deploy-fly.yml — before: limits shipped, machines did not
- name: Deploy to Fly.io
  run: flyctl deploy --remote-only --config fly.toml
# (nothing — scale count lived only in bootstrap-fly.yml)
```

```yaml
# after
- name: Deploy to Fly.io
  run: flyctl deploy --remote-only --config fly.toml
- name: Ensure burst machine pool (2 warm + 6 suspended)
  run: flyctl scale count 8 --region yyz -a "${FLY_APP}" --yes
```

```python
# capacity_watchdog — before: 3 DB ops per alert, through the saturated pool
sent = await send_transactional_email(to=addr, subject=subject, text=body, ...)
```

```python
# after: cached credentials, no suppression query, no audit insert
sent = await send_ops_alert_email(to=addr, subject=subject, text=body, log_id="capacity")
```

```python
# rate_limiter — before: one permanent label set per ride
_metric_inc("spinr_rate_limit_violation_total", {"path": request.url.path})
# /rides/8f14e45f-.../cancel, /rides/b3d9c1a2-.../cancel, … unbounded
```

```python
# after: one label set per endpoint
_metric_inc("spinr_rate_limit_violation_total", {"path": _metric_path_label(request)})
# /rides/{ride_id}/cancel
```

## 8. Rollback plan

- **#2:** revert the workflow step; the pool then persists at whatever
  `scale count` last set (it does not shrink on its own), so no capacity is lost
  by reverting — only the guarantee that the halves stay in sync.
- **#3:** `git revert`. `send_transactional_email` is untouched, so reverting
  restores the previous (DB-dependent) alert path without affecting any other
  mail. To silence alerts without a deploy: `fly secrets unset ALERT_EMAIL_TO`.
- **#4:** `git revert` restores the raw-path label. No config lever, and none is
  warranted — the previous behaviour is the defect.

None of the three touches durable state (no ride rows, wallet deltas, Stripe
calls, or migrations), so code reverts are complete rollbacks with no
data-level remediation.

## 9. Verification performed

- [x] **Automated tests run** (`backend/.venv`):
      - `test_capacity_watchdog.py` — **31 passed** (2 new)
      - `test_rate_limit_metric_cardinality.py` + `test_rate_limit_response_shape.py` — **13 passed**
      - `pytest -k "rate_limit or ratelimit"` — **117 passed, 2 skipped**
      - `pytest -k "email or receipt or watchdog"` — **313 passed, 1 skipped**
- [x] **The DB-free guarantee is asserted, not assumed** — the key test poisons
      `_load_settings`, `_is_suppressed`, and `_log_send` so any DB access
      raises, then asserts the alert still reaches the provider and that zero DB
      calls occurred.
- [x] **Workflow YAML validated** by parsing it and listing the resulting steps;
      confirmed `FLY_APP` is job-level env, matching the existing secrets step.
- [x] **Blast-radius grep** — `send_ops_alert_email` has exactly one caller;
      `send_transactional_email`'s existing callers are unchanged.
- [x] **Lint** — `ruff check` clean on all changed files. One finding during the
      work (`S110 try-except-pass`) was fixed properly by narrowing the exception
      and logging, not suppressed.
- [ ] **Manual repro in staging** — not possible; no staging environment
      (ACTION_ITEMS E1).

## What was NOT verified

- **The scale step has never executed.** `flyctl scale count 8` is correct in
  form and idempotent by documentation, but no Fly deploy was run from this
  environment. If `FLY_API_TOKEN` is a per-app deploy token lacking scale
  permission, this step will fail on the first deploy to `main` — **check the
  token's scope before merging.**
- **No alert has been delivered to a real inbox.** The email path is verified
  against fakes at the `_try_ses` boundary; SES/Resend were never called.
- **Credential priming has not run against a real `app_settings` table.**
- **The claim that a DB outage still permits an alert is proven in tests, not in
  production** — it depends on credentials having been cached earlier, which
  requires the loop to have started while the DB was healthy.
- **Nothing external to this repo was checked for use of the raw-path metric
  label.** If a Grafana dashboard or external alert queries
  `spinr_rate_limit_violation_total{path="/rides/<uuid>/cancel"}`, it will stop
  matching.
- **Not tested against live Supabase** — `mock_supabase_client` fixtures only.

## 10. Sign-off

- [x] Rollback plan is concrete per fix, and notes where no config lever exists
- [x] Blast radius is stated per fix, including the one external-facing
      consequence (metric label change)
- [x] No silent behavior change — the metric relabel is called out as the item
      most likely to surprise anything querying it
