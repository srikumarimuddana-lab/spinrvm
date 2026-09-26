# Change Impact & Risk Log

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | Claude Code (session), reviewed by user |
| Surface(s) | backend (metrics-agent — standalone Fly app, not the production backend runtime) |
| Domain (Sentry tag) | admin (observability infra; no rider/driver/payment surface touched) |
| PR / commit link | (uncommitted at time of writing — user commits separately) |
| Related issue or gap ID | ACTION_ITEMS.md C11 (metrics aggregation); `metrics-agent/README.md` "Logs & Grafana" |

## 1. Issue / gap identified

The 7-day Fly log store (Loki/Vector/Grafana on `spinr-metrics-agent-yyz`,
merged commit `3f2f169a8c`) was never actually running in production despite
`fly releases` showing a "complete" deploy 12h before this was investigated,
the `fly_logs` volume attached, and all required secrets set. `df -h /data`
showed ~24 KB used out of 4.9 GB after ~12.5 hours of uptime — no Loki,
Vector, or Grafana process was running at all.

## 2. Root cause

`metrics-agent/Dockerfile` had `COPY --from=vector /usr/bin/vector
/usr/local/bin/vector`. The `timberio/vector:0.45.0-distroless-static` image
actually ships the binary at `/usr/local/bin/vector`, not `/usr/bin/vector`
(confirmed against Vector's own upstream Dockerfile). Every
`deploy-metrics-agent.yml` run since the log-stack feature merged failed the
Docker build with `"/usr/bin/vector": not found` (confirmed via `gh run list`
— 3 failed `workflow_dispatch` runs today, last success predates the
feature). The machine kept serving whatever image had last deployed
successfully: an older, pre-log-stack `entrypoint.sh` (20 lines, no
Loki/Vector/Grafana logic at all — confirmed by `md5sum`/`wc -l` mismatch
against the repo's current 147-line file) that never touches `/data`. `fly
releases`/`fly status` reported a healthy deploy throughout, because a
prior, different (older-code) deploy genuinely had succeeded — there was no
red status anywhere to notice the running code didn't match `main`.

Separately (not the cause of this incident, but a latent gap noticed while
reading the code): `entrypoint.sh`'s `mountpoint -q /data` check was
one-shot with no retry — a genuine boot-time race on a future machine
replace would permanently disable the log stack for that machine's whole
lifetime, with only a single easily-missed `ERROR` log line.

## 3. Fix / remediation

- `metrics-agent/Dockerfile`: corrected the COPY source path to
  `/usr/local/bin/vector`.
- `metrics-agent/entrypoint.sh`: `mountpoint -q /data` is now a 10-second
  bounded retry loop instead of a one-shot check.
- `metrics-agent/README.md`: documented the incident and both fixes under
  "Known gaps," including the specific gotcha that `fly releases` alone
  cannot distinguish "deployed the code you think" from "deployed
  *something* that happened to build."
- Manually restarted the (at-the-time stale) machine as an immediate,
  no-code-change mitigation attempt before the real cause was found; this
  did not fix anything on its own (restarting doesn't rebuild the image) but
  is harmless and is superseded by the redeploy below.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to `metrics-agent/`.** This app is a standalone
  Fly app (Option B per ADR-010) — it does not share code, a Docker image,
  or a deploy pipeline with `backend/`, `rider-app/`, `driver-app/`, or
  `admin-dashboard/`. Nothing in the production rides/payments/auth path
  reads from or depends on this app; it only scrapes the backend's
  `/metrics` endpoint (read-only, one-way) and now also passively ingests
  Fly's own log stream.
- The pre-existing Alloy metrics scrape → Grafana Cloud remote-write path
  (the original ADR-010 MVP, unrelated to the log-stack feature) was
  unaffected throughout — it kept running on the stale image the whole
  time, since that code hasn't changed since PR #4055.
- No grep for "every other consumer" was needed beyond confirming this via
  the app's own isolation-by-design (documented in `metrics-agent/README.md`'s
  opening paragraph) — there is exactly one Fly app whose Dockerfile this
  touches.

## 5. User-experience effect

None. Nobody rider/driver/corporate-admin/internal-admin-facing observes
this app. It is reached only via `fly proxy` by an operator with Fly
credentials.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `metrics-agent/Dockerfile` | `COPY --from=vector /usr/bin/vector …` → `COPY --from=vector /usr/local/bin/vector …` | Wrong source path was breaking every Docker build for this image since the log-stack feature merged |
| `metrics-agent/entrypoint.sh` | One-shot `mountpoint -q /data` check → 10s-bounded retry loop | Defense-in-depth against a genuine mount race on a future machine replace; not the cause of this incident but a real latent gap found while diagnosing it |
| `metrics-agent/README.md` | Added a dated note under "Known gaps" | Record what happened and how it was found, so the next empty-`/data` investigation starts from this instead of re-deriving it |

## 7. Before / after

```dockerfile
# Before
COPY --from=vector /usr/bin/vector /usr/local/bin/vector
```

```dockerfile
# After
COPY --from=vector /usr/local/bin/vector /usr/local/bin/vector
```

## 8. Rollback plan

Revert the Dockerfile line (single-line change, no data migration, no
feature flag exists for this app's Docker build). If a redeploy of the
corrected image somehow regresses metrics scraping, `LOGS_STACK_ENABLED=false`
can be set via `fly secrets set` to disable the entire log stack instantly
without touching the Alloy metrics path, or the machine can be rolled back
to the prior release with `fly releases` + `fly deploy --image <old-ref>`.
None of this touches live rider/driver/payment data, so no data-level
rollback is applicable.

## 9. Verification performed

- [x] Confirmed the exact build failure via `gh run view <run-id> --log-failed`
      on the 3 failed `deploy-metrics-agent.yml` runs from today.
- [x] Confirmed the corrected path against Vector's own upstream Dockerfile
      (`vectordotdev/vector` `distribution/docker/distroless-static/Dockerfile`
      at tag `v0.45.0`) via WebFetch — not guessed.
- [x] Confirmed the Loki COPY line (`/usr/bin/loki`) is correct by the same
      method (`grafana/loki` `cmd/loki/Dockerfile` at tag `v3.4.2`) — ruled
      out a second instance of the same mistake.
- [x] Read-only production diagnostics performed with the user's explicit
      go-ahead: `fly apps list`, `fly volumes list`, `fly secrets list`,
      `fly logs`, `fly ssh console -C "df -h /data"`, `ls -la /data`,
      `ps aux`, `stat /data`, `md5sum`/`wc -l` on the deployed
      `entrypoint.sh` vs. the repo copy.
- [ ] Not yet verified: a real `fly deploy` with the corrected Dockerfile
      (planned as the immediate next step, pending user confirmation before
      executing — a production deploy is a bigger action than the
      diagnostics above).
- [ ] No automated test covers Dockerfile COPY paths (there is no CI step
      that runs the built image and checks for the vector binary beyond "did
      the build succeed" — the failure mode here was already visible in CI
      logs, just apparently not being watched).

## 10. Sign-off

- [x] Rollback plan is concrete and testable (single-line revert, kill
      switch already exists via `LOGS_STACK_ENABLED`)
- [x] Blast radius is stated, not assumed: isolated to `metrics-agent/`,
      confirmed via the app's architectural isolation, not just asserted
- [x] No silent behavior change to an already-shipped flow — this restores
      intended behavior that was silently never live, it doesn't change
      shipped behavior anyone was relying on
