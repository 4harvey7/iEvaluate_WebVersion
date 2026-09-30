-- 010: page viewer on the live batch page (the picture and the score of
-- every scanned page).
--
-- Review before applying. Not auto-applied to the live project. Run it in the
-- Supabase SQL Editor AFTER 008 and 009 (it does not depend on them; they
-- only have to keep their numbers). Safe to re-run (add column if not exists,
-- create index if not exists, insert ... on conflict).
--
-- What it adds:
--   * scan_batch_items.result_summary (jsonb): the page's score, written by
--     the backend when marking answers (read from the row n8n stored, by
--     task_id), so the list polled every 3 s needs no extra query per row.
--   * scan_batch_items.page_image_path / page_image_saved_at: where the
--     page's viewing copy is in the bucket below, and when it was saved
--     (kept after the retention cleanup removes the picture, so the viewer
--     can say why it is gone).
--   * a PRIVATE storage bucket 'scan-pages' (public = false). NO storage
--     policies are created: with RLS on storage.objects that means nothing
--     in it is readable or writable with the anon key or a user's key. Only
--     the backend's service key (which bypasses RLS) uploads, reads and
--     removes files; staff see a picture only through the backend's
--     authenticated route GET /batches/{batch_id}/items/{item_id}/image.
--   * an index on sast_all_raw_data_survey(task_id) for the score lookup.
--     Read-only for the Flutter app and n8n (an index changes no query result).
--
-- Nothing here is granted to anon/authenticated. scan_batch_items already had
-- no grants for them on the live project (checked 2026-09-30, RLS on, no
-- policies); the revoke below only makes sure.

begin;

alter table public.scan_batch_items
  add column if not exists result_summary jsonb,
  add column if not exists page_image_path text,
  add column if not exists page_image_saved_at timestamptz;

comment on column public.scan_batch_items.result_summary is
  'Score summary of the page (backend app/page_scores.py): average, answered, instructor/subject credited, ratings m1..p10.';
comment on column public.scan_batch_items.page_image_path is
  'Object path of the page''s viewing copy in the private storage bucket scan-pages (<batch_id>/<item_id>.jpg); null = none kept.';
comment on column public.scan_batch_items.page_image_saved_at is
  'When the viewing copy was saved; kept after the retention cleanup removed it.';

-- Retention cleanup: pictures older than SCAN_IMAGE_RETENTION_DAYS.
create index if not exists scan_batch_items_page_image_saved_idx
  on public.scan_batch_items (page_image_saved_at)
  where page_image_path is not null;

-- Score lookup right after marking answered (one row per task_id).
create index if not exists sast_all_raw_data_survey_task_id_idx
  on public.sast_all_raw_data_survey (task_id)
  where task_id is not null;

-- Private bucket, JPEG only, 5 MB per file (viewing copies are ~250 KB).
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('scan-pages', 'scan-pages', false, 5242880, array['image/jpeg'])
on conflict (id) do update
  set public = false,
      file_size_limit = excluded.file_size_limit,
      allowed_mime_types = excluded.allowed_mime_types;

revoke all on public.scan_batch_items from anon, authenticated;

commit;

-- CHECKS (optional, read-only):
--   select id, public, file_size_limit, allowed_mime_types from storage.buckets where id = 'scan-pages';
--     -> public = false
--   select policyname, roles, cmd, qual from pg_policies where schemaname = 'storage';
--     -> no policy that lets anon/authenticated read bucket 'scan-pages'
--        (none existed when this was written; do not add one)
--   select grantee, privilege_type from information_schema.role_table_grants
--     where table_schema = 'public' and table_name = 'scan_batch_items';
--     -> only postgres / service_role

-- ROLLBACK (only if you want to remove the feature; the kept pictures are lost):
--   1. Empty the bucket first: Supabase dashboard > Storage > scan-pages >
--      select all > Delete (Supabase does not allow deleting storage.objects
--      rows with SQL).
--   2. Then:
-- begin;
-- delete from storage.buckets where id = 'scan-pages';
-- drop index if exists public.sast_all_raw_data_survey_task_id_idx;
-- drop index if exists public.scan_batch_items_page_image_saved_idx;
-- alter table public.scan_batch_items
--   drop column if exists page_image_saved_at,
--   drop column if exists page_image_path,
--   drop column if exists result_summary;
-- commit;
-- The backend keeps working after a rollback: the page viewer then says
-- pictures aren't switched on, and the list shows no scores.
