-- ---------------------------------------------------------------------------
-- 015b: delete the DUMMY scan data (test scans) before the stamp-number rule goes live.
--
-- What counts as scan data (and is deleted by default):
--   * website batch scans  -- task_id like 'web:<batch_id>:<sha16>'   (backend app/logic.py make_task_id)
--   * phone scans (Flutter) -- task_id 'SCAN-<epoch ms>'               (data_gatherer_screen.dart)
--   and everything that only exists because of them: the review queue (failed_scan_queue),
--   the phone scans' import_errors rows, the scan pictures (scan_error_images), the website
--   batches, their pages (scan_batch_items) and the pages that could not be saved
--   (scan_unsaved_pages). The view ocr_correction_log reads failed_scan_queue, so it empties
--   by itself.
--
-- NOT deleted by default (each is an OPTIONAL block below, commented out, see the questions):
--   * raw rows with task_id NULL: the Google Sheet imports, AND 29 rows that look like phone
--     scans from before the app stored a task_id (2026 submitted_date, stored one at a time)
--   * import_errors with task_id NULL (all source = 'google_sheet')
--   * 7 scan_error_images with task_id NULL (2026-09-13, referenced by nothing)
--   * student_remarks / instructor_wordcloud / instructor_ai_suggestions (no link to a raw row:
--     student_remarks.source_response_id is NULL on every row, they are per instructor + term)
--   * instructor_subjects links created by scans / batches
--   * audit_logs rows about scans (it is the audit trail)
--   * stations (laptop setup, not scan data)
--
-- Counts checked 2026-10-16 (read-only), = what the default run deletes:
--   sast_all_raw_data_survey   45  (web 38: 2026-09-27..2026-10-08; phone SCAN- 7: 2026-09-27..2026-10-02)
--                                   in 7 instructor + term pairs (all the same term); 3 of these
--                                   pairs have ONLY scan rows, so their totals disappear
--   failed_scan_queue         121  (web 84: 2026-09-27..2026-10-08; phone 37: 2026-09-13..2026-10-08)
--   import_errors              13  (phone, source 'scan': 2026-09-13..2026-09-27)
--   scan_error_images         168  (web 84, phone 84 -- 34 of the phone ones referenced by nothing;
--                                   ~107 MB of base64, the backup copies it once)
--   scan_unsaved_pages          6  (2026-09-30..2026-10-08)
--   scan_batch_items          160  (2026-09-27..2026-10-08; 33 with a picture in bucket scan-pages)
--   scan_batches               41  (2026-09-23..2026-10-02; 40 closed, 1 still OPEN)
--   recomputed aggregates for the 7 pairs (backed up first): management_results 24,
--     performance_results 24, overall_total_survey 7, overall_question_means 140
--   NOT deleted (for comparison): 2407 raw rows with task_id NULL (2378 with a 2025
--     submitted_date = Google Sheet imports, 29 with a 2026 one), 46 google_sheet import_errors,
--     7 NULL-task scan_error_images, 101 scan audit_logs rows.
--
-- Backups: every row deleted or changed is first copied to a table backup_015b_<table>
-- (RLS on, no grants to anon/authenticated, like backup_*_20261003). If ANY backup_015b_%
-- table already exists the script STOPS with an error and changes nothing: either 015b
-- already ran (a second run would back up nothing and could hide the real backup) or a half
-- run left them -- look at them, drop them by hand if they are not needed, then run again.
--
-- The picture files in the PRIVATE storage bucket 'scan-pages' (33 now) are NOT removed by
-- SQL (Supabase does not allow deleting storage.objects with SQL). After this ran, empty the
-- bucket in the dashboard: Storage > scan-pages > select all > Delete. Their list is kept in
-- backup_015b_scan_batch_items.page_image_path.
--
-- BEFORE running: stop the station watchers (no laptop sending pages), make sure nobody scans
-- with the phone app or works on the review page, and do it before the first real scan.
-- DRY RUN: replace the final `commit;` with `rollback;` -- the NOTICEs show the counts and
-- nothing stays (the backup tables are rolled back too).
--
-- Review before applying. Not auto-applied. Run in the Supabase SQL Editor (or psql) after
-- 013 and 014 (recompute_sast). One transaction: all or nothing.
-- ---------------------------------------------------------------------------
begin;


