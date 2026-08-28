# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-28 |
| Author | Claude Code (mkkreddy52@gmail.com) |
| Surface(s) | driver-app |
| Domain (Sentry tag) | auth |
| PR / commit link | branch `claude/driver-notification-area-boost-usaor4` |
| Related issue or gap ID | Product decision this session; reverses the UI half of ACTION_ITEMS.md A41 / `docs/change-log/2026-08-20-explicit-signup-consent-checkbox.md` |

> **Read §4 before merging.** This reverses, by product decision, the UI
> mechanism added 8 days ago to close a documented consent-evidence gap. The
> recorded consent data is unchanged; the *gesture* is now implied by
> continuing rather than an affirmative tick. That is a legal-sufficiency
> judgement, not an engineering one, and it should be confirmed by whoever
> owns A41 before this ships.

## 1. Issue / gap identified

The driver login screen asked for an explicit "I agree to Spinr's Terms of
Service and Privacy Policy" tick before signup, and greeted everyone with
"Welcome back". Product judgement: signing in is itself agreement, the
checkbox is friction on an auth screen, and the greeting was wrong for new
drivers.

## 2. Root cause

Not a defect — a design decision being revisited. The checkbox was added
2026-08-20 to close the consent-evidence gap in ACTION_ITEMS.md A41 (backed by
`docs/audit/2026-08-20-legacy-consent-legal-sufficiency-factsheet.md` §8),
which found the app had a stored policy but no recorded user gesture. The
2026-08-27 revision then stopped it gating the button. This change completes
the arc back to a disclosure, but — unlike the pre-A41 state — with the
backend consent record kept intact.

## 3. Fix / remediation

- Greeting is now "Welcome to Spinr 👋" for everyone (was conditional on
  `hasAuthenticatedBefore`, before that unconditional "Welcome back").
- The checkbox is gone. In its place, the pre-existing `login.termsPrefix`
  copy — "By continuing, you agree to our **Terms of Service** and **Privacy
  Policy**" (already present, and already translated in `fr.json`) — sits
  under the button, with both links still individually tappable.
- The disclosure is shown to everyone, not just first login: the sentence has
  to be visible at the moment of the tap it describes. A repeated *checkbox*
  was confusing; a repeated sentence is not.
- `handleSendCode` now always carries `consentAccepted: 'true'` to `otp.tsx`,
  because reaching that line means the driver tapped the button beneath the
  disclosure. `otp.tsx` still sends it on `POST /auth/verify-otp`.

## 4. Risk & impact on existing functionality

