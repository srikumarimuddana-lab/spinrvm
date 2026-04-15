# Spinr — Naming Conventions
<!-- CANONICAL REFERENCE. All contributors must follow this document. -->

**Last updated:** 2026-04-14

---

## 1. Git Branches

### Format
```
{type}/SPR-{nn}-{slug}
```

### Types
| Type | When to use |
|---|---|
| `feat` | New feature or screen |
| `fix` | Bug fix (code behavior) |
| `test` | Tests only — no production code changes |
| `docs` | Documentation only |
| `chore` | Deps, config, CI, build scripts |
| `refactor` | Restructuring with no behavior change |
| `security` | Security-specific hardening |

### Sprint Numbers
`nn` is zero-padded sprint number: `00`, `01`, `02`, …

### Slug
Lowercase, hyphen-separated, max 5 words. Describe the thing, not the action.

### Examples
```
feat/SPR-01-driver-chat-ws
fix/SPR-01-chat-polling-bug
test/SPR-02-e2e-ride-booking
docs/SPR-00-master-plan
chore/SPR-00-ci-conflict-resolution
security/SPR-01-api-key-rotation
refactor/SPR-01-frontend-consolidation
```

---

## 2. Commit Messages — Conventional Commits

### Format
```
{type}({scope}): {description} [SPR-{nn}]

{optional body — why, not what}
```

### Types
Same as branch types: `feat`, `fix`, `test`, `docs`, `chore`, `refactor`, `security`

### Scopes
| Scope | What it covers |
|---|---|
| `wallet` | Wallet feature (backend + mobile) |
| `chat` | In-ride chat (backend + mobile) |
| `loyalty` | Loyalty points |
| `quests` | Driver/rider quests |
| `fare-split` | Fare splitting |
| `scheduled` | Scheduled rides |
| `dark-mode` | Theme system |
| `offline` | Offline persistence |
| `backend` | Cross-cutting backend changes |
| `rider-app` | Rider-app cross-cutting |
| `driver-app` | Driver-app cross-cutting |
| `admin` | Admin dashboard |
| `shared` | Shared package |
| `ci` | GitHub Actions workflows |
| `deps` | Dependency updates |
| `docs` | Documentation |
| `e2e` | End-to-end tests |
| `frontend` | `frontend/` web app (consolidation work only) |

### Examples
```
feat(chat): wire driverStore.chatMessages to real-time WS [SPR-01]
fix(chat): remove 10s polling and fix broken useEffect hook [SPR-01]
test(e2e): add Playwright smoke test for ride booking flow [SPR-02]
chore(ci): add web export step to rider-app CI job [SPR-01]
docs(project): create MASTER_PLAN.md source of truth [SPR-00]
refactor(frontend): deprecate frontend/ app, add web target to rider-app [SPR-01]
```

### Rules
- Description is lowercase, imperative mood ("add" not "added", "fix" not "fixes")
- Max 72 chars on the first line (including type, scope, sprint tag)
- Body explains WHY if the change is non-obvious
- Never `--no-verify`

---

## 3. File Naming

### TypeScript / React Native
```
PascalCase.tsx       Components, screens
camelCase.ts         Hooks (useXxx.ts), stores, utilities
camelCase.test.ts    Unit tests
camelCase.spec.ts    Integration/E2E tests
```

### Python
```
snake_case.py        All Python files
test_snake_case.py   All test files (pytest convention)
```

### Documentation
```
SCREAMING_SNAKE.md   Key project docs (README, MASTER_PLAN, etc.)
lowercase-hyphen.md  Guides and runbooks
YYYY-MM-DD_slug.md   Date-stamped audit/report files
```

### SQL Migrations
```
NNN_snake_case.sql   Sequential, zero-padded to 3 digits
                     e.g. 018_add_chat_unread_count.sql
```

---

## 4. Versioning

### Mobile Apps — CalVer
```
v{YYYY}.{MM}.{PATCH}

v2026.04.1    First April release
v2026.04.2    Second April release (patch/hotfix)
v2026.05.1    First May release
```
`PATCH` starts at 1 for each month, increments with each release.

