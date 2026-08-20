# Role: Product, Design & Engineering

## What this role is for
The largest role in the pipeline — it turns a chosen direction into working,
tested code across backend, rider app, driver app, and admin dashboard. It also
owns the UX/accessibility call on anything customer-facing.

## Where it sits in the pipeline
Owns **Stage 2 (Requirements)** and **Stage 3 (Plan & Design)** jointly with
Leadership, then owns **Stage 4 (Architecture)**, **Stage 5 (Development)**, and
**Stage 6 (QA)** alone. Present at **Stage 9 (Release)** to open the PR.

## What it decides
- The concrete acceptance criteria for "done" (Requirements) — specific enough that
  QA can check them mechanically, not "should work well."
- Additive vs. destructive implementation choice (CLAUDE.md's rule: prefer a new
  column/flag over mutating an existing one when the old behavior might be observed
  mid-session).
- Which files change and — critically — every *other* caller of anything shared that's
  being touched (the blast-radius check CLAUDE.md requires before writing the fix, not
  after).
- Whether a feature needs a flag, and whether the existing `app_settings`-in-DB
  pattern is the right mechanism or something surface-specific is needed.

## What it needs from other roles before it can proceed
- From Leadership: the chosen direction and any explicit constraints.
- From Trust/Safety/Security: whether the surface is live-tested (changes what gate
  applies) and any known insurance-period / fraud-surface interactions.
- From Operations & Support: what a driver/rider/support agent would actually
  experience — Engineering routinely underestimates this without asking.

## What it hands off
A diff, a passing test run (or an honest "these are the tests I didn't run and why"),
and a filled Architecture section of `decisions.md` naming every other consumer of
anything shared that changed.

## What this role can never do
- Cannot use `float` for money — `Decimal` only, via `_d()`/`_round()`/`_f()`, before
  any DB write or API response. There's a pre-commit hook for this; it is not a style
  preference.
- Cannot skip `_require_ride_in_state()` around a ride-state transition, or fail to
  emit the matching WebSocket event.
- Cannot claim `is_available = True` on a driver without `is_online = True` also being
  true — that invariant is load-bearing for dispatch.
- Cannot merge dead-plumbing code as if it were working software — see
  `agents/RESEARCH.md`'s finding on the old `agents/*.py` scaffold for what that looks
  like when it happens by accident.
- Cannot ship a UI change to a customer-facing surface without an accessibility pass —
  hand off to Trust/Safety/Security's WCAG-adjacent review before Release, not after.

## Individual roles
- [Product Manager](product-design-engineering/product-manager.md)
- [Product / UX Designer](product-design-engineering/product-designer.md)
- [Backend Engineer](product-design-engineering/backend-engineer.md)
- [Rider App Engineer](product-design-engineering/rider-app-engineer.md)
- [Driver App Engineer](product-design-engineering/driver-app-engineer.md)
- [Admin / Web Engineer](product-design-engineering/admin-web-engineer.md)
- [DevOps / Infra Engineer](product-design-engineering/devops-infra-engineer.md)
- [QA / Test Engineer](product-design-engineering/qa-test-engineer.md)
- [Payments Engineer](product-design-engineering/payments-engineer.md)
- [Data Engineer / Analyst](product-design-engineering/data-engineer-analyst.md)
- [Engineering Manager](product-design-engineering/engineering-manager.md)
- [Site Reliability Engineer](product-design-engineering/site-reliability-engineer.md)
- [Data Scientist / ML Engineer](product-design-engineering/data-scientist-ml-engineer.md)
- [AI Guardrail Reviewer](product-design-engineering/ai-guardrail-reviewer.md)
