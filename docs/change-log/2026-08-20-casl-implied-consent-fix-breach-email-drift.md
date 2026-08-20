# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-20 |
| Author | Content/UX review (Claude Code, via `/legal-check` covering the remaining 11 corporate/privacy-infra/website legal drafts — first full-repo coverage of all 19 legal documents) |
| Surface(s) | docs only — one pre-publication legal draft, one internal-only template, one internal runbook |
| Domain (Sentry tag) | payments/compliance (CASL) + safety (breach response) |
| PR / commit link | see PR on branch `claude/spinr-faq-review-uodytp` |
| Related issue or gap ID | Follow-up from a `/legal-check` sweep across the remaining 11 unrun legal drafts |

## 1. Issue / gap identified

Running `/legal-check` across the last 11 legal documents not yet covered (corporate MSA/DPA, contractor agreement, subprocessor list, cookie policy, data retention schedule, CASL disclosure, breach notification template, website ToU, trademark notice, careers privacy notice) surfaced two real, fixable issues:

1. **`casl-marketing-consent-disclosure.md`** claimed Spinr relies on CASL's implied-consent basis "for a limited time after you've had a business relationship" — no such mechanism exists in the codebase. `backend/services/marketing_consent.py` is pure explicit-opt-in by design.
2. **Contact-email drift** between `docs/runbooks/data-breach.md`'s inline user-notification template (`privacy@spinr.ca`) and the standalone `docs/legal/breach-notification-letter-template.md` (`security@spinr.ca`) — the two governing documents for the same breach-response flow disagreed with each other.

The other 9 documents were checked and found to have no code-checkable gate that has newly closed — all correctly remain blocked on counsel review, business decisions (jurisdiction of incorporation, notice periods, insurance limits, ATS vendor, CIPO trademark status), or facts this repo genuinely cannot answer (no marketing website exists in this monorepo at all, so `website-terms-of-use.md`/`cookie-policy.md` stay open on locating that surface).

## 2. Root cause

- The CASL implied-consent paragraph was drafted aspirationally, describing a common CASL compliance pattern (implied consent after a business relationship) without being checked against what `marketing_consent.py` actually enforces.
- The email-address drift happened because the runbook's inline template and the standalone template are two separate copies of similar content that were never cross-checked against each other or against `privacy-policy.md`'s stated convention for which inbox handles incident reports.

## 3. Fix / remediation

**`casl-marketing-consent-disclosure.md`**: rewrote the "WHAT COUNTS AS CONSENT" section to state plainly that Spinr does not rely on implied consent for any marketing message — every message is opt-in. Also confirmed and closed the remaining mechanics (opt-in default-off, one-click unsubscribe, SMS STOP, mandatory sender/address footer) against real code.

**`docs/runbooks/data-breach.md`**: aligned the inline §4c user-notification template's contact address to `security@spinr.ca`, matching `docs/legal/breach-notification-letter-template.md` and `privacy-policy.md`'s own stated convention that `security@spinr.ca` is for incident reports specifically.

**`legal-text-publication-checklist.md`**: updated both documents' rows to record the fixes.

No other draft required an edit — the remaining 9 were re-confirmed accurate as currently blocked.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to 2 draft/template markdown files, 1 internal runbook, 1 checklist file, and this change-log entry.** No application code, schema, or live content touched. `casl-marketing-consent-disclosure.md` is not published to `legal_documents`; `breach-notification-letter-template.md` is explicitly an internal-only template, never public; `data-breach.md` is an internal incident-response runbook, not customer-facing until an actual breach occurs.
- **`docs/runbooks/data-breach.md`** is the one file in this change that isn't purely a draft — it's an active operational runbook. Blast radius check: grepped the repo for other references to this runbook's §4c template — no other file quotes or duplicates that inline template text, so the edit is isolated to the one copy.
- **No user-facing effect today** — none of these documents are live/published; the runbook only matters during an actual security incident, which has not occurred as a result of this change.

