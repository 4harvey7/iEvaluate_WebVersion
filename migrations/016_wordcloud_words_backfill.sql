-- ---------------------------------------------------------------------------
-- 016: one-time rebuild of the instructor word cloud with REAL word counts from the remarks
-- already stored.
--
-- Why: the word cloud only had the 6-10 generic AI themes per batch ("Teaching clarity" ...).
-- The n8n remark-AI parsers now ADD real word counts next to the themes (n8n/tools/website_src/
-- remark_words.js, computed in code, no AI, no tokens). That only covers remarks scanned from now
-- on; this migration adds the words of the remarks that are already stored.
--
-- Source of the remarks: public.sast_all_raw_data_survey ("Remarks_and_Suggestions", keyed by
-- "instructor_ID" + term_id). student_remarks cannot be used: it has no link to a form
-- (source_response_id is NULL) and keeps the AI-processed copy. Only forms that COUNT are used:
--   * fewer than 5 of the 20 items answered (rule 014) -> the form's remarks are skipped, the same
--     test as public.sast_answers (num_nonnulls(m1..m10, p1..p10) >= 5);
--   * a form discarded by the min-answered rule never reaches this table (it stays in
--     failed_scan_queue with status 'discarded'), so it is skipped as well;
--   * rows without an instructor or term, and blank remarks, are skipped.
-- Run migrations/004_mask_existing_profanity.sql first if it has not been applied: stored remarks
-- must already be masked (a masked word such as '****' leaves no letters, so it is never a word).
--
-- Same rules as the n8n code (remark_words.js): lowercase, accents removed, apostrophes removed,
-- punctuation and digits become spaces, split on whitespace; a word counts ONCE per remark; dropped:
-- words under 3 letters, stopwords (English + Cebuano/Bisaya + Tagalog, the same list as an array
-- below), the instructor's own first/last name tokens (user_info), laughter ('hahaha', 'lol') and
-- placeholders ('none', 'n/a', 'wala'). Per instructor + term only the top 100 words are kept.
--
-- HOW IT STAYS APART FROM THE AI THEME COUNTS AND STAYS IDEMPOTENT
--   Every count this migration adds is recorded in public.wordcloud_backfill_016
--   (instructor_id, term_id, word, added). A re-run FIRST subtracts exactly what the previous
--   run added (rows that reach 0 or less are deleted, and only rows listed in the marker table),
--   empties the marker table, and then adds the recomputed counts. Rows and counts that came from
--   the AI themes are never read, updated or deleted except by that exact subtraction/addition,
--   so the theme counts are exactly what they were before. If a real word has the same text as an
--   existing theme (any case), its count is added onto that theme row (one row per word, like the
--   n8n merge); the marker table remembers it, so the re-run subtracts the same amount.
--
-- WHEN TO RUN: once, BEFORE re-importing the new n8n workflow. Re-running before that is safe. Do
-- not re-run it after the new workflow has processed batches (those remarks are then counted
-- live AND would be counted again here).
--
-- Review before applying. Not auto-applied. Run in the Supabase SQL Editor (or psql) after 014.
-- One transaction. It ends with ROLLBACK: read the preview, then switch the last line to COMMIT.
-- ---------------------------------------------------------------------------
begin;

-- 1. marker table: what this migration added (service role only) ----------------------------
create table if not exists public.wordcloud_backfill_016 (
  instructor_id uuid    not null,
  term_id       uuid    not null,
  word          text    not null,
  added         integer not null check (added > 0),
  run_at        timestamptz not null default now(),
  primary key (instructor_id, term_id, word)
);
alter table public.wordcloud_backfill_016 enable row level security;
revoke all on public.wordcloud_backfill_016 from public, anon, authenticated;


-- 2. undo a previous run (exactly what it added, nothing else) -----------------------------
update public.instructor_wordcloud w
   set count = w.count - b.added,
       updated_at = now()
  from public.wordcloud_backfill_016 b
 where w.instructor_id = b.instructor_id
   and w.term_id = b.term_id
   and w.word = b.word;

delete from public.instructor_wordcloud w
 using public.wordcloud_backfill_016 b
 where w.instructor_id = b.instructor_id
   and w.term_id = b.term_id
   and w.word = b.word
   and w.count <= 0;

delete from public.wordcloud_backfill_016;


