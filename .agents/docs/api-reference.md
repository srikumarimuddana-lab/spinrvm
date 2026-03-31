# Spinr API Reference

> **Living Document** — Update this file whenever API endpoints change.
> Last updated: 2026-03-26

## Base URL
- Development: `http://localhost:8000`
- Production: `https://<your-app>.fly.dev`

## Authentication
All authenticated endpoints require:
```
Authorization: Bearer <firebase_id_token or jwt_token>
```

---

## Auth Endpoints (`backend/routes/auth.py`)

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | `/api/auth/login` | No | Send OTP to phone number |
| POST | `/api/auth/verify-otp` | No | Verify OTP and get token |
| POST | `/api/auth/refresh` | Yes | Refresh auth token |

---

## Ride Endpoints (`backend/routes/rides.py`)

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | `/api/v1/rides` | Yes | Create a new ride request |
| GET | `/api/v1/rides` | Yes | List user's rides |
| GET | `/api/v1/rides/{ride_id}` | Yes | Get ride details |
| PATCH | `/api/v1/rides/{ride_id}` | Yes | Update ride (status, etc.) |
| DELETE | `/api/v1/rides/{ride_id}` | Yes | Cancel a ride |
| POST | `/api/v1/rides/{ride_id}/rate` | Yes | Rate a completed ride |

---

## Driver Endpoints (`backend/routes/drivers.py`)

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | `/api/v1/drivers/register` | Yes | Register as a driver |
| GET | `/api/v1/drivers/profile` | Yes | Get driver profile |
| PATCH | `/api/v1/drivers/status` | Yes | Update driver availability |
| PATCH | `/api/v1/drivers/location` | Yes | Update driver location |
| GET | `/api/v1/drivers/rides` | Yes | Get driver's ride history |
| POST | `/api/v1/drivers/{ride_id}/accept` | Yes | Accept a ride request |
| POST | `/api/v1/drivers/{ride_id}/reject` | Yes | Reject a ride request |

---

## Fare Endpoints (`backend/routes/fares.py`)

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | `/api/v1/fares/estimate` | Yes | Get fare estimate |

---

## Payment Endpoints (`backend/routes/payments.py`)

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | `/api/v1/payments/intent` | Yes | Create payment intent |
| GET | `/api/v1/payments/history` | Yes | Get payment history |
| POST | `/api/v1/payments/refund` | Yes | Request refund |

---

## Admin Endpoints (`backend/routes/admin.py`)

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `/api/admin/dashboard` | Admin | Get dashboard analytics |
| GET | `/api/admin/users` | Admin | List all users |
| GET | `/api/admin/drivers` | Admin | List all drivers |
| GET | `/api/admin/rides` | Admin | List all rides |
| PATCH | `/api/admin/users/{id}` | Admin | Update user |
| GET | `/api/admin/settings` | Admin | Get platform settings |
| PATCH | `/api/admin/settings` | Admin | Update platform settings |

---

## Corporate Accounts (`backend/routes/corporate_accounts.py`)

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | `/api/v1/corporate` | Admin | Create corporate account |
| GET | `/api/v1/corporate` | Admin | List corporate accounts |
| PATCH | `/api/v1/corporate/{id}` | Admin | Update corporate account |

---

## WebSocket Endpoints (`backend/routes/websocket.py`)

| Endpoint | Auth | Description |
|----------|------|-------------|
| `ws://host/ws/{user_id}` | Token in query | Real-time ride updates |

---

## Webhook Endpoints (`backend/routes/webhooks.py`)

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | `/api/v1/webhooks/stripe` | Stripe signature | Handle Stripe events |

---

## Error Response Format
```json
{
  "detail": "Error description here"
}
```

> **Note**: This reference is generated from the route files. For exact request/response schemas, check the Pydantic models in `backend/schemas.py`.
