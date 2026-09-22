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

Three distinct causes, not one:

1. **Client-side masking** (name/address/website/email, and the phone before it): the component treated shipped constants as defaults for values whose entire purpose is to be operator-editable without a deploy.

2. **A server-side fallback doing the same thing one layer down** — found by the design review of the first attempt at this change, and the reason that attempt would not have worked in production. `GET /api/company-info` returned `""` for four fields but **hardcoded `"Spinr"` for `name`**:

   ```python
   "name": settings.get("company_name", "Spinr") or "Spinr",   # ← the odd one out
   "address": settings.get("company_address", "") or "",
   ```

   So `companyInfo.name` could never be empty at the client, whatever the component did. A blanked-out Company Name was indistinguishable from one deliberately set to "Spinr", and the "omit the block entirely" branch was unreachable outside unit tests that mock the endpoint. **A previous version of this entry asserted the endpoint returned `""` for every unset column. That was wrong** — it was verified for `phone` and generalised without checking `name`.

   `backend/ai/tools_support.py::get_company_info` carried a verbatim copy of the same line, so the in-app AI assistant would also assert an unconfigured company name.

3. **Never settings-backed at all** (the hours line): there is no `company_hours` column, so no amount of correct fallback handling would have fixed it. The app was asserting a staffing commitment nothing in the system could correct.

## 3. Fix / remediation

**Backend (the blocker):** `/company-info` now returns `""` for `name` like its four siblings, and `backend/ai/tools_support.py::get_company_info` is brought back in line with it. Both carry a comment explaining why, so the next person does not "restore" the default. `utils/company_details.py` is deliberately untouched — its `"Spinr"` default feeds email and PDF footers, a different risk class (documents already filed with SGI and airport authorities).

**Client:** **no hardcoded company detail remains in `SupportScreen.tsx`** (verified by grep for `support@spinr`, `spinr.ca`, `SPINR MOBILITY`, `Saskatoon`, `Mon–Fri`, `'Spinr'` — zero hits).

- Both company blocks (FAQ-tab footer and Contact-tab card) now derive from one `companyRows` array that filters out every unset field, with a type-predicate filter so the rendered `text` is `string`, not `string | undefined`.
- `hasCompanyDetails` gates each block on **at least one real detail row**, not on the name. A name with nothing to caption would otherwise render an elevated, padded card holding a single line — which reads as broken rather than minimal (design-review finding).
- The company name renders only when set; no invented title above the rows.

**Sibling screens:** `rider-app/app/(tabs)/account.tsx` and `driver-app/app/driver/(tabs)/profile.tsx` render the same `/company-info` payload and both still carried `companyInfo.name || 'Spinr'`. Both are fixed. This is the sibling-copy failure mode `CLAUDE.md` pre-merge gate 10 names explicitly — a fix proven in one place that never reaches the copies users actually see — and it was flagged by the design review rather than by the original blast-radius grep, which found the files but accepted them as "already correct" on the strength of their phone handling.
- Both contact chips are individually conditional, and the chip row itself is omitted when neither channel is configured.
- The hours line and its now-orphaned `companyHours` style are deleted.
- `askAssistant()` takes its fallback reply as a parameter; the AI-chat failure copy names the configured email and **drops the "or contact …" clause entirely** when none is set, rather than printing a bare address the operator cannot change.

## 4. Risk & impact on existing functionality

**Blast radius: cross-surface — backend endpoint + AI tool + three frontend screens across two apps. No DB, migration, or state-machine change.**

The backend field is the widest part of this change, so its consumers are enumerated in full. `/company-info` is read by exactly three frontends, and nothing else in the repo (grepped):

| Consumer | Effect of `name` now returning `""` |
|---|---|
| `shared/components/SupportScreen.tsx` | The intended fix — the block can finally be omitted when nothing is configured |
| `rider-app/app/(tabs)/account.tsx` | Would have been none (its own `\|\| 'Spinr'` absorbed it) — **also fixed here**, so a blank name now shows nothing |
| `driver-app/app/driver/(tabs)/profile.tsx` | Same as above — **also fixed here** |

