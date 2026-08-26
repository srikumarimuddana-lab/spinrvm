# Change Impact & Risk Log — #2816 Batch 7, sub-batch 32: cloud-messaging delivery report

**Issue/gap identified**: `cloud-messaging/page.tsx`'s message-detail dialog used hardcoded `bg-emerald-50 dark:bg-emerald-900/20`/`text-emerald-600` and `bg-red-50 dark:bg-red-900/20`/`text-red-500` for the "Successful" / "Failed" delivery-report mini-stat cards instead of the `--success`/`--destructive` semantic tokens.

**Root cause**: Predates the semantic-token system introduced for #2816.

**Fix/remediation**: Converted the two delivery-report stat cards (Successful → `bg-success/10`/`text-success`, Failed → `bg-destructive/10`/`text-destructive`) — a clean single positive/negative signal pairing, same pattern already used for the adjacent "success rate" progress bar in the same dialog (`bg-success`, untouched, already a token).

Also verified (no edit needed): `audit-logs/page.tsx` — its 35-entry categorical `ACTION_CONFIG` audit-action-type map is already fully wrapped in a documented `eslint-disable`/`eslint-enable` block from an earlier pass; nothing further to convert.

Left untouched in `cloud-messaging/page.tsx` (documented/decorative exclusions, consistent with prior sub-batches): the already-documented categorical `NOTIFICATION_TYPES` (5-type icon-color map) and `STATUS_CONFIG`'s `scheduled` exclusion (both already carry `#2816` `eslint-disable` comments), the brand-accent `border-red-500`/`text-red-600` active-tab underline theme, decorative violet/pink header icons, and the multi-column stat-card array (categorical differentiation, not single signals).

**Risk & impact on existing functionality**: Pure CSS class-name substitution on two `<div>`s inside a read-only detail dialog — no logic, props, or state touched. Blast radius: isolated to this one file; `--success`/`--destructive` are pre-existing tokens already used elsewhere in the same file (e.g. the success-rate progress bar), so no new tokens introduced.

**User experience effect**: Internal-admin-only surface (`/dashboard/cloud-messaging`). Visually equivalent in both themes — same hue family, now theme-aware via the token instead of a hardcoded light/dark pair.

**Files modified**:
| File | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/cloud-messaging/page.tsx` | Delivery-report Successful/Failed stat cards → success/destructive tokens | #2816 |

**Before/after snippet**:
```tsx
// before
<div className="rounded-lg bg-emerald-50 dark:bg-emerald-900/20 p-3 text-center"><p className="text-lg font-bold text-emerald-600">{selectedMessage.successful.toLocaleString()}</p>...</div>
<div className="rounded-lg bg-red-50 dark:bg-red-900/20 p-3 text-center"><p className="text-lg font-bold text-red-500">{selectedMessage.failed_count.toLocaleString()}</p>...</div>
// after
<div className="rounded-lg bg-success/10 p-3 text-center"><p className="text-lg font-bold text-success">{selectedMessage.successful.toLocaleString()}</p>...</div>
<div className="rounded-lg bg-destructive/10 p-3 text-center"><p className="text-lg font-bold text-destructive">{selectedMessage.failed_count.toLocaleString()}</p>...</div>
```

**Rollback plan**: `git revert` — pure class-name change, no data/migration involved.

**Verification performed**:
- `eslint` on the changed file: 0 errors, 26 warnings (all pre-existing residual raw-color warnings on lines this sub-batch did not touch).
- `vitest run`: 339/339 tests pass across all 35 test files.
- `tsc --noEmit`/`npm run build`: this session's environment has a pre-existing, diff-unrelated `GeoJSON`/`@spinr/shared` Turbopack failure (confirmed via `git stash` against unmodified `origin/main` in the prior sub-batch of this same session) — not re-verified per-batch since it was already root-caused as environment-level, not code-level, in sub-batch 31's log.

**What was NOT verified**: No visual regression tooling exists in this repo (standing gap). A production build could not be run in this session due to the pre-existing `@spinr/shared` Turbopack issue.
