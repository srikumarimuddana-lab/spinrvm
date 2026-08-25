# Spinr Repo Hygiene Audit — 2026-08-24

**Date:** 2026-08-24 · **Scope:** repository-level hygiene only (file/dir layout, git tracking
correctness, dependency/lockfile consistency, dead code, doc staleness, CI wiring). Not a
security/money/dispatch code audit — those are covered by the domain `spinr-*` reviewers and the
existing `docs/audit/2026-08-18-full-fleet-whole-app-audit.md`.

**Method:** manual inspection of git history/tracking state plus two parallel read-only sweeps
(migrations + CI workflows; dependency/lockfile + dead-code). No files modified as part of the
audit itself; findings below are reported for triage, not auto-fixed, per this repo's
escalate-before-shipping convention for anything touching a live-tested surface.

---

## P0 — Real driver PII committed to the repository, present on `main` today

`driver_bank_sin_migration.sql` (64 KB) and `driver_csv_migration.sql` (76 KB) sit at the repo
**root** (not in `backend/migrations/`, so `run_migrations.py` never touches them) and are
currently tracked on `origin/main`. `driver_bank_sin_migration.sql` contains **plaintext Social
Insurance Numbers, bank account/transit/institution numbers, GST/BN numbers, dates of birth, and
home addresses for 157 real drivers**; `driver_csv_migration.sql` contains names/phones/emails/
license numbers for 189 drivers. The file's own header states SINs are meant to be encrypted via
`encrypt_driver_pii()` and the staging table dropped — but the plaintext staging INSERT itself is
what's committed to git, not just an ephemeral runtime artifact.

This is exactly the class of exposure `.gitignore` already tries to prevent for adjacent data
(`*.csv` is blanket-ignored repo-wide specifically because "a single `git add -A` would turn a
local convenience into an unrecoverable PIPEDA disclosure") — these two `.sql` files carry the
same kind of data and were committed anyway. `.gitleaks.toml` has no SIN/bank-account detection
rule, so gitleaks would not have caught this (it scans for secrets/API-key patterns, not PII).

**Not remediated as part of this audit.** Removing the files from the tree stops new clones from
getting them but does **not** remove them from git history (already-cloned copies, forks, and CI
caches keep them); a real fix needs history rewrite + coordinated force-push + confirming who else
has clones, which is exactly the kind of hard-to-reverse, needs-a-human-decision action this repo's
own rules call out. Per the PIPEDA breach protocol in `CLAUDE.md` (P0 incident, 24h scope
assessment), this should be escalated now rather than fixed unilaterally.

**Immediate, safe stop-gap available on request:** delete both files from the working tree in a
follow-up commit (stops new clones/CI checkouts from picking them up going forward) while the
history-level remediation is planned separately.

---

## P1 — `.gitignore` itself is silently broken for Firebase Admin SDK credentials

Two of the three Firebase-Admin-SDK-credential glitch lines in `.gitignore` were pasted in as
UTF-16LE text (null byte before every character, CRLF line endings) into an otherwise UTF-8/LF
file — confirmed via `od -c` and `file .gitignore` (reports "data", not "ASCII text"). Verified
with `git check-ignore`: a new file matching `driver-app/*-firebase-adminsdk-*.json` is **not**
ignored. Only the third line — a literal, already-committed filename — works, because it's the one
line that's plain ASCII. A newly generated Firebase service-account key (a live secret, not just
PII) dropped into `driver-app/` or `rider-app/` under any other filename would not be caught by
this rule today.

---

## P1 — `frontend/` is deprecated (since 2026-04-14, per `frontend/DEPRECATED.md`) but still fully wired into CI and tracked with cache bloat

- `.github/workflows/ci.yml` still carries `frontend-test` and `deploy-frontend` jobs, disabled via
  `if: false` with a comment citing the deprecation decision — not removed.
- `ci-error-audit.yml` and `security-gates.yml` still reference `frontend` in their target
  enumeration / cache-key comments.
- `frontend/.metro-cache/` — **1,391 files, 2.3 MB** — is tracked in git. `rider-app/.metro-cache/`
  and `driver-app/.metro-cache/` are both correctly gitignored; the equivalent line for `frontend/`
  was never added, so its Metro cache has been accumulating in history since before the deprecation.
