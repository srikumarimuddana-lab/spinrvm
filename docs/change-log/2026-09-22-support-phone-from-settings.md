# Change Impact & Risk Log — Help screen shows a phone number that isn't configured

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-22 |
| Author | mkkreddy52@gmail.com (via Claude Code) |
| Surface(s) | rider-app, driver-app (both via `shared/components/SupportScreen.tsx`) |
| Domain (Sentry tag) | admin (settings-driven content), surfaced on rider/driver support |
| PR / commit link | branch `claude/busy-hamilton-7idupf` |
| Related issue or gap ID | User bug report: "in the help section I can see the phone number; when in the admin dashboard settings the phone number is empty, if this is empty it should be empty there as well" |

## 1. Issue / gap identified

The Help & Support screen's **Contact** tab displayed a phone number — `1-800-SPINR` — even though admin Settings → Company Info has **Phone** empty. Reported by the user from the live app. `1-800-SPINR` is a vanity placeholder with no line behind it, so the screen invited riders and drivers to dial a number that reaches nobody.

## 2. Root cause

Not a backend or settings-propagation bug — `GET /api/company-info` already returns `""` for an unset phone (`backend/routes/settings.py`: `"phone": settings.get("company_phone", "") or ""`).

The defect was entirely a client-side fallback. `shared/components/SupportScreen.tsx` defined a module-level constant `SUPPORT_PHONE_DISPLAY = '1-800-SPINR'` and used it in two places on the Contact tab:

- the quick-action call chip, which rendered it **unconditionally**, ignoring `/company-info` altogether; and
- the company card's phone row, via `companyInfo.phone || SUPPORT_PHONE_DISPLAY`.

So an empty settings value could never reach the screen — it was masked by the placeholder before it got there.

This Contact tab was the **lone outlier** in the codebase. The same `/company-info` payload is rendered correctly (hidden when empty) in three other places, all using the `!!companyInfo.phone &&` guard: `SupportScreen`'s own FAQ tab, `rider-app/app/(tabs)/account.tsx`, and `driver-app/app/driver/(tabs)/profile.tsx`. The fix aligns the outlier with the convention already in force everywhere else, rather than introducing a new pattern.

## 3. Fix / remediation

Deleted the `SUPPORT_PHONE_DISPLAY` constant. The phone now comes from admin settings only:

- the call chip renders **only** when `/company-info` returns a non-empty phone, and displays that configured value;
- the company card's phone row is omitted entirely when no phone is configured;
- a new `telHref()` helper builds the `tel:` URL from the admin-typed number, stripping spaces/dashes/parentheses and preserving a leading `+` (the old code did `.replace(/-/g, '')` on the vanity string, producing the undialable `tel:1800SPINR`).

The **email** deliberately keeps its `support@spinr.ca` fallback: unlike the phone it is a real, monitored address, it is the backend's own documented default (`_DEFAULT_SUPPORT_EMAIL` in `backend/utils/company_details.py`), and keeping it guarantees the Contact tab is never a dead end with zero contact channels. The chip now also *prefers* a configured `company_email` over that fallback, which it previously ignored — matching what the company card below it already did.

## 4. Risk & impact on existing functionality

**Blast radius: cross-surface but narrow — two apps, one shared component, one tab.**

Grep results for the blast-radius check:

