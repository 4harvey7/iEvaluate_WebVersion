-- Scores the form's instructor/subject; Decide adds token scores and applies the thresholds.
WITH
prm AS (
  SELECT lower(btrim(COALESCE($1, ''))) AS batch_iid,
         lower(btrim(COALESCE($2, ''))) AS batch_sid,
         btrim(COALESCE($3, ''))        AS ocr_i,
         btrim(COALESCE($4, ''))        AS ocr_s,
         COALESCE(
           NULLIF(btrim(COALESCE($5, '')), ''),
           (SELECT ss.current_term_id::text
              FROM public.system_settings ss
             ORDER BY (ss.id::text = '1') DESC
             LIMIT 1)
         ) AS term_id
),
cand AS (
  SELECT ui.id::text                                                                AS id,
         btrim(COALESCE(ui.first_name, ''))                                         AS first_name,
         btrim(COALESCE(ui.last_name, ''))                                          AS last_name,
         lower(COALESCE(ui.is_placeholder::text, 'false')) IN ('true', 't', '1', 'yes') AS is_placeholder,
         (ui.id::text = p.batch_iid)                                                AS is_batch,
         COALESCE(ui.account_status = 'approved', false)                            AS approved
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
txt AS (
  SELECT 'o'::text AS kind, ''::text AS id, p.ocr_i AS t FROM prm p
  UNION ALL
  SELECT 'f', c.id, c.first_name FROM cand c
  UNION ALL
  SELECT 'l', c.id, c.last_name FROM cand c
),
-- no unaccent on Supabase, so accents are folded with translate
nrm AS (
  SELECT x.kind, x.id,
         btrim(regexp_replace(
           regexp_replace(
             regexp_replace(
               regexp_replace(
                 lower(translate(x.t,
                   'ÁÀÂÄÃÅĀáàâäãåāÉÈÊËĒéèêëēÍÌÎÏĪíìîïīÓÒÔÖÕŌóòôöõōÚÙÛÜŪúùûüūÑñÇç',
                   'AAAAAAAaaaaaaaEEEEEeeeeeIIIIIiiiiiOOOOOOooooooUUUUUuuuuuNnCc')),
                 '\mma[''’‘`´]?am\M', ' maam ', 'g'),
               '[^a-z]+', ' ', 'g'),
             '\m(prof|professor|dr|doc|mr|mrs|ms|miss|madam|maam|sir|engr|eng|atty|arch|rev|fr|hon|instr|instructor|teacher|phd|edd|dba|mba|mit|mat|maed|lpt|rn|cpa|jr|sr|ii|iii|iv)\M',
             ' ', 'g'),
           '\s+', ' ', 'g')) AS n
  FROM txt x
),
o AS (
  SELECT n.n                                   AS n,
         string_to_array(n.n, ' ')             AS toks,
         length(replace(n.n, ' ', ''))         AS letters
  FROM nrm n
  WHERE n.kind = 'o'
),
cn AS (
  SELECT c.*, f.n AS nf, l.n AS nl, btrim(f.n || ' ' || l.n) AS nfull
  FROM cand c
  JOIN nrm f ON f.kind = 'f' AND f.id = c.id
  JOIN nrm l ON l.kind = 'l' AND l.id = c.id
),
sims AS (
  SELECT cn.*,
         o.n AS o_n,
         round(similarity(cn.nfull, o.n)::numeric, 4)                                    AS sim,
         round(word_similarity(cn.nl, o.n)::numeric, 4)                                  AS last_ws,
         round(GREATEST(word_similarity(cn.nf, o.n),
                        word_similarity(split_part(cn.nf, ' ', 1), o.n))::numeric, 4)    AS first_ws,
         ( cn.nl <> '' AND o.n <> ''
           AND ( string_to_array(cn.nl, ' ') <@ o.toks
                 OR replace(cn.nl, ' ', '') = ANY (o.toks) ) )                           AS last_whole,
         ( cn.nf <> '' AND left(cn.nf, 1) = ANY (o.toks) )                               AS initial_hit,
         ( cn.nl <> '' AND o.n = cn.nl )                                                 AS surname_only
  FROM cn
  CROSS JOIN o
),
hits AS (
  SELECT s.*,
         ( s.nl <> '' AND s.o_n <> '' AND (s.last_whole OR s.last_ws >= 0.8) )          AS last_hit,
         ( s.nf <> '' AND s.o_n <> '' AND s.first_ws >= 0.7 )                            AS first_hit
  FROM sims s
),
scored AS (
  SELECT h.*,
         CASE WHEN h.o_n = ''                   THEN 0::numeric
              WHEN h.last_hit AND h.first_hit   THEN GREATEST(0.95, h.sim)
              WHEN h.last_hit AND h.initial_hit THEN 0.85
              WHEN h.surname_only               THEN 0.70
              WHEN h.last_hit                   THEN GREATEST(0.60, h.sim)
              ELSE h.sim
         END AS score
  FROM hits h
),
ranked AS (
  SELECT s.*,
         row_number() OVER (ORDER BY s.score DESC, s.is_batch DESC, s.approved DESC,
                                     s.last_name, s.first_name, s.id) AS rn
  FROM scored s
),
os AS (
  SELECT p.ocr_s                                                                    AS raw,
         upper(regexp_replace(p.ocr_s, '[^A-Za-z0-9]', '', 'g'))                    AS alnum,
         upper(regexp_replace(COALESCE(substring(p.ocr_s from '\(([^()]*)\)[^()]*$'), ''),
                              '[^A-Za-z0-9]', '', 'g'))                             AS paren_alnum,
         btrim(regexp_replace(lower(translate(p.ocr_s,
                   'ÁÀÂÄÃÅĀáàâäãåāÉÈÊËĒéèêëēÍÌÎÏĪíìîïīÓÒÔÖÕŌóòôöõōÚÙÛÜŪúùûüūÑñÇç',
                   'AAAAAAAaaaaaaaEEEEEeeeeeIIIIIiiiiiOOOOOOooooooUUUUUuuuuuNnCc')),
                              '[^a-z0-9]+', ' ', 'g'))                              AS n
  FROM prm p
),
keys AS (
  SELECT DISTINCT k.key
  FROM (
    SELECT m[1] || m[2] AS key
      FROM os, regexp_matches(upper(os.raw), '([A-Z]{2,8})[ .-]?([0-9]{1,4}[A-Z]?)', 'g') AS m
    UNION ALL
    SELECT os.alnum FROM os
    UNION ALL
    SELECT os.paren_alnum FROM os
  ) k
  WHERE length(k.key) >= 3
),
subj AS (
  SELECT s.id::text                                                                 AS id,
         COALESCE(s.subject_code, '')                                               AS code,
         COALESCE(s.subject_name, '')                                               AS name,
         (s.id::text = p.batch_sid)                                                 AS is_batch,
         upper(regexp_replace(COALESCE(s.subject_code, ''), '[^A-Za-z0-9]', '', 'g')) AS code_key,
         btrim(regexp_replace(lower(translate(COALESCE(s.subject_name, ''),
                   'ÁÀÂÄÃÅĀáàâäãåāÉÈÊËĒéèêëēÍÌÎÏĪíìîïīÓÒÔÖÕŌóòôöõōÚÙÛÜŪúùûüūÑñÇç',
                   'AAAAAAAaaaaaaaEEEEEeeeeeIIIIIiiiiiOOOOOOooooooUUUUUuuuuuNnCc')),
                              '[^a-z0-9]+', ' ', 'g'))                              AS nname
  FROM public.subjects s
  CROSS JOIN prm p
),
sscore AS (
  SELECT sb.*,
         ( sb.code_key <> '' AND EXISTS (SELECT 1 FROM keys WHERE keys.key = sb.code_key) ) AS code_hit,
         CASE WHEN os.n = '' OR sb.nname = '' THEN 0::numeric
              ELSE round(GREATEST(similarity(sb.nname, os.n),
                                  word_similarity(sb.nname, os.n))::numeric, 4)
         END AS name_score,
         COALESCE((SELECT jsonb_agg(DISTINCT lower(isub.instructor_id::text))
                     FROM public.instructor_subjects isub
                    WHERE isub.subject_id::text = sb.id
                      AND isub.instructor_id IS NOT NULL
                      AND isub.term_id::text = p.term_id), '[]'::jsonb)             AS linked_instructor_ids
  FROM subj sb
  CROSS JOIN os
  CROSS JOIN prm p
),
sranked AS (
  SELECT x.*,
         row_number() OVER (ORDER BY x.code_hit DESC, x.name_score DESC, x.is_batch DESC,
                                     x.code, x.id) AS rn
  FROM sscore x
)
SELECT
  1                                                                                AS form_match_version,
  (SELECT o.n FROM o)                                                              AS ocr_instructor_norm,
  (SELECT o.letters FROM o)                                                        AS ocr_instructor_letters,
  EXISTS (SELECT 1 FROM cand c WHERE c.is_batch)                                   AS batch_instructor_found,
  COALESCE((SELECT btrim(c.first_name || ' ' || c.last_name) FROM cand c WHERE c.is_batch LIMIT 1), '')
                                                                                   AS batch_instructor_name,
  COALESCE((SELECT r.score FROM ranked r WHERE r.is_batch LIMIT 1), 0)::float8     AS batch_instructor_score,
  (SELECT COALESCE(jsonb_agg(jsonb_build_object(
            'id', r.id,
            'name', btrim(r.first_name || ' ' || r.last_name),
            'score', r.score,
            'is_placeholder', r.is_placeholder,
            'is_batch', r.is_batch) ORDER BY r.rn), '[]'::jsonb)
     FROM ranked r
    WHERE r.rn <= 300 OR r.is_batch)                                                AS instructor_candidates,
  (SELECT os.n FROM os)                                                            AS ocr_subject_norm,
  (SELECT length(os.alnum) FROM os)                                                AS ocr_subject_alnum_len,
  EXISTS (SELECT 1 FROM subj s WHERE s.is_batch)                                   AS batch_subject_found,
  COALESCE((SELECT s.code FROM subj s WHERE s.is_batch LIMIT 1), '')               AS batch_subject_code,
  COALESCE((SELECT s.name FROM subj s WHERE s.is_batch LIMIT 1), '')               AS batch_subject_name,
  (SELECT COALESCE(jsonb_agg(jsonb_build_object(
            'id', r.id,
            'code', r.code,
            'name', r.name,
            'code_hit', r.code_hit,
            'name_score', r.name_score,
            'score', CASE WHEN r.code_hit THEN 1 ELSE r.name_score END,
            'is_batch', r.is_batch,
            'linked_instructor_ids', r.linked_instructor_ids) ORDER BY r.rn), '[]'::jsonb)
     FROM sranked r
    WHERE r.rn <= 300 OR r.is_batch)                                                AS subject_candidates,
  (SELECT p.term_id FROM prm p)                                                    AS term_id
FROM prm;
