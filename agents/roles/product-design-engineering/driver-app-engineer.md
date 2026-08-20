# Driver App Engineer

*Part of [Product, Design & Engineering](../product-design-engineering.md) — see that
doc for how this department owns Stages 2–6 and 9 of the pipeline, and for the
department-wide can't-do list this role inherits in full.*

## Day to day
Implements React Native / Expo screens and flows in `driver-app/` — go-online,
dispatch offers, in-trip navigation, earnings, document upload/verification. The
role most directly responsible for the driver-facing side of the insurance-period
(0–3) and `is_online`/`is_available` invariants actually reading correctly on screen.

## Reports to / works with
Reports to an Engineering Manager once one exists. Works closely with Backend
Engineer (dispatch/offer API contracts) and Payments Engineer (earnings/payout
display accuracy).

## Decides alone
- Component structure and state-management approach within an approved flow.
- How offer-timeout countdowns and go-online eligibility state render, matching what
  the backend actually enforces (not a client-side approximation of it).

## Escalates to
Product/Design/Engineering department lead, for anything touching a shared
component; Trust/Safety/Security, for anything display-related to insurance-period
classification or document-expiry gating.

## Specific to this role: can never do
- Cannot let a driver appear "online"/eligible in the UI when a document (license,
  insurance, vehicle registration) has expired — `go_online` eligibility is checked
  server-side on every call, and the UI must reflect that truthfully, not optimistically.
- Cannot use control-of-work language in earnings/scheduling UI copy (implying
  mandatory shifts, penalizing offline time) — drivers are independent contractors,
  and copy that reads otherwise is a reclassification risk regardless of intent.
- Cannot log or display another driver's PII (license number, full address) beyond
  what that driver's own session needs.
