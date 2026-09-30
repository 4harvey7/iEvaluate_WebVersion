-- 002_batch_hardening.sql
-- Batch-scanning hardening: station watcher keys, idempotent scan items,
-- atomic counters, heartbeat, instructor-only search.
--
-- ADDITIVE ONLY. Safe to re-run. Does not rename or drop any existing table
-- or column. The only functions replaced (create or replace) are ones used
-- exclusively by the batch-scanning web app: search_instructors. All other
-- functions below are new. Section 8 drops the two permissive RLS policies
-- that migration 001 created on this app's own stations / scan_batches tables
-- and revokes Data API access to them (security fix; the Flutter app does not
-- use these tables).
--
-- Run manually in the Supabase SQL Editor (Dashboard -> SQL Editor -> paste -> Run).

begin;

-- ---------------------------------------------------------------------------
-- 1. Stations: hashed watcher key + heartbeat
-- ---------------------------------------------------------------------------
alter table public.stations
  add column if not exists key_hash text,              -- sha256 hex of the watcher's station key (plain key is never stored)
  add column if not exists key_created_at timestamptz,
  add column if not exists last_seen_at timestamptz,   -- updated by POST /scans/heartbeat
  add column if not exists watcher_version text;

create unique index if not exists stations_key_hash_key
  on public.stations (key_hash)
  where key_hash is not null;

-- ---------------------------------------------------------------------------
-- 2. scan_batches: extra counters + last activity
-- ---------------------------------------------------------------------------
alter table public.scan_batches
  add column if not exists rejected_count int not null default 0,
  add column if not exists duplicate_count int not null default 0,
  add column if not exists last_scan_at timestamptz;

-- ---------------------------------------------------------------------------
-- 3. scan_batch_items: one row per page sent by a watcher (idempotency key =
--    batch_id + file_sha256). Resending the same page returns the stored result.
-- ---------------------------------------------------------------------------
create table if not exists public.scan_batch_items (
  id uuid primary key default gen_random_uuid(),
  batch_id uuid not null references public.scan_batches(id),
  station_id uuid references public.stations(id),
  file_sha256 text not null check (file_sha256 ~ '^[0-9a-f]{64}$'),
  filename text,
  task_id text,
  status text not null default 'processing'
    check (status in ('processing','success','flagged','rejected','duplicate','error')),
  review_reasons text[] not null default '{}',
  identity jsonb,
  message text,
  simulated boolean not null default false,
  attempts int not null default 1,
  retryable boolean not null default true,  -- false = failed permanently (retry cap reached)
  pipeline_failures int not null default 0, -- real pipeline failures only (not timeouts); drives the retry cap
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint scan_batch_items_batch_sha_key unique (batch_id, file_sha256)
);

-- In case an earlier draft of this table was created without it.
alter table public.scan_batch_items add column if not exists retryable boolean not null default true;
alter table public.scan_batch_items add column if not exists pipeline_failures int not null default 0;
-- Orientation normalisation done by the backend before n8n (app/orientation.py):
-- clockwise degrees applied, confidence 0..1, and whether it was unsure
-- (then the page was only turned landscape->portrait).
alter table public.scan_batch_items add column if not exists orientation_applied int;
alter table public.scan_batch_items add column if not exists orientation_confidence real;
alter table public.scan_batch_items add column if not exists orientation_uncertain boolean not null default false;

create index if not exists scan_batch_items_batch_status_idx
  on public.scan_batch_items (batch_id, status);

-- Only the backend (service role) touches this table. RLS on with no policies
-- = no access for anon/authenticated (Flutter app users) via the Data API.
alter table public.scan_batch_items enable row level security;
revoke all on table public.scan_batch_items from anon, authenticated;

-- ---------------------------------------------------------------------------
-- 4. Atomic counter increment (whitelisted field names, no dynamic SQL)
-- ---------------------------------------------------------------------------
create or replace function public.increment_batch_counter(p_batch_id uuid, p_field text)
returns void
language plpgsql
set search_path = public
as $$
begin
  if p_field not in ('processed_count','flagged_count','rejected_count','duplicate_count') then
    raise exception 'invalid counter field: %', p_field;
  end if;
  update public.scan_batches set
    processed_count = processed_count + (p_field = 'processed_count')::int,
    flagged_count   = flagged_count   + (p_field = 'flagged_count')::int,
    rejected_count  = rejected_count  + (p_field = 'rejected_count')::int,
    duplicate_count = duplicate_count + (p_field = 'duplicate_count')::int,
    last_scan_at    = now()
  where id = p_batch_id;
