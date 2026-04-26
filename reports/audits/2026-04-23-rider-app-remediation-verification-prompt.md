# Rider App — Remediation Verification Audit

**Date:** 2026-04-23
**Branch:** `claude/review-pending-audits-Pu1aP`
**Source audit:** `reports/audits/2026-04-19-rider-app-v1.txt` (184 findings, 16 dimensions)
**Source remediation files:** `reports/remediation/rider-P0..P4-*.md` (92 items)
**Goal:** Verify against the *current* codebase which rider remediation items
are done, pending, partial, blocked, or unverifiable — with evidence, owner,
and reason. Mirror of the driver verification methodology.

---

## Prompt (paste into a fresh agent call — one call per sprint)

```
ROLE
You are a remediation verifier. You DO NOT re-audit, DO NOT write new code.
You read each remediation item from the rider-app sprint file, then read the
*current* codebase to decide whether the fix is actually in place, and you
emit a structured status entry.

INPUTS (read exactly once, do not re-read)
1. reports/remediation/rider-<SPRINT>.md                 ← items for this sprint
2. reports/audits/2026-04-19-rider-app-v1.txt            ← ONLY the finding
                                                           referenced by each item
3. audit-framework/regulatory-matrix.md                  ← regulation tags
4. CLAUDE.md                                             ← conventions
5. reports/audits/2026-04-23-driver-P*-verification.md   ← to dedup shared-backend
                                                           items (OTP hashing,
                                                           atomic ride-accept,
                                                           signed URLs, etc.)

SCOPE
- Current sprint file only. Do not cross-check items from other sprints.
- Verification reads codebase at HEAD of branch claude/review-pending-audits-Pu1aP.
- Never modify code. Never run destructive commands.

OUTPUT
A single file at reports/audits/2026-04-23-rider-<SPRINT>-verification.md
containing one entry per remediation item in the sprint file, in the same
order, followed by a ===VERIFICATION-YAML=== block and an
===AUDIT-COMPLETE=== sentinel.

VERIFICATION PROCEDURE per item (in order, stop at first decisive result)
  1. Identify the audit finding the item references (grep the [N-N] tag
     in 2026-04-19-rider-app-v1.txt). Extract file path(s) and root cause.
  2. Before reading, check whether this item is a shared-backend fix that
     the driver verification already evidenced (see driver-P*-verification
     files). If so, mark `duplicate_of: <driver ID>` and carry the status.
  3. Read the referenced file(s) at HEAD. Do NOT read adjacent files
     unless the fix explicitly requires multi-file coordination.
  4. Look for the fix signature described in the remediation item.
  5. Decide STATUS (see taxonomy below).
  6. If STATUS = PENDING or PARTIAL, identify the OWNER.
  7. If STATUS = BLOCKED, capture what blocks it.
  8. If STATUS = UNVERIFIABLE, say so — do NOT guess.

NEVER
- Never mark an item DONE without a file:line grep result in `evidence`.
- Never invent file paths.
- Never re-audit — only verify the listed item.
- Never consolidate two items into one row.
```

---

## Status Taxonomy (identical to driver — mirror for consistency)

| Status | Criteria | Evidence required |
|---|---|---|
| `DONE` | Fix in the code at HEAD, matches remediation description, AND a regression test exists (or code-only with no testable surface). | file:line + test ref if applicable |
| `PARTIAL` | Some of the fix is present but not all. | what's done + what's missing |
| `PENDING` | No evidence of fix at HEAD. | grep proving original issue remains |
| `BLOCKED` | Fix requires something outside the codebase. | what blocks + who |
| `UNVERIFIABLE` | Cannot decide from static inspection. | why + what probe would answer it |
| `SUPERSEDED` | Item obsolete because feature was removed/redesigned. | commit/PR removing it |

**Shared-backend dedup rule (rider-specific):** Many rider P0/P1 items are
the same backend fixes already verified in `driver-P0-verification.md` (OTP
hashing, signed URLs, atomic ride-accept, Firebase refresh-token).
For these, set `duplicate_of: <driver verification ID>` and carry that
status. Do not re-grep the same file evidence.

---

## Owner Taxonomy (identical to driver)

`backend` · `driver-app` · `rider-app` · `shared` · `admin` · `infra` · `data` · `devops` · `legal` · `compliance` · `product` · `ext-stripe` / `ext-firebase` / `ext-twilio` / `ext-supabase`

Max 2 owners per item.

---

## Output Schema (REQUIRED)

Human-readable prose per item, then at end of file:

```yaml
===VERIFICATION-YAML===
- id: rider-P0-1
  source_finding: "01-3"                    # from 2026-04-19-rider-app-v1.txt
  status: DONE
  evidence:
    file: rider-app/store/rideStore.ts
    lines: [218]
    snippet: "triggerEmergency: async (rideId, lat, lng) => { ... Alert.alert ... }"
    test_file: rider-app/store/__tests__/rideStore.test.ts
    test_lines: null
  reason: "SOS failure surfaces an alert; no longer silently caught."
  owner: null
  blocked_by: null
  confidence: high
  regulations: [E911, SK-HRC]
  effort_remaining_hours: 0
  duplicate_of: null
  notes: null
===END-VERIFICATION-YAML===

===AUDIT-COMPLETE=== sprint=<SPRINT> items=<N> done=<N> partial=<N> pending=<N> blocked=<N> unverifiable=<N> superseded=<N>
```

---

## Sprint Execution Order (5 agent calls, API-safe)

Rider sprints are **larger than driver's** — P1 has 33 items, P2 has 33.
Budget accordingly and consider splitting P1/P2 into two calls each if the
output approaches context limits.

