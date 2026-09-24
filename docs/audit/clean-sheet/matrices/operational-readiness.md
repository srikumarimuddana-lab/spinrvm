# Operational Readiness Matrix — R13

Per `greenfield-extensions.md` §7 readiness definitions (**Operationally ready** =
metric + alert + runbook + support article + owner + training note + rollback/flag).
Scope: the critical paths and runbooks this lane (Reliability/SRE & Observability)
owns or directly touches. Not a full inventory of every runbook in
`docs/runbooks/` (70 files) — see `02-findings/reliability.md` §0 for what was and
wasn't read this pass. Owner column reflects `.github/CODEOWNERS`'s path routing
where it exists; **placeholder** means CODEOWNERS routes the path but to an
unassigned team handle (`ACTION_ITEMS.md` E8).

Readiness color: **GREEN** = metric+alert+runbook+owner+tested, all real.
**YELLOW** = most pieces exist but at least one is stale, untested, or gated
behind an unconfirmed human-only step. **RED** = the drill/alert has never run
or the mechanism doesn't exist.

## Critical paths

| Critical path | Owner | Runbook exists & current | Alert exists | Dashboard exists | Tested/drilled | Readiness | Evidence |
|---|---|---|---|---|---|---|---|
| Cross-provider failover (Fly ↔ Railway) | placeholder (infra) | Yes, detailed and current (`railway-fly-failover.md`, last touched 2026-09-21) | Standby-parity monitor (config drift only, not live-traffic failover) | `standby-parity` GitHub issue (auto-opened/closed) | **No** — drill log empty (`:331`); C1 open | **RED** | reliability.md REL-004, ACTION_ITEMS C1 |
| Backup/restore (Supabase PITR) | placeholder (data+infra) | Yes, but self-flags its own core assumption as "UNCONFIRMED AND LIKELY FALSE" (`pitr-restore.md:22-31`) | None (this is a manual-invoke runbook, not a monitored path) | None | **No** — `verify_restore.py` exists, unit-tested against mocks only, never run against a real restored branch | **RED** | reliability.md REL-004, ACTION_ITEMS E7 |
| Background-loop liveness (all 44 loops) | placeholder (backend/CODEOWNERS routes `core/`) | Partial — `lifespan.py`'s watchdog registration self-check is itself the "runbook" (code, not doc); no human-readable runbook for "a loop is stale, what do I do" beyond generic Railway-log triage | Yes, mechanism exists (`utils/loop_alert.py`) but (a) gated entirely on `ALERT_WEBHOOK_URL` (UNKNOWN whether set) and (b) ~30/44 loops have a mistuned or absent threshold | **No** — the JSON status endpoint that would show this (`routes/main.py`) is unmounted dead code; `/metrics` has no loop-status gauge | **No** — no incident in `docs/incidents/` involves a loop stall being caught this way | **RED** | reliability.md REL-001, REL-002, §3 |
| WebSocket cross-replica fan-out | placeholder (backend) | `docs/runbooks/websockets.md` exists (not read in full this pass) | `spinr_ws_fanout_duration_ms` metric exists but not scraped in production (C11); no distinct alert for the Redis-down silent-drop case (REL-005) | No live dashboard (C11) | **No** — no drill found; behaviour is documented/reasoned, not exercised under a forced Redis outage | **YELLOW** — the mechanism (`ws_pubsub.py`) is well-built and unit-tested (per steelman), but the operational signal around it (alert, dashboard) is absent | reliability.md REL-005, §6 |
| Redis outage (any of the 3 URLs) | devops+backend (per `redis-down.md:3`) | Yes, `docs/runbooks/redis-down.md` — detailed, per-feature impact table | Runbook's own "Known Gaps" section says no dedicated rate-limit-Redis alert exists (not independently re-verified this pass — see reliability.md §12) | No | **Unclear** — runbook exists in enough detail to suggest prior familiarity, but no dated drill record found | **YELLOW** | reliability.md §4, `redis-down.md:24-30,97-101` |
| SLA-breach alerting (dispatch latency, payment failure rate — CLAUDE.md Performance SLA table) | placeholder (backend/infra) | ADR-010 design doc exists and is detailed | 2 of 8 SLA rows have a drafted alert rule (not live); 6 of 8 have no metric to alert on at all | 1 dashboard panel drafted, not imported into a real Grafana Cloud account | **No** — nothing has ever fired in production (C11 not deployed) | **RED** | reliability.md §6, ACTION_ITEMS C11 |
| Deploy rollback — backend (Fly/Railway) | placeholder (infra) | **No dedicated runbook found** (admin-dashboard has one, `admin-rollback.md`; backend does not) | N/A | N/A | **No** | **RED** | reliability.md §8 |
| Deploy rollback — admin-dashboard | frontend (per CODEOWNERS, not independently confirmed) | Yes — `admin-rollback.md`, clear, Vercel-native, <2min instant path | N/A (manual trigger by design) | Vercel deployment list itself | Plausible (Vercel's own UI is the mechanism, low-risk by construction) but no dated drill log found in-repo | **YELLOW** | `admin-rollback.md:1-40` |
| Migration apply (schema deploys) | placeholder (migration owner, per HIST-006 "owner deferred") | `apply-supabase-schema.yml` exists (`workflow_dispatch`-only) | No | `--status`/`--dry-run` CLI output only, not a monitored dashboard | **Partially** — the mechanism has been used (some migrations applied), but 8 are pending today (C125) and 116 are untracked (G2), so the *process* as a whole is unreliable, not the tool itself | **RED** | reliability.md §8, HIST-006 |
| Process-role capacity split (`SPINR_PROCESS_ROLE`) | placeholder (infra) | Canary config exists (`fly.worker-canary.toml`) but not the production config | N/A — this is a capacity/cost control, not a user-facing alert path | N/A | **Partially** — the canary config implies some prior validation, but the real `fly.toml`/`railway.json` never adopted it, so production has never run split | **RED** (as deployed) | reliability.md REL-003 |

## Runbooks in this lane's direct scope

| Runbook | Current? | Owner named | Tested/drilled | Readiness |
|---|---|---|---|---|
| `docs/runbooks/railway-fly-failover.md` | Yes (2026-09-21), unusually candid about its own unverified assumptions | infra (implied, not a formal CODEOWNERS handle) | No | **YELLOW** — excellent as written, RED on the one thing that matters (has it run) |
| `docs/runbooks/pitr-restore.md` | Self-flags its core RTO/RPO assumption as likely wrong | data+infra (named in header) | No | **RED** |
| `docs/runbooks/redis-down.md` | Uses older terminology (`reports/incidents/`, `OPEN-ITEMS-TRACKER`) not matching this repo's current `docs/incidents/`/`ACTION_ITEMS.md` naming — possible drift, not independently dated this pass | devops+backend (named in header) | Unclear | **YELLOW** |
| `docs/runbooks/capacity-scaling.md` | **No** — A3-005 (rapid baseline) already found it stale against the real `fly.toml` (suspend vs off, 1GB vs 4/2GB, 18 vs 45 loops) | infra (implied) | N/A | **RED** (per A3-005, cited not re-derived) |
| `docs/runbooks/on-call.md` | Not read this pass | Unknown | Unknown | **UNKNOWN** |
| No backend deploy-rollback runbook | N/A — doesn't exist | N/A | N/A | **RED** (gap, not a stale doc) |

## Summary

Of the 10 critical paths tabled above: **0 GREEN, 3 YELLOW, 7 RED.** The
consistent pattern across every RED row is the same one this report's Top 5
names: a well-built mechanism (atomic claims, a self-verifying watchdog, a
documented failover procedure, a real canary config for the process-role split)
paired with an unexercised or unverified final step — a drill that hasn't run, an
alert channel of unknown configuration, or a production config that never
adopted a working feature branch's pattern. This is a different shape of risk
than "the engineering is wrong" — it is closer to "the engineering is done and
the operational proof is not," which is the gap this matrix exists to make
visible rather than assume away.
