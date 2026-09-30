-- 009: the watcher 2.4 columns on stations that the live database is missing.
--
-- The live project was migrated with an earlier copy of
-- 004_watcher_folder_status.sql that only had the first five watcher_*
-- columns. The backend asks for all of them, so Postgres logs
-- "column stations.watcher_skipped_files does not exist" every few minutes
-- (the backend then falls back to the old columns, retries every 5 minutes,
-- and the "pages scanned before the batch" buttons answer 503).
-- The column names in the code are correct; the database is behind.
--
-- Review before applying. Run it in the Supabase SQL Editor. Safe to re-run
-- (add column if not exists). Purely additive and nullable: the Flutter app
-- never reads these columns. No backend restart is needed (it re-checks
-- within ~5 minutes).
alter table public.stations
  add column if not exists watcher_skipped_files int,         -- files waiting in skipped/before-batch/ (watcher 2.4+)
  add column if not exists watcher_command text,              -- pending command: null | resend_skipped | discard_skipped
  add column if not exists watcher_command_id text,           -- id the watcher echoes back as command_done
  add column if not exists watcher_command_at timestamptz;    -- when the command was set (expires after 15 min)

-- Check afterwards (read-only):
-- select column_name from information_schema.columns
--  where table_schema = 'public' and table_name = 'stations' and column_name like 'watcher_%' order by 1;

-- ROLLBACK (the backend then falls back to the older columns again):
-- alter table public.stations
--   drop column if exists watcher_skipped_files,
--   drop column if exists watcher_command,
--   drop column if exists watcher_command_id,
--   drop column if exists watcher_command_at;