-- 0. Stop if backups of a previous run exist --------------------------------------------
do $$
declare
  v_existing text;
begin
  select string_agg(table_name, ', ' order by table_name) into v_existing
  from information_schema.tables
  where table_schema = 'public' and table_name like 'backup\_015b\_%';
  if v_existing is not null then
    raise exception '015b: backup tables already exist (%). 015b ran before or a run was left half-way. Check them, drop them by hand if not needed, then run again.', v_existing;
  end if;
end $$;


-- 1. What gets deleted (ids only; dropped at commit) ------------------------------------
-- 1a. raw rows: website + phone scans
create temp table t015b_raw on commit drop as
select r.id
from public.sast_all_raw_data_survey r
where r.task_id like 'web:%'
   or r.task_id ~ '^SCAN-[0-9]+$';

-- OPTIONAL B (question 2): raw rows with NO task_id but a 2026 submitted_date, stored one at a
-- time (2026-08-05..2026-09-21, 29 rows on 2026-10-16) -- most likely phone scans from before
-- the app wrote a task_id. Only uncomment if the user confirms they are test data.
-- insert into t015b_raw
-- select r.id from public.sast_all_raw_data_survey r
-- where r.task_id is null and r.submitted_date >= date '2026-01-01';

-- OPTIONAL C (question 1): ALL Google Sheet imports = raw rows with no task_id and a 2025
-- submitted_date (2378 rows on 2026-10-16, 2026-07-31..2026-09-27). This empties almost every
-- result in the system. Only uncomment if the user confirms the sheet data is dummy too.
-- insert into t015b_raw
-- select r.id from public.sast_all_raw_data_survey r
-- where r.task_id is null and (r.submitted_date < date '2026-01-01' or r.submitted_date is null);

-- 1b. the instructor + term pairs to recompute afterwards (collected BEFORE deleting)
create temp table t015b_pairs on commit drop as
select distinct r."instructor_ID"::uuid as instructor_id, r.term_id::uuid as term_id
from public.sast_all_raw_data_survey r
join t015b_raw x on x.id = r.id
where r."instructor_ID" is not null and r.term_id is not null;

-- 1c. review queue: website + phone scans (every website row also has a batch_id)
create temp table t015b_fsq on commit drop as
select q.id
from public.failed_scan_queue q
where q.task_id like 'web:%'
   or q.task_id ~ '^SCAN-[0-9]+$'
   or q.batch_id is not null;

-- 1d. import errors of scans (phone path; website ones would be 'web:%')
create temp table t015b_ie on commit drop as
select e.id
from public.import_errors e
where e.task_id like 'web:%'
   or e.task_id ~ '^SCAN-[0-9]+$';

-- OPTIONAL D (question 1, goes with OPTIONAL C): the Google Sheet import errors
-- (task_id NULL, source 'google_sheet', 46 rows on 2026-10-16).
-- insert into t015b_ie
-- select e.id from public.import_errors e where e.task_id is null and e.source = 'google_sheet';

-- 1e. scan pictures: of a scan task, or used by a queue / import-error row deleted above
create temp table t015b_img on commit drop as
select i.id
from public.scan_error_images i
where i.task_id like 'web:%'
   or i.task_id ~ '^SCAN-[0-9]+$'
   or i.id in (select q.scan_image_id from public.failed_scan_queue q join t015b_fsq x on x.id = q.id)
   or i.id in (select e.scan_image_id from public.import_errors e join t015b_ie x on x.id = e.id);

-- OPTIONAL E (question 3): the 7 pictures with no task_id (2026-09-13, used by nothing).
-- insert into t015b_img
-- select i.id from public.scan_error_images i
-- where i.task_id is null
--   and not exists (select 1 from public.failed_scan_queue q where q.scan_image_id = i.id)
--   and not exists (select 1 from public.import_errors e where e.scan_image_id = i.id);

-- a picture still used by a row that stays must stay too (otherwise the FK would fail)
delete from t015b_img x
where exists (select 1 from public.failed_scan_queue q
              where q.scan_image_id = x.id and q.id not in (select id from t015b_fsq))
   or exists (select 1 from public.import_errors e
              where e.scan_image_id = x.id and e.id not in (select id from t015b_ie));

