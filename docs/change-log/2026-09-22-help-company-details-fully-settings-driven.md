# Change Impact & Risk Log — every Help-screen company detail comes from admin Settings

Follow-up to `2026-09-22-support-phone-from-settings.md`, which fixed only the phone. User direction after seeing that fix: *"all the details should come from the admin dashboard settings and remove the time 9am to 6 pm and mon to fridays as well."*

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-22 |
| Author | mkkreddy52@gmail.com (via Claude Code) |
| Surface(s) | rider-app, driver-app (both via `shared/components/SupportScreen.tsx`) |
| Domain (Sentry tag) | admin (settings-driven content), surfaced on rider/driver support |
| PR / commit link | branch `claude/busy-hamilton-7idupf` |
| Related issue or gap ID | Direct user instruction; also closes warnings 1 and 2 from the `spinr-design-consistency-reviewer` pass recorded in the previous entry |

## 1. Issue / gap identified

The phone fix removed one hardcoded placeholder but left the rest of the same card asserting details the operator never entered:

- company name → `'SPINR MOBILITY INC.'` (Contact card) and `'Spinr'` (FAQ footer)
- address → `'Saskatoon, SK, Canada'`
- website → `'www.spinr.ca'`
- email → `'support@spinr.ca'` (the `SUPPORT_EMAIL` constant, and two more hardcoded copies inside the AI-chat failure messages)
- **support hours → `'Mon–Fri 9am–6pm CST'`, which is not an admin-configurable setting at all** — there is no `company_hours` column, so the app was asserting a staffing commitment nothing in the system could ever correct.

The design review of the previous commit independently flagged the address/website/name cases as "the identical failure class as the phone bug just fixed."

## 2. Root cause

Same root cause as the phone, applied field by field: the component treated shipped constants as defaults for values whose entire purpose is to be operator-editable without a deploy. `GET /api/company-info` returns `""` for every unset column — the client masked all of them.

The hours line has a slightly different root cause worth separating: it was never settings-backed in the first place. No amount of correct fallback handling would have fixed it, because there is no field to read.

## 3. Fix / remediation

**No hardcoded company detail remains in `SupportScreen.tsx`** (verified by grep for `support@spinr`, `spinr.ca`, `SPINR MOBILITY`, `Saskatoon`, `Mon–Fri`, `'Spinr'` — zero hits).

- Both company blocks (FAQ-tab footer and Contact-tab card) now derive from one `companyRows` array that filters out every unset field, with a type-predicate filter so the rendered `text` is `string`, not `string | undefined`.
- `hasCompanyDetails` gates each block: nothing configured → the block is omitted entirely rather than rendered empty.
- The company name renders only when set; no invented title above the rows.
- Both contact chips are individually conditional, and the chip row itself is omitted when neither channel is configured.
- The hours line and its now-orphaned `companyHours` style are deleted.
- `askAssistant()` takes its fallback reply as a parameter; the AI-chat failure copy names the configured email and **drops the "or contact …" clause entirely** when none is set, rather than printing a bare address the operator cannot change.

## 4. Risk & impact on existing functionality

**Blast radius: cross-surface but contained — two apps, one shared component, no backend/DB/state change.**

- `SupportScreen` importers remain exactly two (`rider-app/app/support.tsx`, `driver-app/app/driver/help.tsx`), both thin wrappers. Not in `docs/known-forks.md`.
- `rider-app/app/(tabs)/account.tsx` and `driver-app/app/driver/(tabs)/profile.tsx` render the same `/company-info` payload and are **not touched**. They already hide unset fields, but both still use `companyInfo.name || 'Spinr'` for the heading — see §9b open items.
- `backend/utils/company_details.py` (email footers, receipts, invoice PDFs) is **not touched** and keeps its own documented fallbacks. Deliberate: a PDF already filed with SGI or an airport authority is a different risk class than an in-app screen, per the 2026-08-08 change log's reasoning.
- The 2026-09-15 decision (`ai-chat-preserve-official-contact`) allowlists `company_email`/`company_phone` through `scrub_pii` so the assistant can quote them. This change is aligned with it, not against it: both treat admin Settings as the source of truth for official contact. That decision governs the **backend** reply path; the two strings changed here are **client-side** fallbacks used only when the backend returns nothing or the request fails.

