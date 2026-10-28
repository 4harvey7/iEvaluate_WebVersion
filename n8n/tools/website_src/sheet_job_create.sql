-- Google Sheet import job (migration 018): one row per import, before the early answer.
-- Parameters: rows, matched, unmatched, n8n execution id, requesting user (uuid or NULL).
INSERT INTO public.sheet_import_jobs (status, rows, matched, unmatched, error_count, message, n8n_execution_id, requested_by)
VALUES (
  'processing',
  $1::int,
  $2::int,
  $3::int,
  $3::int,
  'Import started: saving the rows and running the AI remark analysis.',
  NULLIF($4::text, ''),
  NULLIF($5::text, '')::uuid
)
RETURNING id;