end;
$$;

-- ---------------------------------------------------------------------------
-- 5. Claim a page for processing. Inserts a 'processing' row, or re-claims a
--    retryable 'error' row / a stale 'processing' row (crashed request). Returns
--    claimed=false with the current status when the page is already done or
--    in flight elsewhere.
-- ---------------------------------------------------------------------------
create or replace function public.claim_batch_item(
  p_batch_id uuid,
  p_station_id uuid,
  p_file_sha256 text,
  p_filename text,
  p_task_id text,
  p_stale_seconds int default 300
)
returns table(item_id uuid, claimed boolean, item_status text, item_attempts int, item_retryable boolean, item_failures int)
language plpgsql
set search_path = public
as $$
declare
  v_id uuid;
  v_attempts int;
  v_failures int;
begin
  insert into public.scan_batch_items (batch_id, station_id, file_sha256, filename, task_id, status)
  values (p_batch_id, p_station_id, p_file_sha256, p_filename, p_task_id, 'processing')
  on conflict (batch_id, file_sha256) do nothing
  returning id into v_id;

  if v_id is not null then
    return query select v_id, true, 'processing'::text, 1, true, 0;
    return;
  end if;

  update public.scan_batch_items
     set status = 'processing',
         attempts = attempts + 1,
         filename = coalesce(p_filename, filename),
         updated_at = now()
   where batch_id = p_batch_id
     and file_sha256 = p_file_sha256
     and ((status = 'error' and retryable)
          or (status = 'processing' and updated_at < now() - make_interval(secs => p_stale_seconds)))
  returning id, attempts, pipeline_failures into v_id, v_attempts, v_failures;

  if v_id is not null then
    return query select v_id, true, 'processing'::text, v_attempts, true, v_failures;
    return;
  end if;

  return query
    select i.id, false, i.status, i.attempts, i.retryable, i.pipeline_failures
      from public.scan_batch_items i
     where i.batch_id = p_batch_id and i.file_sha256 = p_file_sha256;
end;
$$;

-- ---------------------------------------------------------------------------
-- 6. Record the pipeline result for a claimed page and bump the matching
--    batch counter in the same transaction. Only transitions rows that are
--    still 'processing', so a result can never be counted twice.
-- ---------------------------------------------------------------------------
create or replace function public.complete_batch_item(
  p_item_id uuid,
  p_status text,
  p_review_reasons text[] default '{}',
  p_identity jsonb default null,
  p_message text default null,
  p_simulated boolean default false,
  p_retryable boolean default true,
  p_count_failure boolean default false
)
returns boolean
language plpgsql
set search_path = public
as $$
declare
  v_batch uuid;
  v_field text;
begin
  if p_status not in ('success','flagged','rejected','duplicate','error') then
    raise exception 'invalid item status: %', p_status;
  end if;

  update public.scan_batch_items
     set status = p_status,
         review_reasons = coalesce(p_review_reasons, '{}'),
         identity = p_identity,
         message = left(p_message, 1000),
         simulated = coalesce(p_simulated, false),
         retryable = (p_status <> 'error' or coalesce(p_retryable, true)),
         pipeline_failures = pipeline_failures + (coalesce(p_count_failure, false) and p_status = 'error')::int,
         updated_at = now()
   where id = p_item_id and status = 'processing'
  returning batch_id into v_batch;

  if v_batch is null then
    return false;
  end if;

  v_field := case p_status
    when 'success'   then 'processed_count'
    when 'flagged'   then 'flagged_count'
    when 'rejected'  then 'rejected_count'
    when 'duplicate' then 'duplicate_count'
    else null
  end;

  if v_field is not null then
    perform public.increment_batch_counter(v_batch, v_field);
  end if;
  return true;
end;
$$;