**Behavior changes that are not pure removal — call these out for review:**

1. **FAQ-footer row order changed** from address → phone → email → website to address → **email** → phone → website, because both blocks now share one array. Cosmetic; accepted in exchange for the two blocks no longer being able to drift apart.
2. **The FAQ footer now renders for a name-only configuration.** Previously its condition excluded `name`, so a company with only a name set showed nothing; now it shows the name. More correct, and consistent with the Contact card.
3. **AI-chat failure copy is shorter when no email is configured** ("Please try again." instead of "Please try again or contact support@spinr.ca."). The chat tab's own disclaimer already routes account-specific issues to the Contact tab, and the ticket form there is unconditional.

## 5. User-experience effect

- **Who sees it:** riders and drivers, on Help → FAQ footer, Contact tab, and AI-chat failure messages. No admin/corporate surface changes.
- **Mid-session visibility:** the screen fetches on mount, so changes land on a fresh open of Help, never mutating a screen already on-screen.
- **With the current (empty) settings**, the Contact tab shows the heading, the "How can we help you today?" ticket form and its Submit Report button — and nothing else. That is the intended outcome of the instruction: the app stops asserting an entity name, address, website, phone and staffed hours that nobody configured.
- **Contact is still not a dead end.** The ticket form is unconditional and independent of settings; it was always the primary CTA on this tab.
- **Filling in Settings → Company Info restores each row independently**, with no app release.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `shared/components/SupportScreen.tsx` | Removed `SUPPORT_EMAIL` and every hardcoded name/address/website/email fallback; unified both company blocks onto a filtered `companyRows` + `hasCompanyDetails`; chips individually conditional; `askAssistant` takes a `fallbackReply`; chat copy drops the contact clause when unset | User instruction: all details from admin settings |
| `shared/components/SupportScreen.tsx` | Deleted the `Mon–Fri 9am–6pm CST` line and the `companyHours` style | User instruction; and it was never a configurable setting, so the app should not assert it |
| `shared/components/__tests__/SupportScreen.contact.test.tsx` | Replaced the two cases that pinned the now-removed email fallback; added pins for the fully-empty state, the removed hours line, name-present/absent, and both AI-chat copy states (driving a real failed send rather than asserting vacuously) | The old cases asserted behavior deliberately removed; leaving them would have failed CI for the wrong reason |

## 7. Before / after

```tsx
// Before — five separate hardcoded assertions, plus unconfigurable hours
<Text style={styles.companyTitle}>{companyInfo.name || 'SPINR MOBILITY INC.'}</Text>
{ icon: 'location-outline', text: companyInfo.address || 'Saskatoon, SK, Canada' },
{ icon: 'mail-outline',     text: companyInfo.email   || SUPPORT_EMAIL },
{ icon: 'globe-outline',    text: companyInfo.website || 'www.spinr.ca' },
<Text style={styles.companyHours}>Mon–Fri 9am–6pm CST</Text>
```

```tsx
// After — one filtered source of truth, block omitted when empty
const companyRows = [
  { icon: 'location-outline' as IoniconName, text: companyInfo.address },
  { icon: 'mail-outline'     as IoniconName, text: supportEmail },
  { icon: 'call-outline'     as IoniconName, text: supportPhone },
  { icon: 'globe-outline'    as IoniconName, text: companyInfo.website },
].filter((row): row is { icon: IoniconName; text: string } => !!row.text);

const hasCompanyDetails = !!companyInfo.name || companyRows.length > 0;
```