-- 1f. website batches, their pages and unsaved pages: all of them are scan data
create temp table t015b_batch  on commit drop as select b.id from public.scan_batches b;
create temp table t015b_item   on commit drop as select i.id from public.scan_batch_items i;
create temp table t015b_unsaved on commit drop as select u.id from public.scan_unsaved_pages u;


-- 2. Backups (RLS on, not readable over the API) ----------------------------------------
create table public.backup_015b_sast_all_raw_data_survey as
  select r.* from public.sast_all_raw_data_survey r where r.id in (select id from t015b_raw);
create table public.backup_015b_failed_scan_queue as
  select q.* from public.failed_scan_queue q where q.id in (select id from t015b_fsq);
create table public.backup_015b_import_errors as
  select e.* from public.import_errors e where e.id in (select id from t015b_ie);
create table public.backup_015b_scan_error_images as
  select i.* from public.scan_error_images i where i.id in (select id from t015b_img);
create table public.backup_015b_scan_unsaved_pages as
  select u.* from public.scan_unsaved_pages u where u.id in (select id from t015b_unsaved);
create table public.backup_015b_scan_batch_items as
  select i.* from public.scan_batch_items i where i.id in (select id from t015b_item);
create table public.backup_015b_scan_batches as
  select b.* from public.scan_batches b where b.id in (select id from t015b_batch);
-- aggregates of the affected pairs, as they were before the recompute
create table public.backup_015b_management_results as
  select m.* from public.management_results m
  join t015b_pairs p on p.instructor_id = m.instructor_id and p.term_id = m.term_id;
create table public.backup_015b_performance_results as
  select m.* from public.performance_results m
  join t015b_pairs p on p.instructor_id = m.instructor_id and p.term_id = m.term_id;
create table public.backup_015b_overall_total_survey as
  select o.* from public.overall_total_survey o
  join t015b_pairs p on p.instructor_id = o.instructor_id and p.term_id = o.term_id;
create table public.backup_015b_overall_question_means as
  select o.* from public.overall_question_means o
  join t015b_pairs p on p.instructor_id = o.instructor_id and p.term_id = o.term_id;
create table public.backup_015b_pairs as
  select * from t015b_pairs;

do $$
declare
  t text;
begin
  for t in
    select table_name from information_schema.tables
    where table_schema = 'public' and table_name like 'backup\_015b\_%'
  loop
    execute format('alter table public.%I enable row level security', t);
    execute format('revoke all on table public.%I from anon, authenticated', t);
  end loop;
end $$;


-- 3. Delete (FK-safe order), then recompute the affected pairs -------------------------
do $$
declare
  n_unsaved integer; n_fsq integer; n_ie integer; n_img integer;
  n_item integer; n_batch integer; n_raw integer;
  n_pairs integer := 0; n_pairs_empty integer := 0;
  p record;
  res record;
begin
  -- scan_unsaved_pages -> scan_batch_items / scan_batches (cascade, but explicit)
  delete from public.scan_unsaved_pages where id in (select id from t015b_unsaved);
  get diagnostics n_unsaved = row_count;

  -- failed_scan_queue -> scan_batches, scan_error_images
  delete from public.failed_scan_queue where id in (select id from t015b_fsq);
  get diagnostics n_fsq = row_count;

  -- import_errors -> scan_error_images
  delete from public.import_errors where id in (select id from t015b_ie);
  get diagnostics n_ie = row_count;

  delete from public.scan_error_images where id in (select id from t015b_img);
  get diagnostics n_img = row_count;

  -- scan_batch_items -> scan_batches
  delete from public.scan_batch_items where id in (select id from t015b_item);
  get diagnostics n_item = row_count;

  delete from public.scan_batches where id in (select id from t015b_batch);
  get diagnostics n_batch = row_count;

  -- raw rows (nothing references them by FK)
  delete from public.sast_all_raw_data_survey where id in (select id from t015b_raw);
  get diagnostics n_raw = row_count;

  -- recompute_sast rebuilds management_results / performance_results / overall_total_survey /
  -- overall_question_means from the remaining raw rows and DELETES the rows of a pair that has
  -- no counted form left (013/014: 6b/6c delete subjects not re-saved, 6d deletes the totals and
  -- question means when total_responses = 0). It does not remove instructor_subjects links.
  for p in select instructor_id, term_id from t015b_pairs order by 1, 2 loop
    select * into res from public.recompute_sast(p.instructor_id, p.term_id);
    n_pairs := n_pairs + 1;
    if coalesce(res.total_responses, 0) = 0 then
      n_pairs_empty := n_pairs_empty + 1;
    end if;
  end loop;

  raise notice '015b deleted: sast_all_raw_data_survey %, failed_scan_queue %, import_errors %, scan_error_images %',
    n_raw, n_fsq, n_ie, n_img;
  raise notice '015b deleted: scan_unsaved_pages %, scan_batch_items %, scan_batches %',
    n_unsaved, n_item, n_batch;
  raise notice '015b recomputed % instructor/term pairs (% now have no counted form, their totals are gone)',
    n_pairs, n_pairs_empty;
  raise notice '015b next: empty storage bucket scan-pages in the dashboard (% page pictures listed in backup_015b_scan_batch_items)',
    (select count(*) from public.backup_015b_scan_batch_items where page_image_path is not null);
