# Spinr Platform Architecture

> **Living Document** — Update this file whenever the system architecture changes.
> Last updated: 2026-03-26

## System Overview

Spinr is a ride-sharing platform with four main components communicating through a REST API and WebSocket connections.

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  Rider App   │     │  Driver App  │     │   Admin      │
│  (Expo/RN)   │     │  (Expo/RN)   │     │  Dashboard   │
│              │     │              │     │  (Next.js)   │
└──────┬───────┘     └──────┬───────┘     └──────┬───────┘
       │                    │                    │
       │    REST API + WebSocket                 │
       └────────────┬───────┴────────────────────┘
                    │
           ┌────────▼────────┐
           │  Backend API    │
           │  (FastAPI)      │
           │  Fly.io         │
           └────────┬────────┘
                    │
        ┌───────────┼───────────┐
        │           │           │
   ┌────▼────┐ ┌───▼────┐ ┌───▼─────┐
   │Supabase │ │Firebase│ │ Stripe  │
   │(DB+Auth)│ │ Auth   │ │Payments │
   └─────────┘ └────────┘ └─────────┘
```

## Component Details

### Backend API (`backend/`)
| Aspect | Details |
|--------|---------|
| Framework | FastAPI |
| Language | Python 3.11+ |
| Hosting | Fly.io |
| Entry point | `backend/server.py` |
| API prefix | `/api/v1/` |
| Auth | Firebase tokens + legacy JWT |
| Logging | Loguru + Sentry |
| Config | `backend/core/config.py`, `.env` |

### Rider App (`rider-app/`)
| Aspect | Details |
|--------|---------|
| Framework | React Native 0.76 + Expo 54 |
| Navigation | Expo Router (file-based) |
| State | Zustand 5 |
| Maps | react-native-maps + Google Places |
| Payments | Stripe React Native |
| Auth | Firebase |

### Driver App (`driver-app/`)
| Aspect | Details |
|--------|---------|
| Framework | React Native + Expo |
| Navigation | Expo Router |
| State | Zustand |
| Maps | react-native-maps |

### Admin Dashboard (`admin-dashboard/`)
| Aspect | Details |
|--------|---------|
| Framework | Next.js |
| Language | TypeScript |
| Hosting | Vercel |

## External Services
| Service | Purpose | Config Location |
|---------|---------|----------------|
| Supabase | Database (PostgreSQL) + Auth | `backend/.env` |
| Firebase | Phone auth (OTP) | `backend/core/security.py` |
| Stripe | Payment processing | `backend/routes/payments.py` |
| Twilio | SMS notifications | `backend/sms_service.py` |
| Sentry | Error monitoring | `backend/server.py` |
| Google Maps | Maps + Places API | `rider-app/.env` |
| Cloudinary | Image uploads | `backend/.env` |

## Key Data Flows

### Ride Lifecycle
```
Rider requests ride → Backend creates ride record
→ Backend matches with nearby driver
→ Driver accepts/rejects (WebSocket)
→ Ride starts → Location updates (WebSocket)
→ Ride completes → Fare calculated
→ Payment processed (Stripe)
```

### Authentication Flow
```
User enters phone → Backend generates OTP
→ OTP sent via SMS (Twilio/Firebase)
→ User enters OTP → Backend verifies
→ Firebase token or JWT issued
→ Token sent with all subsequent requests
```

## Technical Debt
- [ ] Legacy JWT auth should be fully migrated to Firebase
- [ ] Some route files exceed 300 lines (rides.py: 33K, drivers.py: 45K, admin.py: 45K)
- [ ] Missing unit tests for most endpoints
- [ ] Debug log statements with sensitive info in `dependencies.py`
- [ ] `db.py` and `db_supabase.py` coexist — should consolidate
