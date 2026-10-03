#!/usr/bin/env node
// Integration test for the website-branch SQL against a THROWAWAY local
// PostgreSQL (with pg_trgm). Never point this at Supabase: it drops and
// recreates mock copies of the iEvaluate tables in a scratch database.
//
//   npm install pg-promise            (anywhere on NODE_PATH)
//   PG_URL=postgres://postgres@127.0.0.1:55432/postgres node test_website_sql.js
//
// It also runs 'Website Form Match' (form_match.sql: the instructor/subject on the form vs the
// database) under 'Website Decide', and the corrected-page chain of sast-manual-correction
// (id-first lookups, website_review term) down to the raw row.
// For every website Postgres node it evaluates the node's own queryReplacement
// expression (with mocked $json / $('...')), formats the query with pg-promise
// exactly like the n8n Postgres node does, and runs it. It runs twice: once with
// uuid id columns in sast_all_raw_data_survey and once with text id columns.
// The schema gets migrations/013_sast_null_aware_means.sql (public.recompute_sast, rating CHECKs),
// which the recompute nodes call; it is also tested directly (blank answers left out of the means).
// It also runs the optional clean-up migrations/004_mask_existing_profanity.sql on synthetic
// rows (preview, apply, student_remarks duplicates, idempotency) and checks that its SQL filter
// masks exactly like the n8n JS filter (profanity.js) on a large synthetic corpus.
'use strict';
const fs = require('fs');
const path = require('path');
const assert = require('assert');
const pgp = require('pg-promise')();

const PG_URL = process.env.PG_URL || 'postgres://postgres@127.0.0.1:55432/postgres';
const u = new URL(PG_URL);
if (!['127.0.0.1', 'localhost', '::1'].includes(u.hostname)) {
  console.error('Refusing to run against a non-local database.');
  process.exit(2);
}
const TEST_DB = 'ievaluate_n8n_website_test';
const wfPath = process.argv[2] || path.join(__dirname, '..', 'perfect-2.1.json');
const wf = JSON.parse(fs.readFileSync(wfPath, 'utf8'));
const node = (name) => {
  const n = wf.nodes.find((x) => x.name === name);
  if (!n) throw new Error(`missing node ${name}`);
  return n;
};

// ------------------------------------------------------------------ fixtures
const ID = {
  juan: '10000000-0000-4000-8000-000000000001',        // batch instructor
  pedro: '10000000-0000-4000-8000-000000000002',
  maria: '10000000-0000-4000-8000-000000000003',
  juanCarlos: '10000000-0000-4000-8000-000000000004',
  ana: '10000000-0000-4000-8000-000000000005',          // same surname as batch
  rosa: '10000000-0000-4000-8000-000000000006',         // pending account
  liza: '10000000-0000-4000-8000-000000000007',         // SAO staff
  disabled: '10000000-0000-4000-8000-000000000008',
  staff: '10000000-0000-4000-8000-000000000009',
  placeholder: '10000000-0000-4000-8000-00000000000a', // is_placeholder, disabled, pending
  it101: '20000000-0000-4000-8000-000000000001',
  it102: '20000000-0000-4000-8000-000000000002',
  ge104: '20000000-0000-4000-8000-000000000003',
  ddmgt3: '20000000-0000-4000-8000-000000000004',
  term: '30000000-0000-4000-8000-000000000001',
  term2: '30000000-0000-4000-8000-000000000002',        // a batch term that is not the current term
  batch: '40000000-0000-4000-8000-000000000001',
  missing: '90000000-0000-4000-8000-000000000009',
};

function schemaSql(rawIdType) {
  const qs = (p, suffix, type) => Array.from({ length: 10 }, (_, i) => `${p}${i + 1}${suffix} ${type}`).join(', ');
  return `
  DROP SCHEMA public CASCADE; CREATE SCHEMA public;
  CREATE EXTENSION IF NOT EXISTS pg_trgm SCHEMA public;
  CREATE TABLE public.user_info (id uuid PRIMARY KEY, first_name text, last_name text, account_status text,
    is_placeholder boolean DEFAULT false, disabled boolean DEFAULT false, fcm_token text,
    alert_performance boolean, alert_sentiment boolean);
  CREATE TABLE public.roles (id serial PRIMARY KEY, "Roles" text);
  CREATE TABLE public.department_table (id serial PRIMARY KEY, user_id uuid, "Department_name_ID" int, roles int);
  CREATE TABLE public.subjects (id uuid PRIMARY KEY, subject_code text UNIQUE, subject_name text, department_id bigint);
  CREATE TABLE public.system_settings (id int PRIMARY KEY, current_term_id uuid);
  CREATE TABLE public.instructor_subjects (id serial, subject_id uuid, instructor_id uuid, term_id uuid,
    UNIQUE (subject_id, instructor_id, term_id));
  CREATE TABLE public.sast_all_raw_data_survey (id bigserial PRIMARY KEY, created_at timestamptz DEFAULT now(),
    ${qs('m', '', 'numeric')}, ${qs('p', '', 'numeric')},
    "Remarks_and_Suggestions" text, sao_staff_id ${rawIdType}, student_id text, instructor text, submitted_date date,
    "instructor_ID" ${rawIdType}, term_id ${rawIdType}, subject_id ${rawIdType}, validation_status text,
    manually_corrected boolean, task_id text,
    UNIQUE (subject_id, term_id, "instructor_ID", student_id));
  CREATE TABLE public.management_results (instructor_id uuid, term_id uuid, subject_id uuid, total_responses numeric,
    ${qs('m', '_total', 'numeric')}, grand_total numeric, ${qs('m', '_mean', 'numeric NOT NULL')},
    overall_management_mean numeric, average_per_response_management numeric,
    UNIQUE (instructor_id, term_id, subject_id));
  CREATE TABLE public.performance_results (instructor_id uuid, term_id uuid, subject_id uuid, total_responses numeric,
    ${qs('p', '_total', 'numeric')}, grand_total numeric, ${qs('p', '_mean', 'numeric NOT NULL')},
    overall_performance_mean numeric, average_per_response_performance numeric,
    UNIQUE (instructor_id, term_id, subject_id));
  CREATE TABLE public.overall_total_survey (instructor_id uuid, term_id uuid, total_responses numeric,
    management_total numeric, performance_total numeric, combined_total numeric, management_mean numeric,
    performance_mean numeric, combined_score_mean numeric,
    overall_mean numeric GENERATED ALWAYS AS (round((management_mean + performance_mean) / 2::numeric, 2)) STORED,
    UNIQUE (instructor_id, term_id));
  CREATE TABLE public.overall_question_means (instructor_id uuid, term_id uuid, category text, question_number int,
    mean_score numeric NOT NULL DEFAULT 0, updated_at timestamptz DEFAULT now(),
    UNIQUE (instructor_id, term_id, category, question_number));
  CREATE TABLE public.scan_error_images (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), task_id text, image_base64 text,
    created_at timestamptz DEFAULT now());
  CREATE TABLE public.failed_scan_queue (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), task_id text UNIQUE,
    user_id uuid, term_id text, table_found boolean, grid_source text, partial_data jsonb, n8n_ocr_image text,
    status text DEFAULT 'pending', validated_data jsonb, validated_by uuid, validated_at timestamptz,
    review_reasons text[], omr_comparison jsonb, validation_status text,
    scan_image_id uuid REFERENCES public.scan_error_images(id), batch_id uuid, created_at timestamptz DEFAULT now());
  CREATE TABLE public.import_errors (id bigserial PRIMARY KEY, created_at timestamptz DEFAULT now(), error_type text,
    source text, status text DEFAULT 'pending', raw_instructor_name text, raw_subject_name text, raw_student_id text,
    raw_term_id text, task_id text, scan_image_id uuid REFERENCES public.scan_error_images(id), raw_data jsonb,
    review_reasons text[], scan_image text, resolved_at timestamptz, resolved_by uuid);
  CREATE TABLE public.student_remarks (id bigserial PRIMARY KEY, instructor_id uuid NOT NULL, remark text NOT NULL,
    tone text, created_at timestamp DEFAULT now(), subject_id uuid, term_id uuid, source_response_id text,
    UNIQUE (instructor_id, subject_id, term_id, remark));
  CREATE TABLE public.instructor_wordcloud (id bigserial PRIMARY KEY, instructor_id uuid NOT NULL, word text NOT NULL,
    count integer NOT NULL DEFAULT 1, created_at timestamp DEFAULT now(), updated_at timestamp DEFAULT now(), term_id uuid,
    UNIQUE (instructor_id, term_id, word));

  INSERT INTO public.roles (id, "Roles") VALUES (1,'SAO_ADMIN'),(2,'SAO_STAFF'),(3,'DEPARTMENT_HEAD'),(4,'PART-TIME'),(5,'FULL-TIME');
  INSERT INTO public.user_info (id, first_name, last_name, account_status, disabled) VALUES
    ('${ID.juan}','Juan','Dela Cruz','approved',false),
    ('${ID.pedro}','Pedro','Reyes','approved',false),
    ('${ID.maria}','Maria','Santos','approved',false),
    ('${ID.juanCarlos}','Juan Carlos','Santos','approved',false),
    ('${ID.ana}','Ana','Dela Cruz','approved',false),
    ('${ID.rosa}','Rosa','Lim','pending',false),
    ('${ID.liza}','Liza','Gomez','approved',false),
    ('${ID.disabled}','Carlo','Mendoza','approved',true),
    ('${ID.staff}','Sam','Staff','approved',false);
  INSERT INTO public.user_info (id, first_name, last_name, account_status, disabled, is_placeholder) VALUES
    ('${ID.placeholder}','Nora','Villanueva','pending',true,true);
  INSERT INTO public.department_table (user_id, "Department_name_ID", roles) VALUES
    ('${ID.juan}',1,5),('${ID.pedro}',1,4),('${ID.maria}',1,5),('${ID.juanCarlos}',1,3),('${ID.ana}',1,4),
    ('${ID.rosa}',1,5),('${ID.liza}',1,2),('${ID.disabled}',1,5),('${ID.juan}',1,5),('${ID.placeholder}',1,5);
  INSERT INTO public.subjects (id, subject_code, subject_name, department_id) VALUES
    ('${ID.it101}','IT 101','Introduction to Computing',1),
    ('${ID.it102}','IT 102','Computer Programming 1',1),
    ('${ID.ge104}','GE 104','Mathematics in the Modern World',1),
    ('${ID.ddmgt3}','DDMGT-3','Database Management',1);
  INSERT INTO public.system_settings (id, current_term_id) VALUES (1, '${ID.term}');
  `;
}

// ------------------------------------------------------------------ migration 013 (null-aware SAST means)
const MIG013 = fs.readFileSync(path.join(__dirname, '..', '..', 'migrations', '013_sast_null_aware_means.sql'), 'utf8');

// ------------------------------------------------------------------ sexual-word filter / migration 004
const MIG_PATH = path.join(__dirname, '..', '..', 'migrations', '004_mask_existing_profanity.sql');
const MIG = fs.readFileSync(MIG_PATH, 'utf8').replace(/\r\n/g, '\n');
const MIG_PART2 = '-- ============================================================================ PART 2: APPLY';
const MIG_PART1 = MIG.slice(0, MIG.indexOf(MIG_PART2));
const MIG_FUNCTIONS = MIG_PART1.slice(0, MIG_PART1.indexOf('-- Preview:'));
function jsFilter() {
  const code = node('Code in JavaScript').parameters.jsCode;
  const m = /\/\/ sexual-word filter, from [^\n]*\n([\s\S]*?)\/\/ end of sexual-word filter\n/.exec(code);
  // eslint-disable-next-line no-new-func
  return new Function(`${m[1]}\nreturn { maskProfanity, PF_PATTERNS };`)();
}
// Synthetic corpus (no real remarks): sexual words in every supported form, insults, ordinary
// Bisaya/Tagalog/English sentences, odd whitespace/punctuation/Unicode, plus seeded random mixes.
function filterCorpus() {
  const words = ['iyot', 'Iyot', 'giiyot', 'iyotan', 'kaiyot', 'maniyot', 'nag-iyot', 'iyooot', 'i.y.o.t', 'i-y-o-t',
    '1y0t', 'b1lat', 'bi1at', 'b!lat', 'bilatan', 'oten', '0t3n', 'utin', 'kinakantot', 'nagkakantutan', 'k4nt0t',
    'kantot-kantot', 'hindutan', 'nagjajakol', 'tsupa', 'burat', 'titi', 'tite', 'etits', 'puke', 'pekpek', 'kepyas',
    'kipay', 'puday', 'tamod', 'betlog', 'bayag', 'salsal', 'pussy', 'cunt', 'boobs', 'tits', 'porn', 'blowjob', 'horny',
    'dildo', 'jizz', 'fuck', 'f*ck', 'fvck', 'FUCKING', "fuckin'", 'motherfucker', 'fak', 'pakyu', 'pakshet', 'iyót',
    'idiot', 'kupal', 'gago', 'yawa', 'piste', 'bwisit', 'animal', 'buang', 'bogo', 'tanga', 'atay', 'lubot', 'putangina',
    'tangina', 'puta', 'shit', 'bitch', 'libog', 'malibog', 'katiting', 'titig', 'titser', 'pakialam', 'pakyaw', 'tamo',
    'salsa', 'putahe', 'inutil', 'faking', 'count', 'Putin', 'potent', 'petite', 'cum', 'laude', 'cocky', 'Dick', 'sex',
    'maayo', 'kaayo', 'sir', "ma'am", 'ka', 'pak', 'yu', 'you', 'ang', 'discussion', 'unta', 'examples', 'klaro', 'po',
    'i', 'y', 'o', 't', 'p', 'a', 'k', 'u', 'f', 'c', '10/10', '1.0', '😀', 'x.iyot', 'iyot.com', '“iyot”', '«bilat»'];
  const seps = [' ', ' ', ' ', ', ', '. ', '! ', '? ', ' - ', '\n', '\t', '\u00A0', '  ', ' (', ') ', ': ', '/', ' "'];
  const fixed = ['i y o t', 'p a k y u', 'f u c k', 'pak yu', 'Pak  you!', 'pak\tyu', 'g i i y o t ka', 'a b c i y o t',
    'O VS S F US', 'Gago ka sir, yawa! pero ang iyot dili maayo', 'putang ina mo', 'tang ina', 'kantot—kantot',
    'maayo si sir pero giiyot', 'sir.iyot', '...iyot...', '--bilat--', '(kantot)', 'iyot😀', 'i\u00A0y\u00A0o\u00A0t', ''];
  let seed = 7;
  const rnd = () => { seed = (seed * 1103515245 + 12345) % 2147483648; return seed / 2147483648; };
  const out = [...fixed, ...words];
  for (let i = 0; i < 600; i++) {
    let s = '';
    const n = 1 + Math.floor(rnd() * 8);
    for (let j = 0; j < n; j++) s += (j ? seps[Math.floor(rnd() * seps.length)] : '') + words[Math.floor(rnd() * words.length)];
    out.push(s);
  }
  return out;
}

