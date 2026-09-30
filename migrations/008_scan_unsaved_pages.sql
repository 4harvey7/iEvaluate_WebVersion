-- 008: pages marking could not save ("Couldn't be saved" on the review page).
--
-- Review before applying. Not auto-applied to the live project. Run it in the
-- Supabase SQL Editor. Safe to re-run (create ... if not exists, constraints
-- guarded, create or replace, drop trigger if exists).
--
-- Why a NEW table (and not a failed_scan_queue row written by the backend):
--   * failed_scan_queue has a UNIQUE index on task_id
--     (failed_scan_queue_task_unique) and n8n's "Website Duplicate Check"
--     answers `duplicate` for any task_id already in failed_scan_queue. A row
--     written by the backend would make every later "Try again" a no-op
--     `duplicate`, and a real flag from n8n would fail with 409
--     (-> internal_error_not_saved again).
--   * The Flutter app lists every pending failed_scan_queue row. A page that
--     is still being retried automatically must not show up there.
--   So the page waits here (with the exact image the backend sent to n8n)
--   until a person decides: "Try again" (re-sent to n8n from this copy) or
--   "Type in the answers" (only THEN the backend writes a normal
--   failed_scan_queue + scan_error_images row, and the existing review /
--   correction flow takes over, on the website or in Flutter).
--
-- Only the backend (service role) touches this table. RLS is on with no
-- policies, so anon / authenticated clients (Flutter, browsers) can't read it.
-- Nothing else is changed: failed_scan_queue, scan_error_images,
-- scan_batch_items and their triggers/functions are untouched.
--
-- Until this is applied the backend keeps working exactly as before (pages
-- that can't be saved end as "Failed"); it picks the table up within ~5
-- minutes, no restart needed.
begin;

create table if not exists public.scan_unsaved_pages (
  id                  uuid primary key default gen_random_uuid(),
  item_id             uuid not null references public.scan_batch_items(id) on delete cascade,
  batch_id            uuid not null references public.scan_batches(id) on delete cascade,
  task_id             text not null,
  filename            text,
  -- The page exactly as the backend sent it to n8n (upright, prepared JPEG),
  -- base64 without a data: prefix. ~0.6-0.9 MB per page.
  image_base64        text not null,
  header_image_base64 text,                     -- optional OCR header crop
  page_meta           jsonb not null default '{}'::jsonb,  -- file_sha256, station_id, orientation, paper_size, preprocess
  review_reasons      text[] not null default '{}',        -- n8n's last reasons (e.g. omr_agreement_below_80, internal_error_not_saved)
  message             text,                     -- n8n's / the backend's last technical note (no secrets)
  status              text not null default 'retrying',
  outcome             text,                     -- once saved: success | flagged | rejected | duplicate
  watcher_failures    integer not null default 0,  -- failed automatic tries (watcher resends)
  website_attempts    integer not null default 0,  -- "Try again" clicks sent to n8n
  last_error          text,                     -- plain-English reason the last website try failed
  fsq_id              uuid,                     -- failed_scan_queue row made by "Type in the answers"
  acted_by            uuid,                     -- staff member of the last website action
  sending_started_at  timestamptz,
  version             integer not null default 0,  -- +1 on every change (optimistic locking)
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now()
);

do $$ begin
  if not exists (select 1 from pg_constraint where conname = 'scan_unsaved_pages_item_key') then
    alter table public.scan_unsaved_pages add constraint scan_unsaved_pages_item_key unique (item_id);
  end if;
  if not exists (select 1 from pg_constraint where conname = 'scan_unsaved_pages_status_check') then
    -- retrying: the laptop is still resending it automatically
    -- needs_review: automatic tries used up, waiting for a person
    -- sending: "Try again" in flight
    -- saved: marking stored it (see outcome)
    -- manual: moved to failed_scan_queue for hand entry (see fsq_id)
    alter table public.scan_unsaved_pages add constraint scan_unsaved_pages_status_check
      check (status in ('retrying', 'needs_review', 'sending', 'saved', 'manual'));
  end if;
end $$;

create index if not exists scan_unsaved_pages_batch_updated_idx
  on public.scan_unsaved_pages (batch_id, updated_at);

create or replace function public.scan_unsaved_pages_touch() returns trigger
language plpgsql set search_path = public as $$
begin
  new.updated_at := now();
  new.version := coalesce(old.version, 0) + 1;
  return new;
end $$;

-- Trigger-only function: nobody calls it directly (same hardening as 007).
revoke all on function public.scan_unsaved_pages_touch() from public, anon, authenticated;

drop trigger if exists scan_unsaved_pages_touch on public.scan_unsaved_pages;
create trigger scan_unsaved_pages_touch before update on public.scan_unsaved_pages
  for each row execute function public.scan_unsaved_pages_touch();

alter table public.scan_unsaved_pages enable row level security;
revoke all on table public.scan_unsaved_pages from anon, authenticated;
grant select, insert, update, delete on table public.scan_unsaved_pages to service_role;

commit;

-- Check afterwards (read-only):
-- select column_name, data_type from information_schema.columns
--  where table_schema = 'public' and table_name = 'scan_unsaved_pages' order by ordinal_position;

-- ROLLBACK (only if you want to remove the feature; the kept copies are lost):
-- begin;
-- drop trigger if exists scan_unsaved_pages_touch on public.scan_unsaved_pages;
-- drop function if exists public.scan_unsaved_pages_touch();
-- drop table if exists public.scan_unsaved_pages;
-- commit;
