-- 007: security hardening (self-signup privilege escalation, open views, import_errors RLS).
--
-- Review before applying. NOT auto-applied. Run 007_audit_queries.sql FIRST, then paste this
-- whole file into the Supabase SQL Editor and run it once. One transaction: all or nothing.
-- Safe to re-run (create or replace / drop ... if exists).
--
-- Flutter compatibility (C:\Projects\ievaluateapp_final, lib/core/services/auth_service.dart):
--   signUp() inserts, as the new user's own JWT, in this order:
--     1. user_info  {id, first_name, last_name, address, email, account_status:'pending',
--                    university_id, employment_status}
--     2. SAO path:     Sao_users        {user_id, role_id = SAO_STAFF}
--        other roles:  department_table {user_id, Department_name_ID, roles}
--   Approval / role changes / admin-created accounts all go through edge functions
--   (admin-accept-user, admin-update-role, admin-create-user, admin-create-academic) that use
--   the service role. The website backend and n8n use the service role too.
--   So: every legit client-side write above still works; everything else that grants
--   privilege must come from the service role, the SQL editor, or an approved SAO_ADMIN.


begin;


-- 0. Helpers -----------------------------------------------------------------------------

-- True for the service role (edge functions, website backend, n8n) and for sessions with no
-- JWT that did not come through PostgREST (SQL editor, psql, pg_cron). Not SECURITY DEFINER on
-- purpose: it only reads session state. session_user is used because current_user is the
-- function owner inside the SECURITY DEFINER triggers below.
create or replace function public.is_trusted_db_caller()
returns boolean
language plpgsql
stable
set search_path = public
as $$
declare
  v_claims text := nullif(current_setting('request.jwt.claims', true), '');
  v_role   text := nullif(current_setting('request.jwt.claim.role', true), '');
begin
  if v_role is null and v_claims is not null then
    v_role := v_claims::jsonb ->> 'role';
  end if;
  if v_role = 'service_role' then
    return true;
  end if;
  return v_role is null
     and session_user not in ('authenticator', 'anon', 'authenticated');
end $$;

revoke all on function public.is_trusted_db_caller() from public, anon, authenticated;

-- Approved, not disabled, SAO_ADMIN or SAO_STAFF. Same rule as the website's resolve_staff().
create or replace function public.is_approved_sao_staff()
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1
    from public."Sao_users" su
    join public.roles r      on r.id = su.role_id
    join public.user_info ui on ui.id = su.user_id
    where su.user_id = auth.uid()
      and upper(btrim(r."Roles")) in ('SAO_ADMIN', 'SAO_STAFF')
      and lower(btrim(coalesce(ui.account_status, ''))) = 'approved'
      and coalesce(ui.disabled, false) = false
  );
$$;

-- Same, SAO_ADMIN only. (Existing is_sao_admin() ignores account_status; left as is.)
create or replace function public.is_approved_sao_admin()
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1
    from public."Sao_users" su
    join public.roles r      on r.id = su.role_id
    join public.user_info ui on ui.id = su.user_id
    where su.user_id = auth.uid()
      and upper(btrim(r."Roles")) = 'SAO_ADMIN'
      and lower(btrim(coalesce(ui.account_status, ''))) = 'approved'
      and coalesce(ui.disabled, false) = false
  );
$$;

revoke all on function public.is_approved_sao_staff() from public, anon;
revoke all on function public.is_approved_sao_admin() from public, anon;
grant execute on function public.is_approved_sao_staff() to authenticated, service_role;
grant execute on function public.is_approved_sao_admin() to authenticated, service_role;


-- 1. Views: run with the caller's RLS, and no direct API access --------------------------
-- Only the service role reads them (website backend, n8n/tools/ocr_eval.py); no Flutter
-- screen reads either view. NOTE: a re-check on 2026-09-29 found this section already applied
-- live (security_invoker=true, anon/authenticated revoked); it is kept here as a no-op re-run.
-- Re-running Flutter migration 20240130000024 or our 006 (create or replace view without
-- "with (security_invoker = true)") can undo it - add that clause to those files.
alter view public.scan_review_queue  set (security_invoker = true);
alter view public.ocr_correction_log set (security_invoker = true);

