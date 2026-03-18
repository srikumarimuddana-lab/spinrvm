-- Corporate accounts table for business rides and billing
CREATE TABLE IF NOT EXISTS corporate_accounts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL,
  contact_name text,
  contact_email text,
  contact_phone text,
  credit_limit numeric DEFAULT 0,
  is_active boolean DEFAULT true,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);

-- Create indexes for common queries
CREATE INDEX IF NOT EXISTS corporate_accounts_name_idx ON corporate_accounts(name);
CREATE INDEX IF NOT EXISTS corporate_accounts_contact_email_idx ON corporate_accounts(contact_email);
CREATE INDEX IF NOT EXISTS corporate_accounts_is_active_idx ON corporate_accounts(is_active);

-- Update updated_at trigger
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Apply trigger to corporate_accounts
DROP TRIGGER IF EXISTS update_corporate_accounts_updated_at ON corporate_accounts;
CREATE TRIGGER update_corporate_accounts_updated_at
BEFORE UPDATE ON corporate_accounts
FOR EACH ROW
EXECUTE FUNCTION update_updated_at_column();