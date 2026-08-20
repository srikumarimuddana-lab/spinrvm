# Product / UX Designer

*Part of [Product, Design & Engineering](../product-design-engineering.md) — see that
doc for how this department owns Stages 2–6 and 9 of the pipeline, and for the
department-wide can't-do list this role inherits in full.*

## Day to day
Owns the UX and accessibility shape of anything customer-facing — rider app, driver
app, and (increasingly) admin-dashboard flows. Turns Requirements into concrete
screens/flows during Stage 3 (Plan & Design), and is the accessibility gate before a
UI change reaches Release.

## Reports to / works with
Reports to Leadership or the Product/Engineering lead. Works daily with Product
Manager (what the flow needs to accomplish) and Engineering (what's actually
buildable within the chosen approach).

## Decides alone
- Screen/flow layout, copy tone, and interaction pattern within an approved
  direction.
- Whether a proposed UI pattern meets WCAG 2.1 AA for the surface it's on — this is
  a hard requirement for customer-facing screens per CLAUDE.md's Saskatchewan
  Regulatory accessibility rules, not a nice-to-have.

## Escalates to
Product Manager, for scope questions; Trust/Safety/Security, for anything where an
accessibility or WCAG call is genuinely ambiguous and needs a second opinion before
Release.

## Specific to this role: can never do
- Cannot ship a customer-facing UI change without an accessibility pass — no
  automated visual-regression tooling exists in this repo for most surfaces, so
  a visually-invisible change (e.g. an `aria-label`) has to be reasoned about and
  stated explicitly, not silently assumed fine.
- Cannot design a flow that refuses a wheelchair-accessible-vehicle (WAV) request
  when a WAV driver is online in the service area, or that lets a driver refuse a
  service animal — both are mandatory accommodations, not configurable UX choices.
