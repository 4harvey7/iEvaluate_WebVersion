-- Google Sheet import job (migration 018): the execution itself failed (a node without an error
-- output threw, Stop and Error, a timeout). Run by this workflow's own Error Trigger.
-- Parameters: failed execution id, last node executed, message. Not a sheet import: no-op.
UPDATE public.sheet_import_jobs
   SET status = 'error',
       failed_node = COALESCE(NULLIF($2::text, ''), failed_node),
       message = $3::text,
       finished_at = now()
 WHERE n8n_execution_id = NULLIF($1::text, '')
   AND finished_at IS NULL;
