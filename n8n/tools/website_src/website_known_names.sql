-- Website only: the known subjects for the OCR prompt. No instructor names: the website never
-- sends an instructor list to Gemini (privacy). The form's instructor is matched against the
-- database afterwards by 'Website Form Match' / 'Website Decide'. Same subject list and columns
-- as known_names.sql (the phone lookup).
WITH
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
  (SELECT COALESCE(jsonb_agg(jsonb_build_object('code', l.code, 'name', l.name) ORDER BY l.code, l.name), '[]'::jsonb)
     FROM sub_l l)                                                                     AS subjects,
  (SELECT COALESCE(string_agg(CASE WHEN l.name <> '' THEN l.code || ' = ' || l.name ELSE l.code END, '; '
                              ORDER BY l.code, l.name), '')
     FROM sub_l l)                                                                     AS subject_list;
