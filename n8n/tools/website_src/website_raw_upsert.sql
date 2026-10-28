-- Same key as the shared upsert; never overwrites another page's row (public.save_website_raw_row,
-- migration 015: ON CONFLICT ... DO UPDATE ... WHERE s.task_id = EXCLUDED.task_id) and never stores a
-- second form with the same stamp in the term (reason duplicate_stamp + duplicate_of = DUP).
SELECT
  COALESCE((r.res ->> 'saved')::boolean, false) AS saved,
  r.res ->> 'reason'                            AS reason,
  r.res -> 'duplicate_of'                       AS duplicate_of
FROM (
  SELECT public.save_website_raw_row($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, $11, $12) AS res
) r;
