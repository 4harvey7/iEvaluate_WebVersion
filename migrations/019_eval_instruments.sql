-- ---------------------------------------------------------------------------
-- 019: evaluation instruments registry + the new 15-item forms (SET-15 student, SUP-15 supervisor).
--
-- ADDITIVE ONLY. Nothing that exists is renamed, altered or dropped: sast_all_raw_data_survey, the
-- SAST result tables, recompute_sast, sast_answers, sast_stamp_duplicate_of and save_website_raw_row
-- are untouched, so the old code can still be redeployed against this database. The one write into
-- an existing table is the same one recompute_sast does: an instructor_subjects link
-- (insert ... on conflict do nothing).
--
-- What is in this file
--   Registry (read-only for signed-in users; seeded here, written by service_role only)
--     eval_instruments          one row per form: code, evaluator_type student|supervisor, title,
--                               item_count, scale (jsonb), min_answered, verbal_bands (jsonb), is_legacy
--     eval_instrument_sections  A / B / C (SAST-20: M / P)
--     eval_instrument_items     item_no, section_code, text, verification_hint (SUP-15 only),
--                               legacy_key (SAST-20 only: m1..m10, p1..p10)
--     Seeds: SAST-20 (legacy, reference only: its data stays in sast_all_raw_data_survey and is still
--            scored by recompute_sast; items 1..10 = m1..m10, 11..20 = p1..p10, texts copied from the
--            Flutter pdf_service.dart), SET-15 (student) and SUP-15 (supervisor).
--   eval_term_instruments       term -> instrument, one per evaluator_type per term (admin setting,
--                               SAO office writes). Nothing is seeded: the first SET term is not known.
--   eval_responses              one row per filled form. NO student name or student id (privacy).
--                               evaluator_user_id is set for supervisor forms only (CHECK).
--   eval_answers                one row per item per form; rating NULL (blank) or 1..5, never 0.
--   eval_results                the scores, LONG format (see "Scoring" below).
--   eval_response_comments      view: comments without evaluator identity, for the instructor.
--   Functions
--     eval_is_sao_office()                      SAO_ADMIN / SAO_STAFF (same rule as 018)
--     eval_dept_head_reads(instructor, term)    caller is a department head of the faculty's dept
--     eval_dept_head_can_evaluate(instr, term)  same, and not the caller himself
--     eval_scale_max(scale) / eval_verbal_label(bands, pct)
--     eval_stamp_duplicate_of(term, stamp, task_id, exclude_response_id)
--                                              DUP of a stored form with this stamp in this term, in
--                                              sast_all_raw_data_survey (shape of 015 + "source":"sast")
--                                              or eval_responses ("source":"eval"); NULL if free.
--                                              sast_stamp_duplicate_of (015) is unchanged.
--     recompute_eval(instructor, subject?, term, instrument)   rebuilds eval_results
--     save_eval_response(...)                   n8n sheet import (and the web) -> one form + answers,
--                                              then recompute_eval
--
-- Stamp numbers (one numbering machine for every paper form)
--   Same format as 015: 3..12 digits, text, leading zeros kept. Unique per term across BOTH tables:
--     * eval vs eval: partial UNIQUE index eval_responses_term_stamp_uniq (term_id, stamp_no).
--     * eval vs SAST: BEFORE INSERT/UPDATE trigger on eval_responses raises unique_violation with
--       constraint name 'eval_responses_stamp_in_sast' when the stamp is already in
--       sast_all_raw_data_survey for the term. It takes an advisory lock on (term, stamp).
--     * SAST vs eval (a NEW SAST row whose stamp is already on an eval form) is NOT blocked by the
--       database: that would need a trigger on the old table (not additive). n8n can call
--       eval_stamp_duplicate_of before saving a SAST row, which checks both tables.
--
-- Scoring (recompute_eval; 013 rounding convention, 014 min-answered rule)
--   * A form counts only when status = 'submitted' and it has >= min_answered (5) non-NULL answers.
--     A form with fewer is kept but excluded (excluded_responses on the overall row).
--   * item mean     = ROUND(AVG(non-NULL answers), 2); nobody answered -> NULL
--   * section mean  = ROUND(AVG of the ROUNDED item means of the section, 2)
--     section %     = ROUND(AVG of the ROUNDED item means / 5 * 100, 2)
--   * overall mean  = ROUND(AVG of all ROUNDED item means, 2)
--     overall %     = ROUND(AVG of all ROUNDED item means / 5 * 100, 2)   (null-aware: NULL means skipped)
--   * per form      : eval_responses.rating_pct = answered total / (5 * answered) * 100, ROUND 2
--                     (+ answered_count, answered_total)
--   * verbal label  on the 2-decimal %: >= 84 Outstanding, >= 68 Very Satisfactory, >= 52 Satisfactory,
--                   >= 36 Fair, else Unsatisfactory (labels exactly as sast_rating.dart; the old mean
--                   bands 4.20 / 3.40 / 2.60 / 1.80 times 20). Stored per instrument in verbal_bands.
--   * "5" is the top of the instrument's scale (eval_scale_max(scale)).
--   * SET and SUP results never mix: every eval_results row belongs to ONE instrument.
--
-- eval_results layout (long format), one set of rows per scope:
--     scope = (instrument, instructor, term, subject_id)  where subject_id NULL = all subjects pooled
--     level 'overall'  : 1 row  (respondents, excluded_responses, answered_count, answer_total, mean, pct, verbal_label)
--     level 'section'  : 1 row per section_code
--     level 'item'     : 1 row per item_no (section_code filled; answered_count = answers to that item)
--   respondents = counted forms in the scope (same on every row of the scope).
--   Supervisor forms without a subject only appear in the pooled scope.
--
-- Access (RLS)
--   * SAO_ADMIN / SAO_STAFF: read + write eval_responses, eval_answers, eval_term_instruments; read
--     eval_results (written only by recompute_eval, never by hand).
--   * Instructor: reads own eval_results and own comments through eval_response_comments (no
--     evaluator, no stamp, no creator). Cannot read eval_responses / eval_answers.
--   * DEPARTMENT_HEAD: reads eval_results + comments of faculty whose teaching department for the
--     term (teaching_department_id) = get_my_dept_id(); writes SUPERVISOR forms only, only as the
--     evaluator, only for such faculty, never for himself; reads only the forms he wrote.
--   * Registry tables: readable by every signed-in user.
--   * Direct table writes do NOT recompute; the web should call save_eval_response (SECURITY DEFINER,
--     checks the same rules for a signed-in caller; n8n / service_role pass without a JWT).
--
-- Checked 2026-10-30 (read-only, live Supabase): PostgreSQL 17.6; no relation or function named
-- eval*; roles = SAO_ADMIN(1), SAO_STAFF(2), DEPARTMENT_HEAD(3), PART-TIME(4), FULL-TIME(5);
-- academic_terms.id, subjects.id, user_info.id are uuid; sast_all_raw_data_survey.term_id uuid,
-- stamp_no text, index sast_raw_term_stamp_uniq present (015 applied). sheet_import_jobs (018) is
-- NOT in the live schema yet, so import_job_id has no foreign key.
--
-- Idempotent (if not exists / on conflict do nothing / create or replace / drop policy if exists).
-- Not auto-applied: run it in the Supabase SQL Editor. Like 016-018 it ends with ROLLBACK so a first
-- run only previews. Check the output, then change the last line to  commit;  and run it again.
-- ---------------------------------------------------------------------------
begin;