revoke all on public.scan_review_queue  from anon, authenticated, public;
revoke all on public.ocr_correction_log from anon, authenticated, public;
grant select on public.scan_review_queue  to service_role;
grant select on public.ocr_correction_log to service_role;


-- 2. user_info: a client INSERT always lands as a pending, unprivileged account ----------
create or replace function public.enforce_user_info_insert_defaults()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if public.is_trusted_db_caller() or public.is_approved_sao_admin() then
    return new;
  end if;

  new.account_status := 'pending';   -- Flutter already sends 'pending'
  new.disabled       := false;
  new.is_admin       := false;
  new.is_placeholder := false;
  return new;
end $$;

drop trigger if exists trg_user_info_insert_defaults on public.user_info;
create trigger trg_user_info_insert_defaults
  before insert on public.user_info
  for each row execute function public.enforce_user_info_insert_defaults();

-- 2b. user_info UPDATE guard: also lock disabled / is_admin / is_placeholder (old current_user = 'service_role' check never matched inside SECURITY DEFINER; auth.role() = 'service_role' did). No Flutter screen updates these flags.
create or replace function public.protect_user_info_fields()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if public.is_trusted_db_caller() or public.is_approved_sao_admin() then
    return new;
  end if;

  if new.account_status is distinct from old.account_status then
    raise exception 'Forbidden: You cannot modify your own account status.';
  end if;

  if new.university_id is distinct from old.university_id then
    raise exception 'Forbidden: You cannot modify your university ID.';
  end if;

  if new.email is distinct from old.email then
    raise exception 'Forbidden: Primary email address cannot be changed directly.';
  end if;

  if new.id is distinct from old.id then
    raise exception 'Forbidden: User ID is immutable.';
  end if;

  if new.disabled is distinct from old.disabled
     or new.is_admin is distinct from old.is_admin
     or new.is_placeholder is distinct from old.is_placeholder then
    raise exception 'Forbidden: You cannot modify account flags.';
  end if;

  return new;
end $$;
-- (trigger trg_protect_user_info_fields already points at this function; unchanged)


-- 3. Sao_users: keep Flutter's self-registration insert, block everything else ----------
-- Why not block client SAO inserts outright: Flutter signUp() self-inserts SAO_STAFF, so the
-- row is allowed ONLY for a brand-new pending account (forced pending by section 2) and only
-- becomes useful after an admin approves it via the service role.
create or replace function public.protect_sao_users_insert()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  v_role   text;
  v_status text;
begin
  if public.is_trusted_db_caller() or public.is_approved_sao_admin() then
    return new;
  end if;

  select upper(btrim(r."Roles")) into v_role from public.roles r where r.id = new.role_id;

  if coalesce(v_role, '') = 'SAO_ADMIN' then
    raise exception 'Forbidden: SAO_ADMIN accounts must be created by an existing administrator.';
  end if;

  if tg_op = 'UPDATE' then
    if new.role_id is distinct from old.role_id or new.user_id is distinct from old.user_id then
      raise exception 'Forbidden: SAO roles can only be changed by an administrator.';
    end if;
    return new;
  end if;

  -- INSERT: only the signup step for the caller's own, still-pending account.
  if new.user_id is distinct from auth.uid() then
    raise exception 'Forbidden: you can only register yourself.';
  end if;

  if coalesce(v_role, '') <> 'SAO_STAFF' then
    raise exception 'Forbidden: invalid role for SAO self-registration.';
  end if;

  select lower(btrim(coalesce(ui.account_status, ''))) into v_status
  from public.user_info ui where ui.id = new.user_id;

  if v_status is distinct from 'pending' then
    raise exception 'Forbidden: only a new, pending registration can request an SAO role.';
  end if;

  if exists (select 1 from public."Sao_users" s where s.user_id = new.user_id)
     or exists (select 1 from public.department_table d where d.user_id = new.user_id) then
    raise exception 'Forbidden: this account already has a role.';
  end if;

  return new;
