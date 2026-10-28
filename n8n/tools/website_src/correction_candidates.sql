-- sast-manual-correction: who and what a correction can be credited to, for the LOCAL text match in
-- 'Correction Name Match' (name_match.js). Only for corrections that send names without ids (older
-- app versions); never sent to Gemini. Same people as 'get instructor id7' (approved +
-- DEPARTMENT_HEAD / FULL-TIME / PART-TIME, or a placeholder), one entry per person; every subject.
-- No parameters. Always exactly one row.
WITH
ins AS (
  SELECT ui.id,
         btrim(regexp_replace(btrim(COALESCE(ui.first_name, '')) || ' ' || btrim(COALESCE(ui.last_name, '')),
                              '\s+', ' ', 'g')) AS name
  FROM public.user_info ui
  WHERE (ui.account_status = 'approved'
         AND EXISTS (SELECT 1
                       FROM public.department_table dt
                       JOIN public.roles r ON r.id = dt.roles
                      WHERE dt.user_id = ui.id
                        AND r."Roles" IN ('DEPARTMENT_HEAD', 'FULL-TIME', 'PART-TIME')))
     OR lower(COALESCE(ui.is_placeholder::text, 'false')) IN ('true', 't', '1', 'yes')
)
SELECT
  1 AS candidates_version,
  (SELECT COALESCE(jsonb_agg(jsonb_build_object('id', i.id, 'name', i.name) ORDER BY lower(i.name), i.id),
                   '[]'::jsonb)
     FROM ins i
    WHERE i.name <> '') AS instructors,
  (SELECT COALESCE(jsonb_agg(jsonb_build_object('id', s.id,
                                                'code', btrim(COALESCE(s.subject_code, '')),
                                                'name', btrim(COALESCE(s.subject_name, '')))
                             ORDER BY s.subject_code, s.id),
                   '[]'::jsonb)
     FROM public.subjects s) AS subjects;
