-- Migration: 99_cloud_messages_jsonb_columns.sql
-- Rollback:
--   ALTER TABLE cloud_messages ALTER COLUMN channels TYPE TEXT USING channels::text;
--   ALTER TABLE cloud_messages ALTER COLUMN particular_ids TYPE TEXT USING particular_ids::text;
--
-- The original migration (06_cloud_messaging.sql) declared `channels` and
-- `particular_ids` as TEXT. The backend inserts Python lists into these
-- columns; PostgREST serialises them as JSON strings on the wire.
-- The admin frontend called .map() on the returned string → TypeError crash,
-- and the fan-out loop iterated over zero users (string != array).
-- Converting to JSONB fixes both: PostgREST returns parsed arrays.

ALTER TABLE cloud_messages
    ALTER COLUMN channels
    TYPE JSONB
    USING CASE
        WHEN channels IS NULL THEN NULL
        WHEN channels LIKE '[%' THEN channels::jsonb
        ELSE to_jsonb(ARRAY[channels])
    END;

ALTER TABLE cloud_messages
    ALTER COLUMN particular_ids
    TYPE JSONB
    USING CASE
        WHEN particular_ids IS NULL THEN NULL
        WHEN particular_ids LIKE '[%' THEN particular_ids::jsonb
        ELSE to_jsonb(ARRAY[particular_ids])
    END;