end $$;
-- (trigger trg_protect_sao_users_insert, BEFORE INSERT OR UPDATE, already uses it; unchanged)

-- 3b. department_table: same rule, so a pending SAO signup cannot add a second role, and an
-- approved instructor cannot self-insert another department / DEPARTMENT_HEAD row.
-- Flutter only inserts department_table in signUp(); admins use edge functions.
create or replace function public.guard_department_table_self_insert()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  v_status text;
begin
  if public.is_trusted_db_caller() or public.is_approved_sao_admin() then
    return new;
  end if;

  if new.user_id is distinct from auth.uid() then
    raise exception 'Forbidden: you can only register yourself.';
  end if;

  if exists (select 1 from public.roles r where r.id = new.roles and upper(btrim(r."Roles")) like 'SAO%') then
    raise exception 'Forbidden: invalid role.';
  end if;

  select lower(btrim(coalesce(ui.account_status, ''))) into v_status
  from public.user_info ui where ui.id = new.user_id;

  if v_status is distinct from 'pending' then
    raise exception 'Forbidden: only a new, pending registration can choose a department role.';
  end if;

  if exists (select 1 from public.department_table d where d.user_id = new.user_id)
     or exists (select 1 from public."Sao_users" s where s.user_id = new.user_id) then
    raise exception 'Forbidden: this account already has a role.';
  end if;

  return new;
end $$;

drop trigger if exists trg_guard_department_table_self_insert on public.department_table;
create trigger trg_guard_department_table_self_insert
  before insert on public.department_table
  for each row execute function public.guard_department_table_self_insert();


-- 4. import_errors: approved SAO staff/admins only (was: any authenticated user) ---------
-- Flutter readers: gatherer/data_validation_screen.dart (SAO_STAFF), sao_admin/import_errors_*
-- (SAO_ADMIN) - all approved. n8n and the website write with the service role (bypasses RLS).
drop policy if exists "Staff can view import errors"                on public.import_errors;
drop policy if exists "Staff can update import errors"              on public.import_errors;
drop policy if exists "import_errors: approved sao staff can read"   on public.import_errors;
drop policy if exists "import_errors: approved sao staff can update" on public.import_errors;

create policy "import_errors: approved sao staff can read"
  on public.import_errors for select to authenticated
  using ((select public.is_approved_sao_staff()));

create policy "import_errors: approved sao staff can update"
  on public.import_errors for update to authenticated
  using ((select public.is_approved_sao_staff()))
  with check ((select public.is_approved_sao_staff()));

drop policy if exists "SAO office deletes import_errors" on public.import_errors;

create policy "SAO office deletes import_errors"
  on public.import_errors for delete to authenticated
  using ((select public.is_approved_sao_staff()));

-- is_sao() is intentionally NOT changed: nearly every policy that uses it has a twin policy
-- with an inline "exists (select 1 from Sao_users ...)" check, so tightening is_sao() alone
-- would not close anything. Follow-up: make all SAO policies call is_approved_sao_staff().


commit;


-- Checks afterwards (read-only):
-- select relname, reloptions from pg_class where relname in ('scan_review_queue','ocr_correction_log');
-- select grantee, privilege_type from information_schema.role_table_grants
--   where table_schema='public' and table_name in ('scan_review_queue','ocr_correction_log');
-- select policyname, cmd, qual, with_check from pg_policies where tablename = 'import_errors';
-- select tgname from pg_trigger where tgrelid in ('public.user_info'::regclass, 'public.department_table'::regclass,
--   'public."Sao_users"'::regclass) and not tgisinternal;
-- Then do one test signup of each kind from the Flutter app (SAO staff + instructor), approve
-- them from the admin screen, and log in to the website / app.


