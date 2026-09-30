-- 007 AUDIT (read-only). Run these BEFORE 007_security_hardening.sql.
--
-- Purpose: find accounts that may already have used the self-signup hole
--   (client could INSERT its own user_info row with account_status='approved',
--    and self-insert / self-update a Sao_users row with role SAO_STAFF).
--
-- Every statement here is a plain SELECT. Nothing is changed.
-- Run in the Supabase SQL Editor (it runs as postgres, so auth.users is readable).
--
-- How to read the trail columns:
--   * Approvals through the app go via the admin-accept-user edge function, which writes
--     audit_logs action='USER_STATUS_CHANGED', metadata.target_user = <uuid>, new_status.
--   * Admin-created accounts write 'SAO_USER_CREATED' (metadata.target) /
--     'ACADEMIC_USER_CREATED' (metadata.target) / 'USER_CREATED' (metadata.new_user_email).
--   * Role changes write 'ROLE_UPDATED' (metadata.target = uuid, metadata.new_role).
--   * audit_logs only starts 2026-05-30, and USER_STATUS_CHANGED only from 2026-09-02, so an
--     account approved before that has no trail for innocent reasons. Judge those by hand.


-- Q1. Headline counts --------------------------------------------------------------------
select
  (select count(*) from public.user_info
     where lower(btrim(coalesce(account_status, ''))) = 'approved')                 as approved_accounts,
  (select count(*) from public."Sao_users")                                          as sao_users_rows,
  (select count(distinct su.user_id) from public."Sao_users" su
     join public.roles r on r.id = su.role_id
     where r."Roles" in ('SAO_ADMIN', 'SAO_STAFF'))                                  as sao_role_holders,
  (select count(distinct su.user_id) from public."Sao_users" su
     join public.roles r on r.id = su.role_id
     join public.user_info ui on ui.id = su.user_id
     where r."Roles" in ('SAO_ADMIN', 'SAO_STAFF')
       and lower(btrim(coalesce(ui.account_status, ''))) = 'approved'
       and coalesce(ui.disabled, false) = false)                                     as website_staff_eligible;


-- Q2. Every SAO_ADMIN / SAO_STAFF holder with its approval / creation trail -------------
-- Look hardest at rows where suspicious_reasons is not empty.
with approvals as (
  select metadata ->> 'target_user'                                              as target,
         max(created_at) filter (where metadata ->> 'new_status' = 'approved')  as last_approved_at,
         (array_agg(user_id order by created_at desc)
            filter (where metadata ->> 'new_status' = 'approved'))[1]           as approved_by
  from public.audit_logs
  where action = 'USER_STATUS_CHANGED'
  group by 1
),
creations as (
  select lower(coalesce(metadata ->> 'target', metadata ->> 'new_user_email')) as target,
         action, user_id as created_by, created_at
  from public.audit_logs
  where action in ('SAO_USER_CREATED', 'ACADEMIC_USER_CREATED', 'USER_CREATED')
),
role_changes as (
  select metadata ->> 'target' as target,
         max(created_at)       as last_role_change,
         (array_agg(metadata ->> 'new_role' order by created_at desc))[1] as last_new_role,
         (array_agg(user_id order by created_at desc))[1]                 as changed_by
  from public.audit_logs
  where action = 'ROLE_UPDATED'
  group by 1
)
select
  su.user_id,
  au.email,
  au.created_at                         as auth_created_at,
  au.email_confirmed_at,
  au.last_sign_in_at,
  au.banned_until,
  ui.created_at                         as user_info_created_at,
  ui.account_status,
  ui.disabled,
  r."Roles"                             as sao_role,
  su.created_at                         as sao_row_created_at,
  dr."Roles"                            as also_department_role,
  ap.last_approved_at,
  apu.email                             as approved_by_email,
  cr.action                             as created_via,
  cru.email                             as created_by_email,
  rc.last_role_change,
  rc.last_new_role,
  rcu.email                             as role_changed_by_email,
  array_remove(array[
    case when ap.last_approved_at is null and cr.action is null and rc.last_role_change is null
         then 'no_admin_trail' end,
    case when lower(btrim(coalesce(ui.account_status, ''))) = 'approved'
          and ap.last_approved_at is null and cr.action is null
          and au.created_at >= timestamptz '2026-09-02'
         then 'approved_after_2026-09-02_without_approval_log' end,
    case when su.created_at > au.created_at + interval '15 minutes'
          and cr.action is null
          and (rc.last_role_change is null or rc.last_new_role is distinct from r."Roles")
         then 'sao_row_added_after_signup_without_matching_role_change' end,
    case when dr."Roles" is not null then 'also_has_department_role' end,
    case when au.created_at > now() - interval '30 days' then 'created_last_30_days' end,
    case when ui.id is null then 'no_user_info_row' end
  ], null)                              as suspicious_reasons