-- 3. words per counted remark ---------------------------------------------------------------
-- One row per (remark, word): a word counts once per remark.
create temp table t016_words on commit drop as
with stop(words) as (
  select array[
    'a', 'about', 'above', 'actually', 'additional', 'after', 'again', 'against',
    'ako', 'akong', 'alin', 'all', 'also', 'although', 'always', 'am',
    'amo', 'amoa', 'amon', 'among', 'an', 'and', 'ang', 'angng',
    'ano', 'any', 'anyone', 'anything', 'apan', 'are', 'aren', 'arent',
    'aron', 'as', 'asa', 'at', 'ato', 'aton', 'atong', 'ay',
    'ayan', 'ayaw', 'ayon', 'ba', 'baka', 'bakit', 'bawat', 'be',
    'because', 'been', 'before', 'being', 'below', 'between', 'bitaw', 'bitawng',
    'both', 'but', 'by', 'can', 'cannot', 'cant', 'comment', 'comments',
    'could', 'couldn', 'couldnt', 'dahil', 'dalawa', 'daw', 'dayon', 'di',
    'diay', 'did', 'didn', 'didnt', 'dili', 'diling', 'din', 'dito',
    'diyan', 'do', 'does', 'doesn', 'doesnt', 'doing', 'don', 'dont',
    'doon', 'down', 'dun', 'duna', 'during', 'dyan', 'each', 'else',
    'eto', 'even', 'ever', 'everyone', 'everything', 'feedback', 'few', 'for',
    'from', 'further', 'ganito', 'gano', 'ganoon', 'ganun', 'ganyan', 'get',
    'gets', 'getting', 'gi', 'gihapon', 'gikan', 'gina', 'gipa', 'got',
    'gyod', 'gyud', 'had', 'hadn', 'hadnt', 'hangtod', 'hangtud', 'has',
    'hasn', 'hasnt', 'have', 'haven', 'havent', 'having', 'he', 'her',
    'here', 'hers', 'herself', 'hes', 'him', 'himself', 'hindi', 'his',
    'ho', 'how', 'however', 'huwag', 'i', 'iba', 'ibang', 'id',
    'if', 'ikaw', 'ilan', 'ilang', 'ill', 'im', 'imo', 'imong',
    'in', 'ingnon', 'ingon', 'instructor', 'into', 'inyo', 'is', 'isa',
    'isn', 'isnt', 'it', 'ito', 'its', 'itself', 'ive', 'iya',
    'iyan', 'iyang', 'iyon', 'jud', 'just', 'ka', 'kaayo', 'kailan',
    'kami', 'kamo', 'kana', 'kanang', 'kani', 'kapag', 'karon', 'kase',
    'kasi', 'kay', 'kaya', 'kayat', 'kayo', 'kayong', 'kini', 'kinsa',
    'kita', 'ko', 'kon', 'kun', 'kung', 'lagi', 'laging', 'lahat',
    'lamang', 'lang', 'let', 'lets', 'lot', 'lots', 'ma', 'maam',
    'madam', 'made', 'mag', 'maka', 'make', 'makes', 'making', 'mam',
    'man', 'many', 'mao', 'maong', 'may', 'maybe', 'mayroon', 'me',
    'meron', 'mga', 'mi', 'might', 'mismo', 'mo', 'more', 'most',
    'mr', 'mrs', 'ms', 'mu', 'much', 'muna', 'must', 'my',
    'myself', 'na', 'naa', 'nag', 'naga', 'naging', 'nagka', 'naka',
    'nakaka', 'nako', 'naman', 'namin', 'namo', 'namong', 'nan', 'nang',
    'nangayo', 'natin', 'nay', 'never', 'ng', 'nga', 'ngano', 'ngunit',
    'ni', 'nil', 'nila', 'nimo', 'ninyo', 'niya', 'no', 'none',
    'nor', 'not', 'nothing', 'now', 'null', 'of', 'off', 'often',
    'og', 'ok', 'okay', 'on', 'once', 'one', 'only', 'opo',
    'or', 'other', 'others', 'ought', 'our', 'ours', 'ourselves', 'out',
    'over', 'own', 'pa', 'paano', 'pag', 'pala', 'palihug', 'pang',
    'para', 'perhaps', 'pero', 'pila', 'po', 'pod', 'prof', 'professor',
    'pud', 'puro', 'quite', 'ra', 'rather', 'raw', 'really', 'remark',
    'remarks', 'rin', 'sa', 'saan', 'sab', 'sad', 'saka', 'same',
    'sana', 'sang', 'say', 'sayo', 'shall', 'she', 'shes', 'should',
    'shouldn', 'shouldnt', 'si', 'siguro', 'sila', 'sina', 'since', 'sino',
    'sir', 'siya', 'so', 'some', 'someone', 'something', 'sometimes', 'still',
    'subalit', 'such', 'suggestion', 'suggestions', 'sulod', 'ta', 'talaga', 'tapos',
    'tatlo', 'tay', 'tayo', 'teacher', 'than', 'thank', 'thanks', 'thankyou',
    'that', 'thats', 'the', 'their', 'theirs', 'them', 'themselves', 'then',
    'there', 'theres', 'these', 'they', 'theyll', 'theyre', 'theyve', 'thing',
    'things', 'this', 'those', 'though', 'through', 'to', 'too', 'tsaka',
    'ug', 'ugg', 'under', 'ung', 'unsa', 'until', 'unya', 'up',
    'upon', 'us', 'usab', 'very', 'wa', 'wag', 'wala', 'walang',
    'walay', 'was', 'wasn', 'wasnt', 'way', 'we', 'were', 'weren',
    'werent', 'weve', 'what', 'whats', 'when', 'where', 'which', 'while',
    'who', 'whom', 'whose', 'why', 'will', 'with', 'won', 'wont',
    'would', 'wouldn', 'wouldnt', 'yan', 'yeah', 'yep', 'yes', 'yet',
    'yon', 'you', 'youll', 'your', 'youre', 'yours', 'yourself', 'yourselves',
    'youve', 'yun', 'yung'
  ]::text[]
),
names as (  -- the instructor's own name tokens, same normalisation as the remarks
  select ui.id as instructor_id, n.tok
    from public.user_info ui
   cross join lateral (
     select distinct t as tok
       from regexp_split_to_table(
              regexp_replace(translate(translate(lower(coalesce(ui.first_name, '') || ' ' || coalesce(ui.last_name, '')),
                'áàâäãåéèêëíìîïóòôöõúùûüñç', 'aaaaaaeeeeiiiiooooouuuunc'), '''’‘`', ''),
                '[^[:alpha:]]+', ' ', 'g'), ' ') as t
      where length(t) >= 2) n
),
counted as (  -- remarks of forms that count (014: at least 5 of the 20 items answered)
  select r.id,
         r."instructor_ID"::uuid as instructor_id,
         r.term_id::uuid         as term_id,
         r."Remarks_and_Suggestions" as remark
    from public.sast_all_raw_data_survey r
   where r."Remarks_and_Suggestions" is not null
     and btrim(r."Remarks_and_Suggestions") <> ''
     and r."instructor_ID" is not null
     and r.term_id is not null
     and num_nonnulls(r.m1, r.m2, r.m3, r.m4, r.m5, r.m6, r.m7, r.m8, r.m9, r.m10,
                      r.p1, r.p2, r.p3, r.p4, r.p5, r.p6, r.p7, r.p8, r.p9, r.p10) >= 5
),
tokens as (
  select distinct c.id, c.instructor_id, c.term_id, t as tok
    from counted c
   cross join lateral regexp_split_to_table(
          regexp_replace(translate(translate(lower(c.remark),
            'áàâäãåéèêëíìîïóòôöõúùûüñç', 'aaaaaaeeeeiiiiooooouuuunc'), '''’‘`', ''),
            '[^[:alpha:]]+', ' ', 'g'), ' ') as t
)
select tk.id as remark_id, tk.instructor_id, tk.term_id, tk.tok as word
  from tokens tk
 cross join stop
 where length(tk.tok) >= 3
   and not (tk.tok = any (stop.words))
   and tk.tok !~ '^(ha|he|hi|ho|hu|ah|eh|ih|oh|uh)+h?$'
   and tk.tok !~ '^(.)\1{2,}$'
   and tk.tok !~ '^(lol|lmao|omg|wtf|xd)+$'
   and not exists (select 1 from names nm where nm.instructor_id = tk.instructor_id and nm.tok = tk.tok);


-- 4. counts per instructor + term, top 100 words, merged onto an existing row of the same text --
create temp table t016_counts on commit drop as
with per_word as (
  select instructor_id, term_id, word, count(*)::int as n
    from t016_words
   group by instructor_id, term_id, word
),
ranked as (
  select p.*, row_number() over (partition by instructor_id, term_id order by n desc, word) as rk
    from per_word p
),
target as (  -- an existing row with the same text (any case) takes the count: one row per word
  select k.instructor_id, k.term_id, k.n,
         coalesce((select w.word
                     from public.instructor_wordcloud w
                    where w.instructor_id = k.instructor_id
                      and w.term_id = k.term_id
                      and lower(w.word) = k.word
                    order by (w.word = k.word) desc, w.id
                    limit 1), k.word) as word
    from ranked k
   where k.rk <= 100
)
select instructor_id, term_id, word, sum(n)::int as added
  from target
 group by instructor_id, term_id, word;


-- 5. add the counts and remember them -------------------------------------------------------
insert into public.wordcloud_backfill_016 (instructor_id, term_id, word, added)
select instructor_id, term_id, word, added from t016_counts;

insert into public.instructor_wordcloud as w (instructor_id, term_id, word, count)
select instructor_id, term_id, word, added from t016_counts
on conflict (instructor_id, term_id, word)
do update set count = w.count + excluded.count,
              updated_at = now();


-- 6. preview: top 10 words per instructor + term, and what changed ----------------------------
select instructor_id, term_id, word, count as total_count,
       coalesce(b.added, 0) as added_by_016
  from (
    select w.*, row_number() over (partition by w.instructor_id, w.term_id order by w.count desc, w.word) as rk
      from public.instructor_wordcloud w
  ) w
  left join public.wordcloud_backfill_016 b using (instructor_id, term_id, word)
 where w.rk <= 10
 order by instructor_id, term_id, w.rk;

select (select count(*) from public.wordcloud_backfill_016)               as words_added_or_raised,
       (select count(distinct instructor_id) from public.wordcloud_backfill_016) as instructors,
       (select coalesce(sum(added), 0) from public.wordcloud_backfill_016) as total_added_count,
       (select count(*) from t016_words)                                  as word_occurrences_counted;


-- Nothing is kept until you switch this to commit. Check the preview first.
rollback;
-- commit;
