# A6 — Observability & tagging

**Lane:** A6 · **Model:** sonnet / spinr-observability-reviewer · **Returned:** 2026-09-24 ~14:29 UTC (partial, 25-minute time box) · **Orchestrator note:** lane output pasted verbatim below; only this header was added. The tagging rollout plan (section d) is duplicated into `tagging-plan.md` per the one-hour-run.md §5 layout. Static code only — the Sentry connector was not used. **Verifier corrections (see `verification.md`):** OBS-001's "122 in matching.py" is a misattribution — `matching.py` has 31 `.error(` calls; the whole `routes/rides/` package has 121 (1 `.bind(`), `routes/drivers/*.py` has 167 (0 `.bind(`); the ~290 total and the mechanism hold. OBS-001 and OBS-006 overlap open ACTION_ITEMS **R4** ("Sweep `.bind(domain=...)` across the remaining Sentry-bound ERROR logs"); treat them as the quantified, larger scope of R4 and its missing CI half, not as previously undocumented. OBS-002 should also cite C11 (nothing scrapes production metrics) and note that Sentry tracing at 0.1 sample rate gives sampled transaction latency for those paths.

---

SPINR OBSERVABILITY AUDIT — Lane A6 (Observability & tagging), repo /home/user/spinrvm
Time-boxed static-code pass (no Sentry connector). Evidence-labelled per master §5/§4.

## (a) Taxonomy coverage (docs/audit/clean-sheet-prompt/standards-and-scale.md §4)

