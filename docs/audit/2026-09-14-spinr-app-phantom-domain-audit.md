# `spinr.app` — phantom-domain audit and disposition proposal

**Date:** 2026-09-14
**Status:** Decided and implemented — see §12 for what shipped and what is still open.
**Scope:** every reference to `spinr.app` / `spinr-track.app` / `api.spinr.app` in the repo.

---

## 1. The finding

`spinr.app` does not resolve. No A record, no AAAA, no nameservers — `NXDOMAIN`.
`spinr-track.app` does not resolve either. Verified 2026-09-14 from the CI sandbox
via `getent hosts` and `socket.getaddrinfo`.

By contrast `spinr.ca` resolves (`216.198.79.1`), `api-spinr.spinr.ca` resolves to
`spinr-backend-yyz.fly.dev` (the documented Fly.io primary), and `track.spinr.ca`
resolves to Vercel. **`spinr.ca` is the operationally canonical domain.**

Every `spinr.app` URL in the codebase is therefore dead today.

### Updated 2026-09-14, after the decision
The product owner confirmed: **neither `spinr.app` nor `spinr-track.app` is owned.**
They also supplied the real published legal pages — `https://www.spinr.ca/legal/privacy`
and `https://www.spinr.ca/legal/terms`. That corrects §5 below, which was written on
the assumption (inherited from LGL-11) that no public page existed: the legal pages
**do** exist, but nothing in the apps was pointing at them correctly. The signup
consent links were missing both the `www.` host and the `/legal/` path prefix.

### Not proven here
Whether `spinr.app` is **registered but unconfigured** versus **never registered**
could not be determined from this environment: `whois` and `dig` are not installed
and the egress proxy rejects RDAP endpoints (403). `NXDOMAIN` proves *not
configured*; it does not prove *not owned*. Note `.app` is an HSTS-preloaded TLD,
so even a parked page there requires a valid certificate.

The repo's own asset registry points to never-owned:
`docs/runbooks/renewal-calendar.md` rows 47-48 list **`spinr.ca` as the only
registered domain** — there is no row for `spinr.app`.

---

## 2. Inventory and proposed disposition

96 references. They are not one problem; they fall into seven classes with
materially different risk.

| # | Class | Locations | Live impact today | Proposal |
|---|---|---|---|---|
| 1 | Backend `referral_link` | `backend/routes/users.py:1051`, `backend/routes/drivers/referrals.py:174` | **None** — see §3 | **REMOVE** |
| 2 | Rate-limit `documentation_url` | `backend/utils/rate_limiter.py:752` | Dead link in every 429 body | **REPLACE** → `https://spinr.ca/docs/rate-limits` |
| 3 | CORS `always_allowed` | `backend/core/middleware.py:820-821` | Dead allowlist entries | **REMOVE** the two `spinr.app` entries |
| 4 | Operator docs | `docs/ENVIRONMENT_VARIABLES.md:31`, `.agents/roles/devops-engineer.md:79`, `docs/runbooks/rate-limits.md`, `docs/runbooks/error-responses.md` | **Actively misleading** — see §5 | **REPLACE** → `spinr.ca` equivalents |
| 5 | Mobile deep-link config | `rider-app/app.config.ts:55-56,135-138`, `driver-app/app.config.ts:56,213-214` | None (inert) — see §4 | **LEAVE + document** this round |
| 6 | Store metadata | `rider-app/store-assets/metadata.json:7-9`, `driver-app/store-assets/metadata.json:7-9` | **Store submission blocker** | **REPLACE, but only after a page exists** |
| 7 | Test email fixtures (`admin@spinr.app`) | ~55 files under `backend/tests/` | None | **LEAVE** — see §6 |
| — | `agents/deployer.py:78` | non-production stub | None | **LEAVE** — see §7 |

---

## 3. Why the referral links are REMOVE, not replace

This is the sharpest call, and it inverts the obvious answer.

`referral_link` is **dead payload — nothing reads it.** Repo-wide it appears in
exactly six places: the two backend producers, two TypeScript interface
declarations that are never dereferenced, and two test fixtures. There is no
render site, no `Share`, no `Clipboard`, no SMS or email path.