-- 1. Registry -------------------------------------------------------------------------------
create table if not exists public.eval_instruments (
  id              bigint generated by default as identity primary key,
  code            text     not null unique,
  evaluator_type  text     not null check (evaluator_type in ('student', 'supervisor')),
  title           text     not null,
  item_count      smallint not null check (item_count between 1 and 100),
  scale           jsonb    not null,   -- [{"value":5,"label":"..."}, ...]
  min_answered    smallint not null default 5,
  verbal_bands    jsonb    not null,   -- [{"min_pct":84,"label":"Outstanding","code":"O"}, ...]
  is_legacy       boolean  not null default false,
  description     text,
  created_at      timestamptz not null default now(),
  constraint eval_instruments_id_type_uniq unique (id, evaluator_type),   -- target of composite FKs
  constraint eval_instruments_min_answered_check check (min_answered between 0 and item_count)
);

create table if not exists public.eval_instrument_sections (
  instrument_id  bigint   not null references public.eval_instruments (id) on delete cascade,
  section_code   text     not null,
  title          text     not null,
  sort_order     smallint not null,
  primary key (instrument_id, section_code)
);

create table if not exists public.eval_instrument_items (
  instrument_id      bigint   not null references public.eval_instruments (id) on delete cascade,
  item_no            smallint not null check (item_no >= 1),
  section_code       text     not null,
  text               text     not null,
  verification_hint  text,     -- SUP-15: means of verification, ';'-separated
  legacy_key         text,     -- SAST-20: the raw column (m1..m10, p1..p10)
  primary key (instrument_id, item_no),
  foreign key (instrument_id, section_code)
    references public.eval_instrument_sections (instrument_id, section_code)
);

comment on table public.eval_instruments is
  'Evaluation forms. SAST-20 is legacy/reference only (its data stays in sast_all_raw_data_survey).';
comment on column public.eval_instruments.verbal_bands is
  'Bands on the 2-decimal rating %, highest min_pct first wins. Labels as sast_rating.dart.';


-- 2. Seeds ----------------------------------------------------------------------------------
insert into public.eval_instruments
  (code, evaluator_type, title, item_count, scale, min_answered, verbal_bands, is_legacy, description)
values
  ('SAST-20', 'student', 'SAST (legacy 20-item student form)', 20,
   '[{"value":5,"label":null},{"value":4,"label":null},{"value":3,"label":null},
     {"value":2,"label":null},{"value":1,"label":null}]'::jsonb,
   5,
   '[{"min_pct":84,"label":"Outstanding","code":"O"},{"min_pct":68,"label":"Very Satisfactory","code":"VS"},
     {"min_pct":52,"label":"Satisfactory","code":"S"},{"min_pct":36,"label":"Fair","code":"F"},
     {"min_pct":0,"label":"Unsatisfactory","code":"US"}]'::jsonb,
   true,
   'Reference only. Data in sast_all_raw_data_survey, scored by recompute_sast (means, not %). Items 1..10 = m1..m10, 11..20 = p1..p10.'),
  ('SET-15', 'student', 'Student Evaluation of Teachers (SET)', 15,
   '[{"value":5,"label":"Always manifested"},{"value":4,"label":"Often manifested"},
     {"value":3,"label":"Sometimes manifested"},{"value":2,"label":"Seldom manifested"},
     {"value":1,"label":"Never/Rarely manifested"}]'::jsonb,
   5,
   '[{"min_pct":84,"label":"Outstanding","code":"O"},{"min_pct":68,"label":"Very Satisfactory","code":"VS"},
     {"min_pct":52,"label":"Satisfactory","code":"S"},{"min_pct":36,"label":"Fair","code":"F"},
     {"min_pct":0,"label":"Unsatisfactory","code":"US"}]'::jsonb,
   false, '15-item student evaluation.'),
  ('SUP-15', 'supervisor', 'Supervisor''s Evaluation of Faculty', 15,
   '[{"value":5,"label":"Always manifested"},{"value":4,"label":"Often manifested"},
     {"value":3,"label":"Sometimes manifested"},{"value":2,"label":"Seldom manifested"},
     {"value":1,"label":"Never/Rarely manifested"}]'::jsonb,
   5,
   '[{"min_pct":84,"label":"Outstanding","code":"O"},{"min_pct":68,"label":"Very Satisfactory","code":"VS"},
     {"min_pct":52,"label":"Satisfactory","code":"S"},{"min_pct":36,"label":"Fair","code":"F"},
     {"min_pct":0,"label":"Unsatisfactory","code":"US"}]'::jsonb,
   false, '15-item supervisor evaluation; verification_hint lists the means of verification.')
on conflict (code) do nothing;

insert into public.eval_instrument_sections (instrument_id, section_code, title, sort_order)
select i.id, s.section_code, s.title, s.sort_order
from public.eval_instruments i
cross join (values
  ('A', 'Management of Teaching and Learning',          1),
  ('B', 'Content Knowledge, Pedagogy and Technology',   2),
  ('C', 'Commitment and Transparency',                  3)
) as s(section_code, title, sort_order)
where i.code in ('SET-15', 'SUP-15')
on conflict do nothing;

