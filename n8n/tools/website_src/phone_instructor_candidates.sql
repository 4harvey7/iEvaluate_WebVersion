-- Phone only: the creditable instructors for the LOCAL name match in the phone reconciler
-- (name_match.js, nmBestInstructor). Runs after the Gemini call and is never sent to Gemini.
-- Same people as 'get instructor id6' (approved + DEPARTMENT_HEAD / FULL-TIME / PART-TIME), one
-- entry per person (two people with the same name stay two entries, so the match is ambiguous).
-- instructors also lists, per person, the subject codes linked in the current term (instructor_subjects
-- or a stored raw row): the reconciler ranks a person linked to the subject on the form a bit higher.
WITH
term AS (SELECT current_term_id AS tid FROM public.system_settings LIMIT 1),
ins AS (
  SELECT ui.id,
         btrim(regexp_replace(btrim(COALESCE(ui.first_name, '')) || ' ' || btrim(COALESCE(ui.last_name, '')),
                              '\s+', ' ', 'g')) AS name
  FROM public.user_info ui
  WHERE ui.account_status = 'approved'
    AND EXISTS (SELECT 1
                  FROM public.department_table dt
                  JOIN public.roles r ON r.id = dt.roles
                 WHERE dt.user_id = ui.id
                   AND r."Roles" IN ('DEPARTMENT_HEAD', 'FULL-TIME', 'PART-TIME'))
),
links AS (
  SELECT DISTINCT l.iid, btrim(s.subject_code) AS code
  FROM (SELECT isub.instructor_id AS iid, isub.subject_id
          FROM public.instructor_subjects isub, term
         WHERE isub.term_id = term.tid
        UNION
        SELECT r."instructor_ID", r.subject_id
          FROM public.sast_all_raw_data_survey r, term
         WHERE r.term_id = term.tid) l
  JOIN public.subjects s ON s.id = l.subject_id
  WHERE btrim(COALESCE(s.subject_code, '')) <> ''
)
SELECT
  1                                                                                    AS candidates_version,
  (SELECT COALESCE(jsonb_agg(i.name ORDER BY lower(i.name), i.name), '[]'::jsonb)
     FROM ins i
    WHERE i.name <> '')                                                                AS instructor_names,
  (SELECT COALESCE(jsonb_agg(jsonb_build_object(
            'name', i.name,
            'subjects', COALESCE((SELECT jsonb_agg(k.code ORDER BY k.code) FROM links k WHERE k.iid = i.id), '[]'::jsonb))
          ORDER BY lower(i.name), i.name), '[]'::jsonb)
     FROM ins i
    WHERE i.name <> '')                                                                AS instructors;
