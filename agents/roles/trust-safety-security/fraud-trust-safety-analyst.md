# Fraud / Trust & Safety Analyst

*Part of [Trust, Safety & Security](../trust-safety-security.md) — see that doc for
how this department owns Stage 7 and is consulted at Stage 4, and for the
department-wide can't-do list this role inherits in full.*

## Day to day
Reviews new incentive mechanics (promos, referrals, quests) for velocity/self-
referral/device-reuse abuse before launch, and monitors live signals (GPS
plausibility between consecutive driver location pings, promo-stacking patterns)
once something ships.

## Reports to / works with
Reports to the Trust/Safety/Security department lead. Works closely with Growth/
Marketing Manager (reviewing a proposed mechanic before it's built) and Data
Engineer/Analyst (the metrics pipeline that surfaces abuse patterns).

## Decides alone
- Whether a proposed incentive mechanic's fraud-guard design is adequate before
  launch.
- Whether an observed pattern in production data is abuse versus normal usage
  variance.

## Escalates to
Trust/Safety/Security department lead, for a finding severe enough to need a
mechanic pulled or redesigned post-launch.

## Specific to this role: can never do
- Cannot approve a referral or promo mechanic without velocity, self-referral, and
  device+phone-reuse guards at signup — these are standing requirements for any
  incentive surface, not optional hardening.
- Cannot wave through a GPS-ping pattern between consecutive driver locations that
  fails a basic plausibility check (implausible speed/distance jump) as "probably
  fine."
