# /pr — Create a spinr Pull Request

Analyse all commits since branching and open a complete PR.

## Steps

1. Check branch
   git branch --show-current
   If main or develop: STOP — cannot PR from protected branches.

2. Analyse diff
   git log develop..HEAD --oneline
   git diff develop...HEAD --stat

3. Target branch: develop (features/fixes) or main (hotfix only)

4. Draft PR body:

---
## What
One sentence — what does this PR deliver?

## Why
Business or technical reason. Reference the ticket.

## How
Key implementation decisions and approach.

## spinr Domain Impact
- [ ] Backend API (Python)
- [ ] Rider app (Expo)
- [ ] Driver app (Expo)
- [ ] Frontend web (Next.js)
- [ ] Admin dashboard
- [ ] Matching engine
- [ ] Payments / Stripe
- [ ] Safety / SOS
- [ ] Auth / Supabase
- [ ] Driver onboarding
- [ ] Notifications
- [ ] Surge pricing
- [ ] Trip state machine

## Compliance & Security
Affects any of the following? (check all that apply)
- [ ] PII or personal data (PIPEDA)
- [ ] Payment processing or fare calculation
- [ ] Location data or data residency
- [ ] Authentication or Supabase RLS
- [ ] Insurance period logic
- [ ] Emergency / SOS features
If any checked: requires human security review before merge.

## Testing
What was tested, how, environment, Stripe test mode confirmed.

## DB / Schema Changes
- [ ] No schema changes
- [ ] Supabase migration included at: ___

## Checklist
- [ ] Tests added/updated
- [ ] No secrets committed
- [ ] No PII in logs
- [ ] CLAUDE.md conventions followed
- [ ] ci.yml not modified
- [ ] Docs updated if behaviour changed
---

5. Create: gh pr create --base develop --title "<type>(scope): subject" --body "<above>"

6. Report: PR URL, domains touched, any compliance flags
   Do not merge — human review required.
