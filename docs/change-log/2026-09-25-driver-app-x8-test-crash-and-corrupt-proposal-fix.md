# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | TeamSpinr (session-assisted) |
| Surface(s) | driver-app (tests only), shared (`shared/auth/refreshProposal.ts`) |
| Domain (Sentry tag) | auth |
| PR / commit link | srikumarimuddana-lab/spinrvm#5808 |
| Related issue or gap ID | Regression from #5782 (X8 refresh-successor commitment), surfaced by CI Error Audit run 36170583224 (P0) on `main` |

## 1. Issue / gap identified

Two driver-app test suites (`authStore.initialize.test.ts`, `authStore.refreshRace.test.ts`) crashed on `main` with `ReferenceError: You are trying to \`import\` a file outside of the scope of the test code`, taking `driver-app-test` red repo-wide. Separately, `refreshProposalFor()` (added by #5782) silently disabled the X8 recovery mechanism whenever the stored proposal entry was corrupt.

## 2. Root cause

- **Test crash**: `shared/store/authStore.ts::refreshTokens()` unconditionally calls `refreshProposalFor()` (`shared/auth/refreshProposal.ts`), which lazily `require('expo-crypto')` — a real native module. Neither test file mocked `expo-crypto`, so the real module loaded for the first time from a file outside `driver-app`'s Jest `rootDir`, tripping a known jest-expo sandbox edge case in Expo's WinterCG `fetch` installer (`expo/src/winter/installGlobal.ts`).
- **Corrupt-proposal bug**: `refreshProposalFor()` parses a stored JSON entry with no inner `try/catch`; a corrupt entry throws `JSON.parse`, which the function's *outer* `try/catch` catches and returns `null` for the whole call — skipping the fresh-proposal-generation fallback the function's own docstring says should run ("ignores a corrupt pending entry and starts a fresh proposal"). Confirmed via the pre-existing (already-written, already-failing) test `refreshProposal.test.ts`'s "ignores a corrupt pending entry and starts a fresh proposal" case.

## 3. Fix / remediation

- Added an `expo-crypto` mock (all-equal bytes, matching `generateProposal()`'s own "no-op RNG" guard so it resolves to `null`) to both crashing test files — same pattern the working `refreshProposal.test.ts` already used, chosen deliberately so these two pre-X8 suites keep asserting their original proposal-less `/auth/refresh` behavior rather than coupling them to X8.
- Wrapped only the `JSON.parse`/pending-check in its own `try/catch` inside `refreshProposalFor()`, so a corrupt entry falls through to `generateProposal()` instead of aborting via the outer catch. No other behavior in the function changed.

## 4. Risk & impact on existing functionality

- `refreshProposalFor()` is called from exactly one place: `shared/store/authStore.ts::refreshTokens()` (grepped, confirmed single call site). No other reader/writer of `REFRESH_PROPOSAL_KEY` exists besides `refreshProposalFor`/`clearRefreshProposal` themselves.
- The whole X8 mechanism is gated server-side by `refresh_successor_commitment_enabled` (default off per #5782); the client always generates/sends a proposal regardless, but the server ignores it while the flag is off. This fix changes client-side behavior only for the corrupt-entry edge case, and only actually matters once the server flag is on.
- Blast radius: **isolated**. Test-file changes affect nothing at runtime. The `refreshProposal.ts` fix is a two-line `try/catch` narrowing with no change to the function's public contract (still `Promise<string | null>`, still never throws).

## 5. User-experience effect

None today (flag off). Once `refresh_successor_commitment_enabled` is on: a driver whose stored proposal entry becomes corrupt (rare — would need partial/interrupted keychain writes) now gets a fresh proposal generated instead of silently falling back to a plain refresh for that one attempt. No degradation in either case — plain refresh was already a safe fallback per the function's docstring ("Best-effort: any failure means refresh without a proposal").

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/__tests__/store/authStore.initialize.test.ts` | Added `expo-crypto` mock | Prevents real native-module load that crashed the suite |
| `driver-app/__tests__/store/authStore.refreshRace.test.ts` | Added `expo-crypto` mock | Same |
| `shared/auth/refreshProposal.ts` | Narrowed the `try/catch` around `JSON.parse` | Fixes the corrupt-entry fallback bug the existing test already expected |

## 7. Before / after

```ts
// Before
const stored = await SecureStore.getItemAsync(REFRESH_PROPOSAL_KEY);
if (stored) {
  const pending = JSON.parse(stored) as { parent?: unknown; proposal?: unknown };
  if (pending.parent === parent && typeof pending.proposal === 'string' && PROPOSAL_RE.test(pending.proposal)) {
    return pending.proposal;
  }
}
```

```ts
// After
const stored = await SecureStore.getItemAsync(REFRESH_PROPOSAL_KEY);
if (stored) {
  try {
    const pending = JSON.parse(stored) as { parent?: unknown; proposal?: unknown };
    if (pending.parent === parent && typeof pending.proposal === 'string' && PROPOSAL_RE.test(pending.proposal)) {
      return pending.proposal;
    }
  } catch {
    // Corrupt pending entry — fall through and generate a fresh proposal.
  }
}
```

## 8. Rollback plan

`git-revert-safe`. No migration, no flag, no data involved — pure test-doubles + a `try/catch` narrowing in client-side logic gated behind a currently-off server flag.

## 9. Verification performed

- [x] Reproduced the original crash locally (`npx jest __tests__/store/authStore.initialize.test.ts`), confirmed identical error to the CI log.
- [x] Confirmed root cause via `grep` for `refreshProposalFor`/`expo-crypto` call sites, then read `shared/auth/refreshProposal.ts` and `shared/store/authStore.ts` directly (not guessed).
- [x] After the `expo-crypto` mock alone, re-ran and found a *second* real issue (an unexpected `proposed_refresh_token` field in existing assertions) — traced to non-uniform mock bytes producing a real proposal; corrected to all-equal bytes, matching `generateProposal()`'s own documented "broken RNG" guard.
- [x] Ran the three affected files together: `3 passed, 63 tests passed`.
- [x] Ran the full driver-app suite: `166 passed, 2021 tests passed` — no regressions elsewhere.
- [x] Blast-radius grep: confirmed `refreshProposalFor` has exactly one call site.

## 10. What was NOT verified

- Not tested against a real device/native `expo-crypto` module — only the Jest mock path. The production code path (`generateProposal()`'s real `Crypto.getRandomBytes(64)` call) is unchanged by this fix.
- Not verified end-to-end with `refresh_successor_commitment_enabled` actually turned on anywhere (it remains off everywhere per #5782).
- No `npm run build` run for driver-app (this is a test-only + shared-lib change, not an app-facing build surface); `npx jest` (full suite, real run) is the verification that applies here.

## Tier 7 · Bug-fix notes (refreshProposal.ts corrupt-entry fix)

- **Root cause**: single outer `try/catch` in `refreshProposalFor()` caught the corrupt-JSON exception meant to be handled locally, short-circuiting the fresh-proposal fallback.
- **How it was introduced**: #5782 (2026-09-25), the X8 refresh-successor-commitment feature's initial implementation.
- **Why not caught earlier**: the test asserting this exact behavior (`refreshProposal.test.ts`'s "ignores a corrupt pending entry and starts a fresh proposal") was written in the same PR but was itself failing in CI — a case of a regression test correctly written but its own suite-crashing sibling files masking the signal until this session's investigation separated the two failure classes.
- **Regression test**: pre-existing, not new — this fix makes an already-correct test pass rather than adding one.
- **Backport needed?**: none (single branch, not yet released).
