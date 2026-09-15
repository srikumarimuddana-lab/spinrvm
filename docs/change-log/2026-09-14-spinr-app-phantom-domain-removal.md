# Change Impact & Risk Log — remove the unregistered `spinr.app` domain

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-14 |
| Author | Claude Code session, branch `claude/inspiring-cerf-1s4ela` |
| Surface(s) | backend, rider-app, driver-app (+ docs, agents/) |
| Domain (Sentry tag) | auth (CORS), rides (tracking WebView), drivers (referrals) |
| PR / commit link | branch `claude/inspiring-cerf-1s4ela` |
| Related issue or gap ID | `docs/audit/2026-09-14-spinr-app-phantom-domain-audit.md`; LGL-11 in `reports/audits/2026-07-22-legal-content-validation-v1.md` |

## 1. Issue / gap identified

96 references across the repo pointed at `spinr.app` (and `spinr-track.app`), neither of
which resolves — `NXDOMAIN`, no A and no NS records — and neither of which is registered
to Spinr (confirmed by the product owner, 2026-09-14). Found by tracing the
`https://{target}.spinr.app` string in `agents/deployer.py`. Two of the references were
security allowlist entries; two were live, tappable links in the rider signup consent flow.

## 2. Root cause

Two structural blind spots, not carelessness:

- **The domain monitor can only see what is already served.** `cert-domain-monitor.yml`
  seeds `TLS_HOSTS`/`WHOIS_DOMAINS` from *"the Cloudflare DNS zone for spinr.ca"*, so a
  hostname that is referenced in code but never provisioned is invisible to it by
  construction. `docs/runbooks/renewal-calendar.md` has the same shape and lists `spinr.ca`
  as the only registered domain. Nothing anywhere lints code-referenced hostnames against
  the set of domains actually owned.
- **One audit hardened an assumption into code.** `reports/audits/2026-04-19-rider-app-v1.txt:2316`
  reasoned *"applinks:spinr.app … meaning spinr.app hosts a web landing page"* — it inferred
  the domain's existence **from the deep-link config**, and remediation R-P1-27 then added it
  to the CORS allowlist. An unverified assumption propagated into two more files.

Separately, the store-metadata files mark unfinished values with a `TODO:` prefix.
`"privacy_url": "https://spinr.app/privacy"` carried no marker, so it read as a finished
value and the file's own review convention would never have flagged it.

## 3. Fix / remediation

Eight commits, each one logical change. Dead references **removed** rather than repointed
wherever nothing consumed them, so the diff asserts nothing about a domain we do not own.
Real values corrected where a consumer exists.

## 4. Risk & impact on existing functionality

**Blast radius: cross-surface, but every individual change is a narrowing or a correction —
nothing new is trusted, and no behaviour is widened.**

| Change | What else touches it | Regression risk |
|---|---|---|
| CORS `always_allowed` | `init_middleware` is the only builder; `ALLOWED_ORIGINS` env var is a *second, independent* source | Removing origins can only narrow access. No browser client exists for an NXDOMAIN host. `tests/test_p1_cors.py` asserts only that `admin-spinr.spinr.ca` is present and `spinr-admin.vercel.app` is absent — neither touched. **Operator action required:** if ops copied the `ENVIRONMENT_VARIABLES.md` example, the deployed env var may still carry `https://spinr.app`; strip it there too |
| `ALLOWED_TRACKING_HOSTS` | `ride-tracking-webview.tsx` only | Verified the `track_base_url` path at line 87 (`setResolvedUrl(\`${trackBaseUrl}/${token}\`)`) does **not** go through `isAllowedTrackingUrl` — only the caller-supplied `trackingUrl` deep-link param does. So an admin who has `app_settings.track_base_url` set to the retired host is unaffected by this change; that URL was already failing on DNS |
| `referral_link` removal | Grepped repo-wide: 6 occurrences total, now 0. No render site, no `Share`, no `Clipboard`, no serializer, no admin surface | The rider/driver referral pair was an **undeclared fork** — `docs/known-forks.md` had no row, so the pre-commit sibling check would not have warned. Both sides changed in one diff and a registry row added |
| `app.config.ts` deep-link blocks | Expo prebuild / EAS | Native-config only. The custom `scheme` (line 17) is untouched, so `spinr-user://` routing — which is how `ride-tracking-webview` actually receives params — is unaffected |
| Money paths | `paid_referral_earnings`, `_money_str` in the same functions | **Not touched.** Only the one dict key was removed from each response |
| Background loops / ride state machine / wallet deltas | — | **No interaction.** Nothing in this diff touches `lifespan.py`, any ride status transition, or any wallet delta |

