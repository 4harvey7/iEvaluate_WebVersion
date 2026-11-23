-- ---------------------------------------------------------------------------
-- 022: the student ID is optional and is never a duplicate key for a scanned form (SAO rule 2026-11-23).
--
-- A scanned SAST form is "already scanned" only by its numbering-machine stamp in the term
-- (sast_raw_term_stamp_uniq, migration 015: unchanged). Until now the raw table was also unique on
-- (student_id, "instructor_ID", subject_id, term_id) (+ the 021 hash twin), so a second form with the same
-- student ID was refused (website: duplicate_student) or, through the n8n shared upsert (phone scans and
-- every staff correction), silently written OVER the first form.
--
--   sast_all_raw_data_survey.dedupe_key    't:' || task_id for a scanned form, '' without a task_id
--                                          (Google Sheet rows); always set by trigger sast_raw_dedupe_key
--   unique (subject_id, term_id, "instructor_ID", student_id, dedupe_key)   replaces
--          unique_student_instructor_subject_term: a scanned form only ever upserts its OWN row (a resend
--          or a re-correction of the same task); sheet rows keep the old per-student key (a re-imported
--          sheet still replaces its rows); a blank student ID (NULL) never collides, as before
--   sast_raw_student_hash_uniq            same change for the keyed-hash twin (021)
--   save_website_raw_row(...)             ON CONFLICT on the new key; a refused row is now only ever a
--                                          duplicate stamp (reason 'not_saved' otherwise, never per student)
--
-- Checked 2026-11-23 (read-only): 2556 raw rows, 92 with a task_id, 0 blank task_ids, 0 task_ids on two
-- rows; triggers on the table: only sast_raw_student_id_hash (fires on student_id only, so the backfill
-- below does not touch hashes); unique_student_instructor_subject_term is a UNIQUE constraint. The new
-- keys only ADD a column to the old ones, so existing data cannot violate them. Mobile migrations
-- 036-040 do not touch these objects (038 only redefines sast_raw_set_student_id_hash; kept).
--
-- ORDER (no gap): apply this, then IMMEDIATELY re-import n8n/perfect-2.1.json (its shared upsert node
-- matches on dedupe_key; the OLD live workflow's upsert has no matching unique key after this and fails),
-- then redeploy the backend. Not auto-applied. One transaction. Safe to re-run.
--
-- ROLLBACK (only while every stored (student_id, instructor, subject, term) is still unique):
--   begin;
--   drop index if exists public.sast_raw_student_key_uniq;
--   alter table public.sast_all_raw_data_survey add constraint unique_student_instructor_subject_term
--     unique (student_id, "instructor_ID", subject_id, term_id);
--   drop index if exists public.sast_raw_student_hash_uniq;
--   create unique index sast_raw_student_hash_uniq on public.sast_all_raw_data_survey
--     (student_id_hash, "instructor_ID", subject_id, term_id) where student_id_hash is not null;
--   -- then re-run section 4 of 015_stamp_no.sql (old save_website_raw_row) and re-import the old workflow
--   commit;
-- ---------------------------------------------------------------------------
begin;


-- 1. The key column + trigger (every writer: n8n upsert, save_website_raw_row, anything else) ----------
alter table public.sast_all_raw_data_survey add column if not exists dedupe_key text not null default '';

create or replace function public.sast_raw_set_dedupe_key()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  -- always recomputed from task_id, so it can't be set to anything else by hand
  new.dedupe_key := case when nullif(btrim(coalesce(new.task_id, '')), '') is null then ''
                         else 't:' || btrim(new.task_id) end;
  return new;
end;
$$;

revoke all on function public.sast_raw_set_dedupe_key() from public, anon, authenticated;

drop trigger if exists sast_raw_dedupe_key on public.sast_all_raw_data_survey;
create trigger sast_raw_dedupe_key
  before insert or update on public.sast_all_raw_data_survey
  for each row execute function public.sast_raw_set_dedupe_key();

-- backfill (the trigger computes it)
update public.sast_all_raw_data_survey
   set task_id = task_id
 where nullif(btrim(coalesce(task_id, '')), '') is not null
   and dedupe_key is distinct from 't:' || btrim(task_id);

-- 021 section 6: anon/authenticated read raw rows column by column; the new column is harmless
grant select (dedupe_key) on public.sast_all_raw_data_survey to anon, authenticated;


-- 2. The student keys: a scanned form never collides with another form by student ID ---------------
alter table public.sast_all_raw_data_survey drop constraint if exists unique_student_instructor_subject_term;
drop index if exists public.unique_student_instructor_subject_term;
create unique index if not exists sast_raw_student_key_uniq
  on public.sast_all_raw_data_survey (subject_id, term_id, "instructor_ID", student_id, dedupe_key);

drop index if exists public.sast_raw_student_hash_uniq;
create unique index sast_raw_student_hash_uniq
  on public.sast_all_raw_data_survey (student_id_hash, "instructor_ID", subject_id, term_id, dedupe_key)
  where student_id_hash is not null;


-- 3. Website Raw Upsert (015 section 4, same contract) on the new key --------------------------------
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
    -- dedupe_key is set by trigger sast_raw_dedupe_key from task_id
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
    on conflict (subject_id, term_id, "instructor_ID", student_id, dedupe_key) do update set
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
    -- another form with this stamp is already stored for the term (also when two pages race)
    return jsonb_build_object('saved', false, 'reason', 'duplicate_stamp',
                              'duplicate_of', public.sast_stamp_duplicate_of(v_row.term_id::text, v_stamp, p_task_id));
  end;

  if v_saved then
    return jsonb_build_object('saved', true);
  end if;
  -- only reachable without a task_id (the website always sends one); never a per-student refusal
  return jsonb_build_object('saved', false, 'reason', 'not_saved');
end;
$$;

revoke all on function public.save_website_raw_row(text, text, text, text, text, text, text, text, text, jsonb, text, text)
  from public, anon, authenticated;
grant execute on function public.save_website_raw_row(text, text, text, text, text, text, text, text, text, jsonb, text, text)
  to service_role;

commit;
