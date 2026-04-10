# Accessibility Compliance

## Standard

Spinr targets **WCAG 2.1 Level AA** compliance across all web surfaces, in
accordance with Ontario's Accessibility for Ontarians with Disabilities Act
(AODA) which requires public-facing web services to meet this standard.

## Automated Enforcement

### Static analysis — eslint-plugin-jsx-a11y

`eslint-plugin-jsx-a11y` is configured in `admin-dashboard/eslint.config.mjs`
with WCAG 2.1 AA–aligned rules. It catches common violations at code-review
time before they reach production.

Run: `cd admin-dashboard && npm run lint`

Critical rules enforced as errors:
- `alt-text` — all images must have descriptive alt text
- `label-has-associated-control` — all form inputs must have associated labels
- `aria-props`, `aria-proptypes` — ARIA attributes must be valid
- `role-has-required-aria-props` — ARIA roles must have required attributes
- `html-has-lang` — html element must have a lang attribute

### Runtime scanning — @axe-core/playwright

axe-core runs during every Playwright E2E test run (CI on `main`). It scans
`/login` and `/dashboard/drivers` for WCAG 2.1 AA violations. Critical
violations fail the build.

Run: `cd admin-dashboard && npm run test:e2e`

## Compliance Status (as of 2026-04-09)

| Surface | Tool | Critical Violations | Status |
|---------|------|---------------------|--------|
| Admin dashboard `/login` | axe-core + jsx-a11y | 0 | ✅ |
| Admin dashboard `/dashboard/*` | axe-core (spot check) | 0 critical | ✅ |
| Rider app (mobile) | Manual | Not yet audited | ⚠️ |
| Driver app (mobile) | Manual | Not yet audited | ⚠️ |
| Frontend web | Manual | Not yet audited | ⚠️ |

## Known Warnings (non-critical, tracked)

The following `warn`-level items are noted for future sprints:

- `anchor-is-valid` — some navigation links use `href="#"` placeholders
- `no-autofocus` — login email input uses autofocus (intentional UX choice)
- `interactive-supports-focus` — some custom click handlers on non-interactive elements

## Mobile Accessibility

React Native accessibility is a separate domain from WCAG. Key practices
already applied:
- `accessibilityLabel` props on icon-only buttons
- `accessibilityRole` on custom interactive components

Items for future work:
- Add `accessibilityHint` to ride booking flow inputs
- Verify VoiceOver/TalkBack works end-to-end on the OTP screen
- Test with iOS Accessibility Inspector and Android Accessibility Scanner

## Resources

- [WCAG 2.1 Quick Reference](https://www.w3.org/WAI/WCAG21/quickref/)
- [AODA Web Accessibility Requirements](https://www.ontario.ca/page/how-make-websites-accessible)
- [axe-core rules](https://dequeuniversity.com/rules/axe/)
- [eslint-plugin-jsx-a11y rules](https://github.com/jsx-eslint/eslint-plugin-jsx-a11y)