- `SupportScreen` importers: exactly two — `rider-app/app/support.tsx` and `driver-app/app/driver/help.tsx`. Both are thin wrappers passing only `role`/`initialTab`. One fix covers both apps; there is no sibling copy to keep in sync (`SupportScreen` is **not** listed in `docs/known-forks.md`).
- Other `/company-info` consumers: `rider-app/app/(tabs)/account.tsx` and `driver-app/app/driver/(tabs)/profile.tsx`. **Neither is touched** by this diff, and both already hide empty fields — so this change moves the Contact tab toward them, not away.
- Other readers of the `company_phone` setting: `backend/utils/company_details.py` (`build_company_details`) feeds email footers, receipts and invoice PDFs. **Not touched.** It already omits an unconfigured phone from its `contact_line` (it filters falsy parts before joining), so no PDF or email changes.
- `SUPPORT_PHONE_DISPLAY` was module-private and never exported; a repo-wide grep confirms no remaining reference in app code (only the new test's regression assertion).

No backend, DB, ride-state, dispatch, money, auth or background-loop interaction. No table, endpoint, or state field changed — this diff reads an existing endpoint's existing field more faithfully.

Regression risk considered and ruled out: the quick-action chip row cannot render empty, because the email chip has no conditional and always falls back to a real address.

## 5. User-experience effect

- **Who sees it:** riders (Help & Support → Contact) and drivers (Help → Contact). No admin, corporate or internal-admin surface changes.
- **Mid-session visibility:** the Help screen is reachable mid-ride, so yes in principle — but the screen fetches `/company-info` on mount, so the change only lands on a fresh open of Help, never mutating a screen already on-screen.
- **Copy change:** yes, by removal. A phone chip and a card row disappear while `company_phone` is empty. This is the intended, reported-as-desired behavior: the app stops advertising an unstaffed number. Users retain the in-app ticket form (primary CTA, unchanged), the email chip, and the FAQ.
- **The moment ops fills in Settings → Company Info → Phone, the chip and row appear on the next Help open, with no app release.** That is the design intent of the `app_settings`-in-DB pattern.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `shared/components/SupportScreen.tsx` | Removed the `SUPPORT_PHONE_DISPLAY` placeholder; phone chip + card row now conditional on configured value; added `telHref()`; email chip now prefers configured `company_email` | The reported bug — Help advertised an unconfigured, undialable number |
| `shared/components/SupportScreen.tsx` | Added `accessibilityRole="button"` + `accessibilityLabel` to the two contact chips | Follow-up to the accessibility review below: the phone chip is newly-authored code on a WCAG 2.1 AA customer surface, so it ships with an accessible name rather than relying on RN's default nested-`Text` fallback. Purely additive — no visual or behavioral change |
| `shared/components/__tests__/SupportScreen.contact.test.tsx` | **New.** Pins empty-phone → nothing rendered, configured-phone → chip + row + correct `tel:` URL, and email-never-a-dead-end | Regression cover; this shared component had no direct test (both apps' existing tests mock it out entirely) |

## 7. Before / after

```tsx
// Before — the chip ignored /company-info entirely
const SUPPORT_PHONE_DISPLAY = '1-800-SPINR';

<TouchableOpacity
  onPress={() => Linking.openURL(`tel:${SUPPORT_PHONE_DISPLAY.replace(/-/g, '')}`)}
>
  <Text>{SUPPORT_PHONE_DISPLAY}</Text>   {/* always rendered */}
</TouchableOpacity>

// ...and the card masked the empty value
{ icon: 'call-outline', text: companyInfo.phone || SUPPORT_PHONE_DISPLAY },
```

```tsx
// After — settings-driven, absent when unconfigured
const supportPhone = companyInfo.phone;

{!!supportPhone && (
  <TouchableOpacity onPress={() => Linking.openURL(telHref(supportPhone))}>
    <Text>{supportPhone}</Text>
  </TouchableOpacity>
)}

// ...and the card row drops out entirely
...(supportPhone ? [{ icon: 'call-outline', text: supportPhone }] : []),
```

## 8. Rollback plan

**No deploy required, and no data remediation needed** (this diff writes nothing — no Stripe charge, wallet delta, ride state or insurance-period row is involved).

Primary rollback: **set a phone number in admin Settings → Company Info → Phone.** The chip and card row reappear on the next Help-screen open across both apps, within the settings cache TTL, with no app release and no backend deploy. This is the `app_settings`-in-DB rotation pattern working as designed, and it is strictly better than the old behavior because the number shown will be one that actually rings.

Secondary (only if the conditional rendering itself misbehaves): `git revert` the commit. Acceptable here because the change is presentational and stateless — reverting restores the previous render path exactly, with nothing persisted to unwind.

Not feature-flagged — see §9 for the justification.

## 9. Verification performed

- [x] **Blast-radius grep performed.** Searched: `support_phone|supportPhone|SUPPORT_PHONE`, `company-info|company_info|company_phone`, `1-800-SPINR|SUPPORT_PHONE_DISPLAY`, and all `SupportScreen` importers across `rider-app`, `driver-app`, `shared`, `admin-dashboard`, plus `docs/known-forks.md`. Results enumerated in §4.
- [x] **Backend contract confirmed by reading source** — `backend/routes/settings.py::get_company_info` returns `""` for an unset phone, so the client guard is checking a value the server genuinely sends. `backend/utils/company_details.py` confirmed unaffected.
- [x] **Reviewed against `CLAUDE.md` conventions** — no money/Decimal, ride-state, RLS, or PIPEDA surface touched. PIPEDA note: this change *reduces* displayed contact data; the phone here is company-owned, not user PII, and nothing new is logged.
- [x] **Regression test written** (`shared/components/__tests__/SupportScreen.contact.test.tsx`, 6 cases). It is placed under `shared/components/__tests__/` deliberately: `rider-app/jest.config.js` sets `roots: ['<rootDir>', '<rootDir>/../shared']`, so CI's existing rider-app test step collects it with no new CI job.
- [ ] **Automated tests NOT executed in this environment** — see "What was NOT verified" below. This is the significant caveat on this entry.
- [ ] Manual repro in staging — not performed (no staging access from this session).
- [ ] Feature-flagged — **no, deliberately.** Justification: the change is presentational, reversible from the admin dashboard with no deploy (§8), and gating it would mean shipping a flag whose "off" state keeps a number that dials nowhere in front of live users. The `app_settings` Phone field is itself the control surface here.

## 9b. Reviewer pass (CLAUDE.md pre-merge gate 10)

`spinr-accessibility-reviewer` was run against the actual diff. **Verdict: no blockers introduced.** It confirmed the conditional render is the correct removal pattern — when `supportPhone` is falsy the subtree never mounts, so no ghost/blank node is left in the accessibility tree, and focus order (submit → phone chip if present → email chip → company card) still matches visual order.

Acted on from that review:

- Added `accessibilityRole="button"` and a descriptive `accessibilityLabel` to both contact chips, and extended the test to assert the empty-phone case leaves nothing behind in the accessibility tree (`queryByLabelText(/^Call support/)` is null) — a screen reader must not find a call affordance sighted users can't see.

Accepted and deliberately **not** actioned here, to keep the diff surgical (each is pre-existing and untouched by this fix):

- `contactChip` (`paddingVertical: 8` around 13–14px content) likely renders under the ~44×44pt touch-target guideline, with no `hitSlop`. Fixing it changes layout on two apps that have **no visual-regression tooling**, so it belongs in its own reviewed change, not smuggled into a bug fix.
- The header back button (`SupportScreen.tsx` ~line 364) is icon-only with no accessible-name fallback at all — a harder gap than the chips, and entirely outside this diff.
- `supportPhone`/`supportEmail` populate after an async `/company-info` fetch with no live-region announcement when the chip appears. Low severity, and it is the same pre-existing pattern the company card already used; this diff extends that pattern consistently rather than introducing an inconsistency.

Product note raised by the review, not a code defect: while `company_phone` is empty, every remaining channel is text-based (email chip, ticket form, AI chat). For users who rely on voice over typing, that is a real reduction in channel modality — an argument for ops configuring a real number in Settings, which is exactly what this change makes visible instead of masking.

## 10. What was NOT verified

Stated explicitly rather than left to silence:

- **The new Jest test was never run.** This session's environment cannot reach the npm registry — `registry.npmjs.org` returns HTTP 403 both through the agent proxy and on a direct connection, and the local npm cache (126 MB) lacks even `@babel/core`, so `npm install` fails with `E403`/`ENOTCACHED` and no `node_modules` exists in any surface. The test is written against the conventions of the neighbouring suites (`rider-app/__tests__/accountScreen.test.tsx` for the mock/render shape, `shared/components/__tests__/Card.test.tsx` for placement) but **it must be run by a human or by CI before merge.** Treat the 6 assertions as unproven until then.
- **`tsc --noEmit` was not run**, for the same reason. The typing decision worth a reviewer's eye: `supportPhone` is hoisted into a local `const` specifically so TypeScript's narrowing survives into the `onPress` closure (property-access narrowing does not), which is why the diff has no non-null assertion.
- **No production build was run** (`npm run build` or EAS) for either app — same blocker.
- **Not screenshotted.** Per `CLAUDE.md` pre-merge gate 6: **rider-app and driver-app have no visual-regression tooling at all**, so the layout consequence of a chip row dropping from two chips to one, and a company card dropping from four rows to three, was **reasoned about, not visually verified**. (The admin-dashboard Playwright baselines are irrelevant here — no admin-dashboard file is touched by this diff.)
- **Not tested against live Supabase** — the `/company-info` behavior for an empty column was confirmed by reading `routes/settings.py`, not by querying a real settings row.