// ------------------------------------------------------------------ helpers
let db;
function evalReplacement(n, $json, nodes) {
  const raw = (n.parameters.options || {}).queryReplacement;
  if (raw === undefined) return [];   // a query without parameters ('Website Known Names')
  const m = /^=\{\{([\s\S]*)\}\}$/.exec(raw.trim());
  if (!m) throw new Error(`${n.name}: unexpected queryReplacement ${raw}`);
  const $ = (ref) => {
    if (!(ref in nodes)) throw new Error(`${n.name}: $('${ref}') not mocked`);
    return { first: () => ({ json: nodes[ref] }) };
  };
  // eslint-disable-next-line no-new-func
  return new Function('$json', '$', `return (${m[1]});`)($json, $);
}
async function runNode(name, $json, nodes = {}) {
  const n = node(name);
  let values = evalReplacement(n, $json, nodes);
  // A single (non-array) replacement value is one parameter, as in 'get instructor id7'.
  if (!Array.isArray(values)) values = [values];
  // Same calls the n8n Postgres node makes: queryBatching = transaction -> db.tx + t.multi per
  // item; otherwise db.multi of the client-side formatted query. All result sets are flattened
  // (a statement without rows, like the advisory-lock DO block, adds nothing).
  const q = pgp.helpers.concat([{ query: n.parameters.query, values }]);
  const res = (n.parameters.options || {}).queryBatching === 'transaction'
    ? await db.tx((t) => t.multi(q))
    : await db.multi(q);
  return res.flat();
}

// Runs a Code node of the workflow with a minimal mock of $ / $input.
async function runCode(name, inputs, nodes = {}) {
  const $ = (ref) => {
    if (!(ref in nodes)) throw new Error(`ExpressionError: Referenced node is unexecuted: ${ref}`);
    return { first: () => ({ json: nodes[ref] }), all: () => [{ json: nodes[ref] }] };
  };
  const $input = { first: () => ({ json: inputs[0] }), all: () => inputs.map((json) => ({ json })) };
  const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
  const out = await new AsyncFunction('$', '$input', node(name).parameters.jsCode)($, $input);
  return out.map((i) => i.json);
}
// Same statement the n8n Postgres node (v2, operation upsert, autoMapInputData) builds for
// 'Insert or update rows in a table1': INSERT ... ON CONFLICT (matching columns) DO UPDATE.
async function sharedRawUpsert(row) {
  const p = node('Insert or update rows in a table1').parameters;
  const match = p.columns.matchingColumns;
  const cols = Object.keys(row);
  const upd = cols.filter((c) => !match.includes(c));
  const q = pgp.as.format(
    `INSERT INTO public.sast_all_raw_data_survey ($1:name) VALUES ($2:csv)
     ON CONFLICT ($3:name) DO UPDATE SET ${upd.map((c) => pgp.as.format('$1:name = EXCLUDED.$1:name', [c])).join(', ')}
     RETURNING *`, [cols, cols.map((c) => row[c]), match]);
  return db.one(q);
}
// Website page -> Website Map to Target Format -> (If19 -> If1) -> Code in JavaScript -> upsert
// Website clean page: 'Website Raw Upsert' (before the answer), then the shared flow:
// Website Map to Target Format -> (If19 -> If1) -> Code in JavaScript -> 'Raw Row Already
// Stored?' (true -> skip the shared upsert).
async function websiteToRaw(d) {
  const [res] = await runNode('Website Raw Upsert', { status: 'success' }, { 'Website Decide': d });
  if (res.saved !== true) return { saved: false };
  const nodes = { 'Website Decide': { decision: 'clean', ...d },
    'Website Reconcile OCR OMR': { ay_semester: '1st 2026', comparisons: null } };
  const mapped = await runCode('Website Map to Target Format', [{}], nodes);
  const rows = await runCode('Code in JavaScript', mapped,
    { 'SAST from Website': { body: { staff_id: d.staff_id } }, 'Website Raw Upsert': res });
  assert.strictEqual(rows.length, 1);
  assert.strictEqual(rows[0]._raw_already_stored, true, 'shared upsert skipped for the stored website row');
  // Same values the shared upsert would have written (the phone path's row shape).
  const { _raw_already_stored: _skip, ...row } = rows[0];
  const stored = (await db.one('SELECT to_jsonb(s) AS j FROM public.sast_all_raw_data_survey s WHERE task_id = $1', [d.task_id])).j;
  for (const [k, v] of Object.entries(row)) {
    const got = stored[k];
    const want = typeof v === 'number' ? String(v) : v;
    assert.strictEqual(got === null ? null : String(got), want === null ? null : String(want), `column ${k}`);
  }
  return { saved: true, row: stored };
}

// 'Website Form Match' for the text Gemini read on the form (batch = Juan Dela Cruz / IT 101 unless given).
async function formMatch(instructor, subject, batch = {}) {
  const prep = { instructor_id: ID.juan, subject_id: ID.it101, term_id: ID.term, task_id: 'web:fm', ...batch };
  const rows = await runNode('Website Form Match', { resolved_term_id: prep.term_id },
    { 'Website Prepare Scan': prep, 'Website Reconcile OCR OMR': { ocr_instructor: instructor, ocr_subject: subject } });
  assert.strictEqual(rows.length, 1, 'form match returns exactly one row');
  return rows[0];
}
// Identity Check -> Form Match -> Website Decide on the real SQL rows (reconciler output mocked).
async function decideForm(instructor, subject, batch = {}, page = {}) {
  const ctx = { batch_id: ID.batch, instructor_id: ID.juan, subject_id: ID.it101, term_id: ID.term, station_id: ID.batch,
    staff_id: ID.staff, filename: 'p.jpg', task_id: 'web:form', image_base64: 'QUJD', ...batch };
  const ratings = {};
  for (let i = 1; i <= 10; i++) { ratings[`m${i}`] = 5; ratings[`p${i}`] = 4; }
  const rec = { _stage: 'reconciled', ocr_instructor: instructor, ocr_subject: subject, review_reasons: [], review_note: null,
    ratings, student_id: 'S-FORM', remarks: 'Good', agreement_percent: 100, comparisons: [], ...page };
  const [idRow] = await runNode('Website Identity Check', rec, { 'Website Prepare Scan': ctx });
  const [fm] = await runNode('Website Form Match', idRow, { 'Website Prepare Scan': ctx, 'Website Reconcile OCR OMR': rec });
  const [d] = await runCode('Website Decide', [fm], { 'Website Prepare Scan': ctx, 'Website Reconcile OCR OMR': rec,
    'Website Identity Check': idRow });
  return { ctx, rec, idRow, fm, d };
}
const MISMATCH = /^(instructor|subject)_mismatch_/;

// What Gemini read on the form. 'Website Identity Check' does NOT use it (batch integrity only;
// the form is compared with the database by 'Website Form Match'); passed to prove that.
function ocrKeys(instructor, subject) {
  return { ocr_instructor: instructor, ocr_subject: subject };
}
async function identity(instructor, subject, batch = {}, page = {}) {
  const prep = { instructor_id: ID.juan, subject_id: ID.it101, term_id: ID.term, task_id: 'web:identity', ...batch };
  const rows = await runNode('Website Identity Check', { ...ocrKeys(instructor, subject), ...page }, { 'Website Prepare Scan': prep });
  assert.strictEqual(rows.length, 1, 'identity check returns exactly one row');
  return rows[0];
}
function decideItem(over = {}) {
  const ratings = {};
  for (let i = 1; i <= 10; i++) { ratings[`m${i}`] = 5; ratings[`p${i}`] = 4; }
  return {
    instructor_id: ID.juan, subject_id: ID.it101, term_id: ID.term, student_id: '2021-0001',
    task_id: 'web:t1', instructor_name: 'Juan Dela Cruz', remark: "Very good, 'strict' teacher;\n-- not SQL",
    submitted_date: '2026-09-26', staff_id: ID.staff, ratings, ...over,
  };
}

// Python /process and Gemini responses for the end-to-end tests (letters cycle O VS S F US).
const LETTERS = ['O', 'VS', 'S', 'F', 'US'];
// blankRows = rows 0..n-1 Python reads blank (Gemini fills them: a disagreement the agreement rule counts)
function pyResp(ocr = {}, { blankRows = 0 } = {}) {
  const cell = (i) => (i < blankRows ? { detected: false, rating_name: null } : { detected: true, rating_name: LETTERS[i % 5] });
  return {
    table_found: true, grid_source: 'structure-fit',
    ratings: { management: [...Array(10)].map((_, i) => cell(i)), performance: [...Array(10)].map((_, i) => cell(i + 10)) },
    omr_debug: [...Array(20)].map((_, i) => ({ row_index: i, scores: { a: 0.9, b: 0.1 } })),
    // Python OCR text: must never reach any stored field
    ocr_fields: { instructor: 'AY/Semester: L U', subject: 'Mà essor', student_id: 'PY-999', remarks: 'py', ...ocr },
  };
}
const QS = [...Array(10)].map((_, i) => `m${i + 1}`).concat([...Array(10)].map((_, i) => `p${i + 1}`));
function gemResp(fields = {}, ratingOver = {}) {
  const ratings = {};
  for (let i = 0; i < 10; i++) { ratings[`m${i + 1}`] = LETTERS[i % 5]; ratings[`p${i + 1}`] = LETTERS[(i + 10) % 5]; }
  const g = { instructor: 'AY/Semester: L U', subject: 'Mà essor', ay_semester: '2026 1st', remarks: 'Good', date: '09/27/26',
    student_id: '2021-7777', document_check: { is_sast_form: true }, scan_quality: { poor_quality: false, issues: [] },
    ratings: { ...ratings, ...ratingOver }, ...fields };
  return { candidates: [{ content: { parts: [{ text: JSON.stringify(g) }] } }] };
}
// Website page end to end: Reconcile (code) -> Identity Check (SQL) -> Decide (code).
async function websiteDecide(task, gem, py = pyResp()) {
  const ctx = { batch_id: ID.batch, instructor_id: ID.juan, subject_id: ID.it101, term_id: ID.term, station_id: ID.batch,
    staff_id: ID.staff, filename: 'p.jpg', task_id: task, image_base64: 'QUJD' };
  const [rec] = await runCode('Website Reconcile OCR OMR', [gem], { 'Website Python OMR': py, 'Website AI Validation': gem });
  const [idRow] = await runNode('Website Identity Check', rec, { 'Website Prepare Scan': ctx });
  const [fm] = await runNode('Website Form Match', idRow, { 'Website Prepare Scan': ctx, 'Website Reconcile OCR OMR': rec });
  const [d] = await runCode('Website Decide', [fm], { 'Website Prepare Scan': ctx, 'Website Reconcile OCR OMR': rec,
    'Website Identity Check': idRow });
  return { ctx, rec, idRow, fm, d };
}