-- Backend-only functions: not callable by anon / authenticated users.
revoke all on function public.increment_batch_counter(uuid, text) from public, anon, authenticated;
revoke all on function public.claim_batch_item(uuid, uuid, text, text, text, int) from public, anon, authenticated;
revoke all on function public.complete_batch_item(uuid, text, text[], jsonb, text, boolean, boolean, boolean) from public, anon, authenticated;
grant execute on function public.increment_batch_counter(uuid, text) to service_role;
grant execute on function public.claim_batch_item(uuid, uuid, text, text, text, int) to service_role;
grant execute on function public.complete_batch_item(uuid, text, text[], jsonb, text, boolean, boolean, boolean) to service_role;

-- ---------------------------------------------------------------------------
-- 7. Instructor search: only instructors (department_table role FULL-TIME /
--    PART-TIME / DEPARTMENT_HEAD) and placeholders. Same signature and return
--    columns as 001 so "create or replace" works. One row per instructor
--    (primary department preferred). Substring + trigram matching.
-- ---------------------------------------------------------------------------
create or replace function public.search_instructors(query text)
returns table(id uuid, first_name text, last_name text, department_id bigint, is_placeholder boolean, disabled boolean, score real)
language sql stable
set search_path = public
as $$
  with q as (select btrim(coalesce(query, '')) as v),
  candidates as (
    select ui.id, ui.first_name, ui.last_name, ui.is_placeholder, ui.disabled,
           coalesce(ui.first_name,'') || ' ' || coalesce(ui.last_name,'') as full_name
      from public.user_info ui
     where ui.is_placeholder = true
        or (
             coalesce(ui.disabled, false) = false
             and lower(coalesce(ui.account_status, '')) <> 'disabled'
             and exists (
               select 1
                 from public.department_table dt
                 join public.roles r on r.id = dt.roles
                where dt.user_id = ui.id
                  and r."Roles" in ('FULL-TIME','PART-TIME','DEPARTMENT_HEAD')
             )
           )
  ),
  scored as (
    select c.*,
           greatest(similarity(c.full_name, q.v), word_similarity(q.v, c.full_name),
                    case when c.full_name ilike '%' || q.v || '%' then 0.9 else 0 end)::real as score
      from candidates c, q
     where length(q.v) >= 2
  )
  select s.id, s.first_name, s.last_name,
         (select idp.department_id
            from public.instructor_departments idp
           where idp.instructor_id = s.id
           order by idp.is_primary desc, idp.id
           limit 1) as department_id,
         s.is_placeholder, s.disabled, s.score
    from scored s
   where s.score >= 0.3
   order by s.score desc, s.last_name, s.first_name
   limit 15;
$$;

-- ---------------------------------------------------------------------------
-- 8. MANDATORY lock-down of the batch-scanning tables.
--    Migration 001 created policies letting ANY authenticated user (signup is
--    open with auto-confirm) read/write stations and scan_batches through the
--    Data API - e.g. rewrite a batch's instructor_id or a station's key_hash.
--    The Flutter app does not use these tables; only the backend (service
--    role, which bypasses RLS) may touch them.
-- ---------------------------------------------------------------------------
drop policy if exists "staff can manage stations" on public.stations;
drop policy if exists "staff can manage batches" on public.scan_batches;
alter table public.stations enable row level security;
alter table public.scan_batches enable row level security;
revoke all on table public.stations, public.scan_batches from anon, authenticated;

commit;

-- ---------------------------------------------------------------------------
-- OPTIONAL - NOT enabled by default. Defense in depth for n8n writes: one
-- result row per task_id in the scoring table. sast_all_raw_data_survey is a
-- SHARED table also used by the Flutter app. Before enabling, check for
-- existing duplicate task_ids (the create fails if any exist) and confirm no
-- Flutter flow legitimately writes several rows per task_id:
--
--   select task_id, count(*) from public.sast_all_raw_data_survey
--    where task_id is not null group by task_id having count(*) > 1;
--
--   create unique index if not exists sast_all_raw_data_survey_task_id_key
--     on public.sast_all_raw_data_survey (task_id)
--     where task_id is not null;
-- ---------------------------------------------------------------------------

