# Change Impact & Risk Log — Legacy/re-consent notice mechanism (mobile UI, remaining gaps)

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-19 |
| Author | Session user (vikas@ngitservices.com), implemented by Claude Code |
| Surface(s) | rider-app, driver-app |
| Domain (Sentry tag) | auth |
| PR / commit link | (this worktree/branch — see commits below) |
| Related issue or gap ID | `docs/change-log/2026-08-19-legacy-consent-notice-mobile.md` §3/§4 ("Known, stated scope gap" / "Not covered — cold start"), `ACTION_ITEMS.md` A41 |

## 1. Issue / gap identified

The 2026-08-19 mobile integration wired the legacy/re-consent notice into exactly one entry
point — each app's `otp.tsx` fresh-login redirect — and explicitly named two populations it did
not cover:

1. **Cold-start / session-restore**: a user who already has a valid session and reopens the app
   (no fresh OTP) never had their consent status checked, so the notice could never reach them
   until their next fresh login.
2. **`profile-setup.tsx`'s own completion redirect**: a pre-existing account with an incomplete
   profile reaches `/profile-setup` (via `otp.tsx`'s `else` branch), and completing the profile
   there skipped the consent check entirely — bypassing the integration for exactly the
   population most likely to be legacy-imported, per the earlier migration audit's finding that
   incomplete profiles are disproportionately legacy-imported accounts.

## 2. Root cause

Not a bug — a deliberately scoped-down first pass (see the original change-log's §3: "safe
subset now" was chosen over full wiring in that session). This work closes the two gaps that
change-log named as still open.

## 3. Fix / remediation

Added the same `GET /consent/status` check + fail-open pattern already used in `otp.tsx` at two
more integration points per app, matching `otp.tsx`'s code shape exactly:

**Cold start** — `rider-app/app/index.tsx` and `driver-app/app/index.tsx` (**not** `_layout.tsx`
in either app; `_layout.tsx` owns app-shell concerns — fonts, providers, push routing,
foreground/background AppState effects — but the actual "where does an already-authenticated
user land" routing decision lives in `app/index.tsx` in both apps, confirmed by reading both
files and by driver-app's own `_layout.tsx` comment: *"The routing decision itself lives in
driver-app/app/index.tsx"*):

- rider-app: inserted only at the final fallback (`router.replace('/(tabs)')`), which is reached
  after the active-ride check block completes or short-circuits. All of that block's early
  returns (ride `completed`/unpaid, `in_progress`, `driver_arrived`, `driver_assigned`/
  `driver_accepted`/`searching`) are unchanged and untouched — so a rider mid-ride, or with any
  active-ride redirect pending, is never interrupted by the consent check. This was a deliberate
  choice, not an oversight: the constraints call out "never mid-session to someone already using
  the app," and an active ride is the clearest mid-session case on this screen.
- driver-app: inserted at the `profileComplete` branch's landing (`router.replace('/driver/')`).
  driver-app's `index.tsx` has no active-ride short-circuit at this point (that's owned by the
  dashboard, not this routing screen), so there was no equivalent mid-flow case to avoid here.

**`profile-setup.tsx` completion** — `rider-app/app/profile-setup.tsx` and
`driver-app/app/profile-setup.tsx`:

- rider-app: inserted only inside `handleSubmit`'s `!isEditing` branch (a new/incomplete profile
  completing for the first time), immediately before what was previously the unconditional
  `router.replace('/(tabs)')`. The `isEditing` branch (`router.back()` — reached when this screen
  is opened from Settings to edit an already-complete profile) is untouched: that population
  already passed the consent gate at their most recent login, so re-checking on every settings
  edit would be a needless network call with no behavioral point.
- driver-app: inserted at `handleSubmit`'s single completion redirect
  (`router.replace('/driver')`). This screen has no separate "edit" mode — an earlier `useEffect`
  in the same file already redirects an already-complete profile straight to `/driver` before the
  form ever renders, so the only reachable completion path is a genuinely-incomplete profile
  finishing setup for the first time.

Both integration points reuse the exact fail-open shape from `otp.tsx`: `api.get('/consent/status')`
with a `.then(onSuccess, onError)` two-argument form, where both the success branch (when
`needs_notice` is falsy) and the error branch route to the same destination the old code
unconditionally used. A network/API failure can never delay or block the existing navigation —
worst case it's the same navigation the code already did, on the same tick the promise settles.

## 4. Risk & impact on existing functionality

- **`authStore.ts` — NOT touched.** Blast-radius grep performed first (per the task's
  instruction) — 22 rider-app files and 17 driver-app files import
  `@shared/store/authStore` (route screens, `hooks/useAuth.ts`, `store/rideStore.ts`,
  `utils/sessionTeardown.ts`, `utils/apiClient.ts`, `utils/backgroundLocation.ts`, several
  `__tests__` files, etc.). All four new integration points read `user.profile_complete` /
  `user.first_name/last_name/email` off the store's existing `user` object — the exact same
  fields `otp.tsx`, `index.tsx`, and `profile-setup.tsx` already read before this change. No new
  field, computed flag, or store action was needed, so the store's public shape is byte-for-byte
  unchanged and none of those 39 consumers are affected.
- **Blast radius of each touched file**: isolated to the one `useEffect`/`handleSubmit` branch
  edited in each of the 4 files. In every case the only behavior change is: one additional
  network call is made before a navigation the code already performed unconditionally, and both
  outcomes of that call (`needs_notice: true` → new screen, everything else → the prior
  destination) preserve the prior destination as the outcome for every existing account with a
  current `consent_version` (or with the flag off, unconditionally for every account).
- **rider-app/app/index.tsx**: the active-ride redirect block (returns early for `completed`
  (unpaid)/`in_progress`/`driver_arrived`/`driver_assigned`/`driver_accepted`/`searching`) is
  completely unchanged — verified by reading the diff, the only inserted code replaces the single
  trailing `router.replace('/(tabs)')` statement.
- **driver-app/app/index.tsx**: the `sessionRecoverable` reconnecting-UI branch, the `/login`
  redirect for missing token/user, and the `!profileComplete` → sign-out-and-redirect-to-login
  branch are all unchanged. Only the `else` (profile-complete) branch's single statement was
  replaced.
- **rider-app/app/profile-setup.tsx**: `createProfile()`, the referral-code apply loop (with its
  retry/backoff), and the `isEditing` → `router.back()` branch are unchanged. Only the trailing
  statement of the `!isEditing` branch was replaced.
- **driver-app/app/profile-setup.tsx**: `createProfile()`, the auto-`registerDriver()` call, and
  the referral-code apply loop are unchanged. Only the trailing `router.replace('/driver')`
  statement was replaced.
- **No migration, no money, no ride state machine, no WebSocket event, no background loop.**
  Same domain (`auth`) and same posture as the original mobile integration.
- **Combined with the original otp.tsx integration**, every entry point named in the original
  change-log's stated gap list is now covered: fresh login (otp.tsx, already shipped, untouched
  this session), cold start (this session), and profile-setup completion (this session). No
  further known entry-point gap remains for this mechanism on mobile.

