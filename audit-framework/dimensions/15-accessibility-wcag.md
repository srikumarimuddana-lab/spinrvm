# Dimension 15 — Accessibility (WCAG 2.1 / AODA)

**Question:** Can a driver with a visual, motor, or cognitive disability use this app?

This dimension is required for Canadian apps (AODA — Accessibility for Ontarians with Disabilities Act) and is reviewed during App Store submission.

---

## Checklist

### Screen Reader (VoiceOver / TalkBack)
- [ ] All interactive elements have `accessibilityLabel` — not "Button" or empty
- [ ] All images have `accessibilityLabel` or `accessibilityElementsHidden={true}` if decorative
- [ ] `accessibilityRole` set on interactive elements: `button`, `link`, `text`, `image`, `header`
- [ ] `accessibilityHint` explains what happens when element is activated (e.g. "Starts your shift")
- [ ] Focus order is logical — matches visual reading order
- [ ] No information conveyed by colour alone (also text/icon label)
- [ ] SOS button: role="button", label="Emergency SOS", hint="Hold for 1.2 seconds to send emergency alert"

### Dynamic Text Scaling
- [ ] `allowFontScaling` set appropriately — large text sizes don't clip or overlap
- [ ] Minimum font size 11pt — nothing unreadable at default size
- [ ] Layout adjusts when system font is at 200% (common for low-vision users)
- [ ] Text containers use `flexWrap` or `numberOfLines` with `ellipsizeMode` — not clipped

### Colour & Contrast
- [ ] Text contrast ratio ≥ 4.5:1 against background (WCAG AA)
- [ ] Large text (≥ 18pt or 14pt bold): contrast ≥ 3:1
- [ ] Interactive elements: contrast ratio ≥ 3:1 against adjacent colours
- [ ] Error states shown in text — not just red colour
- [ ] Check both light and dark modes

### Motor Accessibility
- [ ] Touch targets ≥ 44×44pt (iOS HIG) / 48×48dp (Material)
- [ ] `hitSlop` applied to small elements
- [ ] No double-tap required where single tap works
- [ ] Swipe-to-dismiss / gesture alternatives available via button fallback
- [ ] Long-press actions (e.g. SOS) have a discoverable alternative

### Cognitive Accessibility
- [ ] Error messages explain what went wrong and how to fix it
- [ ] Loading states are explicit — spinner + label ("Finding your driver…")
- [ ] Destructive actions (cancel ride) have a confirmation step
- [ ] Consistent navigation patterns — same action in same place across screens
- [ ] No time limits that surprise the user (or extend-able timeouts)

### iOS Specific
- [ ] `accessibilityViewIsModal={true}` on modal sheets to trap focus
- [ ] `accessibilityLiveRegion` on dynamically updating content
- [ ] Support for Switch Control

### Android Specific
- [ ] `importantForAccessibility` set on non-interactive containers
- [ ] TalkBack focus not trapped in non-modal views

---

## Severity Guide

| Finding | Severity |
|---|---|
| Critical action (SOS, accept ride) has no accessibility label | HIGH |
| Text contrast ratio < 3:1 — unreadable for low-vision | HIGH |
| Screen reader cannot operate any core flow | HIGH |
| Focus order illogical — VoiceOver jumps randomly | MEDIUM |
| Error shown in colour only — not announced to screen reader | MEDIUM |
| Touch target < 20×20pt | HIGH |
| allowFontScaling causes layout to break at 150% | MEDIUM |
| Decorative image not hidden from screen reader | LOW |
| Missing accessibilityHint on non-obvious button | LOW |