-- =========================================================================================
-- ROLLBACK (restores the state read from the live DB on 2026-09-29). Uncomment and run.
-- =========================================================================================
-- begin;
--
-- -- 4. import_errors policies
-- drop policy if exists "import_errors: approved sao staff can read"   on public.import_errors;
-- drop policy if exists "import_errors: approved sao staff can update" on public.import_errors;
-- drop policy if exists "SAO office deletes import_errors" on public.import_errors;
-- create policy "Staff can view import errors" on public.import_errors
--   for select to public using (auth.role() = 'authenticated'::text);
-- create policy "Staff can update import errors" on public.import_errors
--   for update to public using (auth.role() = 'authenticated'::text);
-- -- TODO: restore original "SAO office deletes import_errors" definition from pg_policies before rollback
--
-- -- 3b. department_table guard
-- drop trigger if exists trg_guard_department_table_self_insert on public.department_table;
-- drop function if exists public.guard_department_table_self_insert();
--
-- -- 3. Sao_users trigger function (original body)
-- create or replace function public.protect_sao_users_insert()
--  returns trigger
--  language plpgsql
--  security definer
--  set search_path to 'public'
-- as $function$
-- DECLARE
--   v_role_name text;
-- BEGIN
--   -- Service role always allowed
--   IF current_user = 'service_role' OR auth.role() = 'service_role' THEN
--     RETURN NEW;
--   END IF;
--
--   SELECT r."Roles" INTO v_role_name FROM public.roles r WHERE r.id = NEW.role_id;
--
--   IF UPPER(COALESCE(v_role_name, '')) = 'SAO_ADMIN' AND NOT public.is_sao_admin() THEN
--     RAISE EXCEPTION 'Forbidden: SAO_ADMIN accounts must be created by an existing administrator.';
--   END IF;
--
--   RETURN NEW;
-- END;
-- $function$;
--
-- -- 2b. user_info UPDATE guard (original body)
-- create or replace function public.protect_user_info_fields()
--  returns trigger
--  language plpgsql
--  security definer
--  set search_path to 'public'
-- as $function$
-- BEGIN
--   -- Service role always allowed
--   IF current_user = 'service_role' OR auth.role() = 'service_role' THEN
--     RETURN NEW;
--   END IF;
--
--   -- If caller is not an SAO admin, prevent mutating sensitive governance fields
--   IF NOT public.is_sao_admin() THEN
--     IF NEW.account_status IS DISTINCT FROM OLD.account_status THEN
--       RAISE EXCEPTION 'Forbidden: You cannot modify your own account status.';
--     END IF;
--
--     IF NEW.university_id IS DISTINCT FROM OLD.university_id THEN
--       RAISE EXCEPTION 'Forbidden: You cannot modify your university ID.';
--     END IF;
--
--     IF NEW.email IS DISTINCT FROM OLD.email THEN
--       RAISE EXCEPTION 'Forbidden: Primary email address cannot be changed directly.';
--     END IF;
--
--     IF NEW.id IS DISTINCT FROM OLD.id THEN
--       RAISE EXCEPTION 'Forbidden: User ID is immutable.';
--     END IF;
--   END IF;
--
--   RETURN NEW;
-- END;
-- $function$;
--
-- -- 2. user_info INSERT trigger
-- drop trigger if exists trg_user_info_insert_defaults on public.user_info;
-- drop function if exists public.enforce_user_info_insert_defaults();
--
-- -- Do NOT run this view block: it would re-expose the views to anon.
-- -- 1. views (state first seen 2026-09-29: no reloptions, ALL granted to anon + authenticated;
-- --    a later re-check showed it already hardened by hand - skip this block if that was deliberate)
-- alter view public.scan_review_queue  reset (security_invoker);
-- alter view public.ocr_correction_log reset (security_invoker);
-- grant all on public.scan_review_queue  to anon, authenticated;
-- grant all on public.ocr_correction_log to anon, authenticated;
--
-- -- 0. helpers (after everything that references them is gone)
-- drop function if exists public.is_approved_sao_staff();
-- drop function if exists public.is_approved_sao_admin();
-- drop function if exists public.is_trusted_db_caller();
--
-- commit;
