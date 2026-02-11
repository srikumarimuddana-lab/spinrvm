# Feature Gap Analysis — Spinr

This file lists missing rideshare features, grouped and prioritized (High / Medium / Low) with short implementation notes.

## High Priority (user-impact, required for production)
- Server-side Stripe Payment Intents & webhooks
  - Implement Payment Intents flow, verify webhook signatures, and store only non-sensitive metadata.
- Secure authentication flows
  - Short-lived access tokens + refresh tokens, token revocation, and hardened OTP with throttling.
- Driver verification & KYC workflow
  - Document upload, background checks, admin review UI, and status flags.
- Real-time scalable driver matching
  - Geospatial indexing, optimized nearest-driver search (geohash or PostGIS), and matching service.
- WebSocket auth and scoped channels
  - Authenticate on handshake, authorize per-channel (rider_x, driver_x), and enforce TTL.
- SOS / safety features
  - Emergency button, share-ride link, live location sharing, and panic alerts to ops.
- Background location & push notifications for drivers
  - FCM/APNs integration, OS permission handling, background geolocation for ETA updates.

## Medium Priority (important user/ops features)
- Fare engine and surge pricing
  - Centralized fare config (see `FareConfig`) and dynamic surge multipliers.
- Cancellation, disputes, and refunds flow
  - Admin tools, rider support, partial refunds, and driver payouts adjustments.
- Admin dashboard
  - Manage drivers, KYC, fares, service areas, and view metrics.
- Analytics & monitoring
  - Trip metrics, health checks, Sentry for crashes, and usage dashboards.
- Receipts & notifications
  - Email/SMS receipts, trip summaries, and push notifications.

## Low Priority / Nice-to-have
- Pooling and multi-stop trips
- Scheduled rides and pre-booking
- Promotions, coupon management, and loyalty program
- In-app chat (moderated) between rider and driver
- Driver earnings reports and exportable CSVs

## Infra & Operational Gaps
- No IaC or deployment manifests (Terraform/CloudFormation).  
- No backup/restore plan or DB snapshots documented.  
- No CI/CD configuration included.

## Suggested Implementation Phases
1. Security & payments hardening (payments, tokens, WS auth).  
2. Driver verification + admin dashboard.  
3. Real-time matching & scaling (geospatial optimization).  
4. UX features (notifications, receipts, disputes).  
5. Growth features (pooling, scheduled rides, promotions).

