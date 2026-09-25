# Clean-Sheet Audit — Five Design Sessions (all surfaces)

This is the design counterpart of `research-sessions.md`. Each session spends one hour
on the design of one area of Spinr. It will:
- inventory every screen,
- score each screen against Spinr's own design direction and industry patterns,
- attack it for bad-experience risks,
- recommend the top improvements, each with a first step behind a feature flag.

**The five sessions can run at the same time.** Each one writes only to its own folder
(`docs/audit/clean-sheet/design/<area>/`) and opens its own PR.

| # | Session | Surfaces | Folder |
|---|---|---|---|
| 1 | Rider app | `rider-app/` | `design/rider-app/` |
| 2 | Driver app | `driver-app/` | `design/driver-app/` |
| 3 | Admin / ops console | `admin-dashboard/src/app/dashboard/` | `design/admin-console/` |
| 4 | Corporate portal & public touchpoints | `admin-dashboard/src/app/{company-login,company-signup,company-portal,track}/`, receipts, notification copy | `design/corporate-public/` |
| 5 | Design system, brand, accessibility & content | `shared/theme/`, `shared/components/`, brand, copy, tokens | `design/system-brand-a11y/` |

**Not in this repo:** a public marketing website. The Vercel project
`desktop-website` is unrelated (`.claude/context/connector-scoping.md`). If Spinr has a
marketing site elsewhere, it needs its own session with access to that repo.

---

## 1. How every session runs (shared)

**Model plan:** pick **Fable** as the session model (it acts as orchestrator and does
the synthesis). Launch these lanes in one message, in the background:

