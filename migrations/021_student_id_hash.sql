-- ---------------------------------------------------------------------------
-- 021: keyed hash of the student ID on stored SAST forms (duplicate detection + reporting).
--
-- Rule (2026-11-13): SAO staff still SEE the real student ID (website review, page viewer, Flutter
-- gatherer screens), so sast_all_raw_data_survey.student_id stays plaintext. A new column
-- student_id_hash = 'h1:' || hex(HMAC-SHA256(btrim(student_id), pepper)) is filled by a trigger and
-- is what duplicate checks compare (n8n 'Website Identity Check', backend approve/resolve). Plain
-- SHA-256 is NOT used: IDs are short numbers, so an unkeyed hash is brute-forced in seconds.
-- Instructors / department heads / anon lose SELECT on the student_id column (column privileges).
--
--   public.sast_hash_student_id(text)            -> 'h1:<64 hex>' | input unchanged (NULL, blank, already 'h1:')
--   sast_all_raw_data_survey.student_id_hash      filled by trigger sast_raw_student_id_hash (insert/update)
--   unique index sast_raw_student_hash_uniq        (student_id_hash, "instructor_ID", subject_id, term_id)
--   column privileges                              anon/authenticated: SELECT on every column EXCEPT student_id
--
-- THE PEPPER lives in Supabase Vault (not a DB setting: settings are readable by any role via
-- current_setting / pg_settings). Create it ONCE, before this migration, in the SQL Editor:
--     select vault.create_secret(encode(extensions.gen_random_bytes(32), 'hex'), 'sast_student_id_pepper',
--                                'HMAC pepper for student_id_hash (migration 021). Never rotate casually.');
-- Back it up somewhere offline (password manager). Missing or < 32 chars -> the function raises, so every
-- insert of a raw row with a student ID fails loudly (nothing is stored with a wrong/empty key).
-- Losing or changing it makes every stored hash unmatchable (duplicate detection resets; see
-- paper-updates/HASHING-DEPLOY-NOTES.md). The plaintext column stays, so a lost pepper is recoverable:
-- create a new secret and re-run section 4 with "where true".
--
-- The existing plaintext unique index unique_student_instructor_subject_term (student_id,
-- "instructor_ID", subject_id, term_id) and every ON CONFLICT (subject_id, term_id, "instructor_ID",
-- student_id) writer (save_website_raw_row from 015, the n8n shared upsert node) are left unchanged:
-- the n8n upsert node cannot compute an HMAC (no pepper in n8n), and for trimmed IDs a plaintext
-- conflict is exactly a hash conflict, so both indexes agree.
--
-- Checked 2026-11-13 (read-only): PostgreSQL 17.6; pgcrypto in schema extensions; supabase_vault
-- installed; 2541 raw rows, 13 with a blank student_id, 0 untrimmed, 0 duplicate (instructor, subject,
-- term, btrim(student_id)) groups; no table except sast_all_raw_data_survey / eval_responses has a
-- student_id column; no Flutter or website query selects sast_all_raw_data_survey.student_id with a
-- user JWT (Flutter reads only id, created_at, sao_staff_id, ratings).
--
-- One transaction (backfill included: it only fills the new column, so it is reversible). Safe to
-- re-run. Not auto-applied: run in the Supabase SQL Editor AFTER the Vault secret exists and BEFORE
-- re-importing n8n/perfect-2.1.json and restarting the backend (both call sast_hash_student_id).
--
-- ROLLBACK (all reversible, nothing plaintext was changed):
--   begin;
--   drop trigger if exists sast_raw_student_id_hash on public.sast_all_raw_data_survey;
--   drop function if exists public.sast_raw_set_student_id_hash();
--   drop index if exists public.sast_raw_student_hash_uniq;
--   alter table public.sast_all_raw_data_survey drop column if exists student_id_hash;
--   grant select on public.sast_all_raw_data_survey to anon, authenticated;
--   drop function if exists public.sast_hash_student_id(text);   -- only after n8n + backend are rolled back
--   commit;
-- ---------------------------------------------------------------------------
begin;