## 5. User-experience effect

None today. If a real breach occurs in the future, the corrected runbook copy means the notification email now points to the address `privacy-policy.md` itself says is correct for incident reports — this is a correctness fix for a future incident, not a change to any currently-running flow.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `docs/legal/casl-marketing-consent-disclosure.md` | Rewrote implied-consent claim to match the real pure-opt-in system; closed 4 other mechanics as confirmed accurate | Remove a CASL-compliance overclaim before it could reach counsel or publication |
| `docs/runbooks/data-breach.md` | Changed §4c template's contact email from `privacy@spinr.ca` to `security@spinr.ca` | Resolve drift with the standalone breach-notification template and match `privacy-policy.md`'s stated convention |
| `docs/legal/breach-notification-letter-template.md` | Added a pre-use note documenting the drift fix | Record why the runbook was changed to match this file |
| `docs/legal/legal-text-publication-checklist.md` | Updated 2 rows with the fixes | Keep the canonical tracking table accurate per its own process rule |
| `docs/change-log/2026-08-20-casl-implied-consent-fix-breach-email-drift.md` | New change-log entry | Standard practice for this repo's live-testing-era content fixes |

## 7. Before / after

```
# Before — casl-marketing-consent-disclosure.md
We ask for your express consent to marketing messages — for example, an
opt-in checkbox during signup or in Settings, not a pre-checked box. If you
don't opt in, we don't send marketing messages, though we may still rely on
implied consent for a limited time after you've had a business relationship
with us (for example, shortly after your last completed ride), consistent
with CASL's implied-consent rules — we still let you opt out at any time
even during that period.

# After
We ask for your express consent to marketing messages — for example, an
opt-in checkbox during signup or in Settings, not a pre-checked box. If you
don't opt in, we don't send marketing messages. Spinr does not rely on
CASL's implied-consent basis for any marketing message — every marketing
message you receive is because you actively opted in, and you can opt out
at any time.
```

```
# Before — docs/runbooks/data-breach.md §4c
If you have questions, contact privacy@spinr.ca.

# After
If you have questions, contact security@spinr.ca.
```

## 8. Rollback plan

`git-revert-safe` — markdown content changes only. The runbook edit is a single email-address string swap in a document that only takes effect during a future incident; reverting it before any incident occurs has zero operational impact.

## 9. Verification performed

- Confirmed `backend/services/marketing_consent.py`'s `is_eligible()` has no time-decay or business-relationship fallback path — grepped for `implied`, `business relationship`, `grace.*period` tied to marketing sends, found none; migration 190's own comment explicitly states the pure-opt-in design intent.
- Confirmed `security@spinr.ca` is a real, monitored inbox (`SECURITY.md`, `docs/dpa-register.md`) and that `privacy-policy.md:116` names it specifically for incident reports, distinct from `privacy@spinr.ca`'s general rights/questions purpose.
- Grepped the repo for other copies of the runbook's §4c template text — none found, confirming the edit is isolated.
- Re-confirmed the 4 other CASL mechanics (opt-in default, unsubscribe, SMS STOP, footer) against `backend/routes/marketing.py`, `backend/routes/webhooks.py`, and `backend/utils/marketing_email.py`.

**What was NOT verified**: whether `security@spinr.ca` is monitored on a schedule fast enough to be meaningful during a live incident — confirmed the inbox exists and is referenced as the incident-report channel elsewhere, but did not verify staffing/on-call coverage, which is an operational fact outside what a repo grep can answer. Also not verified: the other 9 documents checked this pass received no code changes and their "no change needed" verdicts rely on the same read-only grep-based verification method as every other `/legal-check` run this session — no live database or external registry (CIPO, LogRocket's DPA) was queried.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed — including the one active-runbook file in this change
- [x] No silent behavior change to an already-shipped flow: the runbook only activates during a future incident, not currently in use
