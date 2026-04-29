# /pr — Create a spinr Pull Request

Open a PR that conforms to `.github/pull_request_template.md`. Auto-fill every
field you can derive from the diff, branch history, and commit messages. Only
leave a field for the human if genuinely ambiguous — and when you do, fill it
with a `<placeholder>` so the `required-fields` CI check lights up instead of
silently passing.

## 0 · Preconditions

```bash
git branch --show-current
```

- If `main`: STOP — cannot PR from the protected long-lived branch.
- If on a `claude/*` branch: confirm with the user before proceeding — `/pr`
  is usually run from a human-authored feature branch. (The Claude workflow
  branches get their own automated PRs.)

**Duplicate PR guard (mandatory):** Before calling `mcp__github__create_pull_request`,
check whether the current branch already has an open PR:

```bash
# Replace BRANCH with the output of git branch --show-current
gh pr list --head BRANCH --state open --json number,title,url
```

Or via MCP: call `mcp__github__list_pull_requests` with
`head: "<owner>:<branch>"` and `state: "open"`.

- If one or more open PRs exist → **STOP creating a new PR**. Push additional
  commits to the existing branch; the existing PR will update automatically.
  Report the existing PR URL to the user.
- If the existing PR is a draft and the work is ready → promote it with
  `mcp__github__update_pull_request` (`draft: false`) rather than opening a
  new one.
- Only proceed to create a new PR if **zero** open PRs exist for this branch.

## 1 · Gather facts

Run these in parallel:

```bash
git log origin/main..HEAD --oneline
git log origin/main..HEAD --pretty=format:'%H%x09%s%x09%b%x00'
git diff origin/main...HEAD --stat
git diff origin/main...HEAD --name-only
git log --merges origin/main..HEAD --oneline
```

Parse out:

- **Changed paths** (full list)
- **Commits on branch** (count, messages, conventional prefixes)
- **Merge commits present** (yes/no — if yes, you'll need a Tier 7 debug note)
- **Total added/deleted lines**

## 2 · Derive Tier 1 fields

| Field | How to derive |
|---|---|
| **Summary** | One sentence that answers "what changed and why"; prefer the first-commit subject if it reads well. |
| **Type** | Dominant conventional-commit prefix (`feat`/`fix`/`chore`/`refactor`/`perf`/`security`/`docs`). Use `trivial` **only** when the entire diff is formatting / typo / comment / lockfile — and when in doubt, don't use `trivial`. |
| **Linked issue** | Search commit bodies for `Fixes #N` / `Refs #N` / `Closes #N`. If none, write `none — <one-line reason>`. Never guess an issue number. |
| **Risk** | Use the decision rules in §4. |
| **User-visible change** | From changed paths — `rider-app/` → `riders`; `driver-app/` → `drivers`; `admin-dashboard/` → `admins`; only backend without endpoint changes → `none`. Pick the primary audience; add a one-liner describing what they'll see. |
| **Scope contract** | Tick `[x]` only if the diff truly matches the declared type. If you bundled a refactor into a feat, fix the PR (split it) or be honest — flag it in the PR body. |

## 3 · Derive Tier 2 fields

Map changed paths to the template options:

- **Surfaces touched** — tick every matching `[x]`:
  - `backend/` → `backend`
  - `rider-app/` → `rider-app`
  - `driver-app/` → `driver-app`
  - `admin-dashboard/` → `admin`
  - `shared/` → `shared`
  - `backend/migrations/` → `migrations`
  - `.github/workflows/`, `scripts/`, `Dockerfile*`, `docker-compose*`, `railway.json`, `render.yaml`, `vercel.json` → `infra`
  - `.github/workflows/` → also `CI`
- **Blast radius** —
  - 1 surface, leaf function only → `isolated`
  - 1 surface, crosses modules → `single-surface`
  - 2+ surfaces → `multi-surface`
  - Everywhere + shared contracts → `cross-cutting`
- **Data schema change** — `backend/migrations/` touched?
  - No → `none`
  - Yes, only additive (new table / new nullable column / new index) → `additive`
  - Yes, drops or rewrites → `breaking` (and flag in PR body)
  - Yes, needs app + DB to deploy together → `coordinated-deploy`
- **API contract change** — `backend/routes/` or `shared/api/` touched?
  - Only new endpoints / new optional fields → `additive`
  - Removed/renamed endpoint/field → `breaking`
  - Versioned path (`/v2/…`) added → `versioned`
  - Else → `none`
- **Background job change** — `backend/core/lifespan.py`, `backend/utils/*_loop.py`, `scheduled_rides.py`, `payment_retry.py`, `surge_engine.py`?
  - New loop registered → `new-loop`
  - Existing loop body changed → `modified`
  - Loop removed → `deleted`
  - Else → `none`
- **Feature flag** — diff mentions `growthbook`, `featureFlag`, `FeatureFlag`, or a new env-gated branch?
  - New flag introduced → `behind-new-flag`
  - Flag removed (cleanup) → `removes-existing-flag`
  - Else → `none`
- **Config / secret change** — `backend/core/config.py`, `.env.example`, or `app_settings`-targeting migration?
  - New env var required → `new-env-var`
  - New `app_settings` row required → `app_settings-row`
  - Credential rotation required → `rotation-required`
  - Else → `none`
- **Rollback plan** — default to `git-revert-safe` **unless**:
  - Schema is breaking or data-migrating → `revert-plus-data-cleanup`
  - Requires mobile release + backend deploy in lockstep → `coordinated`
  - Cannot be safely reverted (regulatory write, external API call) → `not-revertible`
  - In every case write one line on how the revert actually works, including whether the old backend code can tolerate the new DB.
- **Dependencies / coordination** — list other PRs (`#N`), required mobile release, secret rotation, or write `none`.
- **Assumptions** — one line on what the PR assumes (e.g. "assumes corporate wallet balance > 0 for all active accounts"). Write `none` only when truly none.

## 4 · Risk decision rules

Walk these top-down; first match wins:

1. Touches `backend/core/{lifespan,config,middleware}.py`, `backend/db_supabase.py`, `backend/server.py` → **high**
2. Touches `shared/api/`, `shared/types/`, `shared/schema/` and change is not purely additive → **high**
3. Irreversible migration (`DROP`, backfill, column type change) → **high**
4. Money paths (`services/fare_*`, `services/corporate_*`, `routes/payments`, `routes/wallet`, `routes/corporate*`, `utils/surge_engine`) → **medium** (bump to **high** if surge cap or settlement flow changes)
5. Auth / OTP / RLS paths → **medium**
6. Safety / SOS / insurance paths → **medium**
7. Single-surface feature or fix, no regulated data → **low**
8. Docs-only or CI-only, no behaviour change → **low**

Always include a one-line justification for `medium`/`high`.

## 5 · Tier 3 compliance flags — auto-tick

Tick a box only if the diff actually warrants it. The `auto-summary` job will
call out mismatches on the PR, so don't over-claim.

| Flag | Tick if diff touches… |
|---|---|
| Money-touching | any path listed under `area:money` in `.github/labeler.yml` |
| PIPEDA-relevant | logging/analytics/Sentry code, consent text, data export/deletion endpoints, PII column definitions |
| SK Transportation Act | driver eligibility, trip retention, receipt tax lines, accessibility, surge cap |
| Auth / RLS | `routes/auth`, `routes/admin/auth*`, `utils/crypto`, `utils/rate_limiter`, any `*rls*.sql` / `*policy*.sql` migration |
| Safety | `routes/safety`, `routes/sos`, `services/insurance_*`, `utils/emergency_*`, insurance-period logic |
| Third-party SDK added | new dependency in `requirements.txt`, `package.json`, or `yarn.lock` |
| Breaking change to `shared/` | `shared/api/`, `shared/types/`, `shared/schema/` where change is removal/rename/type-narrowing |

Each ticked box needs the one-line justification the template asks for — don't
leave it as `<...>`.

## 6 · Tier 4 — verification

- **Unit tests / Integration tests** — look at `backend/tests/`, `*/__tests__/`
  in the diff. If tests were added → `added` + count; if modified → `updated`
  + one-line reason; if genuinely N/A (docs, config) → `not-applicable`.
- **Metrics / logs introduced** — grep the diff for `logger.info|error|warning`,
  `metric(`, `spinr.<domain>.` strings; list them (confirm naming conforms to
  `spinr.<domain>.<metric>.<unit>`).
- **Screenshots / video** — if any `rider-app/`, `driver-app/`, or
  `admin-dashboard/` UI component changed, write `<attach screenshots>` as a
  placeholder so CI blocks until the human adds them.
- **Perf numbers** — if the diff touches dispatch, fare calc, fare settlement,
  WS fan-out, driver location, token refresh, or Stripe webhook paths, insert
  `<before/after P95>` as a placeholder so CI blocks.
- **Pre-merge checklist** — tick only what you actually did. For items you
  can't tick (e.g. "tested with rider + driver + admin accounts" when you only
  ran the backend), write an inline `N/A — reason`.

