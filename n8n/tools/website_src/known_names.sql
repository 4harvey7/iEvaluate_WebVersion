-- Instructor names and subjects for the OCR prompt, sorted by name so no entry stands out.
WITH
prm AS (
  SELECT lower(btrim(COALESCE($1, ''))) AS batch_iid
),
ins AS (
  SELECT DISTINCT btrim(regexp_replace(btrim(COALESCE(ui.first_name, '')) || ' ' || btrim(COALESCE(ui.last_name, '')),
                                       '\s+', ' ', 'g')) AS name
  FROM public.user_info ui
  CROSS JOIN prm p
  WHERE ui.id::text = p.batch_iid
     OR lower(COALESCE(ui.is_placeholder::text, 'false')) IN ('true', 't', '1', 'yes')
     OR ( ui.account_status = 'approved'
          AND EXISTS (SELECT 1
                        FROM public.department_table dt
                        JOIN public.roles r ON r.id = dt.roles
                       WHERE dt.user_id = ui.id
                         AND r."Roles" IN ('DEPARTMENT_HEAD', 'FULL-TIME', 'PART-TIME')) )
),
ins_l AS (
  SELECT i.name FROM ins i WHERE i.name <> '' ORDER BY lower(i.name), i.name LIMIT 300
),
sub_l AS (
  SELECT DISTINCT btrim(s.subject_code) AS code,
         btrim(regexp_replace(COALESCE(s.subject_name, ''), '\s+', ' ', 'g')) AS name
  FROM public.subjects s
  WHERE btrim(COALESCE(s.subject_code, '')) <> ''
  ORDER BY 1, 2
  LIMIT 300
)
SELECT
  1                                                                                    AS known_names_version,
  (SELECT COALESCE(jsonb_agg(l.name ORDER BY lower(l.name), l.name), '[]'::jsonb) FROM ins_l l) AS instructor_names,
  (SELECT COALESCE(jsonb_agg(jsonb_build_object('code', l.code, 'name', l.name) ORDER BY l.code, l.name), '[]'::jsonb)
     FROM sub_l l)                                                                     AS subjects,
  (SELECT COALESCE(string_agg(l.name, '; ' ORDER BY lower(l.name), l.name), '') FROM ins_l l) AS instructor_list,
  (SELECT COALESCE(string_agg(CASE WHEN l.name <> '' THEN l.code || ' = ' || l.name ELSE l.code END, '; '
                              ORDER BY l.code, l.name), '')
     FROM sub_l l)                                                                     AS subject_list;
