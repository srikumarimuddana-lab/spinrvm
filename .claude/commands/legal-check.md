# /legal-check — Legal Document Readiness Review

Delegate to the `spinr-legal-readiness-reviewer` agent to check draft legal documents under `docs/legal/` against their own stated pre-publication gating conditions, before spending a review cycle re-deriving them by hand.

## Usage

```
/legal-check                                          # audits every Draft-status file in legal-text-publication-checklist.md
/legal-check docs/legal/cancellation-fee-policy.md     # audits one file
```

## What it does

1. Scopes:
   - No args → every `docs/legal/*.md` row marked `Draft` in `docs/legal/legal-text-publication-checklist.md`
   - Path arg → that file only
2. Dispatches `spinr-legal-readiness-reviewer` with the scope
3. Reports findings — no edits applied automatically. The agent classifies each open gating condition as:
   - **Code-checkable** (a constant, a wired-up flow, cross-document consistency) — resolved directly, cited by file:line
   - **Live-data** (depends on database state) — flagged as requiring a session with DB access, not guessed
   - **Human-decision** (counsel review, a business number nobody's committed to, a vendor not yet contracted, infra only an admin can provision) — flagged with exactly who needs to supply what, never invented

## What gets checked

Per file, per open condition in its header/pre-publication notes and its checklist row:

- Does the code/constant/flow the draft cites actually exist, and does it match what the draft claims?
- Do companion documents (e.g. `community-guidelines.md` / `non-discrimination-policy.md` / `driver-deactivation-appeals-policy.md`) still agree with each other?
- Is a prior "verified against live data" note stale enough to warrant re-checking?
- Is a `[BRACKETED PLACEHOLDER]` in the draft body actually resolvable from code, or still a real open question?

## Output

```
SPINR LEGAL READINESS REVIEW — <file(s)>
=========================================
<file>: <Draft | Ready for counsel review | Published>
  [1] <condition> — CLOSED (<file:line>) | STILL OPEN | NEWLY CLOSED
  [2] <condition> — REQUIRES DB SESSION TO VERIFY
  [3] <condition> — BLOCKED, needs <party> to supply <fact>
SUMMARY / VERDICT
```

## When to run

- Before a scheduled or ad-hoc legal-content readiness check (this replaces re-deriving gating status by hand each time)
- After a code change that could close a gating condition (e.g. a new in-app flow, a new config default) — check whether any draft's "still open" note is now stale
- Before asking a human (counsel, safety team, product owner) to weigh in — so their time is spent only on genuinely open human-decision items, not on facts the codebase already answers

## Do NOT

- Do not have the agent write plausible-sounding numbers, names, or dates into a draft — every fact must trace to code, live data, or a named human decision-maker
- Do not have the agent publish anything to the live `legal_documents` table or update `legal-text-publication-checklist.md` directly — apply findings as a separate, explicit, reviewed edit (per CLAUDE.md's Change Impact & Risk Log requirement)
- Do not treat a "still open" verdict on a live-data condition as a real gap without a DB-connected session actually checking it first
