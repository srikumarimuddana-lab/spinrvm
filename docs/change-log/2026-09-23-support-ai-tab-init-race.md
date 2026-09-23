# Change Impact & Risk Log — Support AI tab initialization

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-23 |
| Author | Codex |
| Surface(s) | rider-app, driver-app (shared component) |
| Domain (Sentry tag) | ai |
| PR / commit link | PR #5716 follow-up; commit recorded in git history |
| Related issue or gap ID | PR #5716 rider SupportScreen.contact CI failures |

## 1. Issue / gap identified

The shared support contact test suite failed three assertions. Two tests intended to enter AI chat with `initialTab="chat"` instead found FAQ, and a no-email test counted the permanent Contact tab icon as an email action.

## 2. Root cause

`aiMode` used `hidden` as its initial value while `/ai/config` was still pending. The fallback effect interpreted the unresolved state as confirmed-disabled and changed the active tab to FAQ before the enabled response arrived. The chat panel also showed “AI Assistant coming soon” during that pending interval. The icon assertion included the Contact navigation icon, which is rendered regardless of email settings.

## 3. Fix / remediation

Added an explicit loading state. While loading, the chat tab remains out of navigation and an explicitly labeled loading indicator is shown if chat was the requested initial tab. The component redirects to FAQ only after the server confirms `hidden`; rejected configuration follows the existing hidden fallback. The contact test now asserts one navigation icon and checks the absence of the email chip by label. Added delayed enabled, hidden and rejected configuration coverage.

## 4. Risk & impact on existing functionality

- Consumers: `rider-app/app/support.tsx` and `driver-app/app/driver/help.tsx` both render `shared/components/SupportScreen.tsx`.
- State readers: `aiMode` drives the visible AI tab, the coming-soon panel, the loading state, the redirect effect and the chat panel in this component; no other consumers exist.
- Before: pending configuration could bounce a requested chat entry to FAQ and briefly show disabled copy. After: pending has a distinct neutral loading state; only a confirmed hidden result redirects to FAQ.
- Potential regression: initialization of Help with `initialTab="chat"`; enabled state retains the requested tab, while hidden or failed state still falls back to FAQ. No API contract or backend behavior changed.
- Blast radius: isolated shared UI behavior, affecting rider and driver Help surfaces. No backend, data, payment, ride, or safety paths are involved.

## 5. User-experience effect

A rider or driver entering Help with chat selected sees a brief loading indicator while availability is checked. If enabled, chat appears; if disabled or configuration fails, FAQ appears. This is visible at screen entry, not a mid-session behavior change. No customer-facing copy or notification changed.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `shared/components/SupportScreen.tsx` | Distinguishes config-pending from hidden; renders loading indicator and gates fallback/coming-soon states | Prevents premature redirect and misleading disabled copy |
| `shared/components/__tests__/SupportScreen.contact.test.tsx` | Corrects icon assertion and covers pending, enabled, hidden and rejected configuration | Protects state transitions and original settings assertions |
| `docs/change-log/2026-09-23-support-ai-tab-init-race.md` | Records impact, risk and verification | Required change-impact record |

## 7. Before / after

```text
Before: initial aiMode=hidden → initialTab=chat immediately redirected to FAQ;
        after async config, enabled chat could no longer render.
After:  initial aiMode=loading → no premature redirect or coming-soon copy;
        enabled config keeps chat, hidden/rejected config falls back to FAQ.
```

## 8. Rollback plan

Revert the code and test commit(s). This is a UI-only change with no data or configuration changes; no data-level remediation is required.

## 9. Verification performed

- [x] Rider Jest shared SupportScreen contact suite passed after final edits (14/14).
- [x] Driver Jest config with shared root override passed the shared suite after final edits (14/14).
- [x] Rider full CI-style Jest suite (`jest --ci --coverage --forceExit`) passed: 160 suites, 2,211 tests. Jest reported a worker force-exit warning after tests completed.
- [x] Rider and driver Expo exports (`CI=1 expo export --platform all`) completed successfully for web, iOS and Android bundles. These are JS asset exports, not native builds.
- [x] Blast-radius grep confirmed the rider and driver wrappers are the only app consumers.
- [x] Reviewed relevant `CLAUDE.md` guidance; no ride state, money, RLS, or backend changes.
- [ ] Visual regression/manual staging — not run; app surfaces have no active visual regression test for this component.
- [ ] Native builds — not run.

## 10. Sign-off

- [x] Rollback is a code revert; no live data is affected.
- [x] Blast radius is stated: shared SupportScreen in rider and driver Help.
- [x] No silent mid-session change to an active ride or other live workflow.
