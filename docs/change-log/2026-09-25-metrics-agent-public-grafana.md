# Change Impact & Risk Log

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | Claude Code (session), requested by user |
| Surface(s) | backend (metrics-agent — standalone Fly app; not the production backend runtime, rider-app, driver-app, or admin-dashboard) |
| Domain (Sentry tag) | admin (observability infra) |
| PR / commit link | (uncommitted at time of writing — user commits separately) |
| Related issue or gap ID | `metrics-agent/README.md` "Logs & Grafana"; see also `docs/change-log/2026-09-25-metrics-agent-vector-copy-path.md` (same day, unrelated bug fix on the same app) |

## 1. Issue / gap identified

User wanted to view Grafana (dashboards + the 7-day Loki log store) from
any device without running `fly proxy` first — the prior design required a
Fly account, `flyctl`, and a WireGuard tunnel for every viewing session.

## 2. Root cause

Not a bug — a deliberate original design choice (`fly.toml`'s own header
comment: "intentionally not exposed publicly"). The user explicitly asked
to change that tradeoff.

## 3. Fix / remediation

Added an `[http_service]` block to `metrics-agent/fly.toml` forwarding
`https://spinr-metrics-agent-yyz.fly.dev` (port 443, force_https) to
Grafana's internal port 3000 only. `auto_stop_machines = "off"` and
`auto_start_machines = false` are both set explicitly so Fly's normal
scale-to-zero behavior for HTTP services never stops this machine —
metrics scraping and log ingestion must keep running regardless of whether
anyone is looking at Grafana. Presented three options to the user first
(public URL / WireGuard-peer-per-device / public URL + extra auth layer);
user chose the plain public URL.

Alternative considered and not chosen: a Fly WireGuard peer per device
(zero public exposure, but each new device needs a one-time config
import) — rejected by the user in favor of simplicity. A second auth layer
(Fly `basic_auth` or Cloudflare Access in front of Grafana) was also
offered and declined; noted as a natural follow-up if the exposure ever
feels too wide (see "Known gaps" in the README).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to `metrics-agent/fly.toml`.** No change to
  `backend/`, `rider-app/`, `driver-app/`, or `admin-dashboard/`. No change
  to Alloy's metrics-scrape path, which is unrelated to this HTTP service.
- **Real security posture change**, not just infra config: Grafana — and
  therefore 7 days of backend logs plus the Grafana Cloud metrics view —
  is now reachable by anyone on the internet who has the URL and either
  guesses/obtains the admin password or exploits a Grafana vulnerability.
  Previously that required a Fly account with access to this org.
  `GRAFANA_ADMIN_PASSWORD` should now be treated as an internet-facing
  credential (strong, unique, rotated if ever exposed).
- Anonymous access (`GF_AUTH_ANONYMOUS_ENABLED=false`) and self-sign-up
  (`GF_USERS_ALLOW_SIGN_UP=false`) were already disabled in `entrypoint.sh`
  before this change and remain the only gate — unchanged by this diff,
  but now doing more load-bearing work than before.
- The pre-existing Alloy UI on port `:12345` (no auth at all) is explicitly
  **not** touched by the new `[http_service]` block — confirmed it stays
  reachable only via 6PN / `fly proxy`, not the public internet.
- No PII scrubbing change: logs shown in Grafana are exactly what the
  backend already logs today (CLAUDE.md's no-GPS/no-phone/no-name/no-email
  rules still apply at the source, unchanged); this fix only changes who
  can reach the viewer, not what's logged.

## 5. User-experience effect

None visible to rider/driver/corporate-admin. Only affects whoever has
Fly/Grafana access to this internal observability tool.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `metrics-agent/fly.toml` | Added `[http_service]` (port 3000 → public 443, autostop/start pinned off); updated header comment | Expose Grafana publicly per user request, without accidentally letting Fly scale the machine to zero |
| `metrics-agent/README.md` | Updated architecture diagram, process table, "Open Grafana" instructions, and "Known gaps" to describe the public URL and the new credential-exposure tradeoff | Keep the doc accurate — it previously asserted "nothing here is public" |

## 7. Before / after

```toml
# Before
# No [http_service] block: this app makes only outbound connections
# ...
app = "spinr-metrics-agent-yyz"
```

```toml
# After
app = "spinr-metrics-agent-yyz"
...
[http_service]
  internal_port = 3000
  force_https = true
  auto_stop_machines = "off"
  auto_start_machines = false
  min_machines_running = 1
```

## 8. Rollback plan

Remove the `[http_service]` block and redeploy (`fly deploy` from
`metrics-agent/`) to immediately go back to proxy-only access — no data
migration, no user-facing flag, single-file config revert. If credential
compromise is suspected before rollback, rotate `GRAFANA_ADMIN_PASSWORD`
first (`fly secrets set`, see README "Rotate the Grafana password") since
that closes the hole faster than waiting on a redeploy.

## 9. Verification performed

- [x] Confirmed the `[http_service]` block only forwards to Grafana's port
      (3000), not Alloy's (12345) or Loki's (3100, which isn't even
      network-reachable outside the machine's loopback).
- [x] Explicitly verified `auto_stop_machines`/`auto_start_machines` are
      set so this always-on background collector can't be scaled to zero
      by Fly's default HTTP-service behavior.
- [ ] Not yet verified at time of writing: an actual `fly deploy` + hitting
      the public URL from outside the Fly network (planned as the
      immediate next step).
- [ ] No automated test exists for Fly network topology; this is
      infra-only and verified by direct inspection, not a test suite.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (remove the block, redeploy)
- [x] Blast radius is stated, not assumed: isolated to this one app's
      `fly.toml`
- [x] This *is* a user-facing (to internal users, not riders/drivers)
      behavior change and is documented as such above — not silent
