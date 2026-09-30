-- 006: OCR correction log (read-only view) for measuring Gemini's header OCR accuracy.
--
-- Review before applying. Not auto-applied to the live project, not applied by this agent.
-- Run it in the Supabase SQL Editor. Purely additive and safe to re-run: it only
-- (re)creates a VIEW, no table is altered, no RLS policy or Realtime publication is touched.
-- A view carries no RLS of its own -- the backend already reads every table here with the
-- service role, which bypasses RLS the same way it does everywhere else in this app.
--
-- What it adds: `ocr_correction_log`, one row per RESOLVED website scan (failed_scan_queue,
-- task_id like 'web:%', status in ('validated', 'discarded')), joining:
--   - Gemini's raw header reading, stored in partial_data by n8n's Decide step:
--       partial_data->>'ocr_instructor', partial_data->>'ocr_subject', partial_data->>'student_id'
--     (plus the optional per-field confidence, when the website Gemini prompt returned one)
--   - the staff's final, approved value, read from whichever of these two exists:
--       review_correction   (website review page approve/reroute; migrations/005) --
--                            instructor_id/instructor_name, subject_id/subject_code, student_id
--       validated_data       (set by the pre-existing 'Mark Queue Validated1' n8n node when a
--                            page is corrected from the Flutter app, or for rows resolved before
--                            migrations/005 existed) -- tried under a few plausible key
--                            spellings, since its exact shape lives in n8n, not in this repo;
--                            check a real row (select validated_data from failed_scan_queue
--                            where validated_data is not null limit 5) and extend the coalesce()
--                            lists below if the keys differ
-- and resolves the final instructor/subject to their CURRENT user_info/subjects names (in case
-- only an id was stored), so a later rename doesn't make an old row look wrong.
--
-- Each *_correct column is a per-field boolean: true = Gemini's reading matches the
-- staff-approved value (case/whitespace-insensitive; subject codes and student ids compare
-- letters+digits only), false = it does not, NULL = not counted (either side was blank/
-- unreadable/never resolved) -- ocr_eval.py skips NULLs when computing accuracy.
create or replace view public.ocr_correction_log as
with resolved as (
  select
    q.id, q.task_id, q.batch_id, q.created_at, q.updated_at, q.status, q.review_reasons,
    nullif(btrim(q.partial_data ->> 'ocr_instructor'), '')   as ocr_instructor,
    nullif(btrim(q.partial_data ->> 'ocr_subject'), '')      as ocr_subject,
    nullif(btrim(q.partial_data ->> 'student_id'), '')       as ocr_student_id,
    nullif(q.partial_data ->> 'instructor_confidence', '')   as instructor_confidence,
    nullif(q.partial_data ->> 'subject_confidence', '')      as subject_confidence,
    nullif(q.partial_data ->> 'student_id_confidence', '')   as student_id_confidence,
    -- final instructor/subject id: review_correction first (website review), else validated_data
    nullif(coalesce(q.review_correction ->> 'instructor_id', q.validated_data ->> 'instructor_id'), '')::uuid
      as final_instructor_id,
    nullif(coalesce(q.review_correction ->> 'subject_id', q.validated_data ->> 'subject_id'), '')::uuid
      as final_subject_id,
    -- text fallback, used only when the id above did not resolve to a current row below
    nullif(coalesce(q.review_correction ->> 'instructor_name', q.validated_data ->> 'instructor_name',
                    q.validated_data ->> 'instructor'), '')                                 as final_instructor_name_text,
    nullif(coalesce(q.review_correction ->> 'subject_code', q.validated_data ->> 'subject_code',
                    q.validated_data ->> 'subject'), '')                                    as final_subject_code_text,
    nullif(btrim(coalesce(q.review_correction ->> 'student_id', q.validated_data ->> 'student_id')), '')
      as final_student_id
  from public.failed_scan_queue q
  where q.task_id like 'web:%'
    and q.status in ('validated', 'discarded')
)
select
  r.id as failed_scan_queue_id, r.task_id, r.batch_id, r.created_at, r.updated_at, r.status, r.review_reasons,
  r.ocr_instructor, r.ocr_subject, r.ocr_student_id,
  r.instructor_confidence, r.subject_confidence, r.student_id_confidence,
  r.final_instructor_id,
  coalesce(nullif(btrim(concat_ws(' ', ui.first_name, ui.last_name)), ''), r.final_instructor_name_text)
    as final_instructor_name,
  r.final_subject_id,
  coalesce(s.subject_code, r.final_subject_code_text) as final_subject_code,
  r.final_student_id,
  case
    when r.ocr_instructor is null
      or coalesce(nullif(btrim(concat_ws(' ', ui.first_name, ui.last_name)), ''), r.final_instructor_name_text) is null
    then null
    else lower(r.ocr_instructor) = lower(coalesce(nullif(btrim(concat_ws(' ', ui.first_name, ui.last_name)), ''),
                                                   r.final_instructor_name_text))
  end as instructor_correct,
  case
    when r.ocr_subject is null or coalesce(s.subject_code, r.final_subject_code_text) is null then null
    else upper(regexp_replace(r.ocr_subject, '[^A-Za-z0-9]', '', 'g'))
         = upper(regexp_replace(coalesce(s.subject_code, r.final_subject_code_text), '[^A-Za-z0-9]', '', 'g'))
  end as subject_correct,
  case
    when r.ocr_student_id is null or r.final_student_id is null then null
    else regexp_replace(r.ocr_student_id, '[^0-9A-Za-z]', '', 'g') = regexp_replace(r.final_student_id, '[^0-9A-Za-z]', '', 'g')
  end as student_id_correct
from resolved r
left join public.user_info ui on ui.id = r.final_instructor_id
left join public.subjects s on s.id = r.final_subject_id;

comment on view public.ocr_correction_log is
  'Gemini header OCR reading (failed_scan_queue.partial_data) vs the staff-approved final value, '
  'one row per resolved website scan. Read-only; see migrations/006_ocr_correction_log.sql and '
  'n8n/tools/ocr_eval.py.';

-- Check afterwards (read-only):
-- select instructor_correct, subject_correct, student_id_correct from public.ocr_correction_log limit 20;