Both share handlers deliberately bypass it, with a comment saying so:

```ts
// rider-app/app/referral.tsx:91-93  (driver-app/app/driver/referral.tsx:110-111 identical)
// No deep link — invitees paste the code on the signup screen, so the
// message shares the CODE and tells them exactly where to enter it.
const message = `Join me on Spinr! Download the app, then paste my code ${info.referral_code} during signup...`;
```

Consequences:
- **No user has ever received a `spinr.app/r/...` link.** There is no
  unrecallable-SMS problem.
- The app tests that mock `https://spinr.ca/r/RIDER123` are not "contradicting the
  backend" — they are fixture values for a field the screen never reads. That is
  also why the backend's `/join/` vs the driver test's `/r/` mismatch went
  unnoticed for so long.

**Why not just repoint it to `spinr.ca`?** Because nothing serves `/r/{code}` or
`/join/{code}` on either domain. Repointing produces a *plausible-looking* URL
that still 404s. The next person wiring up link-sharing sees a correct-looking
link and ships a dead one to real users. That launders a dead link as a live one.

**Why not `app_settings.referral_base_url`** (the `track_base_url` precedent)?
Over-engineering by a wide margin: a migration, a `SettingsUpdate` field, an admin
write-allowlist entry (guarded by `test_admin_settings_write_allowlist_drift.py`),
an admin UI row, a `GET /settings` key, a context provider, and fail-loud handling
in both apps — all for a field zero screens render. `track_base_url` earns its
complexity because the tracking WebView genuinely loads that URL. This does not.

**Deletion is the fail-loud design here.** When someone later builds link sharing,
the field's absence forces them to build the serving side first — the same
principle `ride-tracking-webview.tsx` encodes, for the cost of deleting six lines
instead of shipping a migration.

**Contrast — why #2 (rate-limit URL) gets the opposite call.** Two tests
deliberately defend that key's presence (`test_rate_limit_response_shape.py:140-142`
— *"documentation_url stays present so the error log has a crawlable pointer to the
runbook"* — and `test_promo_rate_limit.py:274`). It is an asserted contract with an
intentional affordance, and nothing auto-follows it: a human hits a 404 and files a
bug. Different facts, different call.

---

## 4. Deep links: inert today, and a partial fix makes it worse

Both apps declare iOS `associatedDomains` and Android `intentFilters` with
`autoVerify: true` for `spinr.app`. **These are decorative — the feature is
non-functional end to end, independent of the domain:**

- **No `.well-known` files exist anywhere in the repo** — no
  `apple-app-site-association`, no `assetlinks.json`, for any host.
- **No inbound link handling in either app** — zero hits for `getInitialURL`,
  `addEventListener('url')`, `useURL`, or linking `prefixes`.
- **No route files match the declared prefixes** — `rider-app/app/` has no
  `ride.tsx`, `promo.tsx` or `join.tsx`; `driver-app/app/` has no `join.tsx`.
  Neither app has a `+not-found` handler.

So even with a working domain *and* a valid association file,
`https://spinr.ca/join/ABC` would open the app into an unmatched route.

**Current user-visible symptom: none.** On iOS the association simply never
registers (a tapped link opens Safari). On Android `autoVerify` fails at install
and the app is not offered as a handler. **Neither blocks install, launch, or Play
review.**

**Changing the host alone is not safe.** Today `spinr.app` is `NXDOMAIN`: inert,
fails fast, nothing cached. Point at `spinr.ca`, which resolves and likely serves
HTML at every path, and Apple's CDN fetches the AASA path, receives
HTML-with-200 instead of JSON, and **negatively caches that for up to ~24h even
after you fix it.** Android's verifier records a hard failure against a live host.
You would trade an inert failure for a sticky one.

**Correct order, if you want this feature:**
1. Serve `https://spinr.ca/.well-known/apple-app-site-association` —
   `Content-Type: application/json`, no file extension, no redirect, unauthenticated.
2. Serve `https://spinr.ca/.well-known/assetlinks.json` with the real SHA-256
   fingerprints from **both** EAS credentials and Play App Signing (two different
   certs; omitting the Play one is the classic failure).
