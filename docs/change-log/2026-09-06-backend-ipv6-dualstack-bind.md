# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-06 |
| Author | Claude Code (session on behalf of vikas@ngitservices.com) |
| Surface(s) | backend |
| Domain (Sentry tag) | admin (metrics/observability infra — no `domain` tag in the code touched here fits the six product domains better than `admin`) |
| PR / commit link | (filled in on PR creation) |
| Related issue or gap ID | #3295 (ADR-010 metrics-aggregation MVP) |

## 1. Issue / gap identified

The new `spinr-metrics-agent-yyz` Fly app (ADR-010 Option B, PR #5040) cannot scrape `spinr-backend-yyz`'s `/metrics` endpoint over Fly's private network (6PN) — every scrape attempt fails with `connection refused`, so no backend metrics ever reach Grafana Cloud.

## 2. Root cause

`backend/Dockerfile`'s production `CMD` launches uvicorn with `--host 0.0.0.0`. `0.0.0.0` is the IPv4-only wildcard address — it does not open an IPv6 listening socket. Fly's private networking (6PN, the `fdaa:...` address family used for machine-to-machine traffic) is **IPv6-only**. So the backend has no socket at all on the interface metrics-agent connects through.

Public traffic is unaffected today because Fly's edge proxy forwards public HTTPS requests to `127.0.0.1` (IPv4) inside the machine's network namespace, which does match the IPv4-only bind — masking the gap entirely until something tries to reach the app directly via 6PN, which nothing did before metrics-agent.

Confirmed, not guessed: from inside a live `spinr-backend-yyz` machine (`fly ssh console -a spinr-backend-yyz`), `http://localhost:8000/health` → `200`, but `http://[$FLY_PRIVATE_IP]:8000/health` (the same machine's own 6PN address) → `ConnectionRefusedError: [Errno 111]`. The backend can't even reach itself over 6PN, which rules out any firewall/routing explanation and isolates the cause to the bind address itself.

## 3. Fix / remediation

Changed the uvicorn bind host in `backend/Dockerfile`'s `CMD` from `0.0.0.0` to `::` (the IPv6 wildcard). On Linux, a socket bound to `::` is dual-stack by default (`net.ipv6.bindv6only=0`, the kernel default; not overridden anywhere in this image or `fly.toml`) — it accepts both IPv4 and IPv6 connections on the same socket. This is additive: existing IPv4 traffic (the health check's `localhost` probe, Fly's edge-proxy-to-loopback forwarding) keeps working exactly as before, and IPv6/6PN traffic starts working for the first time.

## 4. Risk & impact on existing functionality

- **Blast radius: single-surface, isolated to `backend/Dockerfile`'s one `CMD` line.** No other file changed.
- Grepped the whole `backend/` tree for `0.0.0.0`: 4 hits — this `Dockerfile` line (fixed), `backend/server.py`'s `if __name__ == "__main__":` dev-only entrypoint (not used in production; Docker's `CMD` invokes uvicorn directly, never `python -m backend.server`), `backend/Procfile` (Railway's standby launch command — Railway isn't part of Fly's 6PN network and isn't a metrics-agent target, so out of scope for this fix), and one unrelated CIDR-format code comment in a migration file (`"10.0.0.0/8"` example string, not networking config).
- Nothing else reads or depends on the bind address family. This doesn't touch ride state, money paths, RLS, or any background loop.
- Theoretical risk: if some future/exotic deployment environment set `net.ipv6.bindv6only=1` (IPv6-only sockets), `::` would stop accepting IPv4 — verified this isn't the case here (no such sysctl set anywhere in this repo's Docker/Fly config), and Fly Machines run standard Linux kernels with the default (dual-stack) setting.

## 5. User-experience effect

**None.** Backend-only infra change — no rider, driver, corporate-admin, or internal-admin-facing behavior changes. Not visible mid-session to anyone. Purely enables a new internal metrics pipeline (ADR-010) that doesn't yet feed any user-facing feature.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/Dockerfile` | `CMD`'s uvicorn `--host 0.0.0.0` → `--host ::`, plus an explanatory comment | Enables IPv6 (Fly 6PN) reachability alongside existing IPv4, so metrics-agent can scrape `/metrics` |

## 7. Before / after

```
# Before
CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${UVICORN_WORKERS:-4}"]
```

```
# After
CMD ["sh", "-c", "uvicorn server:app --host :: --port ${PORT:-8000} --workers ${UVICORN_WORKERS:-4}"]
```

## 8. Rollback plan

`git revert` is a genuine, sufficient rollback here — this changes only how the process binds its listening socket at container start; it writes no data, applies no migration, and mutates no live state (no Stripe charges, wallet deltas, or ride records are involved). Reverting the commit and redeploying restores the exact prior (IPv4-only) behavior with no data-level remediation needed. Given the deploy strategy is `rolling` with `max_unavailable = 1` (`backend/fly.toml`), a bad rollout would only be able to affect one machine at a time before health checks catch it.

## 9. Verification performed

- [x] Manual repro steps followed against the **live** `spinr-backend-yyz` app (not staging — this app has no separate staging deploy target beyond `spinr-backend-staging`, which was not used for this check): confirmed the failure (`ConnectionRefusedError` on the machine's own 6PN address, `200` on `localhost`) via `fly ssh console` before the fix.
- [x] Blast-radius grep performed: `grep -rn "0\.0\.0\.0" backend/` — 4 hits, all accounted for above.
- [x] Reviewed against relevant CLAUDE.md conventions: this is an infra/network-bind change, not a state-machine, money, RLS, or PIPEDA concern — none of those apply.
- [ ] **Not yet verified**: the actual post-fix redeploy and re-confirmation that `spinr-metrics-agent-yyz` successfully scrapes `spinr-backend-yyz` end-to-end (i.e., `up{job="spinr-backend"}` reads `1` in Grafana Cloud). This PR's fix is code-complete and the failure mode is fully reproduced and understood, but the person deploying needs to run `fly deploy -a spinr-backend-yyz` and re-check Grafana after this merges — that live confirmation happens post-merge, not in this PR.
- [ ] No automated test exists for uvicorn's bind address (this is a runtime/process-launch concern, not something `pytest` exercises) — a regression here would only be caught by exactly the kind of live reachability check performed manually above. Not adding one in this PR; flagged as a gap.
- Feature flag: not applicable — a bind-address change can't be flagged (it takes effect at process start, before any app code or `app_settings` read runs).

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no data-level concerns)
- [x] Blast radius is stated, not assumed (grepped, 4 hits enumerated, 3 explicitly out of scope with reasons)
- [x] No silent behavior change to an already-shipped flow — this only adds a previously-absent reachability path (6PN/IPv6); the existing public/IPv4 flow is provably unchanged (dual-stack is additive by construction, and the health check itself proves `localhost`/IPv4 still works)
