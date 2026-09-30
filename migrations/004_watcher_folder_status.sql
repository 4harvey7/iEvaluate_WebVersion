-- Watcher folder status reported by each laptop's folder watcher (2.3+) in
-- its heartbeat, shown on the Watcher setup / scanning pages, plus the
-- "pages scanned before the batch" command channel (watcher 2.4+).
-- Review before applying. Not auto-applied to the live project.
-- Safe to re-run (add column if not exists).
-- The backend keeps working before this is applied (it then only stores
-- last_seen_at / watcher_version, returns these fields as null, and the
-- "add to batch / discard" buttons answer 503 until it is applied).

alter table public.stations
  add column if not exists watcher_folder text,               -- folder the watcher is actually watching
  add column if not exists watcher_folder_exists boolean,     -- does that folder exist on the laptop
  add column if not exists watcher_folder_error text,         -- null | not_found | access_denied | unsafe_path
  add column if not exists watcher_queued_files int,          -- files seen but not yet delivered
  add column if not exists watcher_last_file_at timestamptz,  -- last time a page was sent successfully
  add column if not exists watcher_skipped_files int,         -- files waiting in skipped/before-batch/ (2.4+)
  add column if not exists watcher_command text,              -- pending command: null | resend_skipped | discard_skipped
  add column if not exists watcher_command_id text,           -- id the watcher echoes back as command_done
  add column if not exists watcher_command_at timestamptz;    -- when the command was set (expires after 15 min)
