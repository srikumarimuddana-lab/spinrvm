# Dimension 01 — Feature Completeness

**Question:** Does the module implement everything it was designed to do?

---

## Checklist

### Screens & Navigation
- [ ] Every planned screen exists as a file
- [ ] No screen returns a placeholder ("Coming Soon") without a tracking issue
- [ ] Navigation between screens works in both directions (forward + back)
- [ ] Deep links route to the correct screen with correct state
- [ ] Tab bar / drawer items all resolve to real screens

### User Flows
- [ ] Happy path works end-to-end without manual intervention
- [ ] Every flow has a defined success state (what does the user see when done?)
- [ ] Every flow has a defined error state (what happens when the API fails?)
- [ ] Every flow handles the "halfway through" interruption (app killed, network dropped)

### State Machine
- [ ] All states are defined (idle, loading, success, error, empty)
- [ ] Invalid state transitions are rejected (can't skip steps)
- [ ] State is restored correctly after app restart

### Backend Endpoints
- [ ] Every screen that fetches data has a corresponding backend endpoint
- [ ] Every action (button tap) has a corresponding backend endpoint
- [ ] No endpoint is called but missing (404 in dev = missing feature, not a bug)
- [ ] No endpoint exists in the backend with no corresponding UI (dead code)

### Missing Screen Checklist (common omissions)
- [ ] Legal / Terms of Service / Privacy Policy screen (required for App Store)
- [ ] Data export / account deletion screen (required for PIPEDA/GDPR)
- [ ] Help / FAQ screen
- [ ] Dispute resolution screen
- [ ] Empty state for every list (what shows when there are 0 items?)

---

## Severity Guide

| Finding | Severity |
|---|---|
| Core flow completely missing (e.g. no payout screen) | CRITICAL |
| Screen exists but action button does nothing | HIGH |
| Missing error state — user sees blank screen on API failure | MEDIUM |
| Missing empty state — list just disappears when empty | MEDIUM |
| Coming Soon placeholder with no tracking issue | LOW |
| Nice-to-have feature not yet built | RECOMMENDATION |
