## Decision: Treat CR-4138 as having fixed a real bug but bundled a second, separate regression
**Stage:** 3-4 (Plan & Design / Architecture)
**What was decided:** The root cause of the recurring timeout is not "CR-4138
didn't work" — the real-timer/unmount race it fixed is genuinely gone (no more
`act()` warnings reproduced locally). The recurrence is a second, independent
effect of the same commit: reverting this file's one test from a 20000ms
timeout back to Jest's bare 5000ms default, on the unverified assumption that
fixing the race also fixed whatever originally justified the 20000ms
(C31's earlier, separate finding).
**Why:** Local reproduction directly supports this split: 1 failure in 14
isolated runs, tied to a cold/no-transform-cache run (17s wall) vs. every warm
run (~1.1-1.2s), with zero `act()`/leak symptoms present in the failing run.
That's a timing-margin problem, not a race. C31's own writeup already said the
20000ms timeout's original justification was "CI-runner-specific timing...
rather than a second logic bug" — a claim CR-4138's revert never re-tested
against.
**Alternatives considered:** (a) Assume CR-4138 is simply incomplete and the
race isn't actually fixed — rejected: no `act()` warnings or leaked-timer
symptoms reproduced in 14 local runs, including the one failure, which is
inconsistent with the race still being live. (b) Assume the failure is pure
CI-hardware flakiness with no fixable local signal — rejected: I did get one
local, deterministic-looking reproduction (cold-cache-specific), which is
enough of a signal to act on rather than write off as unreproducible.
**Reversible?** Yes — this is a diagnosis, not a code change. If Stage 5's
implementation and monitoring show the timeout still recurs after restoring a
scoped 15000ms override, that would falsify this diagnosis and point back at
the still-unlocated "worker failed to exit gracefully" leak as the real cause
instead.

## Decision: Recommend a single-test-scoped timeout override, not a file-wide or suite-wide one
**Stage:** 3-4 (Plan & Design / Architecture)
**What was decided:** The fix design calls for adding a timeout parameter to
exactly one `it(...)` call (the mount test) in
`verifyEmailScreen.test.tsx`, not reverting to CR-4138's old file-wide
20000ms, and not touching `jest.config.js`'s suite-wide default.
**Why:** Per CLAUDE.md's pre-merge gates, prefer the narrowest additive change
over a broader one when blast radius should stay contained. A suite-wide
`testTimeout` change would affect all 546 tests across 69 files; a file-wide
change would affect the other 9 tests in this file that never showed the
symptom. This repo already has a precedent for exactly this narrow pattern
(`searchDestinationPinIntegrity.test.tsx:186`, 20000ms on one test), so it's
not introducing a new convention.
**Alternatives considered:** (a) Revert to CR-4138's original file-wide
20000ms (`}, 20000)` on the whole `it`, applied the same way pre-CR-4138) —
rejected as broader than needed since only one test showed the symptom. (b)
Raise `jest.config.js`'s global `testTimeout` — rejected as the widest-blast
option, masking any future genuine slow test anywhere in the suite. (c) Do
nothing and instead chase the still-unresolved "worker failed to exit
gracefully" leak first — considered, but that source file is unconfirmed
(see `challenges-and-issues.md`) and a fix for the confirmed cold-start/margin
issue can ship independently of it.
**Reversible?** Yes — a one-line, one-test parameter; trivial to remove if
proven unnecessary.

## Decision: Use 15000ms, not 20000ms, for the restored per-test timeout
**Stage:** 5 (Development)
**What was decided:** The scoped timeout added to
`it('fires POST /users/verify-email/request on mount', ...)` is 15000ms, not
a revert to CR-4138's old file-wide 20000ms value.
**Why:** Stage 3-4's own reproduction (and this stage's re-run) never showed
this one test alone taking anywhere near 15s — the worst cold run measured
was 17s for the *entire 10-test file*, and this stage's own cold-cache
reproduction of just this file was 7.271s total. 15000ms gives roughly 2x
headroom over the worst whole-file cold time observed for a single test's
budget, without carrying forward the older, unexplained 20000ms figure
verbatim (CR-4102/C31's 20000ms was never itself justified against a
measured worst case — see that incident's own writeup, cited in Stage 3-4's
progress-report — it was a round-number band-aid). Matching a narrower
locally-supported number, on the existing precedent's pattern (a scoped
per-test override), is more defensible than reusing an unexplained constant.
**Alternatives considered:** (a) Reuse 20000ms exactly, matching
`searchDestinationPinIntegrity.test.tsx:186` precedent literally — rejected
as more headroom than any observed data supports needing, and it re-imports
a number this run never independently verified. (b) Use a smaller value
close to the observed worst case (e.g. 10000ms) — rejected as too tight a
margin given CI's shared/contended hardware is expected to be slower than
this dev box, and the whole point of the fix is to stop the test from being
timing-fragile. (c) Leave it at Jest's 5000ms default and treat the
recurrence as CI-only flakiness with no local fix — rejected; this is the
status quo that produced the recurring failure Architecture was asked to
root-cause.
**Reversible?** Yes — a single numeric literal; trivial to raise, lower, or
remove pending real CI recurrence data after this lands.

