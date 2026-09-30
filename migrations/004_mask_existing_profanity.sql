-- 004_mask_existing_profanity.sql  (OPTIONAL, one-off clean-up; safe to run again)
--
-- Masks SEXUAL words (sex acts, genitals, sexual slang in English, Tagalog and Bisaya/Cebuano)
-- in remarks that were stored before the n8n sexual-word filter existed. Each word becomes
-- asterisks of the same length ("iyot" -> "****"). Ordinary insults and curses (idiot, kupal,
-- gago, yawa, piste, putangina, ...) are NOT touched. The word list and matching rules are the
-- ones of n8n/tools/website_src/profanity.js with its default groups (sexual + fuck on,
-- sexual_curses off); the patterns below were generated from it with
--     node n8n/tools/test_website_code.js --print-profanity-patterns
-- and test_website_sql.js checks that this file and the n8n filter give identical results.
-- If you change the word list or a group, regenerate the pattern lines below the same way.
--
-- What it changes:
--   * sast_all_raw_data_survey."Remarks_and_Suggestions"          masked
--   * student_remarks.remark                                       masked; see the note on duplicates
--   * failed_scan_queue.partial_data->'remarks'                    masked, + partial_data.remarks_masked = true
--     (and validated_data->'remarks' when present)
--   * import_errors.raw_data->'Remarks & Suggestions' / ->'remarks' masked
--   * instructor_wordcloud                                         rows whose word is sexual or masked (*) deleted
-- It creates nothing permanent: the helper functions live in pg_temp (this session only).
--
-- student_remarks has the unique key (instructor_id, subject_id, term_id, remark). Masking can
-- make two remarks of the same instructor/subject/term identical ("xxxx ka" and "yyyy ka" both
-- become "**** ka"), or identical to an existing clean row. Such a masked row is DELETED instead
-- of updated: a clean identical row is kept first, otherwise the oldest one (created_at, then id).
-- Rows that need no masking are never deleted. (NULL subject/term are grouped together, so this
-- works whether or not the index treats NULLs as distinct.)
--
-- HOW TO RUN (Supabase SQL editor, which shows only the LAST result of a run):
--   1. PREVIEW: select everything from the top down to the end of PART 1 and run the selection.
--      It changes nothing and lists every row that would change (before/after) or be deleted.
--   2. APPLY: run the whole file. PART 2 runs in one transaction (all or nothing); the final
--      SELECT shows what is left to mask (all zeros). Running it again changes nothing.
-- Take a backup first if you want to be able to undo it: masking cannot be reversed.

