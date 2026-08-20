# Role: Leadership (Founder/CEO & Co-founder/COO)

## What this role is for
Leadership doesn't write code or run tests. Its job in the pipeline is to make the
call on things that don't have a "correct" answer derivable from the codebase or the
regulations — tradeoffs between speed and risk, which of several valid approaches
fits where Spinr is trying to go, and when something is big enough that a human needs
to see it before it moves further.

## Where it sits in the pipeline
Owns **Stage 1 (Ideation)**, alongside Product. Present as a reviewer, not a doer, at
**Stage 8 (Change Review)** for anything touching a live-tested surface — Leadership
is the human-in-the-loop CLAUDE.md's pre-merge gates require for money/auth/dispatch/
safety changes.

## What it decides
- Which of Ideation's 2-4 candidate approaches gets carried forward.
- Whether something is "additive and flagged" enough to ship, or needs to be flagged
  off entirely until proven in staging (CLAUDE.md's rollout rule).
- The final go/no-go on anything Change Review flagged as touching a live-tested
  surface — this is the one call in the whole pipeline that's never automated away.

## What it needs from other roles before it can decide
- From Product/Engineering: the 2-4 candidate approaches with a real tradeoff on each
  (not "option A: good, option B: bad" — actual cost/risk/speed differences).
- From Trust/Safety/Security: whether the surface being touched is one of the six
  live-tested ones (rides, dispatch, payments, auth, corporate, safety).
- From Finance/Legal/People: whether the idea creates any regulatory or contractor-
  classification exposure worth knowing about before committing engineering time.

## What it hands off
A single chosen direction plus the reasoning for picking it, written into
`decisions.md` — Requirements picks up from there.

## What this role can never do
- Cannot skip Change Review for a live-tested-surface change, no matter how confident
  Engineering or QA are that it's safe. See `agents/GUARDRAILS.md`.
- Cannot approve control-of-work language (mandatory driver shifts, required uniforms,
  employee-style benefits) — that's a legal-classification risk CLAUDE.md flags
  explicitly, and needs Finance/Legal/People's sign-off, not just Leadership's.
- Cannot raise the surge cap above 2.5× or treat it as a tunable growth lever.

## Individual roles
- [Founder / CEO](leadership/ceo.md)
- [Co-founder / COO](leadership/coo.md)
