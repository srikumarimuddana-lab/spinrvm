# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-03 |
| Author | vikas@ngitservices.com (via Claude Code) |
| Surface(s) | backend (docs/CI only — no application code touched) |
| Domain (Sentry tag) | admin (ops/observability tooling, not a runtime domain — same classification as E4's 2026-08-18 entry) |
| PR / commit link | (filled in on PR) |
| Related issue or gap ID | ACTION_ITEMS.md E13 (new); complements open E4 |

## 1. Issue / gap identified

On 2026-09-02, `api-spinr.spinr.ca` went unreachable in production
(`SSLHandshakeException: connection closed` on mobile clients) because its
Fly-managed TLS certificate was never provisioned as its own hostname —
only an orphaned `*.spinr.ca` wildcard cert existed, stuck "Pending
validation" for an unknown period because its ACME DNS challenge was never
configured, and nothing was watching it. A repo-wide grep at the start of
this work confirmed **nothing in this codebase checked TLS certificate
expiry, domain registration expiry, vendor/plan renewal dates, or secret
rotation age** — this was a total blind spot, not a partial one.

## 2. Root cause

These are all leading-indicator checks (catch the problem before it
becomes an outage) as opposed to lagging-indicator checks (catch the
outage after it happens, e.g. E4's synthetic uptime probes). The repo had
some of the latter category scaffolded (E4) and real capacity/DB alerting
(`capacity_watchdog`), but zero of the former. Nobody had built it because
nobody had hit this specific failure mode before 2026-09-02.

## 3. Fix / remediation

Five new/extended artifacts, all additive:

- `.github/workflows/cert-domain-monitor.yml` — daily openssl-based TLS
  cert expiry check (all production hostnames) + WHOIS domain expiry
  check for `spinr.ca`. Idempotent tracked-issue pattern (mirrors
  `subprocessor-monitor.yml`).
- `docs/runbooks/renewal-calendar.md` + `.github/workflows/renewal-calendar-monitor.yml`
  — vendor/plan renewal-date tracker + weekly lead-time alert.
- `docs/runbooks/secret-rotation.md` + `.github/workflows/secret-rotation-monitor.yml`
  — credential rotation-cadence tracker (metadata only, never values) +
  monthly overdue alert.
- `.github/workflows/supabase-capacity-monitor.yml` — optional, gated
  behind two new-but-unset GitHub secrets; approximates DB disk usage via
  the Supabase Management API once/if a human configures it.
- `docs/runbooks/synthetic-monitoring.md` extended with a stack-wide
  health-check coverage audit + a flag on unconfirmed `capacity_watchdog`
  alert wiring.
- `docs/runbooks/capacity-scaling.md` — new §9 documenting the optional
  Supabase capacity check and its honest limitations.
- `ACTION_ITEMS.md` — new E13 entry tying all of the above together.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to CI/docs.** No application code, route
  handler, migration, background loop, or config file that the running
  backend/apps read was modified. `capacity-scaling.md` and
  `synthetic-monitoring.md` were edited but only with new appended
  sections — no existing content changed or removed.
- **No other workflow reads any of the four new workflow files.** They
  are net-new scheduled triggers with their own `schedule:`/`workflow_dispatch:`
  entry points; nothing else in `.github/workflows/` references them.
- **No new required secret.** `SUPABASE_ACCESS_TOKEN`/`SUPABASE_PROJECT_REF`
  are optional and the workflow no-ops without them — verified by the
  `gate` step's conditional logic. No existing secret was read, logged, or
  modified.
- **All four new workflows use `issues: write` + `contents: read`
  permissions only** — same scope as the existing `subprocessor-monitor.yml`
  they're patterned on. None can modify code, secrets, or deployment state.
- **GitHub Actions minutes consumption**: four new scheduled jobs (1 daily
  + 1 daily + 1 weekly + 1 monthly), each a single short-lived
  `ubuntu-latest` job (under a minute of actual work each). Noted in the
  renewal calendar itself as a top-level risk (a GitHub Actions minutes
  exhaustion would silently stop these checks along with all other CI) —
  not a new risk this PR introduces, just one now explicitly documented.
- **False-positive risk**: WHOIS rate-limiting or format drift, or a
  transient network blip during the openssl handshake, could produce a
  false alert. Mitigated by opening/updating one tracked issue rather than
  spamming, but not eliminated — a human reading the daily cert-domain
  issue should sanity-check a "handshake failed" finding before treating
  it as confirmed (the workflow's own body text says this).

## 5. User-experience effect

None. No rider, driver, corporate-admin, or internal-admin-facing behavior
changed. This is internal ops/observability tooling only — same
classification as the E4 scaffolding entry it complements.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `.github/workflows/cert-domain-monitor.yml` | New | Daily TLS cert + domain expiry check |
| `.github/workflows/renewal-calendar-monitor.yml` | New | Weekly vendor renewal-date check |
| `docs/runbooks/renewal-calendar.md` | New | Renewal-date tracker (all dates TBD) |
| `.github/workflows/secret-rotation-monitor.yml` | New | Monthly credential rotation-age check |
| `docs/runbooks/secret-rotation.md` | New | Rotation-cadence tracker (metadata only, all dates TBD) |
| `.github/workflows/supabase-capacity-monitor.yml` | New | Optional Supabase disk-usage approximation, gated/off by default |
| `docs/runbooks/capacity-scaling.md` | Appended §9 | Documents the optional capacity check + its limitations |
| `docs/runbooks/synthetic-monitoring.md` | Appended two sections | Stack-wide health-check coverage audit + `capacity_watchdog` wiring flag |
| `ACTION_ITEMS.md` | New E13 entry | Ties the whole set together, records what's still open |
| `docs/change-log/2026-09-03-e5-leading-indicator-monitoring.md` | New | This log |

## 7. Before / after

Not applicable — purely additive new files plus appended sections on
existing docs; no existing behavior-changing diff.

## 8. Rollback plan

`git revert` is sufficient and complete: every change is additive (new
files, or appended sections with no existing content altered), nothing
was applied to live data, no migration ran, no vendor account or
credential was created (the two Supabase secrets referenced don't exist
yet — nothing to revert there). Reverting the commit fully undoes this
change with no follow-up cleanup. If only specific workflows should be
disabled without a full revert, each has a `schedule:` trigger that can be
removed/commented independently, or disabled via the GitHub Actions UI.

## 9. Verification performed

- [x] All four new/edited workflow YAML files parse cleanly
      (`python3 -c "import yaml; yaml.safe_load(...)"`).
- [x] Every `run:` block's shell syntax checked with `bash -n` (batch
      script, all passed).
- [x] `renewal-calendar-monitor.yml`'s markdown-table parser tested
      end-to-end against the actual `renewal-calendar.md` file created in
      this change — correctly identified all 12 TBD rows and 0 false
      due/overdue findings.
- [x] Endpoint/secret names cross-checked against `backend/core/config.py`'s
      `Settings` class and the actual `secrets.*` references in
      `.github/workflows/{deploy-fly,deploy-backend,ci}.yml` — not guessed.
- [ ] No automated test suite applies — this change is CI/docs only, no
      application code. No test was written or needed.
- [ ] Not run: `npm run build` / equivalent — not applicable, no frontend
      code touched.
- [ ] The workflows themselves have **not** been run in a live GitHub
      Actions environment (only local YAML/bash syntax + one parser dry
      run) — this session has no means to trigger a real Actions run or
      observe one execute against the real repo/secrets.

## 10. What was NOT verified

- **No real HTTP/TLS/WHOIS request was made to any production hostname or
  the domain registrar from this session** — `cert-domain-monitor.yml`'s
  logic was reasoned about and syntax-checked, not observed against a live
  target. Its correctness against the *actual* current state of
  `api-spinr.spinr.ca`'s certificate (fixed earlier in this same
  conversation via the Fly dashboard) is unconfirmed by this session.
- **No Supabase Management API call was made** — `supabase-capacity-monitor.yml`
  is gated off (no secrets exist) and its query logic against the real API
  response shape is unverified; the parsing assumes a specific JSON shape
  (`[{"bytes": ...}]`) based on documented Management API conventions, not
  a live test.
- **No renewal or rotation date in either new tracker doc reflects a real
  audited value** — every date is `TBD` by design, stated explicitly in
  both docs. Treat the trackers as scaffolding a human must populate, not
  as confirmed-current data.
- **`capacity_watchdog`'s `ALERT_WEBHOOK_URL`/`ALERT_EMAIL_TO` wiring was
  not confirmed** — this session has no Fly dashboard/CLI access. Flagged
  explicitly in `synthetic-monitoring.md` rather than assumed either way.
- **GitHub Actions cron scheduling reliability** (GitHub's own documented
  caveat that scheduled workflows can be delayed under load, especially on
  free/low-usage repos) was not independently verified for this
  repository's plan tier.

## Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no data
      cleanup needed; no vendor account or credential created).
- [x] Blast radius is stated, not assumed: isolated to CI/docs, no
      application code, no live-tested surface touched.
- [x] No silent behavior change — nothing in this PR changes any
      already-shipped flow; UX effect field states "None" explicitly.
