-- ---------------------------------------------------------------------------
-- 011: instructor name matching.
--   - search_instructors: a row where every typed word starts a word of the
--     name ("rodz licayan" -> "Rodz Harvey d Licayan") now scores above a row
--     that only shares the surname ("Ronald Kinggg licayan").
--   - find_duplicate_instructor: same logic as 001, with a fixed search_path.
--     The backend now does the precise similar-name check itself; this stays
--     as a spelling-mistake safety net.
--
-- Review before applying. Not auto-applied. Run in the Supabase SQL Editor
-- after 010. Safe to re-run (create or replace).
-- ---------------------------------------------------------------------------
begin;

create or replace function public.search_instructors(query text)
returns table(id uuid, first_name text, last_name text, department_id bigint, is_placeholder boolean, disabled boolean, score real)
language sql stable
set search_path = public
as $$
  with q as (
    select btrim(coalesce(query, '')) as v,
           array_remove(regexp_split_to_array(
             lower(btrim(regexp_replace(coalesce(query, ''), '[^[:alnum:]]+', ' ', 'g'))), ' '), '') as toks
  ),
  candidates as (
    select ui.id, ui.first_name, ui.last_name, ui.is_placeholder, ui.disabled,
           coalesce(ui.first_name,'') || ' ' || coalesce(ui.last_name,'') as full_name,
           array_remove(regexp_split_to_array(
             lower(btrim(regexp_replace(coalesce(ui.first_name,'') || ' ' || coalesce(ui.last_name,''),
                                        '[^[:alnum:]]+', ' ', 'g'))), ' '), '') as name_toks
      from public.user_info ui
     where ui.is_placeholder = true
        or (
             coalesce(ui.disabled, false) = false
             and lower(coalesce(ui.account_status, '')) <> 'disabled'
             and exists (
               select 1 from public.department_table dt
                 join public.roles r on r.id = dt.roles
                where dt.user_id = ui.id
                  and r."Roles" in ('FULL-TIME','PART-TIME','DEPARTMENT_HEAD')
             )
           )
  ),
  scored as (
    select c.*,
           greatest(
             similarity(c.full_name, q.v),
             word_similarity(q.v, c.full_name),
             case when c.full_name ilike '%' || q.v || '%' then 0.9 else 0 end,
             -- every typed word is the start of some word of the name
             case when cardinality(q.toks) > 0
                   and not exists (select 1 from unnest(q.toks) t
                                    where not exists (select 1 from unnest(c.name_toks) n
                                                       where left(n, length(t)) = t))
                  then case when cardinality(q.toks) >= 2 then 0.95 else 0.85 end
                  else 0 end
           )::real as score
      from candidates c, q
     where length(q.v) >= 2
  )
  select s.id, s.first_name, s.last_name,
         (select idp.department_id from public.instructor_departments idp
           where idp.instructor_id = s.id order by idp.is_primary desc, idp.id limit 1) as department_id,
         s.is_placeholder, s.disabled, s.score
    from scored s
   where s.score >= 0.3
   order by s.score desc, s.last_name, s.first_name
   limit 15;
$$;

-- Unchanged logic (spelling-mistake net for the backend); fixed search_path.
create or replace function public.find_duplicate_instructor(p_first text, p_last text)
returns table(id uuid, first_name text, last_name text, disabled boolean, score real)
language sql stable
set search_path = public
as $$
  select ui.id, ui.first_name, ui.last_name, ui.disabled,
         similarity(coalesce(ui.first_name,'') || ' ' || coalesce(ui.last_name,''), p_first || ' ' || p_last) as score
  from public.user_info ui
  where similarity(coalesce(ui.first_name,'') || ' ' || coalesce(ui.last_name,''), p_first || ' ' || p_last) >= 0.60
  order by score desc
  limit 1;
$$;

-- Only the website backend (service role) calls these; Flutter and n8n do not.
revoke all on function public.search_instructors(text) from public, anon, authenticated;
revoke all on function public.find_duplicate_instructor(text, text) from public, anon, authenticated;
grant execute on function public.search_instructors(text) to service_role;
grant execute on function public.find_duplicate_instructor(text, text) to service_role;

commit;

-- Check: select first_name, last_name, score from public.search_instructors('rodz licayan');
--   expect 'Rodz Harvey d Licayan' 0.95 above 'Ronald Kinggg licayan' 0.615.
-- Rollback: re-run section 7 of 002 and the find_duplicate_instructor block of 001;
--   grant execute on both to anon, authenticated.
