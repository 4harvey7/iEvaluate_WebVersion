-- ---------------------------------------------------------------------------
-- 012: index for the "same image in another batch" lookup.
--
-- Dedupe is per batch (claim_batch_item is keyed by (batch_id, file_sha256)),
-- so a copy of an already-scanned picture pasted into a NEW batch was marked
-- and saved again. Before calling n8n the backend now looks up
-- scan_batch_items by file_sha256 in other batches (success / flagged /
-- duplicate) and n8n flags the page 'reused_image' for staff to decide
-- (backend/app/routers/scans.py find_reused_image). The existing unique key
-- starts with batch_id, so it can't serve a lookup by file_sha256 alone.
--
-- Review before applying. Not auto-applied. Run in the Supabase SQL Editor
-- after 011. Safe to re-run (create index if not exists). Read-only for the
-- Flutter app and n8n (an index changes no query result). Without it the
-- lookup still works, only slower; a failed lookup never fails a scan.
-- ---------------------------------------------------------------------------
begin;

create index if not exists scan_batch_items_file_sha256_idx
  on public.scan_batch_items (file_sha256);

commit;