-- ============================================================================ PART 1: PREVIEW
CREATE OR REPLACE FUNCTION pg_temp.pf_token_pattern() RETURNS text LANGUAGE sql IMMUTABLE AS $f$
  SELECT
    '^(?:(?:g+[i1!|*]+|g+[i1!|*]+n+[a4@*]+|g+[i1!|*]+p+[a4@*]+|g+[a4@*]+|n+[a4@*]+|n+[a4@*]+g+|' ||
    'n+[a4@*]+g+[a4@*]+|n+[a4@*]+g+k+[a4@*]+|n+[a4@*]+k+[a4@*]+|n+[a4@*]+k+[a4@*]+k+[a4@*]+|n+[' ||
    'a4@*]+n+g+|n+[a4@*]+n+|n+[i1!|*]+|m+[a4@*]+|m+[a4@*]+g+|m+[a4@*]+g+[a4@*]+|m+[a4@*]+g+k+[a' ||
    '4@*]+|m+[a4@*]+k+[a4@*]+|m+[a4@*]+n+g+|m+[a4@*]+n+|m+[i1!|*]+|p+[a4@*]+|p+[a4@*]+g+|p+[a4@' ||
    '*]+n+g+|p+[a4@*]+k+[a4@*]+|k+[a4@*]+|k+[i1!|*]+n+[a4@*]+|[i1!|*]+|[i1!|*]+p+[a4@*]+|[i1!|*' ||
    ']+n+|[uv*]+m+|n+[a4@*]+k+[i1!|*]+g+|m+[a4@*]+k+[i1!|*]+g+|p+[a4@*]+k+[i1!|*]+g+)?(?:[i1!|*' ||
    ']+y+[o0*]+[t7]+|[i1!|*]+y+[uv*]+[t7]+|(?:[b8]+(?:in|um)?[i1!|*]+)?[b8]+(?:in|um)?[i1!|*]+[' ||
    'l1|]+[a4@*]+[t7]+|[o0*]+[t7]+[e3*]+n+|[o0*]+[t7]+[i1!|*]+n+|[uv*]+[t7]+[e3*]+n+|[uv*]+[t7]' ||
    '+[i1!|*]+n+|(?:[s5$]+(?:in|um)?[a4@*]+)?[s5$]+(?:in|um)?[a4@*]+[l1|]+[s5$]+[a4@*]+[l1|]+|(' ||
    '?:k+(?:in|um)?[a4@*]+)?k+(?:in|um)?[a4@*]+n+[t7]+[o0*]+[t7]+|(?:k+(?:in|um)?[a4@*]+)?k+(?:' ||
    'in|um)?[a4@*]+n+[t7]+[uv*]+[t7]+|(?:h+(?:in|um)?[i1!|*]+)?h+(?:in|um)?[i1!|*]+n+d+[o0*]+[t' ||
    '7]+|(?:h+(?:in|um)?[i1!|*]+)?h+(?:in|um)?[i1!|*]+n+d+[uv*]+[t7]+|(?:j+(?:in|um)?[a4@*]+)?j' ||
    '+(?:in|um)?[a4@*]+k+[o0*]+[l1|]+|(?:j+(?:in|um)?[a4@*]+)?j+(?:in|um)?[a4@*]+k+[uv*]+[l1|]+' ||
    '|(?:d+(?:in|um)?y+[a4@*]+)?d+(?:in|um)?y+[a4@*]+k+[o0*]+[l1|]+|(?:[t7]+(?:in|um)?[s5$]+[uv' ||
    '*]+)?[t7]+(?:in|um)?[s5$]+[uv*]+p+[a4@*]+|(?:c+(?:in|um)?h+[uv*]+)?c+(?:in|um)?h+[uv*]+p+[' ||
    'a4@*]+)(?:[a4@*]+n+|h+[a4@*]+n+|[o0*]+n+|h+[o0*]+n+|[a4@*]+|h+[a4@*]+|[a4@*]+y+|[i1!|*]+n+' ||
    '|h+[i1!|*]+n+|[e3*]+r+[o0*]+|[e3*]+r+[a4@*]+|[e3*]+r+[o0*]+[s5$]+|[e3*]+r+[a4@*]+[s5$]+)?|' ||
    '(?:m+[o0*]+[t7]+h+[e3*]+r+|m+[o0*]+[t7]+h+[a4@*]+|m+[uv*]+[t7]+h+[a4@*]+|m+[uv*]+[t7]+h+[e' ||
    '3*]+r+|m+[a4@*]+d+[a4@*]+)?(?:f+[uv*]+c+k+|f+[uv*]+k+|f+c+k+|p+h+[uv*]+c+k+)(?:[s5$]+|[i1!' ||
    '|*]+n+g+|[i1!|*]+n+|[e3*]+d+|[e3*]+r+|[e3*]+r+[s5$]+|f+[a4@*]+c+[e3*]+|h+[e3*]+[a4@*]+d+)?' ||
    '|(?:p+[uv*]+d+[a4@*]+y+|[t7]+[a4@*]+m+[o0*]+d+|[t7]+[a4@*]+m+[uv*]+d+|[b8]+[a4@*]+y+[a4@*]' ||
    '+g+|[b8]+[uv*]+r+[a4@*]+[t7]+|[t7]+[i1!|*]+[t7]+[i1!|*]+|[t7]+[i1!|*]+[t7]+[e3*]+|[e3*]+[t' ||
    '7]+[i1!|*]+[t7]+[s5$]+|p+[uv*]+k+[e3*]+|p+[uv*]+k+[i1!|*]+|p+[e3*]+k+p+[e3*]+k+|k+[e3*]+p+' ||
    'y+[a4@*]+[s5$]+|k+[i1!|*]+p+[a4@*]+y+|[b8]+[e3*]+[t7]+[l1|]+[o0*]+g+|p+[uv*]+[s5$]+[s5$]+y' ||
    '+|p+[uv*]+[s5$]+[s5$]+[i1!|*]+[e3*]+[s5$]+|c+[uv*]+n+[t7]+|c+[uv*]+n+[t7]+[s5$]+|[b8]+[o0*' ||
    ']+[o0*]+[b8]+[s5$]+|[b8]+[o0*]+[o0*]+[b8]+[i1!|*]+[e3*]+[s5$]+|[t7]+[i1!|*]+[t7]+[s5$]+|[t' ||
    '7]+[i1!|*]+[t7]+[t7]+[i1!|*]+[e3*]+[s5$]+|p+[o0*]+r+n+|p+[o0*]+r+n+[o0*]+|[b8]+[l1|]+[o0*]' ||
    '+w+j+[o0*]+[b8]+|[b8]+[l1|]+[o0*]+w+j+[o0*]+[b8]+[s5$]+|h+[a4@*]+n+d+j+[o0*]+[b8]+|h+[a4@*' ||
    ']+n+d+j+[o0*]+[b8]+[s5$]+|h+[o0*]+r+n+y+|d+[i1!|*]+[l1|]+d+[o0*]+|d+[i1!|*]+[l1|]+d+[o0*]+' ||
    '[s5$]+|j+[i1!|*]+z+z+|f+[a4@*]+k+|p+[a4@*]+k+y+[uv*]+|p+[a4@*]+k+[s5$]+h+[e3*]+[t7]+|p+[a4' ||
    '@*]+k+[s5$]+h+[i1!|*]+[t7]+))$'
