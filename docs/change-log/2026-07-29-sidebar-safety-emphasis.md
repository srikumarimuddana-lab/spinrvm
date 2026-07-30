# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-29 |
| Author | Claude Code |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (this branch) |
| Related issue or gap ID | Follow-up from the earlier IA recommendation memo |

## 1. Issue / gap identified

The Safety nav item (SOS, insurance-period audit trail — the one P0-severity destination in the Support sidebar group) rendered with the same flat, muted-grey icon as FAQs and Disputes, undersell­ing what it's for.

## 2. Root cause

Not a bug — cosmetic gap flagged in the earlier IA recommendation memo, not yet acted on.

## 3. Fix / remediation

Added an `emphasize?: boolean` flag to `NavItem`; when set (only on the Safety entry), its icon renders in amber instead of the shared muted grey whenever that item isn't the active route (the active-route highlight already makes it distinct once selected).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated** — one new optional prop, used by exactly one nav entry. No other item sets `emphasize`, so every other row's rendering is byte-for-byte unchanged.
- Purely a Tailwind class addition — no logic, no data, no permission change.

## 5. User-experience effect

- Internal admin only. Safety's icon is now visually distinct (amber) in the sidebar at rest, instead of blending into the same weight as every other Support-group item.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/components/sidebar.tsx` | New `emphasize` NavItem flag; Safety sets it; icon renders amber when set and not active | Visual distinction for the one P0 item in its group |

## 7. Before / after

```tsx
// Before
{ href: "/dashboard/safety", label: "Safety", icon: ShieldAlert, module: "support" },

// After
{ href: "/dashboard/safety", label: "Safety", icon: ShieldAlert, module: "support", emphasize: true },
```

## 8. Rollback plan

Plain `git revert` — presentation-only change.

## 9. Verification performed

- [x] Real production build (`npm run build`) — succeeded.
- [ ] Not visually verified in a browser in this session (no browser available) — reasoned about via the Tailwind class logic, not screenshotted.

## 10. What was NOT verified / deferred

- No other sidebar item was reconsidered for `emphasize` — deliberately scoped to just Safety, the one item explicitly flagged.
