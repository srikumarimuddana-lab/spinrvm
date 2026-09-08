# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-08 |
| Author | Claude (session implementing PR #5085's hardening plan) |
| Surface(s) | backend (observability + deploy config — no request-path behavior change) |
| Domain (Sentry tag) | admin (platform/infra observability, not a runtime domain) |
| PR / commit link | branch `claude/pr-5085-5079-hardening-5a2aj7` |
| Related issue or gap ID | F6, validated in PR #5085; tracked as `ACTION_ITEMS.md` C82 |

## 1. Issue / gap identified

`backend/utils/metrics.py` keeps in-process counters/gauges/histograms and exposes them via `/metrics` for Prometheus-style scraping. Fly runs the backend with `UVICORN_WORKERS=2` (`backend/fly.toml`) — two separate OS processes behind one port. Each scrape lands on whichever worker the kernel happens to hand the connection to, and each worker's counters only reflect requests *it* processed. Without a per-worker label, successive scrapes of the same metric name look like one flapping series: a scrape hitting worker B after worker A has processed more requests reads as a *decrease*, which Prometheus's `rate()`/`increase()` interpret as a process restart, silently discarding real data.

## 2. Root cause

`render_prometheus()` never distinguished which process produced a given counter/gauge/histogram reading — a gap that didn't exist when the module was written for a single-process deploy, and was never revisited when `UVICORN_WORKERS` was set to 2.

## 3. Fix / remediation

`render_prometheus()` now attaches a `worker_pid` label (read live via `os.getpid()` on every call, not cached at import time) to every emitted series, via a new `_with_worker_pid()` helper that also strips any caller-supplied `worker_pid` key first (a duplicate label key would render as an invalid Prometheus line, added after `spinr-observability-reviewer` flagged the original version had no such guard). This makes each worker's series genuinely distinct to Prometheus, so `rate()`/`increase()` stay correct per-worker, and cross-worker totals become an explicit aggregation choice downstream rather than an accidental smear. The docstring states the resulting query contract: counters/histograms are additive (`sum by (...)` is correct), gauges are not (each worker independently reports what's conceptually one shared value, e.g. Redis memory — use `max by (...)`, never `sum by (...)`).

**Live-read vs. cached PID, corrected**: reading `os.getpid()` live on every call (rather than caching it once at import) is still the right, cost-free choice — but the reasoning for *why* it matters was initially stated wrong and is corrected here. `spinr-observability-reviewer` checked the installed `uvicorn==0.52.4` source directly: `--workers N` uses `multiprocessing.get_context("spawn")`, not `fork` — each worker is a fresh interpreter that re-imports the app itself, so a module-level cached PID would in fact have been correct under Uvicorn's actual mechanism. The scenario that would break a cached PID (a prefork server with app preloading, e.g. `gunicorn --preload`) doesn't apply to this repo's Uvicorn-based deploy. Reading live remains strictly the safer and more future-proof choice regardless.