## 7 · Things you do NOT fill

The `pr-checks` workflow appends these automatically once the PR opens — do
not pre-fill them, or the expander will see the marker and skip:

- Tier 5 · Migration details
- Tier 5 · Money-touching details
- Tier 5 · UI change details
- Tier 5 · Auth / RLS details
- Tier 5 · Background-loop details
- Tier 5 · RLS policy details
- Tier 5 · Safety details
- Tier 6 · Bug-fix notes
- Tier 7 · High-risk stop condition

Tier 7 · Conflict & Debug Log: if step 1 detected merge commits, leave a short
stub referencing the `merge-conflict-detect` advisory the workflow will post.

## 8 · Trivial escape hatch

If and only if the entire diff is:

- formatting / whitespace only, or
- a single typo in a comment or string, or
- a `.lock` / `yarn.lock` / `package-lock.json` bump with no code change, or
- removing a dead comment block

…set **Type: trivial**, fill Tier 1 only, and delete everything below the
`<!-- trivial-stop -->` marker. Do not abuse this — the `scope contract`
checkbox is an attestation.

## 9 · Create the PR

Target branch: **`main`** — Spinr is a single-trunk repo (no `develop`).
Backend auto-deploys from `main`, so risk-tier this PR honestly: anything
declared `risk: high` should also be opened as a draft until the on-call
sign-off is in.

Title format: `<type>(<scope>): <one-line subject>` — keep under 70 chars.

Use the GitHub MCP tool `mcp__github__create_pull_request` with:

- `title`: as above
- `body`: the filled-in template (Tiers 1–4; Tier 5+ left for the workflow)
- `base`: `main`
- `head`: current branch
- `draft`: `true` if any required field is still a `<placeholder>`, if tests
  are not yet green, or if `risk: high` and on-call hasn't acknowledged

## 10 · Report back

Reply to the user with:

- PR URL
- Declared type / risk / audience
- Surfaces touched (from diff)
- Any compliance boxes that were ticked
- Fields intentionally left as `<placeholder>` for the human (name them)
- Whether the PR was opened as draft

Do **not** merge. A human reviewer (and the auto-checks) decide that.
