-- ---------------------------------------------------------------------------
-- 014: a SAST form with fewer than 5 of its 20 items answered is not counted.
--
-- Rule from the teacher (2026-10-08): "Less than 5 items responded or checked -> discard".
-- An item is answered when its rating is not NULL (1..5; a blank or a rejected non-check mark
-- is NULL, see 013 and the n8n check-only rule).
--
-- n8n already stores such a scanned form in failed_scan_queue with status 'discarded' (never in
-- sast_all_raw_data_survey), and the website refuses to approve one. This migration is the
-- safety net for every other way a raw row can arrive (Flutter manual corrections, Google Sheet
-- imports, import-error corrections, rows stored before the rule):
--
--   public.sast_answers(instructor, term) -- the only reader of the raw rows in
--   public.recompute_sast -- now skips a raw row with fewer than 5 answered items. Such a row stays
--   in sast_all_raw_data_survey (nothing is deleted) but no longer counts anywhere:
--   management_results / performance_results (means, *_count, total_responses),
--   overall_total_survey and overall_question_means.
--
-- Checked 2026-10-08 (read-only): 2449 raw rows, 1 with fewer than 5 answered (2 of 20; a
-- website-review approval), so the backfill below recomputes 1 instructor + term pair.
--
-- Review before applying. Not auto-applied. Run in the Supabase SQL Editor (or psql) after 013.
-- One transaction: all or nothing. Safe to re-run.
-- ---------------------------------------------------------------------------
begin;


-- 1. Every COUNTED answer of an instructor + term as one row ----------------------------
-- Same output as 013 (form id, subject, category, question 1..10, rating or NULL; 20 rows per
-- form), but only forms with at least 5 of the 20 items answered.
create or replace function public.sast_answers(p_instructor_id uuid, p_term_id uuid)
returns table (response_id bigint, subject_id uuid, category text, question_number integer, rating numeric)
language sql
stable
set search_path = public
as $$
  select r.id, r.subject_id::uuid, a.category, a.question_number, a.rating::numeric
  from public.sast_all_raw_data_survey r
  cross join lateral (values
    ('Management',  1, r.m1), ('Management',  2, r.m2), ('Management',  3, r.m3),
    ('Management',  4, r.m4), ('Management',  5, r.m5), ('Management',  6, r.m6),
    ('Management',  7, r.m7), ('Management',  8, r.m8), ('Management',  9, r.m9),
    ('Management', 10, r.m10),
    ('Performance',  1, r.p1), ('Performance',  2, r.p2), ('Performance',  3, r.p3),
    ('Performance',  4, r.p4), ('Performance',  5, r.p5), ('Performance',  6, r.p6),
    ('Performance',  7, r.p7), ('Performance',  8, r.p8), ('Performance',  9, r.p9),
    ('Performance', 10, r.p10)
  ) as a(category, question_number, rating)
  where r."instructor_ID"::uuid = p_instructor_id
    and r.term_id::uuid = p_term_id
    -- min-answered rule: fewer than 5 of the 20 items answered -> the form is not counted
    and num_nonnulls(r.m1, r.m2, r.m3, r.m4, r.m5, r.m6, r.m7, r.m8, r.m9, r.m10,
                     r.p1, r.p2, r.p3, r.p4, r.p5, r.p6, r.p7, r.p8, r.p9, r.p10) >= 5;
$$;

-- unchanged from 013 (create or replace keeps them; restated so a re-run is self-contained)
revoke all on function public.sast_answers(uuid, uuid) from public, anon, authenticated;
grant execute on function public.sast_answers(uuid, uuid) to service_role;


-- 2. Backfill: recompute every instructor + term that has a form with < 5 answers ---------
-- (only those pairs change; recompute_sast also deletes a subject / total row that has no
-- counted form left)
do $$
declare
  r record;
  v_pairs integer := 0;
begin
  for r in
    select distinct "instructor_ID"::uuid as instructor_id, term_id::uuid as term_id
    from public.sast_all_raw_data_survey
    where "instructor_ID" is not null and term_id is not null
      and num_nonnulls(m1, m2, m3, m4, m5, m6, m7, m8, m9, m10,
                       p1, p2, p3, p4, p5, p6, p7, p8, p9, p10) < 5
    order by 1, 2
  loop
    perform public.recompute_sast(r.instructor_id, r.term_id);
    v_pairs := v_pairs + 1;
  end loop;
  raise notice '014: recomputed % instructor/term pairs', v_pairs;
end $$;

commit;


-- Checks afterwards (read-only):
-- raw rows that are no longer counted (kept, not deleted):
-- select id, task_id, "instructor_ID", term_id,
--        num_nonnulls(m1,m2,m3,m4,m5,m6,m7,m8,m9,m10,p1,p2,p3,p4,p5,p6,p7,p8,p9,p10) as answered
--   from public.sast_all_raw_data_survey
--  where num_nonnulls(m1,m2,m3,m4,m5,m6,m7,m8,m9,m10,p1,p2,p3,p4,p5,p6,p7,p8,p9,p10) < 5;
-- the pair's totals now leave that form out:
-- select * from public.recompute_sast('<instructor uuid>', '<term uuid>');
