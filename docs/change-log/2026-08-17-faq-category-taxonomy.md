# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-17 |
| Author | Content/UX review (Claude Code) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | see PR on branch `claude/spinr-faq-review-uodytp` |
| Related issue or gap ID | FAQ content audit, finding "High — the category list the admin UI offers doesn't match the categories that exist" (session artifact) |

## 1. Issue / gap identified

The dedicated FAQ admin page's category dropdown (`admin-dashboard/src/app/dashboard/faqs/page.tsx`) offered `general, payments, safety, account, rides, drivers, other`, but the actual seeded FAQ content uses `onboarding, documents, troubleshooting, payments, wallet, pricing, promotions, rides, account, safety, accessibility` — only 4 of the 11 real categories were selectable. Editing any onboarding/documents/troubleshooting/wallet/pricing/promotions/accessibility FAQ through that page had no matching dropdown option for its actual category. Separately, the condensed Support-tab FAQ editor (`admin-dashboard/src/app/dashboard/support/_tabs/faqs.tsx`) used a free-text `Input` for category instead of any fixed list, so an admin using that screen could introduce typo variants (`"Payment"` vs `"payments"`) that fragment the taxonomy further.

## 2. Root cause

The two admin screens were built independently against the same `faqs` table (documented in-code as an accepted duplication, see the banner comment in `faqs.tsx`) but never shared a category list — the dedicated page's dropdown was authored before (or without reference to) the seed migrations that later populated the real category values, and the Support-tab editor never had a fixed list at all.

## 3. Fix / remediation

