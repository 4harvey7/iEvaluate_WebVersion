-- ---------------------------------------------------------------------------
-- 020: paper-form fields of the new 15-item forms (SET-15 / SUP-15), from the Google Sheet import.
--
-- ADDITIVE, except that save_eval_response gets one new trailing parameter p_details (jsonb), which
-- changes its signature: the old 15-argument function is DROPPED and recreated with its grants. Only the
-- n8n "Save New Form Row" node calls it (checked by grep in the repo); re-import the n8n workflow
-- (perfect-2.1.json) together with this migration. Re-run safe (if not exists / create or replace).
--
-- New nullable columns on eval_responses
--   student_id      SET only. For the SAO office ONLY: used for the duplicate check, never shown to
--                   instructors or department heads (see Privacy).
--   section, program_level, program_year   both forms
--   submitted_date  the date written on the form (not created_at)
--   academic_rank, designation             SUP only, a snapshot of what is on the form
--
-- Duplicate rule: a second SET form with the same student id (btrim + lower) for the same instrument +
-- instructor + subject + term is NOT saved: {"saved":false,"reason":"duplicate_student"}. Void forms are
-- ignored, a blank student id is never checked, and re-importing the same row (same task_id) replaces
-- its own row (it is not a duplicate of itself). Partial unique index eval_responses_student_dup_uniq.
--
-- Privacy (verified against live 2026-10-30: policies of 019 only)
--   * eval_responses is readable by the SAO office (all) and by an evaluator for his own forms
--     (evaluator_user_id = auth.uid(), supervisor forms only). Instructors have NO policy: they read
--     eval_results and the view eval_response_comments, which does not select student_id.
--   * A department head reads only his own supervisor forms. A CHECK constraint now also guarantees
--     that a supervisor form can never hold a student id (student_id is NULL unless evaluator_type =
--     'student'), so no head can ever read or write one.
--
-- Like 016-019 it ends with ROLLBACK so a first run only previews. Check the output, then change the
-- last line to  commit;  and run it again.
-- ---------------------------------------------------------------------------
begin;

alter table public.eval_responses
  add column if not exists student_id      text,
  add column if not exists section         text,
  add column if not exists program_level   text,
  add column if not exists program_year    text,
  add column if not exists submitted_date  date,
  add column if not exists academic_rank   text,
  add column if not exists designation     text;

do $$
begin
  if not exists (select 1 from pg_constraint
                  where conrelid = 'public.eval_responses'::regclass
                    and conname = 'eval_responses_student_id_student_only') then
    alter table public.eval_responses
      add constraint eval_responses_student_id_student_only
      check (student_id is null or evaluator_type = 'student');
  end if;
end $$;

create unique index if not exists eval_responses_student_dup_uniq
  on public.eval_responses (lower(btrim(student_id)), instrument_id, instructor_id, subject_id, term_id)
  where evaluator_type = 'student' and status <> 'void' and nullif(btrim(student_id), '') is not null;

comment on table public.eval_responses is
  'One filled SET/SUP form. No student name is stored. student_id (SET only) is for the SAO office only: instructors and department heads never read student forms (see eval_response_comments, which does not expose it).';
comment on column public.eval_responses.student_id is
  'SET only. SAO office only (duplicate check). Never exposed to instructors or department heads.';

-- new signature: drop the 019 one (15 arguments) and recreate with p_details
drop function if exists public.save_eval_response(text, text, text, text, jsonb, text, text, text, text,
                                                  text, text, text, text, text, boolean);

-- 10. Save one form + its answers, then recompute ---------------------------------------------
-- n8n (sheet import; postgres/service_role, no JWT) and the web (signed-in user) call this.
-- Parameters are text like save_website_raw_row (015), cast here.
--   p_instrument_code  'SET-15' | 'SUP-15' (legacy SAST-20 is refused)
--   p_answers          {"1":5,"2":null,...} (keys "1".."15", "q1", "item1" ...) or [5,4,null,...]
--                      value 1..5 (number or numeric text); null / "" = blank. Anything else (0,
--                      6, "x") -> {"saved":false,"reason":"invalid_rating"}. Missing item = blank.
--   p_source           sheet | web | scan
--   p_stamp_no         3..12 digits, anything else = no stamp (015 rule)
--   p_evaluator_user_id supervisor forms only (ignored for student forms: never stored)
--   p_task_id          idempotency key: a resend with the same task_id replaces that form
--   p_status           'submitted' (default) | 'draft'
--   p_details          020: {student_id (SET only), section, program_level, program_year, submitted_date,
--                      academic_rank, designation (SUP only)}; NULL = leave the stored details as they are
-- A supervisor form for the same (evaluator, instructor, subject, term) is replaced, not duplicated.
-- Signed-in caller: SAO office may save anything; a department head only SUPERVISOR forms as the
-- evaluator (forced to auth.uid(), source 'web') for faculty of his department, never himself.
-- Answers jsonb:
--   {"saved":true, "response_id", "updated", "instrument", "answered", "counted", "rating_pct", "result"}
--   {"saved":false, "reason": unknown_instrument | legacy_instrument | invalid_id | missing_instructor_or_term
--     | missing_subject | missing_evaluator | self_evaluation | invalid_source | invalid_status
--     | instrument_not_for_term | invalid_answers | invalid_item | duplicate_item | invalid_rating
--     | task_id_conflict | duplicate_student | duplicate_stamp (+ duplicate_of DUP) | duplicate_supervisor | unknown_reference}
create or replace function public.save_eval_response(
  p_instrument_code    text,
  p_instructor_id      text,
  p_subject_id         text,
  p_term_id            text,
  p_answers            jsonb,
  p_comments           text,
  p_source             text,
  p_stamp_no           text    default null,
  p_stamp_source       text    default null,
  p_evaluator_user_id  text    default null,
  p_import_job_id      text    default null,
  p_task_id            text    default null,
  p_created_by         text    default null,
  p_status             text    default 'submitted',
  p_recompute          boolean default true,
  p_details            jsonb   default null)