end $$;


-- OPTIONAL F (question 4): derived remark data of the pairs that are left with NO raw row at
-- all (on 2026-10-16: 3 pairs -> student_remarks 4, instructor_wordcloud 2,
-- instructor_ai_suggestions 1). student_remarks cannot be tied to a single form
-- (source_response_id is NULL everywhere), so remarks of pairs that still have Google Sheet
-- rows are NOT touched here; 26 remarks match a scanned form's remark text exactly, but that
-- is a text match, not a link.
-- create table public.backup_015b_student_remarks as
--   select s.* from public.student_remarks s join t015b_pairs p using (instructor_id, term_id)
--   where not exists (select 1 from public.sast_all_raw_data_survey r
--                     where r."instructor_ID" = s.instructor_id and r.term_id = s.term_id);
-- create table public.backup_015b_instructor_wordcloud as
--   select w.* from public.instructor_wordcloud w join t015b_pairs p using (instructor_id, term_id)
--   where not exists (select 1 from public.sast_all_raw_data_survey r
--                     where r."instructor_ID" = w.instructor_id and r.term_id = w.term_id);
-- create table public.backup_015b_instructor_ai_suggestions as
--   select a.* from public.instructor_ai_suggestions a join t015b_pairs p using (instructor_id, term_id)
--   where not exists (select 1 from public.sast_all_raw_data_survey r
--                     where r."instructor_ID" = a.instructor_id and r.term_id = a.term_id);
-- alter table public.backup_015b_student_remarks enable row level security;
-- alter table public.backup_015b_instructor_wordcloud enable row level security;
-- alter table public.backup_015b_instructor_ai_suggestions enable row level security;
-- revoke all on public.backup_015b_student_remarks, public.backup_015b_instructor_wordcloud,
--   public.backup_015b_instructor_ai_suggestions from anon, authenticated;
-- delete from public.student_remarks where id in (select id from public.backup_015b_student_remarks);
-- delete from public.instructor_wordcloud where id in (select id from public.backup_015b_instructor_wordcloud);
-- delete from public.instructor_ai_suggestions where id in (select id from public.backup_015b_instructor_ai_suggestions);

-- OPTIONAL G (question 5): instructor_subjects links that only scans / batches created
-- (16 on 2026-10-16: no Google Sheet raw row for that instructor + subject + term).
-- Careful: an admin may have assigned some of these on purpose.
-- create table public.backup_015b_instructor_subjects as
--   select s.* from public.instructor_subjects s
--   where not exists (select 1 from public.sast_all_raw_data_survey r
--                     where r."instructor_ID" = s.instructor_id and r.subject_id = s.subject_id
--                       and r.term_id = s.term_id)
--     and (exists (select 1 from public.backup_015b_sast_all_raw_data_survey r
--                  where r."instructor_ID" = s.instructor_id and r.subject_id = s.subject_id
--                    and r.term_id = s.term_id)
--       or exists (select 1 from public.backup_015b_scan_batches b
--                  where b.instructor_id = s.instructor_id and b.subject_id = s.subject_id
--                    and b.term_id = s.term_id));
-- alter table public.backup_015b_instructor_subjects enable row level security;
-- revoke all on public.backup_015b_instructor_subjects from anon, authenticated;
-- delete from public.instructor_subjects where id in (select id from public.backup_015b_instructor_subjects);