from public."Sao_users" su
left join public.roles r         on r.id = su.role_id
left join auth.users au          on au.id = su.user_id
left join public.user_info ui    on ui.id = su.user_id
left join public.department_table dt on dt.user_id = su.user_id
left join public.roles dr        on dr.id = dt.roles
left join approvals ap           on ap.target = su.user_id::text
left join auth.users apu         on apu.id = ap.approved_by
left join lateral (
  select c.* from creations c
  where c.target in (lower(su.user_id::text), lower(au.email))
  order by c.created_at desc limit 1
) cr on true
left join auth.users cru         on cru.id = cr.created_by
left join role_changes rc        on rc.target = su.user_id::text
left join auth.users rcu         on rcu.id = rc.changed_by
order by cardinality(array_remove(array[
    case when ap.last_approved_at is null and cr.action is null and rc.last_role_change is null then 1 end,
    case when dr."Roles" is not null then 1 end], null)) desc,
  au.created_at desc;


-- Q3. Approved accounts of ANY role with no approval / creation trail --------------------
-- (Instructors/dept heads too: a self-approved instructor can then self-insert SAO_STAFF.)
select
  ui.id, au.email, au.created_at as auth_created_at, au.last_sign_in_at,
  ui.account_status, ui.disabled,
  coalesce(sr."Roles", dr."Roles") as role,
  case when sr."Roles" is not null then 'Sao_users' when dr."Roles" is not null then 'department_table' else 'none' end as role_source
from public.user_info ui
left join auth.users au on au.id = ui.id
left join public."Sao_users" su on su.user_id = ui.id
left join public.roles sr on sr.id = su.role_id
left join public.department_table dt on dt.user_id = ui.id
left join public.roles dr on dr.id = dt.roles
where lower(btrim(coalesce(ui.account_status, ''))) = 'approved'
  and coalesce(ui.is_placeholder, false) = false
  and not exists (select 1 from public.audit_logs a
                  where a.action = 'USER_STATUS_CHANGED'
                    and a.metadata ->> 'target_user' = ui.id::text
                    and a.metadata ->> 'new_status' = 'approved')
  and not exists (select 1 from public.audit_logs a
                  where a.action in ('SAO_USER_CREATED', 'ACADEMIC_USER_CREATED', 'USER_CREATED')
                    and lower(coalesce(a.metadata ->> 'target', a.metadata ->> 'new_user_email'))
                        in (lower(ui.id::text), lower(au.email)))
order by au.created_at desc;


-- Q4. Structural oddities that only a direct client write would produce ------------------
-- 4a. users holding BOTH a Sao_users row and a department_table row
select su.user_id, au.email, sr."Roles" as sao_role, dr."Roles" as dept_role, su.created_at as sao_row_created_at
from public."Sao_users" su
join public.department_table dt on dt.user_id = su.user_id
left join public.roles sr on sr.id = su.role_id
left join public.roles dr on dr.id = dt.roles
left join auth.users au on au.id = su.user_id;

-- 4b. users with more than one Sao_users row, or a Sao_users row with a non-SAO role
select su.user_id, au.email, array_agg(r."Roles" order by su.id) as roles, count(*) as n
from public."Sao_users" su
left join public.roles r on r.id = su.role_id
left join auth.users au on au.id = su.user_id
group by su.user_id, au.email
having count(*) > 1 or bool_or(coalesce(r."Roles", '') not in ('SAO_ADMIN', 'SAO_STAFF'));

-- 4c. user_info flags a client could have set on itself (is_admin is unused today, but should be false)
select ui.id, au.email, ui.account_status, ui.disabled, ui.is_admin, ui.is_placeholder
from public.user_info ui
left join auth.users au on au.id = ui.id
where ui.is_admin = true
   or (ui.is_placeholder = true and au.id is not null and au.last_sign_in_at is not null);

-- 4d. account_status values outside what the app writes
select account_status, count(*)
from public.user_info
where lower(btrim(coalesce(account_status, ''))) not in
      ('pending', 'approved', 'rejected', 'suspended', 'disabled', 'deleted')
group by 1;