insert into public.eval_instrument_sections (instrument_id, section_code, title, sort_order)
select i.id, s.section_code, s.title, s.sort_order
from public.eval_instruments i
cross join (values ('M', 'Management', 1), ('P', 'Performance', 2)) as s(section_code, title, sort_order)
where i.code = 'SAST-20'
on conflict do nothing;

-- SET-15 and SUP-15: same items except SUP 2, 4, 9; hints for SUP only
insert into public.eval_instrument_items (instrument_id, item_no, section_code, text, verification_hint)
select i.id, t.item_no, t.section_code,
       case when i.code = 'SUP-15' then coalesce(t.sup_text, t.set_text) else t.set_text end,
       case when i.code = 'SUP-15' then t.hint end
from public.eval_instruments i
cross join (values
  (1::smallint, 'A', 'Comes to class on time.', null,
   'Daily time record; Faculty schedule and timetable; Informal interview with students'),
  (2::smallint, 'A', 'Explains learning outcomes, expectations, grading system, and various requirements of the subject/course.',
   'Submits updated syllabus, grade sheets, and other required reports on time.',
   'Documents submission log; Submission Receipts or Acknowledgment Emails'),
  (3::smallint, 'A', 'Maximizes the allocated time/learning hours effectively.', null,
   'Class Schedules & Timetables; LMS Logs; Informal interview with students'),
  (4::smallint, 'A', 'Facilitates students to think critically and creatively by providing appropriate learning activities.',
   'Provide appropriate learning activities that facilitate critical thinking and creativity of students.',
   'Course syllabus; Learning Plan; Classroom Observation; Informal interview with students; LMS Logs'),
  (5::smallint, 'A', 'Guides students to learn on their own, reflect on new ideas and experiences, and make decisions in accomplishing given tasks.', null,
   'Course Syllabus; Learning Plan; Student Work Samples; Classroom Observation; LMS Logs; Informal interview with students; Faculty Consultation Log'),
  (6::smallint, 'A', 'Communicates constructive feedback to students for their academic growth.', null,
   'Graded Student Work with Feedback; Faculty Consultation Log; Informal interview with students; Emails or Official correspondence; LMS Logs'),
  (7::smallint, 'B', 'Demonstrates extensive and broad knowledge of the subject/course.', null,
   'Course Syllabus; Learning Plan; IMs developed by the faculty; Informal interview with students; Mentorship or Thesis/Dissertation Advisory records'),
  (8::smallint, 'B', 'Simplifies complex ideas in the lesson for ease of understanding.', null,
   'Learning Plan; Course Syllabus; Classroom Observation; Informal interview with students; Lecture notes and presentations; LMS Logs'),
  (9::smallint, 'B', 'Relates the subject matter to contemporary issues and developments in the discipline and/or daily life activities.',
   'Integrates contemporary issues and developments in the discipline and/or daily life activities in the syllabus.',
   'Course Syllabus; Learning Plan; Classroom Observation; Informal interview with students; LMS Logs; IMs developed by the faculty; Participation in Conferences, Webinars, and Training'),
  (10::smallint, 'B', 'Promotes active learning and student engagement by using appropriate teaching and learning resources including ICT tools and platforms.', null,
   'Course Syllabus; Learning Plan; Classroom Observation; Informal interview with students; LMS Logs; Multimedia Lecture Materials; Student Work Samples'),
  (11::smallint, 'B', 'Uses appropriate assessments (projects, exams, quizzes, assignments, etc.) aligned with the learning outcomes.', null,
   'Course Syllabus; Learning Plan; Informal interview with students; Assessment tools and rubrics; Exam and Quiz Samples; Graded Student Work Samples; LMS records'),
  (12::smallint, 'C', 'Recognizes and values the unique diversity and individual differences among students.', null,
   'Course Syllabus; Learning Plan; IMs developed by the faculty; Classroom Observation; Informal interview with students'),
  (13::smallint, 'C', 'Assists students with their learning challenges during consultation hours.', null,
   'Course Syllabus; Faculty Consultation Log; Advisory Records; LMS Logs; Emails or Official Correspondence'),
  (14::smallint, 'C', 'Provides immediate feedback on student outputs and performance.', null,
   'Graded Student Work Samples; Assessment tools and rubrics; Informal interview with students; LMS Logs; Emails or Official Correspondence; Faculty Consultation Log; Advising Reports'),
  (15::smallint, 'C', 'Provides transparent and clear criteria in rating student''s performance.', null,
   'Course Syllabus; Assessment Tools and Rubrics; Informal interview with students; LMS Records; Grade Sheets and Records')
) as t(item_no, section_code, set_text, sup_text, hint)
where i.code in ('SET-15', 'SUP-15')
on conflict do nothing;

-- SAST-20 (texts exactly as ievaluateapp_final pdf_service.dart _managementCriteria / _performanceCriteria)
insert into public.eval_instrument_items (instrument_id, item_no, section_code, text, legacy_key)
select i.id, t.item_no, t.section_code, t.text, t.legacy_key
from public.eval_instruments i
cross join (values
  (1::smallint,  'M', 'Gives reasonable course / subject assignments',                               'm1'),
  (2::smallint,  'M', 'Earns appreciation and kind attention from the students',                      'm2'),
  (3::smallint,  'M', 'Gives orientation about the subject and how the students are evaluated',       'm3'),
  (4::smallint,  'M', 'Gives tests and/or projects which are within the objectives of the course',    'm4'),
  (5::smallint,  'M', 'Shows deep interest and concern in assisting the students',                    'm5'),
  (6::smallint,  'M', 'Manifests sympathetic insight into students'' feelings',                       'm6'),
  (7::smallint,  'M', 'Checks and records test papers/term papers',                                   'm7'),
  (8::smallint,  'M', 'Is on time and regular in meeting the class',                                  'm8'),
  (9::smallint,  'M', 'Apportions fair subject/course assignments',                                   'm9'),
  (10::smallint, 'M', 'Sustains the attention of the class for the whole period',                     'm10'),
  (11::smallint, 'P', 'Presents lesson clearly, methodically, and substantially',                     'p1'),
  (12::smallint, 'P', 'Motivates the students to learn',                                              'p2'),
  (13::smallint, 'P', 'Facilitates learning with the application of appropriate educational methods and techniques', 'p3'),
  (14::smallint, 'P', 'Shows mastery of the lesson',                                                  'p4'),
  (15::smallint, 'P', 'Is ready for the class',                                                       'p5'),
  (16::smallint, 'P', 'Inspires students'' self-reliance in their quest for knowledge',               'p6'),
  (17::smallint, 'P', 'Knows when the students have difficulty understanding the lesson and find ways to make it easy', 'p7'),
  (18::smallint, 'P', 'Integrates values into the lesson',                                            'p8'),
  (19::smallint, 'P', 'Speaks the language of instruction (English or Filipino) clearly and fluently', 'p9'),
  (20::smallint, 'P', 'Delivers thought provoking questions',                                         'p10')
) as t(item_no, section_code, text, legacy_key)
where i.code = 'SAST-20'
on conflict do nothing;