## Decision: Treat this as CI test infrastructure, not a "live-tested surface" requiring a Change Impact Log entry
**Stage:** 3-4 (Plan & Design / Architecture)
**What was decided:** This fix does not require a `docs/change-log/` Change
Impact & Risk entry under CLAUDE.md's live-tested-surface rule (rides,
dispatch, payments, auth, corporate, safety).
**Why:** The change touches only a Jest test timeout in a test file; it does
not touch `app/verify-email.tsx` or any rider-facing behavior, and CR-4138's
own PR explicitly reached the same conclusion for the same file ("No domain
reviewer applicable per CLAUDE.md's PR review guidance (money/dispatch/safety
surfaces not touched)").
**Alternatives considered:** Writing a full Change Impact Log entry anyway,
out of caution — rejected as disproportionate for a single test-timeout
parameter with no production-code diff, and inconsistent with the precedent
this exact file already set.
**Reversible?** N/A (a documentation-process choice, not a code change) —
if a human reviewer disagrees, the entry can be added at PR time with no cost.

## Decision: Confirm (Stage 8, Finance/Legal/People) — no Change Impact & Risk Log entry required
**Stage:** 8 (Change Review)
**What was decided:** Independently re-checked, rather than just carrying forward,
Stage 3-4/6/7's shared conclusion that CLAUDE.md's mandatory Change Impact & Risk
Log does not apply to this change. Confirmed it does not.
**Why:** CLAUDE.md's trigger is explicit: "Any commit or PR that fixes a bug, closes
a gap, or changes existing behavior must include a Change Impact & Risk entry ... for
anything touching a live-tested surface (rides, dispatch, payments, auth, corporate,
safety)." Re-verified directly (not on trust) via `git diff 5f55bad95..HEAD --stat`:
the only code file touched across both commits on this branch is
`rider-app/__tests__/verifyEmailScreen.test.tsx` (26 lines: one `it(...)` call's
third argument plus its comment). `rider-app/app/verify-email.tsx` — the actual
application screen — has zero diff since `915dca9ca` (#4219), which predates this
branch entirely. `rider-app/jest.config.js` has zero diff. None of rides, dispatch,
payments, auth, corporate billing, or safety are touched by a Jest per-test timeout
literal in a test file. This is CI test infrastructure, not a live-tested surface, so
the mandatory-entry trigger is not met — consistent with CR-4138's own PR reaching
the identical conclusion for the identical file.
**Rollback-plan check (this role's other Stage 8 responsibility per
`roles/finance-legal-people.md`):** N/A for the same reason a Change Impact Log entry
is N/A — there is no live data, wallet balance, Stripe charge, or ride state this
change could put in an inconsistent state. If `15000ms` proves wrong (too tight or
unnecessary), the rollback is a literal one-line `git revert` of `31d9da225` — and
unlike CLAUDE.md's warning that "a `git revert` is not a rollback plan for anything
already applied to live data," that caveat doesn't bind here: nothing this diff
touches is live data. This is the one case in the run where a plain revert *is* an
adequate rollback plan, and it's stated as such rather than reflexively over-applying
the live-data caveat to a change that isn't one.
**GST/PST disclosure, driver-classification language checks (this role's other
standing concerns):** not applicable — no receipt, fare, billing, or onboarding/app
copy is touched by this diff.
**Alternatives considered:** Writing an entry anyway "to be safe" — rejected again at
this stage for the same reason Stage 3-4 rejected it: CLAUDE.md scopes the
requirement to live-tested surfaces by explicit list, and stretching it to cover a
test-only CI timeout would blur that list for every future run, not just add caution
to this one.
**Reversible?** N/A — documentation-process confirmation, not a code change.