- `SupportScreen` importers remain exactly two (`rider-app/app/support.tsx`, `driver-app/app/driver/help.tsx`), both thin wrappers. Not in `docs/known-forks.md`.
- `backend/ai/tools_support.py::get_company_info` is a separate copy of the same handler feeding the in-app AI assistant; changed to match. Its existing test passes `company_name` explicitly, so it was unaffected by the default and keeps passing.
- **No backend test asserted the old `"Spinr"` default** (checked: `test_server_coverage.py` only pins the deprecation header; nothing else touched the payload). So the change breaks no existing expectation — but it also means this endpoint had no payload coverage at all, which the new `test_public_company_info.py` closes.
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
| `backend/routes/settings.py` | `/company-info` `name` now defaults to `""` like its four siblings, with a comment explaining why | The blocker — the client fix was unreachable in production without it |
| `backend/ai/tools_support.py` | Same one-line change in the AI assistant's duplicate `get_company_info` | Verbatim copy of the same handler; the assistant must not assert an unconfigured name either |
| `rider-app/app/(tabs)/account.tsx` | `companyInfo.name \|\| 'Spinr'` → conditional | Sibling copy of the same footer, same bug |
| `driver-app/app/driver/(tabs)/profile.tsx` | `companyInfo.name \|\| 'Spinr'` → conditional | Sibling copy of the same footer, same bug |
| `backend/tests/test_public_company_info.py` | **New.** Pins every field empty when unset (key-absent / `""` / `None`), configured values passing through, and partial configuration not backfilling | This endpoint had zero payload coverage |
| `backend/tests/test_ai_tools_support.py` | Added a case pinning `name == ""` when `company_name` is unset | Keeps the two copies of the handler from drifting again |
| `rider-app/__tests__/accountScreen.test.tsx` | Rewrote the case that pinned the `'Spinr'` name fallback; added one pinning a configured name | The old case asserted removed behavior — and its `toContain('Spinr')` would have passed vacuously via the unrelated "Spinr Rider" hero subtitle |
| `shared/components/__tests__/SupportScreen.contact.test.tsx` | Replaced the two cases that pinned the now-removed email fallback; added pins for the fully-empty state, the removed hours line, name-present/absent, name-only (card omitted), row order, and both AI-chat copy states (driving a real failed send rather than asserting vacuously) | The old cases asserted behavior deliberately removed; leaving them would have failed CI for the wrong reason |

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

## 9b. Design-review pass (CLAUDE.md pre-merge gate 10)

`spinr-design-consistency-reviewer` was run against the diff and returned **FIX BLOCKERS**, not a rubber stamp. Both blockers were real and are fixed above:

1. The `/company-info` `name` fallback, which made the whole client-side fix unreachable in production. This is the finding that mattered — without it the Contact card would have rendered permanently, holding a lone "Spinr" caption, which is worse than what the user reported.
2. The two sibling screens still carrying `|| 'Spinr'`.

Its warnings, also actioned: the lone-name card chrome (now gated on `companyRows.length > 0`), and the unpinned FAQ row-order change (now asserted by a real order test using render-order text extraction, since `getAllByText` answers presence, not sequence).

Left open, deliberately:

- **`SupportScreen.tsx`'s `WELCOME_MESSAGES`** hardcodes "Spinr's AI assistant". Product/persona copy rather than Company Info, and `company_app_name` (which exists in Settings for exactly this kind of body copy) is not wired to this component at all. Out of scope for "company details", but a genuine gap if the product is ever rebranded via that setting.
- **The `/company-info` fetch failure is `console.warn`-only** with no error state, so a transient failure is indistinguishable from "nothing configured". Pre-existing on all three screens. Low severity — supplementary footer content, not the screen's primary async action — but now slightly more consequential, since "renders nothing" is a legitimate state rather than always-visible placeholder text.
- **`backend/routes/support.py`'s `FALLBACK_REPLY` still contains `1-800-SPINR`.** Retired `/support/chat` stub, zero live callers, kept as a reviewed compatibility shim (F04). Unchanged.
- **Company hours have no settings field.** Flagged above as a feature, not a regression.

## 10. What was NOT verified

- **Neither test suite was run — including the new backend tests.** npm *and* PyPI are both blocked in this environment (`registry.npmjs.org` returns 403 via the proxy and directly; `pip install fastapi` fails with "no versions found"), so there is no `node_modules` and no `pytest`/`fastapi`. `jest`, `tsc --noEmit` and `pytest` are all unavailable. **CI must validate everything here before merge.** Highest-risk spots if a detail is wrong:
  - the two new AI-chat tests, which drive a real failed send (`changeText` → press the mocked send icon → rejected POST) rather than asserting vacuously — the heaviest interaction in the file;
  - the row-order test's `textsInRenderOrder` tree walk over `toJSON()`;
  - the type-predicate filter on `companyRows`;
  - `test_public_company_info.py`'s patch target (`backend.routes.settings.get_app_settings`), copied from `test_public_settings.py`'s working pattern but never executed.
- **No production build** was run for either app.
- **Not screenshotted.** rider-app and driver-app have **no visual-regression tooling**, so the fully-empty Contact tab — now the ticket form alone, with no chips and no company card — was reasoned about, not seen. This is the state the current empty settings will actually produce, so it is the one most worth a human eye before merge.
- **Not tested against live Supabase** — the empty-column behavior was confirmed by reading `routes/settings.py`, not by querying a real settings row.
