-- ---------------------------------------------------------------------------
-- 018: sheet_import_jobs -- the Google Sheet import tells the app when it REALLY ends.
--
-- Why: 'Google LINK from phone1' (sast-link-upload) answers early ('Sheet Import Response', to avoid
-- the proxy's 504) while n8n keeps saving rows and runs the slow Gemini remark loop. The app showed
-- "finished" at that early answer. Now n8n writes one row here per import:
--   * 'Create Sheet Import Job' (before the early answer): status 'processing'; the answer carries job_id
--   * 'Mark Import Job Failed'  (error outputs after the answer): status 'error', failed_node, message
--   * 'Mark Import Job Done'    (after the whole flow): finished_at + real counts; 'done' unless a
--                               failure was recorded ('error' then)
--   * 'Mark Import Job Stopped' (the workflow's own Error Trigger, when the execution itself fails):
--                               status 'error', failed_node = the last node, finished_at
-- The Flutter app (data gatherer screen) polls the row by id every 4 s.
--
-- Access:
--   * n8n writes with its Postgres credential ('Postgres account', role postgres: bypasses RLS).
--   * The app reads with the signed-in user's JWT (role authenticated). Only the SAO office starts
--     an import (the n8n-proxy edge function admits SAO_ADMIN / SAO_STAFF only and stamps the
--     verified user id into the payload), so SELECT is for the SAO office, same EXISTS rule as
--     "SAO office deletes import_errors" (Flutter migration 20240130000022), or the user who started
--     the job (requested_by = auth.uid()).
--   * Nobody writes from the app or the Data API: no INSERT/UPDATE/DELETE policy, grants revoked.
--
-- Idempotent (create ... if not exists, drop policy if exists). Not auto-applied: run it in the
-- Supabase SQL Editor. The script ends with ROLLBACK so a first run only previews. Check the output,
-- then change the last line to  commit;  and run it again. Apply it BEFORE re-importing the workflow
-- (without the table 'Create Sheet Import Job' fails, the answer has no job_id, and the app falls back
-- to the old "finished" message; the import itself still runs).
-- ---------------------------------------------------------------------------
begin;

create table if not exists public.sheet_import_jobs (
  id                uuid primary key default gen_random_uuid(),
  status            text not null default 'processing',
  rows              int,
  matched           int,
  unmatched         int,
  imported_count    int,
  error_count       int,
  ai_groups         int,
  ai_failed         int,
  message           text,
  failed_node       text,
  n8n_execution_id  text,
  requested_by      uuid,
  created_at        timestamptz not null default now(),
  finished_at       timestamptz
);

-- columns added after a first version of this table (no-ops on a fresh table)
alter table public.sheet_import_jobs add column if not exists ai_groups int;
alter table public.sheet_import_jobs add column if not exists ai_failed int;
alter table public.sheet_import_jobs add column if not exists requested_by uuid;

alter table public.sheet_import_jobs drop constraint if exists sheet_import_jobs_status_check;
alter table public.sheet_import_jobs
  add constraint sheet_import_jobs_status_check check (status in ('processing', 'done', 'error'));

comment on table public.sheet_import_jobs is
  'One row per Google Sheet import (n8n sast-link-upload). Written by n8n only; the app polls it by id.';
comment on column public.sheet_import_jobs.imported_count is
  'Rows saved to sast_all_raw_data_survey. NULL until the import has finished.';
comment on column public.sheet_import_jobs.error_count is
  'Rows not imported: unmatched rows (sent to import_errors) + rows whose save failed.';
comment on column public.sheet_import_jobs.failed_node is
  'n8n node where the first failure happened (status error).';

-- 'Mark Import Job Failed' / 'Mark Import Job Stopped' find the row by execution id
create index if not exists sheet_import_jobs_execution_idx on public.sheet_import_jobs (n8n_execution_id);
create index if not exists sheet_import_jobs_created_idx on public.sheet_import_jobs (created_at desc);

alter table public.sheet_import_jobs enable row level security;

revoke all on table public.sheet_import_jobs from anon, authenticated;
grant select on table public.sheet_import_jobs to authenticated;
grant all on table public.sheet_import_jobs to service_role;

drop policy if exists "SAO office reads sheet import jobs" on public.sheet_import_jobs;
create policy "SAO office reads sheet import jobs"
  on public.sheet_import_jobs for select to authenticated
  using (
    requested_by = auth.uid()
    or exists (
      select 1
      from public."Sao_users" su
      join public.roles r on r.id = su.role_id
      where su.user_id = auth.uid()
        and upper(btrim(r."Roles")) in ('SAO_ADMIN', 'SAO_STAFF')
    )
  );

-- Preview: the table, its policy and grants
select column_name, data_type
  from information_schema.columns
 where table_schema = 'public' and table_name = 'sheet_import_jobs'
 order by ordinal_position;
select policyname, cmd, roles from pg_policies
 where schemaname = 'public' and tablename = 'sheet_import_jobs';
select grantee, privilege_type from information_schema.role_table_grants
 where table_schema = 'public' and table_name = 'sheet_import_jobs'
 order by grantee, privilege_type;

-- Nothing is kept until you switch this to commit. Check the preview first.
rollback;
-- commit;