-- 1. pgcrypto (already in schema extensions on Supabase: no-op there) ----------------------------
create schema if not exists extensions;
create extension if not exists pgcrypto with schema extensions;


-- 2. The keyed hash ---------------------------------------------------------------------------
-- security definer: reads the pepper from Vault for any caller allowed to execute it (only
-- service_role and the owner: the backend's RPC, n8n's postgres role, the trigger below).
create or replace function public.sast_hash_student_id(p_student_id text)
returns text
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_id     text := btrim(p_student_id);
  v_pepper text;
begin
  -- NULL / blank / already hashed: returned unchanged (idempotent)
  if v_id is null or v_id = '' or v_id ~ '^h1:[0-9a-f]{64}$' then
    return p_student_id;
  end if;
  select s.decrypted_secret into v_pepper
    from vault.decrypted_secrets s
   where s.name = 'sast_student_id_pepper'
   limit 1;
  if v_pepper is null or length(v_pepper) < 32 then
    raise exception 'sast_hash_student_id: Vault secret sast_student_id_pepper is missing or shorter than 32 characters'
      using errcode = 'P0001',
            hint = 'Create it once with vault.create_secret (see migrations/021_student_id_hash.sql).';
  end if;
  return 'h1:' || encode(extensions.hmac(v_id, v_pepper, 'sha256'), 'hex');
end;
$$;

revoke all on function public.sast_hash_student_id(text) from public, anon, authenticated;
grant execute on function public.sast_hash_student_id(text) to service_role;


-- 3. The column + trigger (every writer: website, phone, sheet, corrections, Flutter) -----------------
alter table public.sast_all_raw_data_survey add column if not exists student_id_hash text;

create or replace function public.sast_raw_set_student_id_hash()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  -- always recomputed from student_id, so the hash can't be set to anything else by hand
  new.student_id_hash := public.sast_hash_student_id(nullif(btrim(new.student_id), ''));
  return new;
end;
$$;

revoke all on function public.sast_raw_set_student_id_hash() from public, anon, authenticated;

drop trigger if exists sast_raw_student_id_hash on public.sast_all_raw_data_survey;
create trigger sast_raw_student_id_hash
  before insert or update of student_id, student_id_hash on public.sast_all_raw_data_survey
  for each row execute function public.sast_raw_set_student_id_hash();


-- 4. Backfill existing rows (the trigger computes the hash) ------------------------------------
update public.sast_all_raw_data_survey
   set student_id = student_id
 where student_id_hash is null
   and nullif(btrim(student_id), '') is not null;


-- 5. One stored form per student, instructor, subject and term, by hash ------------------------
-- (fails, rolling everything back, if two stored rows differ only by spaces around the ID)
create unique index if not exists sast_raw_student_hash_uniq
  on public.sast_all_raw_data_survey (student_id_hash, "instructor_ID", subject_id, term_id)
  where student_id_hash is not null;


-- 6. Instructors, department heads and anon never read the plaintext ID ------------------------
-- Their RLS policies let them read their own/department raw rows; this hides one column of those rows.
-- The website backend and n8n use service_role / postgres and are not affected. A column added to the
-- table LATER is not readable by anon/authenticated until granted (re-run this section).
revoke select on public.sast_all_raw_data_survey from anon, authenticated;
revoke select (student_id) on public.sast_all_raw_data_survey from anon, authenticated;
do $$
declare
  v_cols text;
begin
  select string_agg(quote_ident(column_name), ', ' order by ordinal_position) into v_cols
    from information_schema.columns
   where table_schema = 'public' and table_name = 'sast_all_raw_data_survey' and column_name <> 'student_id';
  execute format('grant select (%s) on public.sast_all_raw_data_survey to anon, authenticated', v_cols);
end;
$$;

commit;
