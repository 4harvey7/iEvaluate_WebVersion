-- Phone only: the creditable instructors for the LOCAL name match in the phone reconciler
-- (name_match.js, nmBestInstructor). Runs after the Gemini call and is never sent to Gemini.
-- Same people as 'get instructor id6' (approved + DEPARTMENT_HEAD / FULL-TIME / PART-TIME), one
-- entry per person (two people with the same name stay two entries, so the match is ambiguous).
WITH
ins AS (
  SELECT btrim(regexp_replace(btrim(COALESCE(ui.first_name, '')) || ' ' || btrim(COALESCE(ui.last_name, '')),
                              '\s+', ' ', 'g')) AS name
  FROM public.user_info ui
  WHERE ui.account_status = 'approved'
    AND EXISTS (SELECT 1
                  FROM public.department_table dt
                  JOIN public.roles r ON r.id = dt.roles
                 WHERE dt.user_id = ui.id
                   AND r."Roles" IN ('DEPARTMENT_HEAD', 'FULL-TIME', 'PART-TIME'))
)
SELECT
  1                                                                                    AS candidates_version,
  (SELECT COALESCE(jsonb_agg(i.name ORDER BY lower(i.name), i.name), '[]'::jsonb)
     FROM ins i
    WHERE i.name <> '')                                                                AS instructor_names;
