# Where Spinr runs, and how work gets there

A plain-English overview of the four environments, what to set up first, and
the day-to-day development loop. This is the "start here" doc — the decisions
behind it are in `docs/adr/011-environment-topology.md`, and the click-level
detail is in the three runbooks linked below.

> **Nothing here has been run yet.** The accounts, projects, and routing rules
> described in Part B do not exist. The config files and workflows are written
> and checked, but the first person to follow these steps is also the first
> person to test them. Budget an afternoon for stop 1, not an hour.

## Part A — Where everything runs

No new vendor accounts are needed. Every service below is one you already have;
what's missing is a few extra *projects* inside those existing accounts.

| The piece | Runs on | Why there |
|---|---|---|
| Backend (the API) | Fly.io, Toronto (`yyz`) | Closest region to riders. Railway is the backup copy (ADR-007). |
| Database | Supabase, `ca-central-1` | Must stay in Canada — PIPEDA data residency, not a preference. |
| Admin dashboard | Vercel | Already set up and working. |
| Rider & driver apps | Expo EAS | Already set up — `development`/`test`/`preview`/`production` channels exist. |
| Traffic routing | Cloudflare | Decides which backend a request reaches. This is the safety switch. |
| Error reports | Sentry | Tells you when something broke, and where. |

### The four stops your code makes

| # | Stop | Data | Serves | Rough cost | State |
|---|---|---|---|---|---|
| 1 | Dev & test | Synthetic | You + CI | ~$25–30/mo | To build (E1a) |
| 2 | Staging | Synthetic | Migration rehearsal | ~$25–30/mo | To build (E1) |
| 3 | Canary | **Real** | ~5% of real users | ~$15–20/mo | To build (E1b) |
| 4 | Production | **Real** | Everyone | existing | Live today |

Roughly **$70–80/month** on top of current spend, mostly the two new databases.
These are ballpark figures — verify against current pricing.

Stop 1 can start on Supabase's free tier to spend nothing. The catch: free
projects sleep after about a week of inactivity and need waking. Fine while
you're trying the idea; annoying once the team depends on it.

## Part B — Setting it up, once

Do these in order. Stop 3 touches real riders, so it comes last — after you've
done the harmless version twice.

### Stop 1 — Dev & test (start here)

1. **Make the database.** Supabase → new project `spinr-dev`, region
   **Canada (`ca-central-1`)**. Save the password; it's shown once.
2. **Build the tables.** Copy the connection string from Supabase →
   Settings → Database → URI, export it as `DATABASE_URL`, then:
   ```bash
   cd backend
   python -m backend.scripts.run_migrations --dry-run   # shows what would happen
   python -m backend.scripts.run_migrations             # does it
   ```
3. **Make the server.**
   ```bash
   fly apps create spinr-backend-dev
   fly tokens create deploy -a spinr-backend-dev
   ```
4. **Tell GitHub.** Settings → Secrets and variables → Actions. Add
   `FLY_API_TOKEN_DEV` (from step 3), `SUPABASE_DEV_URL` and
   `SUPABASE_DEV_SERVICE_ROLE_KEY` (Supabase → Settings → API).
5. **Press the button.** Actions → *Deploy Backend to Fly.io (Dev/Test)* →
   Run workflow, on any branch.
6. **Point your laptop at it.** Same two Supabase values in `backend/.env`
   with `ENV=development`; set the apps' `EXPO_PUBLIC_BACKEND_URL` to the new
   dev address.

Full detail: `docs/runbooks/dev-test-environments.md`

### Stop 2 — Staging

Same six steps, `staging` instead of `dev`: project `spinr-staging`, app
`spinr-backend-staging`, secrets `FLY_API_TOKEN_STAGING` /
`SUPABASE_STAGING_URL` / `SUPABASE_STAGING_SERVICE_ROLE_KEY`, workflow
*Deploy Backend to Fly.io (Staging)* (already written, waiting on the secrets).

Once up, staging earns its keep one way: **every database change runs here
first.** Watch it work, time it, then run it for real.

Full detail: `docs/runbooks/staging-environment.md`

### Stop 3 — Canary (real riders)

Unlike the first two, this uses the **real database** and serves **real
people**. It is a slice of production, not a practice area.

1. **Make the server.** `fly apps create spinr-backend-canary`, then a deploy
   token saved as `FLY_API_TOKEN_CANARY`.
2. **Give it production's settings.** Database, Redis, Firebase, Stripe — all
   matching production exactly. Especially `JWT_SECRET`: if it differs, riders
   already signed in get rejected when they land on the canary.
3. **Add the traffic switch.** Cloudflare: one pool, production weight 100,
   canary weight **0**. Session stickiness on, so a rider stays on one side for
   a whole trip.
4. **Leave it at zero.** Zero is the resting position.

> **The one line never to "tidy up".** `backend/fly.canary.toml` sets
> `ENV = "production"`. On a file called *canary* that looks wrong. It isn't.
> Four security behaviours check for that exact string — change it and you
> silently disable Firebase App Check, drop the `secure` cookie flag, skip
> HSTS, and send every iOS push to Apple's sandbox where real devices never
> receive it. No error appears. The notifications simply never arrive.

Full detail, including soak and abort criteria:
`docs/runbooks/canary-environment.md`

## Part C — A normal working day

Most changes stop at step 7; the last two are only for risky work.

1. **Start fresh.** `git checkout main && git pull`
2. **Make a branch.** One per piece of work.
3. **Build it locally.** `python3 -m backend.server`, `yarn start` for the
   apps, `npm run dev` for admin. Your laptop talks to the dev database, so
   nothing can reach a real rider.
4. **Check your own work.** `pytest -m unit`, `yarn tsc --noEmit`.
5. **Push and open a PR.** `ci.yml` runs tests, type checks, and security
   scans automatically.
6. **Try it on a real phone** *(only if needed)*. Run the dev deploy workflow
   with `publish_ota` ticked — the change reaches the test build without an
   app-store build.
7. **Merge to `main`.** Production deploys from here. **Most work ends here.**
8. **Changed the database?** Staging first. Time it.
9. **Risky change?** Canary at 5%, watched across a full busy period — not
   twenty quiet minutes. Clean → deploy to production, canary back to 0.

**Risky** means money, ride states, sign-in, dispatch, or a large dependency
bump. **Not risky**: copy edits, admin-screen tweaks, tests, docs.

**If canary looks wrong:** set its Cloudflare weight to 0 first, investigate
second.

## Part D — Fix these before starting

| What's wrong | Why it matters | Effort |
|---|---|---|
| Railway backup is stale (`ACTION_ITEMS.md` C5) | The emergency copy stopped updating. A Fly outage today would fail over to an old build. | Small |
| `DEPLOYMENT.md` is out of date | Still tells new developers to deploy to Railway, which stopped being true when Fly became primary (ADR-007). | Small |
| Staging's health check never runs | `deploy-backend-staging.yml` gates the probe on a step-level env var, but the `secrets` context isn't available in a step `if:` — so it silently skips every run and reads as passing. `deploy-fly.yml` documents the correct pattern. | One line |
