# 03 — Admin Dashboard Deploy (Vercel)

**Goal:** deploy `admin-dashboard/` (Next.js 16) to Vercel, pointing
at the Fly-hosted backend. Admin authentication is already wired in
code — you only need to set env vars.

**Time:** ~20 minutes.

**Pre-reqs:** Fly backend live at `https://api.<domain>` per
[`02-backend-fly.md`](./02-backend-fly.md).

---

## Step 1 — Create the Vercel project

1. Go to https://vercel.com/new.
2. **Import Git Repository** → select `ittalenthireca-sketch/spinr`.
   Grant Vercel access to the repo if asked.
3. **Configure project:**
   - **Project name:** `spinr-admin` (or match your convention).
   - **Framework preset:** Next.js (auto-detected).
   - **Root directory:** `admin-dashboard`. Click **Edit** next to
     Root Directory and pick the folder. This is essential — the
     Next.js app is NOT at the repo root.
   - **Build and Output Settings:** leave the Next.js defaults.

---

## Step 2 — Set environment variables

Before clicking Deploy, click **Environment Variables** and add:

| Name | Value | Environments |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | `https://api.yourdomain.app` | Production, Preview |
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | Development (optional — developers can use a local .env.local) |

That is the only required var. The admin dashboard's
`NEXT_PUBLIC_API_URL` is the one env setting it reads; everything
else (JWT, cookie name, Supabase access) is proxied through the
backend.

---

## Step 3 — Deploy

Click **Deploy**. First build takes 2-4 minutes. Success landing
page shows a `spinr-admin-<hash>.vercel.app` URL.

Open it and confirm:

- `/login` loads.
- Attempting to visit `/dashboard` redirects to `/login?next=/dashboard`.

If you see a 500 or blank page:

- Open the build log in Vercel → Deployments → latest → View build logs.
- Check for "module not found" (monorepo path issue) — confirm Root
  Directory is set to `admin-dashboard`.
- Check env var is set (Vercel → Settings → Environment Variables).

---

## Step 4 — Log in with bootstrap super-admin

1. Open `https://spinr-admin-<hash>.vercel.app/login`.
2. Email: the `ADMIN_EMAIL` you set on Fly.
3. Password: the `ADMIN_PASSWORD` you set on Fly.
4. Click Log in. You should land on `/dashboard`.

If login fails with a 401:
- Check `NEXT_PUBLIC_API_URL` points at your real Fly domain.
- Check the backend's `ADMIN_EMAIL` / `ADMIN_PASSWORD` match what
  you're typing.
- Check CORS: `ALLOWED_ORIGINS` on the Fly side must include
  `https://spinr-admin-<hash>.vercel.app` AND
  `https://admin.yourdomain.app`. Run:

  ```bash
  fly secrets set ALLOWED_ORIGINS='https://admin.yourdomain.app,https://spinr-admin-<hash>.vercel.app' \
    --app spinr-backend
  ```

---

## Step 5 — Map your custom domain

Vercel → Project → **Settings** → **Domains** → **Add**.

- Add `admin.yourdomain.app`.
- Vercel prints DNS records to add. Typically:
  - CNAME `admin` → `cname.vercel-dns.com` (or a specific value
    Vercel shows you).
- Add the record at your DNS provider.
- Wait 1-5 minutes for propagation. Vercel auto-issues the TLS cert.

Once verified, re-update the backend `ALLOWED_ORIGINS` to include
`https://admin.yourdomain.app` (it probably already does if you
followed the previous step).

---

## Step 6 — Seed integration keys via the admin UI

Log in at `https://admin.yourdomain.app/login`. Go to **Settings**
in the sidebar. Paste these values (they're stored in the Supabase
`settings` row, not in env vars):

- Stripe secret key (`sk_live_…`)
- Stripe publishable key (`pk_live_…`)
- Stripe webhook secret (`whsec_…`)
- Twilio account SID / auth token / from number
- SendGrid API key
- Cloudinary cloud name / API key / API secret
- (Optional) feature flags, fare configuration

Click **Save**. No backend restart needed; the backend reads the
`settings` row on demand per request.

Verify by clicking whatever test actions the UI exposes (e.g. "Send
test SMS", "Send test email") if available. Otherwise verify at
launch via the smoke tests in [`CHECKLIST.md`](./CHECKLIST.md).

---

## Step 7 — Restrict additional admin logins

The `ADMIN_EMAIL` / `ADMIN_PASSWORD` combo is the **bootstrap**
super-admin only. Real operators should each have their own account.

From the admin dashboard sidebar → **Staff** (or similar):

1. Create an account for each ops team member.
2. Assign the minimum role they need (not every operator needs
   super-admin).
3. Each operator sets their own password via the invite flow (or the
   dashboard force-reset, depending on UI).

Then **log out of the bootstrap account** and verify at least one
non-super-admin can log in and perform normal ops. The bootstrap
account should rarely be used.

---

## Step 8 — Production hardening knobs (optional but recommended)

- **Vercel Pro** ($20/mo) unlocks password-protected preview
  deployments — prevents preview builds leaking to the public.
- Enable **Vercel Web Application Firewall** if your Pro tier
  includes it — blocks abuse patterns at the edge.
- Add `SECURITY.md` to the repo root describing the vulnerability
  disclosure process.

---

## Done when…

- [ ] Vercel project built successfully.
- [ ] `https://admin.yourdomain.app` loads the login page.
- [ ] Bootstrap login works end-to-end.
- [ ] `admin.yourdomain.app` TLS cert is active.
- [ ] Backend `ALLOWED_ORIGINS` includes the admin domain.
- [ ] Stripe / Twilio / SendGrid / Cloudinary values pasted into the
      Settings page and saved.
- [ ] At least one real operator account exists and can log in.

Next: [`04-mobile-eas.md`](./04-mobile-eas.md) to build the rider and
driver mobile apps.
