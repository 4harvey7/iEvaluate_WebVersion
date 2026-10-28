-- Stamp rule (2026-10-16): is this numbering-machine stamp already stored for the term?
-- Parameters: term ('' = the current term from system_settings, the term phone scans are credited
-- to), stamp ('' = no stamp, never a duplicate), task_id of the page or correction (its own row
-- is never its duplicate). Used by 'Phone Stamp Check' (phone scans, before the answer) and
-- 'Correction Stamp Check' (sast-manual-correction, before the queue row is validated).
-- Always exactly one row: duplicate_stamp_exists, duplicate_stamp_of (DUP or NULL).
WITH
t AS (
  SELECT COALESCE(
           NULLIF(lower(btrim(COALESCE($1, ''))), ''),
           (SELECT ss.current_term_id::text
              FROM public.system_settings ss
             ORDER BY (ss.id::text = '1') DESC
             LIMIT 1)
         ) AS term_id
),
stamp AS (
  SELECT public.sast_stamp_duplicate_of(t.term_id, btrim(COALESCE($2, '')), $3) AS dup
  FROM t
)
SELECT
  (stamp.dup IS NOT NULL) AS duplicate_stamp_exists,
  stamp.dup               AS duplicate_stamp_of,
  (SELECT t.term_id FROM t) AS stamp_term_id
FROM stamp;
