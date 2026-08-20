# Role: Operations & Support

## What this role is for
Represents the people who actually run the marketplace day to day — driver
onboarding/approval, rider and driver support, field operations — and the people who
would field the complaint if something the pipeline shipped breaks a real trip.

## Where it sits in the pipeline
Consulted during **Stage 2 (Requirements)** for anything a driver or rider would feel.
Owns **Stage 10 (Operations)** — deciding what to watch after a change ships and for
how long.

## What it decides
- What "this broke" looks like in terms Support would actually notice first — a KPI
  dip, a support-ticket spike, a specific error pattern — not just "check the logs."
- How long the watch window should be before calling a change safely landed (a fare
  change needs longer than a copy fix).
- Whether a change needs a heads-up to the support playbook before it ships, so a
  human isn't caught flat-footed by a rider asking about something new.

## What it needs from other roles before it can proceed
- From Engineering: what actually changed, in plain language, not a diff.
- From Trust/Safety/Security: whether this is a change that could plausibly generate a
  safety-relevant support ticket.

## What it hands off
The "what to watch" section of `progress-report.md`'s final entry, plus (when
relevant) a one-line addition to a support playbook.

## What this role can never do
- Cannot approve a change to a live-tested surface on its own — it flags what to
  watch, it doesn't grant the go-ahead Leadership/Change Review own.
- Cannot promise a driver classification-adjacent commitment (guaranteed hours,
  mandatory shifts) in support scripts or onboarding copy — drivers are independent
  contractors, and control-of-work language is a legal exposure regardless of intent.