## 5. User-experience effect

- **Rider — visible, and an improvement.** The signup consent checkbox in
  `profile-setup.tsx` opened `https://spinr.ca/terms` and `/privacy`; both were wrong
  (missing the `www.` host and the `/legal/` path). They now open the published pages.
  Visible to any rider completing profile setup. Not visible mid-ride.
- **Rider — no change.** Referral screens share the CODE, never the link, so removing
  `referral_link` changes nothing on screen.
- **Rider — narrowing.** A `spinr-user://ride-tracking-webview?trackingUrl=https://spinr-track.app/...`
  deep link now shows "Invalid tracking link." instead of attempting to load. That domain
  does not resolve, so the previous behaviour was a spinner into a load error.
- **Driver / corporate admin / internal admin — none.**
- **API consumers** — the 429 body's `documentation_url` value changed; the key is
  unchanged and still present.
- No notification copy changed.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/core/middleware.py` | Dropped 4 unregistered origins from `always_allowed` | CORS grant for hostnames anyone could buy |
| `rider-app/app/ride-tracking-webview.tsx` | `ALLOWED_TRACKING_HOSTS` narrowed to `track.spinr.ca` | In-app WebView trusted a buyable domain, reachable via a registered deep-link scheme |
| `rider-app/__tests__/rideTrackingWebviewScreen.test.tsx` | Legacy-host "is allowed" test → "is rejected" regression test | Keep the narrowing enforced rather than delete coverage |
| `rider-app/__tests__/{rideInProgress,aiAssistant,driverArriving}Screen.test.tsx` | Mock `track_base_url` swapped to `track.spinr.ca` | Same assertions, no dependence on an unowned domain |
| `backend/routes/users.py`, `backend/routes/drivers/referrals.py` | Removed the `referral_link` key | Dead payload on a dead domain |
| `rider-app/app/referral.tsx`, `driver-app/app/driver/referral.tsx` | Removed the never-dereferenced interface line | Match the response |
| `rider-app/__tests__/referralScreen.test.tsx`, `driver-app/__tests__/app/driverReferralScreen.test.tsx` | Removed the fixture line | Match the response |
| `docs/known-forks.md` | Registered the rider/driver referral pair | It was an undeclared fork that had already drifted (`/r/` vs `/join/`) |
| `rider-app/app.config.ts`, `driver-app/app.config.ts` | Deleted `associatedDomains` + `intentFilters` | Dead config; see §7 |
| `rider-app/app/profile-setup.tsx` | Consent links → `https://www.spinr.ca/legal/{terms,privacy}` | **Live dead links on a consent surface** |
| `rider-app/store-assets/metadata.json`, `driver-app/store-assets/metadata.json` | All three URLs → published pages: `privacy_url` `/legal/privacy`, `support_url` `/help`, `marketing_url` `/` (rider) and `/drive` (driver) | Store submission requires a reachable privacy URL. **Superseded by `53268bf`** — this session shipped `support_url`/`marketing_url` as unverified guesses marked TODO; the repo owner then checked every route against the live site and corrected `support_url` from the invented `/support` to the real `/help`, replacing the TODO with the verified list |
| `backend/utils/rate_limiter.py` | `documentation_url` → the published runbook on GitHub | Key is pinned by two tests; value was dead. **Superseded by `36178cd`** — this session pointed it at `https://spinr.ca/docs/rate-limits`, which is also a page that does not exist; the repo owner repointed it at `docs/runbooks/rate-limits.md` on GitHub, which actually resolves. That is the better fix: it swaps a dead URL for a live one instead of a differently-dead one |
| `backend/.env.example`, `docs/ENVIRONMENT_VARIABLES.md` | `ALLOWED_ORIGINS` examples no longer suggest `spinr.app` | This is how the dead origin could reach a real deployment |
| `.agents/roles/devops-engineer.md` | Production backend → `api-spinr.spinr.ca` | Was `api.spinr.app` — wrong host in an incident-response doc |
| `docs/runbooks/rate-limits.md`, `docs/runbooks/error-responses.md` | curl examples → `api-spinr.spinr.ca` | Copy-pasted repro commands would not resolve |
| `agents/deployer.py` | Removed the fabricated `url` key | Non-production stub emitting a malformed, dead hostname |

