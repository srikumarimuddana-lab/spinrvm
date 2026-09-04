# Admin Portal Design Canvas — Screen Inventory

**Live artifact:** https://claude.ai/code/artifact/faebb44e-78a4-4e03-b090-f622c79da45d

Built with the `/design` skill (Claude Design canvas — an early-preview visual
editor, not part of the production build). This document is a text index of
every artboard on that canvas: what real admin-dashboard route/component it
mirrors, what's drawn, and any non-obvious behavior called out on the
artboard itself. It exists so the canvas's content is searchable and
diffable in git even though the canvas page itself (custom `<x-dc>` /
`sc-for` component markup + a runtime script) isn't a portable static
document — open the live link above for the actual visuals.

Every wireframe entry below was built by reading the real
`admin-dashboard/src/app/dashboard/**` source for that route before drawing
it — field names, table columns, tab labels, and button copy are pulled
from the actual code, not invented. Fidelity is intentionally split:

- **Hi-fi screens** (8, listed first) — polished, on-brand mockups matching
  the app's real design tokens (colors, type, `Card`/`Badge` styles from
  `admin-dashboard/src/app/globals.css`). Three are click-through
  prototypes (Rides, Drivers, Settings).
- **Wireframes** (48) — deliberately low-fidelity: grayscale, dashed
  borders, placeholder bars, hand-written (`Architects Daughter` font)
  annotations. Structure and real copy only, not visual polish.

---

## Hi-fi core flow (row 0)

The primary click-through: **Login → Dashboard → Live Monitoring → Rides →
Drivers → Corporate → Safety → Settings**.

| Screen | Notes |
|---|---|
| **Login** | Static mockup of the admin sign-in screen. |
| **Dashboard** | Static landing/overview mockup. |
| **Live Monitoring** | Static mockup of the real-time ops map/feed. |
| **Rides** *(interactive)* | Clickable prototype. Status tabs (all / searching / assigned / in progress / completed / cancelled / scheduled) filter a 7-row table; row click opens a right-side detail drawer (route timeline, fare breakdown). |
| **Drivers** *(interactive)* | Clickable prototype. Tabs (All Drivers / Approvals / Appeals / Expiring Docs) with live badge counts; row click opens a detail drawer with documents + conditional Approve/Reject (pending) or Reinstate (suspended) actions. |
| **Corporate Accounts** | Static mockup of the account list. |
| **Safety** | Static mockup of the SOS/safety ops screen. |
| **Settings** *(interactive)* | Clickable prototype, 5 tabs (Integrations / Email & Alerts / **Operations** / Company & Apps / **Security**). Operations shows 4 real cards including the 2.5× surge cap table. Security shows 3 real cards (MFA enforcement, dual-approval PII export gate, corporate wallet adjustment cap). Other 3 tabs show an honest "not built out" placeholder. |

**Shared components:** `Sidebar` (7-item nav, active-state + amber Safety
emphasis) and `Topbar` (account controls), both `<dc-import>`ed into every
hi-fi screen above.

---

## Wireframe row 1 — Corporate detail + core admin tools

