# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-30 |
| Author | Claude Code (session: rider-textbox-visibility) |
| Surface(s) | rider-app |
| Domain (Sentry tag) | auth |
| PR / commit link | branch `claude/rider-textbox-visibility-d4w9lv` |
| Related issue or gap ID | Owner directive: make rider match the driver app. Mirrors `2026-08-28-driver-login-clickwrap-consent.md` (commit `7ca0ddf`); reverses the rider half of `2026-08-20-explicit-signup-consent-checkbox.md` (ACTION_ITEMS.md A41) |

## 1. Issue / gap identified

The driver app replaced its login consent checkbox with a clickwrap disclosure
on 2026-08-28. The rider app kept its checkbox, so the two auth screens asked
for consent in different ways. The rider screen also greeted **every** visitor
with "Welcome back", including someone creating an account for the first time.

## 2. Root cause

Not a defect — the 2026-08-28 driver change was deliberately scoped to the
driver app ("rider-app keeps its checkbox; only the driver app was in scope").
This is the follow-up that closes the gap, on the owner's instruction.

## 3. Fix / remediation

`rider-app/app/login.tsx` now mirrors `driver-app/app/login.tsx`:

- the checkbox, its `consentAccepted` state, the `hasAuthenticatedBefore`
  heuristic and its `AsyncStorage` read are gone;
- a "By continuing, you agree to our Terms of Service and Privacy Policy"
  disclosure sits under the button, always shown — tapping **Send Verification
  Code** is the acceptance gesture;
- the route param to `/otp` is now always `consentAccepted: 'true'`, because
  reaching that line means the rider tapped the button under the disclosure;
- the greeting is "Welcome to Spinr" instead of an unconditional "Welcome back".

`HAS_AUTHENTICATED_BEFORE_KEY` is **kept and still exported** — `otp.tsx` still
writes it on every successful auth, and deleting it would silently discard the
signal on every device that already has it. Same call the driver app made.

## 4. Risk & impact on existing functionality

- **The backend contract is untouched.** `routes/auth.py`'s `verify_otp` still
  refuses a brand-new account without `consent_accepted` and still stamps
  `consent_version` / `consent_accepted_at`. What is *recorded* as consent
  evidence is byte-for-byte identical; only the gesture that produces it moved.
- **This does not weaken consent for returning riders, because there was never
  anything to weaken:** the consent check lives in the `else` (new-account)
  branch of `verify_otp`. A returning rider takes the `existing_user` branch,
  which never reads `consent_accepted`. Gating the login screen on a checkbox
  was a client-side decision with no server-side counterpart for that user.
- **`otp.tsx`'s inline consent card is untouched and still the safety net.**
  If the backend ever returns `consent_required` — an older build that sent
  `'false'`, or a future re-consent flow after a `CONSENT_VERSION` bump — the
  OTP screen still prompts inline. Its tests are unchanged and still valid.
- **Blast radius: one screen.** Greps: `consentAccepted` (now only the route
  param), `hasAuthenticatedBefore`, `consentRow`, `AsyncStorage` and `useEffect`
  all return zero in `login.tsx`; `Ionicons`, `TouchableOpacity` and `loading`
  are still used elsewhere in the file, so no import is orphaned.
- No ride, payment, dispatch or PII path touched. No migration. No API change.

## 5. User-experience effect

- **Rider-facing, on the login screen only.** Not visible mid-session.
- A first-time rider: no checkbox to tick; the disclosure is visible at the
  moment of the tap it describes; greeting no longer claims they have been
  here before.
- A returning rider on a fresh device (or after clearing storage): previously
  saw a checkbox that had no legal effect for them. Now sees a one-line
  restatement, which is harmless.