**`UVICORN_WORKERS` default change — reverted, not shipped.** The original version of this change also lowered `backend/Dockerfile`'s and `railway.json`'s `${UVICORN_WORKERS:-4}` fallback to `${UVICORN_WORKERS:-2}`, reasoning that Railway shares Fly's "shared-cpu-1x/1gb-class memory budget." `spinr-observability-reviewer` caught that this was an unverified assumption: `docs/adr/006-railway-deployment.md` documents `--workers 4` as a *deliberate*, resource-sized decision for Railway specifically (§"Negative/trade-offs": "4 processes × 7 background loops = 28 loop instances across 2 replicas"), and nothing in this repo states Railway's actual current instance size. Copying Fly's number onto Railway without verifying that would have been exactly the kind of unverified capacity assumption CLAUDE.md's "escalate, don't silently ship" gate exists to catch — so both files were reverted back to `${UVICORN_WORKERS:-4}` in this same change, and that half of C82's original plan is left open pending someone actually checking Railway's current plan/instance size (tracked back in `ACTION_ITEMS.md` C82's entry). The `worker_pid` labeling fix above stands on its own and does not depend on this.

## 4. Risk & impact on existing functionality

- **Blast radius**: `render_prometheus()` is called from exactly two places — `backend/server.py`'s `/metrics` handler and `backend/worker.py`'s own `/metrics` handler (the standalone outbox-worker process). Both are read-only text-exposition endpoints; no other code parses `render_prometheus()`'s return value. `iter_counters()` and `snapshot()` (used internally by other tests/call sites for raw counter inspection) are unchanged — the label is added only at exposition time, not to the underlying stored keys.
- **`worker_pid` naming precedent**: `routes/admin/monitoring.py`'s `fanout_status` endpoint already reports `"worker_pid": os.getpid()` for the identical "which process answered" reasoning — this change reuses that established name rather than inventing a new one.
- **Existing Grafana alert rules unaffected**: `metrics-agent/grafana/alert-rules.yaml`'s PromQL already aggregates with bare `sum(...)` or `sum(...) by (le)` — both correctly sum across every label including the new `worker_pid`, since `by (le)` explicitly groups out everything except `le`. No gauge is currently queried with `sum()` anywhere in this repo (grepped `alert-rules.yaml` and `metrics-agent/grafana/dashboard-panel.json`) — the gauge-aggregation caution in the new docstring is preventive documentation for future dashboard/alert authors, not a fix to an existing incorrect query.
- **`UVICORN_WORKERS` default**: left unchanged (see the correction above) — `backend/Dockerfile` and `railway.json` both still default to 4 if the env var is unset, matching `docs/adr/006-railway-deployment.md`'s documented Railway sizing. Root `Dockerfile` (hardcoded `--workers 1`, no env-var interpolation at all) was untouched throughout — `bootstrap-fly.yml`'s own comment confirms it isn't used for Fly's build (`backend/Dockerfile` is).
- **Not in scope**: the `should_spawn_on_api()` background-loop topology work referenced alongside this item in `ACTION_ITEMS.md` (which of the 41 loops run on which replica type) is a separate WS-3 concern and is untouched here — every loop already holds its own replay-safety guard (atomic claim / idempotency key / Redis leader lock) independent of worker count.

## 5. User-experience effect

None — internal observability and deploy-config only, no rider/driver/corporate-admin/internal-admin-facing change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/metrics.py` | `render_prometheus()` attaches a live `worker_pid` label to every series via new `_with_worker_pid()` helper, which also strips any pre-existing `worker_pid` key to prevent a duplicate-label render | Stop a multi-worker scrape from reading as a flapping/decreasing counter |
| `backend/tests/test_metrics_histogram.py` | Updated the one exact-string assertion the new label changed; added `TestWorkerPidLabel` covering counters, gauges, histograms, label coexistence, and the duplicate-key guard | Cover the new behavior; keep the pre-existing exposition test accurate |
| `backend/Dockerfile`, `railway.json` | Touched during review, then reverted — no net change | See "UVICORN_WORKERS default change — reverted" above |

## 7. Before / after

```python
# Before
def render_prometheus() -> str:
    ...
    for labels_tuple, value in sorted(bucket.items()):
        lines.append(f"{name}{_format_labels(labels_tuple)} {value}")
```

```python
# After
def render_prometheus() -> str:
    worker_pid = str(os.getpid())
    ...
    for labels_tuple, value in sorted(bucket.items()):
        lines.append(f"{name}{_format_labels(_with_worker_pid(labels_tuple, worker_pid))} {value}")
```

Concrete scenario — two workers, a scrape alternating between them:

| | Before | After |
|---|---|---|
| Scrape 1 (hits worker A, 50 reqs served) | `spinr_x_total 50` | `spinr_x_total{worker_pid="111"} 50` |
| Scrape 2 (hits worker B, 3 reqs served) | `spinr_x_total 3` (reads as a 47-request *decrease*) | `spinr_x_total{worker_pid="222"} 3` (a distinct, still-monotonic series) |
| `rate()` over the two scrapes | Wrong — Prometheus assumes a process restart, resets its counter model | Correct — each worker's own series is monotonic; `sum by (...)` combines them |

## 8. Rollback plan

`git revert` — pure exposition-format change to `backend/utils/metrics.py` only (the deploy-config files end this change with no net diff from `main`). No schema, no data, no flag. Reverting restores the previous per-metric line format exactly. A scraper/dashboard built against the new `worker_pid` label during the window this is live would need updating if reverted, but nothing in this repo's own tracked config (grepped) depends on it yet.

## 9. Verification performed

- [x] Automated tests: `pytest tests/test_metrics_histogram.py tests/test_utils_extended.py tests/test_metrics_auth.py tests/test_server_coverage.py -q` — 199 passed, 1 skipped, 0 failed on the labeling fix; re-ran `tests/test_metrics_histogram.py` alone after the collision-guard follow-up fix — 15 passed.
- [x] Confirmed (by stashing the change and re-running the identical multi-file test batch) that 5 unrelated `test_dispatch_metrics.py` failures seen during this work are pre-existing test-order pollution in this repo, reproducing identically with or without this change, and unrelated to it.
- [x] `ruff check`/`ruff format --check` on the edited backend file — clean (one pre-existing `B905 zip() without strict=` finding on an unmoved, un-widened line was left alone per this repo's surgical-changes convention).
- [x] Grepped `metrics-agent/grafana/alert-rules.yaml` and `dashboard-panel.json` for any gauge queried with `sum(...)` — none found, confirming the gauge-aggregation caution added to the docstring is preventive, not a fix to an existing wrong query.
- [x] Grepped every call site of `render_prometheus()` (`backend/server.py`, `backend/worker.py`) and every test file referencing it, to confirm no consumer parses exact label sets in a way this change would break beyond the one test fixed above.
- [x] `spinr-observability-reviewer` subagent review: no blockers. Findings acted on: (1) missing duplicate-`worker_pid` collision guard — fixed, test added; (2) `os.getpid()`-live-vs-cached reasoning stated the wrong mechanism (said "fork," Uvicorn actually uses `spawn`) — doc corrected above, code behavior was already correct either way; (3) the `UVICORN_WORKERS` default change wasn't backed by verified Railway capacity data — reverted, see above. Not actioned: the gauge-aggregation-contract-is-unenforced and Grafana-Cloud-cardinality-growth warnings, both of which are about external infrastructure (Grafana Cloud dashboards/alerts) not yet provisioned in this repo (per `metrics-agent/README.md`'s "What's blocked" section) — nothing in-repo to change today; flagged here for whoever provisions that pipeline.

## What was NOT verified

Not exercised against a real multi-worker Fly deployment — the "stops the flapping" claim is reasoned from Prometheus's documented counter/rate semantics and this repo's own `fly.toml` topology (`UVICORN_WORKERS=2`), not observed against a live scrape hitting alternating worker PIDs (no staging/production metrics-agent access from this session). Railway's actual current instance size/plan was not determined — the `UVICORN_WORKERS` default change was reverted specifically because this was unverifiable from this session, not because it was confirmed unnecessary.
