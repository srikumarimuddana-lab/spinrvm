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

6. Run: git add -A && git commit -m "<message>"

7. Report: hash, type/scope, files changed, domain affected
   Do NOT push — commit locally only.