- **What is unchanged, and this is the important part:** the backend still
  refuses to create a new account without `consent_accepted`
  (`routes/auth.py`'s `verify_otp`, `errors.auth.consent_required`), and still
  stamps `consent_version` and `consent_accepted_at`. The row written for a
  new driver is byte-identical to what the checkbox produced. No backend,
  schema, or migration change is in this diff.
- **What changed legally:** the gesture behind that stamp. Before, a
  discrete affirmative act (ticking a box) distinguishable from the act of
  signing in; now, clickwrap — continuing under a disclosure. Clickwrap is
  the industry-standard pattern and is generally enforceable when the notice
  is conspicuous and adjacent to the action, which it is here. But A41 was
  opened precisely because "no recorded evidence of a user gesture" was
  judged insufficient, and this weakens (does not remove) the strength of
  that evidence. **Flagged for the A41 owner / legal, not decided here.**
- **Blast radius: driver-app login screen + its tests.** Consumers checked:
  `otp.tsx` (reads the `consentAccepted` route param — still sent, now always
  `'true'`; its inline consent card is untouched and still handles a
  `consent_required` rejection, which now only reaches an older client),
  `e2e/fixtures.ts` (clicked the checkbox — updated), and two test files
  (updated/rewritten). `HAS_AUTHENTICATED_BEFORE_KEY` is still exported and
  still written by `otp.tsx`; only `login.tsx`'s read of it is gone.
- **Not changed: rider-app.** It has the same checkbox from the same
  2026-08-20 change. Only the driver app was asked for, so the two surfaces
  are now inconsistent — deliberate, and called out here rather than
  silently widened.
- No change to OTP send/verify, rate limiting, lockout, navigation, or any
  ride/money/dispatch path.

## 5. User-experience effect

- **Driver-facing, login screen, immediately visible.** New drivers no longer
  tick a box; everyone sees "Welcome to Spinr 👋" and the disclosure under the
  button. One fewer required tap to sign up.
- Not visible mid-session — pre-authentication screen only.
- Copy reviewed against the customer-centric tone standard: plain, specific,
  and it no longer asserts something false about the reader.
- Accessibility: both legal links remain individually reachable with
  `accessibilityRole="link"`, deliberately not wrapped in an outer touchable
  (a previous `spinr-accessibility-reviewer` pass flagged that collapsing
  them into one node is a blocker, since this screen is the only pre-account
  path to those documents). A regression test now pins exactly that.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/app/login.tsx` | Greeting unconditional; checkbox → clickwrap disclosure; `consentAccepted` always `'true'`; removed the now-orphaned `hasAuthenticatedBefore` state/effect and `consentRow` style | The requested change |
| `driver-app/__tests__/screens/loginConsentCheckbox.test.tsx` → `loginClickwrapConsent.test.tsx` | Rewritten: asserts no checkbox renders, the disclosure and both links are present and separately reachable, each link opens its document, continue carries `consentAccepted:'true'` | The old file pinned the behaviour that was just removed |
| `driver-app/__tests__/app/loginScreen.test.tsx` | Expected param `'false'` → `'true'`; stale header reference updated | Matches the new gesture |
| `driver-app/e2e/fixtures.ts` | Dropped the `consent-checkbox` click from the login helper | That test ID no longer exists — every E2E spec using this helper would fail |

## 7. Before / after

```tsx
// Before — affirmative tick, shown only on a device that had never signed in
{!hasAuthenticatedBefore && (
  <TouchableOpacity testID="consent-checkbox" accessibilityRole="checkbox" ...>
    <Ionicons name={consentAccepted ? 'checkbox' : 'square-outline'} ... />
  </TouchableOpacity>
)}
// ...
params: { ..., consentAccepted: String(consentAccepted) }   // could be 'false'
```

```tsx
// After — clickwrap disclosure, always shown, links still individually tappable
<Text style={styles.termsText}>
  {t('login.termsPrefix')} <Text accessibilityRole="link" ...>Terms of Service</Text>
  {' '}and{' '} <Text accessibilityRole="link" ...>Privacy Policy</Text>
</Text>
// ...
params: { ..., consentAccepted: 'true' }   // the tap IS the gesture
```

## 8. Rollback plan

`git revert` of this commit restores the checkbox, its tests, and the E2E
click in one step — no migration, no flag, no persisted state, and no live
data to repair, since the consent rows written under either mechanism are
identical. A driver who signed up during the clickwrap window keeps a valid
`consent_version` / `consent_accepted_at`; nothing needs backfilling on
revert. If legal instead wants the affirmative gesture back *without* losing
the greeting fix, revert this commit and re-apply the one-line greeting
change.

## 9. Verification performed

- Traced the consent path end to end: `login.tsx` → route param → `otp.tsx`'s
  `POST /auth/verify-otp` `consent_accepted` → `verify_otp`'s new-user branch.
  Confirmed the backend gate and the `consent_version` stamp are untouched by
  this diff.
- Grepped every consumer of what was removed: `consent-checkbox` testID,
  `consentAccepted`, `consentPrefix`, `hasAuthenticatedBefore`,
  `HAS_AUTHENTICATED_BEFORE_KEY`, `consentRow`. Every hit is either updated
  here or intentionally left (`otp.tsx`'s writer + inline card; `consentPrefix`
  now unused in driver-app but still used by `otp.tsx`'s card).
- Confirmed `login.termsPrefix` already exists in both `en.json` and
  `fr.json`, so no untranslated string ships.
- Checked JSX balance in the edited file and re-read the full rendered block.

## 10. What was NOT verified

- **Nothing was executed: no build, typecheck, lint, Jest, or Playwright.**
  `driver-app/node_modules` is not installed and npm is blocked by this
  environment's network policy (403 via the agent proxy). The rewritten test
  file has never been run — its queries (`UNSAFE_queryAllByProps`,
  `getByText(/login\.termsPrefix/)`) are written against the file's existing
  conventions but are unproven. **CI is the first execution, and a rewritten
  test file is exactly where an unrun assertion is most likely to be wrong.**
- No screenshot or device check of the new layout; driver-app has no
  visual-regression tooling (standing gap, `ACTION_ITEMS.md`). The disclosure
  now occupies less vertical space than the checkbox row — reasoned about,
  not seen.
- The legal sufficiency of clickwrap versus an affirmative tick for PIPEDA
  meaningful consent was **not** assessed. That is the open question in §4.
- rider-app's identical checkbox was not touched or tested.
