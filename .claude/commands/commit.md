# /commit — Smart Conventional Commit for spinr

Analyse staged changes and create a proper commit.

## Steps

1. Read the diff
   git diff --cached && git status

2. Map to spinr scope
   backend  rider-app  driver-app  frontend  admin  shared
   matching  payments  auth  safety  notifications  pricing

3. Choose type
   feat fix security perf refactor test docs chore ci

4. Write message (72 char subject, imperative mood)
   format: <type>(<scope>): <subject>
   body:   explain WHY (business/technical reason)
   footer: Closes #<ticket>

5. Safety gate before committing
   - No .env files staged
   - No Stripe live keys in diff
   - No raw GPS in log statements in diff
   - If auth or payment code → note human review required

6. Memory check — did this session resolve something worth not re-litigating?
   Ask: did an ambiguous request get resolved a specific way this session (not
   just "what to code" but "what did X actually mean"), did a recurring
   question get a standing answer, or did a wrong assumption get caught and
   corrected? If yes, and it doesn't already have a durable home (not ADR-worthy,
   not already covered by a domain-*.md/regulatory-sk.md/brand-spinr.md, not
   sprint status), add one short entry to `.claude/context/memory.md` (newest
   first, a few lines, per its own format) as part of this same commit. If
   nothing like that happened this session, skip this step silently — most
   commits won't need an entry, don't force one.

7. Run: git add -A && git commit -m "<message>"

8. Report: hash, type/scope, files changed, domain affected
   Do NOT push — commit locally only.
