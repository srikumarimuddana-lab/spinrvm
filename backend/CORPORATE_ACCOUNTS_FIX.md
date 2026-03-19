# Corporate Accounts API Fix

## Problem
The admin dashboard was showing a console error when trying to access the corporate accounts page:
```
API Error: /api/v1/admin/corporate-accounts {}
src/lib/api.ts (33:21) @ request
```

## Root Cause
The `corporate_accounts` table was missing from the main Supabase schema file (`supabase_schema.sql`), even though it was defined in the migration file (`05_corporate_accounts.sql`). This caused the API endpoint to fail when trying to query the database.

## Solution

### 1. Created Separate Corporate Accounts Schema File
**File:** `spinr/backend/corporate_accounts_schema.sql`

This file contains:
- Corporate accounts table definition with UUID primary key
- Proper indexes for performance (name, email, active status)
- Updated_at trigger for automatic timestamp updates
- Row Level Security (RLS) enabled
- Admin-only access policy

### 2. Updated Main Schema File
**File:** `spinr/backend/supabase_schema.sql`

- Removed corporate accounts section from main schema
- Added reference to the separate corporate accounts schema file
- Maintains clean separation of concerns

### 3. Created Test Suite
**Files:** 
- `spinr/backend/test_corporate_accounts.py` - Comprehensive test suite
- `spinr/backend/run_test.bat` - Windows test runner

## How to Apply the Fix

### Step 1: Run the Corporate Accounts Schema
1. Go to Supabase Dashboard → SQL Editor
2. Copy and paste the contents of `spinr/backend/corporate_accounts_schema.sql`
3. Execute the SQL to create the table, trigger function, and policies

**Note:** The schema file includes the `update_updated_at_column()` function which automatically updates the `updated_at` timestamp when records are modified.

### Step 2: Fix Frontend API Endpoints (IMPORTANT)
The frontend was using `/api/v1/admin/` endpoints but the backend uses `/api/admin/` endpoints. All API calls have been updated to match the backend routes:

- ✅ Fixed auth session endpoint: `/api/admin/auth/session`
- ✅ Fixed all admin endpoints: `/api/admin/stats`, `/api/admin/rides`, etc.
- ✅ Fixed corporate accounts endpoint: `/api/admin/corporate-accounts`
- ✅ Fixed all other admin functionality endpoints

**Files Updated:**
- `spinr/admin-dashboard/src/lib/api.ts` - All API endpoints corrected
- `spinr/admin-dashboard/src/store/authStore.ts` - Session endpoint corrected

### Step 2: Test the Fix
1. Run the test suite:
   ```bash
   cd spinr/backend
   python test_corporate_accounts.py
   ```
2. Or use the Windows batch file:
   ```bash
   spinr/backend/run_test.bat
   ```

### Step 3: Test the Admin Dashboard
1. Navigate to the corporate accounts page in the admin dashboard
2. The page should now load without errors
3. You can create, edit, and delete corporate accounts

## Files Modified/Created

### New Files:
- `spinr/backend/corporate_accounts_schema.sql` - Corporate accounts schema
- `spinr/backend/CORPORATE_ACCOUNTS_FIX.md` - This documentation
- `spinr/backend/test_corporate_accounts.py` - Test suite
- `spinr/backend/run_test.bat` - Windows test runner

### Modified Files:
- `spinr/backend/supabase_schema.sql` - Removed corporate accounts section, added reference

## Table Structure

```sql
corporate_accounts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT NOT NULL,
    contact_name    TEXT,
    contact_email   TEXT,
    contact_phone   TEXT,
    credit_limit    NUMERIC DEFAULT 0,
    is_active       BOOLEAN DEFAULT true,
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);
```

## Security
- Row Level Security (RLS) enabled
- Admin-only access policy
- Only users with `role = 'admin'` can access corporate accounts

## Next Steps
After applying this fix, the corporate accounts functionality in the admin dashboard should work properly without any API errors.