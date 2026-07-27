# Change Impact & Risk Log

> Copy this template into the PR description, or save a filled copy to
> `docs/change-log/YYYY-MM-DD-<short-slug>.md` for anything touching a
> live-tested surface (rides, dispatch, payments, auth, corporate, safety).
> See `CLAUDE.md` → "Change Impact & Risk Log (mandatory)" for the full policy.

## Summary

| Field | Value |
|---|---|
| Date | |
| Author | |
| Surface(s) | backend / rider-app / driver-app / admin-dashboard (pick all that apply) |
| Domain (Sentry tag) | dispatch / payments / auth / corporate / safety / drivers / rides / admin / ai |
| PR / commit link | |
| Related issue or gap ID | |

## 1. Issue / gap identified

One or two sentences: what's wrong today, observed how (bug report, audit finding, QA catch).

## 2. Root cause

Why it happens — not just the symptom. If unknown/unconfirmed, say so explicitly rather than guessing.

## 3. Fix / remediation

What changed, in plain language, before the code detail below.

## 4. Risk & impact on existing functionality

- What else reads/writes the same table, state field, endpoint, or background loop?
- Could this regress a flow that currently works? Which one(s)?
- Blast radius: isolated / single-surface / cross-surface — state it explicitly, don't leave it implied.
- Any interaction with the 16 background loops (`backend/core/lifespan.py`), the ride state machine, or money/wallet deltas?

## 5. User-experience effect

- Who sees a difference: rider / driver / corporate admin (owner/billing/ops/section-manager) / internal admin / nobody (backend-only)?
- Is the change visible **mid-session** to someone already using the app (e.g. a rider mid-ride, a driver online)?
- Any copy/notification change? If yes, was it reviewed against the customer-centric tone standard (specific, non-technical, actionable)?

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| | | |

## 7. Before / after

Only required for behavior-changing diffs (skip for pure additive code with no existing caller).

```
# Before
<snippet>
```

```
# After
<snippet>
```

## 8. Rollback plan

How to revert **without a second deploy** if this goes wrong once live:
- Feature flag to flip off (name it), or
- Config/`app_settings` value to revert, or
- Migration rollback SQL (must already exist per `backend/migrations/CLAUDE.md` — "always reversible on paper"), or
- Explicitly state why none of the above applies and a redeploy is the only path (acceptable only for genuinely isolated, low-risk changes).

Note: a `git revert` alone is **not** a rollback plan for anything already applied to live data (Stripe charges, wallet deltas, ride state, insurance-period rows) — those need a data-level remediation plan, not just a code revert.

## 9. Verification performed

- [ ] Automated tests run (list which — unit / integration / e2e)
- [ ] Manual repro steps followed in staging
- [ ] Blast-radius grep performed (list what was searched)
- [ ] Reviewed against relevant `CLAUDE.md` convention(s) (state machine / money / RLS / PIPEDA / observability)
- [ ] Feature-flagged if user-visible and non-trivial (or justify why not)

## 10. Sign-off

- [ ] Rollback plan is concrete and testable
- [ ] Blast radius is stated, not assumed
- [ ] No silent behavior change to an already-shipped flow without the UX field filled in
