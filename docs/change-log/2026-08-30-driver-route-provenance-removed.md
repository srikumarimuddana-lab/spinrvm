# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-30 |
| Author | Claude Code (session: rider-textbox-visibility) |
| Surface(s) | driver-app, rider-app |
| Domain (Sentry tag) | drivers |
| PR / commit link | branch `claude/rider-textbox-visibility-d4w9lv` |
| Related issue or gap ID | Owner directive — third and final pass on route-provenance copy |

## 1. Issue / gap identified

`driver-app/app/driver/ride-detail.tsx` was the last remaining app-side caller of
`routeQualityLabel()`, still showing drivers
`Actual route · Route reconstructed · 59% GPS observed · 41% inferred` in a pill
over the map. Owner directive: remove it there too.

While making that change, a **second issue in my own earlier commit** surfaced:
`327d3e8` removed the rider Ride Details caption *including* its imported-ride
branch. That branch was never route-provenance copy — it explains why an
imported ride's map is empty — and removing it was outside the directive.

## 2. Root cause

The provenance label and the imported-ride notice shared one container on both
screens (`routeStatusPill` on driver, `routeQualityText` on rider). Deleting the
container to remove the label took the notice with it.

## 3. Fix / remediation

**driver-app** — removed the `routeLabel` / `routeQuality` / `routeIsProcessing`
/ `routeStatus` chain and the `routeQualityLabel` import. The pill itself is
**kept but narrowed to `{isImported && …}`**: it now renders only the
imported-ride notice, with the fixed `archive-outline` icon (the conditional
icon had no other branch left) and `numberOfLines={2}`, since that sentence no
longer shares the row with a short label. On an ordinary ride the pill does not
render at all.

**rider-app** — restored the imported-ride notice under the map on Ride Details,
along with the `routeQualityText` style, gated `isCompleted && isImported`. No
provenance copy comes back with it.

`routeQualityLabel` now has **zero app-side consumers** across all three apps. It
remains exported from `shared/utils/routeSegments.ts` for
`admin-dashboard/.../ride-detail-modal.tsx` (2 call sites), which is where an
operator acts on it.

## 4. Risk & impact on existing functionality

- **Blast radius: two screens, presentation only.** No API, stored field, or
  backend behaviour. `route_quality` is still served and still consumed by admin.
- **`shared/utils/routeSegments.ts` unmodified** — this is call-site removal, so
  the admin surface cannot regress from it.
- **`show_legacy_badge` consumers unchanged.** Both apps still render their
  separate "Imported" badge from the same flag; this change only affects the
  longer sentence beside the map. The flag-gating rule (never derive from
  `legacy_import_metadata`) is untouched and still pinned by an existing test.
- **Driver pill is now conditional.** On an ordinary ride one absolutely
  positioned view disappears from over the map. Nothing else is laid out
  relative to it (`position: absolute, left/right/bottom: 12`), so no reflow.
- No money, ride-state, dispatch, insurance-period, or PII path touched. No
  migration.

## 5. User-experience effect

- **Driver-facing**, on the completed-ride detail screen only — never mid-ride.
- Ordinary ride: the blue pill over the map is gone entirely; the map fills the
  space.
- Imported ride: unchanged text, now on its own and allowed to wrap to two lines.
- **Rider-facing:** an imported ride's Ride Details screen gets its "no GPS was
  recorded" sentence back under the map — restoring behaviour that shipped
  before `327d3e8` on this same unmerged branch. No rider on any build has seen
  it missing.
- Admin dashboard, corporate: no change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/app/driver/ride-detail.tsx` | Removed the provenance chain + import; narrowed the pill to the imported-ride case | Directive; the notice is a different message |
| `rider-app/app/ride-details.tsx` | Restored the imported-ride notice and its style | `327d3e8` removed it beyond the directive's scope |
| `driver-app/__tests__/screens/ride-detail-route.test.tsx` | Dropped 4 assertions on deleted copy; added a provenance-absent test that pins the imported notice stays | Contract now covers both halves |
| `rider-app/__tests__/ride-details-route.test.tsx` | Dropped the `routeQualityText` absence pin; added an imported-notice pin | The style is legitimately back |
| `rider-app/__tests__/rideDetailsScreen.test.tsx` | Added a render test for the restored notice | Behaviour, not just source strings |

## 7. Before / after

```tsx
// Before — driver: one pill, two very different messages
<View style={styles.routeStatusPill}>
  <Ionicons name={isImported ? 'archive-outline' : hasActualRoute ? … : …} … />
  <Text numberOfLines={1}>
    {isImported ? 'Imported from the previous app — …' : `${routeLabel} · ${routeStatus}`}
  </Text>
</View>

// After — only the message that explains an empty map
{isImported && (
  <View style={styles.routeStatusPill}>
    <Ionicons name="archive-outline" size={14} color="#2563EB" />
    <Text numberOfLines={2}>Imported from the previous app — no GPS was recorded for this ride</Text>
  </View>
)}
```

## 8. Rollback plan

`git revert` is a complete rollback — presentation-only, client-side, no writes,
no migration. Not feature-flagged, same reasoning as the two prior entries.

This commit **partially reverts `327d3e8`** (restoring the rider imported
notice). Reverting this commit alone would remove that notice again; revert the
whole route-provenance series together, or none of it.

## 9. Verification performed

- [x] Blast-radius grep: `routeQualityLabel` repo-wide — zero app-side consumers
      remain, 2 admin call sites survive. `routeLabel` / `routeStatus` /
      `routeQuality` / `routeIsProcessing` in the driver screen — the only
      surviving matches are unrelated *style names*
      (`styles.routeStatusPill/Text`, `styles.routeLabel` for PICKUP/DROPOFF),
      confirmed individually, not by count.
- [x] Confirmed `isImported`, `Ionicons` and `Text` all still have other uses in
      the driver screen, so nothing is orphaned.
- [x] Read both existing driver imported-ride tests and traced them through the
      change: the positive one still finds the sentence; the negative one
      (`queryByText('Imported')` null) is now satisfied because the pill does not
      render at all rather than because it renders different text.
- [x] `tsc --noEmit --noResolve` parse pass clean on both changed screens and all
      three changed test files.
- [ ] **Jest NOT run; no typecheck, lint or production build.** npm and yarn
      registries remain blocked by this session's egress policy (403 on CONNECT).
      All test changes here are committed unexecuted.
- [ ] Manual check on device/simulator — not performed.

## 10. What was NOT verified

- Nothing was executed. Every assertion added or edited is reasoned, not run.
- **The imported-notice call was mine, not the owner's.** The directive said
  "remove it from the driver app as well"; I judged the imported-ride sentence to
  be a different message and kept it — and restored it on the rider side for the
  same reason. If the owner wants *all* text off the map, that is a two-line
  follow-up on both screens. Flagged rather than assumed.
- `numberOfLines={2}` on the driver pill is a guess at what the sentence needs
  now that it no longer shares its row; not measured on a device, and the pill is
  absolutely positioned over the map, so a third line would clip rather than
  reflow.
- No screenshot of either screen. driver-app and rider-app have no
  visual-regression tooling (`CLAUDE.md` release gate #6).
- The rider-side restoration means this branch's *net* effect on
  `ride-details.tsx` is "provenance copy gone, imported notice unchanged" — but
  no single commit in the series shows that; it only reads correctly across
  `327d3e8` + `9458206` + this one.