-- 3. Term -> instrument (admin setting; one per evaluator_type per term; nothing seeded) ------
create table if not exists public.eval_term_instruments (
  term_id         uuid   not null references public.academic_terms (id) on delete cascade,
  evaluator_type  text   not null check (evaluator_type in ('student', 'supervisor')),
  instrument_id   bigint not null,
  set_by          uuid,
  set_at          timestamptz not null default now(),
  primary key (term_id, evaluator_type),
  -- the instrument must be of the same evaluator type
  foreign key (instrument_id, evaluator_type)
    references public.eval_instruments (id, evaluator_type)
);

comment on table public.eval_term_instruments is
  'Which form a term uses, per evaluator type. No row = not set; save_eval_response then accepts any non-legacy instrument.';


-- 4. Responses: one row per filled form (no student name / id) --------------------------------
create table if not exists public.eval_responses (
  id                 bigint generated by default as identity primary key,
  instrument_id      bigint not null,
  evaluator_type     text   not null,   -- copied from the instrument (composite FK keeps it right)
  instructor_id      uuid   not null references public.user_info (id),
  subject_id         uuid            references public.subjects (id),
  term_id            uuid   not null references public.academic_terms (id),
  evaluator_user_id  uuid            references public.user_info (id),   -- supervisor forms only
  stamp_no           text,
  stamp_source       text,
  comments           text,
  source             text   not null,
  import_job_id      uuid,      -- sheet_import_jobs.id (018) when it came from a sheet import; no FK
  task_id            text,      -- caller's idempotency key (sheet row / scan page / web form)
  status             text   not null default 'submitted',
  answered_count     smallint,  -- non-NULL answers          (save_eval_response / recompute_eval)
  answered_total     smallint,  -- sum of the answers
  rating_pct         numeric(5, 2),   -- answered_total / (5 * answered_count) * 100
  created_at         timestamptz not null default now(),
  created_by         uuid,
  updated_at         timestamptz,
  constraint eval_responses_id_instrument_uniq unique (id, instrument_id),
  constraint eval_responses_instrument_fkey foreign key (instrument_id, evaluator_type)
    references public.eval_instruments (id, evaluator_type),
  constraint eval_responses_source_check check (source in ('sheet', 'web', 'scan')),
  constraint eval_responses_status_check check (status in ('draft', 'submitted', 'void')),
  constraint eval_responses_stamp_no_format check (stamp_no is null or stamp_no ~ '^[0-9]{3,12}$'),
  constraint eval_responses_stamp_source_check
    check (stamp_source is null or stamp_source in ('gemini+python', 'gemini', 'staff', 'override', 'sheet')),
  -- students are never identified; a supervisor form always names its evaluator
  constraint eval_responses_evaluator_check
    check ((evaluator_type = 'supervisor') = (evaluator_user_id is not null)),
  constraint eval_responses_not_self check (evaluator_user_id is null or evaluator_user_id <> instructor_id),
  constraint eval_responses_student_subject check (evaluator_type = 'supervisor' or subject_id is not null)
);

-- one form per stamp and term (rows without a stamp are never compared)
create unique index if not exists eval_responses_term_stamp_uniq
  on public.eval_responses (term_id, stamp_no)
  where stamp_no is not null;

-- a supervisor evaluates a faculty member once per subject (or once with no subject) per term
create unique index if not exists eval_responses_supervisor_uniq
  on public.eval_responses (evaluator_user_id, instructor_id, subject_id, term_id) nulls not distinct
  where evaluator_type = 'supervisor' and status <> 'void';

create unique index if not exists eval_responses_task_uniq
  on public.eval_responses (task_id)
  where task_id is not null;

create index if not exists eval_responses_pair_idx
  on public.eval_responses (instructor_id, term_id, instrument_id);
create index if not exists eval_responses_evaluator_idx
  on public.eval_responses (evaluator_user_id) where evaluator_user_id is not null;

comment on table public.eval_responses is
  'One filled SET/SUP form. No student name or id is stored. Instructors never read this table (see eval_response_comments).';


-- 5. Answers: NULL (blank) or 1..5 -----------------------------------------------------------
create table if not exists public.eval_answers (
  response_id    bigint   not null,
  instrument_id  bigint   not null,
  item_no        smallint not null,
  rating         smallint,
  primary key (response_id, item_no),
  constraint eval_answers_response_fkey foreign key (response_id, instrument_id)
    references public.eval_responses (id, instrument_id) on delete cascade,
  constraint eval_answers_item_fkey foreign key (instrument_id, item_no)
    references public.eval_instrument_items (instrument_id, item_no),
  constraint eval_answers_rating_1_to_5 check (rating is null or rating between 1 and 5)
);


-- 6. Results (long format, see header) --------------------------------------------------------
create table if not exists public.eval_results (
  id                  bigint generated by default as identity primary key,
  instrument_id       bigint not null references public.eval_instruments (id),
  instructor_id       uuid   not null references public.user_info (id),
  term_id             uuid   not null references public.academic_terms (id),
  subject_id          uuid            references public.subjects (id),   -- NULL = all subjects pooled
  level               text   not null check (level in ('overall', 'section', 'item')),
  section_code        text,
  item_no             smallint,
  respondents         integer not null,   -- counted forms in the scope
  excluded_responses  integer,            -- overall rows: submitted forms below min_answered
  answered_count      integer not null,   -- non-NULL answers behind this row
  answer_total        integer not null,
  mean                numeric(4, 2),      -- NULL = nobody answered
  pct                 numeric(5, 2),
  verbal_label        text,
  updated_at          timestamptz not null default now(),
  constraint eval_results_level_shape check (
       (level = 'overall' and section_code is null     and item_no is null)
    or (level = 'section' and section_code is not null and item_no is null)
    or (level = 'item'    and section_code is not null and item_no is not null)),
  constraint eval_results_scope_uniq unique nulls not distinct
    (instrument_id, instructor_id, term_id, subject_id, level, section_code, item_no)
);