## 7. Before / after

Signup consent links (`rider-app/app/profile-setup.tsx`) — the one user-visible fix:

```tsx
// Before — wrong host (no www) and wrong path; both 404
<Text style={styles.link} onPress={() => Linking.openURL('https://spinr.ca/terms')}>Terms</Text>
<Text style={styles.link} onPress={() => Linking.openURL('https://spinr.ca/privacy')}>Privacy Policy</Text>
```

```tsx
// After — the published pages
<Text style={styles.link} onPress={() => Linking.openURL('https://www.spinr.ca/legal/terms')}>Terms</Text>
<Text style={styles.link} onPress={() => Linking.openURL('https://www.spinr.ca/legal/privacy')}>Privacy Policy</Text>
```

WebView host allowlist (`rider-app/app/ride-tracking-webview.tsx`):

```ts
// Before
const ALLOWED_TRACKING_HOSTS = new Set([
  'track.spinr.ca', 'spinr-track.app', 'www.spinr-track.app',
]);
```

```ts
// After
const ALLOWED_TRACKING_HOSTS = new Set(['track.spinr.ca']);
```

## 8. Rollback plan

`git revert` is sufficient and complete for this change set, and this is one of the cases
where that is a real answer rather than a dodge:

- **No migration, no schema change, no `app_settings` write.** Nothing is applied to live data.
- **No money, wallet, ride-state or insurance-period row is touched**, so there is no
  data-level remediation to plan.
- Every change is either a deletion of an unreferenced value or a string correction. The
  only stateful dependency runs the other way: `app_settings.track_base_url` is read by the
  app and was not modified.

Per-item, if one change specifically needs backing out:

| Item | Rollback |
|---|---|
| CORS | Re-add the origins, or set them via the `ALLOWED_ORIGINS` env var — **no redeploy needed**, it is read from env at startup |
| Tracking allowlist | Revert the commit; requires an app build to reach devices (native/JS bundle) |
| Consent links | Revert the commit; requires an app build. An OTA update reaches devices without a store round-trip |
| Everything else | `git revert`, no runtime effect |

## 9. Verification performed

- [ ] **Automated tests run — NO. None could be run.** There is no test runner in this
      environment: `pytest` is not installed, `pip` cannot reach PyPI, no app has
      `node_modules`, and `npm` cannot reach its registry. **Every code change here is
      made by inspection and has NOT been executed.**
