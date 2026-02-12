-- Add user_id to drivers table
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='drivers' AND column_name='user_id') THEN
        ALTER TABLE drivers ADD COLUMN user_id uuid REFERENCES users(id);
        CREATE INDEX IF NOT EXISTS drivers_user_id_idx ON drivers(user_id);
    END IF;
END $$;
