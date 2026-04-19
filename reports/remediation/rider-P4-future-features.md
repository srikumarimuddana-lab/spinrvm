# P4 — Rider App Future Features: Post-Launch Roadmap

These are not bugs or gaps — they are improvements and features that will
make the Spinr rider experience best-in-class. Prioritise after stabilisation.

---

## R-P4-1 · In-App Navigation (Turn-by-Turn for Rider Sharing)
Allow riders to share a "track my ride" link that shows a live map in a browser.
Backend endpoint `POST /rides/{id}/share` exists — build the web view.

## R-P4-2 · Promo Selection UI
Currently the best promo is auto-applied. Allow riders to browse available promos
and manually select one from a bottom sheet in `ride-options.tsx`.

## R-P4-3 · Ride Receipt Sharing
`ride-completed.tsx` has a share button placeholder. Implement PDF/image receipt
generation and native share sheet.

## R-P4-4 · Dark Mode
The app currently only supports light theme. Implement a dark mode toggle in
`settings.tsx` using the existing `shared/theme/` system.

## R-P4-5 · Multi-Language Beyond French
After French is implemented (P1-10), add: Spanish (for future US expansion),
Simplified Chinese (large diaspora in Saskatchewan).

## R-P4-6 · AI FAQ Assistant
`support.tsx` currently has static FAQs. Add a Claude-powered chat assistant
for common questions (trip disputes, payment issues, account help).

## R-P4-7 · Ride Sharing / Carpool
Allow multiple riders to share a vehicle and split the fare automatically.
Backend would need a new ride type.

## R-P4-8 · Loyalty Program Deeper Integration
`loyalty.tsx` screen exists. Build out tier progression, reward redemption,
and point history. Connect to backend loyalty endpoints.

## R-P4-9 · Wallet Transfer Between Riders
`walletStore.ts` has `transfer(recipientPhone, amount)`. Build UI for this
in `wallet.tsx` — peer-to-peer wallet transfers.

## R-P4-10 · Scheduled Ride Push Reminder
15 minutes before a scheduled ride, send a push notification reminding the rider.
Backend cron job + FCM notification type `scheduled_ride_reminder`.

---

## Checklist (plan, not implement)

- [ ] R-P4-1 Live tracking web view for shared ride link
- [ ] R-P4-2 Promo selection UI in ride-options
- [ ] R-P4-3 Ride receipt PDF/share
- [ ] R-P4-4 Dark mode toggle
- [ ] R-P4-5 Additional language support (ES, ZH)
- [ ] R-P4-6 AI FAQ assistant in support screen
- [ ] R-P4-7 Ride sharing / carpool mode
- [ ] R-P4-8 Loyalty program full implementation
- [ ] R-P4-9 Wallet peer-to-peer transfer UI
- [ ] R-P4-10 Scheduled ride push reminder