- [x] `ruff check` + `ruff format --check` passed on all staged backend files, via the
      pre-commit hook (the hook has a working ruff even though the session's python does not)
- [x] `python3 -m py_compile` clean on every changed Python file: `middleware.py`,
      `users.py`, `drivers/referrals.py`, `utils/rate_limiter.py`, `agents/deployer.py`,
      and `tests/test_p1_cors.py`
- [x] Both store-metadata files re-parsed with `json.loads` after editing
- [x] Blast-radius greps performed and recorded: `referral_link` (6 → 0 occurrences),
      `spinr.app` / `spinr-track.app` / `api.spinr.app` repo-wide, `always_allowed` in
      `backend/tests/`, `_execute_deployment` + `deployment_id` callers,
      `getInitialURL` / `addEventListener('url')` / `useURL` across both apps, route files
      matching every declared deep-link prefix, and every `openURL`/terms/privacy link site
- [x] Reviewed against CLAUDE.md conventions: money paths untouched; no ride-state
      transition touched; no PII added to any log; dual-import pattern untouched
- [x] Reviewer agent (`spinr-security-auditor`) run against the actual diff. Verdict:
      safe to merge, no PII/auth-bypass/money issue introduced, and it independently
      confirmed the two load-bearing claims above (the `trackingUrl`-only deep-link
      path, and that `track_base_url` bypasses the allowlist). It also found four
      real defects, all fixed in a follow-up commit: a comment this work had
      garbled in a security-relevant file, a dangling `§12` cross-reference, a
      missing CORS regression assertion, and `marketing_url` shipped without the
      TODO hedge its two sibling URLs carry. It surfaced `admin.spinr.ca` as a
      separate pre-existing CORS gap — filed as C119(h), deliberately not fixed
      here because adding an origin widens access
- [x] Regression test added: `backend/tests/test_p1_cors.py` now asserts all four
      removed origins stay absent, following the negative-assertion precedent the
      file already used for the dead Vercel preview domain
- [ ] Manual repro in staging — not performed
- [x] Feature flag — deliberately none. Every item is a deletion or correction of an inert
      value, not a user-visible UX change, except the consent-link fix, which replaces a
      broken link with a working one and would be worse to ship dark

## 10. What was NOT verified

State this plainly rather than let the checklist above imply coverage:

- **No test, anywhere, was executed.** The six rider-app/driver-app test files edited here
  have not been run. `rideTrackingWebviewScreen.test.tsx` in particular contains a
  hand-written new test case that has never executed — it must pass a real `jest` run
  before merge.
- **No production build was run** for rider-app or driver-app. CLAUDE.md requires saying so
  explicitly: neither `npm run build` nor any EAS build was attempted, and `app.config.ts`
  was edited in both apps — a config error there surfaces at prebuild, which nothing here
  exercised.
- **The new URLs were never fetched.** The egress proxy in this environment rejects
  `spinr.ca` outright (every probe returns `000`), so `https://www.spinr.ca/legal/privacy`
  and `/legal/terms` are taken on the product owner's word, not confirmed by a request.
- ~~**`support_url` in both store-metadata files is unconfirmed**~~ — **closed by `53268bf`.**
  Originally only its host was corrected, leaving an invented `/support` path marked TODO.
  The repo owner then checked the routes against the live site and set the real `/help`.
- ~~**The new legal URLs were never fetched.**~~ — **partly closed by `53268bf`**, whose
  message records that the website routes were retrieved through web access on 2026-09-14.
  This session still never fetched them (the egress proxy rejects `spinr.ca`); the
  confirmation is the repo owner's, not this session's.
- **Registration status of `spinr.app` was never independently proven.** `whois` and `dig`
  are absent and the proxy blocks RDAP; the NXDOMAIN result proves *not configured*, not
  *not owned*. The "not owned" fact comes from the product owner.
- **No visual-regression coverage.** rider-app and driver-app have no visual tooling at all
  (CLAUDE.md release gate 6), so the consent-link change was reasoned about, not
  screenshotted. No admin-dashboard page was touched, so the seeded Playwright baselines
  are not involved.
- **The deployed `ALLOWED_ORIGINS` env var was not inspected.** The hardcoded list is fixed;
  whether production's env var also carries `https://spinr.app` is unknown from here and
  needs an operator to check.

## 11. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow — the one user-visible change
      (consent links) has its UX effect recorded in §5
- [ ] **Not merge-ready as-is:** the test suites must be run in an environment that has one.
