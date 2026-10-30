-- ---------------------------------------------------------------------------
-- 021: re-importing a Google Sheet SKIPS the forms that are already stored ("already imported earlier",
--      not an error) + sheet_import_jobs.details (the per-reason breakdown the app shows).
--
-- Why (2026-10-30): the same 40-row SET demo sheet was imported 4 times into one term. Run 1 saved 36.
-- Runs 2-4 said "Imported 2, Not imported 38": 36 rows were refused as duplicate_stamp (the import was
-- from a COPY of the sheet, a different Google file id, so the task_id sheet:<file>:<term>:<row> did not
-- match and the stamp check called the stored form a duplicate = an error), and the "2 imported" were
-- two rows that run 2 saved as new and runs 3 and 4 then OVERWROTE in place (same file -> same task_id ->
-- save_eval_response updated the row and answered saved:true, updated:true, which counted as imported).
--
-- What changes (ADDITIVE; save_eval_response keeps its 16-argument signature, CREATE OR REPLACE keeps
-- its grants; nothing is dropped)
--   save_eval_response: when called by an import (p_import_job_id set), a form that is already stored
--   for this term by an EARLIER import job (or outside any import) is NOT saved and NOT replaced:
--     {"saved":false, "reason":"already_imported", "match":"task_id|supervisor|stamp|student_id",
--      "response_id":<stored form id>, "import_job_id":<its job id or null>}
--   It matches, in this order:
--     task_id     the same sheet row (same file, term, row number) was imported before
--     supervisor  SUP: the same supervisor + instructor + subject + term is already stored (as before,
--                 the supervisor key finds the form; it is no longer replaced by a later import)
--     stamp       the same stamp number in this term, SAME instrument + instructor + subject
--     student_id  SET: the same student id for the same instrument + instructor + subject + term
--   Unchanged:
--     * the same stamp on a DIFFERENT instructor or subject (or in an old SAST scan) -> duplicate_stamp
--     * a repeated student inside the SAME import job -> duplicate_student
--     * the same import job saving its own row again (an n8n retry) -> replaces it, as before
--     * calls without an import job (web, scan, resends by task_id) -> exactly as before
--   Old SAST forms: eval_stamp_duplicate_of ALREADY checks sast_all_raw_data_survey (stamp_no, same
--   term) through sast_stamp_duplicate_of (019), and the trigger eval_responses_stamp_guard backs it up,
--   so a stamp used by an old-form scan stays duplicate_stamp. Not changed here; the preview proves it.
--
--   sheet_import_jobs.details jsonb (nullable): written by the n8n node 'Mark Import Job Done':
--     {form_type, term_label, rows, new, already_imported, excluded_under_5, rejected,
--      reasons:[{reason, kind, plain_text, count, rows:[sheet row numbers]}]}
--   imported_count = new; error_count = rejected only (already_imported is not an error).
--   No student id and no instructor name is ever put in it. Readable like the rest of the row (018:
--   SAO office only).
--
-- Run this BEFORE re-importing the n8n workflow (perfect-2.1.json): its 'Mark Import Job Done' writes
-- the details column.
--
-- Like 016-020 it ends with ROLLBACK so a first run only previews. Check the output, then change the
-- last line to  commit;  and run it again.
-- ---------------------------------------------------------------------------
begin;

alter table public.sheet_import_jobs add column if not exists details jsonb;

do $$
begin
  if not exists (select 1 from pg_constraint
                  where conrelid = 'public.sheet_import_jobs'::regclass
                    and conname = 'sheet_import_jobs_details_object') then
    alter table public.sheet_import_jobs
      add constraint sheet_import_jobs_details_object
      check (details is null or jsonb_typeof(details) = 'object');
  end if;
end $$;

comment on column public.sheet_import_jobs.details is
  'Per-import breakdown (021): {form_type, term_label, rows, new, already_imported, excluded_under_5, rejected, reasons:[{reason, kind, plain_text, count, rows}]}. rows = sheet row numbers. Never a student id or an instructor name.';
