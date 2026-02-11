# Spinr — Discovery Report

## Overview
This report summarizes the repository structure, technology stack, dependencies, security highlights, and recommended next steps for the Spinr rideshare project.

## Repo Structure (top-level)
- `backend/` — FastAPI server (`server.py`), `requirements.txt`
- `frontend/` — Expo React Native app, `package.json`, `app/` screens, `store/`
- `config/` — app configuration (`spinr.config.ts`)
- `tests/` — Python tests
- `memory/`, `Swagger UI_files/`, `test_reports/` — auxiliary
- Root files: `README.md`, `backend_test.py`, `test_result.md`

## Key Components
- Backend API + WebSockets: `backend/server.py` (FastAPI, Motor for MongoDB)
- Frontend mobile/web app: `frontend/` (Expo, React Native, TypeScript, `expo-router`)
- State: `frontend/store` (Zustand)
- Auth: OTP + JWT (server + client usage in `authStore.ts`)
- Payments: Stripe integration referenced in both front and back
- Real-time: WebSocket manager implemented in `server.py`

## Tech Stack (concise)
- Frontend: Expo SDK, React Native, TypeScript, `react-native-maps`, `expo-location`, `@stripe/stripe-react-native`, `axios`, `expo-secure-store`
- Backend: Python 3.x, FastAPI, Uvicorn, Motor (async MongoDB), Pydantic, PyJWT, Stripe Python SDK
- Database: MongoDB
- Testing & Dev: pytest, black, flake8, ESLint

## Artifacts
- Tech stack & dependency lists: `discovery/tech-stack.md` (generated)
- Security findings & remediation: `discovery/security.md`
- Feature gap list: `discovery/features.md`

## Next recommended actions
1. Review `discovery/security.md` and prioritize high-impact fixes (JWT secret, WebSocket auth, OTP rate-limits, CORS).  
2. Implement server-side Stripe Payment Intents and webhook verification before production payments.  
3. Add CI checks: dependency auditing, linting, and tests.  
4. Build an admin dashboard and KYC pipeline for driver verification.  

---
Generated files:
- [discovery/report.md](discovery/report.md)
- [discovery/security.md](discovery/security.md)
- [discovery/features.md](discovery/features.md)