- Both legal links remain individually tappable and individually reachable by
  VoiceOver/TalkBack — this screen is the only pre-account path to those
  documents, so collapsing them into one node would be an accessibility
  blocker (the reason the driver version avoids an outer touchable).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/app/login.tsx` | Checkbox → clickwrap disclosure; consent + first-login state removed; `consentAccepted: 'true'`; greeting flattened; orphaned imports and `consentRow` style dropped | Match the driver app |
| `rider-app/__tests__/loginConsentCheckbox.test.tsx` → `loginClickwrapConsent.test.tsx` | Rewritten to pin the clickwrap: no checkbox, disclosure present, both links reachable and separately navigable, `consentAccepted:'true'` carried, phone validity alone gates continue, greeting | The old file pinned the behaviour being removed |
| `rider-app/__tests__/loginScreen.test.tsx` | Dropped the `toggleConsent` helper and its 5 call sites; corrected two stale comments | Those toggles were setup, never load-bearing — the tests assert toast branches and loading state |

## 7. Before / after

```tsx
// Before
{!hasAuthenticatedBefore && (
  <View style={styles.terms}>
    <View style={styles.consentRow} accessible={false}>
      <TouchableOpacity accessibilityRole="checkbox" onPress={() => setConsentAccepted(c => !c)}>
        <Ionicons name={consentAccepted ? 'checkbox' : 'square-outline'} … />
      </TouchableOpacity>
      <Text>I agree to Spinr&apos;s <Text …>Terms of Service</Text> and <Text …>Privacy Policy</Text></Text>
    </View>
  </View>
)}
// …and: params: { …, consentAccepted: String(consentAccepted) }
```

```tsx
// After
<View style={styles.terms}>
  <Text style={styles.termsText}>
    By continuing, you agree to our <Text …>Terms of Service</Text> and <Text …>Privacy Policy</Text>
  </Text>
</View>
// …and: params: { …, consentAccepted: 'true' }
```

## 8. Rollback plan

`git revert` is a complete rollback — presentation-only, client-side, no writes,
no migration, no API change. Not feature-flagged: it is a directed UI change,
and shipping it dark would leave the two apps inconsistent for the flag-off
cohort, which is the thing being fixed. Reaching riders needs an OTA/EAS update
like any other rider-app change, so rollback uses the same mechanism as rollout.

## 9. Verification performed

- [x] Ported from the driver implementation by reading commit `7ca0ddf`'s actual
      diff, not from memory — structure, comments and accessibility handling
      match, adapted to this file's conventions (literal strings, since rider
      `login.tsx` does not use `t()` here, unlike the driver screen).
- [x] Confirmed against `backend/routes/auth.py` that `consent_accepted` is read
      only in the new-account branch, so no returning-rider behaviour changes.
- [x] Orphan sweep after the removal (listed in §4) — zero stragglers, no
      unused imports.
- [x] Swept every rider test and the e2e fixtures for the removed behaviour;
      updated the two affected files. `otpScreen.test.tsx`'s inline-consent
      tests were reviewed and deliberately left alone (§4).
- [x] `tsc --noEmit --noResolve` parse pass clean on all three changed files —
      which caught a real error: the removed `{!hasAuthenticatedBefore && (`
      left a dangling `)}`.
- [ ] **Jest NOT run; no typecheck, lint or production build.** npm and yarn
      registries are blocked by this session's egress policy (403 on CONNECT),
      so rider-app dependencies could not be installed. Every test change here
      is committed unexecuted.
- [ ] Manual check on device/simulator — not performed.

## 10. What was NOT verified

- Nothing was executed. The rewritten test file is new code that has never run;
  its `findAllByProps` traversals are modelled on the file it replaces and on
  `otpScreen.test.tsx`, but that is reasoning, not a green run.
- **The affirmative-gesture question is still open and is not mine to close.**
  The 2026-08-20 checkbox existed to close a consent-evidence gap
  (ACTION_ITEMS.md A41); the driver change log flagged for the A41 owner
  whether a clickwrap tap is an adequate substitute, and that flag now covers
  both apps. This commit implements a product instruction — it is not a legal
  sign-off, and no lawyer has reviewed either app's wording.
- No screenshot. rider-app has no visual-regression tooling (`CLAUDE.md` release
  gate #6), so the disclosure's layout with `flex: 1` removed from `termsText`
  was reasoned from the stylesheet, not observed. If the sentence now centres
  differently from the driver app's, that will only show on a device.
- The greeting change ("Welcome back" → "Welcome to Spinr") is a copy change
  beyond the literal consent ask. It was included because the driver commit
  made the same change for the same reason, but it was not separately directed.
