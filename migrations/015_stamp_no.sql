-- ---------------------------------------------------------------------------
-- 015: the numbering-machine stamp of a SAST form (Stamp Phase B, 2026-10-16).
--
-- Every paper form gets a number from a numbering-machine stamp (black ink) BEFORE the student
-- fills it in. The number is any 3..12 digits, stored as TEXT exactly as printed (leading zeros
-- kept). A form is counted once per term: (term_id, stamp_no) is unique in the raw table, so a copy
-- of an already scanned form is caught (n8n auto-rejects it as duplicate_stamp).
--
--   sast_all_raw_data_survey.stamp_no / stamp_source   + CHECKs, partial UNIQUE (term_id, stamp_no)
--   failed_scan_queue.stamp_no / stamp_source          + CHECKs, plain index (term_id, stamp_no)
--   public.sast_stamp_duplicate_of(term, stamp, task)  -> the DUP object of the stored row, or NULL
--   public.save_website_raw_row(...)                   -> what 'Website Raw Upsert' (n8n) runs
--
-- stamp_source: 'gemini+python' (read automatically: Gemini and the OMR's stamp reader agree, 2026-10-17),
-- 'gemini' (Gemini alone: only with the n8n fallback switch ST_ALLOW_GEMINI_ONLY), 'staff' (typed or confirmed
-- by staff in the website review or Flutter), 'override' (staff rescued a form that was
-- auto-rejected as a duplicate). NULL when stamp_no is NULL. Google Sheet rows never get a stamp.
--
-- save_website_raw_row keeps the exact behaviour of n8n/tools/website_src/website_raw_upsert.sql
-- (same columns and values, ON CONFLICT (subject_id, term_id, "instructor_ID", student_id) DO
-- UPDATE ... WHERE s.task_id = EXCLUDED.task_id, so another page's row is never overwritten) plus
-- the two stamp columns, and answers with jsonb:
--   {"saved": true}
--   {"saved": false, "reason": "duplicate_student"}                  (another page's row; as before)
--   {"saved": false, "reason": "duplicate_stamp", "duplicate_of": DUP} (the stamp index said no)
-- DUP = {raw_id, task_id, batch_id (uuid from a 'web:<batch>:<sha16>' task_id, else null),
--        submitted_date (YYYY-MM-DD), instructor_id, subject_id, term_id, stamp_no}.
-- A unique_violation of any other constraint is raised again (never hidden).
--
-- Checked 2026-10-16 (read-only, Supabase SQL): PostgreSQL 17.6; 2452 raw rows (0 with a NULL
-- term_id), 121 queue rows; no column named stamp_no / stamp_source in public, no index named
-- sast_raw_term_stamp_uniq / failed_scan_queue_term_stamp_idx, no function save_website_raw_row /
-- sast_stamp_duplicate_of, no constraint with 'stamp' in its name. The new columns start NULL for
-- every existing row and the unique index is partial (WHERE stamp_no IS NOT NULL), so existing data
-- cannot violate it. To check again before applying:
--
--   select table_name, column_name from information_schema.columns
--    where table_schema = 'public' and column_name in ('stamp_no', 'stamp_source');          -- expect 0 rows
--   select indexname from pg_indexes where schemaname = 'public'
--    and indexname in ('sast_raw_term_stamp_uniq', 'failed_scan_queue_term_stamp_idx');      -- expect 0 rows
--   select proname from pg_proc where proname in ('save_website_raw_row', 'sast_stamp_duplicate_of'); -- 0 rows
--
-- Order: apply this BEFORE re-importing n8n/perfect-2.1.json (the workflow writes the new columns
-- and calls the new functions). Review before applying. Not auto-applied. Run in the Supabase SQL
-- Editor (or psql) after 014. One transaction: all or nothing. Safe to re-run.
-- ---------------------------------------------------------------------------
begin;


-- 1. Raw rows: the stamp + where it came from -----------------------------------------------
alter table public.sast_all_raw_data_survey add column if not exists stamp_no text;
alter table public.sast_all_raw_data_survey add column if not exists stamp_source text;

alter table public.sast_all_raw_data_survey drop constraint if exists sast_raw_stamp_no_format;
alter table public.sast_all_raw_data_survey add constraint sast_raw_stamp_no_format
  check (stamp_no is null or stamp_no ~ '^[0-9]{3,12}$');
alter table public.sast_all_raw_data_survey drop constraint if exists sast_raw_stamp_source_check;
alter table public.sast_all_raw_data_survey add constraint sast_raw_stamp_source_check
  check (stamp_source is null or stamp_source in ('gemini+python', 'gemini', 'staff', 'override'));

-- one form per stamp and term (rows without a stamp are never compared)
create unique index if not exists sast_raw_term_stamp_uniq
  on public.sast_all_raw_data_survey (term_id, stamp_no)
  where stamp_no is not null;


-- 2. Review queue: the stamp read on the page (or typed by staff), same checks, NOT unique -----
-- (the same form can be queued twice, e.g. the original and its auto-rejected copy; term_id is text)
alter table public.failed_scan_queue add column if not exists stamp_no text;
alter table public.failed_scan_queue add column if not exists stamp_source text;

alter table public.failed_scan_queue drop constraint if exists failed_scan_queue_stamp_no_format;
alter table public.failed_scan_queue add constraint failed_scan_queue_stamp_no_format
  check (stamp_no is null or stamp_no ~ '^[0-9]{3,12}$');
alter table public.failed_scan_queue drop constraint if exists failed_scan_queue_stamp_source_check;
alter table public.failed_scan_queue add constraint failed_scan_queue_stamp_source_check
  check (stamp_source is null or stamp_source in ('gemini+python', 'gemini', 'staff', 'override'));

create index if not exists failed_scan_queue_term_stamp_idx
  on public.failed_scan_queue (term_id, stamp_no)
  where stamp_no is not null;


-- 3. The stored form that already has this stamp in this term (DUP), or NULL ----------------------
-- p_task_id = the page asking: its own row is never its duplicate (no task id: every row counts).
-- Used by save_website_raw_row and by the n8n checks (Website Identity Check, Phone Stamp Check,
-- Correction Stamp Check), so DUP has one shape everywhere.
create or replace function public.sast_stamp_duplicate_of(p_term_id text, p_stamp_no text, p_task_id text)
returns jsonb
language sql
stable
set search_path = public
as $$
  select jsonb_build_object(
           'raw_id',         r.id,
           'task_id',        r.task_id,
           'batch_id',       substring(r.task_id from '^web:([0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}):'),
           'submitted_date', to_char(r.submitted_date, 'YYYY-MM-DD'),
           'instructor_id',  r."instructor_ID"::text,
           'subject_id',     r.subject_id::text,
           'term_id',        r.term_id::text,
           'stamp_no',       r.stamp_no)
  from public.sast_all_raw_data_survey r
  where nullif(btrim(coalesce(p_stamp_no, '')), '') is not null
    and r.stamp_no = btrim(p_stamp_no)
    and r.term_id::text = lower(btrim(coalesce(p_term_id, '')))
    and (nullif(btrim(coalesce(p_task_id, '')), '') is null or r.task_id is distinct from btrim(p_task_id))
  order by r.id
  limit 1;
$$;

revoke all on function public.sast_stamp_duplicate_of(text, text, text) from public, anon, authenticated;
grant execute on function public.sast_stamp_duplicate_of(text, text, text) to service_role;


-- 4. Website Raw Upsert: store a clean scanned page, never over another page's row or stamp ----------
-- Parameters as n8n sends them (text; the values are cast to the table's own column types by
-- jsonb_populate_record): batch instructor, subject, term, student id (NULL = blank), instructor
-- name, remark, scan date (YYYY-MM-DD), staff id, task_id, ratings ({m1..m10, p1..p10}: 1..5 or
-- null), stamp_no (3..12 digits, anything else = no stamp), stamp_source (gemini+python|gemini|staff|override).
create or replace function public.save_website_raw_row(
  p_instructor_id  text,
  p_subject_id     text,
  p_term_id        text,
  p_student_id     text,
  p_instructor     text,
  p_remarks        text,
  p_submitted_date text,
  p_staff_id       text,
  p_task_id        text,
  p_ratings        jsonb,
  p_stamp_no       text,
  p_stamp_source   text)
returns jsonb
language plpgsql
volatile
set search_path = public
as $$
declare
  v_stamp      text := nullif(btrim(coalesce(p_stamp_no, '')), '');
  v_source     text;
  v_row        public.sast_all_raw_data_survey;
  v_saved      boolean;
  v_constraint text;
begin
  if v_stamp is not null and v_stamp !~ '^[0-9]{3,12}$' then
    v_stamp := null;
  end if;
  v_source := case
                when v_stamp is null then null
                when p_stamp_source in ('gemini+python', 'gemini', 'staff', 'override') then p_stamp_source
                else 'gemini'
              end;

  v_row := jsonb_populate_record(null::public.sast_all_raw_data_survey,
    jsonb_build_object(
      'instructor_ID',           p_instructor_id,
      'subject_id',              p_subject_id,
      'term_id',                 p_term_id,
      'student_id',              p_student_id,
      'instructor',              p_instructor,
      'Remarks_and_Suggestions', p_remarks,
      'submitted_date',          p_submitted_date,
      'sao_staff_id',            p_staff_id,
      'validation_status',       'auto',
      'manually_corrected',      false,
      'task_id',                 p_task_id,
      'stamp_no',                v_stamp,
      'stamp_source',            v_source)
    || coalesce((select jsonb_object_agg(k, p_ratings -> k)
                   from unnest(array['m1', 'm2', 'm3', 'm4', 'm5', 'm6', 'm7', 'm8', 'm9', 'm10',
                                     'p1', 'p2', 'p3', 'p4', 'p5', 'p6', 'p7', 'p8', 'p9', 'p10']) as k
                  where jsonb_typeof(p_ratings) = 'object' and p_ratings ? k), '{}'::jsonb));

  begin
    insert into public.sast_all_raw_data_survey as s (
      "instructor_ID", subject_id, term_id, student_id, instructor,
      m1, m2, m3, m4, m5, m6, m7, m8, m9, m10,
      p1, p2, p3, p4, p5, p6, p7, p8, p9, p10,
      "Remarks_and_Suggestions", submitted_date, sao_staff_id,
      validation_status, manually_corrected, task_id, stamp_no, stamp_source
    ) values (
      v_row."instructor_ID", v_row.subject_id, v_row.term_id, v_row.student_id, v_row.instructor,
      v_row.m1, v_row.m2, v_row.m3, v_row.m4, v_row.m5, v_row.m6, v_row.m7, v_row.m8, v_row.m9, v_row.m10,
      v_row.p1, v_row.p2, v_row.p3, v_row.p4, v_row.p5, v_row.p6, v_row.p7, v_row.p8, v_row.p9, v_row.p10,
      v_row."Remarks_and_Suggestions", v_row.submitted_date, v_row.sao_staff_id,
      v_row.validation_status, v_row.manually_corrected, v_row.task_id, v_row.stamp_no, v_row.stamp_source
    )
    on conflict (subject_id, term_id, "instructor_ID", student_id) do update set
      instructor = excluded.instructor,
      m1 = excluded.m1, m2 = excluded.m2, m3 = excluded.m3, m4 = excluded.m4, m5 = excluded.m5,
      m6 = excluded.m6, m7 = excluded.m7, m8 = excluded.m8, m9 = excluded.m9, m10 = excluded.m10,
      p1 = excluded.p1, p2 = excluded.p2, p3 = excluded.p3, p4 = excluded.p4, p5 = excluded.p5,
      p6 = excluded.p6, p7 = excluded.p7, p8 = excluded.p8, p9 = excluded.p9, p10 = excluded.p10,
      "Remarks_and_Suggestions" = excluded."Remarks_and_Suggestions",
      submitted_date = excluded.submitted_date,
      sao_staff_id = excluded.sao_staff_id,
      validation_status = excluded.validation_status,
      manually_corrected = excluded.manually_corrected,
      stamp_no = excluded.stamp_no,
      stamp_source = excluded.stamp_source
    -- only this page's own row (a resend); another page's row is never overwritten
    where s.task_id = excluded.task_id
    returning true into v_saved;
  exception when unique_violation then
    get stacked diagnostics v_constraint = constraint_name;
    if v_constraint is distinct from 'sast_raw_term_stamp_uniq' then
      raise;
    end if;
    -- another form with this stamp is already stored for the term (also when two pages race:
    -- the second insert waits for the first to commit, then lands here)
    return jsonb_build_object('saved', false, 'reason', 'duplicate_stamp',
                              'duplicate_of', public.sast_stamp_duplicate_of(v_row.term_id::text, v_stamp, p_task_id));
  end;

  if v_saved then
    return jsonb_build_object('saved', true);
  end if;
  return jsonb_build_object('saved', false, 'reason', 'duplicate_student');
end;
$$;

revoke all on function public.save_website_raw_row(text, text, text, text, text, text, text, text, text, jsonb, text, text)
  from public, anon, authenticated;
grant execute on function public.save_website_raw_row(text, text, text, text, text, text, text, text, text, jsonb, text, text)
  to service_role;

commit;
