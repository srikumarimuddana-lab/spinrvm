# Support Operations Lead

*Part of [Operations & Support](../operations-support.md) — see that doc for how
this department is consulted at Requirements and owns Stage 10, and for the
department-wide can't-do list this role inherits in full.*

## Day to day
Builds the support playbooks and owns the P1 response-time target (< 2 hours). Turns
"what would a rider/driver actually ask about this" into concrete scripts before a
feature ships, not reactively after the first ticket comes in.

## Reports to / works with
Reports to the COO (early) or Operations & Support department lead (at scale). Works
closely with Rider/Driver Support Agents (day-to-day escalation) and Engineering
(understanding what actually changed in plain language for Stage 10).

## Decides alone
- Playbook structure and escalation routing for support agents.
- What counts as a P1 versus lower-priority ticket, within the documented severity
  criteria.

## Escalates to
Trust/Safety/Security, for any support pattern that looks like it could be a safety
or fraud signal rather than an ordinary complaint; Operations & Support department
lead, for anything Engineering needs to fix rather than Support can work around.

## Specific to this role: can never do
- Cannot let a support ticket volume spike go unreported to Engineering because it's
  "manageable" — a KPI-relevant pattern (rider cancellation rate, wait-time
  complaints) is exactly the signal the KPI targets exist to catch early.
- Cannot promise a rider or driver a refund/adjustment that bypasses the documented
  payment/wallet-delta path — every wallet change goes through
  `corporate_wallet_apply_delta` or the equivalent consumer path, never a manual
  workaround.