| Screen | Real route | Notes |
|---|---|---|
| **Corporate Account Detail** | `/dashboard/corporate-accounts/[id]` | Company header, 4-tab sub-nav, Company Profile / KYB checklist / Recent Activity, Master Wallet + Quick Stats. |
| **Corporate · Members** | `.../[id]/members` | Status-filter pills, members table, Allowance Requests card with Pending/Approved/Denied sub-tabs. |
| **Corporate · Policy** | `.../[id]/policy` | Policy toggle, max-fare cap, payment-source select, booking-window rows. |
| **Corporate · Subscription** | `.../[id]/subscription` | Current-plan card (status, period end, cancel-at-period-end), Subscription History table. |
| **Corporate · KYB Verification Queue** | `.../kyb-queue` | Top-level queue (not per-account): company/business-number/tier/region table, Approve/Reject actions. |
| **Staff Management** | `/dashboard/staff` | New-staff form expanded — role presets, real 16-module × 4-column access grid; staff table with MFA/disabled-row states. |
| **Audit Logs** | `/dashboard/audit-logs` | Grouped action filter, "most active actors" rollup, colored-badge log table. |
| **Analytics / KPI Dashboard** | `/dashboard/analytics` | 4 top stat cards, daily ride-trend chart, 8-tab row, KPI-vs-target grid (grounded in this repo's real KPI targets, one shown failing), conversion funnel. |
| **AI Console** | `/dashboard/ai-console` (super admin only) | "Test as" rider/driver panel + chat panel with a real `ActionBubble` (drop-a-pin tool call) reproduction; quotes the real blocked-state copy for non-super-admins. |
| **Promotions** | `/dashboard/promotions` | Create Code dialog expanded, 6 real stat cards, Public/Private/Expired tabs, promo table. |
| **Vehicle Types** | `/dashboard/vehicle-types` | 3-card grid (one inactive), Add/Edit dialog with illustration upload + map-marker picker. |
| **Service Areas** | `/dashboard/service-areas` | Area list + one row expanded to the real 9-tab sub-nav, General tab fleshed out (regulatory fields, Safety panel, surge, driver matching, boundary map). |
| **Referrals** | `/dashboard/referrals` | Combined spend summary, Driver/Rider toggle, 8-stat funnel, redemption trend chart, Failed Claims re-queue card, leaderboard, referrer→referee pairs. |

---

## Wireframe row 2 — Remaining sidebar pages

| Screen | Real route | Notes |
|---|---|---|
| **Document Requirements** | `/dashboard/documents/requirements` | Closest real page to "vehicle documents" — orphaned from nav since `/dashboard/documents` now redirects to Service Areas. Requirements table (driver/vehicle/both) + Add dialog. |
| **Users** | `/dashboard/users` | Directory (masked PII, role/status badges) + full detail dialog (status controls, role flags, wallet ledger, saved cards, recent rides). |
| **Heat Map** | `/dashboard/heatmap` | Demand overlay placeholder + live demand-pressure panel with the real 6-tier band legend and a 6-hour forecast chart. |
| **Pickup Venues** | `/dashboard/venues` | Venue list + editor with a pickup-points sub-list (one flagged outside its detection radius). |
| **Quests & Bonuses** | `/dashboard/quests` | 4 stat cards, quest table with an expanded participant-progress panel, full Create Quest dialog. |
| **Earnings** | `/dashboard/earnings` | 5-tab GBV/take-rate business overview, daily trend chart, ride funnel, cancellation mix, transactions table. |
| **Subscriptions** | `/dashboard/subscriptions` | Platform-wide Spinr Pass **plan definitions** (distinct from a corporate account's own subscription tab above). |
| **Support & Issues** | `/dashboard/support` | Full 7-tab shell; see row 3 below for each tab fleshed out individually. |
| **Help Desk / Zoho Tickets** | `/dashboard/support-tickets` | Ticket list + a compressed Trends section (opened-vs-closed chart) in one artboard, covering both real sub-routes. |
| **Notifications / Cloud Messaging** | `/dashboard/cloud-messaging` | Compose form (audience/channel/schedule targeting) + send-history table. |
| **Redis & Infra** | `/dashboard/monitoring/redis` | Connectivity probes, WS fan-out health, keys-by-prefix table, per-replica counters. |
| **Sentry Issues** | `/dashboard/sentry-logs` (super admin only) | Filtered issue-card list with domain/surface/level badges. |
| **Stripe Events** | `/dashboard/stripe-events` (super admin only) | Stuck-event table + replay/dismiss dialog previews. |
| **Records & Compliance** | `/dashboard/records` (super admin only) | 4-tab shell (Search & Transfer / Compliance Reports / Bulk Import / Export Approvals) with the dual-approval Export Approvals queue fleshed out; the other 3 tabs get their own dedicated artboards in row 4. |

---

## Wireframe row 3 — Support & Issues tabs + Drivers legacy tools

Support & Issues (`/dashboard/support`) has 7 real tabs; each gets its own
artboard here (the row-2 `SupportPage` artboard only fleshes out Tickets):

| Screen | Notes |
|---|---|
| **· Disputes tab** | This tab literally renders the standalone `/dashboard/disputes` component. Rider Disputes / Chargebacks sub-tabs, 4 stat cards, Resolve dialog (Approve Full Refund / Partial Refund / Reject). |
| **· Complaints tab** | Filters, complaints table, Review dialog (Resolve/Dismiss) + File Complaint dialog. |
| **· Flags tab** | Filters, flags table (driver/rider target icon), Flag Details dialog (Deactivate/Delete) + Create Flag dialog. |
| **· Lost & Found tab** | All 4 real statuses (Reported/Driver Notified/Resolved/Unresolved), Report Item + Edit Item dialogs. |
| **· FAQs tab** | Renders the standalone `/dashboard/faqs` page (merged, one implementation). FAQ table sorted by real display order, New FAQ dialog. |
| **· Legal tab (Rider)** | Rider/Driver audience sub-tabs, doc-type dropdown, document editor card (version, save-when-dirty). |
| **· Legal tab (Driver)** | Second artboard for the same tab — Driver audience adds 2 driver-only policy types (Deactivation & Appeals, Background-Check Consent) the Rider view doesn't have. |

Drivers sub-items not on the hi-fi Drivers screen's own tabs:

| Screen | Real route | Notes |
|---|---|---|
| **Driver Appeals** | `/dashboard/drivers/appeals` | Pending/Approved/Denied stats, Review dialog — Approve auto-reactivates the account. |
| **Driver Decals / Welcome Letters** | `/dashboard/drivers/decals` | Sidebar says "Welcome Letters"; route/component still say "decals". 4 filter/summary cards, driver table with per-row Generate + bulk Download PDF. |
| **Drivers · Expiring Docs** | `/dashboard/drivers/expiring` | 7/14/30-day window toggle, flat cross-driver document list, Nudge action (reminds, never renews). |
| **Bulk Driver Import** | `/dashboard/drivers/import` | Saskatoon recruitment CSV shape. Template → Validate → Review (errors/warnings tables) → token-bound Commit. No longer in the sidebar nav, kept at direct URL. |
| **Legacy Driver Import** | `/dashboard/drivers/legacy-import` | A **different** CSV shape/parser from Bulk Import (raw Mongo export). Every created driver forced `needs_review`/unverified/offline. Includes two bolted-on one-time incident-repair tools (Fix Orphaned Legacy-Linked Accounts, Fix Backfilled Driver Join Dates). |
| **Legacy SIN/DOB Backfill** | `/dashboard/drivers/legacy-sin-dob-backfill` | Two-file (`banks.csv` + `drivers.csv`) validate/review/commit flow. Never overwrites an existing SIN/DOB; neither value is ever shown in the UI. |

---

## Wireframe row 4 — Remaining legacy-import family + heavyweight tools

| Screen | Real route | Notes |
|---|---|---|
| **Legacy Vehicle History Backfill** | `/dashboard/drivers/legacy-vehicle-history-backfill` | Sibling of SIN/DOB Backfill — same two-file validate/review/commit shape, appends vehicle history without touching live vehicle fields. |
| **Driver Licence Backfill** | `/dashboard/driver-license-backfill` | Manual, no-OCR transcription workflow — a "Pending" list of drivers missing a licence number/class, inline number/class inputs + Save per row. |
| **Legacy Saved-Address Backfill (Riders)** | `/dashboard/riders/legacy-saved-address-backfill` | The **rider**-side sibling (`customer_addresses.csv` + `customers.csv`) — distinct from every driver-side tool above it. |
| **Data Transfer** | `/dashboard/data-transfer` (super admin only, strict role gate) | Real 5-tab module: Search & Select (fully drawn — fuzzy search, "select all N matching" banner, results table) feeds Export / Import / SGI Compliance Forms (real D00032/D00033) / Jobs & History via one shared selection. |
| **Compliance & Tax Reporting** | `/dashboard/compliance` | Service-area-scoped report tabs (GST/PST, SGI Insurance Billing, Knight Archer Insurance Billing, Driver Roster, T4A Filer Handoff, Airport Trips, Saskatoon City) — SGI Insurance Billing tab fleshed out with its real $0.11/km Period 2/3 rate description. |
| **Bulk Operations** | `/dashboard/bulk-operations` (super admin only) | The largest page in the app — a 6-phase migration tool. Only Phase 3 (Stripe Mapping Import) is drawn in full; the other 5 phases (Bulk/Legacy Driver Import, SIN/DOB & vehicle-history backfills, Legacy Booking/Wallet import, route-snapshot regeneration, pre-launch data-quality tools) are named but collapsed. |

---

## Coverage notes

- This closes wireframe coverage of essentially every real route in
  `admin-dashboard/src/app/dashboard/**`. The only two routes left
  unbuilt — `/dashboard/driver-offers` and `/dashboard/forecast` — are
  plain server-side redirects into Analytics tabs (`?tab=offers`,
  `?tab=forecast`) already represented in the KPI Dashboard artboard's tab
  row, so there was nothing further to draw for them.
- "Vehicle documents," "legal documents," and "driver decals" as requested
  during this build don't have their own standalone routes in the real
  app — each maps onto an existing page/tab (Document Requirements, the
  Legal tab, and the "decals"-named Welcome Letters page respectively);
  the entries above note that mapping rather than inventing a new page.
- Canvas layout is 4 wireframe rows below the hi-fi row, `y = 2080 / 4200 /
  6800 / 9100`, each artboard `1440px` wide with `120px` horizontal gaps —
  see the live canvas for the actual visual layout and pan/zoom.
