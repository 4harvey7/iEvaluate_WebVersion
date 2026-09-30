-- Same key as the shared upsert; never overwrites another page's row.
WITH up AS (
  INSERT INTO public.sast_all_raw_data_survey AS s (
    "instructor_ID", subject_id, term_id, student_id, instructor,
    m1, m2, m3, m4, m5, m6, m7, m8, m9, m10,
    p1, p2, p3, p4, p5, p6, p7, p8, p9, p10,
    "Remarks_and_Suggestions", submitted_date, sao_staff_id,
    validation_status, manually_corrected, task_id
  ) VALUES (
    $1, $2, $3, $4, $5,
    ($10::jsonb ->> 'm1')::numeric, ($10::jsonb ->> 'm2')::numeric, ($10::jsonb ->> 'm3')::numeric,
    ($10::jsonb ->> 'm4')::numeric, ($10::jsonb ->> 'm5')::numeric, ($10::jsonb ->> 'm6')::numeric,
    ($10::jsonb ->> 'm7')::numeric, ($10::jsonb ->> 'm8')::numeric, ($10::jsonb ->> 'm9')::numeric,
    ($10::jsonb ->> 'm10')::numeric,
    ($10::jsonb ->> 'p1')::numeric, ($10::jsonb ->> 'p2')::numeric, ($10::jsonb ->> 'p3')::numeric,
    ($10::jsonb ->> 'p4')::numeric, ($10::jsonb ->> 'p5')::numeric, ($10::jsonb ->> 'p6')::numeric,
    ($10::jsonb ->> 'p7')::numeric, ($10::jsonb ->> 'p8')::numeric, ($10::jsonb ->> 'p9')::numeric,
    ($10::jsonb ->> 'p10')::numeric,
    $6, $7, $8,
    'auto', false, $9
  )
  ON CONFLICT (subject_id, term_id, "instructor_ID", student_id) DO UPDATE SET
    instructor = EXCLUDED.instructor,
    m1 = EXCLUDED.m1, m2 = EXCLUDED.m2, m3 = EXCLUDED.m3, m4 = EXCLUDED.m4, m5 = EXCLUDED.m5,
    m6 = EXCLUDED.m6, m7 = EXCLUDED.m7, m8 = EXCLUDED.m8, m9 = EXCLUDED.m9, m10 = EXCLUDED.m10,
    p1 = EXCLUDED.p1, p2 = EXCLUDED.p2, p3 = EXCLUDED.p3, p4 = EXCLUDED.p4, p5 = EXCLUDED.p5,
    p6 = EXCLUDED.p6, p7 = EXCLUDED.p7, p8 = EXCLUDED.p8, p9 = EXCLUDED.p9, p10 = EXCLUDED.p10,
    "Remarks_and_Suggestions" = EXCLUDED."Remarks_and_Suggestions",
    submitted_date = EXCLUDED.submitted_date,
    sao_staff_id = EXCLUDED.sao_staff_id,
    validation_status = EXCLUDED.validation_status,
    manually_corrected = EXCLUDED.manually_corrected
  WHERE s.task_id = EXCLUDED.task_id
  RETURNING s.task_id
)
SELECT (SELECT count(*) FROM up) > 0 AS saved;
