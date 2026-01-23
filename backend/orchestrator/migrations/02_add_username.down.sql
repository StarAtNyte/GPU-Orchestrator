-- Remove username field from jobs table
DROP INDEX IF EXISTS idx_jobs_username;
ALTER TABLE jobs DROP COLUMN IF EXISTS username;
