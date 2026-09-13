# Spinr UX/UI design principles

Canonical reference for usability and visual-design principles, each tied to a
concrete Spinr example — load this before auditing a UI diff for anything
beyond brand/token compliance. Companion to `.claude/context/brand-spinr.md`
(palette/type/logo) and the minimalism principle documented there — this file
is the broader usability/UI layer minimalism sits inside, not a replacement
for it.

Referenced by `spinr-design-consistency-reviewer` (mechanical per-diff checks)
and `spinr-ui-ux-critic` (holistic per-screen judgment). Built 2026-09-12 from
a repo-wide audit that found these principles were checked *implicitly* in
several places but never named or collected — see the coverage map below for
what already existed versus what this doc adds.

## Nielsen's 10 Usability Heuristics

| # | Principle | What it means for Spinr | Review coverage |
|---|---|---|---|
| 1 | **Visibility of system status** | User always knows what's happening — fare-calc spinner, ride-status banner, driver GPS updates, every ride-state change pushed via WebSocket | Existing — `spinr-design-consistency-reviewer`'s four-states check, `spinr-realtime-reliability-reviewer`'s WS-event-per-transition rule |
| 2 | **Match between system and the real world** | Plain language, not jargon — "Driver is 3 min away," not a raw ETA timestamp; "ride" not `trip_id` | Existing, partial — the copy-tone check covers register but not jargon-vs-plain-language specifically |
| 3 | **User control and freedom** | Clear undo/cancel/back paths through any flow | **New — see below** |
| 4 | **Consistency and standards** | Same word/color/action means the same thing everywhere | Existing — brand-consistency check + sibling-screen consistency check |
| 5 | **Error prevention** | Stop the mistake before it happens, not just handle it after | **New — see below** |
| 6 | **Recognition rather than recall** | Show options/info on-screen; don't make the user remember them | **New — see below** |
| 7 | **Flexibility and efficiency of use** | Shortcuts for repeat users without cluttering new ones | **New — see below** (with a Spinr-specific guardrail) |
| 8 | **Aesthetic and minimalist design** | No irrelevant/rarely-needed info competing for attention | Existing — see the shared minimalism principle in `brand-spinr.md` |
| 9 | **Help users recognize, diagnose, and recover from errors** | Plain-language errors with a fix, not a stack trace | Existing, partial — design-consistency-reviewer flags raw exception strings shown to users |
| 10 | **Help and documentation** | Findable, task-focused help exactly when needed | **New — see below** |

Related frameworks, not separately tracked here because they overlap heavily
with the table above: **Shneiderman's 8 Golden Rules** (strive for
consistency, enable shortcuts, offer feedback, design for closure, prevent
errors, permit easy reversal, support internal locus of control, reduce
short-term memory load) and **Gestalt principles**, which are really the
perceptual layer the UI principles below sit on.

## UI / visual design principles

| Principle | What it covers | Review coverage |
|---|---|---|
| Hierarchy | One clear primary action; size/weight/position signal importance | Existing — `spinr-ui-ux-critic`'s "information hierarchy" check |
| Contrast | Color/size/weight separate elements; WCAG contrast ratio | Existing — `spinr-accessibility-reviewer` |
| Alignment | Grid discipline, nothing floats arbitrarily | Not separately named — folds into the sibling-screen consistency check |
| Proximity / grouping | Related items sit together | Existing — `spinr-ui-ux-critic`'s "dense content grouping" check |
| Repetition / consistency | Same pattern reused everywhere | Existing — the token system + brand-consistency check |
| Balance | Visual weight distributed sensibly | Not separately named — folds into the minimalism/visual-noise check |
| Whitespace | Breathing room | Existing — the minimalism/visual-noise check |
| Feedback | Every action gets a visible response | Existing — the four-states check |
| Affordance | A tappable thing looks tappable | Existing — the error-state "retry affordance" check, though not generalized beyond errors |
| Accessibility (WCAG POUR) | Perceivable, Operable, Understandable, Robust | Existing — `spinr-accessibility-reviewer` |
| Responsiveness | Adapts across phone sizes, tablets, orientation | Existing — the "responsive/layout" check |

**Scope note**: Spinr has no separate marketing-website surface in this repo
— the five documented surfaces are `backend`, `rider-app`, `driver-app`,
`admin-dashboard`, `shared`. `admin-dashboard` (Next.js) is the only web
surface these UI principles apply to today.

## The 5 gaps this doc introduces coverage for

These had no review coverage anywhere before this doc. Each entry gives
concrete, checkable signals — not just the abstract principle name — so a
reviewer can actually act on it in code.

### Error prevention (proactive, not reactive)
- A form/input with a required or format-constrained field (payment details,
  promo code, phone/email) that has no client-side validation before hitting
  the API — the API-level rejection is a fallback, not the first line
- A destructive or hard-to-reverse action (delete a saved place, remove a
  payment method, a driver going offline mid-search, an admin removing a
  driver) shipped with no confirmation step
- A money- or state-changing action (submit ride request, confirm
  cancellation) with no disabled/loading guard against a double-tap creating
  a duplicate ride or duplicate charge

### User control and freedom
- A multi-step flow (booking, KYB onboarding, corporate signup) with no
  visible way to go back a step without losing already-entered data
- A cancel/dismiss action styled with equal or greater visual weight than the
  primary confirm action — the classic mis-tap trap
- **Don't flag** a genuinely irreversible action that's irreversible by
  deliberate business/regulatory rule (e.g., `cancelled` being invalid after
  `in_progress` in the ride state machine) as a missing-undo gap — that's a
  policy boundary, not a UX oversight

### Recognition rather than recall
- A flow that shows information once (a code, a reference number, a fare
  breakdown) and then requires the user to have remembered it on a later
  screen, instead of keeping it visible or accessible
- Repeated manual entry of information the system already has (a saved/recent
  destination, existing vehicle info) with no suggested/autofill affordance
- **Driver-app specific**: given the app's own documented "fast,
  one-handed, glanceable" direction (`spinr-rider-driver-design-system`), a
  ride-offer or dispatch screen that shows fare/pickup details and then hides
  them before the accept/decline decision is a real finding here, not a
  style nit

### Flexibility and efficiency of use
- A repeat action with no shortcut for returning users (e.g., rebooking a
  recent/favorite trip requires the full booking flow every time) — flag as
  an opportunity, not a blocker
- **Hard guardrail**: any "efficiency" feature proposed for driver-app must
  stay strictly opt-in. Auto-accept defaults, mandatory quick-actions, or
  anything nudging toward "always-on" driver behavior is a
  contractor-misclassification risk (see CLAUDE.md's "What Spinr Is Not" and
  Saskatchewan Regulatory sections) — flag it for legal review, don't treat
  implementing it as a pure UX win

### Help and documentation
- A new non-trivial feature or flow shipped with no discoverable help entry
  point (support link, tooltip, "what does this mean" affordance) —
  especially for an error state distinctive enough that plain-language copy
  alone may not resolve user confusion
- A new safety-adjacent feature that doesn't link to the safety-hub/support
  surface within a small number of taps
