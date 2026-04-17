-- Migration 30 (AUDIT ONLY — read-only; makes no changes)
-- =========================================================
-- Before we add UNIQUE(drivers.user_id), UNIQUE(drivers.phone), and flip
-- the Admin Users / Admin Drivers filters, we need to know what duplicate
-- and orphaned rows exist TODAY. The UNIQUE migration will fail if the
-- data already violates the constraints — so decide merges/deletes first.
--
-- Run this whole file in Supabase SQL Editor. Each SELECT is labeled.
-- Copy the output back into the chat so we can decide what to do with
-- each group of duplicates.

-- ── 1. Drivers sharing the same phone (the headline problem) ──────
-- Expect: clusters of 2+ rows per phone. Each cluster is one person
-- whose driver record got duplicated.
SELECT
    '1_duplicate_drivers_by_phone' AS audit,
    phone,
    COUNT(*) AS driver_row_count,
    array_agg(id ORDER BY created_at)            AS driver_ids,
    array_agg(user_id ORDER BY created_at)       AS user_ids,
    array_agg(status ORDER BY created_at)        AS statuses,
    array_agg(is_verified ORDER BY created_at)   AS verified_flags,
    array_agg(created_at ORDER BY created_at)    AS created_timestamps
FROM drivers
GROUP BY phone
HAVING COUNT(*) > 1
ORDER BY COUNT(*) DESC, phone;

-- ── 2. Drivers with NULL user_id (detached — never linked to a user) ──
-- Expect: zero is ideal. Any row here was created bypassing the normal
-- /drivers/register flow (admin create, manual SQL, legacy import).
SELECT
    '2_drivers_without_user' AS audit,
    id AS driver_id,
    phone,
    status,
    is_verified,
    created_at
FROM drivers
WHERE user_id IS NULL
ORDER BY created_at DESC;

-- ── 3. Drivers whose user_id points at a user that doesn't exist ──
-- Expect: zero. If any show up, the user row was deleted but the driver
-- row wasn't cascaded.
SELECT
    '3_drivers_pointing_to_missing_user' AS audit,
    d.id     AS driver_id,
    d.user_id,
    d.phone,
    d.status,
    d.created_at
FROM drivers d
LEFT JOIN users u ON u.id = d.user_id
WHERE d.user_id IS NOT NULL
  AND u.id IS NULL;

-- ── 4. Multiple drivers pointing at the same user_id ──────────────
-- Even when phone looks fine, user_id should be 1:1 with drivers.
SELECT
    '4_multiple_drivers_per_user' AS audit,
    user_id,
    COUNT(*) AS driver_row_count,
    array_agg(id ORDER BY created_at) AS driver_ids,
    array_agg(phone ORDER BY created_at) AS phones
FROM drivers
WHERE user_id IS NOT NULL
GROUP BY user_id
HAVING COUNT(*) > 1;

-- ── 5. Users with role='driver' but no driver row ─────────────────
-- These users got their role flipped to 'driver' somewhere but no
-- driver record was created. Rider-app would treat them as driver,
-- but Admin Drivers page wouldn't list them.
SELECT
    '5_users_role_driver_without_driver_row' AS audit,
    u.id AS user_id,
    u.phone,
    u.role,
    u.is_driver,
    u.created_at
FROM users u
LEFT JOIN drivers d ON d.user_id = u.id
WHERE u.role = 'driver'
  AND d.id IS NULL
ORDER BY u.created_at DESC;

-- ── 6. Users with a driver row but users.role is NOT 'driver' ─────
-- Flip side of #5: they registered as driver but users.role stayed 'rider'.
-- This is the current /drivers/register bug (drivers.py:365).
SELECT
    '6_drivers_with_rider_role_user' AS audit,
    u.id   AS user_id,
    u.phone,
    u.role AS users_role,
    u.is_driver,
    d.id   AS driver_id,
    d.status AS driver_status,
    d.created_at AS driver_created_at
FROM users u
JOIN drivers d ON d.user_id = u.id
WHERE u.role <> 'driver'
ORDER BY u.created_at DESC;

-- ── 7. Same phone appears on BOTH a user row and a detached driver row ──
-- i.e. users.phone = drivers.phone but drivers.user_id is NULL or doesn't
-- match the user. These are the "two account with same number" cases.
SELECT
    '7_phone_split_across_user_and_driver' AS audit,
    u.id   AS user_id,
    u.phone,
    u.role AS users_role,
    d.id   AS driver_id,
    d.user_id AS driver_user_id,
    d.status AS driver_status
FROM users u
JOIN drivers d ON d.phone = u.phone
WHERE d.user_id IS DISTINCT FROM u.id
ORDER BY u.phone;

-- ── 8. Summary counts ─────────────────────────────────────────────
SELECT '8_summary_counts' AS audit,
       (SELECT COUNT(*) FROM users)                                   AS total_users,
       (SELECT COUNT(*) FROM users WHERE role='rider')                AS users_rider,
       (SELECT COUNT(*) FROM users WHERE role='driver')               AS users_driver,
       (SELECT COUNT(*) FROM users WHERE role NOT IN ('rider','driver')) AS users_other_role,
       (SELECT COUNT(*) FROM drivers)                                 AS total_drivers,
       (SELECT COUNT(*) FROM drivers WHERE user_id IS NULL)           AS drivers_detached,
       (SELECT COUNT(DISTINCT phone) FROM drivers)                    AS distinct_driver_phones;