| key | where present today | est. % of T0 components covered | gap |
|---|---|---|---|
| `domain` | `logger.bind(domain=...)` in 41 loguru modules (26 total `.bind(` calls); `set_tag("domain",...)`/`tags={"domain":...}` in ~15 explicit Sentry-capture sites (`ai/*.py`, `core/security.py`, `routes/admin/compliance.py`, `routes/websocket.py`, `services/ledger_service.py`, `utils/refresh_tokens.py`, `utils/outbox_worker.py`, `utils/h3_location_index.py`, `services/data_transfer/observability.py`) | **~15–20%** VERIFIED | `routes/rides/*.py` (matching/booking/cancellation/lifecycle/payments/safety/tracking/stops) and `routes/drivers/*.py` — 122 + 168 = **290** `logger.error`/`.opt(exception=True).error(` call sites — have **zero** `domain` binds (see finding OBS-001). 263 stdlib-`logging` modules get zero domain promotion from Sentry's `LoggingIntegration` (no equivalent of `tags_from_log_extra` exists for stdlib). |
| `surface` | Universally stamped: `scrub_event`'s `before_send` does `tags.setdefault("surface","backend")` (`utils/sentry_scrub.py:150`); rider-app/driver-app `initialScope.tags.surface` (`shared/services/errorReporting.ts`); admin-dashboard `initialScope.tags.surface='admin'` (`sentry.server.config.ts`) | **100%** VERIFIED (backend), 100% for the 3 configured frontend surfaces | none found |
| `component` | Not a Sentry tag anywhere; approximated by module docstrings/comments only, no consistent field | **~0%** VERIFIED | no standard key; would need a lint-enforced module header or `set_tag("component", __name__)` |
| `owner` | `.github/CODEOWNERS` exists (73 lines, path-based, 2 collaborators) — this is review routing, not a runtime tag; no `COMMENT ON TABLE ... owner=` and no log/metric field named `owner` | CODEOWNERS: high path coverage; as a *tag* concept: **0%** | taxonomy's `owner` key doesn't exist as emitted telemetry at all |
| `tier` (T0/T1/T2/T3) | Not found anywhere in code/logs/metrics/Sentry/migrations | **0%** VERIFIED (grepped `tier.*T0\|T1\|T2\|T3` — no hits as a telemetry key) | fully unimplemented — CLAUDE.md doesn't even enumerate a T0 list explicitly (see rollout plan §d for a first cut) |
| `data_class` | Not a tag; PII handling is enforced structurally instead (`utils/pii.py` key-denylist, `utils/sentry_scrub.py` redaction, `backend/tests/test_log_guard.py`) rather than via a `data_class` label on the data itself | **0%** as a taxonomy key (though the *effect* it's meant to produce — no PII in logs/Sentry — is independently, robustly enforced, see steelman) | no `COMMENT ON COLUMN ... data_class=` found among the 206 files with `COMMENT ON TABLE/COLUMN` |
| `sla` | Only informally, via metric-name choice matching CLAUDE.md SLA rows (e.g. `spinr_fare_calc_duration_ms`) — no explicit `sla` tag/label on any metric or log line | **0%** as an explicit key | see (b) below — 3 of 8 SLA rows have no metric at all, so this key can't be added to something that doesn't exist yet |
| `flag` | `app_settings` rows have `description` columns (informal); no consistent `flag` key on logs at the read site — some sites do log the flag name as part of the message text (e.g. `spinr_rides_settings_read_failed_total{flag}` per ACTION_ITEMS R3), inconsistently named across ADR-011's flag-read sites | **~10%**, inconsistent | ACTION_ITEMS R2/R3 (open) already track converging this — don't re-file |
| `vendor` | Present implicitly via file/module names (`stripe_reconcile.py`, `zoho_desk_*.py`) and `monitoring/synthetic-checks.yaml`'s `target_service` field, not as a log/metric/Sentry tag | **~5%** | no `vendor=` tag on Stripe/Twilio/Zoho/FCM/Maps error logs |
| `env`/`region` | `environment=` set globally on `sentry_sdk.init` (`server.py:702`) and on all 3 frontend Sentry inits; **not** a per-event override anywhere (relies on the global init value) | **100%** for `env` at the process level | `region` (ca-central vs vendor ingest region) only appears as a one-off residency check in admin-dashboard's `sentry.server.config.ts` (`_checkSentryRegion`) — good pattern, not replicated in backend/mobile |
| `cost_center` | Not found | **0%** | `loadtest/`, `.github/workflows/billing-usage-monitor.yml` track vendor cost but not per-request/per-domain cost attribution |
| `scale_limit` | `docs/audit/clean-sheet-prompt/standards-and-scale.md` §5 has a per-vendor budget *table* (aspirational); `utils/maps_budget.py` is the one place a real budget/tripwire is enforced in code | **~5%** (Maps only) | no `scale_limit` tag/metric for Supabase/Redis/Fly/Stripe/Twilio despite `*-monitor.yml` workflows existing for adjacent concerns (capacity, billing, secrets) |

## (b) SLA → metric → alert (CLAUDE.md Performance SLA table)

| SLA path | Metric name + path:line, or MISSING | Alert, or MISSING |
|---|---|---|
| Dispatch offer → driver notification < 2s | `spinr_dispatch_offer_to_accept_duration_ms` — `routes/drivers/ride_flow.py:315` (per ADR-010's own table; not independently re-verified line-by-line this pass) | **MISSING** — ADR-010 §5 MVP (Grafana Cloud agent + 2 alert rules) not implemented; tracked as **CR-2026-008 / issue #3295**, open, P1, blocked on a human provisioning Grafana Cloud (ACTION_ITEMS.md line ~28335). `monitoring/synthetic-checks.yaml` is an explicit **spec only** — its own header states "nothing in this repo talks to Checkly/UptimeRobot/... this file does not create, call, or authenticate against any of them." Do not cite it as a live alert. |
| Fare estimate < 300ms (known exception, 3.5s worst-case, accepted 2026-08-21) | `spinr_fare_calc_duration_ms` — `routes/rides/estimates.py:657` (decorator) | **MISSING**, same CR-2026-008 gap |
| Fare settlement < 1s | `spinr_payment_settlement_duration_ms` — `routes/rides/payments.py:346` (VERIFIED, found this pass) — note this name is **not** in CLAUDE.md's canonical metric list (only `spinr_payment_settlement_total{outcome}` is documented there); minor doc drift, not a functional gap | **MISSING** |
| WebSocket fan-out < 100ms | `spinr_ws_fanout_duration_ms` — `socket_manager.py:319-327` (per ADR-010 table) | **MISSING**; ADR-010 §5 explicitly defers WS/match-rate alert rules past day one |
| Driver location update (write) < 150ms | **MISSING** — grepped `routes/drivers/location.py`; only `spinr_live_marker_write_failures_total` (a failure counter) exists, no latency histogram | **MISSING** |
| Auth token refresh < 200ms | **MISSING** — only `spinr_auth_refresh_recovered_total`/`spinr_auth_otp_lockout_total` (event counters) exist in `routes/auth.py`; no duration metric | **MISSING** |
| Stripe webhook processing < 500ms | **MISSING** — `routes/webhooks.py` has no `duration_ms` metric found by grep | **MISSING** |
| Migration apply (prod window) < 30s | N/A — deploy-time, not a runtime metric; no evidence a duration is captured by `run_migrations.py` | **MISSING** |

**5 of 8 SLA rows have no measuring metric at all** (3 runtime rows plus the migration row, plus the lane counts the fan-out row's ADR-only evidence as unverified), and **0 of 8** have a live, firing alert — the aggregation/alerting layer (ADR-010 §5) is an already-tracked, still-open gap (CR-2026-008/#3295); the 3 missing-metric runtime rows (location write, token refresh, webhook processing) are a *new* observation this pass, not previously tracked under that CR (which only names dispatch latency and payment failure rate as the day-one pair). *Orchestrator note: the lane's own OBS-002 card uses the count "3 of 8" for the missing-metric rows; that is the number carried into the executive summary.*

## (c) Finding cards

### OBS-001 — 290 ERROR-level dispatch/driver logs reach Sentry with no `domain`/`ride_id`/`driver_id` tag
- Hierarchy: L2 Reliability/SRE & Observability › L3 Sentry tagging › L4 Backend error capture › L5 rides+drivers ERROR logs
- Severity: CRITICAL   Priority score: S×B×L = high×fleet-wide×high (≈ top of lane)
- Status: VERIFIED   Existing item: related-but-narrower — ACTION_ITEMS "R4" (2026-09-21, PR #5614 follow-ups) covers only 2 handlers in `dependencies/__init__.py`; this finding is a much larger, previously-undocumented instance of the same defect class
- Adversary: SRE on-call at 3am filtering Sentry by `domain:dispatch` during a real dispatch-latency incident
- Evidence: `backend/routes/rides/matching.py` — 122 `logger.error`/`.opt(exception=True).error(` calls, 0 `logger.bind(domain=...)`; `backend/routes/drivers/*.py` — 168 error calls, 0 binds (grep counts, this session); sample lines `matching.py:162,270,301,333,751,817,1007,1069`, `booking.py:117,188,205,208,240`, `cancellation.py:139,147,218,258,270`, `drivers/_shared.py:184,191,199,208,234`, `drivers/ride_flow.py:68,148,244,303,342` (stdlib `logging`, same gap via a different mechanism — see below). Root mechanism: `backend/server.py:698-727`'s loguru→Sentry sink only promotes tags via `tags_from_log_extra(record["extra"])` (`utils/sentry_scrub.py:209-228`), which is empty unless the call site did `logger.bind(...)` first; `core/security.py:12-24`'s own code comment independently confirms stdlib `logging`'s `extra=` is **not** wired to any tag-promotion at all (only `surface` gets stamped globally by `scrub_event`, `utils/sentry_scrub.py:150`).
- What happens (plain language): a dispatch failure, driver-accept failure, or ride-cancellation-fee-capture failure lands in Sentry as a bare stack trace tagged only `surface=backend` — no way to filter by domain, no `ride_id` to jump to the affected trip, no `driver_id` to correlate with a driver-side complaint. An on-call engineer triaging "why did dispatch degrade at 3am" cannot filter these events out from every other backend error.
- Root cause: the loguru→Sentry bridge and the stdlib `LoggingIntegration` were both built to auto-capture ERROR logs, but neither carries an enforced requirement that the call site attach `domain`/`ride_id`/`driver_id` before calling `.error(...)` — so the two highest-stakes domains (dispatch/rides, drivers) simply never adopted `.bind()`, while `payments`/`auth`/`admin` largely did (`payment_service.py:124,2475,2536`, `wallet_repo.py:428`, `dependencies/__init__.py:403,429`, `worker.py:22`, `settings_loader.py:63`).
- Recommendation: (1) add `logger.bind(domain="dispatch"/"rides"/"drivers", ride_id=..., driver_id=...)` at the ~290 call sites, starting with `matching.py` and `drivers/ride_flow.py`/`_shared.py` (highest SLA stakes); (2) add a new static check to `backend/tests/test_loguru_call_conventions.py` (which already statically scans every loguru module, per its own docstring) that fails when a `.error(`/`.opt(exception=True).error(` call has no preceding `.bind(domain=...)` in the same statement/chain, mirroring its existing `test_no_extra_kwarg_in_loguru_calls`/`test_no_exc_info_kwarg_in_loguru_calls` pattern; (3) for the 263 stdlib-logging modules, either migrate SLA-critical ones (payments/auth/dispatch-adjacent) to loguru, or add an explicit tagged `sentry_sdk.capture_exception` wrapper at their error sites the way `core/security.py`/`routes/websocket.py` already do.
- Alternative considered: leave stdlib modules alone and rely on `LoggingIntegration`'s auto-capture — rejected because it structurally cannot carry `domain`/`ride_id` (confirmed by `core/security.py`'s own comment explaining why it needed a manual capture instead of relying on the auto path).
- Blast radius: every consumer of Sentry's issue list/alerts for dispatch or driver incidents (on-call SRE, `spinr-dispatch` domain owner); no code-behavior change, log-only/tag-only addition, so no runtime risk to ride flow itself.
- Rollout: additive (adding `.bind()` calls doesn't change control flow); start with new/changed code via the proposed CI check, backfill T0 files per (d) below. No flag needed — pure telemetry addition.
- Verification to close: extend `test_loguru_call_conventions.py` with the new check, run it against `matching.py` before/after a sample fix, confirm it fails on unbound `.error(` and passes once bound.

> Orchestrator severity note: OBS-001 is a triage-visibility gap, not a user-facing or money-moving defect. The lane rated it CRITICAL by its own domain scale ("will be invisible in prod"); under master §4's severity × blast radius × likelihood it sits at HIGH. Both ratings are recorded; the executive summary uses HIGH.

### OBS-002 — 3 of 8 CLAUDE.md Performance-SLA rows have zero measuring metric
- Hierarchy: L2 Reliability/SRE & Observability › L3 SLA instrumentation
- Severity: HIGH   Priority score: high×medium×medium
- Status: VERIFIED   Existing item: adjacent to but not identical to CR-2026-008/#3295 (which is about aggregation/alerting for metrics that **already exist** — dispatch latency, payment failure rate). This finding is that 3 rows have no emitting metric at all, which #3295 doesn't cover.
- Adversary: auditor/regulator asking "show me your P95 for driver-location-update writes" during an incident review
- Evidence: grep for `duration_ms` metric emission near `routes/drivers/location.py` (only found `spinr_live_marker_write_failures_total`, a failure counter, no latency histogram), `routes/auth.py` (only `spinr_auth_otp_lockout_total`/`spinr_auth_refresh_recovered_total` event counters, no refresh-duration histogram), `routes/webhooks.py` (no `duration_ms` metric of any kind).
- What happens (plain language): "Driver location update (write) < 150ms — Failure impact: Stale ETA", "Auth token refresh < 200ms — UX stutter", and "Stripe webhook processing < 500ms — Payment backlog" are all published SLA commitments in CLAUDE.md with literally nothing counting whether they're met.
- Root cause: `utils/metrics.py`'s registry exists and is used correctly where adopted (dispatch/fare/payment/WS), but metric emission was never added at these 3 call sites when the SLA table itself was written — the table documents an aspiration, not an instrumented reality (same pattern ADR-010 already flagged for dispatch/fare before it was fixed there).
- Recommendation: add `metrics.observe("spinr_drivers_location_write_duration_ms", ...)` in `routes/drivers/location.py`'s write path, `spinr_auth_token_refresh_duration_ms` in the refresh handler, `spinr_payments_webhook_duration_ms{event_type}` in `routes/webhooks.py`'s dispatch loop — all snake_case, `_duration_ms` suffix per convention.
- Alternative considered: rely on synthetic checks (`monitoring/synthetic-checks.yaml`) instead of in-process histograms — rejected as primary signal because that file is explicitly a not-yet-wired spec, not a running probe.
- Blast radius: none — additive metric calls only, no behavior change.
- Rollout: additive, no flag. Rollback: revert the metric-emission lines.
- Verification to close: hit each endpoint locally/in a test, confirm the new series appears in `/metrics` output.

### OBS-003 — `print()` present in backend/ (hard CLAUDE.md violation, low blast radius)
- Hierarchy: L3 Logging discipline
- Severity: MEDIUM   Priority score: low blast radius but a hard, named rule
- Status: VERIFIED   Existing item: none found
- Adversary: auditor grepping for `print(` to confirm the "never print() in production code" rule
- Evidence: `backend/services/driver_import_service.py:954-965,1357-1368,1649+` (dry-run report printer), `backend/services/dormant_driver_sin_purge_service.py:199-207`, `backend/list_users.py:8,12` — 21 total `print(` call sites in `backend/` outside `tests/`/`scripts/`.
- What happens (plain language): these are CLI-invoked, manual dry-run/report tools (SIN purge preview, driver-import plan summary, user listing) — not request-serving code paths — so today's practical impact is low (output only reaches whoever runs the script directly, not lost telemetry for a live request). But per this audit's explicit, non-negotiable rule ("print() anywhere in backend/ — always a finding") and CLAUDE.md's "Never print() in production code," these are still in scope: `backend/` is production code even when a file is CLI-only, and a future refactor that calls these functions from a route/background loop would silently inherit the same gap.
- Root cause: dry-run/report helpers were written as human-readable CLI output rather than structured logs, likely because they're meant to be read directly on a terminal during a manual admin operation.
- Recommendation: replace with `logger.info(...)` (or a dedicated `click`/`rich` CLI-output helper distinct from the app's `logging`/`loguru` loggers) so these scripts' output is capturable if ever wired into an automated job.
- Alternative considered: leave as-is since these are dry-run/manual-only tools, not live traffic — rejected as the final answer only because the rule is stated as unconditional ("always a finding"); recommend the human owner confirm whether "print() anywhere" was meant to include manual CLI report scripts before spending the fix budget here, since it's genuinely lower value than OBS-001/002.
- Blast radius: `driver_import_service.py`/`dormant_driver_sin_purge_service.py` are called only from their own CLI entrypoints per this session's grep (not re-verified as fully exhaustive under the 25-min box).
- Rollout: additive, no flag.
- Verification to close: re-run `grep -rn "print(" backend --include=*.py` excluding `tests/`/`scripts/`, confirm zero hits.

### OBS-004 — `zoho_desk_integration.py` "ticket skipped" warnings have no paired metric
- Hierarchy: L3 what-to-log-vs-metric table compliance › degraded-but-recovered pairing
- Severity: MEDIUM   Priority score: medium×low×medium
- Status: VERIFIED   Existing item: none found
- Adversary: support-ops lead asking "how often is Zoho ticket creation failing silently" a month from now
- Evidence: `backend/services/zoho_desk_integration.py:80-83` (`logger.warning("Zoho ticket skipped for %s (%s): %s", ...)` on `ZohoDeskError`, `except Exception:` branch correctly escalates to `logger.error` with `exc_info=True`), same pattern at `:237,284`; `backend/routes/admin/support_tickets.py:388,831` similar warn-and-continue on Zoho scope/credential issues with no metric.
- What happens (plain language): a Zoho outage or a scope/credential drift degrades ticket auto-creation for complaints/L&F cases/disputes; it's correctly logged at `warning` (not swallowed, not escalated to Sentry-noise) but there's no counter to answer "how many tickets silently failed to open this week" without grepping logs.
- Root cause: the warning-log half of the CLAUDE.md "Degraded-but-recovered → warning log + metric" pair was implemented; the metric half wasn't.
- Recommendation: add `metrics.inc("spinr_admin_zoho_ticket_skipped_total", {"table": table})` alongside each of these warnings.
- Alternative considered: escalate to Sentry instead — explicitly rejected per CLAUDE.md's own rule (this is exactly the "noise" case the table warns against; the `except Exception` branch already covers genuinely unexpected failures via `logger.error`).
- Blast radius: `_link_ticket` is called from `create_ticket_for_complaint`, the L&F case path, and the dispute path — all 3 call sites get the fix for free once added to the one shared function.
- Rollout: additive, no flag.
- Verification to close: trigger a `ZohoDeskError` in a test, assert the counter incremented.

### OBS-005 — `payment_service.py:566` skips a dispute ledger write on unresolved `user_id`, warning-only, no metric
- Hierarchy: L3 Payments observability
- Severity: MEDIUM (not BLOCKER — this is a guarded precondition skip, not a raw DB/auth/payment error being swallowed)
- Status: VERIFIED   Existing item: none found
- Adversary: plaintiff's lawyer / auditor reconciling dispute ledger entries against Stripe's dispute list and finding a gap with no trace of why
- Evidence: `backend/services/payment_service.py:558-568` — `if not user_id: logger.warning("[B27] Skipping dispute ledger write — no resolvable user_id for dispute {}", dispute_id); return`.
- What happens (plain language): a Stripe dispute event whose `user_id` can't be resolved gets no `financial_events` row at all — deliberate (avoids an FK violation, per the docstring), but silent beyond a warning log; nothing counts how often this happens.
- Root cause: guard exists to protect the FK constraint, correctly documented as deliberate, but the observability half (metric so this is monitorable, not just discoverable via log search after the fact) was not added.
- Recommendation: add `metrics.inc("spinr_payments_dispute_ledger_write_skipped_total")` at this site; consider whether it should also be a Sentry `capture_message` (domain=payments) rather than warning-only, since "we have no ledger record of a real Stripe dispute" is arguably closer to "user-visible error" (reconciliation will eventually surface it) than "degraded but recovered" — flagging for the payments-domain owner to decide, not deciding it here.
- Alternative considered: leave as warning+return since it's a rare, guarded edge case — plausible, but the missing metric means nobody would know if it stopped being rare.
- Blast radius: isolated to this one guard clause in `record_dispute_ledger_entries` (name inferred from context, not independently re-verified this pass).
- Rollout: additive.
- Verification to close: add a test asserting the counter increments when `user_id` is falsy.

### OBS-006 — No CI check enforces domain-tag presence on ERROR-level loguru calls
- Hierarchy: L3 CI/CD guardrails
- Severity: HIGH (root cause of OBS-001)   Priority score: high leverage — one check prevents an entire recurrence class
- Status: VERIFIED   Existing item: none — `test_loguru_call_conventions.py`'s existing checks (`test_no_percent_style_placeholders_in_loguru_calls`, `test_no_exc_info_kwarg_in_loguru_calls`, `test_no_extra_kwarg_in_loguru_calls`, `test_every_logger_binding_resolves`, `test_scan_covers_reexported_loggers_and_only_those`) do not check tag/domain presence — confirmed by reading the file's function list (`backend/tests/test_loguru_call_conventions.py:222-335`).
- Adversary: the next engineer adding a new `routes/rides/*.py` error path who has no signal they've reintroduced OBS-001
- Evidence: as above; this is the CI-enforcement counterpart to OBS-001.
- What happens (plain language): new dispatch/rides/drivers error-handling code keeps landing untagged forever, because nothing red-flags it — same shape as the CLAUDE.md-cited "10 audits over 6 weeks, no standing check" pattern this task explicitly warns about, just for the tagging defect class rather than the warning-and-continue defect class (which, per this session's sampling of `payments.py`/`auth.py`/`dispatch_service.py`, appears to already be well-disciplined — see steelman).
- Root cause: the loguru-conventions test suite was built to catch loguru API misuse (C60/C65/C69's defect class), not tag-completeness — a related but distinct concern that was never added.
- Recommendation: add `test_error_level_loguru_calls_bind_domain` to the same file, scanning for `.error(`/`.critical(`/`.opt(exception=True).error(` calls not preceded (in the same statement or an enclosing `.bind()` chain, or a module-level `logger = logger.bind(domain=...)` reassignment like `outbox_worker.py:28`) by a `domain` key.
- Alternative considered: enforce via code review only (agent-run `spinr-observability-auditor` before merge) — insufficient per the escalation history CLAUDE.md itself cites; a standing CI check is the whole point.
- Blast radius: adding a test is zero-risk to runtime; will initially fail loudly against ~290 existing call sites, so it should land as a new test with an initial allowlist/baseline (grandfather existing offenders, block new ones) rather than a big-bang fix, mirroring CLAUDE.md's incremental-rollout gate.
- Rollout: additive test, baseline-gated (fail only on new violations) to avoid blocking unrelated PRs on the full backfill.
- Verification to close: add the test with a baseline file, confirm it passes on current `main`, confirm it fails when a new unbound `.error(` is introduced in a scratch branch.

## (d) Tagging rollout plan — see `tagging-plan.md` (same content)

## (e) Steelman — what's already right

- The Sentry pipeline itself is unusually mature for this stage: a dedicated PIPEDA scrubber (`utils/sentry_scrub.py`) with key-name AND value-pattern redaction, a documented "never drop an event on scrub failure" invariant, a loguru→Sentry bridge specifically built to carry domain/ride_id context (`tags_from_log_extra`), and a production-gated boot-verification event. This is well beyond "add sentry_sdk.init and hope."
- `payment_service.py`, `auth.py`, `dispatch_service.py` sampled `logger.warning`-then-continue sites are, without exception in this session's sample, well-documented, genuinely-recoverable degraded paths (RPC-unavailable→legacy-path fallback, presence-filter Redis-unreachable→fail-open with a comment explaining why, allowance-cap contention→reroute to master wallet) — none of the classic "swallow a real DB/auth/payment error at warning level" BLOCKER pattern this lane is specifically tasked to hunt was found in the sampled set. This suggests the 10-prior-audits lesson has actually been absorbed in the domains sampled.
- `test_loguru_call_conventions.py` is a genuinely good, statically-scanning regression guard against the C60/C65/C69 defect class (all three closed) — it resolves logger re-exports correctly and has its own meta-tests (`test_scan_actually_sees_the_backend`, `test_detectors_catch_the_original_defects`).
- rider-app/driver-app (`shared/services/errorReporting.ts`) and admin-dashboard Sentry configs both correctly set `surface`, scrub PII defensively (`beforeSend`/`beforeBreadcrumb`), and admin-dashboard even checks Sentry's own ingest region against a documented accepted-risk list at boot.
- Metric naming in every call site found this pass followed the `spinr_<domain>_<metric>_<unit>` convention correctly; the deprecated dotted spelling was not found anywhere in code (only in CLAUDE.md's own "do not use" example).

## (f) NOT VERIFIED (time-boxed — 25 min)

- Did not independently re-verify every ADR-010-cited line number (`routes/rides/matching.py:726`, `routes/drivers/ride_flow.py:293/315`) — trusted the ADR's own table where the metric name itself was corroborated by a second grep.
- Did not check `zoho_ticket_service_area.py`, `routes/support.py`, `admin/support.py` beyond the logger-declaration/level grep shown — did not read every error branch for level-correctness in those 3 files.
- Did not verify `test_every_logger_binding_resolves`'s exact enforcement scope beyond its name and file location — read the function list, not its full body.
- Did not check `driver_import_service.py`'s callers to confirm the `print()` sites are truly never reachable from a route/background loop (stated as "per this session's grep," not exhaustively traced).
- Did not check migration-apply duration instrumentation in `run_migrations.py` itself (assumed absent based on no metric-name match; not read end-to-end).
- Did not verify `.github/labeler.yml` or PR-label taxonomy usage in detail (found the file exists, did not check whether any of §4's keys appear as PR labels).
- Sampled ~15 `logger.warning` sites for the BLOCKER pattern across payments/rides/drivers/auth/dispatch, not all 97+ hits found by the broad grep — cannot claim the BLOCKER pattern is absent fleet-wide, only absent in the sample read.

VERDICT (lane): MISSING SIGNAL — WILL BE INVISIBLE IN PROD for the dispatch/drivers Sentry-triage path (OBS-001) and for 3 of 8 published SLA rows (OBS-002); FIX BLOCKERS is not the right verdict since nothing found this pass silently swallows a live DB/auth/payment/dispatch error (the specific hard-BLOCKER pattern this lane is escalated to hunt) — the gap is tag/metric *completeness*, not silent failure.
