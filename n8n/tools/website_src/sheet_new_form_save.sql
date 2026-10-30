-- Google Sheet import of the new 15-item forms (migration 019): one row per sheet row.
-- Parameters: form code, instructor, subject, term, answers (json text), comments, stamp no,
-- supervisor (email or name, SUP-15 only), task id, import job id, row number, requesting user,
-- details (json text: student_id, section, program_level, program_year, submitted_date, academic_rank, designation; migration 020).
-- SUP-15: the supervisor is matched to an approved DEPARTMENT_HEAD by exact email first, else by name
-- similarity (>= 0.5); not found = the row is not saved. SET-15: the student id (if any) goes to the database for the SAO office only (duplicate check).
WITH ev AS (
  SELECT ui.id
  FROM public.user_info ui
  JOIN public.department_table dt ON dt.user_id = ui.id
  JOIN public.roles r             ON r.id = dt.roles
  WHERE $1 = 'SUP-15'
    AND r."Roles" = 'DEPARTMENT_HEAD'
    AND ui.account_status = 'approved'
    AND btrim(COALESCE($8, '')) <> ''
    AND (lower(btrim(COALESCE(ui.email, ''))) = lower(btrim($8))
         OR similarity(COALESCE(ui.first_name, '') || ' ' || COALESCE(ui.last_name, ''), btrim($8)) >= 0.5)
  ORDER BY (lower(btrim(COALESCE(ui.email, ''))) = lower(btrim($8))) DESC,
           similarity(COALESCE(ui.first_name, '') || ' ' || COALESCE(ui.last_name, ''), btrim($8)) DESC
  LIMIT 1
)
SELECT CASE
         WHEN $1 = 'SUP-15' AND NOT EXISTS (SELECT 1 FROM ev)
           THEN jsonb_build_object('saved', false, 'reason', 'supervisor_not_found')
         ELSE public.save_eval_response(
                $1, $2, $3, $4, $5::jsonb, NULLIF($6, ''), 'sheet', NULLIF($7, ''), 'sheet',
                (SELECT id::text FROM ev), NULLIF($10, ''), $9, NULLIF($12, ''), 'submitted', true,
                NULLIF($13, '')::jsonb)
       END AS result,
       NULLIF($11, '')::int AS row_no;