create index if not exists eval_results_instructor_term_idx on public.eval_results (instructor_id, term_id);

comment on table public.eval_results is
  'Long format. Scope = (instrument, instructor, term, subject_id; NULL subject = pooled). '
  'level overall: 1 row; section: 1 per section; item: 1 per item. Written only by recompute_eval.';


-- 7. Small helpers -----------------------------------------------------------------------------
create or replace function public.eval_is_sao_office()
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1
    from public."Sao_users" su
    join public.roles r on r.id = su.role_id
    where su.user_id = auth.uid()
      and upper(btrim(r."Roles")) in ('SAO_ADMIN', 'SAO_STAFF')
  );
$$;

-- the caller is a department head of the faculty's teaching department for the term
create or replace function public.eval_dept_head_reads(p_instructor_id uuid, p_term_id uuid)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select auth.uid() is not null
     and public.is_dept_head()
     and public.teaching_department_id(p_instructor_id, p_term_id) = public.get_my_dept_id();
$$;

-- same, and never the caller himself (a supervisor form about the head is written by someone else)
create or replace function public.eval_dept_head_can_evaluate(p_instructor_id uuid, p_term_id uuid)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select p_instructor_id is distinct from auth.uid()
     and public.eval_dept_head_reads(p_instructor_id, p_term_id);
$$;

create or replace function public.eval_scale_max(p_scale jsonb)
returns numeric
language sql
immutable
as $$
  select coalesce(max((e ->> 'value')::numeric), 5)
  from jsonb_array_elements(case when jsonb_typeof(p_scale) = 'array' then p_scale else '[]'::jsonb end) e;
$$;

-- highest band whose min_pct <= the 2-decimal %; NULL % -> NULL ("No data" is a display concern)
create or replace function public.eval_verbal_label(p_bands jsonb, p_pct numeric)
returns text
language sql
immutable
as $$
  select b ->> 'label'
  from jsonb_array_elements(case when p_pct is not null and jsonb_typeof(p_bands) = 'array'
                                 then p_bands else '[]'::jsonb end) b
  where round(p_pct, 2) >= (b ->> 'min_pct')::numeric
  order by (b ->> 'min_pct')::numeric desc
  limit 1;
$$;


-- 8. Stamp: DUP across SAST and eval forms (additive; sast_stamp_duplicate_of unchanged) ---------
-- p_task_id: the caller's own row is never its duplicate; p_exclude_response_id: same, by id.
create or replace function public.eval_stamp_duplicate_of(
  p_term_id text, p_stamp_no text, p_task_id text, p_exclude_response_id bigint default null)
returns jsonb
language sql
stable
set search_path = public
as $$
  select coalesce(
    public.sast_stamp_duplicate_of(p_term_id, p_stamp_no, p_task_id) || jsonb_build_object('source', 'sast'),
    (select jsonb_build_object(
              'source',         'eval',
              'response_id',    r.id,
              'task_id',        r.task_id,
              'instrument',     i.code,
              'submitted_date', to_char(r.created_at, 'YYYY-MM-DD'),
              'instructor_id',  r.instructor_id::text,
              'subject_id',     r.subject_id::text,
              'term_id',        r.term_id::text,
              'stamp_no',       r.stamp_no)
       from public.eval_responses r
       join public.eval_instruments i on i.id = r.instrument_id
      where nullif(btrim(coalesce(p_stamp_no, '')), '') is not null
        and r.stamp_no = btrim(p_stamp_no)
        and r.term_id::text = lower(btrim(coalesce(p_term_id, '')))
        and (nullif(btrim(coalesce(p_task_id, '')), '') is null or r.task_id is distinct from btrim(p_task_id))
        and (p_exclude_response_id is null or r.id <> p_exclude_response_id)
      order by r.id
      limit 1));
$$;

-- eval form vs SAST form with the same stamp in the same term (any writer, also direct table writes)
create or replace function public.eval_responses_stamp_guard()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if new.stamp_no is null then
    return new;
  end if;
  perform pg_advisory_xact_lock(hashtext('ievaluate:stamp:' || new.term_id::text || ':' || new.stamp_no));
  if exists (select 1 from public.sast_all_raw_data_survey s
              where s.term_id = new.term_id and s.stamp_no = new.stamp_no) then
    raise exception 'stamp % is already on a SAST form in this term', new.stamp_no
      using errcode = 'unique_violation', constraint = 'eval_responses_stamp_in_sast',
            schema = 'public', table = 'eval_responses';
  end if;
  return new;
end;
$$;

drop trigger if exists eval_responses_stamp_guard on public.eval_responses;
create trigger eval_responses_stamp_guard
  before insert or update of stamp_no, term_id on public.eval_responses
  for each row execute function public.eval_responses_stamp_guard();


-- 9. Scoring ------------------------------------------------------------------------------------
-- Rebuilds eval_results of (instrument, instructor, term): the pooled scope always, and the subject
-- scope of p_subject_id (NULL = every subject). Refreshes answered_count / answered_total /
-- rating_pct of every form of the pair first. Returns the pooled overall row.
create or replace function public.recompute_eval(
  p_instructor_id uuid, p_subject_id uuid, p_term_id uuid, p_instrument_id bigint)
returns table (instrument_id bigint, instructor_id uuid, term_id uuid, respondents integer,
               excluded_responses integer, overall_mean numeric, overall_pct numeric, verbal_label text)
language plpgsql
volatile
set search_path = public
as $fn$
#variable_conflict use_column
declare
  v_inst      public.eval_instruments;
  v_max       numeric;
  v_excluded  integer;