- Added `admin-dashboard/src/lib/faq-categories.ts` exporting one canonical `FAQ_CATEGORIES` list — the 11 categories the seed data actually uses, plus `general` kept as the explicit default/catch-all (matches the backend's `FaqCreateRequest.category` default and both editors' empty-form state). `drivers` and `other` were dropped: `drivers` described an audience, not a topic (that's what the separate `audience` field is for), and neither value was ever used by an actual FAQ row.
- `faqs/page.tsx`: `CATEGORY_OPTIONS` now imports `FAQ_CATEGORIES` instead of its own local, stale array.
- `support/_tabs/faqs.tsx`: replaced the free-text category `Input` with a `Select` populated from the same `FAQ_CATEGORIES` list, matching the dropdown UX the dedicated page already uses.
- No backend change: `backend/routes/admin/faqs.py`'s `category` field was never validated against an enum (plain `str`), so no schema/API change was needed — this is purely a frontend dropdown-content fix.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to the two FAQ admin editors.** Grepped `admin-dashboard/src` for other consumers of the old `CATEGORY_OPTIONS` array or the `drivers`/`other` category values — the only other hit is `support/_tabs/complaints.tsx`, an unrelated complaints-category field on a different table, untouched by this change.
- **Existing FAQ rows are unaffected.** This changes what an admin can *select* going forward; it does not touch stored data. All 51 (well, 50 post-consolidation) seeded FAQ rows already use categories that are in the new canonical list, so every existing row now has a matching dropdown option — improvement, not disruption.
- **Edge case, not a regression**: if an admin previously saved a category through the Support tab's free-text field that isn't in `FAQ_CATEGORIES` (can't be verified — no DB access in this session), opening that row's edit dialog now shows a `Select` with no option matching the stored value, which Radix renders as an empty-looking trigger rather than erroring. The underlying `form.category` state still holds the original stored string until the admin explicitly changes it, so saving without touching that field does not silently overwrite or lose the original category — but the admin would need to explicitly re-pick a category from the new list to change anything else on that row without also normalizing its category. Flagging as a known limitation rather than something this fix could resolve without live DB access.
- No change to the public `/faqs` API, `search_faqs`, or either mobile app — category is not used for audience/visibility filtering, only for the admin table's grouping/badge display and (client-side, in `driver-app/app/driver/faq.tsx`) accordion section titles.

## 5. User-experience effect

- **Internal-admin facing only.** Riders and drivers see no change — category isn't shown to them as a raw value in either app's UI beyond being title-cased into an accordion section heading.
- Not visible mid-session to anyone already using the rider/driver app.
- No copy/notification change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/lib/faq-categories.ts` | New file: single canonical `FAQ_CATEGORIES` list | Give both FAQ editors one shared source of truth instead of two independently-drifting lists |
| `admin-dashboard/src/app/dashboard/faqs/page.tsx` | `CATEGORY_OPTIONS` now imports `FAQ_CATEGORIES` instead of a local, stale array | Dropdown now matches the categories real FAQ content actually uses |
| `admin-dashboard/src/app/dashboard/support/_tabs/faqs.tsx` | Category field changed from free-text `Input` to `Select` populated from `FAQ_CATEGORIES` | Removes the typo/free-text drift risk on the one screen that had no fixed list at all |

## 7. Before / after

```
# Before — admin-dashboard/src/app/dashboard/faqs/page.tsx
const CATEGORY_OPTIONS = ["general", "payments", "safety", "account", "rides", "drivers", "other"];
// 4 of 11 real seeded categories selectable; "drivers"/"other" unused by any row

# Before — admin-dashboard/src/app/dashboard/support/_tabs/faqs.tsx
<Label className="text-xs">Category</Label>
<Input placeholder="general" value={form.category}
  onChange={(e) => setForm({ ...form, category: e.target.value })} />
// free text — no list at all, typo-prone
```

```
# After — both files import the same list
import { FAQ_CATEGORIES } from "@/lib/faq-categories";
// onboarding, documents, troubleshooting, payments, wallet, pricing,
// promotions, rides, account, safety, accessibility, general

# admin-dashboard/src/app/dashboard/faqs/page.tsx
const CATEGORY_OPTIONS: readonly string[] = FAQ_CATEGORIES;

# admin-dashboard/src/app/dashboard/support/_tabs/faqs.tsx
<Label className="text-xs">Category</Label>
<Select value={form.category} onValueChange={(v) => setForm({ ...form, category: v })}>
  <SelectTrigger><SelectValue /></SelectTrigger>
  <SelectContent>
    {FAQ_CATEGORIES.map((c) => (<SelectItem key={c} value={c} className="capitalize">{c}</SelectItem>))}
  </SelectContent>
</Select>
```

## 8. Rollback plan

`git-revert-safe` — pure frontend dropdown-content change, no schema, no data mutation, no API contract change. Reverting the three files restores the prior (mismatched) dropdown/free-text behavior exactly; no data-level cleanup needed since no stored data was touched.

## 9. Verification performed

- [x] **Real production build run**: `npm run build` in `admin-dashboard/` completed with no errors (checked explicitly — not just a dev server or `tsc --noEmit`), and both `/dashboard/faqs` and `/dashboard/support` routes are present in the build output.
- [x] Blast-radius grep performed: searched `admin-dashboard/src` for other consumers of the old `CATEGORY_OPTIONS` list and for the removed `drivers`/`other` category values — only an unrelated field on the complaints tab matched, untouched by this change.
- [x] Reviewed against relevant CLAUDE.md conventions: additive change (new shared constants file), no schema/RLS/money path touched.
- [ ] Manual repro / staging check — not performed; would require opening both `/dashboard/faqs` and the Support tab's FAQs sub-tab against a real backend and confirming the dropdown now includes every category the seeded rows use, and that editing an existing FAQ pre-selects the correct category.
- [ ] Unit/integration tests — not applicable; no test file exists for either FAQ admin screen (frontend, no test harness in this repo for admin-dashboard component behavior beyond build/lint).

**What was NOT verified**: whether any FAQ row currently in the live/staging database has a category value outside the new canonical list (e.g. saved as free text through the Support tab before this fix, or one of the removed `drivers`/`other` values) — no DB access in this session. If such rows exist, their edit dialog will show an unmatched (blank-looking) category selector until an admin explicitly re-picks a value for that field; this doesn't lose or corrupt the underlying data, but is worth a one-time query (`SELECT DISTINCT category FROM faqs`) before or shortly after this ships, to confirm real coverage. No automated visual-regression tooling exists in this repo for admin-dashboard, so the Select's rendering (vs. the old Input) was reasoned about from the component code and confirmed only by production build success, not screenshotted.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no data involved)
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow: this is a strict widening of admin-selectable options (4 → 12) that doesn't remove any category a real row uses; the User-experience effect field is filled in above
