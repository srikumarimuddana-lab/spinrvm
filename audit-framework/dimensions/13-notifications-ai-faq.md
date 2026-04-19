# Dimension 13 — Notifications, AI Support & Knowledge Base

**Question:** Does every important event notify the user? Is there in-app help?

---

## Checklist

### Push Notification Coverage (all cases)
Verify each has: FCM payload, deeplink target, foreground handler, background handler

- [ ] New ride offer → driver app
- [ ] Ride cancelled by rider → driver app
- [ ] Ride cancelled by driver → rider app
- [ ] Driver arrived at pickup → rider app
- [ ] Trip started → rider app
- [ ] Trip completed → both apps
- [ ] Payment processed → rider app
- [ ] Payout processed → driver app
- [ ] Payout failed → driver app
- [ ] Document expiry 7-day warning → driver app
- [ ] Document expiry 1-day warning → driver app
- [ ] Document expiry day-of → driver app
- [ ] Post-expiry suspension → driver app
- [ ] Quest completed / reward earned → driver app
- [ ] Spinr Pass renewal reminder → driver app
- [ ] Subscription activated/cancelled → driver app
- [ ] System alerts (maintenance, service outage) → both apps

### Notification Infrastructure
- [ ] Android notification channels declared in `app.config.ts`:
  - `ride-offers` — MAX priority (wakes screen)
  - `default` — HIGH priority
- [ ] iOS: `UNNotificationPresentationOptions.badge + sound + banner` in foreground
- [ ] Background handler registered at module level (before app mounts)
- [ ] Foreground handler processes all notification types — not just a subset
- [ ] Notification tap navigates to relevant screen (deeplink routing)
- [ ] Notification centre: unread badge, read/unread state, pull-to-refresh
- [ ] Notification preferences synced to backend (not local state only)

### AI Support Bot
- [ ] In-app support chat screen exists
- [ ] AI model connected (Gemini 1.5 Flash recommended — `google-generativeai` already installed)
- [ ] Knowledge base / FAQ content loaded as context
- [ ] Fallback to human agent / phone / email when bot can't answer
- [ ] PII not stored in chat history (driver ID in metadata only — not full name in prompt)
- [ ] Rate limiting on AI endpoint (prevent abuse)

### Knowledge Base & FAQ
- [ ] In-app FAQ screen with categories (Onboarding, Payments, Documents, Technical)
- [ ] FAQ search functionality
- [ ] Error states deep-link to relevant FAQ entry
- [ ] FAQ content managed via admin panel — not hardcoded in app

---

## Severity Guide

| Finding | Severity |
|---|---|
| Ride offer notification missing — driver never knows about rides | CRITICAL |
| Document expiry notification missing — driver surprised by suspension | HIGH |
| Foreground handler only processes some notification types | HIGH |
| Notification tap does not navigate — user has to find screen manually | MEDIUM |
| Notification preferences lost on reinstall (local only) | MEDIUM |
| No AI bot or FAQ — drivers have no self-serve help | RECOMMENDATION |
| Android channels not declared — notifications may be silent on Android 8+ | HIGH |
| Background handler not at module level — missed on app kill | HIGH |
