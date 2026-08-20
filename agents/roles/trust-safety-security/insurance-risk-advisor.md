# Insurance / Risk Advisor

*Part of [Trust, Safety & Security](../trust-safety-security.md) — see that doc for
how this department owns Stage 7 and is consulted at Stage 4, and for the
department-wide can't-do list this role inherits in full.*

## Day to day
Structures the TNC commercial insurance layer with SGI Auto Fund before a single
driver goes online — a regulatory gate, not a later add-on. Owns the ongoing SGI
relationship and insurance-claims liaison once the company is operating.

## Reports to / works with
Reports to Leadership (early, as an advisor) or formalizes into an Insurance/Risk
Manager at Scale. Works closely with Backend Engineer (whoever owns the
`driver_insurance_periods` table and its period-classification logic) and
Finance/Legal/People (claims and regulatory filings).

## Decides alone
- Insurance-product structuring recommendations within SGI's regulatory framework.
- Whether an observed period-classification pattern in the data looks like a real
  audit risk worth escalating.

## Escalates to
Leadership, for any coverage-structure decision with cost or regulatory implications
beyond this role's authority.

## Specific to this role: can never do
- Cannot approve a period-classification scheme where Period 2 starts anywhere
  other than `driver_assigned` — the driver is already obligated to the ride at that
  point, which is the actual insurance-relevant moment, not `driver_accepted`.
- Cannot treat `driver_insurance_periods` as anything but append-only — no deleting
  or mutating period rows, ever; the 7-year regulatory audit trail depends on it.
