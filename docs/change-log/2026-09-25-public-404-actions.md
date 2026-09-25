# Change Impact & Risk Log: the 404 page points public visitors somewhere useful

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | Claude Code session (claude.ai/code) |
| Surface(s) | admin-dashboard app (its public routes: `/track/*`, `/register/*`, `/company-portal/*`) |
| Domain (Sentry tag) | admin (display only) |
| PR / commit link | UX program W1.4 — branch `claude/spinr-animations-admin-ux-x7nl5x` |
| Related issue or gap ID | plan W1.4; research doc `docs/audit/2026-09-25-ux-motion-admin-website-research.md` |

## 1. Issue / gap identified

The app's only 404 page always offered "Go to Dashboard". The same Next.js app also serves three public audiences. For them, `/dashboard` redirects to the **staff** login, a dead end:

| Audience | Example unmatched URL | Before |
|---|---|---|
| Rider's family or friend on a shared tracking link | `/track/<token>/extra` | "Go to Dashboard" → staff login |
| Driver applicant on a mistyped recruitment link | `/register/drivers` | "Go to Dashboard" → staff login |
| Company user on a stale portal link | `/company-portal/<id>/extra` | "Go to Dashboard" → staff login |

## 2. Root cause

The 404 page was written for staff only, before the public routes shared the app.

## 3. Fix / remediation

`not-found.tsx` picks its message and action from the URL's path prefix (`usePathname()`):

| Path prefix | Message | Action |
|---|---|---|
| `/track/` | "This tracking link is invalid or has expired. Ask the rider to share it again." | none (no useful page exists for this audience) |
| `/register/` | unchanged | "Go to driver sign-up" → `/register/driver` |
| `/company-portal/` | unchanged | "Go to company portal" → `/company-portal` |
| anything else | unchanged | "Go to Dashboard" (unchanged) |

**The other half of W1.4 needed no code:** `/track` already honours Reduce Motion. W1.3's global `prefers-reduced-motion` rule overrides the car marker's inline `transform 600ms` transition with `!important`. The tracking page also already shows its own "Tracking unavailable / Tracking link is invalid or has expired." state for a bad or expired token.

## 4. Risk & impact on existing functionality

- **Blast radius:** one component, the root `app/not-found.tsx`. Nothing in the app calls `notFound()` (grep: 0 hits), so it renders only for unmatched URLs. No other file imports it.
- **Staff:** unchanged. Every path outside the three public prefixes gets the same message and "Go to Dashboard" link as before. Unauthenticated visitors on staff paths never reach it; middleware redirects them to `/login` first (confirmed: `/dashboard-typo` → 307).
- **Rendering:** `/_not-found` is dynamic (ƒ in the `next build` route table) because the root layout reads `headers()`. So the server renders it per request with the real path, and there is no static-HTML/hydration mismatch.
- **HTTP status:** still 404 on all paths (confirmed on the production build).
- **Not reached by this change:** on the tracking domain (`track.spinr.ca`), middleware answers the root and multi-segment paths with a plain-text `Not found` before Next.js renders anything. A single segment is rewritten to `/track/<token>`, which shows the tracking page's own error state. So a truncated share link already gets a proper message; this change only affects `/track/a/b`-shaped URLs. **Not changed but considered:** replacing that plain-text middleware 404 with this page. It touches host routing and the tracking CSP, so it is out of scope here and logged as a follow-up.
- **Visual baselines:** none of the 6 baselined pages renders the 404 page.

## 5. User-experience effect

- **Tracking-link viewers:** a malformed tracking URL now says the link is invalid or expired and to ask the rider for it again, instead of offering a staff dashboard.
- **Driver applicants and company users:** the button now leads to driver sign-up or the company portal.
- **Staff:** no change.
- **Mid-session:** applies on the next page load after deploy.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/not-found.tsx` | Message and action chosen by path prefix | Public visitors aren't sent to the staff login |
| `admin-dashboard/src/app/not-found.test.tsx` | New, 5 tests | Each audience's action, plus the unknown-path fallback |

## 7. Before / after

```tsx
// Before
<p className="text-muted-foreground mt-2">The page you are looking for does not exist or has been moved.</p>
<Button asChild><Link href="/dashboard">Go to Dashboard</Link></Button>
```

```tsx
// After
const { message, href, label } = actionFor(usePathname());
<p className="text-muted-foreground mt-2">{message}</p>
{href && (<Button asChild><Link href={href}>{label}</Link></Button>)}
```

## 8. Rollback plan

**No feature flag.** The change only affects visitors already on a 404 page, and staff paths are unchanged. **Full rollback:** revert and redeploy on Vercel (or promote the previous Vercel deployment). No data is touched.

## 9. Verification performed

- [x] **New tests:** 5 in `not-found.test.tsx`. Three fail on the old page (tracking, register and company paths) and all five pass on the new one.
- [x] **Full admin suite:** 87 files, 763 tests pass.
- [x] **Typecheck:** `tsc --noEmit` passes.
- [x] **Lint:** ESLint on both files is clean, as was the old file.
- [x] **Production build:** `npm run build` (`next build`) succeeds; `/_not-found` is dynamic (ƒ).
- [x] **Served build:** `next start` on the build, then `curl`:
  - `/register/drivers` → 404, "Go to driver sign-up"
  - `/track/abc/extra` → 404, the expired-link message, no button
  - `/dashboard-typo` → 307 to `/login` (unchanged)
- [x] **Accessibility review:** `spinr-accessibility-reviewer` ran on the diff (code-read only).
  - **Blocker:** none. Link names are descriptive and the heading structure is unchanged.
  - **Should-fix, checked against the code and not adopted:**
    - *Bare `/register`, `/track`, `/company-portal` fall through to "Go to Dashboard".* Public visitors can't reach a 404 there. Middleware treats only `/register/` and `/track/` as public, so a signed-out visitor on the bare path is redirected to `/login` first, and `/company-portal` is a real page. A staff member who reaches them gets the dashboard link, which is correct for staff.
    - *`/track/*` has no link.* This matches the tracking page's own error state, which has none either. The only site-wide destination would be spinr.ca, a new outbound link not asked for here.
  - **Nit, accepted:** a staff member on `/company-portal/a/b` sees "Go to company portal". Staff don't normally use that prefix.
  - **Pre-existing nit, incorrect:** the `FileQuestion` icon is already hidden from screen readers. The installed lucide-react (1.34) adds `aria-hidden="true"` whenever no accessibility prop is passed.
  - **Follow-up it surfaced, not fixed here:** a driver applicant who types bare `/register` is redirected by middleware to the **staff** login. Same class of gap, but in auth routing, so it's logged in the plan.
- [x] **Next.js docs checked:** the installed Next 16 docs (`not-found.md`) allow client hooks such as `usePathname` in a client `not-found` component.

## 10. What was NOT verified

- **`/company-portal/a/b` was not fetched from the served build,** because it needs a valid company session cookie; it is covered by the unit test only.
- **No screenshots:** the 404 page has no visual baseline. The layout is unchanged apart from the text and the optional button, so it was reasoned about, not screenshotted.
- **No screen-reader pass** (NVDA or VoiceOver).
- **The tracking domain was not exercised** (it needs the `track.spinr.ca` host header and a deployed environment).
