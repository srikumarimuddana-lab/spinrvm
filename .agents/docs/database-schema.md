# Spinr Database Schema

> **Living Document** — Update this file whenever database tables or columns change.
> Last updated: 2026-03-26
> Source of truth: `backend/supabase_schema.sql`

## Database

- **Provider**: Supabase (PostgreSQL)
- **Schema file**: `backend/supabase_schema.sql`
- **RLS policies**: `backend/supabase_rls.sql`
- **Migrations**: `backend/migrations/`

## Core Tables

### users
| Column | Type | Description |
|--------|------|-------------|
| id | UUID (PK) | Firebase UID or generated |
| phone | VARCHAR | Phone number |
| email | VARCHAR | Email (optional) |
| name | VARCHAR | Display name |
| role | VARCHAR | `rider`, `driver`, `admin` |
| profile_complete | BOOLEAN | Profile setup status |
| current_session_id | VARCHAR | For single-device enforcement |
| created_at | TIMESTAMP | Registration date |
| updated_at | TIMESTAMP | Last profile update |

### drivers
| Column | Type | Description |
|--------|------|-------------|
| id | UUID (PK) | Driver record ID |
| user_id | UUID (FK → users) | Linked user account |
| status | VARCHAR | `online`, `offline`, `on_ride` |
| vehicle_type | VARCHAR | `standard`, `premium`, `xl` |
| vehicle_details | JSONB | Make, model, plate, color |
| current_location | POINT | Lat/lng coordinates |
| rating | DECIMAL | Average driver rating |
| documents_verified | BOOLEAN | Document verification status |
| created_at | TIMESTAMP | Registration date |

### rides
| Column | Type | Description |
|--------|------|-------------|
| id | UUID (PK) | Ride ID |
| rider_id | UUID (FK → users) | Rider who requested |
| driver_id | UUID (FK → drivers) | Assigned driver |
| status | VARCHAR | `requested`, `matched`, `accepted`, `started`, `completed`, `cancelled` |
| pickup_location | JSONB | `{lat, lng, address}` |
| dropoff_location | JSONB | `{lat, lng, address}` |
| vehicle_type | VARCHAR | Requested vehicle type |
| fare_estimate | DECIMAL | Estimated fare |
| fare_actual | DECIMAL | Final fare |
| distance_km | DECIMAL | Trip distance |
| duration_minutes | DECIMAL | Trip duration |
| rating_by_rider | INTEGER | Rider's rating (1-5) |
| rating_by_driver | INTEGER | Driver's rating (1-5) |
| created_at | TIMESTAMP | Request time |
| started_at | TIMESTAMP | Trip start time |
| completed_at | TIMESTAMP | Trip end time |

### payments
| Column | Type | Description |
|--------|------|-------------|
| id | UUID (PK) | Payment ID |
| ride_id | UUID (FK → rides) | Associated ride |
| user_id | UUID (FK → users) | Payer |
| amount | DECIMAL | Payment amount |
| currency | VARCHAR | Currency code |
| stripe_payment_id | VARCHAR | Stripe payment intent ID |
| status | VARCHAR | `pending`, `completed`, `refunded`, `failed` |
| created_at | TIMESTAMP | Payment time |

### promotions
| Column | Type | Description |
|--------|------|-------------|
| id | UUID (PK) | Promotion ID |
| code | VARCHAR | Promo code |
| discount_type | VARCHAR | `percentage`, `fixed` |
| discount_value | DECIMAL | Discount amount |
| valid_from | TIMESTAMP | Start date |
| valid_until | TIMESTAMP | End date |
| max_uses | INTEGER | Usage limit |
| current_uses | INTEGER | Times used |

### corporate_accounts
| Column | Type | Description |
|--------|------|-------------|
| id | UUID (PK) | Account ID |
| company_name | VARCHAR | Company name |
| billing_email | VARCHAR | Billing contact |
| credit_limit | DECIMAL | Monthly limit |
| active | BOOLEAN | Account status |

### notifications
| Column | Type | Description |
|--------|------|-------------|
| id | UUID (PK) | Notification ID |
| user_id | UUID (FK → users) | Recipient |
| type | VARCHAR | Notification type |
| title | VARCHAR | Title |
| body | TEXT | Message body |
| read | BOOLEAN | Read status |
| created_at | TIMESTAMP | Sent time |

## Row Level Security (RLS)
- Users can only read/update their own records
- Drivers can only update their own driver profile
- Rides are visible only to the rider and assigned driver
- Admin role can access all records
- See `backend/supabase_rls.sql` for exact policies

## Relationships
```
users 1──── many rides (as rider)
users 1──── 1 drivers (optional)
drivers 1──── many rides (as driver)
rides 1──── 1 payments
users 1──── many notifications
```