comment on column public.sheet_import_jobs.imported_count is
  'Rows saved as NEW forms by this import (021: rows already imported earlier are not counted).';
comment on column public.sheet_import_jobs.error_count is
  'Rows refused (unmatched, invalid, duplicate stamp/student, database error). 021: rows already imported earlier are skipped, not errors.';


create or replace function public.save_eval_response(
  p_instrument_code text, p_instructor_id text, p_subject_id text, p_term_id text, p_answers jsonb,
  p_comments text, p_source text, p_stamp_no text default null, p_stamp_source text default null,
  p_evaluator_user_id text default null, p_import_job_id text default null, p_task_id text default null,
  p_created_by text default null, p_status text default 'submitted', p_recompute boolean default true,
  p_details jsonb default null)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_uid         uuid := auth.uid();
  v_is_sao      boolean := false;
  v_inst        public.eval_instruments;
  v_instructor  uuid;
  v_subject     uuid;
  v_term        uuid;
  v_evaluator   uuid;
  v_job         uuid;
  v_created_by  uuid;
  v_source      text := lower(btrim(coalesce(p_source, '')));
  v_status      text := lower(btrim(coalesce(p_status, 'submitted')));
  v_task        text := nullif(btrim(coalesce(p_task_id, '')), '');
  v_stamp       text := nullif(btrim(coalesce(p_stamp_no, '')), '');
  v_stamp_src   text;
  v_mapped      bigint;
  v_max         numeric;
  v_ratings     smallint[];
  v_seen        boolean[];
  v_answered    integer;
  v_total       integer;
  v_pct         numeric;
  v_item        integer;
  v_txt         text;
  r             record;
  v_old         public.eval_responses;
  v_old_by      text;      -- 021: how v_old was found ('task_id' / 'supervisor')
  v_prev_id     bigint;    -- 021: a stored form this import row matches
  v_prev_job    uuid;
  v_id          bigint;
  v_dup         jsonb;
  v_constraint  text;
  v_res         record;
  v_result      jsonb;     -- recompute_eval's pooled overall row (NULL when p_recompute = false)
  v_has_det     boolean := jsonb_typeof(p_details) = 'object';
  v_student     text;
  v_section     text;
  v_plevel      text;
  v_pyear       text;
  v_sdate       date;
  v_rank        text;
  v_desig       text;
