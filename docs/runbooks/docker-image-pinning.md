# Docker Image Pinning — Runbook

**Audience:** anyone who edits `backend/Dockerfile` or rotates the base image.

**Why this exists:** B-P2-8 from the 2026-04-26 backend audit flagged that
the builder stage used `python:3.12-slim` (any 3.12.x) and the runtime stage
used `python:3.12.9-slim` — both **mutable tags** with no `@sha256:` content
hash. A Docker Hub re-tag, a compromised mirror, or a typosquat in a
multi-platform manifest can replace the bytes behind a tag without any
diff in our repo.

The Dockerfile in this branch pins both stages to the same minor version
(`3.12.9-slim`); the **content-hash pin** (the `@sha256:<digest>` suffix)
is the next layer and is performed manually because it requires a
`docker pull` against the current upstream image.

---

## Threat model

| Attack | Without digest pin | With digest pin |
|---|---|---|
| Docker Hub publishes a re-tag of `python:3.12.9-slim` with malicious bytes | New build runs the malicious base | Build fails with `manifest digest mismatch` until digest is refreshed |
| Compromised PyPI mirror typosquats a transitive dependency baked into Python's slim image | Captured in next build | Captured in next build (same as without; digest only protects the image, not the runtime install — but our `--require-hashes` lockfile already covers that path) |
| Insider with Docker Hub push access tampers with the manifest | New build inherits change | Insider must also rotate our pinned digest, which leaves a PR audit trail |

The digest pin is a defense-in-depth layer: it doesn't replace
`--require-hashes` (which we have for `requirements-locked.txt`) — it
covers the OS-level layers below the Python install.

---

## Procedure: add or refresh the SHA256 digest

Run on a host with a working `docker` CLI (your laptop, a CI worker, or
a Railway shell):

```bash
# 1. Pull the current upstream image. This downloads bytes, not just metadata.
docker pull python:3.12.9-slim

# 2. Read the manifest digest pinned to that pull.
docker inspect --format='{{index .RepoDigests 0}}' python:3.12.9-slim
# → python@sha256:abcd1234...
```

The output is the form `python@sha256:<digest>`. Copy the digest.

Then edit `backend/Dockerfile`:

```dockerfile
# Builder stage
FROM python:3.12.9-slim@sha256:abcd1234efgh5678...

# Runtime stage
FROM python:3.12.9-slim@sha256:abcd1234efgh5678... AS runtime
```

Commit on a feature branch:

```bash
git checkout -b chore/docker-base-pin-YYYYMMDD
git add backend/Dockerfile
git commit -m "chore(docker): refresh python:3.12.9-slim digest pin

Re-pinned the builder + runtime base to the current Docker Hub digest
for python:3.12.9-slim. Verifies via 'docker inspect' on a 2026-MM-DD
pull. Trivy / Grype image scan should be clean against this digest
before this PR merges.
"
git push -u origin chore/docker-base-pin-YYYYMMDD
gh pr create --title "chore(docker): refresh python:3.12.9-slim digest pin" \
             --body "B-P2-8 quarterly digest refresh. See docs/runbooks/docker-image-pinning.md."
```

CI must pass:
- The `docker-image-scan` Trivy job builds the image and reports CVE
  count. A digest refresh that introduces NEW Highs/Criticals must be
  reverted or scoped (e.g. add an apt-get patch step in the Dockerfile).

---

## Cadence

- **Quarterly**: refresh the digest unconditionally so we're never more
  than 90 days behind upstream's CVE patches.
- **On Trivy alert**: if `docker-image-scan` flags a HIGH/CRITICAL CVE in
  the current base, refresh the digest as soon as Docker Hub publishes
  a patched build (typically within a week of the upstream Python
  release).
- **On Python minor bump**: when bumping `3.12.9` → `3.12.10`, the digest
  must change too — never carry an old digest into a new minor.

---

## Why we don't pin to the major-only tag

`python:3.12-slim` is a moving alias for the latest 3.12.x. Pinning to it
gets us "free" patch upgrades but loses build reproducibility — the same
commit can produce different artifacts on different days. Reproducibility
beats automatic upgrades for a security-sensitive backend; we pin the
minor and refresh on a known cadence.

---

## What ELSE the audit (B-P2-8) called for

The audit also flagged "no read-only-root-filesystem in deploy spec".
Status (2026-04-28):

- **Railway** (primary host): does not expose `read_only: true` for
  Docker deployments via `railway.toml`. The container's root filesystem
  is writable by default. Mitigations in place: `USER spinr` (non-root
  PID), `--require-hashes` lockfile, `--prefix=/install` builder layer.
  Tracked as a future enhancement; would require migrating to a host
  that supports the option (Fly.io, K8s, ECS).
- **Render** (fallback): runtime is `python` (not Docker), so RO-root
  doesn't apply.

---

## Related

- `backend/Dockerfile` — current pinned state.
- `backend/requirements-locked.txt` — Python dependency hash pinning
  (B-P1-10).
- `docs/runbooks/dependency-update.md` — Python dependency update flow.
- `.github/workflows/security-gates.yml` — Trivy / Grype image scan
  job that gates merges.