## 5. User-experience effect

None yet — `legacy_consent_notice_enabled` stays off this session (not flipped, not touched).
The backend and the original mobile screens are already merged into this branch but the flag
remains off, so `/consent/status` reports `needs_notice: false` unconditionally regardless of
account state, and every code path added here resolves to the exact same navigation target the
app already used before this change. Once the flag is turned on in a future session: a returning
rider/driver whose account predates consent tracking will see the one-time notice the first time
they either (a) reopen the app with a valid session, or (b) finish a forced profile-setup step —
whichever they hit first — in addition to the already-shipped fresh-login path. Never mid-session:
the cold-start check explicitly happens only before/instead of the home-landing navigation, not
while already inside the app, and the rider-app cold-start check is placed after (not before) the
active-ride redirect so an active ride is never interrupted.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/app/index.tsx` | Cold-start final fallback now checks `/consent/status` before routing to `/(tabs)` | Close gap 1 (cold start) |
| `driver-app/app/index.tsx` | Cold-start profile-complete branch now checks `/consent/status` before routing to `/driver/` | Close gap 1 (cold start) |
| `rider-app/app/profile-setup.tsx` | `handleSubmit`'s `!isEditing` completion branch now checks `/consent/status` before routing to `/(tabs)` | Close gap 2 (profile-setup completion) |
| `driver-app/app/profile-setup.tsx` | `handleSubmit`'s completion redirect now checks `/consent/status` before routing to `/driver` | Close gap 2 (profile-setup completion) |

`shared/store/authStore.ts` — **not modified** (see §4).

## 7. Before / after

```tsx
// rider-app/app/index.tsx — before
} catch {
  // fall through to home on error
}
router.replace('/(tabs)');

// rider-app/app/index.tsx — after
} catch {
  // fall through to home on error
}
api.get('/consent/status').then(
  (res) => {
    if ((res.data as any)?.needs_notice) {
      router.replace('/legacy-consent-notice' as any);
    } else {
      router.replace('/(tabs)');
    }
  },
  () => router.replace('/(tabs)'), // fail open
);
```

```tsx
// driver-app/app/index.tsx — before
} else {
  router.replace('/driver/' as any);
}

// driver-app/app/index.tsx — after
} else {
  api.get('/consent/status').then(
    (res) => {
      if ((res.data as any)?.needs_notice) {
        router.replace('/legacy-consent-notice' as any);
      } else {
        router.replace('/driver/' as any);
      }
    },
    () => router.replace('/driver/' as any), // fail open
  );
}
```

```tsx
// rider-app/app/profile-setup.tsx handleSubmit, !isEditing branch — before
if (applied) showToast('Referral applied', 'Your referral code was added.', 'success');
}
router.replace('/(tabs)' as any);