```tsx
// Before / after — AI chat failure copy
"I'm having trouble connecting right now. Please try again or contact support@spinr.ca."
`I'm having trouble connecting right now. Please try again${contactSuffix}.`   // clause drops when unset
```

## 8. Rollback plan

**No deploy required, and no data remediation needed** — this diff writes nothing; no Stripe charge, wallet delta, ride state or insurance-period row is involved.

Primary rollback: **populate the fields in admin Settings → Company Info.** Every removed line reappears the moment its field has a value, across both apps, with no release. This is strictly better than the previous state, because what displays will be what the operator actually entered.

The one thing settings cannot restore is the **business-hours line** — there is no `company_hours` column. If those hours must appear in-app again, that is a new feature (settings column + migration + admin field + render), not a rollback. Ops can state hours in the FAQ content today, which is already admin-editable, with no code change.

Secondary: `git revert` the commit. Safe here because the change is presentational and stateless.

Not feature-flagged — the change is reversible from the dashboard with no deploy, and gating it would mean shipping a flag whose "off" state keeps asserting an address, phone and staffed hours nobody configured.

## 9. Verification performed

- [x] **Blast-radius grep** — `SUPPORT_EMAIL`, `support@spinr`, `spinr.ca`, `SPINR MOBILITY`, `Saskatoon`, `Mon–Fri`, `companyHours`, `'Spinr'` across the file (zero remaining); `SupportScreen` importers; other `/company-info` consumers; `docs/known-forks.md`.
- [x] **Checked against the 2026-09-15 `ai-chat-preserve-official-contact` decision** before touching the chat strings — confirmed aligned, and that the strings changed here are client-side fallbacks outside that decision's backend scope.
- [x] **Reviewed against `CLAUDE.md` conventions** — no money/Decimal, ride-state, RLS or background-loop surface. PIPEDA: this reduces displayed data; nothing new is logged.
- [x] **Tests updated rather than deleted** — the two cases pinning the old email fallback were rewritten to pin the new rule, not removed.
- [ ] **Automated tests NOT executed** — same environment blocker as the previous entry; see §10.
- [ ] Manual repro in staging — not performed.
- [ ] Feature-flagged — no, justified above.

## 9b. Open items deliberately left

- **`account.tsx` / `profile.tsx` still use `companyInfo.name || 'Spinr'`.** Same class, on two screens outside this fix's scope. Lower-stakes than the Contact card's all-caps legal-entity claim, since "Spinr" is the app's own brand name rather than a registered-entity or contact assertion. Worth a follow-up sweep if the rule is to hold everywhere.
- **`backend/routes/support.py`'s `FALLBACK_REPLY` still contains `1-800-SPINR`.** Retired `/support/chat` stub, zero live callers, kept as a reviewed compatibility shim (F04). Unchanged.
- **Company hours have no settings field.** Flagged above as a feature, not a regression.

## 10. What was NOT verified

- **The Jest suite was never run.** This environment cannot reach npm (`registry.npmjs.org` returns 403 via the proxy and directly; the local cache lacks `@babel/core`), so no `node_modules` exists and neither `jest` nor `tsc --noEmit` can run. **CI must validate before merge.** Two areas carry the most risk if a detail is wrong: the type-predicate filter on `companyRows`, and the two new AI-chat tests, which drive a real failed send (`changeText` → press the mocked send icon → rejected POST) rather than asserting vacuously — a heavier interaction than the other cases and the most likely to need adjustment.
- **No production build** was run for either app.
- **Not screenshotted.** rider-app and driver-app have **no visual-regression tooling**, so the fully-empty Contact tab — now the ticket form alone, with no chips and no company card — was reasoned about, not seen. This is the state the current empty settings will actually produce, so it is the one most worth a human eye before merge.
- **Not tested against live Supabase** — the empty-column behavior was confirmed by reading `routes/settings.py`, not by querying a real settings row.