begin
  -- instrument
  select * into v_inst from public.eval_instruments i
   where i.code = upper(btrim(coalesce(p_instrument_code, '')));
  if not found then
    return jsonb_build_object('saved', false, 'reason', 'unknown_instrument');
  end if;
  if v_inst.is_legacy then
    return jsonb_build_object('saved', false, 'reason', 'legacy_instrument');
  end if;

  -- ids
  begin
    v_instructor := nullif(btrim(coalesce(p_instructor_id, '')), '')::uuid;
    v_subject    := nullif(btrim(coalesce(p_subject_id, '')), '')::uuid;
    v_term       := nullif(btrim(coalesce(p_term_id, '')), '')::uuid;
    v_evaluator  := nullif(btrim(coalesce(p_evaluator_user_id, '')), '')::uuid;
    v_job        := nullif(btrim(coalesce(p_import_job_id, '')), '')::uuid;
    v_created_by := nullif(btrim(coalesce(p_created_by, '')), '')::uuid;
  exception when invalid_text_representation then
    return jsonb_build_object('saved', false, 'reason', 'invalid_id');
  end;

  -- who is calling (no JWT = n8n / service_role: trusted)
  if v_uid is not null then
    v_is_sao := public.eval_is_sao_office();
    if not v_is_sao then
      if v_inst.evaluator_type = 'supervisor' and public.is_dept_head() then
        v_evaluator := v_uid;
        v_source    := 'web';
        if v_instructor is not null and v_term is not null
           and not public.eval_dept_head_can_evaluate(v_instructor, v_term) then
          if v_instructor = v_uid then
            return jsonb_build_object('saved', false, 'reason', 'self_evaluation');
          end if;
          raise exception 'save_eval_response: faculty is not in your department'
            using errcode = 'insufficient_privilege';
        end if;
      else
        raise exception 'save_eval_response: not allowed' using errcode = 'insufficient_privilege';
      end if;
    end if;
    v_created_by := v_uid;
  end if;

  if v_instructor is null or v_term is null then
    return jsonb_build_object('saved', false, 'reason', 'missing_instructor_or_term');
  end if;
  if v_inst.evaluator_type = 'student' then
    v_evaluator := null;                       -- privacy: a student is never stored
    if v_subject is null then
      return jsonb_build_object('saved', false, 'reason', 'missing_subject');
    end if;
  else
    if v_evaluator is null then
      return jsonb_build_object('saved', false, 'reason', 'missing_evaluator');
    end if;
    if v_evaluator = v_instructor then
      return jsonb_build_object('saved', false, 'reason', 'self_evaluation');
    end if;
  end if;
  if v_source not in ('sheet', 'web', 'scan') then
    return jsonb_build_object('saved', false, 'reason', 'invalid_source');
  end if;
  if v_status not in ('submitted', 'draft') then
    return jsonb_build_object('saved', false, 'reason', 'invalid_status');
  end if;

  -- the term's instrument (when the admin has set one)
  select t.instrument_id into v_mapped
    from public.eval_term_instruments t
   where t.term_id = v_term and t.evaluator_type = v_inst.evaluator_type;
  if v_mapped is not null and v_mapped <> v_inst.id then
    return jsonb_build_object('saved', false, 'reason', 'instrument_not_for_term',
      'term_instrument', (select code from public.eval_instruments where id = v_mapped));
  end if;

  -- form details (migration 020). Student forms: student_id (SAO only); supervisor forms: rank, designation.
  if v_has_det then
    v_student := case when v_inst.evaluator_type = 'student'
                      then left(nullif(btrim(coalesce(p_details ->> 'student_id', '')), ''), 60) end;
    v_section := left(nullif(btrim(coalesce(p_details ->> 'section', '')), ''), 100);
    v_plevel  := left(nullif(btrim(coalesce(p_details ->> 'program_level', '')), ''), 100);
    v_pyear   := left(nullif(btrim(coalesce(p_details ->> 'program_year', '')), ''), 100);
    v_rank    := case when v_inst.evaluator_type = 'supervisor'
                      then left(nullif(btrim(coalesce(p_details ->> 'academic_rank', '')), ''), 100) end;
    v_desig   := case when v_inst.evaluator_type = 'supervisor'
                      then left(nullif(btrim(coalesce(p_details ->> 'designation', '')), ''), 100) end;
    begin
      v_sdate := nullif(btrim(coalesce(p_details ->> 'submitted_date', '')), '')::date;
    exception when others then
      v_sdate := null;                         -- an unreadable date is dropped, never fails the form
    end;
  end if;

  -- answers -> v_ratings[1..item_count]
  if jsonb_typeof(p_answers) is distinct from 'object' and jsonb_typeof(p_answers) is distinct from 'array' then
    return jsonb_build_object('saved', false, 'reason', 'invalid_answers');
  end if;
  v_ratings := array_fill(null::smallint, array[v_inst.item_count::int]);
  v_seen    := array_fill(false, array[v_inst.item_count::int]);
  for r in
    select x.o::text as k, x.v
      from jsonb_array_elements(case when jsonb_typeof(p_answers) = 'array' then p_answers else '[]'::jsonb end)
           with ordinality as x(v, o)
    union all
    select e.key, e.value
      from jsonb_each(case when jsonb_typeof(p_answers) = 'object' then p_answers else '{}'::jsonb end) e
  loop
    v_item := substring(btrim(r.k) from '^[A-Za-z_]*([0-9]+)$')::int;
    if v_item is null or v_item < 1 or v_item > v_inst.item_count then
      return jsonb_build_object('saved', false, 'reason', 'invalid_item', 'key', r.k);
    end if;
    if v_seen[v_item] then
      return jsonb_build_object('saved', false, 'reason', 'duplicate_item', 'item_no', v_item);
    end if;
    v_seen[v_item] := true;
    v_txt := btrim(coalesce(r.v #>> '{}', ''));
    if r.v is null or jsonb_typeof(r.v) = 'null' or v_txt = '' then
      v_ratings[v_item] := null;               -- blank = NULL, never 0
    elsif v_txt ~ '^[1-5](\.0+)?$' then
      v_ratings[v_item] := left(v_txt, 1)::smallint;
    else
      return jsonb_build_object('saved', false, 'reason', 'invalid_rating', 'item_no', v_item, 'value', r.v);
    end if;
  end loop;

  select count(x)::int, coalesce(sum(x), 0)::int into v_answered, v_total from unnest(v_ratings) x;
  v_max := public.eval_scale_max(v_inst.scale);
  v_pct := case when v_answered > 0 then round(v_total::numeric / (v_max * v_answered) * 100, 2) end;

  -- stamp (015 format rule)
  if v_stamp is not null and v_stamp !~ '^[0-9]{3,12}$' then
    v_stamp := null;
  end if;
  v_stamp_src := case
                   when v_stamp is null then null
                   when p_stamp_source in ('gemini+python', 'gemini', 'staff', 'override', 'sheet') then p_stamp_source
                   when v_source = 'sheet' then 'sheet'
                   when v_source = 'scan'  then 'gemini'
                   else 'staff'
                 end;

  -- the form this replaces: same task_id, or the same supervisor key
  if v_task is not null then
    select * into v_old from public.eval_responses x where x.task_id = v_task;
    if found and v_old.instrument_id <> v_inst.id then
      return jsonb_build_object('saved', false, 'reason', 'task_id_conflict', 'response_id', v_old.id);
    end if;
    if found then
      v_old_by := 'task_id';
    end if;
  end if;
  if v_old.id is null and v_inst.evaluator_type = 'supervisor' then
    select * into v_old from public.eval_responses x
     where x.evaluator_type = 'supervisor' and x.status <> 'void'
       and x.evaluator_user_id = v_evaluator and x.instructor_id = v_instructor
       and x.subject_id is not distinct from v_subject and x.term_id = v_term;
    if found then
      v_old_by := 'supervisor';
    end if;
  end if;
  if v_old.id is not null and v_uid is not null and not v_is_sao
     and v_old.evaluator_user_id is distinct from v_uid then
    raise exception 'save_eval_response: not your form' using errcode = 'insufficient_privilege';
  end if;

  -- 021: an import never replaces a form stored by an EARLIER import (or outside any import): skipped.
  -- The same import job saving its own row again (an n8n retry) still replaces it.
  if v_job is not null and v_old.id is not null and v_old.import_job_id is distinct from v_job then
    return jsonb_build_object('saved', false, 'reason', 'already_imported', 'match', v_old_by,
      'response_id', v_old.id, 'import_job_id', v_old.import_job_id);
  end if;

  -- stamp already used in this term (SAST or eval)?
  if v_stamp is not null then
    perform pg_advisory_xact_lock(hashtext('ievaluate:stamp:' || v_term::text || ':' || v_stamp));
    -- 021: the same stamp on the SAME instrument + instructor + subject from an earlier import = this form
    -- is already stored (skipped, not an error). Any other use of the stamp stays duplicate_stamp.
    if v_job is not null then
      select x.id, x.import_job_id into v_prev_id, v_prev_job
        from public.eval_responses x
       where x.term_id = v_term and x.stamp_no = v_stamp
         and x.instrument_id = v_inst.id and x.instructor_id = v_instructor
         and x.subject_id is not distinct from v_subject
         and x.status <> 'void'
         and x.import_job_id is distinct from v_job
         and (v_old.id is null or x.id <> v_old.id)
       order by x.id
       limit 1;
      if v_prev_id is not null then
        return jsonb_build_object('saved', false, 'reason', 'already_imported', 'match', 'stamp',
          'response_id', v_prev_id, 'import_job_id', v_prev_job);
      end if;
    end if;
    v_dup := public.eval_stamp_duplicate_of(v_term::text, v_stamp, v_task, v_old.id);
    if v_dup is not null then
      return jsonb_build_object('saved', false, 'reason', 'duplicate_stamp', 'duplicate_of', v_dup);
    end if;
  end if;

  -- same student, instrument, instructor, subject, term (not the form being replaced)? The unique index backs
  -- this up. 021: stored by an earlier import (or outside any import) -> already_imported; by this same
  -- import job (the student is twice in the sheet) -> duplicate_student, as before.
  if v_student is not null then
    v_prev_id := null;
    v_prev_job := null;
    select x.id, x.import_job_id into v_prev_id, v_prev_job
      from public.eval_responses x
     where x.evaluator_type = 'student' and x.status <> 'void'
       and lower(btrim(x.student_id)) = lower(v_student)
       and x.instrument_id = v_inst.id and x.instructor_id = v_instructor
       and x.subject_id = v_subject and x.term_id = v_term
       and (v_old.id is null or x.id <> v_old.id)
     order by x.id
     limit 1;
    if v_prev_id is not null then
      if v_job is not null and v_prev_job is distinct from v_job then
        return jsonb_build_object('saved', false, 'reason', 'already_imported', 'match', 'student_id',
          'response_id', v_prev_id, 'import_job_id', v_prev_job);
      end if;
      return jsonb_build_object('saved', false, 'reason', 'duplicate_student');
    end if;
  end if;

  begin
    if v_old.id is null then
      insert into public.eval_responses (
        instrument_id, evaluator_type, instructor_id, subject_id, term_id, evaluator_user_id,
        stamp_no, stamp_source, comments, source, import_job_id, task_id, status,
        answered_count, answered_total, rating_pct, created_by,
        student_id, section, program_level, program_year, submitted_date, academic_rank, designation)
      values (
        v_inst.id, v_inst.evaluator_type, v_instructor, v_subject, v_term, v_evaluator,
        v_stamp, v_stamp_src, nullif(btrim(coalesce(p_comments, '')), ''), v_source, v_job, v_task, v_status,
        v_answered, v_total, v_pct, v_created_by,
        v_student, v_section, v_plevel, v_pyear, v_sdate, v_rank, v_desig)
      returning id into v_id;
    else
      v_id := v_old.id;
      update public.eval_responses x set
        instructor_id = v_instructor, subject_id = v_subject, term_id = v_term,
        evaluator_user_id = v_evaluator,
        stamp_no = v_stamp, stamp_source = v_stamp_src,
        comments = nullif(btrim(coalesce(p_comments, '')), ''),
        source = v_source, import_job_id = coalesce(v_job, x.import_job_id),
        task_id = coalesce(v_task, x.task_id), status = v_status,
        answered_count = v_answered, answered_total = v_total, rating_pct = v_pct,
        student_id     = case when v_has_det then v_student else x.student_id end,
        section        = case when v_has_det then v_section else x.section end,
        program_level  = case when v_has_det then v_plevel  else x.program_level end,
        program_year   = case when v_has_det then v_pyear   else x.program_year end,
        submitted_date = case when v_has_det then v_sdate   else x.submitted_date end,
        academic_rank  = case when v_has_det then v_rank    else x.academic_rank end,
        designation    = case when v_has_det then v_desig   else x.designation end,
        updated_at = now()
      where x.id = v_id;
      delete from public.eval_answers a where a.response_id = v_id;
    end if;

    insert into public.eval_answers (response_id, instrument_id, item_no, rating)
    select v_id, v_inst.id, g::smallint, v_ratings[g]
    from generate_series(1, v_inst.item_count) g;
  exception
    when unique_violation then
      get stacked diagnostics v_constraint = constraint_name;
      if v_constraint in ('eval_responses_term_stamp_uniq', 'eval_responses_stamp_in_sast') then
        return jsonb_build_object('saved', false, 'reason', 'duplicate_stamp',
          'duplicate_of', public.eval_stamp_duplicate_of(v_term::text, v_stamp, v_task, v_old.id));
      elsif v_constraint = 'eval_responses_student_dup_uniq' then
        return jsonb_build_object('saved', false, 'reason', 'duplicate_student');
      elsif v_constraint = 'eval_responses_supervisor_uniq' then
        return jsonb_build_object('saved', false, 'reason', 'duplicate_supervisor');
      elsif v_constraint = 'eval_responses_task_uniq' then
        return jsonb_build_object('saved', false, 'reason', 'task_id_conflict');
      end if;
      raise;
    when foreign_key_violation then
      get stacked diagnostics v_constraint = constraint_name;
      return jsonb_build_object('saved', false, 'reason', 'unknown_reference', 'constraint', v_constraint);
  end;

  if p_recompute then
    -- the scope the form left (a resend that moved it), then the scope it is in now
    if v_old.id is not null and (v_old.instructor_id, v_old.term_id) is distinct from (v_instructor, v_term) then
      perform public.recompute_eval(v_old.instructor_id, null, v_old.term_id, v_inst.id);
    end if;
    select * into v_res from public.recompute_eval(
      v_instructor,
      case when v_old.id is not null and v_old.subject_id is distinct from v_subject then null else v_subject end,
      v_term, v_inst.id);
    v_result := jsonb_build_object(
      'respondents', v_res.respondents, 'excluded_responses', v_res.excluded_responses,
      'overall_mean', v_res.overall_mean, 'overall_pct', v_res.overall_pct,
      'verbal_label', v_res.verbal_label);
  end if;

  return jsonb_build_object(
    'saved',      true,
    'response_id', v_id,
    'updated',    v_old.id is not null,
    'instrument', v_inst.code,
    'answered',   v_answered,
    'counted',    v_status = 'submitted' and v_answered >= v_inst.min_answered,
    'rating_pct', v_pct,
    'result',     v_result);
end;
$$;

-- CREATE OR REPLACE keeps the grants of 020; restated so a fresh database gets the same rights.
revoke all on function public.save_eval_response(text, text, text, text, jsonb, text, text, text, text,
                                                 text, text, text, text, text, boolean, jsonb)
  from public, anon;
grant execute on function public.save_eval_response(text, text, text, text, jsonb, text, text, text, text,
                                                    text, text, text, text, text, boolean, jsonb)
  to authenticated, service_role;


-- Preview -----------------------------------------------------------------------------------------
select column_name, data_type, is_nullable from information_schema.columns
 where table_schema = 'public' and table_name = 'sheet_import_jobs' and column_name = 'details';   -- 1 row: jsonb, YES
select pg_get_function_identity_arguments(oid) from pg_proc
 where pronamespace = 'public'::regnamespace and proname = 'save_eval_response';   -- ONE row, 16 args, ends with p_details jsonb
select position('already_imported' in prosrc) > 0 as has_already_imported from pg_proc
 where pronamespace = 'public'::regnamespace and proname = 'save_eval_response';   -- true

-- (b) old SAST scans: the stamp check already reads sast_all_raw_data_survey (same term) -> duplicate_stamp
select position('sast_stamp_duplicate_of' in prosrc) > 0 as eval_dup_checks_sast from pg_proc
 where pronamespace = 'public'::regnamespace and proname = 'eval_stamp_duplicate_of';   -- true
select position('sast_all_raw_data_survey' in prosrc) > 0 as sast_dup_reads_raw_table from pg_proc
 where pronamespace = 'public'::regnamespace and proname = 'sast_stamp_duplicate_of';   -- true
select tgname from pg_trigger where tgrelid = 'public.eval_responses'::regclass
   and tgname = 'eval_responses_stamp_guard';                                          -- 1 row

-- Dry runs on stored sheet forms (no row is written: each call returns before any insert/update).
-- A later import of the same row (same task_id, another job) -> already_imported / task_id
select r.id, public.save_eval_response(i.code, r.instructor_id::text, r.subject_id::text, r.term_id::text,
         '{}'::jsonb, null, 'sheet', r.stamp_no, 'sheet', r.evaluator_user_id::text,
         gen_random_uuid()::text, r.task_id, null, 'submitted', false, null) ->> 'reason' as same_row_again
  from public.eval_responses r join public.eval_instruments i on i.id = r.instrument_id
 where r.source = 'sheet' and r.status <> 'void' and r.task_id is not null
 order by r.id desc limit 3;                                                          -- already_imported
-- The same form from a COPY of the sheet (other task_id), same stamp + instructor + subject -> already_imported / stamp
select r.id, public.save_eval_response(i.code, r.instructor_id::text, r.subject_id::text, r.term_id::text,
         '{}'::jsonb, null, 'sheet', r.stamp_no, 'sheet', r.evaluator_user_id::text,
         gen_random_uuid()::text, 'preview-021:' || r.id, null, 'submitted', false, null) ->> 'reason' as sheet_copy
  from public.eval_responses r join public.eval_instruments i on i.id = r.instrument_id
 where r.source = 'sheet' and r.status <> 'void' and r.stamp_no is not null and r.evaluator_type = 'student'
 order by r.id desc limit 3;                                                          -- already_imported
-- The same stamp on ANOTHER instructor -> still duplicate_stamp (or missing_instructor_or_term if there is no other one)
select r.id, public.save_eval_response(i.code,
         (select x.instructor_id::text from public.eval_responses x where x.instructor_id <> r.instructor_id limit 1),
         r.subject_id::text, r.term_id::text, '{}'::jsonb, null, 'sheet', r.stamp_no, 'sheet', null,
         gen_random_uuid()::text, 'preview-021b:' || r.id, null, 'submitted', false, null) ->> 'reason' as other_instructor
  from public.eval_responses r join public.eval_instruments i on i.id = r.instrument_id
 where r.source = 'sheet' and r.status <> 'void' and r.stamp_no is not null and r.evaluator_type = 'student'
 order by r.id desc limit 3;                                                          -- duplicate_stamp
select count(*) as eval_responses_rows from public.eval_responses;                   -- unchanged by the dry runs

-- Nothing is kept until you switch this to commit. Check the preview first.
rollback;
-- commit;


-- =========================================================================================
-- Verification (by hand AFTER the commit; wrapped in begin ... rollback).
-- =========================================================================================
-- begin;
-- select public.save_eval_response('SET-15','<instructor uuid>','<subject uuid>','<term uuid>',
--   '{"1":5,"2":4,"3":5,"4":4,"5":3}'::jsonb, null, 'sheet', '990101', null, null,
--   '11111111-1111-4111-8111-111111111111', 'sheet:t:row2', null, 'submitted', true,
--   '{"student_id":"2021-0001"}'::jsonb);                                         -- saved:true
-- -- the same row in a later import (other job id): skipped
-- select public.save_eval_response('SET-15','<instructor uuid>','<subject uuid>','<term uuid>',
--   '{"1":5,"2":4,"3":5,"4":4,"5":3}'::jsonb, null, 'sheet', '990101', null, null,
--   '22222222-2222-4222-8222-222222222222', 'sheet:t:row2', null, 'submitted', true, null);
--   -- reason already_imported, match task_id
-- -- a copy of the sheet (other task_id), same stamp: skipped
-- select public.save_eval_response('SET-15','<instructor uuid>','<subject uuid>','<term uuid>',
--   '{"1":5}'::jsonb, null, 'sheet', '990101', null, null,
--   '22222222-2222-4222-8222-222222222222', 'sheet:copy:row2', null, 'submitted', true, null);
--   -- reason already_imported, match stamp
-- -- the same student twice in ONE import job: still duplicate_student
-- select public.save_eval_response('SET-15','<instructor uuid>','<subject uuid>','<term uuid>',
--   '{"1":5}'::jsonb, null, 'sheet', null, null, null,
--   '11111111-1111-4111-8111-111111111111', 'sheet:t:row3', null, 'submitted', true,
--   '{"student_id":"2021-0001"}'::jsonb);                                         -- reason duplicate_student
-- rollback;
