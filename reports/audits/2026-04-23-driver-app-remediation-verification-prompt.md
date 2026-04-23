# Driver App — Remediation Verification Audit

**Date:** 2026-04-23
**Branch:** `claude/review-pending-audits-Pu1aP`
**Source audit:** `reports/audits/2026-04-18-driver-app-production-readiness-v4.txt` (258 findings, 14 tasks)
**Source remediation files:** `reports/remediation/P0-critical-fix-now.md` → `P4-future-features.md`
**Goal:** Verify against the *current* codebase which remediation items are done, pending, partial, blocked, or unverifiable — with evidence, owner, and reason.

---

## Prompt (paste into a fresh agent call — one call per sprint)

```
ROLE
You are a remediation verifier. You DO NOT re-audit and you DO NOT write
new code. You read each remediation item from the driver app sprint file,
then read the *current* codebase to decide whether the fix is actually in
place, and you emit a structured status entry.

INPUTS (read exactly once, do not re-read)
1. reports/remediation/<SPRINT>.md                      ← items for this sprint
2. reports/audits/2026-04-18-driver-app-production-readiness-v4.txt
   (but ONLY the finding referenced by each item — grep for [N-N])
3. audit-framework/regulatory-matrix.md                  ← for regulation tags
4. CLAUDE.md                                             ← for conventions

SCOPE
- Current sprint file only. Do not cross-check items from other sprints.
- Verification reads codebase at HEAD of branch claude/review-pending-audits-Pu1aP.
- Never modify code. Never run destructive commands.

OUTPUT
A single file at reports/audits/2026-04-23-driver-<SPRINT>-verification.md
containing one entry per remediation item in the sprint file, in the same
order, followed by a ===VERIFICATION-YAML=== block and an
===AUDIT-COMPLETE=== sentinel.

VERIFICATION PROCEDURE per item (in order, stop at first decisive result)
  1. Identify the audit finding the item references (grep the [N-N] tag
     in the source audit file). Extract the file path(s) and root cause.
  2. Read the referenced file(s) at HEAD. Do NOT read adjacent files
     unless the fix explicitly requires multi-file coordination.
  3. Look for the fix signature described in the remediation item.
     Examples of fix signatures:
       - "field renamed X → Y"         → grep for both, confirm only Y exists
       - "idempotency key added"       → grep for stripe event table write
       - "filter on status=searching"  → grep the update clause
       - "Decimal instead of float"    → grep for float( on money columns
  4. Decide STATUS (see taxonomy below).
  5. If STATUS = PENDING or PARTIAL, identify the OWNER (see taxonomy below).
  6. If STATUS = BLOCKED, capture what blocks it (missing env var, upstream
     migration, third-party decision).
  7. If STATUS = UNVERIFIABLE from static inspection, say so — do NOT guess.

NEVER
- Never mark an item DONE without a file:line grep result in `evidence`.
- Never invent file paths. If the remediation points to a file that doesn't
  exist at HEAD, STATUS = PENDING with reason "file not found".
- Never re-audit — only verify the listed item.
- Never consolidate two items into one row.
```

---

## Status Taxonomy

| Status | Criteria | Evidence required |
|---|---|---|
| `DONE` | Fix is in the code at HEAD, matches the remediation description, AND a regression test exists (or the finding was code-only with no testable surface). | file:line where fix is; test file:line if applicable |
| `PARTIAL` | Some of the fix is present but not all. E.g. server-side fixed but no client update, or code fixed but no regression test. | what's done + what's missing |
| `PENDING` | No evidence of fix at HEAD. Item is still open. | grep proving original issue still present |
| `BLOCKED` | Fix requires something outside the codebase (env var, third-party action, migration that must run in prod, legal sign-off). | what blocks + who needs to act |
| `UNVERIFIABLE` | Cannot decide from static inspection alone (e.g. "Redis rate-limit works in prod" — needs runtime probe). | why + what probe would answer it |
| `SUPERSEDED` | Remediation item is obsolete because the feature was removed/redesigned. | commit or PR that removed it |