// ------------------------------------------------------------------ tests
async function suite(rawIdType) {
  const t = [];
  const test = (name, fn) => t.push({ name: `[raw ids ${rawIdType}] ${name}`, fn });

  test('batch check: exactly 5 parameters (batch ids, term, student id, task_id); no OCR name/subject', async () => {
    const vals = evalReplacement(node('Website Identity Check'), { ocr_instructor: 'X', ocr_subject: 'Y', student_id: ' 1 ' },
      { 'Website Prepare Scan': { instructor_id: ID.juan, subject_id: ID.it101, term_id: null, task_id: 'web:p' } });
    assert.deepStrictEqual(vals, [ID.juan, ID.it101, null, '1', 'web:p']);
    const q = node('Website Identity Check').parameters.query;
    for (const gone of ['similarity', '$6', 'instructor_status', 'subject_status']) assert.ok(!q.includes(gone), gone);
  });
  test('batch check: batch names/codes + resolved term, whatever the form says (batch-only; the form is Form Match\'s job)', async () => {
    const first = await identity('Dr. Juan A. Dela Cruz, MIT', 'IT 101');
    assert.strictEqual(first.batch_instructor_exists, true);
    assert.strictEqual(first.batch_instructor_disabled, false);
    assert.strictEqual(first.batch_instructor_name, 'Juan Dela Cruz');
    assert.strictEqual(first.batch_subject_exists, true);
    assert.strictEqual(first.batch_subject_code, 'IT 101');
    assert.strictEqual(first.batch_subject_name, 'Introduction to Computing');
    assert.strictEqual(first.resolved_term_id, ID.term);
    assert.strictEqual(first.duplicate_student_exists, false);
    for (const k of ['instructor_status', 'subject_status', 'other_instructor_id', 'other_subject_id']) assert.ok(!(k in first), k);
    for (const [i, s] of [['AY/Semester: L U', 'Mà essor'], ['', ''], ['Pedro Reyes', 'IT 102'], ['Jose Rizal', 'XYZ 999']]) {
      assert.deepStrictEqual(await identity(i, s), first, `${i} / ${s}`);
    }
  });
  test('identity: batch references (disabled / missing / term fallback)', async () => {
    const d = await identity('Carlo Mendoza', 'IT 101', { instructor_id: ID.disabled });
    assert.strictEqual(d.batch_instructor_disabled, true);
    const m = await identity('Juan Dela Cruz', 'IT 101', { instructor_id: ID.missing, subject_id: ID.missing });
    assert.strictEqual(m.batch_instructor_exists, false);
    assert.strictEqual(m.batch_subject_exists, false);
    assert.strictEqual(m.batch_instructor_name, '');
    const tnull = await identity('Juan Dela Cruz', 'IT 101', { term_id: null });
    assert.strictEqual(tnull.resolved_term_id, ID.term);
  });
  test('identity: placeholder batch instructor is valid (not disabled)', async () => {
    const r = await identity('Nora Villanueva', 'IT 101', { instructor_id: ID.placeholder });
    assert.strictEqual(r.batch_instructor_exists, true);
    assert.strictEqual(r.batch_instructor_disabled, false, 'placeholder must not count as disabled');
    assert.strictEqual(r.batch_instructor_is_placeholder, true);
    assert.strictEqual(r.batch_instructor_name, 'Nora Villanueva');
  });
  test('identity: SQL-ish student id / OCR text is just data', async () => {
    const r = await identity("Robert'); DROP TABLE public.subjects; --\n$1", "IT 101'; DELETE FROM public.subjects; --",
      {}, { student_id: "S1'); DROP TABLE public.subjects; --\n$1" });
    assert.strictEqual(r.duplicate_student_exists, false);
    const c = await db.one('SELECT count(*)::int AS n FROM public.subjects');
    assert.strictEqual(c.n, 4);
  });

  test('duplicate check: false/false, then true after Website Raw Upsert stored the task_id', async () => {
    let r = await runNode('Website Duplicate Check', { task_id: 'web:t1' });
    assert.deepStrictEqual(r[0], { in_raw: false, in_queue: false });
    const ins = await websiteToRaw(decideItem());
    assert.strictEqual(ins.saved, true);
    assert.strictEqual(ins.row.task_id, 'web:t1');
    r = await runNode('Website Duplicate Check', { task_id: 'web:t1' });
    assert.strictEqual(r[0].in_raw, true);
  });
  test('duplicate student: identity check sees another page of the same student; raw upsert never overwrites it', async () => {
    const same = await identity('Juan Dela Cruz', 'IT 101', {}, { student_id: ' 2021-0001 ' });
    assert.strictEqual(same.duplicate_student_exists, true);
    assert.strictEqual(same.duplicate_student_task_id, 'web:t1');
    const own = await identity('Juan Dela Cruz', 'IT 101', { task_id: 'web:t1' }, { student_id: '2021-0001' });
    assert.strictEqual(own.duplicate_student_exists, false, 'the same page (same task_id) is not a duplicate');
    assert.strictEqual((await identity('Juan Dela Cruz', 'IT 101', {}, { student_id: '' })).duplicate_student_exists, false);
    assert.strictEqual((await identity('Juan Dela Cruz', 'IT 102', { subject_id: ID.it102 }, { student_id: '2021-0001' })).duplicate_student_exists, false);
    // race: Decide did not see it -> the upsert writes nothing (saved = false)
    const r = await websiteToRaw(decideItem({ task_id: 'web:t2', remark: 'Changed remark' }));
    assert.strictEqual(r.saved, false);
    const row = await db.one(`SELECT task_id, "Remarks_and_Suggestions" AS rem FROM public.sast_all_raw_data_survey WHERE student_id = '2021-0001'`);
    assert.strictEqual(row.task_id, 'web:t1');
    assert.match(row.rem, /Very good/);
    // a row without task_id (phone/sheet) for the same student also counts
    await db.none(`UPDATE public.sast_all_raw_data_survey SET task_id = NULL WHERE task_id = 'web:t1'`);
    assert.strictEqual((await identity('Juan Dela Cruz', 'IT 101', {}, { student_id: '2021-0001' })).duplicate_student_exists, true);
    await db.none(`UPDATE public.sast_all_raw_data_survey SET task_id = 'web:t1' WHERE task_id IS NULL`);
  });
  test('website raw rows: blank students (NULL) never collide and are stored exactly once', async () => {
    assert.strictEqual((await websiteToRaw(decideItem({ task_id: 'web:t3', student_id: null }))).saved, true);
    assert.strictEqual((await websiteToRaw(decideItem({ task_id: 'web:t4', student_id: '   ' }))).saved, true);
    assert.strictEqual((await websiteToRaw(decideItem({ task_id: 'web:t5', student_id: '2021-0002',
      ratings: { ...decideItem().ratings, m1: null, p10: null } }))).saved, true);
    const rows = await db.any(`SELECT student_id, task_id, m1, p10, validation_status, manually_corrected, sao_staff_id::text AS staff,
      "instructor_ID"::text AS iid, instructor, submitted_date::text AS d FROM public.sast_all_raw_data_survey ORDER BY id`);
    assert.strictEqual(rows.length, 4);
    assert.deepStrictEqual(rows.map((r) => r.task_id), ['web:t1', 'web:t3', 'web:t4', 'web:t5']);
    assert.strictEqual(rows[1].student_id, null);
    assert.strictEqual(rows[2].student_id, null);
    assert.strictEqual(rows[3].m1, null);
    assert.ok(rows.every((r) => r.validation_status === 'auto' && r.manually_corrected === false
      && r.staff === ID.staff && r.iid === ID.juan && r.instructor === 'Juan Dela Cruz' && r.d === '2026-09-26'));
  });
  test('shared upsert (phone/sheet rows): same student updates, NULL student inserts', async () => {
    const phone = await runCode('Code in JavaScript', [{ instructor_ID: ID.maria, true_subject_id: ID.ge104, current_term_id: ID.term,
      'Student ID': 'P-1', M1: 4, P1: 4, Date: '2026-09-20' }], { 'SAST image from phone': { body: { user_id: ID.staff } } });
    assert.ok(!('_raw_already_stored' in phone[0]));
    const a = await sharedRawUpsert(phone[0]);
    const b = await sharedRawUpsert({ ...phone[0], m1: 5 });
    assert.strictEqual(a.id, b.id);
    assert.strictEqual(Number(b.m1), 5);
    await db.none(`DELETE FROM public.sast_all_raw_data_survey WHERE "instructor_ID"::text = $1`, [ID.maria]);
  });
  test('recompute (update to overall total2 -> recompute_sast) from ALL raw rows (4 forms, one with 2 blanks)', async () => {
    const item = { instructor_id: ID.juan, term_id: ID.term, subject_id: ID.it101 };
    const agg = await runNode('update to overall total2', item);
    assert.strictEqual(Number(agg[0].total_responses), 4);
    const m = await db.one('SELECT * FROM public.management_results');
    const p = await db.one('SELECT * FROM public.performance_results');
    assert.strictEqual(Number(m.total_responses), 4);
    assert.strictEqual(Number(m.m1_total), 15);          // 5+5+5, the blank is skipped
    assert.strictEqual(Number(m.m1_count), 3);
    assert.strictEqual(Number(m.m1_mean), 5);            // 15/3 (was 15/4 = 3.75: blank counted as 0)
    assert.strictEqual(Number(m.grand_total), 195);
    assert.strictEqual(Number(m.overall_management_mean), 5); // mean of the 10 question means (was 195/40 = 4.88)
    assert.strictEqual(Number(m.average_per_response_management), 48.75); // 195 / 4 forms
    assert.strictEqual(Number(p.p10_total), 12);
    assert.strictEqual(Number(p.p10_count), 3);
    assert.strictEqual(Number(p.p10_mean), 4);
    assert.strictEqual(Number(p.grand_total), 156);
    assert.strictEqual(Number(p.overall_performance_mean), 4);
    const o = await db.one('SELECT * FROM public.overall_total_survey');
    assert.deepStrictEqual([o.total_responses, o.management_mean, o.performance_mean, o.combined_score_mean, o.overall_mean].map(Number),
      [4, 5, 4, 4.5, 4.5]);
    const q = await db.any(`SELECT category, question_number AS n, mean_score, response_count FROM public.overall_question_means
      ORDER BY category, question_number`);
    assert.strictEqual(q.length, 20);
    assert.deepStrictEqual([q[0].n, Number(q[0].mean_score), q[0].response_count], [1, 5, 3]);    // M1
    assert.deepStrictEqual([q[19].n, Number(q[19].mean_score), q[19].response_count], [10, 4, 3]); // P10
    const link = await db.any('SELECT * FROM public.instructor_subjects');
    assert.strictEqual(link.length, 1);
    await runNode('update to overall total2', item);   // idempotent, no double counting
    assert.strictEqual(Number((await db.one('SELECT total_responses FROM public.management_results')).total_responses), 4);
  });
  test('recompute holds a transaction advisory lock (a concurrent recompute waits)', async () => {
    const item = { instructor_id: ID.juan, term_id: ID.term, subject_id: ID.it101 };
    await db.tx(async (t) => {
      await t.any("SELECT pg_advisory_xact_lock(hashtext('ievaluate:recompute_aggregates'))");
      const n = node('update to overall total2');
      const values = evalReplacement(n, item, {});
      const q = pgp.helpers.concat([{ query: n.parameters.query, values }]);
      await assert.rejects(db.tx(async (t2) => {
        await t2.none('SET LOCAL statement_timeout = 700');
        await t2.multi(q);
      }), /statement timeout|canceling statement/);
    });
    // lock released at commit: runs normally now, and the DO statement adds no output row
    const out = await runNode('update to overall total2', item);
    assert.strictEqual(out.length, 1);
    assert.strictEqual(Number(out[0].total_responses), 4);
  });
  test('correction lookups (get instructor id7 / id9) resolve placeholder instructors; others unchanged', async () => {
    await db.none(`INSERT INTO public.user_info (id, first_name, last_name, account_status, disabled, is_placeholder)
      VALUES ('10000000-0000-4000-8000-00000000000b', 'Tomas', 'Aquino', 'pending', true, true)`);   // no department row
    const hooks = { 'get instructor id7': 'sast-manual-correction1', 'get instructor id9': 'import-error-correction1' };
    for (const name of ['get instructor id7', 'get instructor id9']) {
      // input = the scan image lookup's row; the body is read from the webhook by name
      const look = async (instructor) => (await runNode(name, { scan_image_id: null },
        { [hooks[name]]: { body: { instructor } } }))[0];
      assert.strictEqual((await look('Nora Villanueva')).instructor_id, ID.placeholder, `${name}: placeholder with role`);
      assert.strictEqual((await look('Tomas Aquino')).instructor_id, '10000000-0000-4000-8000-00000000000b', `${name}: placeholder w/o role`);
      assert.strictEqual((await look('Juan Dela Cruz')).instructor_id, ID.juan, `${name}: approved instructor`);
      assert.strictEqual((await look('Rosa Lim')).instructor_id, null, `${name}: pending non-placeholder still excluded`);
      assert.strictEqual((await look('Liza Gomez')).instructor_id, null, `${name}: SAO staff still excluded`);
      assert.strictEqual((await look('')).instructor_id, null);
    }
    await db.none(`DELETE FROM public.user_info WHERE id = '10000000-0000-4000-8000-00000000000b'`);
  });
  test('correction image lookups: original scan image (with data) is found, exactly one row, empty copies skipped', async () => {
    const img = (n) => `50000000-0000-4000-8000-00000000000${n}`;
    const fsq = '60000000-0000-4000-8000-000000000001';
    // original image (with data), plus the EMPTY copy the old flow inserted for the same task
    await db.none(`INSERT INTO public.scan_error_images (id, task_id, image_base64, created_at) VALUES
      ('${img(1)}', 'phone-task-1', 'iVBORw0KGgo=', now() - interval '1 hour'),
      ('${img(2)}', 'phone-task-1', NULL, now()),
      ('${img(3)}', 'phone-task-3', 'iVBORw0KGgo=', now())`);
    await db.none(`INSERT INTO public.failed_scan_queue (id, task_id, scan_image_id, status)
      VALUES ('${fsq}', 'phone-task-1', '${img(1)}', 'pending')`);
    const broken = await db.one(`INSERT INTO public.import_errors (source, task_id, scan_image_id)
      VALUES ('scan', 'phone-task-1', '${img(2)}') RETURNING id`);
    const good = await db.one(`INSERT INTO public.import_errors (source, task_id, scan_image_id)
      VALUES ('scan', 'phone-task-3', '${img(3)}') RETURNING id`);
    const sheet = await db.one(`INSERT INTO public.import_errors (source) VALUES ('google_sheet') RETURNING id`);

    const corr = async (body) => runNode('get existing scan image id', { body });
    let r = await corr({ failed_scan_id: fsq, task_id: 'phone-task-1' });
    assert.deepStrictEqual(r, [{ scan_image_id: img(1) }], 'failed scan id -> its image');
    r = await corr({ failed_scan_id: fsq, task_id: [null] });
    assert.deepStrictEqual(r, [{ scan_image_id: img(1) }], 'Flutter [null] task_id is ignored');
    r = await corr({ failed_scan_id: 'not-a-uuid', task_id: 'phone-task-1' });
    assert.deepStrictEqual(r, [{ scan_image_id: img(1) }], 'task_id fallback; the empty copy never wins');
    r = await corr({ task_id: 'phone-task-3' });
    assert.deepStrictEqual(r, [{ scan_image_id: img(3) }], 'no queue row: stored image of the task');
    r = await corr({});
    assert.deepStrictEqual(r, [{ scan_image_id: null }], 'nothing -> exactly one NULL row (chain continues)');
    r = await corr({ failed_scan_id: '60000000-0000-4000-8000-0000000000ff', task_id: 'nope' });
    assert.deepStrictEqual(r, [{ scan_image_id: null }]);

    const ie = async (body) => runNode('get existing scan image id1', { body });
    r = await ie({ import_error_id: good.id });
    assert.deepStrictEqual(r, [{ scan_image_id: img(3) }], 'import error with an image -> that image');
    r = await ie({ import_error_id: Number(broken.id) });
    assert.deepStrictEqual(r, [{ scan_image_id: img(1) }], 'import error on the EMPTY row -> original image');
    r = await ie({ import_error_id: String(sheet.id), raw_data: { task_id: null } });
    assert.deepStrictEqual(r, [{ scan_image_id: null }], 'Google Sheet error -> NULL (source stays google_sheet)');
    r = await ie({ import_error_id: 'x', raw_data: 'not json' });
    assert.deepStrictEqual(r, [{ scan_image_id: null }]);
    await db.none('DELETE FROM public.import_errors');
    await db.none('DELETE FROM public.failed_scan_queue');
    await db.none('DELETE FROM public.scan_error_images');
  });
  test('main flow (phone/sheet/correction): recompute from ALL raw rows, ignoring per-execution totals', async () => {
    // Three forms for Pedro / IT 102 stored by earlier executions (phone upsert,
    // sheet import, manual correction). Ratings: all 3s, all 4s, m=5 with p blank.
    const mc = Array.from({ length: 10 }, (_, i) => `m${i + 1}`);
    const pc = Array.from({ length: 10 }, (_, i) => `p${i + 1}`);
    const rowSql = (student, m, p, task) => `INSERT INTO public.sast_all_raw_data_survey
      ("instructor_ID", term_id, subject_id, student_id, task_id, validation_status, manually_corrected, ${mc.join(',')}, ${pc.join(',')})
      VALUES ('${ID.pedro}', '${ID.term}', '${ID.it102}', ${student === null ? 'NULL' : `'${student}'`}, '${task}', 'auto', false,
      ${mc.map(() => m).join(',')}, ${pc.map(() => (p === null ? 'NULL' : p)).join(',')});`;
    await db.none(rowSql('s1', 3, 3, 'phone-1') + rowSql('s2', 4, 4, 'sheet') + rowSql(null, 5, null, 'corrected'));
    // Stale single-form aggregate, as the old calculate -> send logic left it.
    await db.none(`INSERT INTO public.management_results (instructor_id, term_id, subject_id, total_responses, m1_total, grand_total)
      VALUES ('${ID.pedro}', '${ID.term}', '${ID.it102}', 1, 5, 50)`);
    const before = await db.one('SELECT total_responses FROM public.management_results WHERE subject_id = $1', [ID.it101]);

    // Item exactly as 'calculate mangement1' emits it for a one-form execution.
    const mItem = { instructor_id: ID.pedro, term_id: ID.term, subject_id: ID.it102, total_responses: 1, grand_total: 50,
      overall_management_mean: 5, average_per_response_management: 50 };
    for (let i = 1; i <= 10; i++) { mItem[`m${i}_total`] = 5; mItem[`m${i}_mean`] = 5; }
    const r1 = await runNode('update to overall total2', mItem);
    assert.strictEqual(Number(r1[0].total_responses), 3);
    const expectAll = async () => {
      const m = await db.one('SELECT * FROM public.management_results WHERE subject_id = $1', [ID.it102]);
      const p = await db.one('SELECT * FROM public.performance_results WHERE subject_id = $1', [ID.it102]);
      assert.strictEqual(Number(m.total_responses), 3, 'all 3 raw rows, not the 1 form of this execution');
      assert.strictEqual(Number(m.m1_total), 12);
      assert.strictEqual(Number(m.m1_mean), 4);
      assert.strictEqual(Number(m.grand_total), 120);
      assert.strictEqual(Number(m.overall_management_mean), 4);
      assert.strictEqual(Number(m.average_per_response_management), 40);
      assert.strictEqual(Number(p.total_responses), 2);    // the form with all P blank does not count
      assert.strictEqual(Number(p.p1_total), 7);           // 3 + 4, blank skipped
      assert.strictEqual(Number(p.p1_count), 2);
      assert.strictEqual(Number(p.p1_mean), 3.5);          // 7/2 (was 7/3 = 2.33)
      assert.strictEqual(Number(p.overall_performance_mean), 3.5);
      assert.strictEqual(Number(p.average_per_response_performance), 35);
    };
    await expectAll();
    // a second run (another item of the same pair) is idempotent.
    const pItem = { instructor_id: ID.pedro, term_id: ID.term, subject_id: ID.it102, total_responses: 1, p1_total: 4 };
    await runNode('update to overall total2', pItem);
    await expectAll();
    // Other (instructor, term, subject) aggregates are untouched.
    const after = await db.one('SELECT total_responses FROM public.management_results WHERE subject_id = $1', [ID.it101]);
    assert.strictEqual(Number(after.total_responses), Number(before.total_responses));
    // calculate nodes emit term_id = null when the row had no term: nothing is written, no NULL-term link.
    await runNode('update to overall total2', { instructor_id: ID.pedro, term_id: null, subject_id: ID.it102 });
    const nulls = await db.one(`SELECT
      (SELECT count(*) FROM public.management_results WHERE term_id IS NULL)::int AS m,
      (SELECT count(*) FROM public.instructor_subjects WHERE term_id IS NULL)::int AS l`);
    assert.deepStrictEqual(nulls, { m: 0, l: 0 });
  });
  // ---- migration 013: blanks are left out of both the sum and the count
  const QM = [...Array(10)].map((_, i) => `m${i + 1}`);
  const QP = [...Array(10)].map((_, i) => `p${i + 1}`);
  // one raw form for Maria / term2; r = { m1: 5, ... } (missing = blank)
  const mariaForm = (subject, student, r) => db.none(`INSERT INTO public.sast_all_raw_data_survey
    ("instructor_ID", term_id, subject_id, student_id, validation_status, manually_corrected, ${[...QM, ...QP].join(', ')})
    VALUES ($1, $2, $3, $4, 'auto', false, $5:csv)`,
  [ID.maria, ID.term2, subject, student, [...QM, ...QP].map((k) => (r[k] === undefined ? null : r[k]))]);
  const all = (keys, v) => Object.fromEntries(keys.map((k) => [k, v]));
  const recompute = async () => db.one('SELECT * FROM public.recompute_sast($1, $2)', [ID.maria, ID.term2]);
  const mgmt = (subject) => db.oneOrNone(`SELECT * FROM public.management_results
    WHERE instructor_id = $1 AND term_id = $2 AND subject_id = $3`, [ID.maria, ID.term2, subject]);
  const perf = (subject) => db.oneOrNone(`SELECT * FROM public.performance_results
    WHERE instructor_id = $1 AND term_id = $2 AND subject_id = $3`, [ID.maria, ID.term2, subject]);
  const cleanMaria = async () => {
    await db.none(`DELETE FROM public.sast_all_raw_data_survey WHERE "instructor_ID"::text = $1`, [ID.maria]);
    await recompute();
    await db.none('DELETE FROM public.instructor_subjects WHERE instructor_id = $1', [ID.maria]);
  };

  test('013: two forms m1 = 5 and 4 -> 4.50; a third form with m1 blank -> still 4.50 (count 2)', async () => {
    await mariaForm(ID.it101, 'A1', { ...all(QM, 5), ...all(QP, 4) });
    await mariaForm(ID.it101, 'A2', { ...all(QM, 4), ...all(QP, 4) });
    let r = await recompute();
    let m = await mgmt(ID.it101);
    assert.deepStrictEqual([m.m1_mean, m.m1_count, m.total_responses].map(Number), [4.5, 2, 2]);
    assert.strictEqual(r.total_responses, 2);
    await mariaForm(ID.it101, 'A3', { ...all(QM, 3), m1: null, ...all(QP, 4) });
    r = await recompute();
    m = await mgmt(ID.it101);
    assert.strictEqual(Number(m.m1_mean), 4.5, 'blank m1 is not a 0');
    assert.strictEqual(Number(m.m1_count), 2);
    assert.strictEqual(Number(m.m1_total), 9);
    assert.strictEqual(Number(m.m2_mean), 4);           // 5, 4, 3
    assert.strictEqual(Number(m.m2_count), 3);
    assert.strictEqual(Number(m.total_responses), 3);
    assert.strictEqual(Number(m.overall_management_mean), 4.05);  // (4.50 + 9 x 4.00) / 10
    assert.strictEqual(r.total_responses, 3);
  });
  test('013: category mean = mean of the ROUNDED question means; all-blank question -> NULL', async () => {
    // M1-M5 = 5, 4, 3 and M6-M10 = 5, 4, blank; P7 blank on every form
    const f = (a, b, p) => ({ m1: a, m2: a, m3: a, m4: a, m5: a, m6: b, m7: b, m8: b, m9: b, m10: b, ...all(QP, p), p7: null });
    await mariaForm(ID.ge104, 'B1', f(5, 5, 3));
    await mariaForm(ID.ge104, 'B2', f(4, 4, 3));
    await mariaForm(ID.ge104, 'B3', f(3, null, 3));
    const r = await recompute();
    const m = await mgmt(ID.ge104);
    assert.strictEqual(Number(m.m1_mean), 4);
    assert.strictEqual(Number(m.m6_mean), 4.5);
    assert.strictEqual(Number(m.m6_count), 2);
    assert.strictEqual(Number(m.overall_management_mean), 4.25, '(5 x 4.00 + 5 x 4.50) / 10');
    assert.strictEqual(Number(m.grand_total), 105);
    assert.strictEqual(Number(m.average_per_response_management), 35);
    const p = await perf(ID.ge104);
    assert.strictEqual(p.p7_mean, null, 'nobody answered P7: NULL, not 0');
    assert.strictEqual(Number(p.p7_count), 0);
    assert.strictEqual(Number(p.p7_total), 0);
    assert.strictEqual(Number(p.overall_performance_mean), 3, 'P7 is left out of the category mean');
    // Instructor + term totals pooled over both subjects (6 forms):
    //   M1 = 5,4,_,5,4,3 -> 4.20; M2-M5 = 5,4,3 twice -> 4.00; M6-M10 = 5,4,3,5,4,_ -> 4.20 => 4.12
    //   P (not 7) = 4,4,4,3,3,3 -> 3.50; P7 = 4,4,4 -> 4.00 => 3.55; combined (4.12 + 3.55) / 2 = 3.835 -> 3.84
    assert.deepStrictEqual([r.total_responses, Number(r.management_mean), Number(r.performance_mean), Number(r.combined_score_mean)],
      [6, 4.12, 3.55, 3.84]);
    const o = await db.one('SELECT * FROM public.overall_total_survey WHERE instructor_id = $1 AND term_id = $2', [ID.maria, ID.term2]);
    assert.deepStrictEqual([o.total_responses, o.management_total, o.performance_total, o.combined_total].map(Number), [6, 222, 201, 423]);
    assert.deepStrictEqual([o.management_mean, o.performance_mean, o.combined_score_mean, o.overall_mean].map(Number),
      [4.12, 3.55, 3.84, 3.84]);
    const q = await db.any(`SELECT category, question_number AS n, mean_score, response_count FROM public.overall_question_means
      WHERE instructor_id = $1 AND term_id = $2 ORDER BY category, question_number`, [ID.maria, ID.term2]);
    assert.strictEqual(q.length, 20);
    const qm = (cat, n) => q.find((x) => x.category === cat && x.n === n);
    assert.deepStrictEqual([Number(qm('Management', 1).mean_score), qm('Management', 1).response_count], [4.2, 5]);
    assert.deepStrictEqual([Number(qm('Performance', 7).mean_score), qm('Performance', 7).response_count], [4, 3]);
    const links = await db.any('SELECT subject_id::text AS s FROM public.instructor_subjects WHERE instructor_id = $1 ORDER BY 1', [ID.maria]);
    assert.deepStrictEqual(links.map((x) => x.s), [ID.it101, ID.ge104].sort());
  });
  test('013: a subject whose forms moved away loses its rows; all-blank category; no forms left', async () => {
    await db.none(`UPDATE public.sast_all_raw_data_survey SET subject_id = $1
      WHERE "instructor_ID"::text = $2 AND subject_id::text = $3`, [ID.ddmgt3, ID.maria, ID.ge104]);
    await recompute();
    assert.strictEqual(await mgmt(ID.ge104), null, 'moved subject: management row deleted');
    assert.strictEqual(await perf(ID.ge104), null, 'moved subject: performance row deleted');
    assert.strictEqual(Number((await mgmt(ID.ddmgt3)).overall_management_mean), 4.25);
    assert.strictEqual(Number((await mgmt(ID.it101)).total_responses), 3, 'other subject untouched');
    // one form, every P blank: no performance row, combined = management
    await db.none(`DELETE FROM public.sast_all_raw_data_survey WHERE "instructor_ID"::text = $1`, [ID.maria]);
    await mariaForm(ID.it101, 'C1', { ...all(QM, 4), m10: 5 });
    let r = await recompute();
    assert.strictEqual(await perf(ID.it101), null, 'no Performance answer: no performance row');
    assert.strictEqual(await mgmt(ID.ddmgt3), null);
    assert.strictEqual(Number((await mgmt(ID.it101)).overall_management_mean), 4.1);
    assert.deepStrictEqual([r.total_responses, Number(r.management_mean), r.performance_mean, Number(r.combined_score_mean)],
      [1, 4.1, null, 4.1]);
    const o = await db.one('SELECT * FROM public.overall_total_survey WHERE instructor_id = $1 AND term_id = $2', [ID.maria, ID.term2]);
    assert.deepStrictEqual([o.performance_mean, Number(o.combined_score_mean), Number(o.overall_mean)], [null, 4.1, 4.1]);
    const p1 = await db.one(`SELECT mean_score, response_count FROM public.overall_question_means
      WHERE instructor_id = $1 AND term_id = $2 AND category = 'Performance' AND question_number = 1`, [ID.maria, ID.term2]);
    assert.deepStrictEqual([p1.mean_score, p1.response_count], [null, 0]);
    // no forms left: totals and question means removed, still one row back
    await db.none(`DELETE FROM public.sast_all_raw_data_survey WHERE "instructor_ID"::text = $1`, [ID.maria]);
    r = await recompute();
    assert.deepStrictEqual([r.total_responses, r.combined_score_mean], [0, null]);
    const left = await db.one(`SELECT
      (SELECT count(*) FROM public.management_results WHERE instructor_id = $1)::int AS m,
      (SELECT count(*) FROM public.performance_results WHERE instructor_id = $1)::int AS p,
      (SELECT count(*) FROM public.overall_total_survey WHERE instructor_id = $1)::int AS o,
      (SELECT count(*) FROM public.overall_question_means WHERE instructor_id = $1)::int AS q`, [ID.maria]);
    assert.deepStrictEqual(left, { m: 0, p: 0, o: 0, q: 0 });
    await cleanMaria();
  });
  test('013: raw answers must be blank or 1..5 (CHECK rejects 0 and 6)', async () => {
    for (const bad of [0, 6]) {
      await assert.rejects(mariaForm(ID.it101, `X${bad}`, { m1: 5, p10: bad }), /sast_raw_p10_rating_1_to_5/);
      await assert.rejects(mariaForm(ID.it101, `Y${bad}`, { m3: bad }), /sast_raw_m3_rating_1_to_5/);
    }
    await mariaForm(ID.it101, 'OK', { m1: 1, p10: 5 });   // 1, 5 and blanks are fine
    await cleanMaria();
  });
  test('013: recompute_sast / sast_answers are not executable by PUBLIC', async () => {
    const acl = await db.one(`SELECT has_function_privilege('public', 'public.recompute_sast(uuid, uuid)', 'EXECUTE') AS r,
      has_function_privilege('public', 'public.sast_answers(uuid, uuid)', 'EXECUTE') AS a`);
    assert.deepStrictEqual(acl, { r: false, a: false });
  });
  test('flagged: build flagged record1 (website record) only uses real failed_scan_queue columns', async () => {
    const webRec = {
      _source: 'website', task_id: 'web:f1', user_id: ID.staff, term_id: ID.term, batch_id: ID.batch,
      table_found: true, grid_source: 'corners', validation_status: 'flagged',
      review_reasons: ['omr_agreement_below_30', "it's"], omr_agreement: null,
      partial_data: { instructor: 'Juan Dela Cruz', subject: 'IT 101', remarks: "multi\nline -- 'quoted'", ratings: { m1: 5 } },
      scan_image: 'QUJD',
    };
    // 'Save scan error image' (Supabase REST insert) -> row id
    const img = await db.one('INSERT INTO public.scan_error_images (task_id, image_base64) VALUES ($1, $2) RETURNING id',
      [webRec.task_id, webRec.scan_image]);
    const [record] = await runCode('build flagged record1', [{}],
      { 'Website Flagged Record': webRec, 'Save scan error image': [{ id: img.id }] });
    const cols = (await db.any(`SELECT column_name FROM information_schema.columns
      WHERE table_schema = 'public' AND table_name = 'failed_scan_queue'`)).map((r) => r.column_name);
    const unknown = Object.keys(record).filter((k) => !cols.includes(k));
    assert.deepStrictEqual(unknown, [], 'PostgREST rejects unknown columns');
    // 'save flagged to failed_scan_queue1' posts the record as JSON (PostgREST insert)
    await db.none(`INSERT INTO public.failed_scan_queue ($1:name)
      SELECT $1:name FROM jsonb_populate_record(NULL::public.failed_scan_queue, $2::jsonb)`, [Object.keys(record), JSON.stringify(record)]);
    const row = await db.one('SELECT * FROM public.failed_scan_queue');
    assert.deepStrictEqual(row.review_reasons, ['omr_agreement_below_30', "it's"]);
    assert.strictEqual(row.status, 'pending');
    assert.strictEqual(row.validation_status, 'flagged');
    assert.strictEqual(row.user_id, ID.staff);
    assert.strictEqual(row.term_id, ID.term);
    assert.strictEqual(row.batch_id, ID.batch);
    assert.strictEqual(row.scan_image_id, img.id);
    assert.strictEqual(row.partial_data.instructor, 'Juan Dela Cruz');
    assert.strictEqual(row.partial_data.subject, 'IT 101');
    assert.strictEqual(row.partial_data.remarks, "multi\nline -- 'quoted'");
    const dup = await runNode('Website Duplicate Check', { task_id: 'web:f1' });
    assert.deepStrictEqual(dup[0], { in_raw: false, in_queue: true });
  });
  async function insertFlagged(record) {
    const cols = (await db.any(`SELECT column_name FROM information_schema.columns
      WHERE table_schema = 'public' AND table_name = 'failed_scan_queue'`)).map((r) => r.column_name);
    assert.deepStrictEqual(Object.keys(record).filter((k) => !cols.includes(k)), [], 'PostgREST rejects unknown columns');
    await db.none(`INSERT INTO public.failed_scan_queue ($1:name)
      SELECT $1:name FROM jsonb_populate_record(NULL::public.failed_scan_queue, $2::jsonb)`, [Object.keys(record), JSON.stringify(record)]);
    return db.one('SELECT * FROM public.failed_scan_queue WHERE task_id = $1', [record.task_id]);
  }
  test('website end to end: batch name on the form + (25%) agreement -> flagged ONLY omr_agreement_below_30', async () => {
    const gem = gemResp({ instructor: 'Dr. Juan A. Dela Cruz, MIT', subject: 'IT 101' });
    // Python blank on 15 rows Gemini reads -> 5 of 20 rows agree (a confident scanner would win instead)
    const { ctx, d } = await websiteDecide('web:e2e-flag', gem, pyResp({}, { blankRows: 15 }));
    assert.strictEqual(d.decision, 'flagged');
    assert.deepStrictEqual(d.review_reasons, ['omr_agreement_below_30']);
    assert.strictEqual(d.identity_check, null);
    const [wrec] = await runCode('Website Flagged Record', [d], { 'Website Prepare Scan': ctx });
    const [record] = await runCode('build flagged record1', [{}], { 'Website Flagged Record': wrec, 'Save scan error image': [{ id: null }] });
    const row = await insertFlagged(record);
    assert.deepStrictEqual(row.review_reasons, ['omr_agreement_below_30']);
    assert.strictEqual(row.partial_data.instructor, 'Juan Dela Cruz', 'the batch instructor DB name, not the OCR text');
    assert.strictEqual(row.partial_data.subject, 'IT 101');
    assert.strictEqual(row.partial_data.ocr_instructor, 'Dr. Juan A. Dela Cruz, MIT');
    assert.strictEqual(row.partial_data.instructor_match, 'same');
    assert.strictEqual(row.partial_data.subject_match, 'same');
    assert.strictEqual(row.partial_data.agreement_percent, 25);
    assert.strictEqual(row.partial_data.student_id, '2021-7777', 'student id from Gemini, not Python OCR');
    assert.match(row.partial_data.review_note, /5 of 20 rows \((25%)\)/);
    assert.ok(!('identity_check' in row.partial_data));
    assert.strictEqual(row.omr_comparison.agreement_percent, 25);
    assert.strictEqual(row.omr_comparison.comparisons.length, 20);
  });
  test('website end to end: garbage OCR instructor/subject -> not found, NOT flagged, credited to the batch', async () => {
    const { d, fm } = await websiteDecide('web:e2e-garbage', gemResp({ student_id: '2021-8888' }));   // 'AY/Semester: L U' / 'Mà essor'
    assert.strictEqual(d.decision, 'clean');
    assert.deepStrictEqual(d.review_reasons, []);
    assert.strictEqual(fm.ocr_instructor_norm, 'ay semester l u');
    assert.strictEqual(d.partial_data.instructor_match, 'not_found');
    assert.strictEqual(d.partial_data.subject_match, 'not_found');
    assert.strictEqual(d.instructor_id, ID.juan);
    assert.strictEqual(d.subject_id, ID.it101);
    assert.strictEqual(d.identity_check, null);
  });
  test('website end to end: batch instructor/subject on the form (title, code), engines agree -> clean, credited to the batch', async () => {
    const { d } = await websiteDecide('web:e2e-clean', gemResp({ student_id: '2021-8888', instructor: 'Sir J. Dela Cruz',
      subject: 'IT-101 Intro to Computing' }));
    assert.strictEqual(d.decision, 'clean');
    assert.deepStrictEqual(d.review_reasons, []);
    // blank / unreadable text on the form -> held for review with the batch pre-selected
    for (const [i, sj] of [['', ''], ['N/A', 'N/A'], ['J.', '-']]) {
      const b = await websiteDecide('web:e2e-blank', gemResp({ instructor: i, subject: sj }));
      assert.deepStrictEqual(b.d.review_reasons, ['instructor_missing', 'subject_missing'], `${i} / ${sj}`);
      assert.strictEqual(b.d.partial_data.instructor_match, 'unreadable');
      assert.strictEqual(b.d.partial_data.suggested_instructor_id, ID.juan);
    }
    // blank student id -> student_id_missing
    const noId = await websiteDecide('web:e2e-noid', gemResp({ student_id: '', instructor: 'Sir J. Dela Cruz', subject: 'IT-101' }));
    assert.deepStrictEqual(noId.d.review_reasons, ['student_id_missing']);
    assert.strictEqual(noId.d.student_id, null);
    const r = await websiteToRaw(d);
    assert.strictEqual(r.saved, true);
    assert.strictEqual(r.row.instructor, 'Juan Dela Cruz');
    assert.strictEqual(r.row.student_id, '2021-8888');
    assert.strictEqual(String(r.row.m1), '5');
  });
  test('phone end to end: new reconciler record (agreement, Gemini-only text) fits failed_scan_queue', async () => {
    const hook = { body: { task_id: 'phone-e2e', user_id: ID.staff, term_id: ID.term, image: 'data:image/jpeg;base64,QUJD' } };
    const gem = gemResp({ instructor: '', subject: '' });
    const [rec] = await runCode('reconcile ocr and omr1', [{}],
      { 'image to ocr and omr python': pyResp({}, { blankRows: 15 }), 'ai validation ocr and omr': gem, 'SAST image from phone': hook });
    assert.deepStrictEqual(rec.review_reasons, ['missing_instructor', 'missing_subject', 'omr_agreement_below_30']);
    assert.strictEqual(rec.instructor, '', 'no Python OCR fallback');
    const [record] = await runCode('build flagged record1', [{}], { 'reconcile ocr and omr1': rec, 'Save scan error image': [{ id: null }] });
    const row = await insertFlagged(record);
    assert.strictEqual(row.partial_data.agreement_percent, 25);
    assert.strictEqual(row.partial_data.student_id, '2021-7777');
    assert.strictEqual(row.omr_comparison.agreement_min_percent, 30);
    assert.strictEqual(row.batch_id, null);
  });
  test('flagged: Get staff FCM for flagged scan reads user_id from build flagged record1 (queue POST has no body)', async () => {
    await db.none(`UPDATE public.user_info SET fcm_token = 'tok-staff' WHERE id = $1`, [ID.staff]);
    // $json = output of 'save flagged to failed_scan_queue1' (Prefer: return=minimal -> no body)
    const n = node('Get staff FCM for flagged scan');
    const vals = evalReplacement(n, {}, { 'build flagged record1': { user_id: ID.staff } });
    assert.ok(Array.isArray(vals), 'explicit array (a bare undefined fails in n8n)');
    const hit = await runNode('Get staff FCM for flagged scan', {}, { 'build flagged record1': { user_id: ID.staff } });
    assert.deepStrictEqual(hit, [{ fcm_token: 'tok-staff' }]);
    // no staff id (phone record without user_id): NULL -> no row, no error, no push
    for (const rec of [{ user_id: null }, {}]) {
      assert.deepStrictEqual(evalReplacement(n, {}, { 'build flagged record1': rec }), [null]);
      assert.deepStrictEqual(await runNode('Get staff FCM for flagged scan', {}, { 'build flagged record1': rec }), []);
    }
    await db.none(`UPDATE public.user_info SET fcm_token = NULL WHERE id = $1`, [ID.staff]);
  });
  test('phone: get subject id4 with an unmatched instructor (empty lookup item) still resolves the subject', async () => {
    await db.none('DELETE FROM public.instructor_subjects');
    // 'get instructor id6' found nobody: alwaysOutputData -> {} ; subject text comes from If31's item
    const miss = await runNode('get subject id4', {}, { If31: { subject: 'IT 101' } });
    assert.strictEqual(miss.length, 1);
    assert.strictEqual(miss[0].subject_id, ID.it101);
    assert.strictEqual(miss[0].instructor_id, null);
    assert.strictEqual(miss[0].auto_assigned, false);
    // matched instructor: subject_search_term from the lookup row; a comma no longer splits the value
    const hit = await runNode('get subject id4',
      { instructor_id: ID.juan, subject_search_term: 'Introduction to Computing' }, { If31: { subject: 'x' } });
    assert.strictEqual(hit[0].subject_id, ID.it101);
    assert.strictEqual(hit[0].instructor_id, ID.juan);
    assert.strictEqual(hit[0].auto_assigned, true);
    const comma = await runNode('get subject id4', { instructor_id: null, subject_search_term: 'Math, Science' }, { If31: {} });
    assert.deepStrictEqual([comma.length, comma[0].subject_id], [1, null]);
    const links = await db.one('SELECT count(*)::int AS n FROM public.instructor_subjects');
    assert.strictEqual(links.n, 1, 'only the matched instructor is linked');
    await db.none('DELETE FROM public.instructor_subjects');
  });
  // ---------------------------------------------------------------- migration 004 (sexual-word clean-up)
  const INSTR_ = ID.juan;
  async function seedRemarks() {
    await db.none(`DELETE FROM public.sast_all_raw_data_survey; DELETE FROM public.student_remarks;
      DELETE FROM public.failed_scan_queue; DELETE FROM public.import_errors; DELETE FROM public.instructor_wordcloud;`);
    await db.none(`INSERT INTO public.sast_all_raw_data_survey ("instructor_ID", subject_id, term_id, student_id, "Remarks_and_Suggestions")
      VALUES ($1, $2, $3, 'S1', 'Maayo si sir pero iyot'), ($1, $2, $3, 'S2', 'Gago ka sir, yawa!'), ($1, $2, $3, 'S3', NULL)`,
    [INSTR_, ID.it101, ID.term]);
    await db.none(`INSERT INTO public.student_remarks (instructor_id, subject_id, term_id, remark, tone, created_at) VALUES
      ($1, $2, $3, 'iyot ka sir', 'Critical', '2026-01-01'),
      ($1, $2, $3, 'iyut ka sir', 'Critical', '2026-01-02'),
      ($1, $2, $3, '**** ka', 'Critical', '2026-01-01'),
      ($1, $2, $3, 'bilat ka', 'Critical', '2026-01-03'),
      ($1, $2, $3, 'iyot ka', 'Critical', '2026-01-04'),
      ($1, $2, $3, 'kupal si sir', 'Critical', '2026-01-05'),
      ($1, NULL, $3, 'kantot', 'Critical', '2026-01-06'),
      ($1, NULL, $3, 'kantut', 'Critical', '2026-01-07')`, [INSTR_, ID.it101, ID.term]);
    await db.none(`INSERT INTO public.failed_scan_queue (task_id, partial_data, validated_data) VALUES
      ('m1', '{"remarks": "pak yu sir", "instructor": "X"}', '{"remarks": "pak yu sir"}'),
      ('m2', '{"remarks": "yawa ka"}', NULL), ('m3', '{"instructor": "no remarks key"}', NULL)`);
    await db.none(`INSERT INTO public.import_errors (error_type, raw_data) VALUES
      ('instructor_not_found', '{"Remarks & Suggestions": "tite", "remarks": "b1lat", "M1": 5}'),
      ('subject_not_found', '{"Remarks & Suggestions": "animal ka"}')`);
    await db.none(`INSERT INTO public.instructor_wordcloud (instructor_id, term_id, word, count) VALUES
      ($1, $2, 'Teaching clarity', 3), ($1, $2, 'iyot', 1), ($1, $2, '****', 2), ($1, $2, 'Kupal', 1)`, [INSTR_, ID.term]);
  }
  test('migration 004: SQL filter masks exactly like the n8n JS filter (synthetic corpus) and embeds the same patterns', async () => {
    const pf = jsFilter();
    const tok = pf.PF_PATTERNS.token;
    const lines = [];
    for (let i = 0; i < tok.length; i += 90) lines.push(`    '${tok.slice(i, i + 90)}'`);
    assert.ok(MIG.includes(lines.join(' ||\n')), 'token pattern in the SQL differs from profanity.js (--print-profanity-patterns)');
    for (const [a, b] of pf.PF_PATTERNS.phrases) assert.ok(MIG.includes(`('${a}', '${b}')`), `phrase ${a} ${b}`);
    const corpus = filterCorpus();
    const rows = await db.task(async (tk) => {
      await tk.multi(MIG_FUNCTIONS);
      return tk.any('SELECT u.s, pg_temp.mask_sexual_words(u.s) AS m FROM unnest($1::text[]) WITH ORDINALITY AS u(s, i) ORDER BY u.i', [corpus]);
    });
    assert.strictEqual(rows.length, corpus.length);
    let masked = 0;
    for (const r of rows) {
      const want = pf.maskProfanity(r.s);
      assert.strictEqual(r.m, want, `SQL ${JSON.stringify(r.m)} vs JS ${JSON.stringify(want)} for ${JSON.stringify(r.s)}`);
      if (want !== r.s) masked++;
    }
    assert.ok(masked > 200 && masked < corpus.length - 100, `corpus covers both cases (${masked}/${corpus.length} masked)`);
  });
  test('migration 004: preview changes nothing and lists every row to mask / delete', async () => {
    await seedRemarks();
    const before = await db.one(`SELECT (SELECT json_agg(s ORDER BY id) FROM public.student_remarks s) AS a,
      (SELECT count(*)::int FROM public.instructor_wordcloud) AS w`);
    const preview = await db.task(async (tk) => (await tk.multi(MIG_PART1)).pop());
    const after = await db.one(`SELECT (SELECT json_agg(s ORDER BY id) FROM public.student_remarks s) AS a,
      (SELECT count(*)::int FROM public.instructor_wordcloud) AS w`);
    assert.deepStrictEqual(after, before, 'PART 1 is read-only');
    const got = preview.map((r) => `${r.target}|${r.action}|${r.after ?? ''}`).sort();
    assert.deepStrictEqual(got, [
      'failed_scan_queue.validated_data|mask|*** ** sir',
      'failed_scan_queue|mask|*** ** sir',
      'import_errors|mask|**** | *****',
      'instructor_wordcloud|delete|', 'instructor_wordcloud|delete|',
      'sast_all_raw_data_survey|mask|Maayo si sir pero ****',
      'student_remarks|delete (duplicate after masking)|******',
      'student_remarks|delete (duplicate after masking)|**** ka',
      'student_remarks|delete (duplicate after masking)|**** ka sir',
      'student_remarks|mask|******', 'student_remarks|mask|***** ka', 'student_remarks|mask|**** ka sir',
    ].sort());
  });
  test('migration 004: apply masks every target, resolves student_remarks duplicates, and is idempotent', async () => {
    await seedRemarks();
    const res = await db.task((tk) => tk.multi(MIG));
    assert.deepStrictEqual(res.pop(), [{ raw_rows: '0', student_remarks: '0', failed_scan_queue: '0', import_errors: '0', wordcloud_rows: '0' }]);
    const raw = await db.map('SELECT "Remarks_and_Suggestions" AS r FROM public.sast_all_raw_data_survey ORDER BY student_id', [], (x) => x.r);
    assert.deepStrictEqual(raw, ['Maayo si sir pero ****', 'Gago ka sir, yawa!', null]);
    const sr = (await db.map('SELECT remark, created_at::date::text AS d, subject_id IS NULL AS ns FROM public.student_remarks',
      [], (x) => `${x.remark}|${x.d}|${x.ns}`)).sort();
    assert.deepStrictEqual(sr, [
      '******|2026-01-06|true',        // kantot kept (older); kantut would clash -> deleted
      '***** ka|2026-01-03|false',     // bilat ka: masked (different length, no clash)
      '**** ka|2026-01-01|false',      // the clean '**** ka' kept; 'iyot ka' would clash -> deleted
      '**** ka sir|2026-01-01|false',  // the oldest of iyot/iyut ka sir kept
      'kupal si sir|2026-01-05|false', // insults untouched
    ].sort());
    const q = await db.map('SELECT task_id, partial_data, validated_data FROM public.failed_scan_queue ORDER BY task_id', [], (x) => x);
    assert.deepStrictEqual(q[0].partial_data, { remarks: '*** ** sir', instructor: 'X', remarks_masked: true });
    assert.deepStrictEqual(q[0].validated_data, { remarks: '*** ** sir' });
    assert.deepStrictEqual(q[1].partial_data, { remarks: 'yawa ka' });
    assert.deepStrictEqual(q[2].partial_data, { instructor: 'no remarks key' });
    const ie = await db.map('SELECT raw_data FROM public.import_errors ORDER BY id', [], (x) => x.raw_data);
    assert.deepStrictEqual(ie, [{ 'Remarks & Suggestions': '****', remarks: '*****', M1: 5 }, { 'Remarks & Suggestions': 'animal ka' }]);
    const wc = await db.map('SELECT word FROM public.instructor_wordcloud ORDER BY word', [], (x) => x.word);
    assert.deepStrictEqual(wc, ['Kupal', 'Teaching clarity']);
    // second run: nothing left to change
    const snap = async () => db.one(`SELECT (SELECT json_agg(x ORDER BY id) FROM public.student_remarks x) a,
      (SELECT json_agg(x ORDER BY id) FROM public.failed_scan_queue x) b, (SELECT json_agg(x ORDER BY id) FROM public.import_errors x) c,
      (SELECT json_agg(x ORDER BY id) FROM public.sast_all_raw_data_survey x) d, (SELECT json_agg(x ORDER BY id) FROM public.instructor_wordcloud x) e`);
    const s1 = await snap();
    await db.task((tk) => tk.multi(MIG));
    assert.deepStrictEqual(await snap(), s1, 'idempotent');
  });

  // ---------------------------------------------------------------- the form's instructor/subject vs the database
  test('form match: 5 array parameters (batch ids, OCR instructor/subject, term); exactly one row', async () => {
    const n = node('Website Form Match');
    assert.strictEqual(n.onError, 'continueErrorOutput');
    const vals = evalReplacement(n, { resolved_term_id: null },
      { 'Website Prepare Scan': { instructor_id: ID.juan, subject_id: ID.it101, term_id: ID.term2 },
        'Website Reconcile OCR OMR': { ocr_instructor: 'Juan', ocr_subject: undefined } });
    assert.deepStrictEqual(vals, [ID.juan, ID.it101, 'Juan', '', ID.term2]);
    const fm = await formMatch('', '');
    assert.strictEqual(fm.form_match_version, 1);
    assert.strictEqual(fm.batch_instructor_name, 'Juan Dela Cruz');
    assert.strictEqual(fm.batch_subject_code, 'IT 101');
    assert.strictEqual(fm.term_id, ID.term);
    assert.strictEqual(typeof fm.batch_instructor_score, 'number', 'float8, not a numeric string');
    assert.ok(fm.instructor_candidates.some((c) => c.is_batch), 'batch instructor always listed');
    assert.ok(fm.subject_candidates.some((c) => c.is_batch), 'batch subject always listed');
    assert.ok(!fm.instructor_candidates.some((c) => [ID.rosa, ID.liza, ID.staff].includes(c.id)), 'pending / SAO accounts are no candidates');
    assert.ok(fm.instructor_candidates.some((c) => c.id === ID.placeholder && c.is_placeholder === true));
  });
  test('form match: same instructor with title / initials / misspelling / surname only / word order -> no flag', async () => {
    for (const name of ['Dr. Juan A. Dela Cruz, MIT', 'J. Dela Cruz', 'Juan Dela Crus', 'Dela Cruz', 'Cruz, Juan Dela',
      'JUAN DELA CRUZ', 'Sir Juan Delacruz', 'Prof. Juan dela Cruz Jr.', 'Mr. Juan Déla Cruz']) {
      const { d } = await decideForm(name, 'IT 101');
      assert.strictEqual(d.partial_data.instructor_match, 'same', name);
      assert.deepStrictEqual(d.review_reasons, [], name);
      assert.strictEqual(d.decision, 'clean');
    }
    const fm = await formMatch('Dr. Juan A. Dela Cruz, MIT', 'IT 101');
    assert.strictEqual(fm.ocr_instructor_norm, 'juan a dela cruz', 'titles and degrees stripped');
    assert.strictEqual(fm.batch_instructor_score, 0.95);
    assert.strictEqual((await formMatch("Ma'am Maria Santos", '')).ocr_instructor_norm, 'maria santos');
    assert.strictEqual((await formMatch('Ma’am Peña-Ñuñez, PhD', '')).ocr_instructor_norm, 'pena nunez');
    assert.strictEqual((await formMatch('Ma. Theresa', '')).ocr_instructor_norm, 'ma theresa', '"ma" (Maria) is kept');
  });
  test('form match: another instructor found -> instructor_mismatch_found with that instructor suggested', async () => {
    for (const [name, want] of [['Prof. Pedro Reyes', ID.pedro], ['Mr. Pédro Réyes', ID.pedro], ["Ma'am Maria Santos", ID.maria],
      ['Engr. Juan Carlos Santos, MIT', ID.juanCarlos], ['Ana Dela Cruz', ID.ana], ['Nora Villanueva', ID.placeholder],
      ['Reyes, Pedro', ID.pedro]]) {
      const { d } = await decideForm(name, 'IT 101');
      assert.deepStrictEqual(d.review_reasons, ['instructor_mismatch_found'], name);
      assert.strictEqual(d.partial_data.suggested_instructor_id, want, name);
      assert.strictEqual(d.partial_data.instructor, d.partial_data.suggested_instructor_name);
      assert.strictEqual(d.partial_data.instructor_candidates[0].id, want);
      assert.strictEqual(d.identity_check.instructor, 'mismatch');
      assert.strictEqual(d.identity_check.subject, 'match');
    }
    // initial + surname of another instructor with the same surname as someone else
    const { d } = await decideForm('A. Dela Cruz', 'IT 101', { instructor_id: ID.pedro });
    assert.strictEqual(d.partial_data.suggested_instructor_id, ID.ana, 'A. -> Ana, not Juan');
  });
  test('form match: shared surname -> instructor_mismatch_ambiguous; unknown / not creditable -> not_found, not flagged', async () => {
    // batch Pedro Reyes, form "Dela Cruz": Juan and Ana Dela Cruz both 0.70
    let { d, fm } = await decideForm('Dela Cruz', 'IT 101', { instructor_id: ID.pedro });
    assert.deepStrictEqual(d.review_reasons, ['instructor_mismatch_ambiguous']);
    assert.deepStrictEqual(fm.instructor_candidates.slice(0, 2).map((c) => [c.name, c.score]).sort(),
      [['Ana Dela Cruz', 0.7], ['Juan Dela Cruz', 0.7]]);
    assert.strictEqual(d.partial_data.suggested_instructor_id, ID.pedro, 'ambiguous -> batch pre-selected');
    assert.deepStrictEqual(d.partial_data.instructor_candidates.map((c) => c.id).sort(), [ID.juan, ID.ana].sort());
    ({ d } = await decideForm('Santos', 'IT 101'));
    assert.deepStrictEqual(d.review_reasons, ['instructor_mismatch_ambiguous'], 'Maria Santos vs Juan Carlos Santos');
    for (const name of ['Jose Rizal', 'AY/Semester: L U', 'Rosa Lim', 'Liza Gomez', 'Sam Staff']) {
      ({ d } = await decideForm(name, 'IT 101'));
      assert.deepStrictEqual(d.review_reasons, [], name);
      assert.strictEqual(d.partial_data.instructor_match, 'not_found', name);
      assert.strictEqual(d.partial_data.suggested_instructor_id, ID.juan);
      assert.strictEqual(d.partial_data.instructor, 'Juan Dela Cruz');
    }
  });
  test('form match: blank / placeholder / too short -> unreadable, instructor_missing + subject_missing', async () => {
    for (const [i, sj] of [['', ''], ['N/A', 'N/A'], ['J.', 'X'], ['Instructor', 'Subject'], ['Dr.', ' - '], ['unknown', 'none']]) {
      const { d } = await decideForm(i, sj);
      assert.deepStrictEqual(d.review_reasons, ['instructor_missing', 'subject_missing'], `${i} / ${sj}`);
      assert.strictEqual(d.partial_data.instructor_match, 'unreadable', i);
      assert.strictEqual(d.partial_data.subject_match, 'unreadable', sj);
      assert.strictEqual(d.identity_check, null);
    }
  });
  test('form match: subject code hit / name match / not found / ambiguous / link bonus', async () => {
    const subj = async (s, batch = {}, instructor = 'Juan Dela Cruz') => (await decideForm(instructor, s, batch)).d.partial_data;
    for (const s of ['IT 101', 'IT101', 'it-101', 'Introduction to Computing', 'intro to computing', 'IT 101 (Intro)', 'Intro (IT 101)']) {
      assert.strictEqual((await subj(s)).subject_match, 'same', s);
    }
    for (const [s, want] of [['IT-102', ID.it102], ['IT 102 Introduction to Computing', ID.it102], ['Computer Programming 1', ID.it102],
      ['DDMGT 3', ID.ddmgt3], ['GE104', ID.ge104], ['Mathematics in the Modern World', ID.ge104]]) {
      const pd = await subj(s);
      assert.strictEqual(pd.subject_match, 'found', s);
      assert.strictEqual(pd.suggested_subject_id, want, s);
      assert.strictEqual(pd.subject, pd.suggested_subject_code, 'partial_data.subject = the suggested subject_code');
    }
    for (const s of ['XYZ 999', 'Mà essor', 'Database']) assert.strictEqual((await subj(s)).subject_match, 'not_found', s);
    // ambiguous: two subjects with about the same name (temporary fixture)
    await db.none(`INSERT INTO public.subjects (id, subject_code, subject_name) VALUES ('20000000-0000-4000-8000-000000000005', 'IT 103', 'Computer Programming 2')`);
    const amb = await subj('Computer Programming');
    assert.strictEqual(amb.subject_match, 'ambiguous');
    assert.strictEqual(amb.suggested_subject_id, ID.it101);
    assert.strictEqual((await subj('IT 103')).suggested_subject_id, '20000000-0000-4000-8000-000000000005', 'a code is never ambiguous');
    await db.none(`DELETE FROM public.subjects WHERE subject_code = 'IT 103'`);
    // link bonus: +0.05 on IT 102 when it is linked to the TARGET instructor this term
    await db.none(`INSERT INTO public.instructor_subjects (subject_id, instructor_id, term_id) VALUES ($1, $2, $3)`, [ID.it102, ID.pedro, ID.term]);
    const fm = await formMatch('Pedro Reyes', 'Computer Prog');
    const it102 = fm.subject_candidates.find((c) => c.id === ID.it102);
    assert.deepStrictEqual(it102.linked_instructor_ids, [ID.pedro]);
    assert.ok(it102.name_score > 0.55 && it102.name_score < 0.6, String(it102.name_score));
    const withPedro = await subj('Computer Prog', {}, 'Pedro Reyes');   // target instructor = Pedro (found)
    const withJuan = await subj('Computer Prog');                        // batch Juan is not linked
    for (const pd of [withPedro, withJuan]) {
      assert.strictEqual(pd.subject_match, 'found', '"Prog" abbreviates "Programming"');
      assert.strictEqual(pd.suggested_subject_id, ID.it102);
    }
    const score = (pd) => pd.subject_candidates.find((c) => c.id === ID.it102).score;
    assert.ok(Math.abs(score(withPedro) - score(withJuan) - 0.05) < 0.011, `${score(withPedro)} vs ${score(withJuan)}`);
    assert.strictEqual((await subj('Programming')).subject_match, 'not_found', 'part of a title is not enough');
    await db.none('DELETE FROM public.instructor_subjects');
  });
  // the real batch from the live site (2026-09-28): 8 pages, all flagged before
  test('form match: real batch Rodz Harvey D Licayan / PC121 -- every reading resolves to the batch, a real other one is flagged', async () => {
    const X = { rodz: '10000000-0000-4000-8000-0000000000b1', ronald: '10000000-0000-4000-8000-0000000000b2',
      evangeline: '10000000-0000-4000-8000-0000000000b3', banjiee: '10000000-0000-4000-8000-0000000000b4',
      pc121: '20000000-0000-4000-8000-0000000000b1', asp: '20000000-0000-4000-8000-0000000000b2' };
    const people = [X.rodz, X.ronald, X.evangeline, X.banjiee];
    await db.none(`INSERT INTO public.user_info (id, first_name, last_name, account_status) VALUES
      ($1, 'Rodz Harvey d', 'Licayan', 'approved'), ($2, 'Ronald Kinggg', 'licayan', 'approved'),
      ($3, 'Evangeline', 'Caruana', 'approved'), ($4, 'Banjiee', 'Caruana', 'approved')`, people);
    await db.none(`INSERT INTO public.department_table (user_id, "Department_name_ID", roles) VALUES ($1, 1, 4), ($2, 1, 3), ($3, 1, 5), ($4, 1, 3)`,
      people);
    await db.none(`INSERT INTO public.subjects (id, subject_code, subject_name) VALUES ($1, 'PC121', 'Discreet Math'), ($2, 'ASP 101', 'asp.net mvc')`,
      [X.pc121, X.asp]);
    try {
      const batch = { instructor_id: X.rodz, subject_id: X.pc121 };
      const readI = ['Pich Harvey D. Lincayan', 'Kodi Harvey D. Licayan', 'Feda Harvey D. Ulayan', 'Kathe Harvey D. Hicayan',
        'Kobe Harvey D. Vicoyan', 'Proh Harvey D. Llegan', 'Harry D. Viiayan', 'Edg Harvey D. Vicojan'];
      const readS = ['Astrc Math', 'PO121', 'PCFP', 'IT1021', 'PC127', 'IT', 'ASP.NET', 'PC121 Discreet Math'];
      for (let k = 0; k < readI.length; k++) {
        const { d } = await decideForm(readI[k], readS[k], batch);
        assert.deepStrictEqual(d.review_reasons, [], `${readI[k]} / ${readS[k]}`);
        assert.strictEqual(d.decision, 'clean');
        assert.strictEqual(d.partial_data.instructor_match, 'same', readI[k]);
        assert.strictEqual(d.instructor_id, X.rodz);
        assert.strictEqual(d.subject_id, X.pc121);
      }
      for (const [name, want] of [['Ronald Licayan', X.ronald], ['Evangeline Caruana', X.evangeline], ['Pedro Reyes', ID.pedro]]) {
        const { d } = await decideForm(name, 'PC121 Discreet Math', batch);
        assert.deepStrictEqual(d.review_reasons, ['instructor_mismatch_found'], name);
        assert.strictEqual(d.partial_data.suggested_instructor_id, want, name);
      }
      assert.deepStrictEqual((await decideForm('Caruana', 'PC121', batch)).d.review_reasons, ['instructor_mismatch_ambiguous']);
      for (const [s, want] of [['ASP 101', X.asp], ['asp.net mvc', X.asp], ['IT 102', ID.it102]]) {
        const { d } = await decideForm('Kodi Harvey D. Licayan', s, batch);
        assert.deepStrictEqual(d.review_reasons, ['subject_mismatch_found'], s);
        assert.strictEqual(d.partial_data.suggested_subject_id, want, s);
      }
      // Gemini's subject pick from the known list (reconciler output known_subject); the website
      // has no instructor pick, so a stale known_instructor is ignored by Decide
      const { d } = await decideForm('Proh Harvey D. Llegan', 'Astrc Math', batch,
        { known_instructor: 'Rodz Harvey d Licayan', known_subject: 'PC121' });
      const { d: ref } = await decideForm('Proh Harvey D. Llegan', 'Astrc Math', batch, { known_subject: 'PC121' });
      assert.strictEqual(d.partial_data.instructor_match, ref.partial_data.instructor_match);
      assert.strictEqual(d.partial_data.instructor_match_confidence, ref.partial_data.instructor_match_confidence);
      assert.deepStrictEqual(d.partial_data.instructor_candidates, ref.partial_data.instructor_candidates);
      assert.ok(!('known_instructor' in d.partial_data));
      assert.strictEqual(d.partial_data.subject_match_confidence, 0.95);
      // the known lists for the prompt. Phone: creditable instructors + placeholders, sorted, plus
      // subjects. Website: subjects only -- no instructor names are looked up or sent to Gemini.
      const [kw] = await runNode('Website Known Names', {}, { 'Website Prepare Scan': batch });
      const [kp] = await runNode('Phone Known Names', {}, {});
      assert.deepStrictEqual(Object.keys(kw).sort(), ['known_names_version', 'subject_list', 'subjects']);
      assert.deepStrictEqual(kw.subjects, kp.subjects, 'same subject list on both branches');
      assert.strictEqual(kw.subject_list, kp.subject_list);
      assert.deepStrictEqual(kp.instructor_names, ['Ana Dela Cruz', 'Banjiee Caruana', 'Carlo Mendoza', 'Evangeline Caruana', 'Juan Carlos Santos',
        'Juan Dela Cruz', 'Maria Santos', 'Nora Villanueva', 'Pedro Reyes', 'Rodz Harvey d Licayan', 'Ronald Kinggg licayan']);
      assert.ok(kp.instructor_list.startsWith('Ana Dela Cruz; Banjiee Caruana; '));
      assert.ok(!/Rosa|Liza|Sam Staff/.test(kp.instructor_list), 'pending / SAO accounts are not listed');
      assert.ok(!kp.instructor_names.some((n) => JSON.stringify(kw).includes(n)), 'no instructor name in the website lookup');
      assert.deepStrictEqual(kw.subjects.map((x) => x.code), ['ASP 101', 'DDMGT-3', 'GE 104', 'IT 101', 'IT 102', 'PC121']);
      assert.ok(kw.subject_list.includes('PC121 = Discreet Math'));
    } finally {
      await db.none('DELETE FROM public.department_table WHERE user_id IN ($1:csv)', [people]);
      await db.none('DELETE FROM public.user_info WHERE id IN ($1:csv)', [people]);
      await db.none('DELETE FROM public.subjects WHERE id IN ($1:csv)', [[X.pc121, X.asp]]);
    }
  });
  test('form match: SQL-ish / multi-line OCR text is just data', async () => {
    const fm = await formMatch("Robert'); DROP TABLE public.subjects; --\n$1 $2", "IT 101'; DELETE FROM public.subjects; --\n$5");
    assert.strictEqual(fm.form_match_version, 1);
    const c = await db.one('SELECT count(*)::int AS n FROM public.subjects');
    assert.strictEqual(c.n, 4);
  });
  test('form match: duplicate_student_id against the batch is dropped when the page is suggested for someone else', async () => {
    assert.strictEqual((await websiteToRaw(decideItem({ task_id: 'web:dup-1', student_id: 'S-DUP' }))).saved, true);
    const other = await decideForm('Pedro Reyes', 'IT 101', {}, { student_id: 'S-DUP' });
    assert.strictEqual(other.idRow.duplicate_student_exists, true, 'Identity Check still reports it (batch-only)');
    assert.deepStrictEqual(other.d.review_reasons, ['instructor_mismatch_found']);
    const same = await decideForm('Juan Dela Cruz', 'IT 101', {}, { student_id: 'S-DUP' });
    assert.deepStrictEqual(same.d.review_reasons, ['duplicate_student_id']);
    await db.none(`DELETE FROM public.sast_all_raw_data_survey WHERE task_id = 'web:dup-1'`);
  });
  test('form match: query error -> form_match_failed (held, batch values)', async () => {
    const ctx = { batch_id: ID.batch, instructor_id: ID.juan, subject_id: ID.it101, term_id: ID.term, task_id: 'web:fmerr' };
    const rec = { ocr_instructor: 'Pedro Reyes', ocr_subject: 'IT 102', review_reasons: [], ratings: {} };
    let err = null;
    try {
      await db.one(pgp.as.format(node('Website Form Match').parameters.query.replace('public.subjects s', 'public.no_such_table s'),
        [ID.juan, ID.it101, 'Pedro Reyes', 'IT 102', null]));
    } catch (e) { err = { error: { message: e.message } }; }
    assert.ok(err, 'query failed');
    const [idRow] = await runNode('Website Identity Check', rec, { 'Website Prepare Scan': ctx });
    const [d] = await runCode('Website Decide', [err], { 'Website Prepare Scan': ctx, 'Website Reconcile OCR OMR': rec,
      'Website Identity Check': idRow });
    assert.deepStrictEqual(d.review_reasons, ['form_match_failed']);
    assert.strictEqual(d.partial_data.instructor_match, 'unchecked');
    assert.strictEqual(d.partial_data.suggested_instructor_id, ID.juan);
    assert.strictEqual(d.identity_check, null);
  });

  // ---------------------------------------------------------------- corrections: id first, website_review term
  const correctionBody = (over = {}) => ({ body: { failed_scan_id: '60000000-0000-4000-8000-0000000000aa', task_id: 'web:fix-1',
    user_id: ID.staff, term_id: ID.term2, instructor: 'Pedro Reyes', instructor_id: null, subject: 'IT 102', subject_id: null,
    remarks: 'Good', student_id: 'S-FIX', m1: 5, p1: 4, manually_corrected: true, validation_status: 'corrected',
    correction_source: 'manual_text', ...over } });
  test('correction lookups: get instructor id7 prefers a creditable body.instructor_id, else the old name lookup', async () => {
    const look = async (over) => (await runNode('get instructor id7', { scan_image_id: null },
      { 'sast-manual-correction1': correctionBody(over) }))[0];
    assert.strictEqual((await look({ instructor: 'Juan Dela Cruz', instructor_id: ID.pedro })).instructor_id, ID.pedro, 'id wins');
    assert.strictEqual((await look({ instructor: 'Juan Dela Cruz', instructor_id: ID.pedro.toUpperCase() })).instructor_id, ID.pedro);
    assert.strictEqual((await look({ instructor: '', instructor_id: ID.placeholder })).instructor_id, ID.placeholder);
    const byId = await look({ instructor: 'whatever', instructor_id: ID.ana });
    assert.deepStrictEqual([byId.first_name, byId.last_name], ['Ana', 'Dela Cruz'], 'DB name for Map target');
    // not creditable / unknown / not a uuid -> the name decides, exactly as before
    assert.strictEqual((await look({ instructor: 'Juan Dela Cruz', instructor_id: ID.liza })).instructor_id, ID.juan, 'SAO staff id ignored');
    assert.strictEqual((await look({ instructor: 'Juan Dela Cruz', instructor_id: ID.rosa })).instructor_id, ID.juan, 'pending id ignored');
    assert.strictEqual((await look({ instructor: 'Juan Dela Cruz', instructor_id: ID.missing })).instructor_id, ID.juan);
    assert.strictEqual((await look({ instructor: 'Juan Dela Cruz', instructor_id: 'not-a-uuid' })).instructor_id, ID.juan);
    assert.strictEqual((await look({ instructor: 'Juan Dela Cruz', instructor_id: [null] })).instructor_id, ID.juan, 'Flutter [null]');
    assert.strictEqual((await look({ instructor: 'Rosa Lim', instructor_id: ID.rosa })).instructor_id, null);
    assert.strictEqual((await look({ instructor: '', instructor_id: null })).instructor_id, null, 'exactly one NULL row');
    // a comma in the name is no longer split into two parameters
    assert.strictEqual((await look({ instructor: 'Dela Cruz, Juan' })).instructor_id, ID.juan);
  });
  test('correction lookups: get subject id6 prefers body.subject_id, else the old code/name lookup', async () => {
    await db.none('DELETE FROM public.instructor_subjects');
    const look = async (over, instructorId = ID.pedro) => (await runNode('get subject id6', { instructor_id: instructorId },
      { 'sast-manual-correction1': correctionBody(over) }))[0];
    assert.strictEqual((await look({ subject: 'IT 101', subject_id: ID.it102 })).subject_id, ID.it102, 'id wins');
    // Flutter's autocomplete display text ("CODE — name") only resolves through the id
    assert.strictEqual((await look({ subject: 'IT 102 — Computer Programming 1', subject_id: ID.it102 })).subject_id, ID.it102);
    assert.strictEqual((await look({ subject: 'IT 102 — Computer Programming 1', subject_id: null })).subject_id, null, 'unchanged without an id');
    assert.strictEqual((await look({ subject: 'IT 101', subject_id: ID.missing })).subject_id, ID.it101, 'unknown id -> code lookup');
    assert.strictEqual((await look({ subject: 'Introduction to Computing', subject_id: '' })).subject_id, ID.it101, 'name lookup');
    const noInstr = await look({ subject: 'GE 104', subject_id: ID.ge104 }, null);
    assert.deepStrictEqual([noInstr.subject_id, noInstr.instructor_id, noInstr.auto_assigned], [ID.ge104, null, false]);
    const links = await db.any('SELECT subject_id::text AS s, instructor_id::text AS i, term_id::text AS t FROM public.instructor_subjects ORDER BY 1');
    assert.ok(links.every((l) => l.i === ID.pedro && l.t === ID.term), 'links only the resolved instructor, current term (unchanged)');
    await db.none('DELETE FROM public.instructor_subjects');
  });
  test('correction end to end (website_review): mismatch page -> approve with ids -> raw row for the chosen instructor, batch term', async () => {
    // 1. the page: the form names Pedro Reyes / IT 102 in a Juan / IT 101 batch (batch term = term2, not the current term)
    const gem = gemResp({ instructor: 'Prof. Pedro Reyes', subject: 'IT 102', student_id: 'S-FIX' });
    const ctx = { batch_id: ID.batch, instructor_id: ID.juan, subject_id: ID.it101, term_id: ID.term2, station_id: ID.batch,
      staff_id: ID.staff, filename: 'p.jpg', task_id: 'web:fix-1', image_base64: 'QUJD' };
    const [rec] = await runCode('Website Reconcile OCR OMR', [gem], { 'Website Python OMR': pyResp(), 'Website AI Validation': gem });
    const [idRow] = await runNode('Website Identity Check', rec, { 'Website Prepare Scan': ctx });
    const [fm] = await runNode('Website Form Match', idRow, { 'Website Prepare Scan': ctx, 'Website Reconcile OCR OMR': rec });
    const [d] = await runCode('Website Decide', [fm], { 'Website Prepare Scan': ctx, 'Website Reconcile OCR OMR': rec, 'Website Identity Check': idRow });
    assert.deepStrictEqual(d.review_reasons, ['instructor_mismatch_found', 'subject_mismatch_found']);
    const [wrec] = await runCode('Website Flagged Record', [d], { 'Website Prepare Scan': ctx });
    const [record] = await runCode('build flagged record1', [{}], { 'Website Flagged Record': wrec, 'Save scan error image': [{ id: null }] });
    const row = await insertFlagged({ ...record, id: '60000000-0000-4000-8000-0000000000aa' });
    assert.strictEqual(row.term_id, ID.term2);
    assert.strictEqual(row.partial_data.instructor, 'Pedro Reyes');
    assert.strictEqual(row.partial_data.subject, 'IT 102');
    assert.strictEqual(row.partial_data.suggested_instructor_id, ID.pedro);
    assert.strictEqual(row.partial_data.suggested_subject_id, ID.it102);
    assert.strictEqual(row.partial_data.batch_instructor_name, 'Juan Dela Cruz');
    const raw0 = await db.one(`SELECT count(*)::int AS n FROM public.sast_all_raw_data_survey WHERE task_id = 'web:fix-1'`);
    assert.strictEqual(raw0.n, 0, 'nothing credited before approval');
    // 2. approve (backend -> sast-manual-correction): ids + the queue row's term
    const hook = correctionBody({ correction_source: 'website_review', term_id: row.term_id, instructor: 'Pedro Reyes',
      instructor_id: ID.pedro, subject: 'IT 102', subject_id: ID.it102, reviewed_by: ID.staff });
    const nodes = { 'sast-manual-correction1': hook };
    [nodes['get existing scan image id']] = await runNode('get existing scan image id', hook);
    [nodes['get instructor id7']] = await runNode('get instructor id7', nodes['get existing scan image id'], nodes);
    [nodes['get subject id6']] = await runNode('get subject id6', nodes['get instructor id7'], nodes);
    assert.strictEqual(nodes['get instructor id7'].instructor_id, ID.pedro);
    assert.strictEqual(nodes['get subject id6'].subject_id, ID.it102);
    const [built] = await runCode('build the record1', [{}], nodes);
    // 'GET TERM BY ID6' (system current term) merged in by position
    const [mapped] = await runCode('Map target', [{ ...built, current_term_id: ID.term }], nodes);
    assert.strictEqual(mapped.current_term_id, ID.term2, 'website_review -> the page term, not the current term');
    const [passed] = await runCode('After Correction Response', [{ success: true }], { 'Map target': mapped });
    const rows = await runCode('Code in JavaScript', [passed], { 'sast-manual-correction1': hook });
    const stored = await sharedRawUpsert(rows[0]);
    assert.strictEqual(stored["instructor_ID"], ID.pedro);
    assert.strictEqual(String(stored.subject_id), ID.it102);
    assert.strictEqual(String(stored.term_id), ID.term2);
    assert.strictEqual(stored.task_id, 'web:fix-1');
    assert.strictEqual(stored.validation_status, 'corrected');
    assert.strictEqual(stored.instructor, 'Pedro Reyes');
    // 3. the same approval from Flutter (manual_text) keeps the current term, as before
    const fl = correctionBody({ instructor: 'Pedro Reyes', subject: 'IT 102' });
    const n2 = { 'sast-manual-correction1': fl };
    [n2['get existing scan image id']] = await runNode('get existing scan image id', fl);
    [n2['get instructor id7']] = await runNode('get instructor id7', n2['get existing scan image id'], n2);
    [n2['get subject id6']] = await runNode('get subject id6', n2['get instructor id7'], n2);
    const [b2] = await runCode('build the record1', [{}], n2);
    const [m2] = await runCode('Map target', [{ ...b2, current_term_id: ID.term }], n2);
    assert.deepStrictEqual([m2.instructor_ID, m2.true_subject_id, m2.current_term_id], [ID.pedro, ID.it102, ID.term]);
    await db.none(`DELETE FROM public.sast_all_raw_data_survey WHERE task_id = 'web:fix-1'`);
    await db.none(`DELETE FROM public.failed_scan_queue WHERE task_id = 'web:fix-1'`);
    await db.none('DELETE FROM public.instructor_subjects');
  });
  return t;
}

