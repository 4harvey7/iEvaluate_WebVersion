-- 003_repair_import_error_scan_images.sql
-- Repairs import errors created from a corrected flagged scan (webhook
-- sast-manual-correction) that show no scan image in the Flutter app.
--
-- Cause (fixed in n8n/perfect-101-website.json, build_website_branch.py
-- serialize_correction_image_lookups / guard_scan_image_save): the correction flow
-- looked up the original scan image AFTER it had already written the import error,
-- so it saved a NEW scan_error_images row with image_base64 = NULL and pointed
-- import_errors.scan_image_id at that empty row. The original image is still
-- stored and still linked from failed_scan_queue.scan_image_id (same task_id).
--
-- What this does
--   1. Points every import error whose scan image row is empty back at the
--      original image of the failed_scan_queue row with the same task_id
--      (only when that image really holds data).
--   2. Deletes scan_error_images rows that hold no image AND are no longer
--      referenced by import_errors or failed_scan_queue (the empty copies).
-- No other table or column is touched. Safe to re-run (a second run changes nothing).
--
-- Run manually in the Supabase SQL Editor (Dashboard -> SQL Editor -> paste -> Run).
-- Optional: run the PREVIEW query first on its own.

-- PREVIEW (read only): how many import errors will be relinked / empty rows removed.
-- select
--   (select count(*) from public.import_errors ie
--      join public.scan_error_images s on s.id = ie.scan_image_id
--     where coalesce(s.image_base64, '') = '') as import_errors_on_empty_image,
--   (select count(*) from public.scan_error_images s
--     where coalesce(s.image_base64, '') = '') as empty_image_rows;

begin;

-- ---------------------------------------------------------------------------
-- 1. Relink import errors to the original scan image
-- ---------------------------------------------------------------------------
with fix as (
  select distinct on (ie.id)
         ie.id          as import_error_id,
         f.scan_image_id as original_image_id
  from public.import_errors ie
  join public.scan_error_images cur  on cur.id = ie.scan_image_id
  join public.failed_scan_queue f    on f.task_id = ie.task_id
                                    and f.scan_image_id is not null
                                    and f.scan_image_id <> ie.scan_image_id
  join public.scan_error_images orig on orig.id = f.scan_image_id
  where coalesce(cur.image_base64, '') = ''
    and coalesce(orig.image_base64, '') <> ''
  order by ie.id, f.created_at desc
)
update public.import_errors ie
set scan_image_id = fix.original_image_id
from fix
where ie.id = fix.import_error_id;

-- ---------------------------------------------------------------------------
-- 2. Remove empty, unreferenced scan_error_images rows
-- ---------------------------------------------------------------------------
delete from public.scan_error_images s
where coalesce(s.image_base64, '') = ''
  and not exists (select 1 from public.import_errors ie    where ie.scan_image_id = s.id)
  and not exists (select 1 from public.failed_scan_queue f where f.scan_image_id = s.id);

commit;

-- VERIFY (read only): both counts should be 0 afterwards (an import error whose task has
-- no stored original image cannot be relinked; it keeps its row and is still counted).
-- select
--   (select count(*) from public.import_errors ie
--      join public.scan_error_images s on s.id = ie.scan_image_id
--     where coalesce(s.image_base64, '') = '') as import_errors_on_empty_image,
--   (select count(*) from public.scan_error_images s
--     where coalesce(s.image_base64, '') = '') as empty_image_rows;