**Tie-breaker rules**
- "Code is fixed but no test" → `PARTIAL`, not `DONE`. Regression tests are part of the fix contract.
- "Test exists but code still has the bug" → `PENDING` (means the test isn't running or was added without fix).
- "Claim of fix in a PR that isn't merged to HEAD of review branch" → `PENDING`.

---

## Owner Taxonomy

| Owner tag | Responsibility |
|---|---|
| `backend` | `backend/` Python, FastAPI, Supabase helpers, migrations |
| `driver-app` | `driver-app/` React Native screens, stores, hooks |
| `shared` | `shared/` TS package — API client, types, stores, components |
| `admin` | `backend/routes/admin/` or `admin-dashboard/` |
| `infra` | Railway, Render, CI/CD, Docker, env vars, secrets rotation |
| `data` | `backend/migrations/`, RLS policies, Supabase config |
| `devops` | Redis, WS pub/sub, monitoring, alerting |
| `legal` | Privacy policy, terms, consent records (CASL, PIPEDA) |
| `compliance` | Tax (CRA), insurance (SGI), TNC permits, AML/FINTRAC |
| `product` | Feature decisions, copy, UX flows (non-code blockers) |
| `ext-stripe` / `ext-firebase` / `ext-twilio` / `ext-supabase` | External vendor action required |

One owner per item unless the fix genuinely spans two (e.g. `backend`+`driver-app`
for a new API + client wiring). If more than two → the remediation item is too
coarse; split it during the next audit cycle.

---

## Output Schema (REQUIRED)

Human-readable prose above for each item, then at end of file:

```yaml
===VERIFICATION-YAML===
- id: P0-1                              # remediation item id
  source_finding: "2-3"                 # finding tag from driver audit file
  status: DONE                          # see taxonomy
  evidence:
    file: backend/routes/auth.py
    lines: [142]
    snippet: "if user['revoked_at'] is not None: ..."
    test_file: backend/tests/test_auth.py
    test_lines: [88, 102]
  reason: "Field renamed; regression test verifies revoked_at path."
  owner: null                           # null if DONE
  blocked_by: null                      # only if BLOCKED
  confidence: high                      # high=read file · med=inferred · low=guess
  regulations: [PIPEDA]                 # from regulatory-matrix.md
  effort_remaining_hours: 0
  notes: null
- id: P0-2
  ...
===END-VERIFICATION-YAML===

===AUDIT-COMPLETE=== sprint=<SPRINT> items=<N> done=<N> partial=<N> pending=<N> blocked=<N> unverifiable=<N> superseded=<N>
```

---

## Sprint Execution Order (one agent call per sprint, API-safe)

| Call # | Sprint file | Item count (approx) | Output file |
|-------|------------|---------------------|-------------|
| 1 | `reports/remediation/P0-critical-fix-now.md` | 7 | `reports/audits/2026-04-23-driver-P0-verification.md` |
| 2 | `reports/remediation/P1-before-beta.md` | 10 | `reports/audits/2026-04-23-driver-P1-verification.md` |
| 3 | `reports/remediation/P2-before-launch.md` | ~15 | `reports/audits/2026-04-23-driver-P2-verification.md` |
| 4 | `reports/remediation/P3-hardening.md` | 10 | `reports/audits/2026-04-23-driver-P3-verification.md` |
| 5 | `reports/remediation/P4-future-features.md` | 7 | `reports/audits/2026-04-23-driver-P4-verification.md` |

Run sequentially. After each call, commit the output file so partial progress
is preserved if a later call errors.

---

## Self-Review Pass (before emitting the sentinel)

1. Every `DONE` has a file:line in evidence. If any doesn't, downgrade to `PARTIAL` or `PENDING`.
2. Every `PARTIAL` or `PENDING` has an `owner`. If null → assign one now.
3. Every `BLOCKED` has a `blocked_by` value that names a specific person/vendor/artifact.
4. Count sanity: `done + partial + pending + blocked + unverifiable + superseded == items in sprint file`.
5. Regulation tags: any item touching PII/payments/SMS/push/insurance must have a `regulations` tag from `audit-framework/regulatory-matrix.md`.
6. Cross-check `P0-*-verification.md` IDs against the sprint file IDs — no duplicates, no omissions.

---

## Worked Example (gold standard — include in the emitted report)

```markdown
### P0-1 · App Will Crash at Login — Wrong Database Field Name

**Status:** DONE
**Source finding:** [2-3]
**Evidence:**
- Code: `backend/routes/auth.py:142` — `if user.get('revoked_at') is not None`
- Test: `backend/tests/test_auth.py:88-102` — `test_revoked_refresh_token_rejected`
- Grep for legacy field name `user['revoked']` returns 0 hits.
**Reason:** Field was renamed and a regression test exists that exercises the
revoked-token path.
**Owner:** —
**Confidence:** high
**Regulations:** PIPEDA (session management)
**Effort remaining:** 0 h

### P0-5 · Driver With Expired Licence Can Keep Working — No Automatic Suspension

**Status:** PARTIAL
**Source finding:** [12-3]
**Evidence:**
- `backend/utils/document_expiry.py:54` — loop iterates expired docs and calls
  `mark_driver_suspended()`. Good.
- `backend/services/dispatch_service.py:210` — dispatcher still queries drivers
  by `status='online'` only; does NOT filter `is_suspended`. So the loop sets
  the flag but dispatch ignores it.
- No regression test under `backend/tests/` that proves an expired driver
  is excluded from the nearby-driver query.
**Reason:** Backend marks driver suspended but dispatch query doesn't honour it.
Second half of the fix is missing.
**Owner:** `backend`
**Blocked by:** —
**Confidence:** high
**Regulations:** SAFE-DRV, SGI, SK-TNC
**Effort remaining:** 3 h (add filter + regression test)
```

---

## After All Sprints Are Verified

Produce one rollup file: `reports/audits/2026-04-23-driver-remediation-rollup.md`

```
| Sprint | Items | Done | Partial | Pending | Blocked | Unverifiable | % Complete |
|--------|-------|------|---------|---------|---------|--------------|-----------|
| P0     | 7     | ?    | ?       | ?       | ?       | ?            | ?         |
| P1     | 10    | ...                                                            |
| ...    |                                                                       |
```

Plus:
- Top 5 owners by open-item count (who needs to act first).
- Any `UNVERIFIABLE` items grouped with the probe/runbook needed.
- Any `BLOCKED` items grouped by blocker (vendor / legal / env / migration).
- Regulation-exposure table: open items tagged `PIPEDA` / `PCI-DSS` / `SAFE-*` / etc.

---

## Notes for the verifier

- The original audit used severity labels (CRITICAL / HIGH / …). The
  remediation files group items by sprint (P0 / P1 / …). Severity is implicit
  in the sprint; you do not need to re-derive it.
- If a remediation item references multiple findings (e.g. "P4-3 · Create 4
  missing screens [1-16/1-17/1-18/1-19]"), treat each sub-finding as its own
  line within the single remediation ID (P4-3a, P4-3b, …).
- If you discover a genuinely new issue while reading code, DO NOT include it
  in this output. Log it as a comment at the bottom with the heading
  `### New issues discovered (not in scope for this verification)` so a
  future audit can follow up.
- Time budget: ≤ 10 minutes per item. If an item takes longer, mark
  `UNVERIFIABLE` with a reason like "verification requires running the migration
  and observing side effects".