returns jsonb
language plpgsql
volatile
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
  end if;
  if v_old.id is null and v_inst.evaluator_type = 'supervisor' then
    select * into v_old from public.eval_responses x
     where x.evaluator_type = 'supervisor' and x.status <> 'void'
       and x.evaluator_user_id = v_evaluator and x.instructor_id = v_instructor
       and x.subject_id is not distinct from v_subject and x.term_id = v_term;
  end if;
  if v_old.id is not null and v_uid is not null and not v_is_sao
     and v_old.evaluator_user_id is distinct from v_uid then
    raise exception 'save_eval_response: not your form' using errcode = 'insufficient_privilege';
  end if;

  -- stamp already used in this term (SAST or eval)?
  if v_stamp is not null then
    perform pg_advisory_xact_lock(hashtext('ievaluate:stamp:' || v_term::text || ':' || v_stamp));
    v_dup := public.eval_stamp_duplicate_of(v_term::text, v_stamp, v_task, v_old.id);
    if v_dup is not null then
      return jsonb_build_object('saved', false, 'reason', 'duplicate_stamp', 'duplicate_of', v_dup);
    end if;
  end if;

  -- same student, instrument, instructor, subject, term (not the form being replaced)? The unique index backs this up.
  if v_student is not null then
    if exists (select 1 from public.eval_responses x
                where x.evaluator_type = 'student' and x.status <> 'void'
                  and lower(btrim(x.student_id)) = lower(v_student)
                  and x.instrument_id = v_inst.id and x.instructor_id = v_instructor
                  and x.subject_id = v_subject and x.term_id = v_term
                  and (v_old.id is null or x.id <> v_old.id)) then
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


revoke all on function public.save_eval_response(text, text, text, text, jsonb, text, text, text, text,
                                                 text, text, text, text, text, boolean, jsonb)
  from public, anon;
grant execute on function public.save_eval_response(text, text, text, text, jsonb, text, text, text, text,
                                                    text, text, text, text, text, boolean, jsonb)
  to authenticated, service_role;


-- Preview -----------------------------------------------------------------------------------------
select column_name, data_type from information_schema.columns
 where table_schema = 'public' and table_name = 'eval_responses'
   and column_name in ('student_id','section','program_level','program_year','submitted_date','academic_rank','designation')
 order by column_name;                                                         -- 7 rows
select indexname from pg_indexes where schemaname = 'public' and indexname = 'eval_responses_student_dup_uniq';  -- 1 row
select pg_get_function_identity_arguments(oid) from pg_proc
 where pronamespace = 'public'::regnamespace and proname = 'save_eval_response';   -- ONE row, ends with p_details jsonb
select column_name from information_schema.columns
 where table_schema = 'public' and table_name = 'eval_response_comments' and column_name = 'student_id';   -- 0 rows
select policyname, cmd from pg_policies where schemaname = 'public' and tablename = 'eval_responses' order by 1;   -- the 4 policies of 019, none for instructors

-- Nothing is kept until you switch this to commit. Check the preview first.
rollback;
-- commit;


-- =========================================================================================
-- Verification (by hand AFTER the commit; wrapped in begin ... rollback).
-- =========================================================================================
-- begin;
-- select public.save_eval_response('SET-15','<instructor uuid>','<subject uuid>','<term uuid>',
--   '{"1":5,"2":4,"3":5,"4":4,"5":3}'::jsonb, null, 'sheet', null, null, null, null, 'sheet:t:row2', null,
--   'submitted', true, '{"student_id":"2021-0001","section":"A1","program_level":"Undergraduate","program_year":"3rd","submitted_date":"2026-10-30"}'::jsonb);
--   -- expect saved:true
-- select public.save_eval_response('SET-15','<instructor uuid>','<subject uuid>','<term uuid>',
--   '{"1":5,"2":4,"3":5,"4":4,"5":3}'::jsonb, null, 'sheet', null, null, null, null, 'sheet:t:row3', null,
--   'submitted', true, '{"student_id":" 2021-0001 "}'::jsonb);                 -- expect reason duplicate_student
-- select public.save_eval_response('SET-15','<instructor uuid>','<subject uuid>','<term uuid>',
--   '{"1":5,"2":4,"3":5,"4":4,"5":3}'::jsonb, null, 'sheet', null, null, null, null, 'sheet:t:row2', null,
--   'submitted', true, '{"student_id":"2021-0001"}'::jsonb);                   -- same task_id: saved, updated:true
-- rollback;
