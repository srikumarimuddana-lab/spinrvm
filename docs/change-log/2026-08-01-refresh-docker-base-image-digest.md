# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-01 |
| Author | Claude Code (background job) |
| Surface(s) | backend |
| Domain (Sentry tag) | backend (infra / build pipeline — not a runtime request-serving domain) |
| PR / commit link | branch `claude/refresh-docker-base-image-digest` (draft PR, see PR description) |
| Related issue or gap ID | `ACTION_ITEMS.md` C6 — base-image-staleness half only (the `msgpack` half of C6 is a separate, unresolved investigation, explicitly out of scope here) |

## 1. Issue / gap identified

`backend/Dockerfile` pinned its Python base image (builder + runtime stages) to
`python:3.12.9-slim@sha256:48a11b7...`, captured 2026-04-29 with the comment
"refresh quarterly." As of 2026-08-01 that pin was 94 days old — past the
stated quarterly cadence — first flagged as part of `ACTION_ITEMS.md` C6.

## 2. Root cause

Nobody had run the quarterly refresh in `docs/runbooks/docker-image-pinning.md`
since April. Investigating further: refreshing the digest for the *same* tag
(`3.12.9-slim`) would not actually have fixed anything — verified directly
against the Docker registry that tag's digest has not changed one bit since
the 2026-04-29 capture. Docker Hub does not rebuild a fixed patch-version tag
(e.g. `3.12.9-slim`) after newer patches ship; it stays frozen at whatever was
built when 3.12.9 was current. Newer CVE fixes land in new patch tags
(`3.12.10-slim` … `3.12.13-slim`), not in rebuilds of old ones. So the actual
gap wasn't "stale digest for the pinned tag," it was "pinned to a tag that
stopped receiving updates 3 minor patches ago."

## 3. Fix / remediation

Bumped both `FROM` lines in `backend/Dockerfile` from `python:3.12.9-slim` to
`python:3.12.13-slim` (current latest 3.12.x patch) at its verified current
digest, and updated the pin comment's captured-date and rationale.

Digest verification (no Docker daemon available in this sandbox — see
"What was NOT verified" below for exactly how this was obtained):
- Docker registry v2 API (`registry-1.docker.io/v2/library/python/manifests/3.12.13-slim`,
  `Accept: application/vnd.oci.image.index.v1+json`) → `Docker-Content-Digest:
  sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de`
- Docker Hub v2 metadata API (`hub.docker.com/v2/repositories/library/python/tags/3.12.13-slim/`)
  → same digest, `last_updated: 2026-07-16T11:07:39Z`
- Floating alias `python:3.12-slim` (always tracks the newest 3.12.x patch)
  resolves to the **same** digest — independent confirmation this is
  genuinely the current latest patch, not a stale/incomplete tag.

All three independent lookups agree exactly, byte-for-byte, on the digest.

## 4. Risk & impact on existing functionality

- **Blast radius: backend Docker build only.** `backend/Dockerfile` is the
  build recipe for the backend container image deployed to both Fly.io
  (primary) and Railway (standby) per `backend/fly.toml` (`dockerfile =
  "Dockerfile"`, relative to `backend/`) and `railway.json`
  (`"dockerfilePath": "backend/Dockerfile"`). No application code changes.
- **Other consumers checked:** grepped the repo for the old digest and the
  `3.12.9-slim` tag string. Found a second, apparently-**unused** root-level
  `Dockerfile` that also pins the same stale digest under its own "Q-5"
  comment convention — but neither `backend/fly.toml` nor `railway.json`
  reference it (both point at `backend/Dockerfile`), so it looks orphaned.
  Left untouched — out of scope for this change, noted as a follow-up in
  `ACTION_ITEMS.md` C6.
- **Docs referencing the old digest/tag** (`docs/runbooks/docker-image-pinning.md`
  and several `docs/audit/`, `reports/` files) were **not** edited — the
  runbook's example commands are generic/instructional (not asserting a
  specific current pin value beyond the historical record), and the
  `reports/`/`docs/audit/` files are point-in-time snapshots that should not
  be retroactively rewritten.
- **Python 3.12.9 → 3.12.13 is a patch-only bump** (same minor version,
  13 patch releases of bugfixes/CVE patches, no API/ABI break per CPython's
  patch-release policy). `requirements-locked.txt` pins wheels via
  `--require-hashes`, which are resolved against Python's stable ABI, not a
  specific patch build, so this should not affect the resolved dependency
  set. This is reasoned about, not empirically verified (no local build —
  see below).
- **Underlying OS packages (Debian bookworm-slim) also move forward** with
  this bump — this is the intended effect (closing the CVE gap the
  quarterly-refresh policy exists for), and is exactly what
  `docker-image-scan` (Trivy) in CI is designed to catch if it introduces a
  *new* problem.

## 5. User-experience effect

