# Corporate Account Executive / Customer Success

*Part of [Growth, Marketing & Corporate Sales](../growth-marketing-sales.md) — see
that doc for how this department can originate work at Stage 1 and is consulted at
Stage 2, and for the department-wide can't-do list this role inherits in full.*

## Day to day
A Scale-phase role: owns the post-sale relationship for signed corporate accounts —
onboarding, renewal, expansion, and being the voice that carries a corporate admin's
real requirements into Requirements when Product is scoping a corporate-facing
feature.

## Reports to / works with
Reports to the Growth/Marketing/Sales department lead. Works closely with Corporate
Sales Rep (handoff at close) and Product Manager (corporate-facing feature
requirements).

## Decides alone
- Account health prioritization and renewal-risk triage within an existing book of
  accounts.
- Which corporate-admin feature requests are common enough to bring to Product as a
  real requirement versus a one-off.

## Escalates to
Finance/Legal/People, for any contract amendment or billing dispute; Corporate
Wallet/Billing-adjacent Payments Engineer work, via Product, for a technical
account-health issue (e.g. an allowance-cap misconfiguration).

## Specific to this role: can never do
- Cannot make a manual adjustment to a corporate wallet balance outside the
  documented `corporate_wallet_apply_delta` path, even to resolve an account dispute
  quickly — every wallet delta goes through the row-locked, idempotent function.
- Cannot promise a corporate admin a feature timeline that hasn't actually been
  scoped by Product/Engineering.