3. Verify both are live and correctly typed.
4. *Then* flip the hosts in `app.config.ts`.
5. Add route files matching the prefixes, plus an inbound link handler.
6. New native build + store submission — config changes never reach installed binaries.

Steps 4-6 are worthless without 1-3 **and** 5. Hence: leave it this round, or
delete the blocks as honest dead config. See open question Q4.

---

## 5. Separate live defect found during this audit

`rider-app/app/profile-setup.tsx:326,328` ships **live, tappable links in the
signup consent flow**:

```tsx
<Text style={styles.link} onPress={() => Linking.openURL('https://spinr.ca/terms')}>Terms</Text>
<Text style={styles.link} onPress={() => Linking.openURL('https://spinr.ca/privacy')}>Privacy Policy</Text>
```

These point at the *correct* domain but almost certainly at pages that do not
exist — per LGL-11 (`reports/audits/2026-07-22-legal-content-validation-v1.md`),
no page in this repo serves any public URL. That is a live-tested surface and a
consent-capture surface, which makes it higher priority than anything in §2.
The proxy in this environment blocks `spinr.ca`, so I could not confirm what it
serves — **this needs a manual check.**

Also note `.agents/roles/devops-engineer.md:79` documents the production backend as
`api.spinr.app`. The real one is `api-spinr.spinr.ca`. That is wrong in a document
an incident responder would reach for under pressure.

---

## 6. Why the test fixtures should NOT be changed

~55 files use `admin@spinr.app`, `a@spinr.app` etc. as fake identities. Changing
these to `@spinr.ca` would point test fixtures at a **real domain the organisation
owns** — strictly worse than the status quo, since fixture addresses can leak into
outbound-mail code paths. If they are ever churned, the correct target is
`@example.com` (RFC 2606 reserved), not `spinr.ca`. Recommend leaving them.

## 7. Why `agents/deployer.py` should NOT be repointed

```python
# agents/deployer.py:70-78 — every method in this class hardcodes success
def _execute_deployment(self, context: Dict) -> Dict[str, Any]:
    target = context.get("target", "backend")
    return {"success": True, ..., "url": f"https://{target}.spinr.app"}
```

`target` is one of `backend` / `rider_app` / `driver_app` / `admin_dashboard`, so
this emits `https://rider_app.spinr.app` — not even a syntactically valid hostname
(underscores are illegal). It is fabricated output from mock code in the
non-production agent framework (`agents/CLAUDE.md`: explicitly not part of the
production runtime). Repointing it to `spinr.ca` would make a fake value *look*
real. If touched at all, drop the `url` key entirely.

---

## 8. How this was missed

Two structural blind spots, not carelessness:

1. **The domain monitor can only see what is already served.**
   `.github/workflows/cert-domain-monitor.yml` sets
   `TLS_HOSTS: "api-spinr.spinr.ca admin-spinr.spinr.ca api.spinr.ca admin.spinr.ca spinr.ca"`
   and `WHOIS_DOMAINS: "spinr.ca"`, explicitly *"Sourced from the Cloudflare DNS
   zone for spinr.ca."* A domain referenced in code but never provisioned is
   invisible to it **by construction**. Same for `renewal-calendar.md`.
   Nothing anywhere lints code-referenced hostnames against the set of domains
   actually owned.

2. **The store-metadata files hide it in plain sight.** Their placeholder
   convention is `"apple_id": "TODO: numeric App Store Connect app ID"`, and the
   file header says *"Fill in the values marked TODO before submitting."*
   `"privacy_url": "https://spinr.app/privacy"` carries no TODO marker — it reads
   as a finished value, so the file's own review convention would never flag it.

3. **One audit hardened an assumption into code.**
   `reports/audits/2026-04-19-rider-app-v1.txt:2316-2317` reasoned
   *"applinks:spinr.app ... meaning spinr.app hosts a web landing page"* — it
   inferred the domain's existence **from the deep-link config**, and remediation
   R-P1-27 then added it to the CORS allowlist. A circular citation: an unverified
   assumption propagated into two more files.

---

## 9. Proposed sequencing