$f$;

CREATE OR REPLACE FUNCTION pg_temp.pf_norm(s text) RETURNS text LANGUAGE sql IMMUTABLE AS $f$
  SELECT regexp_replace(lower(normalize(s, NFD)), '[̀-ͯ._''`‘’-]', '', 'g')
$f$;

CREATE OR REPLACE FUNCTION pg_temp.pf_is_word(core text) RETURNS boolean LANGUAGE sql IMMUTABLE AS $f$
  SELECT char_length(core) <= 64 AND pg_temp.pf_norm(core) ~ pg_temp.pf_token_pattern()
$f$;

CREATE OR REPLACE FUNCTION pg_temp.pf_is_phrase(a text, b text) RETURNS boolean LANGUAGE sql IMMUTABLE AS $f$
  SELECT EXISTS (
    SELECT 1 FROM (VALUES
      ('^p+[a4@*]+k+$', '^y+[uv*]+$'),
      ('^p+[a4@*]+k+$', '^y+[o0*]+[uv*]+$')
    ) AS v(x, y)
    WHERE pg_temp.pf_norm(a) ~ v.x AND pg_temp.pf_norm(b) ~ v.y)
$f$;

-- Same algorithm as findProfanity()/maskProfanity() in profanity.js: split into chunks at
-- whitespace and punctuation, strip non-word characters at the chunk edges, then
--   1. whole chunk (separators . - _ ' removed), else each separated part;
--   2. runs of 3+ single letters separated by spaces ("i y o t"), longest match first;
--   3. two-word forms ("pak yu").
-- Every match is replaced by '*' per non-space character, so lengths never change.
CREATE OR REPLACE FUNCTION pg_temp.mask_sexual_words(t text) RETURNS text LANGUAGE plpgsql IMMUTABLE AS $f$
DECLARE
  ws constant text := ' \t\n\r\f\v   -     　﻿';
  wc constant text := 'A-Za-z0-9À-ɏḀ-ỿ@$';
  dc text;
  starts int[] := '{}';
  cores text[] := '{}';
  afters text[] := '{}';
  masked boolean[] := '{}';
  n int := 0;
  pos int := 1;
  res text := t;
  r record;
  p record;
  tok text;
  lead int;
  core text;
  offs int;
  st int;
  k int;
  e int;
  s int;
  len int;
  j int;
  span_len int;
BEGIN
  IF t IS NULL OR t = '' THEN
    RETURN t;
  END IF;
  dc := ws || ',;:/\\()\[\]{}"“”«»<>?';
  FOR r IN SELECT m[1] AS tk, m[2] AS sep
             FROM regexp_matches(t, '([^' || dc || ']+)|([' || dc || ']+)', 'g') WITH ORDINALITY AS x(m, ord)
            ORDER BY ord LOOP
    IF r.tk IS NOT NULL THEN
      tok := r.tk;
      lead := char_length(substring(tok FROM '^[^' || wc || ']*'));
      core := substr(tok, lead + 1);
      core := left(core, char_length(core) - char_length(substring(core FROM '[^' || wc || ']*$')));
      n := n + 1;
      starts[n] := pos + lead;
      cores[n] := core;
      afters[n] := '';
      masked[n] := false;
      pos := pos + char_length(tok);
    ELSE
      IF n > 0 THEN
        afters[n] := r.sep;
      END IF;
      pos := pos + char_length(r.sep);
    END IF;
  END LOOP;

  -- 1. whole words, else each separated part
  FOR i IN 1..n LOOP
    CONTINUE WHEN cores[i] = '';
    IF pg_temp.pf_is_word(cores[i]) THEN
      res := overlay(res PLACING regexp_replace(cores[i], '[^' || ws || ']', '*', 'g') FROM starts[i] FOR char_length(cores[i]));
      masked[i] := true;
    ELSIF cores[i] ~ '[._''`‘’-]' THEN
      offs := 1;
      FOR p IN SELECT m[1] AS part FROM regexp_matches(cores[i], '([^._''`‘’-]+)', 'g') WITH ORDINALITY AS x(m, ord)
                ORDER BY ord LOOP
        st := offs + strpos(substr(cores[i], offs), p.part) - 1;
        offs := st + char_length(p.part);
        IF pg_temp.pf_is_word(p.part) THEN
          res := overlay(res PLACING regexp_replace(p.part, '[^' || ws || ']', '*', 'g') FROM starts[i] + st - 1 FOR char_length(p.part));
          masked[i] := true;
        END IF;
      END LOOP;
    END IF;
  END LOOP;

  -- 2. spaced-out single letters
  k := 1;
  WHILE k <= n LOOP
    IF masked[k] OR char_length(cores[k]) <> 1 THEN
      k := k + 1;
      CONTINUE;
    END IF;
    e := k;
    WHILE e + 1 <= n AND NOT masked[e + 1] AND char_length(cores[e + 1]) = 1 AND afters[e] ~ ('^[' || ws || ']+$') LOOP
      e := e + 1;
    END LOOP;
    s := k;
    WHILE s <= e LOOP
      len := least(e - s + 1, 20);
      WHILE len >= 3 AND NOT pg_temp.pf_is_word(array_to_string(cores[s:s + len - 1], '')) LOOP
        len := len - 1;
      END LOOP;
      IF len >= 3 THEN
        span_len := starts[s + len - 1] + char_length(cores[s + len - 1]) - starts[s];
        res := overlay(res PLACING regexp_replace(substr(t, starts[s], span_len), '[^' || ws || ']', '*', 'g') FROM starts[s] FOR span_len);
        FOR j IN s..s + len - 1 LOOP
          masked[j] := true;
        END LOOP;
        s := s + len;
      ELSE
        s := s + 1;
      END IF;
    END LOOP;
    k := e + 1;
  END LOOP;

  -- 3. two-word forms
  k := 1;
  WHILE k + 1 <= n LOOP
    IF NOT masked[k] AND NOT masked[k + 1] AND cores[k] <> '' AND cores[k + 1] <> ''
       AND afters[k] ~ ('^[' || ws || ']+$') AND pg_temp.pf_is_phrase(cores[k], cores[k + 1]) THEN
      span_len := starts[k + 1] + char_length(cores[k + 1]) - starts[k];
      res := overlay(res PLACING regexp_replace(substr(t, starts[k], span_len), '[^' || ws || ']', '*', 'g') FROM starts[k] FOR span_len);
      masked[k] := true;
      masked[k + 1] := true;
      k := k + 2;
    ELSE
      k := k + 1;
    END IF;
  END LOOP;
  RETURN res;
END
$f$;

-- The given string keys of a JSON object, masked.
CREATE OR REPLACE FUNCTION pg_temp.pf_mask_json_keys(j jsonb, keys text[]) RETURNS jsonb LANGUAGE plpgsql IMMUTABLE AS $f$
DECLARE
  k text;
BEGIN
  IF j IS NULL OR jsonb_typeof(j) <> 'object' THEN
    RETURN j;
  END IF;
  FOREACH k IN ARRAY keys LOOP
    IF jsonb_typeof(j -> k) = 'string' THEN
      j := jsonb_set(j, ARRAY[k], to_jsonb(pg_temp.mask_sexual_words(j ->> k)));
    END IF;
  END LOOP;
  RETURN j;
END
$f$;

-- Preview: every row that PART 2 would change (before -> after) or delete. Changes nothing.
SELECT target, row_id, action, before, after FROM (
  SELECT 'sast_all_raw_data_survey' AS target, s.id::text AS row_id, 'mask' AS action,
         s."Remarks_and_Suggestions" AS before, pg_temp.mask_sexual_words(s."Remarks_and_Suggestions") AS after
    FROM public.sast_all_raw_data_survey s
   WHERE pg_temp.mask_sexual_words(s."Remarks_and_Suggestions") IS DISTINCT FROM s."Remarks_and_Suggestions"
  UNION ALL
  SELECT 'student_remarks', r.id::text,
         CASE WHEN r.rn > 1 THEN 'delete (duplicate after masking)' ELSE 'mask' END, r.remark, r.masked
    FROM (SELECT sr.id, sr.remark, pg_temp.mask_sexual_words(sr.remark) AS masked,
                 row_number() OVER (PARTITION BY sr.instructor_id, sr.subject_id, sr.term_id, pg_temp.mask_sexual_words(sr.remark)
                                    ORDER BY (pg_temp.mask_sexual_words(sr.remark) = sr.remark) DESC, sr.created_at NULLS LAST, sr.id) AS rn
            FROM public.student_remarks sr) r
   WHERE r.masked IS DISTINCT FROM r.remark
  UNION ALL
  SELECT 'failed_scan_queue', q.id::text, 'mask', q.partial_data ->> 'remarks',
         pg_temp.pf_mask_json_keys(q.partial_data::jsonb, ARRAY['remarks']) ->> 'remarks'
    FROM public.failed_scan_queue q
   WHERE pg_temp.pf_mask_json_keys(q.partial_data::jsonb, ARRAY['remarks']) IS DISTINCT FROM q.partial_data::jsonb
  UNION ALL
  SELECT 'failed_scan_queue.validated_data', q.id::text, 'mask', q.validated_data ->> 'remarks',
         pg_temp.pf_mask_json_keys(q.validated_data::jsonb, ARRAY['remarks']) ->> 'remarks'
    FROM public.failed_scan_queue q
   WHERE pg_temp.pf_mask_json_keys(q.validated_data::jsonb, ARRAY['remarks']) IS DISTINCT FROM q.validated_data::jsonb
  UNION ALL
  SELECT 'import_errors', e.id::text, 'mask',
         concat_ws(' | ', e.raw_data ->> 'Remarks & Suggestions', e.raw_data ->> 'remarks'),
         concat_ws(' | ', pg_temp.pf_mask_json_keys(e.raw_data::jsonb, ARRAY['Remarks & Suggestions', 'remarks']) ->> 'Remarks & Suggestions',
                          pg_temp.pf_mask_json_keys(e.raw_data::jsonb, ARRAY['Remarks & Suggestions', 'remarks']) ->> 'remarks')
    FROM public.import_errors e
   WHERE pg_temp.pf_mask_json_keys(e.raw_data::jsonb, ARRAY['Remarks & Suggestions', 'remarks']) IS DISTINCT FROM e.raw_data::jsonb
  UNION ALL
  SELECT 'instructor_wordcloud', w.id::text, 'delete', w.word, NULL
    FROM public.instructor_wordcloud w
   WHERE strpos(w.word, '*') > 0 OR pg_temp.mask_sexual_words(w.word) IS DISTINCT FROM w.word
) preview
ORDER BY target, row_id;
-- ============================================================================ END OF PART 1

-- ============================================================================ PART 2: APPLY
BEGIN;

UPDATE public.sast_all_raw_data_survey s
   SET "Remarks_and_Suggestions" = pg_temp.mask_sexual_words(s."Remarks_and_Suggestions")
 WHERE pg_temp.mask_sexual_words(s."Remarks_and_Suggestions") IS DISTINCT FROM s."Remarks_and_Suggestions";

-- student_remarks: first delete the masked rows that would collide (unique key), then mask the rest.
DELETE FROM public.student_remarks d
 USING (SELECT sr.id,
               pg_temp.mask_sexual_words(sr.remark) IS DISTINCT FROM sr.remark AS changes,
               row_number() OVER (PARTITION BY sr.instructor_id, sr.subject_id, sr.term_id, pg_temp.mask_sexual_words(sr.remark)
                                  ORDER BY (pg_temp.mask_sexual_words(sr.remark) = sr.remark) DESC, sr.created_at NULLS LAST, sr.id) AS rn
          FROM public.student_remarks sr) x
 WHERE d.id = x.id AND x.changes AND x.rn > 1;

UPDATE public.student_remarks sr
   SET remark = pg_temp.mask_sexual_words(sr.remark)
 WHERE pg_temp.mask_sexual_words(sr.remark) IS DISTINCT FROM sr.remark;

UPDATE public.failed_scan_queue q
   SET partial_data = pg_temp.pf_mask_json_keys(q.partial_data::jsonb, ARRAY['remarks']) || '{"remarks_masked": true}'::jsonb
 WHERE pg_temp.pf_mask_json_keys(q.partial_data::jsonb, ARRAY['remarks']) IS DISTINCT FROM q.partial_data::jsonb;

UPDATE public.failed_scan_queue q
   SET validated_data = pg_temp.pf_mask_json_keys(q.validated_data::jsonb, ARRAY['remarks'])
 WHERE pg_temp.pf_mask_json_keys(q.validated_data::jsonb, ARRAY['remarks']) IS DISTINCT FROM q.validated_data::jsonb;

UPDATE public.import_errors e
   SET raw_data = pg_temp.pf_mask_json_keys(e.raw_data::jsonb, ARRAY['Remarks & Suggestions', 'remarks'])
 WHERE pg_temp.pf_mask_json_keys(e.raw_data::jsonb, ARRAY['Remarks & Suggestions', 'remarks']) IS DISTINCT FROM e.raw_data::jsonb;

DELETE FROM public.instructor_wordcloud w
 WHERE strpos(w.word, '*') > 0 OR pg_temp.mask_sexual_words(w.word) IS DISTINCT FROM w.word;

COMMIT;

-- Left to mask after PART 2 (all zeros).
SELECT
  (SELECT count(*) FROM public.sast_all_raw_data_survey s
    WHERE pg_temp.mask_sexual_words(s."Remarks_and_Suggestions") IS DISTINCT FROM s."Remarks_and_Suggestions") AS raw_rows,
  (SELECT count(*) FROM public.student_remarks sr
    WHERE pg_temp.mask_sexual_words(sr.remark) IS DISTINCT FROM sr.remark) AS student_remarks,
  (SELECT count(*) FROM public.failed_scan_queue q
    WHERE pg_temp.pf_mask_json_keys(q.partial_data::jsonb, ARRAY['remarks']) IS DISTINCT FROM q.partial_data::jsonb
       OR pg_temp.pf_mask_json_keys(q.validated_data::jsonb, ARRAY['remarks']) IS DISTINCT FROM q.validated_data::jsonb) AS failed_scan_queue,
  (SELECT count(*) FROM public.import_errors e
    WHERE pg_temp.pf_mask_json_keys(e.raw_data::jsonb, ARRAY['Remarks & Suggestions', 'remarks']) IS DISTINCT FROM e.raw_data::jsonb) AS import_errors,
  (SELECT count(*) FROM public.instructor_wordcloud w
    WHERE strpos(w.word, '*') > 0 OR pg_temp.mask_sexual_words(w.word) IS DISTINCT FROM w.word) AS wordcloud_rows;