-- OPTIONAL H (question 6): audit_logs rows about scans (101 on 2026-10-16). Normally kept.
-- create table public.backup_015b_audit_logs as
--   select a.* from public.audit_logs a
--   where a.action in ('scan_batch_started', 'scan_batch_closed', 'scan_review_approved',
--                      'scan_review_rejected', 'scan_unsaved_retry', 'scan_unsaved_retry_result');
-- alter table public.backup_015b_audit_logs enable row level security;
-- revoke all on public.backup_015b_audit_logs from anon, authenticated;
-- delete from public.audit_logs where id in (select id from public.backup_015b_audit_logs);

commit;


-- Checks afterwards (read-only):
-- no scan rows left (all 0):
-- select (select count(*) from public.sast_all_raw_data_survey
--          where task_id like 'web:%' or task_id ~ '^SCAN-[0-9]+$')                     as raw_scan,
--        (select count(*) from public.failed_scan_queue)                                 as fsq,
--        (select count(*) from public.import_errors where task_id ~ '^(web:|SCAN-)')      as ie_scan,
--        (select count(*) from public.scan_error_images
--          where task_id like 'web:%' or task_id ~ '^SCAN-[0-9]+$')                      as imgs,
--        (select count(*) from public.scan_batches)                                      as batches,
--        (select count(*) from public.scan_batch_items)                                  as items,
--        (select count(*) from public.scan_unsaved_pages)                                as unsaved;
-- untouched (2026-10-16: 2407 and 46, unless an OPTIONAL block was enabled):
-- select count(*) from public.sast_all_raw_data_survey where task_id is null;
-- select count(*) from public.import_errors where task_id is null;
-- totals of the affected pairs, before -> after:
-- select b.instructor_id, b.term_id, b.total_responses as before, o.total_responses as after
--   from public.backup_015b_overall_total_survey b
--   left join public.overall_total_survey o using (instructor_id, term_id);
-- every pair's totals match its remaining raw rows (one row each):
-- select p.*, (select total_responses from public.overall_total_survey o
--               where o.instructor_id = p.instructor_id and o.term_id = p.term_id) as total_responses
--   from public.backup_015b_pairs p;
-- backups are not readable over the API (rowsecurity = true):
-- select tablename, rowsecurity from pg_tables where schemaname = 'public' and tablename like 'backup\_015b\_%';
-- pictures still in the bucket (should be 0 after emptying it in the dashboard):
-- select count(*) from storage.objects where bucket_id = 'scan-pages';


-- =========================================================================================
-- RESTORE (puts the deleted rows back from the backups and recomputes). Uncomment and run.
-- Order: parents first. import_errors.id is GENERATED ALWAYS -> overriding system value.
-- =========================================================================================
-- begin;
-- insert into public.scan_batches       select * from public.backup_015b_scan_batches;
-- insert into public.scan_batch_items   select * from public.backup_015b_scan_batch_items;
-- insert into public.scan_unsaved_pages select * from public.backup_015b_scan_unsaved_pages;
-- insert into public.scan_error_images  select * from public.backup_015b_scan_error_images;
-- insert into public.failed_scan_queue  select * from public.backup_015b_failed_scan_queue;
-- insert into public.import_errors overriding system value select * from public.backup_015b_import_errors;
-- insert into public.sast_all_raw_data_survey select * from public.backup_015b_sast_all_raw_data_survey;
-- select public.recompute_sast(instructor_id, term_id) from public.backup_015b_pairs;
-- commit;
-- (the page pictures in bucket scan-pages cannot be restored once the bucket was emptied)

-- CLEANUP (once you are sure the backups are not needed; frees ~110 MB):
-- drop table if exists public.backup_015b_sast_all_raw_data_survey, public.backup_015b_failed_scan_queue,
--   public.backup_015b_import_errors, public.backup_015b_scan_error_images,
--   public.backup_015b_scan_unsaved_pages, public.backup_015b_scan_batch_items,
--   public.backup_015b_scan_batches, public.backup_015b_management_results,
--   public.backup_015b_performance_results, public.backup_015b_overall_total_survey,
--   public.backup_015b_overall_question_means, public.backup_015b_pairs;
