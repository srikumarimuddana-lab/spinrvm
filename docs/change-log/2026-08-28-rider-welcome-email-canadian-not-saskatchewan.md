# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-28 |
| Author | Claude (session, on request) |
| Surface(s) | backend |
| Domain (Sentry tag) | rides |
| PR / commit link | branch `claude/driver-rider-emails-messages-pdf-bio6az` |
| Related issue or gap ID | — (ad hoc brand-tone request) |

## 1. Issue / gap identified

The rider welcome email described Spinr as "Saskatchewan-built." Requester
wants email copy to lead with "Canadian," going forward, to read as
distinctly non-US and not tied to one province.

## 2. Root cause

Not a bug — a brand-positioning decision. Confirmed scope with the requester
via `AskUserQuestion` before touching anything, because "Saskatchewan"
appears in ~25 places across the repo and most of them are NOT brand tone:

- Regulatory fact citations (`.claude/context/regulatory-sk.md`, the account-
  deletion email's "Saskatchewan Transportation Act" retention citation) —
  these are legally accurate statements, not marketing language, and stay
  unchanged.
- Engineering rationale in code comments (`America/Regina` timezone-no-DST
  reasoning, trip-duration margins, map default center, GPS speed caps) —
  these explain a real technical constraint, unrelated to customer-facing
  tone, and stay unchanged.
- `CLAUDE.md`'s own project-overview line — explicitly out of scope per the
  requester (chose "email copy only," not the broader options offered).

Confirmed scope: **email copy only** (not rider-app UI, app store listing,
support FAQ, or AI assistant copy — those were offered as options and
declined). Confirmed the "FAIR both ways" phrase is **tone guidance**, not a
tagline to insert verbatim — so no new slogan text was added, only the
existing "Saskatchewan-built" → "Canadian-built" swap, since the surrounding
paragraph already carries the fairness-both-ways substance (0% driver
commission + upfront transparent pricing + itemized tax lines).

## 3. Fix / remediation

`send_welcome_email` in `utils/rider_emails.py`: replaced "Saskatchewan-built"
with "Canadian-built" in the one paragraph that used it. No other paragraph,
subject, or heading in this email changed.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to this one string**, confirmed by a repo-wide
  grep for `Saskatchewan-built`/`Saskatchewan-first` before editing anything
  (see conversation). Within actual EMAIL copy specifically, only one other
  Saskatchewan mention exists — the account-deletion notice's retention-
  policy citation (`send_account_deletion_notice`, "...because the
  Saskatchewan Transportation Act and Canadian tax rules require us to hold
  them for seven years") — left untouched because it is a factual legal
  citation, not brand tone; rewording it to omit "Saskatchewan" would make a
  compliance disclosure less accurate, not more Canadian.
- One test asserted on the exact literal string: `backend/tests/
  test_rider_emails_app_name.py::test_welcome_body_mentions_the_configured_
  app_name` checked for `"Northern Rides is Saskatchewan-built"` — updated
  to `"Northern Rides is Canadian-built"` in the same commit so it doesn't
  start failing.
- Also updated `backend/scripts/preview_notification_templates.py` (QA
  preview tool, not production) to keep its sample copy in sync.
- No state, table, money path, or other sender touched.

## 5. User-experience effect

- **Rider-facing.** Every newly-registered rider's welcome email now says
  "Canadian-built" instead of "Saskatchewan-built." One-time email at
  profile setup, not visible mid-session.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/rider_emails.py` | `send_welcome_email`: "Saskatchewan-built" → "Canadian-built" | Requested brand-tone change |
| `backend/tests/test_rider_emails_app_name.py` | Updated the matching literal-string assertion | Keep the existing test accurate, not broken by the copy change |
| `backend/scripts/preview_notification_templates.py` | Synced sample copy | Keep QA preview tool accurate |

## 7. Before / after

```
# Before
f"{company.app_name} is Saskatchewan-built and takes 0% commission — every dollar of the fare "
"goes to your driver. GST and PST are shown as separate line items on your receipt.",
```

```
# After
f"{company.app_name} is Canadian-built and takes 0% commission — every dollar of the fare "
"goes to your driver. GST and PST are shown as separate line items on your receipt.",
```

## 8. Rollback plan

Pure copy change: `git revert` this commit. No data, migration, or money
path involved.

## 9. Verification performed

- [ ] Automated tests run — **not run** (this sandbox has no network access
      to install backend Python dependencies; confirmed earlier in this
      session via a failed pip install and a direct 403 from pypi.org).
      Verified by reading instead: the only test asserting on this literal
      string was updated in the same commit; no other test in the repo
      references "Saskatchewan-built" (grepped before committing).
- [x] Manual repro — regenerated both preview PDFs via
      `preview_notification_templates.py` and visually confirmed the new
      wording renders correctly in the branded email shell.
- [x] Blast-radius grep performed — repo-wide grep for `Saskatchewan-built`,
      `Saskatchewan-first`, and plain `Saskatchewan` inside `backend/utils/`
      and every `routes/*email*` file, before editing, to separate brand-tone
      copy from regulatory/technical text.
- [x] Reviewed against CLAUDE.md conventions — treated this as the kind of
      ambiguous, wide-blast-radius decision "Escalate, don't silently ship"
      calls for; confirmed scope and tagline literalness with the requester
      via `AskUserQuestion` before editing.
- [ ] Feature-flagged — not applicable; one-time onboarding copy, not a
      toggleable behavior.

## 10. What was NOT verified

- Not rendered through a real SES/Resend provider or viewed in a real mail
  client — only via the same headless-Chromium preview used earlier in this
  session.
- No production test run (`pytest`) — see above.
- Did not change the account-deletion email's "Saskatchewan Transportation
  Act" mention, the rider-app UI tile, app store listing, support FAQ, AI
  assistant copy, or CLAUDE.md's own project description — all explicitly
  out of scope per the requester's answer ("email copy only"). If the intent
  turns out to be broader, that's a separate, larger follow-up.

## Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`)
- [x] Blast radius is stated, not assumed (one string in production code,
      one matching test, one preview-tool mirror)
- [x] No silent behavior change — UX field filled in above; scope explicitly
      confirmed with the requester rather than assumed