begin
  -- recomputes never race; held until commit
  perform pg_advisory_xact_lock(hashtext('ievaluate:recompute_eval'));

  if p_instructor_id is null or p_term_id is null or p_instrument_id is null then
    return query select p_instrument_id, p_instructor_id, p_term_id, 0, 0,
                        null::numeric, null::numeric, null::text;
    return;
  end if;

  select * into v_inst from public.eval_instruments i where i.id = p_instrument_id;
  if not found then
    raise exception 'recompute_eval: unknown instrument %', p_instrument_id;
  end if;
  v_max := public.eval_scale_max(v_inst.scale);

  -- 9a. per form: answered count, total, rating %
  update public.eval_responses r
     set answered_count = x.answered,
         answered_total = x.total,
         rating_pct     = case when x.answered > 0
                               then round(x.total::numeric / (v_max * x.answered) * 100, 2) end
    from (select r2.id,
                 count(a.rating)::int            as answered,
                 coalesce(sum(a.rating), 0)::int as total
            from public.eval_responses r2
            left join public.eval_answers a on a.response_id = r2.id
           where r2.instrument_id = p_instrument_id
             and r2.instructor_id = p_instructor_id
             and r2.term_id = p_term_id
           group by r2.id) x
   where r.id = x.id;

  -- 9b. old rows of the scopes being rebuilt
  delete from public.eval_results e
   where e.instrument_id = p_instrument_id
     and e.instructor_id = p_instructor_id
     and e.term_id = p_term_id
     and (e.subject_id is null or p_subject_id is null or e.subject_id = p_subject_id);

  -- 9c. new rows
  with pair as (
    select r.id as response_id, r.subject_id,
           coalesce(r.answered_count, 0) >= v_inst.min_answered as counted
    from public.eval_responses r
    where r.instrument_id = p_instrument_id
      and r.instructor_id = p_instructor_id
      and r.term_id = p_term_id
      and r.status = 'submitted'
  ),
  scoped as (               -- (scope subject, form); NULL scope = pooled
    select null::uuid as scope_subject, p.response_id, p.counted from pair p
    union all
    select p.subject_id, p.response_id, p.counted from pair p
     where p.subject_id is not null and (p_subject_id is null or p.subject_id = p_subject_id)
  ),
  resp as (
    select s.scope_subject,
           count(*) filter (where s.counted)::int     as respondents,
           count(*) filter (where not s.counted)::int as excluded
    from scoped s
    group by s.scope_subject
    having count(*) filter (where s.counted) > 0     -- a scope without a counted form has no results
  ),
  items as (                -- every item of the instrument for every scope
    select rs.scope_subject, it.section_code, it.item_no,
           round(avg(a.rating), 2)          as mean,       -- blanks skipped; none -> NULL
           count(a.rating)::int             as answered,
           coalesce(sum(a.rating), 0)::int  as total
    from resp rs
    cross join public.eval_instrument_items it
    left join scoped s on s.scope_subject is not distinct from rs.scope_subject and s.counted
    left join public.eval_answers a on a.response_id = s.response_id and a.item_no = it.item_no
    where it.instrument_id = p_instrument_id
    group by rs.scope_subject, it.section_code, it.item_no
  ),
  secs as (                 -- of the ROUNDED item means (013)
    select i.scope_subject, i.section_code, avg(i.mean) as raw_mean,
           sum(i.answered)::int as answered, sum(i.total)::int as total
    from items i
    group by i.scope_subject, i.section_code
  ),
  ovr as (
    select i.scope_subject, avg(i.mean) as raw_mean,
           sum(i.answered)::int as answered, sum(i.total)::int as total
    from items i
    group by i.scope_subject
  )
  insert into public.eval_results (
    instrument_id, instructor_id, term_id, subject_id, level, section_code, item_no,
    respondents, excluded_responses, answered_count, answer_total, mean, pct, verbal_label, updated_at
  )
  select p_instrument_id, p_instructor_id, p_term_id, i.scope_subject, 'item', i.section_code, i.item_no,
         rs.respondents, null::int, i.answered, i.total, i.mean,
         round(i.mean / v_max * 100, 2),
         public.eval_verbal_label(v_inst.verbal_bands, round(i.mean / v_max * 100, 2)),
         now()
    from items i
    join resp rs on rs.scope_subject is not distinct from i.scope_subject
  union all
  select p_instrument_id, p_instructor_id, p_term_id, s.scope_subject, 'section', s.section_code, null::smallint,
         rs.respondents, null::int, s.answered, s.total, round(s.raw_mean, 2),
         round(s.raw_mean / v_max * 100, 2),
         public.eval_verbal_label(v_inst.verbal_bands, round(s.raw_mean / v_max * 100, 2)),
         now()
    from secs s
    join resp rs on rs.scope_subject is not distinct from s.scope_subject
  union all
  select p_instrument_id, p_instructor_id, p_term_id, o.scope_subject, 'overall', null::text, null::smallint,
         rs.respondents, rs.excluded, o.answered, o.total, round(o.raw_mean, 2),
         round(o.raw_mean / v_max * 100, 2),
         public.eval_verbal_label(v_inst.verbal_bands, round(o.raw_mean / v_max * 100, 2)),
         now()
    from ovr o
    join resp rs on rs.scope_subject is not distinct from o.scope_subject;

  -- 9d. instructor <-> subject link (as recompute_sast does), for subjects with a submitted form
  insert into public.instructor_subjects (subject_id, instructor_id, term_id)
  select distinct r.subject_id, p_instructor_id, p_term_id
  from public.eval_responses r
  where r.instrument_id = p_instrument_id
    and r.instructor_id = p_instructor_id
    and r.term_id = p_term_id
    and r.status = 'submitted'
    and r.subject_id is not null
  on conflict (subject_id, instructor_id, term_id) do nothing;

  select count(*)::int into v_excluded
  from public.eval_responses r
  where r.instrument_id = p_instrument_id
    and r.instructor_id = p_instructor_id
    and r.term_id = p_term_id
    and r.status = 'submitted'
    and coalesce(r.answered_count, 0) < v_inst.min_answered;

  return query
    select p_instrument_id, p_instructor_id, p_term_id, coalesce(e.respondents, 0), v_excluded,
           e.mean, e.pct, e.verbal_label
    from (select 1) d
    left join public.eval_results e
      on e.instrument_id = p_instrument_id and e.instructor_id = p_instructor_id
     and e.term_id = p_term_id and e.subject_id is null and e.level = 'overall';
