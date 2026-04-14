# Spinr Production Deployment Runbook

This directory is the **single source of truth** for taking Spinr from
code-complete to serving real users. Every environmental setup step an
operator has to perform by hand lives here — the code itself refuses to
start in production with weak defaults, so this runbook exists to fill
in the gap between "the code is ready" and "traffic is live".

> **Audience:** the operator performing the deploy — could be a founder,
> an SRE, or an engineer. No prior context about the repo is assumed.
>
> **Companion docs:**
> - [`docs/ops/SECRETS_ROTATION.md`](../ops/SECRETS_ROTATION.md) — how
>   to rotate each secret after launch.
> - [`docs/ops/INCIDENT_RESPONSE.md`](../ops/INCIDENT_RESPONSE.md) —
>   what to do when the app misbehaves post-launch (on the P1 roadmap).
> - [`docs/runbooks/`](../runbooks/) — per-scenario response scripts.

---

## What "ready to deploy" means today

The code is **C+ → B- production-ready** as of the 2026-04 audit:

- ✅ All 10 P0 blockers from the audit are merged (PR #126).
- ✅ All Node lockfile security alerts closed (PR #135).
- ✅ Production-config validator in `backend/core/middleware.py` will
  refuse to start with weak secrets — the environment MUST be correct.
- ⚠ 52 P1 items remain open (see
  [`docs/audit/production-readiness-2026-04/09_ROADMAP_CHECKLIST.md`](../audit/production-readiness-2026-04/09_ROADMAP_CHECKLIST.md)).
  None of them block first-user launch; they are scheduled for the
  first 4-6 weeks of operation.

The gate is **not code**. The gate is **third-party provider setup,
secrets, and domain wiring**.

---

## The deploy sequence (do these in order)

Each file below is a standalone guide. Follow them end-to-end, in the
order listed. Each guide ends with an explicit "done when…" checklist —
only proceed to the next guide when the previous one's checklist is
100% green.

| # | Guide | What you do | Time |
|---|---|---|---|
| 1 | [`SECRETS_INVENTORY.md`](./SECRETS_INVENTORY.md) | Generate every secret value up-front, store them in a vault. | 45 min |
| 2 | [`01-supabase.md`](./01-supabase.md) | Create Supabase project, apply schema + migrations, seed the `settings` table, enable RLS. | 90 min |
| 3 | [`05-third-party-services.md`](./05-third-party-services.md) | Stand up Firebase, Stripe, Twilio, Redis, Google Maps, Sentry, SendGrid, Cloudinary. | 3-4 hrs |
| 4 | [`02-backend-fly.md`](./02-backend-fly.md) | Deploy FastAPI backend to Fly.io with all secrets populated. | 45 min |
| 5 | [`03-admin-vercel.md`](./03-admin-vercel.md) | Deploy Next.js admin dashboard to Vercel. | 20 min |
| 6 | [`04-mobile-eas.md`](./04-mobile-eas.md) | Build rider + driver apps via EAS; submit to App Store + Play Store. | 90 min (+ store review) |
| 7 | [`CHECKLIST.md`](./CHECKLIST.md) | Run the final go/no-go verification. **All green = launch.** | 30 min |

**Total active operator time to launch:** roughly 1 working day,
excluding app-store review (typical 2-7 days for first submission).

---

## What the operator needs before starting

Non-negotiable:

- [ ] A **company email domain** (e.g. `@spinr.app`) with access to
      create provider accounts. Avoid personal Gmail.
- [ ] A **password manager or secrets vault** (1Password, Bitwarden,
      AWS Secrets Manager, HashiCorp Vault). You'll generate ~25
      production secrets — do not store them in a text file.
- [ ] **Credit card or company PO** for paid tiers:
      Supabase (Pro), Fly.io, Upstash (Redis), Twilio, Stripe,
      Sentry, optionally Vercel Pro.
- [ ] **Two DNS-controllable domains** (or subdomains):
      `api.<domain>` for the backend, `admin.<domain>` for the
      dashboard. Either registrar access or API access to the DNS
      provider (Cloudflare, Route53).
- [ ] **Apple Developer Program** ($99/yr) and
      **Google Play Console** ($25 one-time) accounts with
      agreements signed. Both require 1-2 days to propagate after
      payment. Start this first.
- [ ] **Legal copy** for Terms of Service + Privacy Policy.
      Canadian TNCs must comply with PIPEDA and, in Quebec, Law 25.
      Do not ship with placeholder legal text — the
      `settings.terms_of_service_text` and `privacy_policy_text`
      columns must be populated before the first rider can sign up.

Nice to have:

- [ ] A staging environment mirroring production. Sprint 4 of the P1
      roadmap adds this. For first launch, skip and deploy to prod.
- [ ] PagerDuty / Ops Genie for the on-call rotation.
- [ ] A status page (StatusPage, Instatus, Better Stack).

---

## Environment matrix (what lives where)

| Component | Environment | Lives on | How to access |
|---|---|---|---|
| **Backend API** | production | Fly.io (`spinr-backend`) | `fly status --app spinr-backend` |
| **Admin dashboard** | production | Vercel | Vercel dashboard |
| **Rider app** | production | App Store + Play Store | Apple Connect / Play Console |
| **Driver app** | production | App Store + Play Store | Apple Connect / Play Console |
| **Database** | production | Supabase (one project) | Supabase dashboard |
| **Redis** | production | Upstash or Fly Redis | Provider console |
| **Payments** | production | Stripe Live Mode | Stripe dashboard |
| **SMS** | production | Twilio | Twilio console |
| **Push** | production | Firebase FCM (Android) + APNs (iOS) | Firebase console |
| **Monitoring** | production | Sentry + Fly logs | Sentry UI + `fly logs` |
| **Email** | production | SendGrid | SendGrid dashboard |
| **Images** | production | Cloudinary | Cloudinary console |
| **Maps** | production | Google Maps Platform | Google Cloud Console |

There is no staging environment today. Sprint 4 of the P1 roadmap adds
a staging Supabase + staging Fly app at `spinr-backend-staging`.

---

## Cost estimate (first month)

Rough per-month at MVP traffic (100 DAU, 500 rides/day):

| Service | Tier | Est. cost |
|---|---|---|
| Supabase Pro | includes 8 GB DB + 100 GB egress | $25 |
| Fly.io | 2× shared-cpu-1x @ 1GB, yyz | $15-30 |
| Upstash Redis | Pay-as-you-go, ~1M cmds/mo | $5-10 |
| Vercel | Hobby OK for admin | $0-20 |
| Twilio | ~$1 per phone number + $0.0075/SMS | $20-40 |
| Stripe | 2.9% + $0.30 per txn | usage |
| Firebase | FCM free, App Check free | $0 |
| Google Maps | $200 credit; heavy routing can exceed | $0-100 |
| Sentry | Developer free (5k events/mo) | $0 |
| SendGrid | Free tier 100 mail/day | $0 |
| Cloudinary | Free tier 25 credits/mo | $0 |
| Apple Developer | annual | $99 / 12 = $8 |
| Google Play Console | one-time $25 | — |
| Domain + DNS | Cloudflare | $2-10 |
| **Total** | | **~$80-250 / month** |

Scale up Supabase + Fly first when you hit traffic — both auto-scale
and will quietly grow the bill. Redis and Twilio scale linearly with
usage; watch them.

---

## When something goes wrong

The runbook is optimistic — every provider console has a moment where a
UI changes, a region is renamed, or a feature is gated behind a paid
tier you didn't expect. When that happens:

1. **Do NOT skip a step.** If a guide says "copy X to Y", both X and Y
   matter; a wrong value will fail the validator and the app won't
   boot.
2. **Use [`CHECKLIST.md`](./CHECKLIST.md) as the source of truth.** If
   a checkbox can't be ticked, the launch is blocked.
3. **Check [`docs/ops/SECRETS_ROTATION.md`](../ops/SECRETS_ROTATION.md)**
   for "this secret should look like…" structural guidance.
4. **Ask the team before working around the validator.** The
   production-config validator exists because silent misconfigurations
   cost more than a 10-minute delay.

---

## Final words before you start

This runbook exists because Spinr's backend is strict about what it
accepts. That strictness is intentional — it prevents the "we deployed
with a test Stripe key" class of incident. The tradeoff is that the
first deploy is a 1-day operator exercise rather than a single-click
affair. Budget the time. Do not cut corners on the legal content, the
secrets vault, or the 2FA-for-every-provider step.

Once it's live and the [`CHECKLIST.md`](./CHECKLIST.md) is green,
further deploys are a single `fly deploy` or Vercel git push.