### API — SemVer
```
v{MAJOR}.{MINOR}.{PATCH}

v1.0.0    Current stable
v1.1.0    New features (loyalty, wallet, chat v2)
v1.1.1    Bug fixes on v1.1.0
v2.0.0    Breaking change (new auth scheme, etc.)
```

### Database Migrations
Continue the existing sequential numbering:
```
migrations/NNN_slug.sql    (currently at 024)
```
Never renumber existing migrations. If a migration needs correction, add a new one.

---

## 5. Environment Variables

### Format
```
SCREAMING_SNAKE_CASE

Backend (.env):
  SUPABASE_URL
  SUPABASE_SERVICE_ROLE_KEY
  JWT_SECRET
  FIREBASE_SERVICE_ACCOUNT_JSON

Mobile (.env):
  EXPO_PUBLIC_*    (exposed to JS bundle — never put secrets here)

Admin (.env.local):
  NEXT_PUBLIC_*    (exposed to browser bundle — never put secrets here)
```

### Rules
- Never commit real values — only `your-placeholder-value` in `.env.example`
- `EXPO_PUBLIC_` and `NEXT_PUBLIC_` prefixed vars are public; never put API keys there
- Rotate any key that was ever committed — see `docs/deploy/SECRETS_INVENTORY.md`

---

## 6. API Endpoints

### Format
```
/api/v1/{resource}/{id?}/{action?}

/api/v1/rides                    Collection
/api/v1/rides/{id}               Resource
/api/v1/rides/{id}/cancel        Action on resource
/api/v1/drivers/earnings/export  Sub-resource action
```

### Rules
- All lowercase, hyphen-separated
- Plural nouns for collections (`/rides` not `/ride`)
- POST for actions that change state (`/cancel`, `/complete`, `/accept`)
- Avoid verbs in the resource name itself

---

## 7. WebSocket Message Types

### Format
```json
{ "type": "snake_case_verb_noun", ...payload }
```

### Registry
| type | Direction | Description |
|---|---|---|
| `auth` | C→S | First message after connect; contains `token` |
| `ping` | S→C | Heartbeat ping |
| `pong` | C→S | Heartbeat reply |
| `driver_location` | C→S | Driver sends location update |
| `driver_location_update` | S→C | Backend broadcasts location to rider |
| `location_batch` | C→S | Batch GPS upload |
| `location_batch_ack` | S→C | Batch received confirmation |
| `chat_message` | Bidirectional | In-ride chat message |
| `ride_status_changed` | S→C | Generic status update |
| `driver_accepted` | S→C | Driver accepted the ride |
| `driver_arrived` | S→C | Driver arrived at pickup |
| `ride_started` | S→C | Ride in progress |
| `ride_completed` | S→C | Ride finished |
| `ride_cancelled` | S→C | Ride cancelled |
| `new_ride_assignment` | S→C | New ride offer for driver |
| `error` | S→C | Error response |

---

## 8. Logging (Backend)

### Format — loguru structured fields
```python
logger.info("module.action",    user_id=uid, result="ok",    ms=elapsed)
logger.warning("module.action", user_id=uid, error="reason")
logger.error("module.action",   user_id=uid, exc_info=True)
```

### Module names
```
auth.otp_send / auth.otp_verify
ride.create / ride.accept / ride.complete / ride.cancel
driver.location_update / driver.status_change
chat.send / chat.receive
wallet.topup / wallet.pay / wallet.transfer
payment.charge / payment.refund
```

---

## 9. Test IDs

### Format
```
test_{module}_{scenario}_{expected_outcome}

test_wallet_topup_success
test_wallet_topup_insufficient_balance
test_chat_send_message_to_disconnected_driver
test_ride_accept_race_condition_second_driver_gets_400
```

---

## 10. Sprint / Task IDs

| Format | Example | Used in |
|---|---|---|
| `SPR-{nn}` | `SPR-01` | Sprint number (in branches, commits, docs) |
| `{nn}{letter}` | `1a`, `1b` | Task within sprint (in MASTER_PLAN) |
| `D-{nnn}` | `D-001` | Architecture Decision Record |
| `OPS-{nn}` | `OPS-01` | Operator task (human-gated) |
