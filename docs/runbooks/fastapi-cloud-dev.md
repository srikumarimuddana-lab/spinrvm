# Dev tier on FastAPI Cloud (trial)

> **Status: scaffolding only, not live.** No FastAPI Cloud app or secret
> exists yet. Decision record: `docs/adr/011-environment-topology.md`
> (2026-08-26 addendum).

> **This is a trial with a decision at the end of it.** FastAPI Cloud is in
> public beta, and regions, background workers, and scheduled jobs are on its
> published roadmap rather than shipped. The dev tier is where we learn
> whether it can carry Spinr at all. The Fly.io dev path
> (`backend/fly.dev.toml`, `.github/workflows/deploy-backend-dev.yml`) is kept
> as the fallback — **do not delete it** until the gates below are answered.

## Why the dev tier and not something else

Dev is the only tier where FastAPI Cloud's unknowns are cheap. It holds no
real user data, serves no riders, and can be down for a day without anyone
noticing. Every unknown that would be a P0 in production is, here, just an
observation to write down.

## What was added to the repo for this

| File | Purpose |
|---|---|
| `backend/pyproject.toml` | Required by the deploy path. **Mirrors** `requirements.txt`; not a second source of truth. |
| `backend/scripts/sync_pyproject_deps.py` | Generates (`--write`) and verifies (`--check`) that mirror. |
| `backend/main.py` | Re-export shim — FastAPI discovery looks for `main.py`, Spinr's app lives in `server.py`. |
| `.github/workflows/deploy-backend-dev-fastapicloud.yml` | The deploy, manual dispatch only. |
| `pip-compile-check.yml` → `pyproject-sync` job | Fails CI if the mirror drifts. |

Production is untouched: `backend/Dockerfile` and `railway.json` still build
from `requirements.txt` and target `server:app`.

## Setup

### 1. Create the app

```bash
pip install fastapi-cloud-cli==0.23.0
fastapi cloud login
cd backend
fastapi cloud apps create
```

Run it from `backend/`, since that is the directory holding `pyproject.toml`.
This writes `.fastapicloud/cloud.json` linking the directory to the app.

### 2. Get the two CI values

```bash
fastapi cloud apps list      # note the app ID
fastapi cloud tokens create  # prints a deploy token, shown once
```

Add both to GitHub → Settings → Secrets and variables → Actions:

| Secret | Value |
|---|---|
| `FASTAPI_CLOUD_APP_ID` | The app ID from `apps list` |
| `FASTAPI_CLOUD_TOKEN` | The deploy token from `tokens create` |

### 3. Set the app's environment variables

Point it at the **dev** Supabase project — the same synthetic-data project the
Fly dev tier uses (`docs/runbooks/dev-test-environments.md` step 1). Never the
production one.

```bash
fastapi cloud env set ENV development
fastapi cloud env set SUPABASE_REGION ca-central-1

# Secrets: --value-stdin keeps the value out of your shell history and out of
# any process list. --secret marks it so it is not echoed back.
printf %s "$DEV_SUPABASE_URL" | fastapi cloud env set SUPABASE_URL --secret --value-stdin
printf %s "$DEV_SERVICE_KEY" | fastapi cloud env set SUPABASE_SERVICE_ROLE_KEY --secret --value-stdin
printf %s "$(python3 -c 'import secrets;print(secrets.token_urlsafe(48))')" \
  | fastapi cloud env set JWT_SECRET --secret --value-stdin
```

Generate fresh dev values. Never copy production's. Leave Stripe credentials
unset (ADR-011, "Deliberately out of scope").

`ENV=development` matters: it is what keeps App Check enforcement and HSTS off,
which mobile dev builds need. Do not set it to `production` here — that is the
opposite of the canary's rule, and for the opposite reason.

### 4. Deploy

GitHub → Actions → **Deploy Backend to FastAPI Cloud (Dev)** → Run workflow.

Or locally from `backend/`: `fastapi cloud deploy`.

Logs: `fastapi cloud logs`.

## The three gates

These decide whether FastAPI Cloud goes any further than dev. Answer them
while using the dev tier — they need observation, not speculation. Record the
answers in `ACTION_ITEMS.md` E1a.

### Gate 1 — Do the background loops survive idle? (blocking)

`backend/core/lifespan.py` starts **18 asyncio loops** on every replica:
surge engine, scheduled dispatch, payment retry, document expiry, safety
check-in, stuck-ride sweeper, and more. They run *inside* the app process, so
they live only while a container lives.

If FastAPI Cloud scales the app to zero when idle, they stop — and they stop
**silently**. No error, no alert; scheduled rides simply never dispatch and
surge never updates. This is the same shape of failure as the APNs-sandbox
trap in `fly.canary.toml`: nothing looks broken.

**How to test it.** Deploy, leave the app completely idle for an hour, then
check whether the surge engine (2-minute interval) logged its runs across that
whole window, or only around your requests.

```bash
fastapi cloud logs | grep -i surge
```

A contiguous run of entries every ~2 minutes is a pass. A gap that matches
your idle period is a fail.

**If it fails:** FastAPI Cloud cannot host any Spinr tier until its
background-workers feature ships. Stay on Fly. This is not a workaround
candidate — moving 18 loops out of process is a much larger architectural
change than a hosting migration.

### Gate 2 — Do WebSockets hold? (blocking)

Every rider and driver holds one long-lived WebSocket, with a 30-second
heartbeat (`socket_manager.py`). Fly's config counts concurrency in
connections for precisely this reason.

**How to test it.** Connect a driver app to the dev backend, go online, and
leave it connected through a simulated ride. Watch for disconnects, and check
whether an idle connection survives past any platform timeout.

**If it fails:** same conclusion as gate 1.

### Gate 3 — Which region is it actually in? (blocking for anything past dev)

Region selection is on the roadmap, not shipped, so the dev app lands wherever
FastAPI Cloud puts it. For dev that is tolerable — the data is synthetic and
invented.

It is **not** tolerable for staging, canary, or production. `CLAUDE.md`'s
PIPEDA section requires Canadian residency, and `core/config.py` hard-fails at
startup when `SUPABASE_REGION` is not `ca-*`. Real user data must not be
processed in an unknown region without legal sign-off.

**How to test it.** Ask FastAPI Cloud support directly which region serves the
app, and whether a Canadian region is on the near-term roadmap. This is a
question for a human, not something to infer from latency.

## If the gates pass

Then a staging trial is worth planning, and only after that a canary. Do not
skip tiers: the canary runs on the production database, so a platform that has
not survived staging has no business touching it.

## If the gates fail

Turn the trial off by simply not running the workflow. Nothing else is needed —
no traffic is routed to it and nothing depends on it. The Fly dev path is
still configured and one workflow run away.

The repo-side additions (`pyproject.toml`, the sync script, `main.py`) are
harmless to keep either way — the mirror is CI-enforced, and `main.py` is an
alias production does not use. Keep them if a future retry is likely; remove
them together if not.

## What NOT to do

- **Never point this at the production Supabase project.** `ENV=development`
  disables App Check and HSTS; with production data that is a live exposure.
- **Never hand-edit `pyproject.toml`'s dependency list.** It is generated;
  CI fails on drift. Change `requirements.in`, re-run pip-compile, then
  `sync_pyproject_deps.py --write`.
- **Never add logic to `backend/main.py`.** It is an alias. Anything added
  there runs on this tier and nowhere else — exactly the divergence a dev tier
  exists to prevent.
- **Never delete the Fly dev path** while the gates are unanswered.
- **Never promote past dev on gate answers you assumed rather than observed.**
