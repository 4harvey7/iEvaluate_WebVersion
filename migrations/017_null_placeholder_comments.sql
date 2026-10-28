-- ---------------------------------------------------------------------------
-- 017 (OPTIONAL, data only): comments that are only a placeholder become NULL.
--
-- n8n (2026-10-18) now stores NULL instead of "N/A", "none", ... for new rows
-- (website_src/remark_clean.js). This script cleans the rows stored before that.
-- No schema change. Not auto-applied. Run in the Supabase SQL Editor.
--
-- Only a WHOLE value counts, after trimming, collapsing whitespace and lowercasing:
--   n/a, na, n.a., n.a, none, none., nothing, no comment, no comments, no remarks,
--   wala, wala man, -, --, ., .., x, a blank value, or only punctuation/symbols.
-- A real comment that merely contains one of these ("none of it was clear") is never touched.
--
-- What it changes:
--   1. sast_all_raw_data_survey."Remarks_and_Suggestions"  -> NULL
--   2. student_remarks (one row per comment, shown in the apps) -> the placeholder rows are DELETED
--      (NULL would show an empty bubble, and the column may be NOT NULL).
-- Not touched: instructor_wordcloud (counts per word; a word such as "none" stays until the next
-- recompute) and the AI summaries.
--
-- The script ends with ROLLBACK so a first run only previews. Read the counts, then change the last
-- line to  commit;  and run it again.
-- ---------------------------------------------------------------------------
begin;

create or replace function pg_temp.is_placeholder_comment(t text)
returns boolean language sql immutable as $$
  select t is not null and (
    lower(btrim(regexp_replace(replace(replace(t, chr(8216), ''''), chr(8217), ''''), '\s+', ' ', 'g')))
      in ('', 'n/a', 'na', 'n.a.', 'n.a', 'none', 'none.', 'nothing', 'no comment', 'no comments',
          'no remarks', 'wala', 'wala man', '-', '--', '.', '..', 'x')
    or t ~ '^[[:punct:][:space:]]+$'
  );
$$;

-- Preview: how many rows would change
select 'sast_all_raw_data_survey' as source, count(*) as rows_to_null
  from public.sast_all_raw_data_survey
 where "Remarks_and_Suggestions" is not null
   and pg_temp.is_placeholder_comment("Remarks_and_Suggestions")
union all
select 'student_remarks (deleted)', count(*)
  from public.student_remarks
 where pg_temp.is_placeholder_comment(remark);

update public.sast_all_raw_data_survey
   set "Remarks_and_Suggestions" = null
 where "Remarks_and_Suggestions" is not null
   and pg_temp.is_placeholder_comment("Remarks_and_Suggestions");

delete from public.student_remarks
 where pg_temp.is_placeholder_comment(remark);

-- Change to  commit;  to keep the changes.
rollback;
