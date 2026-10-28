-- Google Sheet import job (migration 018): a step after the early answer failed. The first failure
-- keeps its node and message; the import goes on, 'Mark Import Job Done' sets finished_at later.
-- Parameters: n8n execution id, failed node, message. No job row (phone, website, correction): no-op.
UPDATE public.sheet_import_jobs
   SET status = 'error',
       failed_node = COALESCE(failed_node, $2::text),
       message = CASE WHEN failed_node IS NULL THEN $3::text ELSE message END
 WHERE n8n_execution_id = $1::text
   AND finished_at IS NULL;
