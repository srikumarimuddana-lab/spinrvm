# Change Impact & Risk Log

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | Claude Code (session), requested by user |
| Surface(s) | backend (metrics-agent — standalone Fly app; observability only) |
| Domain (Sentry tag) | admin (observability infra) |
| PR / commit link | (uncommitted at time of writing) |
| Related issue or gap ID | Follow-on to `2026-09-25-metrics-agent-vector-copy-path.md` and `2026-09-25-metrics-agent-public-grafana.md` (same day) |

## 1. Issue / gap identified

User wanted a Grafana dashboard to break down backend log severity
(INFO/WARNING/ERROR/CRITICAL), count occurrences, catch crash/segfault-style
signatures, and drill into the exact log line for further analysis.

## 2. Root cause

Not a bug — a net-new capability request. While building it, found a real
pre-existing gap: the Loki `level` label (set by Vector from Fly's own log
envelope) is `info` for **every** backend log line regardless of actual
severity, because the backend's loguru sink (`server.py`:
`logger.add(sys.stderr, ...)`) writes everything to stderr uniformly. The
`level` label was never a usable severity signal — this was previously
undocumented and the README's own example query
(`{app=..., level="error"}`) would silently have matched nothing.

## 3. Fix / remediation

Added a provisioned Grafana dashboard
(`grafana/provisioning/dashboards/files/backend-log-analysis.json` +
`.../dashboards/dashboards.yaml`) that extracts the real severity from
`record.level.name` inside the backend's loguru JSON at query time, using
an explicitly-named field (`app_level`) to avoid Loki's silent
`level_extracted` rename collision with the pre-existing `level` label —
hit this exact bug while building the dashboard and verified the fix
against live data before shipping it (first version returned zero results
for every count; confirmed root cause via raw Loki query, fixed, re-verified
non-zero real counts before redeploying). Also corrected the README's
now-known-wrong `level="error"` example query.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to `metrics-agent/grafana/`.** Pure addition —
  no existing datasource, alert rule, or query path was changed. The
  `grafana/provisioning-optional/` Grafana Cloud metrics datasource and the
  existing `dashboard-panel.json`/`alert-rules.yaml` (dispatch/payment
  alerts) are untouched.
- No change to what the backend logs, how Vector/Loki ingest it, or
  retention. Purely a new read-only view over existing data.
- The `README.md` LogQL-example correction is also isolated documentation;
  no code depends on that example.

## 5. User-experience effect

None visible to rider/driver/corporate-admin. Affects only whoever has
Grafana access to this internal tool — for them, a genuinely new severity
breakdown that previously would have silently shown nothing if attempted
via the label filter this repo's own docs suggested.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `metrics-agent/grafana/provisioning/dashboards/dashboards.yaml` | New file — dashboard-provisioning config | Auto-load the dashboard on every deploy, no manual import |
| `metrics-agent/grafana/provisioning/dashboards/files/backend-log-analysis.json` | New file — the dashboard itself | Severity counts, trend, drill-down, exception/crash panels |
| `metrics-agent/README.md` | Corrected the `level="error"` example query; added "Dashboards" section | The old example was silently wrong; document the real extraction pattern and the new dashboard |

## 7. Before / after

```logql
# Before (README example — silently matches nothing)
{app="spinr-backend-yyz", level="error"}
```

```logql
# After (actually works)
{app="spinr-backend-yyz"} | json app_level="record.level.name" | app_level="ERROR"
```

## 8. Rollback plan

Delete the two new files and redeploy — Grafana's file-provisioner removes
a dashboard whose source file disappears (`disableDeletion: false` in
`dashboards.yaml`). No data or existing config affected.

## 9. Verification performed

- [x] Validated dashboard JSON is well-formed (`python -m json.tool`
      equivalent, `json.load` succeeded, 13 panels present).
- [x] Deployed and confirmed the dashboard is provisioned and reachable via
      `GET /api/dashboards/uid/spinr-backend-log-analysis` and
      `GET /api/search`.
- [x] Caught and fixed a real bug before calling it done: first version's
      count queries all returned 0 results. Diagnosed via a raw Loki query
      showing Loki had renamed the extracted field to `level_extracted`
      (collision with the pre-existing `level` label). Renamed to
      `app_level`, re-verified directly against Loki (non-zero real counts:
      288 INFO / 2 WARNING / 1 CRITICAL in a 1h test window) before
      redeploying the corrected version.
- [x] Confirmed the CRITICAL-count panel surfaces a real, meaningful event
      end-to-end: a `[SAFETY]` route-deviation incident notification from
      `features.py:notify_safety_team`, at CRITICAL severity as designed —
      not a false positive.
- [ ] Not verified: the dashboard's visual rendering in an actual browser
      (only verified via the HTTP API — panel JSON structure and underlying
      query correctness, not pixel layout).

## 10. Sign-off

- [x] Rollback plan is concrete (delete files, redeploy)
- [x] Blast radius stated: isolated to two new files + one doc correction
- [x] No silent behavior change — this is additive; the one correction
      (README example) fixes a previously-wrong doc, not a working feature
