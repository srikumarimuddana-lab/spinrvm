# Change Impact & Risk Log — remove dead axios apiClient files

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-29 |
| Author | Claude Code (session: driver sign-out investigation) |
| Surface(s) | driver-app, rider-app |
| Domain (Sentry tag) | `auth` (by subject matter; no runtime code path) |
| PR / commit link | _(pending — subtask 6 of 6)_ |
| Related issue or gap ID | Cleanup discovered during the driver sign-out investigation |

## 1. Issue / gap identified

`driver-app/utils/apiClient.ts` and `rider-app/utils/apiClient.ts` are byte-for-byte
identical 53-line axios clients with a 401 → `/auth/refresh` → retry → `logout()`
response interceptor. They have **no importers** and are not the production auth
path — that is `shared/api/client.ts`, a fetch-based client.

They are not harmless. They read as the app's auth layer: plausible file name,
plausible location, a complete-looking refresh-and-logout interceptor. During this
session they were the first files opened when investigating driver sign-outs, and
the real defect was in a different file that behaves differently (fetch, not axios;
`_inflight401Retries` and a G2 backstop that these files have no equivalent of).
Anyone debugging auth will make the same detour.

## 2. Root cause

Leftovers from the migration to the shared fetch client. The migration replaced
every call site but left the files behind, and nothing fails when dead code
persists — no importer means no type error, no test failure, no lint error.

## 3. Fix / remediation

Delete both files. No replacement — `shared/api/client.ts` has been the real
client for both apps all along.

## 4. Risk & impact on existing functionality

**Blast radius: none at runtime.** Establishing that was the whole job:

- `grep -rn "apiClient"` across `driver-app`, `rider-app`, `shared` for
  `.ts`/`.tsx`/`.js`/`.json`: every hit is a **local variable name** for a
  `shared/api/client` import inside a test, or a prose comment. No import of these
  files.
- `grep -rn "utils/apiClient"` repo-wide: only two documentation artifacts,
  `Spinr_Code_Review_Matrix.csv` and `SPINR_CODE_REVIEW.md`. Deliberately **not**
  edited — they are point-in-time audit records, and rewriting a review artifact to
  match later code is worse than a stale path reference in it.
- Jest config: neither app's `moduleNameMapper` references the path.
- **`axios` must stay a dependency** — `driver-app/utils/tripLocationTransport.ts`
  and `rider-app/__tests__/auth.integration.ts` still import it. This commit does
  **not** touch `package.json`.

Not touched: ride state machine, money arithmetic, background loops, RLS,
migrations, WebSocket events, and — because there were no importers — no runtime
behaviour of any kind.

## 5. User-experience effect

None. No rider, driver, corporate-admin, or internal-admin visible change; no code
path removed from any build. The benefit is entirely to whoever next debugs auth.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/utils/apiClient.ts` | Deleted | Dead code that impersonates the auth layer |
| `rider-app/utils/apiClient.ts` | Deleted | Same file, same reason |

Deliberately kept: `rider-app/__tests__/api-client-401-refresh.test.ts`. Despite
its name it exercises `shared/api/client.ts::handleApiError`, and subtask 3 added
the concurrent-401 regression cases to it.

## 7. Before / after

Not applicable — pure deletion of code with no callers. For the record, the
deleted interceptor differed materially from the real one, which is the point:

```ts
// Deleted (axios, both files) — no _inflight401Retries, no G2 backstop,
// no SOS exemption, no refresh dedup, no CSRF handling.
if (error.response?.status === 401 && !originalRequest.headers['X-Retry-Attempted']) {
  try { await apiClient.post('/auth/refresh'); ... }
  catch { await useAuthStore.getState().logout(); }
}
```

## 8. Rollback plan

`git revert` restores both files verbatim. Nothing is written to the database, no
migration, no `app_settings` value, and no behaviour to unwind — restoring dead
code returns it to being dead code. A rollback would need an app build to reach
installed apps, but there is nothing in either app that would notice.

**Not feature-flagged** (gate #3): there is no user-visible behaviour to gate. A
flag cannot meaningfully guard the removal of code nothing imports.

## 9. Verification performed

- [x] **Read both files in full before deleting** — 53 lines each, confirmed
      identical, confirmed axios-based, confirmed a complete-looking but unused
      401 interceptor.
- [x] **Importer search, three ways** — `apiClient` across all `.ts`/`.tsx`/`.js`/
      `.json` in the three source trees; `utils/apiClient` repo-wide; jest
      `moduleNameMapper` in both app configs. Results in §4.
- [x] **`npx tsc --noEmit`** — driver-app: exit 0. rider-app: exit 0. An unresolved
      import would have failed here.
- [x] **Full rider-app suite** — **51/51 suites, 436/436 tests passed** (fully
      green, which also confirms the 4 failures seen during subtask 3 were
      parallel-load flakes rather than regressions).
- [x] **Full driver-app suite** — 43/45 suites, **335/337 tests passed**. The two
      failures are the pair already triaged in
      `2026-07-29-concurrent-401-false-logout.md`: `onlineResync` (pre-existing,
      reproduced on HEAD with all session changes reverted) and `ActivityView`
      (passes standalone twice; fails only under full parallel load). Neither
      relates to these deletions — both suites import nothing from the deleted
      files.
- [x] **Confirmed `axios` is still required** by a live file before touching
      nothing in `package.json`.
- [x] **Reviewed against `CLAUDE.md` conventions** — no money, state machine, RLS,
      migration, or observability surface involved.

### What was NOT verified

- **No production build was run for this commit specifically.** For a deletion of
  files with no importers, `tsc --noEmit` plus a full test run on both apps is the
  meaningful signal — an unresolved module would fail both. Subtasks 2–4 each ran
  `expo export --platform web` successfully on the same working tree, so the
  bundler has already accepted this tree minus these files.
- **Two documentation artifacts now reference a path that no longer exists**
  (`Spinr_Code_Review_Matrix.csv`, `SPINR_CODE_REVIEW.md`). Left intentionally
  stale, per §4.
- **A related piece of test rot was found and NOT fixed here:**
  `rider-app/__tests__/auth.integration.ts` contains `expect(true).toBe(true)` with
  a comment claiming "This flow is tested in apiClient tests" and describing
  `apiClient.interceptors.response` — i.e. it documents the axios interceptor just
  deleted, and asserts nothing. It is a vacuous test in an auth suite. Folded into
  the existing test-hygiene task rather than fixed in a deletion commit.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed — "dead code" is backed by three
      independent searches plus a clean type-check on both apps, not by inspection
- [x] No silent behavior change to an already-shipped flow — there is no behaviour
      change at all, and §4 says why rather than asserting it