- `frontend/` also carries its own `package-lock.json` **and** `yarn.lock` (mixed package managers
  for a directory that's dead code) and a stray `tsc_errors.txt` scratch file.
- `README.md`'s "Project Structure" section still documents `frontend/` as *the* React Native app
  and doesn't mention `rider-app/`/`driver-app/` at all — actively misleading for a new contributor
  reading the repo's front door.

None of this blocks anything today, but every day `frontend/` stays un-deleted is another day its
tracked Metro cache and disabled CI jobs keep bit-rotting in a way nobody will notice.

---

## P2 — Stale top-level status docs

- `.claude/context/sprint-current.md` — last real status entry is dated 2026-05-06 ("Full audit
  sweep complete... Remaining non-blockers..."), marked COMPLETE, while `CLAUDE.md` describes the
  product as **currently** in live app testing three and a half months later. This is the file
  `CLAUDE.md` tells Claude to load for "active sprint goal, in-flight tickets, blockers" — right
  now it loads context that's ~3.5 months out of date. (The session-start hook already flags this
  every session; it's been silently ignored rather than updated.)
- `.planning/PROJECT.md` still describes Spinr as "pre-launch... device testing is the immediate
  next milestone before production launch" — contradicts `CLAUDE.md`'s "product is currently going
  through live app testing with real users."
- `README.md` — see `frontend/` section above; Project Structure is stale on top of the dead-app
  issue.

---

## P2 — Root-level scratch/graveyard files tracked in git

Repo root:

| File | Size | Note |
|---|---|---|
| `validation_output.txt` | 8 KB | UTF-16-garbled scratch output from a one-off validation run |
| `test_admin_endpoints.py` | 4 KB | ad-hoc manual test script, outside any `tests/` convention |
| `SPINR_CODE_REVIEW.md` | 252 KB | |
| `Spinr_Code_Review_Report.docx` | 148 KB | |
| `Spinr_Code_Review_Matrix.csv` | 312 KB | |
| `Spinr_Code_Review_Driver_Rider_Branded.docx` | 20 KB | |
| `Spinr_Skills_and_Code_Review_Recommendations.docx` | 16 KB | |
| `ACTION_ITEMS.md` | 1.1 MB | actively referenced from `CLAUDE.md` as the live backlog — not dead, but its single-file size makes it expensive to load/grep and is worth periodic archiving of closed items |

`driver-app/` has accumulated an Expo-SDK-migration scratch dump: `final_audit.txt`,
`audit_report_final.txt`, `audit_report.txt`, `final_audit_v3.txt`, `final_audit_v4.txt`,
`expo_52_deps.json`, `expo_53_deps.json`, `expo_52_real_deps.json`, `screens_versions.json`.
`rider-app/tsc-errors.txt` and `frontend/tsc_errors.txt` are the same pattern (a one-off local
`tsc` run redirected to a file and then `git add -A`'d). `backend/` root has `test_batch.json`,
`test_batch_dict.json`, `test_dns.py`, `test_corporate_accounts.py`, `uvicorn.log` sitting outside
`backend/tests/`.

A root-level `tests/` directory (`test_cross_app_ride_lifecycle.py`, `test_rate_limits.py`) exists
alongside — and separate from — the documented `backend/tests/` (784 files). It isn't referenced
by any CI workflow or root pytest config; unclear whether these two tests currently run anywhere.

---

## P3 — Mixed package managers

- Repo root: `package.json` is a two-dependency commitlint/husky stub, yet carries **both**
  `package-lock.json` (36 KB) and `yarn.lock` (24 KB) — one is redundant for a project with no
  real dependency tree.
- `frontend/`: same pattern — both `package-lock.json` (634 KB) and `yarn.lock` (382 KB) present
  for one app (moot once `frontend/` is removed, but worth knowing this isn't unique to root).
- `backend/requirements-win.txt` has drifted from `backend/requirements.in`/`requirements.txt`:
  materially different pins for shared packages (`aiohttp` 3.14.3 vs 3.13.5, `h2` 4.4.1 vs 4.3.0,
  `pydantic-core` 2.46.4 vs 2.48.0, `python-multipart` 0.0.32 vs 0.0.27, others), plus a dev-lint
  toolchain (`black`, `flake8`, `mccabe`, `pyflakes`, `pycodestyle`) that isn't in the main lock —
  looks generated once and never regenerated alongside the main requirements files.
- `backend/requirements.in` declares `redis[asyncio]>=5.0.0` twice (harmless — `pip-compile`
  dedupes — but worth a one-line cleanup).

---

## P3 — Large generated artifacts tracked as if they were source

| File | Size |
|---|---|
| `.planning/graphs/graph.json` | 6.5 MB |
| `.planning/graphs/.last-build-snapshot.json` | 6.5 MB |
| `.playwright-mcp/page-*.yml` (12 files, May 2026) | ~0.4 MB combined |

The `.planning/graphs/*` files look like generated knowledge-graph build output (the dotfile name
`.last-build-snapshot.json` says so directly) rather than something meant to be hand-edited or
diffed — a candidate for `.gitignore` unless the graph is intentionally versioned as a point-in-time
snapshot. `.playwright-mcp/page-*.yml` are dated scratch captures from interactive tool sessions,
not test fixtures (no `e2e/` or `__tests__/` path references them).

---

## Confirmed, not new: migration numbering duplicates

Cross-checked independently: **66 numeric-prefix groups** among 449 files in `backend/migrations/`
share a leading number (highest prefix currently in use: `363`). This matches the scale
`CLAUDE.md` already documents ("~60 numeric prefixes shared by 2+ files repo-wide") and is handled
correctly by `run_migrations.py`'s full-filename idempotency keying — not a functional bug, just
confirming the documented state hasn't drifted further. No new duplicate groups found beyond what's
already tracked.

## Confirmed, not a problem: `.github/workflows/` dual backend deploy

`deploy-backend.yml` (Railway) and `deploy-fly.yml` (Fly.io) both trigger on `push: main` +
`backend/**` — this is the documented intentional dual-deploy (ADR
`docs/adr/007-fly-primary-railway-standby.md`), not an accidental duplicate. Its current
degraded state (Railway blocked by a GitHub Environment protection rule) is already tracked as
`ACTION_ITEMS.md` C5 and not re-litigated here.

## Confirmed, not urgent: undocumented top-level tool directories

`.kilo/` and `.emergent/` have no README explaining their purpose — `.emergent/` in particular has
only a `summary.txt` and three empty sentinel marker files (`.bootstrap-complete`,
`.restic-restore-verified`, `.restore-complete`) with no doc tying them to a still-active
integration, worth a one-line confirmation that it isn't leftover from an abandoned hosting
platform trial rather than active tooling (`CLAUDE.md`'s "Claude-Adjacent Directories" table lists
it as "Active" but doesn't say what it does). `.semgrep/` and `monitoring/` are low-severity (single
config file, purpose inferable from filename). `plans/` has three loose planning docs with no
index — including both an "Expo SDK 52 downgrade" and an "Expo SDK 54 migration" plan coexisting,
which may just mean one is stale.

---

## Summary table

| Priority | Finding | Files affected | Action needed |
|---|---|---|---|
| P0 | Real SIN/bank data committed, live on `main` | 2 root `.sql` files | Escalate — human decision on incident response + history remediation |
| P1 | `.gitignore` Firebase-key rule silently non-functional | `.gitignore` | Re-save as UTF-8, re-verify with `git check-ignore` |
| P1 | Deprecated `frontend/` still wired into CI + 1,391 cached files tracked | `ci.yml`, `ci-error-audit.yml`, `security-gates.yml`, `frontend/.metro-cache/`, `README.md` | Decide: finish deletion (SPR-02 was the stated timeline) or explicitly re-scope |
| P2 | Sprint/planning docs 3.5 months stale, contradict `CLAUDE.md` | `.claude/context/sprint-current.md`, `.planning/PROJECT.md` | Update or mark historical |
| P2 | Root/app-level scratch files tracked | ~20 files across root, `driver-app/`, `rider-app/`, `backend/` | Delete, add gitignore rules for the pattern |
| P2 | Orphan root `tests/` dir, not wired to CI | `tests/*.py` | Confirm still needed; move into `backend/tests/` or wire into CI |
| P3 | Mixed package-manager lockfiles | root, `frontend/` | Pick one per directory |
| P3 | `requirements-win.txt` drifted from main lock | `backend/requirements-win.txt` | Regenerate or document why it's allowed to drift |
| P3 | Generated artifacts tracked as source | `.planning/graphs/*.json`, `.playwright-mcp/*.yml` | Gitignore or confirm intentional |

## What was NOT verified

- Whether the two PII SQL files' data has already been fully applied to production and the
  staging tables dropped there (the file's own comments say that's the intended flow) — this audit
  only confirms the plaintext is in git, not the current state of the production database.
- Whether any fork or external clone of this repo already has the PII files, which would mean
  history rewrite on `origin` alone would not fully contain the exposure.
- Runtime behavior of `.emergent/`'s marker files (whether some CI/deploy step actually reads
  them) — flagged from static inspection only, not traced through code.
- No attempt was made to determine who added the PII files or why (`ab34bcc8`'s commit message
  is unrelated to them; likely a merge artifact) — out of scope for a hygiene pass.
