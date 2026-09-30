-- 005: review page on the website (live "Needs review" + "Import errors" lists).
--
-- Review before applying. Not auto-applied to the live project. Run it in the
-- Supabase SQL Editor. Safe to re-run (add column if not exists, constraints
-- guarded, create or replace, drop trigger if exists).
--
-- Purely ADDITIVE: nothing is renamed or dropped, no RLS policy and no
-- Realtime publication is changed, and every new column is nullable or has a
-- default, so the Flutter app and the n8n workflow keep reading and writing
-- these tables exactly as before (they never need to know the new columns).
--
-- What it adds, on failed_scan_queue and import_errors:
--   updated_at       set on every UPDATE by a trigger (the website asks
--                    "what changed since <time>?" every few seconds)
--   review_version   +1 whenever the row's content or status changes (from the
--                    website, Flutter or n8n). The website sends the version it
--                    was editing; an outdated edit is refused instead of
--                    silently overwriting someone else's change.
--   review_state     null | submitting | submit_failed (website correction in flight)
--   review_action    null | approved | rerouted | rejected (failed_scan_queue only)
--   review_error     plain-English reason a website correction failed
--   review_submitted_at, review_claimed_by, review_claimed_at
--                    advisory "someone is looking at this" marker; claiming
--                    does NOT change review_version
--   review_correction (failed_scan_queue only) what the reviewer sent
--
-- Until this is applied the website review page still lists the forms
-- (read-only, refreshed in full) and every action answers
-- 503 migration_required. The backend picks it up within ~5 minutes, no
-- restart needed.
begin;

alter table public.failed_scan_queue
  add column if not exists updated_at timestamptz not null default now(),
  add column if not exists review_version integer not null default 0,
  add column if not exists review_state text,            -- null | submitting | submit_failed
  add column if not exists review_action text,           -- null | approved | rerouted | rejected
  add column if not exists review_error text,
  add column if not exists review_submitted_at timestamptz,
  add column if not exists review_claimed_by uuid,
  add column if not exists review_claimed_at timestamptz,
  add column if not exists review_correction jsonb;

alter table public.import_errors
  add column if not exists updated_at timestamptz not null default now(),
  add column if not exists review_version integer not null default 0,
  add column if not exists review_state text,
  add column if not exists review_error text,
  add column if not exists review_submitted_at timestamptz,
  add column if not exists review_claimed_by uuid,
  add column if not exists review_claimed_at timestamptz;

do $$ begin
  if not exists (select 1 from pg_constraint where conname = 'failed_scan_queue_review_state_check') then
    alter table public.failed_scan_queue add constraint failed_scan_queue_review_state_check
      check (review_state is null or review_state in ('submitting', 'submit_failed'));
  end if;
  if not exists (select 1 from pg_constraint where conname = 'failed_scan_queue_review_action_check') then
    alter table public.failed_scan_queue add constraint failed_scan_queue_review_action_check
      check (review_action is null or review_action in ('approved', 'rerouted', 'rejected'));
  end if;
  if not exists (select 1 from pg_constraint where conname = 'import_errors_review_state_check') then
    alter table public.import_errors add constraint import_errors_review_state_check
      check (review_state is null or review_state in ('submitting', 'submit_failed'));
  end if;
end $$;

-- "Rows of this batch changed since ..." and "website import errors of this batch".
create index if not exists failed_scan_queue_batch_updated_idx
  on public.failed_scan_queue (batch_id, updated_at) where batch_id is not null;
create index if not exists import_errors_web_task_idx
  on public.import_errors (task_id text_pattern_ops) where task_id like 'web:%';
create index if not exists import_errors_updated_idx
  on public.import_errors (updated_at);

-- Bump updated_at on every update; bump review_version only when the content
-- or status changes (claims and error notes leave it alone).
-- The JSON columns are compared as text: that works whether they are jsonb,
-- json (which has no "=" operator) or text, so this trigger can never make an
-- UPDATE from the Flutter app or n8n fail.
create or replace function public.fsq_review_touch() returns trigger
language plpgsql set search_path = public as $$
begin
  new.updated_at := now();
  new.review_version := coalesce(old.review_version, 0) + case when
    (new.status, new.partial_data::text, new.validated_data::text, new.review_state, new.review_action) is distinct from
    (old.status, old.partial_data::text, old.validated_data::text, old.review_state, old.review_action) then 1 else 0 end;
  return new;
end $$;

create or replace function public.import_errors_review_touch() returns trigger
language plpgsql set search_path = public as $$
begin
  new.updated_at := now();
  new.review_version := coalesce(old.review_version, 0) + case when
    (new.status, new.raw_data::text, new.error_type, new.raw_instructor_name, new.raw_subject_name, new.raw_student_id, new.review_state) is distinct from
    (old.status, old.raw_data::text, old.error_type, old.raw_instructor_name, old.raw_subject_name, old.raw_student_id, old.review_state) then 1 else 0 end;
  return new;
end $$;

drop trigger if exists fsq_review_touch on public.failed_scan_queue;
create trigger fsq_review_touch before update on public.failed_scan_queue
  for each row execute function public.fsq_review_touch();

drop trigger if exists import_errors_review_touch on public.import_errors;
create trigger import_errors_review_touch before update on public.import_errors
  for each row execute function public.import_errors_review_touch();

commit;

-- OPTIONAL, not used (the website polls through the backend instead):
-- alter publication supabase_realtime add table public.failed_scan_queue, public.import_errors;

-- Check afterwards (read-only):
-- select column_name from information_schema.columns
--  where table_schema = 'public' and table_name in ('failed_scan_queue', 'import_errors')
--    and column_name like any (array['review_%', 'updated_at']) order by 1;