end
$fn$;


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
-- A supervisor form for the same (evaluator, instructor, subject, term) is replaced, not duplicated.
-- Signed-in caller: SAO office may save anything; a department head only SUPERVISOR forms as the
-- evaluator (forced to auth.uid(), source 'web') for faculty of his department, never himself.
-- Answers jsonb:
--   {"saved":true, "response_id", "updated", "instrument", "answered", "counted", "rating_pct", "result"}
--   {"saved":false, "reason": unknown_instrument | legacy_instrument | invalid_id | missing_instructor_or_term
--     | missing_subject | missing_evaluator | self_evaluation | invalid_source | invalid_status
--     | instrument_not_for_term | invalid_answers | invalid_item | duplicate_item | invalid_rating
--     | task_id_conflict | duplicate_stamp (+ duplicate_of DUP) | duplicate_supervisor | unknown_reference}
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
  p_recompute          boolean default true)
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

  begin
    if v_old.id is null then
      insert into public.eval_responses (
        instrument_id, evaluator_type, instructor_id, subject_id, term_id, evaluator_user_id,
        stamp_no, stamp_source, comments, source, import_job_id, task_id, status,
        answered_count, answered_total, rating_pct, created_by)
      values (
        v_inst.id, v_inst.evaluator_type, v_instructor, v_subject, v_term, v_evaluator,
        v_stamp, v_stamp_src, nullif(btrim(coalesce(p_comments, '')), ''), v_source, v_job, v_task, v_status,
        v_answered, v_total, v_pct, v_created_by)
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


-- 11. Comments without evaluator identity (instructor / dept head / SAO) ------------------------
-- Only counted forms (submitted, >= min_answered), like 016 for SAST remarks. Runs as the view
-- owner; the WHERE clause is the access rule.
create or replace view public.eval_response_comments with (security_barrier = true) as
select r.id            as response_id,
       r.instrument_id,
       i.code          as instrument_code,
       i.evaluator_type,
       r.instructor_id,
       r.subject_id,
       r.term_id,
       r.comments
from public.eval_responses r
join public.eval_instruments i on i.id = r.instrument_id
where r.status = 'submitted'
  and coalesce(r.answered_count, 0) >= i.min_answered
  and nullif(btrim(coalesce(r.comments, '')), '') is not null
  and (   r.instructor_id = auth.uid()
       or public.eval_is_sao_office()
       or public.eval_dept_head_reads(r.instructor_id, r.term_id));


-- 12. RLS + grants ------------------------------------------------------------------------------
alter table public.eval_instruments         enable row level security;
alter table public.eval_instrument_sections enable row level security;
alter table public.eval_instrument_items    enable row level security;
alter table public.eval_term_instruments    enable row level security;
alter table public.eval_responses           enable row level security;
alter table public.eval_answers             enable row level security;
alter table public.eval_results             enable row level security;

revoke all on table public.eval_instruments, public.eval_instrument_sections, public.eval_instrument_items,
                    public.eval_term_instruments, public.eval_responses, public.eval_answers,
                    public.eval_results, public.eval_response_comments
  from public, anon, authenticated;
grant select on table public.eval_instruments, public.eval_instrument_sections, public.eval_instrument_items,
                      public.eval_results, public.eval_response_comments
  to authenticated;
grant select, insert, update, delete on table public.eval_term_instruments, public.eval_responses,
                                              public.eval_answers
  to authenticated;
grant all on table public.eval_instruments, public.eval_instrument_sections, public.eval_instrument_items,
                   public.eval_term_instruments, public.eval_responses, public.eval_answers,
                   public.eval_results
  to service_role;
grant select on table public.eval_response_comments to service_role;

-- registry: every signed-in user reads; nobody writes over the API
drop policy if exists "eval_instruments: signed-in read" on public.eval_instruments;
create policy "eval_instruments: signed-in read" on public.eval_instruments
  for select to authenticated using (true);
drop policy if exists "eval_instrument_sections: signed-in read" on public.eval_instrument_sections;
create policy "eval_instrument_sections: signed-in read" on public.eval_instrument_sections
  for select to authenticated using (true);
drop policy if exists "eval_instrument_items: signed-in read" on public.eval_instrument_items;
create policy "eval_instrument_items: signed-in read" on public.eval_instrument_items
  for select to authenticated using (true);

-- term mapping: everyone reads, SAO office writes
drop policy if exists "eval_term_instruments: signed-in read" on public.eval_term_instruments;
create policy "eval_term_instruments: signed-in read" on public.eval_term_instruments
  for select to authenticated using (true);
drop policy if exists "eval_term_instruments: sao office writes" on public.eval_term_instruments;
create policy "eval_term_instruments: sao office writes" on public.eval_term_instruments
  for all to authenticated
  using (public.eval_is_sao_office()) with check (public.eval_is_sao_office());

-- responses
drop policy if exists "eval_responses: sao office all" on public.eval_responses;
create policy "eval_responses: sao office all" on public.eval_responses
  for all to authenticated
  using (public.eval_is_sao_office()) with check (public.eval_is_sao_office());
drop policy if exists "eval_responses: evaluator reads own" on public.eval_responses;
create policy "eval_responses: evaluator reads own" on public.eval_responses
  for select to authenticated
  using (evaluator_user_id = auth.uid());
drop policy if exists "eval_responses: dept head inserts supervisor" on public.eval_responses;
create policy "eval_responses: dept head inserts supervisor" on public.eval_responses
  for insert to authenticated
  with check (evaluator_type = 'supervisor' and evaluator_user_id = auth.uid() and source = 'web'
              and public.eval_dept_head_can_evaluate(instructor_id, term_id));
drop policy if exists "eval_responses: dept head updates own supervisor" on public.eval_responses;
create policy "eval_responses: dept head updates own supervisor" on public.eval_responses
  for update to authenticated
  using (evaluator_type = 'supervisor' and evaluator_user_id = auth.uid())
  with check (evaluator_type = 'supervisor' and evaluator_user_id = auth.uid() and source = 'web'
              and public.eval_dept_head_can_evaluate(instructor_id, term_id));

