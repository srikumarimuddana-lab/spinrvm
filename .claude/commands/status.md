# /status — spinr Project Health Dashboard

Run a full health check on the spinr codebase and report status.

## What to Check

1. Git state
   git status && git log --oneline -10 && git branch -a

2. Structure check
   ls -la backend/ rider-app/ driver-app/ frontend/ admin-dashboard/ shared/

3. Backend language and framework
   cat backend/requirements.txt 2>/dev/null | head -20
   find backend -name "*.py" | wc -l

4. Test coverage
   find . -name "*.test.*" -not -path "*/node_modules/*" | wc -l
   find . -name "test_*.py" | wc -l
   ls test_reports/ 2>/dev/null | wc -l

5. Workflows
   ls .github/workflows/

6. Security posture
   cat .gitignore | grep -E "\.env|\.key|secret"
   find . -name ".env*" -not -path "*/.git/*" 2>/dev/null

7. Outstanding work
   cat TODO.md 2>/dev/null | head -40
   cat GAP_ANALYSIS.md 2>/dev/null | head -30

## Output Format

SPINR PROJECT STATUS — [date]
==============================
GIT
  Branch:      [current]
  Status:      [clean / N uncommitted changes]
  Last commit: [hash message time]

CODEBASE
  Backend:          [Python files count, framework detected]
  Rider app:        [exists / missing]
  Driver app:       [exists / missing]
  Frontend:         [exists / missing]
  Admin dashboard:  [exists / missing]

TESTS
  Python tests:     [count]
  JS/TS tests:      [count]
  Test reports:     [count in test_reports/]

CI/CD WORKFLOWS
  [list each workflow and what it covers]
  [flag any obvious gaps]

SECURITY
  .gitignore covers .env files: [yes/no]
  Accidental .env tracked:      [count — must be 0]

STALE FILES
  JSON reports in root:  [count — should be moved to test_reports/]

TOP PRIORITIES
  1. [from TODO.md / GAP_ANALYSIS.md]
  2. [from TODO.md / GAP_ANALYSIS.md]
  3. [from TODO.md / GAP_ANALYSIS.md]

RECOMMENDATION
  [Single most important next action for spinr today]