// after
if (applied) showToast('Referral applied', 'Your referral code was added.', 'success');
}
api.get('/consent/status').then(
  (res) => {
    if ((res.data as any)?.needs_notice) {
      router.replace('/legacy-consent-notice' as any);
    } else {
      router.replace('/(tabs)' as any);
    }
  },
  () => router.replace('/(tabs)' as any), // fail open
);
```

```tsx
// driver-app/app/profile-setup.tsx handleSubmit — before
if (applied) showToast('success', 'Referral applied', 'Your referral code was added.');
}
router.replace('/driver' as any);

// after
if (applied) showToast('success', 'Referral applied', 'Your referral code was added.');
}
api.get('/consent/status').then(
  (res) => {
    if ((res.data as any)?.needs_notice) {
      router.replace('/legacy-consent-notice' as any);
    } else {
      router.replace('/driver' as any);
    }
  },
  () => router.replace('/driver' as any), // fail open
);
```

## 8. Rollback plan

- **Code**: `git revert` either or both commits — each is additive to one branch of one
  `useEffect`/`handleSubmit`, with a fail-open path; reverting restores the exact prior
  unconditional redirect statement in each file. The two subtasks (cold-start, profile-setup)
  were committed separately so either can be reverted independently without affecting the other.
- **Live, without a redeploy**: identical to the original integration's rollback — the backend
  flag (`legacy_consent_notice_enabled = false`) already makes `/consent/status` report
  `needs_notice: false` unconditionally, so all four new checks are no-op extra network calls
  with the flag off. Disabling it there is sufficient without an app store update even after a
  mobile build ships.

## 9. Verification performed

- [x] TypeScript: `tsc --noEmit` clean on the **full project** for both `rider-app` and
  `driver-app` (not just the touched files) — exit code 0, zero output, on freshly-installed
  `node_modules` for both apps in this worktree.
  - `rider-app`'s initial `npm install` failed on a pre-existing peer-dependency conflict
    unrelated to this change (`@testing-library/react-native@12.9.0` vs. `expo-router@57.0.9`'s
    `>= 13.2.0` peer requirement) — retried with `--legacy-peer-deps`, a standard, install-only
    workaround; no source file or lockfile intended to be part of this change was affected.
  - Both `npm install` runs modified `yarn.lock` and generated a stray `package-lock.json`
    (this repo is yarn-managed per the tracked `yarn.lock`). Both were discarded
    (`git checkout -- **/yarn.lock`, `rm **/package-lock.json`) before committing — neither
    lockfile change is part of this change and nothing in `git status` reflects them post-commit.
- [x] ESLint clean on all 4 touched files (`app/index.tsx` and `app/profile-setup.tsx` in both
  apps) — zero findings, run via `npx eslint <file> <file>` directly against the two touched
  files per app (not the full-project `expo lint`, to keep the run scoped and fast — both files'
  full-project `tsc` pass already covers the whole-project type-check requirement).
- [ ] **No visual verification performed — no simulator or device available this session**,
  identical constraint to the original mobile integration. This repo has no automated
  visual/snapshot regression tooling for either app (a standing, already-documented gap). None of
  the four edits change any rendered UI — every change is inside an existing `useEffect`/
  `handleSubmit` navigation branch, with no new JSX — so there is no new visual surface to verify
  in the first place; the pre-existing `legacy-consent-notice.tsx` screens' rendered appearance
  remains exactly as unverified as the original change-log stated.
- [ ] Manual repro / staging — not performed (no simulator/device, flag stays off).
- [x] Fail-open behavior confirmed **by reading the code path**, not by running it: both branches
  of every `.then(onSuccess, onError)` call were traced to confirm the error branch always
  reaches the same destination the pre-change code used unconditionally, matching `otp.tsx`'s
  established pattern exactly (same two-argument `.then()` shape, same comment style).
- [x] Blast-radius grep performed on `shared/store/authStore.ts` importers before deciding not to
  touch it — 22 rider-app + 17 driver-app files (§4) — and on the pre-change navigation logic in
  all 4 touched files, read in full before editing.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (flag flip covers the live case even post-shipment;
  the two commits are independently revertible).
- [x] Blast radius is stated, not assumed — `authStore.ts`'s 39 total importers were enumerated,
  and each touched file's untouched branches were named explicitly.
- [x] No silent behavior change — the flag is off, nothing is live yet, and the "no visual
  verification" and "no manual/staging repro" boundaries are stated plainly rather than implied
  covered.

**Both gaps the original mobile change-log named as open are now closed**: cold-start/
session-restore (`rider-app/app/index.tsx`, `driver-app/app/index.tsx`) and `profile-setup.tsx`'s
own completion redirect (`rider-app/app/profile-setup.tsx`, `driver-app/app/profile-setup.tsx`).
`authStore.ts` was deliberately left untouched — a `_layout.tsx`/`index.tsx`-only implementation
was sufficient, per the task's own preferred-safer-choice guidance. Visual verification on a real
device and flipping the flag on remain out of scope for this session, same as before.
