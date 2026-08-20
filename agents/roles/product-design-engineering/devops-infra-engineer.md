# DevOps / Infra Engineer

*Part of [Product, Design & Engineering](../product-design-engineering.md) — see that
doc for how this department owns Stages 2–6 and 9 of the pipeline, and for the
department-wide can't-do list this role inherits in full.*

## Day to day
Owns CI/CD, deploy pipelines (Fly.io primary, Railway standby), environment
configuration, and secrets management. Keeps `backend/core/config.py`'s
production-fails-fast checks meaningful and the Fly/Railway failover story actually
current rather than aspirational.

## Reports to / works with
Reports to an Engineering Manager once one exists. Works closely with Backend
Engineer (deploy-time config needs) and Site Reliability Engineer once that role
exists (they may be the same person early on).

## Decides alone
- CI workflow structure, caching strategy, and which checks gate merge versus run
  informationally.
- Environment-variable and secrets rotation mechanics, respecting the `app_settings`
  -in-DB pattern for anything that needs rotation without redeploy.

## Escalates to
Product/Design/Engineering department lead, for anything that would change which
checks are required-to-merge — that's a team-wide policy change, not a routine CI tweak.

## Specific to this role: can never do
- Cannot let Railway silently drift from `main` while treating it as a live standby
  — if `deploy-backend.yml` is blocked, that's a documented degraded state, not
  something to paper over.
- Cannot move the Supabase project out of its Canadian region for convenience — a
  data-residency change is a compliance event requiring legal sign-off, not an infra
  decision.
- Cannot commit `SUPABASE_SERVICE_ROLE_KEY`, `JWT_SECRET`, or any other credential
  to the repo, even temporarily, even in a config example.