(async () => {
  const admin = pgp(PG_URL);
  await admin.none('DROP DATABASE IF EXISTS $1:name', [TEST_DB]);
  await admin.none('CREATE DATABASE $1:name', [TEST_DB]);
  const testUrl = new URL(PG_URL);
  testUrl.pathname = `/${TEST_DB}`;
  db = pgp(testUrl.toString());
  let failed = 0;
  let total = 0;
  try {
    for (const rawIdType of ['uuid', 'text']) {
      await db.multi(schemaSql(rawIdType));
      // Supabase API roles that 013 revokes from / grants to (cluster-wide; created once)
      await db.none(`DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN CREATE ROLE anon NOLOGIN; END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN CREATE ROLE authenticated NOLOGIN; END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN CREATE ROLE service_role NOLOGIN; END IF;
      END $$`);
      await db.multi(MIG013);
      for (const tc of await suite(rawIdType)) {
        total++;
        try {
          await tc.fn();
          console.log(`PASS  ${tc.name}`);
        } catch (e) {
          failed++;
          console.log(`FAIL  ${tc.name}\n      ${String(e && e.message).split('\n').join('\n      ')}`);
        }
      }
    }
  } finally {
    await db.$pool.end();
    await admin.none('DROP DATABASE IF EXISTS $1:name', [TEST_DB]);
    pgp.end();
  }
  console.log(`\n${total - failed}/${total} tests passed`);
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });
