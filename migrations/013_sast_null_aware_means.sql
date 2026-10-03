-- ---------------------------------------------------------------------------
-- 013: SAST means leave blank answers out (blank = NULL, never 0).
--
-- Bug: the aggregates divided by the number of forms (m1_mean = SUM(m1)/COUNT(*),
-- overall = total / (forms * 10)), so a blank answer counted as a 0 and pulled the
-- mean down. Rule from the teacher: a blank is left out of BOTH the sum and the count.
--
-- Calculation (public.recompute_sast, one instructor + term, from the raw rows):
--   1. question mean   = ROUND(AVG(answer), 2); AVG skips blanks. No answers -> NULL.
--   2. answered count  = COUNT(answer)                     (new *_count columns)
--   3. category mean   = ROUND(AVG of the non-NULL ROUNDED question means, 2), so a hand
--                        check from the displayed 2-decimal values matches exactly.
--   4. combined mean   = ROUND((management + performance) / 2, 2); one NULL -> the other.
--   5. total_responses = forms with at least one answer in the category
--                        (overall: at least one of the 20).
--   6. *_total / grand_total = SUM of the answers (blanks skipped);
--      average_per_response_* = grand_total / total_responses (unchanged meaning).
--
-- Schema:
--   * sast_all_raw_data_survey: CHECK each m1..m10 / p1..p10 is NULL or 1..5
--     (checked 2026-10-03: 2448 rows, 0 values outside 1..5, 0 zeros, 121 blanks in 30 rows).
--   * management_results m1_count..m10_count, performance_results p1_count..p10_count,
--     overall_question_means.response_count (new, integer).
--   * m*_mean / p*_mean and overall_question_means.mean_score may now be NULL
--     (a question nobody answered has no mean; it was NOT NULL and would have been 0).
--     overall_management_mean / overall_performance_mean / average_per_response_* stay
--     NOT NULL: a management_results / performance_results row only exists when at least
--     one form answered that category (otherwise the row is deleted).
--   * overall_total_survey.overall_mean is a GENERATED column
--     (was round((management_mean + performance_mean) / 2, 2), NULL when one side is NULL);
--     its expression now falls back to the other side, so it always equals
--     combined_score_mean. Needs PostgreSQL 17+ (Supabase runs 17.6).
--
-- n8n calls  SELECT * FROM public.recompute_sast(instructor_id, term_id)
-- (node 'update to overall total2', once per pair after the raw upsert). It rebuilds
-- instructor_subjects links, management_results, performance_results, overall_total_survey
-- and overall_question_means for the pair and returns one row (combined_score_mean feeds the
-- "< 3" alert IF node). Same advisory-lock key as the old recompute query.
--
-- Review before applying. Not auto-applied. Run in the Supabase SQL Editor (or psql) after
-- 012 (013 does not depend on 012). One transaction: all or nothing. Safe to re-run.
-- The backfill at the end recomputes every (instructor, term) that has raw rows.
-- ---------------------------------------------------------------------------
begin;


-- 1. Raw answers: NULL (blank) or 1..5 -------------------------------------------------
-- One named constraint per question, so an error says which answer was bad.
do $$
declare
  q text;
  c text;
begin
  foreach q in array array['m1','m2','m3','m4','m5','m6','m7','m8','m9','m10',
                           'p1','p2','p3','p4','p5','p6','p7','p8','p9','p10']
  loop
    c := 'sast_raw_' || q || '_rating_1_to_5';
    if not exists (select 1 from pg_constraint
                    where conrelid = 'public.sast_all_raw_data_survey'::regclass and conname = c) then
      execute format('alter table public.sast_all_raw_data_survey
                        add constraint %I check (%I is null or %I between 1 and 5) not valid', c, q, q);
    end if;
    execute format('alter table public.sast_all_raw_data_survey validate constraint %I', c);
  end loop;
end $$;


-- 2. Answered counts per question ----------------------------------------------------
alter table public.management_results
  add column if not exists m1_count integer,
  add column if not exists m2_count integer,
  add column if not exists m3_count integer,
  add column if not exists m4_count integer,
  add column if not exists m5_count integer,
  add column if not exists m6_count integer,
  add column if not exists m7_count integer,
  add column if not exists m8_count integer,
  add column if not exists m9_count integer,
  add column if not exists m10_count integer;

alter table public.performance_results
  add column if not exists p1_count integer,
  add column if not exists p2_count integer,
  add column if not exists p3_count integer,
  add column if not exists p4_count integer,
  add column if not exists p5_count integer,
  add column if not exists p6_count integer,
  add column if not exists p7_count integer,
  add column if not exists p8_count integer,
  add column if not exists p9_count integer,
  add column if not exists p10_count integer;

alter table public.overall_question_means
  add column if not exists response_count integer;


-- 3. A question nobody answered has no mean (NULL, not 0) -------------------------------
alter table public.management_results
  alter column m1_mean drop not null,
  alter column m2_mean drop not null,
  alter column m3_mean drop not null,
  alter column m4_mean drop not null,
  alter column m5_mean drop not null,
  alter column m6_mean drop not null,
  alter column m7_mean drop not null,
  alter column m8_mean drop not null,
  alter column m9_mean drop not null,
  alter column m10_mean drop not null;

alter table public.performance_results
  alter column p1_mean drop not null,
  alter column p2_mean drop not null,
  alter column p3_mean drop not null,
  alter column p4_mean drop not null,
  alter column p5_mean drop not null,
  alter column p6_mean drop not null,
  alter column p7_mean drop not null,
  alter column p8_mean drop not null,
  alter column p9_mean drop not null,
  alter column p10_mean drop not null;

alter table public.overall_question_means
  alter column mean_score drop not null,
  alter column mean_score drop default;     -- was 0: an unanswered question is not a 0


-- 4. overall_mean (generated) = combined_score_mean, also when one category is empty ----
alter table public.overall_total_survey
  alter column overall_mean set expression as
    (round(coalesce((management_mean + performance_mean) / 2, management_mean, performance_mean), 2));


-- 5. Every answer of an instructor + term as one row ----------------------------------
-- (form id, subject, category, question 1..10, rating or NULL). 20 rows per form.
-- The ::uuid casts are no-ops on the live uuid columns.
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
    and r.term_id::uuid = p_term_id;
$$;


-- 6. Rebuild all SAST aggregates of one instructor + term from the raw rows ----------
create or replace function public.recompute_sast(p_instructor_id uuid, p_term_id uuid)
returns table (instructor_id uuid, term_id uuid, total_responses integer,
               management_mean numeric, performance_mean numeric, combined_score_mean numeric)
language plpgsql
volatile
set search_path = public
as $fn$
#variable_conflict use_column
declare
  v_responses  integer;   -- forms with at least one of the 20 answered
  v_m_mean     numeric;
  v_p_mean     numeric;
  v_m_total    integer;
  v_p_total    integer;
  v_combined   numeric;
begin
  -- Same key as the old recompute query: recomputes never race; held until commit.
  perform pg_advisory_xact_lock(hashtext('ievaluate:recompute_aggregates'));

  if p_instructor_id is null or p_term_id is null then
    -- nothing to attach results to (a row without a term): write nothing
    return query select p_instructor_id, p_term_id, 0, null::numeric, null::numeric, null::numeric;
    return;
  end if;

  -- 6a. instructor <-> subject link for every subject with raw rows
  insert into public.instructor_subjects (subject_id, instructor_id, term_id)
  select distinct a.subject_id, p_instructor_id, p_term_id
  from public.sast_answers(p_instructor_id, p_term_id) a
  where a.subject_id is not null
  on conflict (subject_id, instructor_id, term_id) do nothing;

  -- 6b. management_results, one row per subject with at least one Management answer;
  --     rows of subjects that no longer have one (e.g. a form moved by a correction) go.
  with q as (               -- one row per subject and question
    select a.subject_id, a.question_number,
           round(avg(a.rating), 2)          as mean,       -- blanks skipped; none -> NULL
           count(a.rating)::int             as answered,
           coalesce(sum(a.rating), 0)::int  as total
    from public.sast_answers(p_instructor_id, p_term_id) a
    where a.category = 'Management' and a.subject_id is not null
    group by a.subject_id, a.question_number
  ),
  n as (                    -- forms with at least one Management answer
    select a.subject_id, count(distinct a.response_id)::int as responses
    from public.sast_answers(p_instructor_id, p_term_id) a
    where a.category = 'Management' and a.subject_id is not null and a.rating is not null
    group by a.subject_id
  ),
  s as (                    -- one row per subject; arrays are indexed by question 1..10
    select q.subject_id, n.responses,
           array_agg(q.mean     order by q.question_number) as mean,
           array_agg(q.answered order by q.question_number) as answered,
           array_agg(q.total    order by q.question_number) as total,
           round(avg(q.mean), 2)                            as category_mean,  -- of the rounded means
           sum(q.total)::int                                as grand_total
    from q
    join n on n.subject_id = q.subject_id
    group by q.subject_id, n.responses
  ),
  saved as (
    insert into public.management_results (
      instructor_id, term_id, subject_id, total_responses,
      m1_total, m2_total, m3_total, m4_total, m5_total, m6_total, m7_total, m8_total, m9_total, m10_total,
      grand_total,
      m1_mean, m2_mean, m3_mean, m4_mean, m5_mean, m6_mean, m7_mean, m8_mean, m9_mean, m10_mean,
      m1_count, m2_count, m3_count, m4_count, m5_count, m6_count, m7_count, m8_count, m9_count, m10_count,
      overall_management_mean, average_per_response_management
    )
    select p_instructor_id, p_term_id, s.subject_id, s.responses,
           s.total[1], s.total[2], s.total[3], s.total[4], s.total[5],
           s.total[6], s.total[7], s.total[8], s.total[9], s.total[10],
           s.grand_total,
           s.mean[1], s.mean[2], s.mean[3], s.mean[4], s.mean[5],
           s.mean[6], s.mean[7], s.mean[8], s.mean[9], s.mean[10],
           s.answered[1], s.answered[2], s.answered[3], s.answered[4], s.answered[5],
           s.answered[6], s.answered[7], s.answered[8], s.answered[9], s.answered[10],
           s.category_mean,
           round(s.grand_total::numeric / s.responses, 2)
    from s
    on conflict (instructor_id, term_id, subject_id) do update set
      total_responses = excluded.total_responses,
      m1_total = excluded.m1_total, m2_total = excluded.m2_total, m3_total = excluded.m3_total,
      m4_total = excluded.m4_total, m5_total = excluded.m5_total, m6_total = excluded.m6_total,
      m7_total = excluded.m7_total, m8_total = excluded.m8_total, m9_total = excluded.m9_total,
      m10_total = excluded.m10_total,
      grand_total = excluded.grand_total,
      m1_mean = excluded.m1_mean, m2_mean = excluded.m2_mean, m3_mean = excluded.m3_mean,
      m4_mean = excluded.m4_mean, m5_mean = excluded.m5_mean, m6_mean = excluded.m6_mean,
      m7_mean = excluded.m7_mean, m8_mean = excluded.m8_mean, m9_mean = excluded.m9_mean,
      m10_mean = excluded.m10_mean,
      m1_count = excluded.m1_count, m2_count = excluded.m2_count, m3_count = excluded.m3_count,
      m4_count = excluded.m4_count, m5_count = excluded.m5_count, m6_count = excluded.m6_count,
      m7_count = excluded.m7_count, m8_count = excluded.m8_count, m9_count = excluded.m9_count,
      m10_count = excluded.m10_count,
      overall_management_mean = excluded.overall_management_mean,
      average_per_response_management = excluded.average_per_response_management
    returning subject_id
  )
  delete from public.management_results m
  where m.instructor_id = p_instructor_id
    and m.term_id = p_term_id
    and not exists (select 1 from saved where saved.subject_id = m.subject_id);

  -- 6c. performance_results: same as 6b for p1..p10
  with q as (
    select a.subject_id, a.question_number,
           round(avg(a.rating), 2)          as mean,
           count(a.rating)::int             as answered,
           coalesce(sum(a.rating), 0)::int  as total
    from public.sast_answers(p_instructor_id, p_term_id) a
    where a.category = 'Performance' and a.subject_id is not null
    group by a.subject_id, a.question_number
  ),
  n as (
    select a.subject_id, count(distinct a.response_id)::int as responses
    from public.sast_answers(p_instructor_id, p_term_id) a
    where a.category = 'Performance' and a.subject_id is not null and a.rating is not null
    group by a.subject_id
  ),
  s as (
    select q.subject_id, n.responses,
           array_agg(q.mean     order by q.question_number) as mean,
           array_agg(q.answered order by q.question_number) as answered,
           array_agg(q.total    order by q.question_number) as total,
           round(avg(q.mean), 2)                            as category_mean,
           sum(q.total)::int                                as grand_total
    from q
    join n on n.subject_id = q.subject_id
    group by q.subject_id, n.responses
  ),
  saved as (
    insert into public.performance_results (
      instructor_id, term_id, subject_id, total_responses,
      p1_total, p2_total, p3_total, p4_total, p5_total, p6_total, p7_total, p8_total, p9_total, p10_total,
      grand_total,
      p1_mean, p2_mean, p3_mean, p4_mean, p5_mean, p6_mean, p7_mean, p8_mean, p9_mean, p10_mean,
      p1_count, p2_count, p3_count, p4_count, p5_count, p6_count, p7_count, p8_count, p9_count, p10_count,
      overall_performance_mean, average_per_response_performance
    )
    select p_instructor_id, p_term_id, s.subject_id, s.responses,
           s.total[1], s.total[2], s.total[3], s.total[4], s.total[5],
           s.total[6], s.total[7], s.total[8], s.total[9], s.total[10],
           s.grand_total,
           s.mean[1], s.mean[2], s.mean[3], s.mean[4], s.mean[5],
           s.mean[6], s.mean[7], s.mean[8], s.mean[9], s.mean[10],
           s.answered[1], s.answered[2], s.answered[3], s.answered[4], s.answered[5],
           s.answered[6], s.answered[7], s.answered[8], s.answered[9], s.answered[10],
           s.category_mean,
           round(s.grand_total::numeric / s.responses, 2)
    from s
    on conflict (instructor_id, term_id, subject_id) do update set
      total_responses = excluded.total_responses,
      p1_total = excluded.p1_total, p2_total = excluded.p2_total, p3_total = excluded.p3_total,
      p4_total = excluded.p4_total, p5_total = excluded.p5_total, p6_total = excluded.p6_total,
      p7_total = excluded.p7_total, p8_total = excluded.p8_total, p9_total = excluded.p9_total,
      p10_total = excluded.p10_total,
      grand_total = excluded.grand_total,
      p1_mean = excluded.p1_mean, p2_mean = excluded.p2_mean, p3_mean = excluded.p3_mean,
      p4_mean = excluded.p4_mean, p5_mean = excluded.p5_mean, p6_mean = excluded.p6_mean,
      p7_mean = excluded.p7_mean, p8_mean = excluded.p8_mean, p9_mean = excluded.p9_mean,
      p10_mean = excluded.p10_mean,
      p1_count = excluded.p1_count, p2_count = excluded.p2_count, p3_count = excluded.p3_count,
      p4_count = excluded.p4_count, p5_count = excluded.p5_count, p6_count = excluded.p6_count,
      p7_count = excluded.p7_count, p8_count = excluded.p8_count, p9_count = excluded.p9_count,
      p10_count = excluded.p10_count,
      overall_performance_mean = excluded.overall_performance_mean,
      average_per_response_performance = excluded.average_per_response_performance
    returning subject_id
  )
  delete from public.performance_results m
  where m.instructor_id = p_instructor_id
    and m.term_id = p_term_id
    and not exists (select 1 from saved where saved.subject_id = m.subject_id);

  -- 6d. Instructor + term totals, straight from ALL raw rows (question means pooled
  --     across subjects, then the same category / combined rules).
  with q as (
    select a.category, a.question_number,
           round(avg(a.rating), 2)          as mean,
           coalesce(sum(a.rating), 0)::int  as total
    from public.sast_answers(p_instructor_id, p_term_id) a
    group by a.category, a.question_number
  )
  select (select count(distinct a.response_id)::int
            from public.sast_answers(p_instructor_id, p_term_id) a
           where a.rating is not null),
         round(avg(q.mean) filter (where q.category = 'Management'), 2),
         round(avg(q.mean) filter (where q.category = 'Performance'), 2),
         coalesce(sum(q.total) filter (where q.category = 'Management'), 0)::int,
         coalesce(sum(q.total) filter (where q.category = 'Performance'), 0)::int
    into v_responses, v_m_mean, v_p_mean, v_m_total, v_p_total
    from q;

  v_combined := round(coalesce((v_m_mean + v_p_mean) / 2, v_m_mean, v_p_mean), 2);

  if coalesce(v_responses, 0) = 0 then
    -- no answered form left for this pair: no totals, no question means
    delete from public.overall_total_survey o
     where o.instructor_id = p_instructor_id and o.term_id = p_term_id;
    delete from public.overall_question_means o
     where o.instructor_id = p_instructor_id and o.term_id = p_term_id;
    return query select p_instructor_id, p_term_id, 0, null::numeric, null::numeric, null::numeric;
    return;
  end if;

  -- overall_mean is generated (= combined_score_mean); it is not written here.
  insert into public.overall_total_survey (
    instructor_id, term_id, total_responses, management_total, performance_total, combined_total,
    management_mean, performance_mean, combined_score_mean
  )
  values (p_instructor_id, p_term_id, v_responses, v_m_total, v_p_total, v_m_total + v_p_total,
          v_m_mean, v_p_mean, v_combined)
  on conflict (instructor_id, term_id) do update set
    total_responses     = excluded.total_responses,
    management_total    = excluded.management_total,
    performance_total   = excluded.performance_total,
    combined_total      = excluded.combined_total,
    management_mean     = excluded.management_mean,
    performance_mean    = excluded.performance_mean,
    combined_score_mean = excluded.combined_score_mean;

  -- 6e. overall_question_means: all 20 questions, pooled across subjects.
  insert into public.overall_question_means (
    instructor_id, term_id, category, question_number, mean_score, response_count, updated_at
  )
  select p_instructor_id, p_term_id, a.category, a.question_number,
         round(avg(a.rating), 2),          -- NULL when nobody answered the question
         count(a.rating)::int,
         now()
  from public.sast_answers(p_instructor_id, p_term_id) a
  group by a.category, a.question_number
  on conflict (instructor_id, term_id, category, question_number) do update set
    mean_score     = excluded.mean_score,
    response_count = excluded.response_count,
    updated_at     = excluded.updated_at;

  return query select p_instructor_id, p_term_id, v_responses, v_m_mean, v_p_mean, v_combined;
end
$fn$;


-- 7. Only n8n (postgres role) and the service role run these; not callable over the API.
revoke all on function public.sast_answers(uuid, uuid)   from public, anon, authenticated;
revoke all on function public.recompute_sast(uuid, uuid) from public, anon, authenticated;
grant execute on function public.sast_answers(uuid, uuid)   to service_role;
grant execute on function public.recompute_sast(uuid, uuid) to service_role;


-- 8. Backfill: recompute every instructor + term that has raw rows ---------------------
do $$
declare
  r record;
  v_pairs integer := 0;
begin
  for r in
    select distinct "instructor_ID"::uuid as instructor_id, term_id::uuid as term_id
    from public.sast_all_raw_data_survey
    where "instructor_ID" is not null and term_id is not null
    order by 1, 2
  loop
    perform public.recompute_sast(r.instructor_id, r.term_id);
    v_pairs := v_pairs + 1;
  end loop;
  raise notice '013: recomputed % instructor/term pairs', v_pairs;
end $$;

commit;


-- Checks afterwards (read-only):
-- select conname from pg_constraint
--  where conrelid = 'public.sast_all_raw_data_survey'::regclass and conname like 'sast_raw_%_rating_1_to_5';
--  -> 20 rows
-- select count(*) from public.management_results where m1_count is null;   -- 0
-- select instructor_id, term_id, management_mean, performance_mean, combined_score_mean, overall_mean
--   from public.overall_total_survey order by combined_score_mean;          -- overall_mean = combined_score_mean
-- select * from public.recompute_sast('<instructor uuid>', '<term uuid>');  -- one row


-- =========================================================================================
-- ROLLBACK (restores the pre-013 schema; the recomputed numbers stay until the old n8n
-- query runs again). Uncomment and run.
-- =========================================================================================
-- begin;
-- drop function if exists public.recompute_sast(uuid, uuid);
-- drop function if exists public.sast_answers(uuid, uuid);
-- alter table public.overall_total_survey
--   alter column overall_mean set expression as (round((management_mean + performance_mean) / 2::numeric, 2));
-- update public.overall_question_means set mean_score = 0 where mean_score is null;
-- alter table public.overall_question_means alter column mean_score set default 0,
--   alter column mean_score set not null, drop column if exists response_count;
-- -- m*_mean / p*_mean: set NOT NULL again only after replacing NULLs (e.g. with 0):
-- -- update public.management_results set m1_mean = coalesce(m1_mean, 0), ... ;
-- -- alter table public.management_results alter column m1_mean set not null, ... ;
-- alter table public.management_results
--   drop column if exists m1_count, drop column if exists m2_count, drop column if exists m3_count,
--   drop column if exists m4_count, drop column if exists m5_count, drop column if exists m6_count,
--   drop column if exists m7_count, drop column if exists m8_count, drop column if exists m9_count,
--   drop column if exists m10_count;
-- alter table public.performance_results
--   drop column if exists p1_count, drop column if exists p2_count, drop column if exists p3_count,
--   drop column if exists p4_count, drop column if exists p5_count, drop column if exists p6_count,
--   drop column if exists p7_count, drop column if exists p8_count, drop column if exists p9_count,
--   drop column if exists p10_count;
-- do $$ declare q text; begin
--   foreach q in array array['m1','m2','m3','m4','m5','m6','m7','m8','m9','m10',
--                            'p1','p2','p3','p4','p5','p6','p7','p8','p9','p10'] loop
--     execute format('alter table public.sast_all_raw_data_survey drop constraint if exists %I',
--                    'sast_raw_' || q || '_rating_1_to_5');
--   end loop; end $$;
-- commit;