**Code-only, safe to land now (one PR, pending decision):**
1. Remove `referral_link` — both backend producers, both TS interface lines, both
   test fixtures. **Do both sides in one diff:** the rider/driver referral pair is
   an *undeclared* fork — `docs/known-forks.md` has no entry, so the pre-commit
   hook will not warn. Add a registry row.
2. `backend/utils/rate_limiter.py:752` → `spinr.ca`; update the docstring at ~691
   and `docs/runbooks/rate-limits.md:51` to match.
3. `backend/core/middleware.py:820-821` — delete the two `spinr.app` origins.
4. Fix the operator docs in class #4.
5. File `ACTION_ITEMS.md` entries: LGL-11 (confirmed untracked), the
   `profile-setup.tsx` dead consent links, and universal links.
   **Correct LGL-11's target while doing so** — the original audit recommends
   standing up `spinr.app/privacy`, inheriting the same false premise.

**Requires action outside this repo — an agent cannot do these:**
- **E1.** Registrar check: does the business own `spinr.app` / `spinr-track.app`?
- **E2.** Stand up public pages on `spinr.ca`: `/`, `/privacy`, `/terms`, `/support`.
  This unblocks store submission *and* fixes the already-live consent links in §5.
- **E3.** After E2: update both `store-assets/metadata.json` files **and** the real
  values in App Store Connect / Play Console (the JSON is a checked-in record, not
  a submission mechanism).
- **E4.** Universal links per §4, including pulling signing fingerprints from EAS
  and Play Console.

On E2 specifically: `admin-dashboard/src/middleware.ts` + `src/lib/track-host.ts`
already demonstrate host-based routing, so extending that app to serve an apex host
is available — but a **separate one-route deploy is safer**. Adding a public host to
the app that holds the admin surface means any middleware bug exposes admin routes;
`handleTrackingHost()` returns 404 for everything non-track precisely because that
risk was taken seriously.

---

## 10. Risk notes (CLAUDE.md pre-merge gates)

A Change Impact & Risk entry is mandatory for any of this — referral endpoints and
429 responses are live-tested surfaces.

- **CORS is the highest-risk step.** Removing origins can only narrow access, and
  no browser client exists for an `NXDOMAIN` host — but **check the deployed
  `ALLOWED_ORIGINS` env var separately.** If ops followed the
  `ENVIRONMENT_VARIABLES.md` example, production may already carry
  `https://spinr.app` there, and the hardcoded list is not the only source. Verify
  `track.spinr.ca` and `admin-spinr.spinr.ca` still work after deploy.
- **429 body** is a public API contract on every rate-limited response. Both
  guarding tests assert presence, not value, so a value change passes — still note
  it in the impact log.
- **Referral removal** touches money-adjacent functions (`paid_referral_earnings`,
  `_money_str`). Change nothing else inside them.
- **Feature flags:** none needed. These are deletions and corrections of inert
  values, not user-visible UX. The one item that would need flagging — universal
  links — is a native-binary change that cannot be runtime-flagged, which argues
  for doing it once, completely.

---

## 11. Open questions — needed before anything ships

| # | Question | Blocks |
|---|---|---|
| Q1 | Do you own `spinr.app` and/or `spinr-track.app`? Is either intended to be pointed somewhere? | Whether §2 is "remove dead refs" or "provision the domain" |
| Q2 | Were `spinr-track.app/{token}` share links ever actually sent to real users? | Whether the backward-compat entry at `ride-tracking-webview.tsx:18-22` and the two `spinr-track.app` CORS origins still protect anything. If that domain is `NXDOMAIN`, those links are already dead and the compat entry protects nothing |
| Q3 | Is `spinr.ca` apex a Vercel project you control, or a registrar parking page? Does it serve `/terms` and `/privacy` today? | §5 severity, and E2 effort |
| Q4 | Do you want universal links in the next release, or should the `associatedDomains`/`intentFilters` blocks be deleted as honest dead config? | §2 class 5 |

**Hedging principle used throughout:** prefer **deletion over replacement.**
Deleting asserts nothing about either domain; replacing asserts `spinr.ca` serves
something it may not. Where a comment is needed, record the dated observation
("not resolvable as of 2026-09-14"), not an ownership claim.
