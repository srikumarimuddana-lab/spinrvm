# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-14 |
| Author | Claude Code |
| Surface(s) | CI |
| Domain (Sentry tag) | admin |
| PR / commit link | (branch `claude/fix-expo-public-key-scan-false-positive`) |
| Related issue or gap ID | Found blocking PR #5350's "Security posture check" |

## 1. Issue / gap identified

`.github/workflows/ci-guardrails.yml`'s "Check EXPO_PUBLIC_ variable exposure" step (part of the hard-blocking `security-posture-gate` job, `continue-on-error: false`) false-positives on legitimate, documented-public client SDK identifiers, and separately on ordinary English prose in comments — blocking PR #5350 even though that PR's actual diff contains zero occurrences of any of the matched strings.

## 2. Root cause

Two independent flaws in the same regex, `EXPO_PUBLIC_.*(?:SECRET|PRIVATE|PASSWORD|KEY|TOKEN)`:

1. **No allowlist for known-public `*_KEY`/`*_TOKEN` names.** Several client SDKs require a public, non-secret identifier that happens to be named with a `_KEY`/`_TOKEN` suffix by the vendor's own convention: `GOOGLE_MAPS_API_KEY` (Google-Console-restricted, meant to ship client-side — this repo's own `CLAUDE.md` documents it as a required rider-app env var), `FIREBASE_API_KEY` (Firebase's own docs: identifies the project only, access is enforced by Firebase Security Rules, explicitly safe to publish), and `FB_CLIENT_TOKEN` (Meta's Facebook SDK client-side init token, distinct from the app secret, required by the `react-native-fbsdk-next` Expo config plugin). None of these are secrets, but the bare regex can't tell them apart from a real one.
2. **`.*` has no identifier-shape constraint**, so it matches across spaces and punctuation, not just within a single `EXPO_PUBLIC_`-prefixed identifier. A comment sentence like `// EXPO_PUBLIC_* value inlined empty). Once the key was restored ...` (`driver-app/lib/androidAuto/carSurface.tsx`) matches purely because the English word "key" appears later on the same line — nothing to do with an actual environment variable reference.

Compounding both: the scan reads each **whole changed file's full text**, not the diff hunk, so either flaw fires on the mere presence of the string anywhere in a file that has any unrelated change — which is how PR #5350 (whose actual diff touches neither an env var name nor this comment) got blocked by content nobody in that PR wrote.

## 3. Fix / remediation

- Restricted the pattern's middle segment from `.*` to `[A-Z0-9_]*`, so it can only span a single contiguous UPPER_SNAKE_CASE-shaped identifier immediately after `EXPO_PUBLIC_` — prose containing spaces/punctuation can no longer bridge a match. Still case-insensitive (`re.IGNORECASE` untouched), so a stray lowercase declaration is still caught.
- Added a negative-lookahead allowlist for the three confirmed-public identifiers: `(?!GOOGLE_MAPS_API_KEY\b|FIREBASE_API_KEY\b|FB_CLIENT_TOKEN\b)`.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to this one regex in this one CI step.** Grepped the other `EXPO_PUBLIC_` patterns in `.github/workflows/ci.yml` (a separate, narrower set: `SECRET|PRIVATE|SERVICE_ROLE|ANTHROPIC` and an explicit `(ANTHROPIC|STRIPE_SECRET|SUPABASE_SERVICE_ROLE|JWT_SECRET)` list) — neither would ever match `GOOGLE_MAPS_API_KEY`/`FIREBASE_API_KEY`/`FB_CLIENT_TOKEN` regardless of this change, so nothing there needed touching.
- **Does not weaken real secret detection.** Verified with 12 unit cases (see §9) that every genuine pattern this gate exists to catch — `*_SECRET_KEY`, `*_ADMIN_PASSWORD`, `*_AUTH_TOKEN`, an unallowlisted `*_API_KEY`, `EXPO_PUBLIC_SUPABASE_SERVICE_ROLE_KEY`, and even a variant name that merely *starts* with an allowlisted string (`GOOGLE_MAPS_API_KEY_BACKUP`, `FIREBASE_API_KEY_LEGACY`) — still matches. The `\b` word boundary in each allowlist entry means only an exact match of the allowlisted name is excluded, not any name that happens to contain it as a prefix.
- Ran the full, updated regex against every `.ts`/`.tsx`/`.js`/`.jsx`/`.env`/`.json` file currently in the repo (not just the ones known to be affected) — zero matches, confirming no other legitimate file is newly caught or newly missed.
- No production code path is touched — this is CI-only tooling.

