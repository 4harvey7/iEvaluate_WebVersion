-- Batch still valid, no other stored form of this student, and (stamp rule, 2026-10-16) no other
-- stored form with this page's numbering-machine stamp in the resolved term ('' = no stamp).
WITH
bi AS (
  -- placeholder instructors never count as disabled
  SELECT ui.id::text                          AS id,
         btrim(COALESCE(ui.first_name, ''))   AS first_name,
         btrim(COALESCE(ui.last_name,  ''))   AS last_name,
         lower(COALESCE(ui.is_placeholder::text, 'false')) IN ('true', 't', '1', 'yes') AS is_placeholder,
         (     lower(COALESCE(ui.disabled::text, 'false')) IN ('true', 't', '1', 'yes')
           AND lower(COALESCE(ui.is_placeholder::text, 'false')) NOT IN ('true', 't', '1', 'yes') ) AS disabled
  FROM public.user_info ui
  WHERE ui.id = $1
  LIMIT 1
),
bs AS (
  SELECT s.id::text                     AS id,
         COALESCE(s.subject_code, '')   AS subject_code,
         COALESCE(s.subject_name, '')   AS subject_name
  FROM public.subjects s
  WHERE s.id = $2
  LIMIT 1
),
t AS (
  SELECT COALESCE(
           NULLIF(btrim(COALESCE($3, '')), ''),
           (SELECT ss.current_term_id::text
              FROM public.system_settings ss
             ORDER BY (ss.id::text = '1') DESC
             LIMIT 1)
         ) AS term_id
),
dup AS (
  SELECT r.task_id
  FROM public.sast_all_raw_data_survey r
  CROSS JOIN t
  WHERE btrim(COALESCE($4, '')) <> ''
    AND r."instructor_ID"::text = lower(btrim(COALESCE($1, '')))
    AND r.subject_id::text      = lower(btrim(COALESCE($2, '')))
    AND r.term_id::text         = t.term_id
    AND r.student_id            = btrim($4)
    AND r.task_id IS DISTINCT FROM $5
  LIMIT 1
),
stamp AS (
  SELECT public.sast_stamp_duplicate_of(t.term_id, btrim(COALESCE($6, '')), $5) AS dup
  FROM t
)
SELECT
  (bi.id IS NOT NULL)                                                    AS batch_instructor_exists,
  COALESCE(bi.disabled, false)                                           AS batch_instructor_disabled,
  COALESCE(bi.is_placeholder, false)                                     AS batch_instructor_is_placeholder,
  COALESCE(bi.first_name, '')                                            AS batch_instructor_first_name,
  COALESCE(bi.last_name, '')                                             AS batch_instructor_last_name,
  btrim(COALESCE(bi.first_name, '') || ' ' || COALESCE(bi.last_name, '')) AS batch_instructor_name,
  (bs.id IS NOT NULL)                                                    AS batch_subject_exists,
  COALESCE(bs.subject_code, '')                                          AS batch_subject_code,
  COALESCE(bs.subject_name, '')                                          AS batch_subject_name,
  t.term_id                                                              AS resolved_term_id,
  EXISTS (SELECT 1 FROM dup)                                             AS duplicate_student_exists,
  (SELECT dup.task_id FROM dup)                                          AS duplicate_student_task_id,
  (stamp.dup IS NOT NULL)                                                AS duplicate_stamp_exists,
  stamp.dup                                                              AS duplicate_stamp_of
FROM t
CROSS JOIN stamp
LEFT JOIN bi ON true
LEFT JOIN bs ON true;
