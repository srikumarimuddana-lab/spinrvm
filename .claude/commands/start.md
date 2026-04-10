# /start — Start a New spinr Feature Branch

Usage:
  /start feat/47 matching engine proximity query
  /start fix/89 driver location race condition
  /start spike/12 surge pricing research
  /start hotfix supabase connection pool exhaustion

## Steps

1. Parse input → type, optional ticket number, slug (kebab-case)
   Branch name: feature/47-matching-engine-proximity-query

2. Check working tree is clean
   git status
   If uncommitted changes exist: STOP and ask to stash or commit first.

3. Switch to base and create branch
   feat/fix/docs/refactor/chore → base: develop
   hotfix → base: main
   git checkout <base> && git pull origin <base>
   git checkout -b <branch-name>

4. Identify spinr domain being touched
   - Backend API (Python/FastAPI)
   - Rider app (Expo/React Native)
   - Driver app (Expo/React Native)
   - Frontend web (Next.js)
   - Admin dashboard (Next.js)
   - Matching engine
   - Payments / Stripe
   - Safety / SOS
   - Auth / Supabase
   - Driver onboarding
   - Notifications
   - Pricing / surge
   - Trip state machine

5. Surface domain-specific rules from CLAUDE.md
   Payment work    → "No floating-point for money"
   Safety work     → "SOS within 3 taps, no auto-dial 911"
   Auth work       → "Flag for human review before commit"
   Location work   → "Never log raw GPS coordinates"
   State machine   → "Never skip states"

6. Confirm and orient
   Show: branch name, base branch, domain, key rules, next steps