| Call # | Sprint file | Item count | Output file | Splittable |
|-------|------------|---------:|-------------|:-:|
| 1 | `reports/remediation/rider-P0-critical-fix-now.md` | 8 | `reports/audits/2026-04-23-rider-P0-verification.md` | — |
| 2a | `reports/remediation/rider-P1-before-beta.md` (items 1–17) | 17 | `reports/audits/2026-04-23-rider-P1a-verification.md` | Y |
| 2b | `reports/remediation/rider-P1-before-beta.md` (items 18–33) | 16 | `reports/audits/2026-04-23-rider-P1b-verification.md` | Y |
| 3a | `reports/remediation/rider-P2-before-launch.md` (items 29–45) | 17 | `reports/audits/2026-04-23-rider-P2a-verification.md` | Y |
| 3b | `reports/remediation/rider-P2-before-launch.md` (items 46–61) | 16 | `reports/audits/2026-04-23-rider-P2b-verification.md` | Y |
| 4 | `reports/remediation/rider-P3-hardening.md` | 8 | `reports/audits/2026-04-23-rider-P3-verification.md` | — |
| 5 | `reports/remediation/rider-P4-future-features.md` | 10 | `reports/audits/2026-04-23-rider-P4-verification.md` | — |

If call 2a/2b/3a/3b fit in one window each after practice, collapse to
`rider-P1-verification.md` / `rider-P2-verification.md` single files.
Safer to split first time.

**After each call:** commit the output file immediately so any subsequent
truncation preserves previous progress.

---

## Self-Review Pass (before emitting the sentinel)

1. Every `DONE` has a file:line in evidence. If not → downgrade.
2. Every `PARTIAL`/`PENDING` has an `owner` field populated.
3. Every `BLOCKED` has a `blocked_by` value naming a specific person/vendor/artifact.
4. Count sanity: `done + partial + pending + blocked + unverifiable + superseded == items in sprint file`.
5. Regulation tags: any item touching rider-PII-exposure-to-driver, payments, location,
   SMS/push, fare-estimate, or promo must have at least one regulation tag.
6. Shared-backend items carrying a `duplicate_of` reference to a driver
   verification ID: confirm the driver verification actually covered the
   rider-facing code path too (some backend fixes affect only driver routes).

---

## Worked Example — rider-specific

```markdown
### rider-P0-1 · Emergency SOS Silently Fails on Network Error

**Status:** DONE
**Source finding:** [10-2]
**Evidence:**
- `rider-app/store/rideStore.ts:218` — `triggerEmergency` now wraps the
  `api.post('/rides/...emergency')` in a try/catch and emits
  `Alert.alert('SOS not sent', 'Please call 911 directly')` on error.
- `shared/components/SOSButton.tsx:74` — pre-ride fallback opens
  `Linking.openURL('tel:911')` when `rideId` is absent.
- No grep hit for `catch.*\/\/ *swallow` around emergency endpoint.
**Reason:** Network failure surfaces an actionable fallback to the rider;
silent swallow eliminated. Aligns with CLAUDE.md rule against swallowing
errors on safety paths.
**Owner:** —
**Confidence:** high
**Regulations:** E911, SK-HRC
**Effort remaining:** 0 h

### rider-P1-12 · Firebase Token Does Not Enforce Rider App Audience

**Status:** PENDING
**Source finding:** [2-9]
**Evidence:**
- `backend/routes/auth.py:402-403` —
  `_firebase_auth.verify_id_token(body.firebase_token)` is called without
  an `audience=` parameter.
- `grep -n "firebase.*audience" backend/ --include="*.py"` → 0 hits.
- Driver verification P3-10 confirmed the refresh-token issue but
  explicitly left audience check as "open — see rider-P1-12".
**Reason:** Firebase path still accepts any audience. A rider-issued
Firebase ID token would be accepted by the driver path (and vice versa).
**Owner:** `backend`
**Blocked by:** —
**Confidence:** high
**Regulations:** PIPEDA
**Effort remaining:** 4 h
**Shared with:** driver DV-10
**Action:**
1. Register separate Firebase audiences for rider vs driver app.
2. Pass `check_revoked=True, audience=<expected>` to `verify_id_token`.
3. Add regression test: cross-audience token rejected.
```

---

## After All Sprints Are Verified

Produce `reports/audits/2026-04-23-rider-remediation-rollup.md` matching the
driver rollup format:

1. Per-sprint completion % table.
2. Top 5 owners by open-item count.
3. `UNVERIFIABLE` items grouped with probe/runbook needed.
4. `BLOCKED` items grouped by blocker.
5. Regulation-exposure table for open items.
6. **New-issues section** — any issue discovered during verification that
   is NOT in any rider remediation file. Log here for triage into
   `OPEN-ITEMS-TRACKER.md`.
7. Closing checklist: "rider audit-ready for public launch when ..."

Cross-append the rider rollup's section A into `OPEN-ITEMS-TRACKER.md`
(section C becomes verified, replace with a rider-specific section A twin).

---

## Notes for the Verifier

- Rider P1 has 33 items vs driver's 10 — expect longer verification time
  and more likelihood of `duplicate_of` entries where the fix is a shared
  backend route.
- Several rider items reference `rider-app/store/rideStore.ts` (600+ lines)
  — read relevant sections only, not the whole file.
- rider-P1-10 (i18n library) is known to be a big-effort PENDING item;
  don't spend > 10 min trying to find evidence of `i18next` or similar
  when the baseline grep shows it's absent.
- If an item references `rider-app/app/<screen>.tsx` and that screen has
  been moved/renamed (e.g. fare-split flow reorganised), mark `SUPERSEDED`
  with a pointer to the new location.
- Time budget: ≤ 10 min per item. > 10 min → `UNVERIFIABLE` with a reason.
