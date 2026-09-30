-- Batch scanning feature: new tables + columns.
-- Review before applying. Not auto-applied to the live project.

-- 1. Stations = one row per scanning laptop.
create table if not exists public.stations (
  id uuid primary key default gen_random_uuid(),
  label text not null unique,               -- e.g. "Laptop 1"
  folder_path text,                          -- where the scanner drops images on that machine
  current_staff_id uuid references public.user_info(id),
  updated_at timestamptz not null default now()
);

-- 2. Batches = one scanning session: one instructor + subject + station.
create table if not exists public.scan_batches (
  id uuid primary key default gen_random_uuid(),
  station_id uuid not null references public.stations(id),
  instructor_id uuid not null references public.user_info(id),
  subject_id uuid not null references public.subjects(id),
  term_id uuid references public.academic_terms(id),
  status text not null default 'open' check (status in ('open','closed')),
  processed_count int not null default 0,
  flagged_count int not null default 0,
  pending_count int not null default 0,
  created_by uuid references public.user_info(id),
  created_at timestamptz not null default now(),
  closed_at timestamptz,
  closed_by uuid references public.user_info(id),
  force_closed boolean not null default false
);

create unique index if not exists one_open_batch_per_station
  on public.scan_batches (station_id)
  where status = 'open';

-- 3. Instructor placeholder/disabled flags (kept separate from account_status,
--    which existing app logic already relies on).
alter table public.user_info
  add column if not exists is_placeholder boolean not null default false,
  add column if not exists disabled boolean not null default false,
  add column if not exists is_admin boolean not null default false;

-- 4. Link failed_scan_queue rows back to the batch they came from.
alter table public.failed_scan_queue
  add column if not exists batch_id uuid references public.scan_batches(id);

alter table public.stations enable row level security;
alter table public.scan_batches enable row level security;

-- Minimal policy: any authenticated staff (Sao_users) can read/write.
-- Tighten this later if you want per-role restrictions.
drop policy if exists "staff can manage stations" on public.stations;
create policy "staff can manage stations" on public.stations
  for all using (auth.role() = 'authenticated') with check (auth.role() = 'authenticated');

drop policy if exists "staff can manage batches" on public.scan_batches;
create policy "staff can manage batches" on public.scan_batches
  for all using (auth.role() = 'authenticated') with check (auth.role() = 'authenticated');

-- 5. Fuzzy instructor search for the batch-setup screen (reuses pg_trgm,
--    already enabled -- the existing n8n workflow depends on it too).
create extension if not exists pg_trgm;

create or replace function public.search_instructors(query text)
returns table(id uuid, first_name text, last_name text, department_id bigint, is_placeholder boolean, disabled boolean, score real)
language sql stable as $$
  select ui.id, ui.first_name, ui.last_name, id2.department_id, ui.is_placeholder, ui.disabled,
         similarity(coalesce(ui.first_name,'') || ' ' || coalesce(ui.last_name,''), query) as score
  from public.user_info ui
  left join public.instructor_departments id2 on id2.instructor_id = ui.id
  where query <> ''
    and similarity(coalesce(ui.first_name,'') || ' ' || coalesce(ui.last_name,''), query) >= 0.25
  order by score desc
  limit 10;
$$;

-- Used before creating a new placeholder instructor, to block near-duplicates.
create or replace function public.find_duplicate_instructor(p_first text, p_last text)
returns table(id uuid, first_name text, last_name text, disabled boolean, score real)
language sql stable as $$
  select ui.id, ui.first_name, ui.last_name, ui.disabled,
         similarity(coalesce(ui.first_name,'') || ' ' || coalesce(ui.last_name,''), p_first || ' ' || p_last) as score
  from public.user_info ui
  where similarity(coalesce(ui.first_name,'') || ' ' || coalesce(ui.last_name,''), p_first || ' ' || p_last) >= 0.60
  order by score desc
  limit 1;
$$;

-- After applying, mark whichever staff account(s) should be able to
-- activate instructor accounts (only SAO admins should be able to do this):
--   update public.user_info set is_admin = true where email = 'admin@example.com';
