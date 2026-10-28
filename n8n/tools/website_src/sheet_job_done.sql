-- Google Sheet import job (migration 018): the whole flow has finished. Real counts; 'done' unless a
-- failure was recorded ('Mark Import Job Failed' or found by the summary), which stays 'error'.
-- Parameters: job id, rows, matched, unmatched, imported, errors, AI groups, AI failed,
-- has problems, first failed node, message.
UPDATE public.sheet_import_jobs
   SET status = CASE WHEN status = 'error' OR $9::boolean THEN 'error' ELSE 'done' END,
       rows = $2::int,
       matched = $3::int,
       unmatched = $4::int,
       imported_count = $5::int,
       error_count = $6::int,
       ai_groups = $7::int,
       ai_failed = $8::int,
       failed_node = COALESCE(failed_node, NULLIF($10::text, '')),
       message = CASE WHEN status = 'error' AND message IS NOT NULL AND failed_node IS NOT NULL
                      THEN message || ' ' || $11::text
                      ELSE $11::text END,
       finished_at = now()
 WHERE id = $1::uuid
   AND finished_at IS NULL;