None. Backend-only infra change; no rider/driver/corporate-admin/internal-admin
facing behavior changes. Not visible mid-session to anyone — takes effect only
on the next image build/deploy, and only after this draft PR is reviewed,
un-drafted, and merged (it is explicitly left as a draft and not merged by this job).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/Dockerfile` | Both `FROM` lines: `python:3.12.9-slim@sha256:48a11b7...` → `python:3.12.13-slim@sha256:57cd7c3a...`; updated pin comment (captured-date, rationale for the tag bump vs. same-tag digest refresh) | Close the 94-day-stale quarterly-refresh gap (`ACTION_ITEMS.md` C6) with a change that actually moves the CVE-patch baseline, rather than re-pinning bytes that hadn't changed |
| `ACTION_ITEMS.md` | Added a 2026-08-01 update under C6 documenting what was tried, found, and fixed (base-image half only); left the msgpack half open | Keep the tracked action item accurate and avoid re-discovering the same investigation next quarter |
| `docs/change-log/2026-08-01-refresh-docker-base-image-digest.md` | New file (this file) | Mandatory Change Impact Log per `CLAUDE.md` for any commit closing a gap |

## 7. Before / after

```
# Before (backend/Dockerfile, both stages)
FROM python:3.12.9-slim@sha256:48a11b7ba705fd53bf15248d1f94d36c39549903c5d59edcfa2f3f84126e7b44
```

```
# After (backend/Dockerfile, both stages)
FROM python:3.12.13-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de
```

## 8. Rollback plan

No feature flag applies — this is a build-time base image pin, not a runtime
behavior toggle, and nothing here touches live data (no Stripe charges,
wallet deltas, or ride state). Rollback is a plain revert of the two `FROM`
lines back to the prior tag/digest (`git revert` of this commit, or a
follow-up PR pinning back to `python:3.12.9-slim@sha256:48a11b7ba705fd53bf
15248d1f94d36c39549903c5d59edcfa2f3f84126e7b44`), then a normal redeploy —
acceptable here specifically *because* no live data or in-flight state is
affected by a base-image swap; the container is rebuilt fresh on every
deploy regardless.

## 9. Verification performed

- [x] Digest cross-verified against **three independent Docker-operated
      endpoints** (registry v2 API, Hub v2 metadata API, floating-tag
      resolution) — all agree exactly.
- [ ] Automated tests run — **not applicable/not run**; no Python code
      changed, only the Dockerfile base image reference.
- [ ] Manual repro / staging check — **not performed**, see below.
- [x] Blast-radius grep performed: searched the full repo for the old
      digest string and the `3.12.9-slim` tag; found and evaluated every
      hit (`backend/Dockerfile`, the orphaned root `Dockerfile`, the
      runbook, and historical audit/report docs — see section 4).
- [x] Reviewed against relevant `CLAUDE.md` conventions: not a state-machine,
      money, RLS, or PIPEDA change; Docker base-image pinning convention
      (`docs/runbooks/docker-image-pinning.md`) followed for the digest
      verification method (registry lookup), deviating only in *how* the
      lookup was performed (HTTP registry API instead of `docker pull` +
      `docker inspect`, because no Docker daemon is available in this
      sandbox and the proxy blocks the Docker Hub blob CDN outright).
- [ ] Feature-flagged — not applicable (build-time-only, no runtime toggle
      exists or is needed for a base image swap).

## 10. What was NOT verified

- **No local Docker build was possible.** This sandbox has no Docker daemon
  (`docker version` shows the client but the API socket doesn't exist:
  `dial unix /var/run/docker.sock: connect: no such file or directory`).
  `docker pull` / `docker manifest inspect` also independently fail here
  because the outbound proxy blocks `production.cloudfront.docker.com`
  (the blob CDN Docker Hub redirects to) with a 403 — confirmed via
  `$HTTPS_PROXY/__agentproxy/status`'s `recentRelayFailures`. The digest was
  instead obtained directly from the Docker registry v2 HTTP API and the
  Docker Hub v2 metadata API, which the proxy does allow, and which do not
  require downloading any image blobs — only manifest JSON and an auth
  token. This is a legitimate way to obtain the same "docker-content-digest"
  a local `docker pull` would resolve to (both APIs are the actual source
  of truth `docker pull` itself queries), but it is not the same as an
  end-to-end build.
- **No `docker build` / `docker-image-scan` (Trivy) run locally.** That is
  explicitly the job of the `docker-image-scan` CI check on the resulting
  draft PR — this change is unverified beyond "the pinned digest is real,
  current, and matches what three independent Docker-operated sources
  report for the same tag" until that CI check runs.
- **The `msgpack` half of `ACTION_ITEMS.md` C6 was not investigated** — it
  requires an actual Docker build to inspect the installed package inside
  the image, which remains unavailable in this environment. Explicitly out
  of scope for this change per the task that produced it.
- **No production build (`npm run build` etc.) applies** — this change
  touches no frontend surface (`admin-dashboard`/`rider-app`/`driver-app`).

## Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert` — safe here
      because no live data is affected by a base-image pin change)
- [x] Blast radius is stated, not assumed (backend Docker build only; one
      other file — an orphaned, unreferenced root `Dockerfile` — found and
      explicitly left alone with reasoning given)
- [x] No silent behavior change to an already-shipped flow — this is a
      build-time-only change with zero runtime/UX surface