## 5. User-experience effect

None for riders/drivers/admins — internal CI tooling only. Effect is entirely for future PR authors: a PR touching any of the ~25 files that already reference one of these three public identifiers will no longer be incorrectly blocked by this check.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `.github/workflows/ci-guardrails.yml` | Restricted `PRIVATE_PATTERNS`'s wildcard to an identifier-shaped character class; added a 3-name allowlist for documented-public client SDK identifiers | Closes two false-positive classes without weakening detection of any real secret pattern |

## 7. Before / after

```python
# Before
PRIVATE_PATTERNS = [
    r'EXPO_PUBLIC_.*(?:SECRET|PRIVATE|PASSWORD|KEY|TOKEN)',
    r'EXPO_PUBLIC_SUPABASE_SERVICE',
    r'EXPO_PUBLIC_JWT',
    r'EXPO_PUBLIC_ADMIN',
]
```

```python
# After
PRIVATE_PATTERNS = [
    r'EXPO_PUBLIC_(?!GOOGLE_MAPS_API_KEY\b|FIREBASE_API_KEY\b|FB_CLIENT_TOKEN\b)'
    r'[A-Z0-9_]*(?:SECRET|PRIVATE|PASSWORD|KEY|TOKEN)',
    r'EXPO_PUBLIC_SUPABASE_SERVICE',
    r'EXPO_PUBLIC_JWT',
    r'EXPO_PUBLIC_ADMIN',
]
```

## 8. Rollback plan

`git-revert-safe` — this is a CI-only regex change with no persisted state; reverting restores the previous (over-broad, false-positive-prone) pattern with no other side effect.

## 9. Verification performed

- [x] 12 unit cases run directly against both the old and new pattern (4 known-public identifiers/prose that must NOT match, 6 real secret shapes that must still match, 2 "starts with an allowlisted name but is a different var" cases that must still match) — all pass against the new pattern.
- [x] Full-repository scan: ran the updated `PRIVATE_PATTERNS` against every `.ts`/`.tsx`/`.js`/`.jsx`/`.env`/`.json` file currently tracked in the repo (not just the PR-changed set) — zero matches, confirming the fix is complete for every file that exists today, not just the ones discovered via PR #5350.
- [x] Confirmed via `git log`/`git show` that the flagged strings predate PR #5350's actual diff (not introduced by that PR).
- [x] Blast-radius check: confirmed `ci.yml`'s separate `EXPO_PUBLIC_` patterns don't overlap with any of the three newly-allowlisted names, so they needed no change.

**What was NOT verified:** this fix was validated by re-implementing and running the exact same scan logic locally (not by actually pushing to a PR and watching this specific GitHub Actions step go green) — the real workflow step's `git diff --name-only` + per-file scan behavior was reasoned about and matched line-for-line against the workflow source, not executed inside an Actions runner. CI on this PR itself will be the first real end-to-end confirmation.

## 10. Alternatives considered

- **Diff-hunk-scoped scanning instead of whole-file** (would have prevented PR #5350 from ever being blocked by pre-existing content it didn't touch, addressing the *symptom* that surfaced this): a more invasive rewrite of the scan's file-selection logic, and doesn't fix the underlying two false-positive classes, which would still block a PR that genuinely *does* touch one of these lines (e.g. a future PR editing `carSurface.tsx`'s nearby comment, or adding a new Firebase config field). Rejected as out of scope for this fix; the regex-precision fix here is narrower and addresses the actual false-positive, not just its blast radius.
