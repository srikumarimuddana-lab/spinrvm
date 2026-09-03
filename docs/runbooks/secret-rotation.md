# Runbook — Secret / Key Rotation Tracker

**Owner:** `devops` + `security` · **Status:** SCAFFOLDING — rotation dates below are placeholders
**Tracks:** `ACTION_ITEMS.md` E5 (see `docs/change-log/2026-09-03-e5-leading-indicator-monitoring.md`)

---

## Why this exists

Nothing in this repo previously tracked *when a credential was last
rotated* — confirmed by grepping `backend/core/config.py`'s `Settings`
class and every `secrets.*` reference across `.github/workflows/`. Startup
only validates that a handful of values aren't left at an insecure
**default** (`core/middleware._validate_production_config()` — e.g.
`ADMIN_PASSWORD != "admin123"`, `JWT_SECRET` ≥ 32 chars); it has no concept
of a credential being valid-but-stale.

**This doc holds metadata only — dates and cadence, never a secret value.**
Do not paste an actual key, token, or password into this file or into any
issue this workflow opens. If you're unsure whether something is a secret,
treat it as one.

**No rotation date below has been verified.** Every row starts `TBD` —
same caveat as `renewal-calendar.md`: I do not have access to any of these
vendor consoles or to Fly/GitHub secrets storage, so I cannot know when
these were last rotated. A blank row means "not yet audited," not "safe."

---

## How the automated check works

`.github/workflows/secret-rotation-monitor.yml` runs monthly and flags any
row where `days since Last rotated` exceeds `Recommended cadence`. Same
idempotent-tracked-issue pattern as the other monitors in this set.

---

## Rotation tracker

| Credential | Where it lives | Last rotated | Recommended cadence | Rotation procedure |
|---|---|---|---|---|
| `JWT_SECRET` | Fly + Railway secrets (must be byte-identical — see `docs/runbooks/railway-fly-failover.md`) | TBD | 180 days | Rotating invalidates every live access/refresh token — requires a coordinated rollout (dual-secret verify window) or a forced re-login for all users. Needs its own runbook before first rotation; do not rotate ad hoc. |
| `ADMIN_PASSWORD` | Fly + Railway secrets | TBD | 90 days | Update in both providers simultaneously — see the "identical across providers" rule in `railway-fly-failover.md`'s Safety checks |
| `ADMIN_EMAIL` | Fly + Railway secrets | N/A — not a secret, but flagged here because `_validate_production_config()` crash-loops the app if left at the `admin@spinr.ca` default | — | Not a rotation candidate; listed for awareness only |
| `OTP_PEPPER` | Fly + Railway secrets | TBD | 180 days | Rotating invalidates in-flight OTP hashes — coordinate with a low-traffic window |
| `SUPABASE_SERVICE_ROLE_KEY` | Fly + Railway secrets, Supabase dashboard | TBD | 180 days | Full DB access — treat as the highest-blast-radius credential in this table |
| `FIREBASE_SERVICE_ACCOUNT_JSON` | Fly + Railway secrets, Google Cloud service account | TBD | 180 days | Must stay valid JSON, not base64 — see the `fly secrets set` caveat in `railway-fly-failover.md` |
| Stripe secret/webhook signing keys | Fly + Railway secrets, Stripe dashboard | TBD | 180 days | Rotate webhook signing secret and endpoint secret together; verify `claim_stripe_event` idempotency table survives the cutover |
| Twilio auth token | Fly + Railway secrets (via `app_settings` DB table per CLAUDE.md's "Settings in DB" convention) | TBD | 180 days | Rotatable without redeploy since it's DB-stored, not an env var — confirm via admin dashboard |
| Google Maps API key | `app_settings` DB table | TBD | 365 days | Rotatable without redeploy — confirm key restrictions (HTTP referrer / API restrictions) carry over |
| `FLY_API_TOKEN` | GitHub Actions secret (`deploy-fly.yml`, `bootstrap-fly.yml`) | TBD | 180 days | Deploy-scoped token per the runbook's own guidance (`fly tokens create deploy`) — regenerate and update the GitHub secret |
| `RAILWAY_TOKEN` | GitHub Actions secret (`deploy-backend.yml`) | TBD | 180 days | Must be a project-scoped token per the workflow's own comments, not account/personal |
| `SENTRY_DSN` | GitHub Actions secret (`deploy-fly.yml`) | TBD | Not security-sensitive (write-only ingest key) — rotate only if leaked | — |
| `VERCEL_TOKEN` / `VERCEL_ORG_ID` | GitHub Actions secrets | TBD | 180 days | Admin dashboard deploy access |
| `EXPO_TOKEN` | GitHub Actions secret (`eas-build.yml`, `maestro-e2e.yml`) | TBD | 180 days | Mobile build pipeline access |
| `SUPABASE_SERVICE_ROLE_KEY` (CI, `apply-supabase-schema.yml`) | GitHub Actions secret | TBD | 180 days | Confirm this is the same value as the Fly/Railway one or intentionally separate — not verified here |

---

## Adding a new credential

1. Add a row with `Last rotated` and `Recommended cadence` filled in (never the value itself).
2. If the credential requires a coordinated rollout (like `JWT_SECRET`), write the procedure or link a dedicated runbook rather than leaving it implicit — a same-day ad hoc rotation of a session-invalidating secret is a live-tested-surface incident waiting to happen.

## Change Log

- 2026-09-03 — Scaffolding created (this doc + `secret-rotation-monitor.yml`), all dates TBD pending human audit. See `docs/change-log/2026-09-03-e5-leading-indicator-monitoring.md`.