| Lane | Model | subagent_type | Job |
|---|---|---|---|
| **C — Current state** | `sonnet` | `spinr-ui-ux-critic` | Screen inventory and a scorecard for each screen (§1.2) against the surface's own direction. Returns text |
| **R — External research** | `fable` | general-purpose | Up to 12 web searches, primary sources first: Apple HIG, Material 3, WCAG 2.1/2.2 (W3C), Nielsen Norman Group, the design/engineering blogs of Uber, Lyft and Grab (Uber's Base design system), app-store screenshots and help-centre pages. Returns cited pattern notes |
| **A — Adversary** | `sonnet` | `spinr-accessibility-reviewer`, then `spinr-design-consistency-reviewer` rules | How can each screen confuse, surprise or exclude someone? Checks include: <ul><li>missing loading, empty, error or offline states</li><li>hidden costs</li><li>tiny tap targets</li><li>colour-only meaning</li><li>screen-reader dead ends</li><li>sunlight, cold/gloves or one-hand use</li><li>low-end Android</li></ul> Returns text |

**Timeline:**

| Minutes | What happens |
|---|---|
| 0–5 | Read inputs |
| 5–35 | Lanes run |
| 35–50 | Synthesis |
| 50–60 | Commit, draft PR, report |

At minute 35, use whatever has come back and list anything missing in `DEFERRED.md`.

**Inputs to read first:**
- `CLAUDE.md`
- the master prompt §4 and §7
- this file (your session's section)
- your surface's design skill:
  - `.claude/skills/spinr-rider-driver-design-system/SKILL.md` (sessions 1, 2, 5)
  - `.claude/skills/spinr-admin-design-system/SKILL.md` (sessions 3, 4, 5)
- `docs/design/rider-driver-app-design-system.md`
- `.claude/context/brand-spinr.md`
- if they exist, the latest `rapid-baseline-*` findings for UX/a11y (lane A7)

**Rules:**
- Recommend only. No code, design-token, config or data changes.
- No PII: use synthetic names in every example.
- Judge each surface against **its own** direction:
  - admin = "Quiet Console" (calm, restrained)
  - rider and driver apps = warmer, and deliberately different from each other
  - never apply one surface's direction to another
- Every claim is labelled VERIFIED / INFERRED / ASSUMED / PROPOSED / UNKNOWN.
  External patterns are cited with URL and date read.
- **Visual evidence:**
  - Rider and driver apps have **no screenshot tooling**. Say "reasoned from code, not
    screenshotted" on every visual claim.
  - Admin has Playwright visual baselines for 6 pages in
    `admin-dashboard/e2e/visual-regression.spec.ts-snapshots/`. Read those PNGs as
    evidence, and state which pages have none.
- **Guardrails from CLAUDE.md:** surge and every fee visible before booking; no dark
  patterns; contractor-safe driver copy (no control-of-work language); WCAG 2.1 AA
  floor; SOS never claims to replace 911.

### 1.1 Patterns radar entry (`patterns-radar.md`)

```markdown
### <Pattern name> — ADOPT | TRIAL | ASSESS | HOLD
- What it is (plain language): …
- Who uses it / source: … (URL, date read)
- Spinr today: … (screen path, VERIFIED/INFERRED)
- User benefit: …   Effort: S/M/L   Risk (incl. a11y): …
- How it could backfire: …
- First step behind a flag: …   Measure success by: <metric, e.g. booking completion, contacts per 100 trips>
```

### 1.2 Screen scorecard (`current-state.md`, one row per screen)

Score each column 1–5 and cite evidence for any score below 3.

`Screen · Clarity (one main action) · Hierarchy · States (loading/empty/error/offline) ·
Trust & transparency (price, fees, ETA) · Effort (taps/typing) · Accessibility ·
Consistency with direction · Copy tone · Evidence`

### 1.3 Outputs (in the session's folder)

| File | Contents |
|---|---|
| `current-state.md` | Screen inventory + scorecards |
| `patterns-radar.md` | External patterns evaluated with the §1.1 entry format |
| `recommendations.md` | The top 5, in plain language. Each has: the user problem, why this beats the alternative, a flagged first step, rollback, verification, and whether a visual baseline needs re-capturing |
| `DEFERRED.md` | What the session didn't cover |

**Optional follow-up (not in the hour):** mock the top recommendations as frames using
the Figma connector, after a human picks which ones.

---

## 2. Session 1 — Rider app

- **Scope:** every screen in `rider-app/app/`. Priority journey: `index` → `search-destination` → `ride-options` → `confirm-pickup` → `payment-confirm` → `driver-arriving` → `driver-arrived` → `ride-in-progress` → `ride-completed`. Then wallet, safety-hub, support, lost-and-found and settings.
- **Questions:**
  - Can a first-time rider book in the fewest taps with **no surprise** on price, pickup spot or wait time?
  - Is the surge and fee breakdown clear before confirming?
  - Is it clear what happens when no driver is found or the card is declined?
  - Is safety reachable in one tap mid-trip without alarming the rider?
  - Is the receipt understandable?
  - Does it work in cold weather and at night?
- **Industry comparison:** booking flows, pickup-spot guidance, trip sharing, rating/tipping and receipts on Uber/Lyft/Grab.

## 3. Session 2 — Driver app

- **Scope:** `driver-app/app/` including `driver/(tabs)`, the offer card, `ActiveRidePanel`, `destination-mode`, `payout`, `tax-documents`, `documents`, `appeal` and `account-deactivated`.
- **Questions:**
  - Can the offer card be read at a glance while driving (fare, pickup distance, destination, timer)?
  - Are in-trip controls large, few and safe to use at arm's length?
  - Are earnings transparent per trip and per week?
  - Is document expiry flagged well before it blocks going online?
  - Are deactivation and appeal screens fair and clear?
  - Is all copy contractor-safe?
- **Industry comparison:** driver offer cards, earnings screens and heatmaps (without pressure to go online), and driver safety features.

## 4. Session 3 — Admin / ops console

- **Scope:** `admin-dashboard/src/app/dashboard/**`. Priority: monitoring and live rides, driver verification queue, rides, refunds/disputes, SOS/safety, support tickets, settings/flags, staff and RBAC.
- **Questions:**
  - Can an operator handle an SOS or a refund in the fewest steps, with no ambiguity?
  - Are destructive actions confirmed and reversible?
  - Are lists dense but scannable, with keyboard support?
  - Does every page follow Quiet Console?
  - Where does alert fatigue or noise creep in?
- **Evidence:** the 6 visual baselines. Any recommendation that changes a baselined page must note that a human needs to re-seed that baseline.
- **Industry comparison:** ops consoles (incident tools, marketplace ops dashboards), data-dense table patterns.

## 5. Session 4 — Corporate portal & public touchpoints

- **Scope:**
  - `admin-dashboard/src/app/{company-signup,company-login,company-portal}/`
  - `admin-dashboard/src/app/track/[rideId]/` — the trusted-contact trip-tracking link
  - rider receipts: `backend/utils/email_receipt.py`, `backend/utils/receipt_pdf.py`
  - push/SMS/email copy, reusing `spinr-notification-ux-reviewer` rules
- **Questions:**
  - Can a company admin sign up, pass KYB, fund the wallet and add riders without calling support?
  - Are spend reports clear?
  - Does the tracking page reassure a worried contact without exposing PII?
  - Do receipts show every line item and tax clearly?
  - Is the notification copy clear, actionable and deduplicated?
- **Industry comparison:** Uber for Business / Lyft Business onboarding and reporting, trip-share pages, receipt design.

## 6. Session 5 — Design system, brand, accessibility & content

- **Scope:** `shared/theme/`, `shared/components/`, `docs/design/`, `.claude/context/brand-spinr.md`, the admin design tokens (per `spinr-admin-design-system`), and `docs/known-forks.md` (component forks).
- **Questions:**
  - Is there one token source, and does every surface use it? Where are the hard-coded colours and sizes?
  - Which component forks have drifted?
  - Is the type scale consistent, and does it support dynamic type?
  - Do colours meet contrast in light and dark mode?
  - Is motion reduced when the OS setting asks for it?
  - Is there one icon set?
  - Is there a voice-and-tone guide for copy?
  - Is the app ready for French (string extraction)?
  - Is the WCAG 2.2 delta (target size, focus appearance, dragging) addressed?
  - Should tokens sync Figma variables ↔ code?
- **Industry comparison:** Uber Base, Material 3 tokens, Apple HIG; design-token pipelines.

---

## 7. Kickoff prompts (paste one per new session; choose Fable)

**Session 1 — Rider app**
```text
Run Spinr design session 1 (Rider app) from
docs/audit/clean-sheet-prompt/design-sessions.md. You are the Orchestrator. Follow §1
of that file exactly and §2 (Session 1). Recommend only — no code, token, config, or
data changes. Launch lanes C, R, and A in ONE message as background Agent calls with
the listed models. Lanes return text; only you write files, into
docs/audit/clean-sheet/design/rider-app/. Stop waiting at minute 35. Finish by
committing, pushing this session's branch, opening a draft PR with
.github/pull_request_template.md, and reporting to me in plain language: the top 5
recommendations, why each beats its alternative, what needs my decision, and what was
NOT verified (say which claims were reasoned from code, not screenshotted).
```

**Session 2 — Driver app**
```text
Run Spinr design session 2 (Driver app) from
docs/audit/clean-sheet-prompt/design-sessions.md. You are the Orchestrator. Follow §1
of that file exactly and §3 (Session 2). Recommend only — no code, token, config, or
data changes. Launch lanes C, R, and A in ONE message as background Agent calls with
the listed models. Lanes return text; only you write files, into
docs/audit/clean-sheet/design/driver-app/. Stop waiting at minute 35. Finish by
committing, pushing this session's branch, opening a draft PR with
.github/pull_request_template.md, and reporting to me in plain language: the top 5
recommendations, why each beats its alternative, what needs my decision, and what was
NOT verified (say which claims were reasoned from code, not screenshotted).
```

**Session 3 — Admin / ops console**
```text
Run Spinr design session 3 (Admin / ops console) from
docs/audit/clean-sheet-prompt/design-sessions.md. You are the Orchestrator. Follow §1
of that file exactly and §4 (Session 3). Recommend only — no code, token, config, or
data changes. Launch lanes C, R, and A in ONE message as background Agent calls with
the listed models. Lanes return text; only you write files, into
docs/audit/clean-sheet/design/admin-console/. Stop waiting at minute 35. Finish by
committing, pushing this session's branch, opening a draft PR with
.github/pull_request_template.md, and reporting to me in plain language: the top 5
recommendations, why each beats its alternative, what needs my decision, which
visual baselines would need re-capturing, and what was NOT verified.
```

**Session 4 — Corporate portal & public touchpoints**
```text
Run Spinr design session 4 (Corporate portal & public touchpoints) from
docs/audit/clean-sheet-prompt/design-sessions.md. You are the Orchestrator. Follow §1
of that file exactly and §5 (Session 4). Recommend only — no code, token, config, or
data changes. Launch lanes C, R, and A in ONE message as background Agent calls with
the listed models. Lanes return text; only you write files, into
docs/audit/clean-sheet/design/corporate-public/. Stop waiting at minute 35. Finish by
committing, pushing this session's branch, opening a draft PR with
.github/pull_request_template.md, and reporting to me in plain language: the top 5
recommendations, why each beats its alternative, what needs my decision, and what was
NOT verified.
```

**Session 5 — Design system, brand, accessibility & content**
```text
Run Spinr design session 5 (Design system, brand, accessibility & content) from
docs/audit/clean-sheet-prompt/design-sessions.md. You are the Orchestrator. Follow §1
of that file exactly and §6 (Session 5). Recommend only — no code, token, config, or
data changes. Launch lanes C, R, and A in ONE message as background Agent calls with
the listed models. Lanes return text; only you write files, into
docs/audit/clean-sheet/design/system-brand-a11y/. Stop waiting at minute 35. Finish
by committing, pushing this session's branch, opening a draft PR with
.github/pull_request_template.md, and reporting to me in plain language: the top 5
recommendations, why each beats its alternative, what needs my decision, and what was
NOT verified.
```
