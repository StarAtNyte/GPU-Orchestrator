-- Add username field to jobs table
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS username VARCHAR(100);

-- Create index for faster username lookups
CREATE INDEX IF NOT EXISTS idx_jobs_username ON jobs(username);

COMMENT ON COLUMN jobs.username IS 'Username of the user who submitted the job';