-- answers (the EXISTS runs under eval_responses RLS: a head only sees his own forms)
drop policy if exists "eval_answers: sao office all" on public.eval_answers;
create policy "eval_answers: sao office all" on public.eval_answers
  for all to authenticated
  using (public.eval_is_sao_office()) with check (public.eval_is_sao_office());
drop policy if exists "eval_answers: evaluator own supervisor form" on public.eval_answers;
create policy "eval_answers: evaluator own supervisor form" on public.eval_answers
  for all to authenticated
  using (exists (select 1 from public.eval_responses r
                  where r.id = eval_answers.response_id
                    and r.evaluator_type = 'supervisor' and r.evaluator_user_id = auth.uid()))
  with check (exists (select 1 from public.eval_responses r
                       where r.id = eval_answers.response_id
                         and r.evaluator_type = 'supervisor' and r.evaluator_user_id = auth.uid()));

-- results: read only (recompute_eval writes)
drop policy if exists "eval_results: sao office reads all" on public.eval_results;
create policy "eval_results: sao office reads all" on public.eval_results
  for select to authenticated using (public.eval_is_sao_office());
drop policy if exists "eval_results: instructor reads own" on public.eval_results;
create policy "eval_results: instructor reads own" on public.eval_results
  for select to authenticated using (instructor_id = auth.uid());
drop policy if exists "eval_results: dept head reads dept" on public.eval_results;
create policy "eval_results: dept head reads dept" on public.eval_results
  for select to authenticated using (public.eval_dept_head_reads(instructor_id, term_id));

-- functions
revoke all on function public.eval_is_sao_office()                      from public, anon;
revoke all on function public.eval_dept_head_reads(uuid, uuid)          from public, anon;
revoke all on function public.eval_dept_head_can_evaluate(uuid, uuid)   from public, anon;
grant execute on function public.eval_is_sao_office()                    to authenticated, service_role;
grant execute on function public.eval_dept_head_reads(uuid, uuid)        to authenticated, service_role;
grant execute on function public.eval_dept_head_can_evaluate(uuid, uuid) to authenticated, service_role;

grant execute on function public.eval_scale_max(jsonb)             to authenticated, service_role;
grant execute on function public.eval_verbal_label(jsonb, numeric) to authenticated, service_role;

revoke all on function public.eval_stamp_duplicate_of(text, text, text, bigint) from public, anon, authenticated;
grant execute on function public.eval_stamp_duplicate_of(text, text, text, bigint) to service_role;

revoke all on function public.eval_responses_stamp_guard() from public, anon, authenticated;

revoke all on function public.recompute_eval(uuid, uuid, uuid, bigint) from public, anon, authenticated;
grant execute on function public.recompute_eval(uuid, uuid, uuid, bigint) to service_role;

revoke all on function public.save_eval_response(text, text, text, text, jsonb, text, text, text, text,
                                                 text, text, text, text, text, boolean)
  from public, anon;
grant execute on function public.save_eval_response(text, text, text, text, jsonb, text, text, text, text,
                                                    text, text, text, text, text, boolean)
  to authenticated, service_role;


-- Preview -----------------------------------------------------------------------------------------
select i.code, i.evaluator_type, i.item_count, i.is_legacy,
       (select count(*) from public.eval_instrument_sections s where s.instrument_id = i.id) as sections,
       (select count(*) from public.eval_instrument_items t where t.instrument_id = i.id)    as items,
       (select count(*) from public.eval_instrument_items t
         where t.instrument_id = i.id and t.verification_hint is not null)                    as hints
  from public.eval_instruments i order by i.id;            -- SAST-20 2/20/0, SET-15 3/15/0, SUP-15 3/15/15
select tablename, policyname, cmd from pg_policies
 where schemaname = 'public' and tablename like 'eval%' order by tablename, policyname;
select public.eval_verbal_label(i.verbal_bands, p) as label, p
  from public.eval_instruments i, unnest(array[84, 83.99, 68, 52, 36, 35.99, 20]::numeric[]) p
 where i.code = 'SET-15';

-- Nothing is kept until you switch this to commit. Check the preview first.
rollback;
-- commit;


-- =========================================================================================
-- Verification (run by hand AFTER the commit). Read-only except the sample save, which is
-- wrapped in its own begin ... rollback.
-- =========================================================================================
-- select code, evaluator_type, item_count, min_answered from public.eval_instruments order by id;
-- select item_no, section_code, text, verification_hint
--   from public.eval_instrument_items t join public.eval_instruments i on i.id = t.instrument_id
--  where i.code = 'SUP-15' order by item_no;
-- select * from public.eval_term_instruments;                 -- empty until the admin sets a term
-- select proname from pg_proc where proname in
--   ('save_eval_response','recompute_eval','eval_stamp_duplicate_of','eval_verbal_label');   -- 4 rows
--
-- -- sample save + recompute (pick real ids: an instructor, a subject, a term)
-- begin;
-- select public.save_eval_response(
--   'SET-15', '<instructor uuid>', '<subject uuid>', '<term uuid>',
--   '{"1":5,"2":4,"3":5,"4":4,"5":3,"6":5,"7":5,"8":4,"9":null,"10":4,"11":5,"12":5,"13":4,"14":"","15":5}'::jsonb,
--   'Very clear lessons.', 'sheet', null, null, null, null, 'sheet:test:row2', null);
--   -- expect {"saved":true, "answered":13, "counted":true, "rating_pct":89.23, ...}
-- select public.save_eval_response(
--   'SET-15', '<instructor uuid>', '<subject uuid>', '<term uuid>',
--   '[5,4,null,null,null,null,null,null,null,null,null,null,null,null,null]'::jsonb,
--   null, 'sheet', null, null, null, null, 'sheet:test:row3', null);
--   -- expect saved, "counted": false (2 answers < 5); excluded_responses 1 in result
-- select level, section_code, item_no, respondents, excluded_responses, answered_count, mean, pct, verbal_label
--   from public.eval_results
--  where instructor_id = '<instructor uuid>' and term_id = '<term uuid>' and subject_id is null
--  order by level, section_code, item_no;
-- select * from public.recompute_eval('<instructor uuid>', null, '<term uuid>',
--                                     (select id from public.eval_instruments where code = 'SET-15'));
-- select public.eval_stamp_duplicate_of('<term uuid>', '<a stamp from sast_all_raw_data_survey>', null);  -- source sast
-- rollback;
