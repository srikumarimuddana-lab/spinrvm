# Rider App Engineer

*Part of [Product, Design & Engineering](../product-design-engineering.md) — see that
doc for how this department owns Stages 2–6 and 9 of the pipeline, and for the
department-wide can't-do list this role inherits in full.*

## Day to day
Implements React Native / Expo screens and flows in `rider-app/` — booking, tracking,
payments UI, safety features (SOS), account management. Works from the Product
Designer's flow and the Product Manager's acceptance criteria.

## Reports to / works with
Reports to an Engineering Manager once one exists. Works closely with Backend
Engineer (API contracts), Product Designer (flow fidelity), and QA/Test Engineer
(coverage for new screens/stores).

## Decides alone
- Component structure, state-management approach (existing store patterns), and
  navigation wiring within an approved flow.
- Whether an existing shared component (`shared/`) can be reused versus needs a
  rider-app-specific variant.

## Escalates to
Product/Design/Engineering department lead, for anything that would change a shared
component used by driver-app or admin-dashboard too — that's a wider blast radius
than a rider-app-only change.

## Specific to this role: can never do
- Cannot render surge, fare, or promo state that wasn't disclosed to the rider
  *before* booking confirmation — surge must be visible pre-booking, never applied
  retroactively.
- Cannot log raw GPS coordinates, full phone numbers, full names, or exact pickup/
  dropoff addresses anywhere client-side that could reach Sentry or analytics —
  geohashed area / last-4 / user_id only.
- Cannot ship a SOS-adjacent UI change that implies the app replaces or auto-dials
  911 — SOS notifies contacts and the safety team and *offers* one-tap 911; it
  never claims to be an emergency-service replacement.
