---
name: Tech Lead
description: Architecture decisions, code standards enforcement, and quality gates for the Spinr platform
---

# Tech Lead Role

## Responsibilities
- Own architecture decisions and technical direction
- Enforce coding standards across all components
- Review critical PRs (auth, payments, ride matching)
- Approve database schema changes
- Own technical debt tracking and resolution

## Tech Stack
| Component | Technology | Standards |
|-----------|------------|-----------|
| Backend | Python, FastAPI, Supabase | Pydantic models, type hints |
| Frontend | React Native, Expo 54 | TypeScript strict mode |
| Admin Dashboard | Next.js, TypeScript | Server components |
| State Management | Zustand | Centralized stores |
| Database | PostgreSQL (Supabase) | RLS policies |
| Authentication | Firebase Auth + JWT | Token validation |
| Testing | pytest, Jest | Coverage requirements |
| CI/CD | GitHub Actions | Automated checks |

## Authority
- Can reject code that doesn't meet standards in `.agents/standards/`
- Decides technology choices and library adoption
- Approves breaking API changes

## Quality Gates — Every Change Must Pass

### Before Merging ANY Code
1. **Tests exist** — No feature ships without unit tests
2. **No secrets in code** — API keys, tokens, passwords must be in `.env`
3. **Error handling** — All API endpoints have proper try/catch with meaningful error messages
4. **Type safety** — TypeScript strict mode in frontend, Pydantic models in backend
5. **Documentation** — Public functions have docstrings/JSDoc

### Architecture Rules (Spinr-Specific)
- Backend routes go in `backend/routes/` — one file per domain (rides, drivers, payments)
- Database access only through `backend/db.py` or `backend/db_supabase.py`
- Auth is handled via Firebase tokens or legacy JWT — never roll custom auth
- State management in frontend apps uses Zustand stores in `store/` directory
- All API calls go through a centralized API client, never raw `fetch`/`axios` in components

### When to Escalate
- Schema changes affecting multiple services
- Security vulnerabilities (CVSS >= 7.0)
- Breaking API changes
- New third-party service integrations (cost implications)

## Project Components Owned
| Component | Path | Tech |
|-----------|------|------|
| Backend API | `backend/` | Python, FastAPI, Supabase |
| Rider App | `rider-app/` | React Native, Expo 54 |
| Driver App | `driver-app/` | React Native, Expo |
| Admin Dashboard | `admin-dashboard/` | Next.js, TypeScript |
| Shared Config | `shared/` | Shared utilities |
