#!/usr/bin/env node
// Behavioural tests for the website-branch Code nodes, executed straight from
// n8n/perfect-2.1.json with a small mock of n8n's $ / $input / helpers.
// Usage: node test_website_code.js [path/to/perfect-2.1.json] [--print-profanity-patterns]
//   --print-profanity-patterns  prints the SQL pattern lines of migrations/004_mask_existing_profanity.sql
//                               (generated from the filter in the workflow) instead of running the tests
'use strict';
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const ARGS = process.argv.slice(2);
const wfPath = ARGS.find((a) => !a.startsWith('--')) || path.join(__dirname, '..', 'perfect-2.1.json');
const wf = JSON.parse(fs.readFileSync(wfPath, 'utf8'));
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;

function codeOf(name) {
  const n = wf.nodes.find((x) => x.name === name);
  if (!n) throw new Error(`node not found: ${name}`);
  return n.parameters.jsCode;
}

// nodes: { 'Node Name': json | undefined }  (undefined = executed, main output empty)
//        { __items: [json, ...] }                 (multi-item output)
//        { __branches: [[json...], [json...]] }   (node with an error output: output 0 / output 1)
async function run(name, input, nodes = {}, inputs = null) {
  const $ = (ref) => {
    if (!Object.prototype.hasOwnProperty.call(nodes, ref)) {
      throw new Error(`ExpressionError: Referenced node is unexecuted: ${ref}`);
    }
    const v = nodes[ref];
    const branches = v && Array.isArray(v.__branches) ? v.__branches : null;
    const many = v && Array.isArray(v.__items) ? v.__items : null;   // multi-item output
    const list = (b) => (branches ? (branches[b || 0] || []) : many || (v === undefined ? [] : [v]));
    return {
      first: (b) => (list(b)[0] !== undefined ? { json: list(b)[0] } : undefined),
      all: (b) => list(b).map((j) => ({ json: j })),
      item: list(0)[0] !== undefined ? { json: list(0)[0] } : undefined,
      isExecuted: true,
    };
  };
  const list = inputs || [input];
  const $input = { first: () => (list[0] === undefined ? undefined : { json: list[0] }), all: () => list.map((json) => ({ json })), item: { json: list[0] } };
  const helpers = {
    prepareBinaryData: async (buf, fileName, mimeType) => ({ fileName, mimeType, fileSize: buf.length }),
  };
  const quiet = { log() {}, warn() {}, error() {} };
  const fn = new AsyncFunction('$', '$input', 'Buffer', 'console', codeOf(name));
  return fn.call({ helpers }, $, $input, Buffer, quiet);
}

const STATUSES = ['success', 'flagged', 'rejected', 'duplicate'];
// Removed identity reasons (earlier designs): never emitted again.
const IDENTITY_REASONS = ['batch_instructor_mismatch', 'instructor_not_verified', 'batch_subject_mismatch',
  'subject_not_verified', 'identity_check_failed'];
// The form's instructor/subject vs the database ('Website Form Match' + 'Website Decide').
const MISMATCH_REASONS = ['instructor_mismatch_found', 'instructor_mismatch_not_found', 'instructor_mismatch_ambiguous',
  'subject_mismatch_found', 'subject_mismatch_not_found', 'subject_mismatch_ambiguous'];
const MISMATCH_RE = /^(instructor|subject)_mismatch_/;
// Blank/unreadable header fields ('Website Decide'): held for review, never saved silently.
const MISSING_REASONS = ['instructor_missing', 'subject_missing', 'student_id_missing'];
function assertContract(out, expectStatus) {
  assert.ok(Array.isArray(out) && out.length === 1, 'exactly one item');
  const r = out[0].json;
  assert.deepStrictEqual(Object.keys(r).sort(),
    ['flagged', 'identity_check', 'message', 'review_reasons', 'status', 'task_id'].sort());
  assert.ok(STATUSES.includes(r.status), `status ${r.status}`);
  if (expectStatus) assert.strictEqual(r.status, expectStatus);
  assert.strictEqual(typeof r.flagged, 'boolean');
  assert.strictEqual(r.flagged, r.status === 'flagged');
  assert.strictEqual(typeof r.task_id, 'string');
  assert.ok(Array.isArray(r.review_reasons) && r.review_reasons.every((x) => typeof x === 'string'));
  assert.strictEqual(typeof r.message, 'string');
  assertIdentity(r.identity_check, r.review_reasons);
  assert.ok(!r.review_reasons.some((x) => IDENTITY_REASONS.includes(x)), `no removed identity reason: ${r.review_reasons}`);
  assert.ok(!/unverified|not verified/i.test(r.message), `no "unverified" noise: ${r.message}`);
  return r;
}
// identity_check is an object ONLY when a *_mismatch_* reason is present, else null.
function assertIdentity(ic, reasons) {
  if (!reasons.some((x) => MISMATCH_RE.test(x))) {
    assert.strictEqual(ic, null, `identity_check must be null without a mismatch reason (${reasons})`);
    return;
  }
  assert.ok(ic && typeof ic === 'object', 'identity_check object with a mismatch reason');
  assert.deepStrictEqual(Object.keys(ic).sort(), ['instructor', 'ocr_instructor', 'ocr_subject', 'subject']);
  for (const k of ['instructor', 'subject']) assert.ok(['match', 'mismatch', 'unreadable'].includes(ic[k]), `${k}: ${ic[k]}`);
  assert.strictEqual(ic.instructor === 'mismatch', reasons.some((x) => x.startsWith('instructor_mismatch_')));
  assert.strictEqual(ic.subject === 'mismatch', reasons.some((x) => x.startsWith('subject_mismatch_')));
}

// ---------------------------------------------------------------- fixtures
const BATCH = 'aaaaaaaa-1111-4111-8111-111111111111';
const INSTR = 'bbbbbbbb-2222-4222-8222-222222222222';
const SUBJ = 'cccccccc-3333-4333-8333-333333333333';
const TERM = 'dddddddd-4444-4444-8444-444444444444';
const STAFF = 'eeeeeeee-5555-4555-8555-555555555555';
const SHA = 'f'.repeat(64);
const TASK = `web:${BATCH}:${SHA.slice(0, 16)}`;
const IMAGE = Buffer.alloc(300, 7).toString('base64');

const body = {
  batch_id: BATCH, instructor_id: INSTR, subject_id: SUBJ, term_id: TERM, station_id: BATCH,
  staff_id: STAFF, filename: 'scan_001.jpg', file_sha256: SHA, task_id: TASK, image: IMAGE, mime_type: 'image/jpeg',
};
const letters = ['O', 'VS', 'S', 'F', 'US'];
// override[i] = letter, or '' for a row Python reads blank (detected: false)
function python({ table = true, override = {}, weak = {}, grid = 'structure-fit', ocr = {} } = {}) {
  const cell = (i) => (override[i] === ''
    ? { detected: false, rating_name: null }
    : { detected: true, rating_name: override[i] || letters[i % 5] });
  return {
    table_found: table,
    grid_source: grid,
    ratings: {
      management: Array.from({ length: 10 }, (_, i) => cell(i)),
      performance: Array.from({ length: 10 }, (_, i) => cell(i + 10)),
    },
    omr_debug: Array.from({ length: 20 }, (_, i) => ({ row_index: i, scores: weak[i] || { a: 0.9, b: 0.1 } })),
    ocr_fields: { instructor: '', subject: '', student_id: '', ...ocr },
  };
}
// Python OCR text that must NEVER be used (the real garbage from a scanner page).
const PY_OCR_GARBAGE = { instructor: 'AY/Semester: L U', subject: 'Mà essor', ay_semester: 'x1', remarks: 'py remark',
  date: '01/01/99', student_id: 'PY-999' };
function geminiResp(fields = {}) {
  const ratings = {};
  for (let i = 0; i < 10; i++) { ratings[`m${i + 1}`] = letters[i % 5]; ratings[`p${i + 1}`] = letters[(i + 10) % 5]; }
  const g = {
    instructor: 'Dr. Juan A. Dela Cruz, MIT', subject: 'IT 1O1', ay_semester: '2026 1st Sem',
    remarks: 'Very good teacher', date: '09/20/26', student_id: '2021 0001',
    document_check: { is_sast_form: true, reason: 'SAST form' },
    scan_quality: { poor_quality: false, issues: [], reason: 'Good quality' },
    ratings, ...fields,
  };
  return { candidates: [{ content: { parts: [{ text: JSON.stringify(g) }] } }] };
}
// One row of 'Website Identity Check' (batch integrity only).
function identity(over = {}) {
  return {
    batch_instructor_exists: true, batch_instructor_disabled: false, batch_instructor_is_placeholder: false,
    batch_instructor_first_name: 'Juan', batch_instructor_last_name: 'Dela Cruz',
    batch_instructor_name: 'Juan Dela Cruz',
    batch_subject_exists: true, batch_subject_code: 'IT 101', batch_subject_name: 'Introduction to Computing',
    resolved_term_id: TERM, duplicate_student_exists: false, duplicate_student_task_id: null,
    ...over,
  };
}

// Other instructors/subjects the form may name (ids as 'Website Form Match' returns them).
const PEDRO = 'bbbbbbbb-2222-4222-8222-000000000002';
const ANA = 'bbbbbbbb-2222-4222-8222-000000000005';
const MARIA = 'bbbbbbbb-2222-4222-8222-000000000003';
const JCARLOS = 'bbbbbbbb-2222-4222-8222-000000000004';
const NORA = 'bbbbbbbb-2222-4222-8222-00000000000a';
const IT102 = 'cccccccc-3333-4333-8333-000000000002';
const IT103 = 'cccccccc-3333-4333-8333-000000000005';
const GE104 = 'cccccccc-3333-4333-8333-000000000003';
const icand = (id, name, score, extra = {}) => ({ id, name, score, is_placeholder: false, is_batch: id === INSTR, ...extra });
const scand = (id, code, name, { hit = false, ns = 0, linked = [] } = {}) => ({ id, code, name, code_hit: hit,
  name_score: ns, score: hit ? 1 : ns, is_batch: id === SUBJ, linked_instructor_ids: linked });
const BATCH_I = (score) => icand(INSTR, 'Juan Dela Cruz', score);
const BATCH_S = (o = { hit: true, ns: 0.1 }) => scand(SUBJ, 'IT 101', 'Introduction to Computing', o);
// One row of 'Website Form Match' (scores only; Decide applies the thresholds).
function formMatch({ i = 'juan a dela cruz', ic = [BATCH_I(0.95), icand(ANA, 'Ana Dela Cruz', 0.85)],
  s = 'it 101', sc = [BATCH_S(), scand(IT102, 'IT 102', 'Computer Programming 1', { ns: 0.04 })], ...over } = {}) {
  return {
    form_match_version: 1,
    ocr_instructor_norm: i, ocr_instructor_letters: i.replace(/ /g, '').length,
    batch_instructor_found: true, batch_instructor_name: 'Juan Dela Cruz',
    batch_instructor_score: (ic.find((c) => c.is_batch) || { score: 0 }).score,
    instructor_candidates: ic,
    ocr_subject_norm: s, ocr_subject_alnum_len: s.replace(/[^a-z0-9]/g, '').length,
    batch_subject_found: true, batch_subject_code: 'IT 101', batch_subject_name: 'Introduction to Computing',
    subject_candidates: sc, term_id: TERM, ...over,
  };
}
const FORM_PD_KEYS = ['form_check_version', 'instructor_match', 'subject_match', 'suggested_instructor_id',
  'suggested_instructor_name', 'suggested_subject_id', 'suggested_subject_code', 'suggested_subject_name',
  'instructor_match_confidence', 'subject_match_confidence', 'instructor_candidates', 'subject_candidates',
  'batch_instructor_name', 'batch_subject_code', 'batch_subject_name'];

const tests = [];
const test = (name, fn) => tests.push({ name, fn });

// ---------------------------------------------------------------- Prepare
let CTX;
test('Prepare: valid payload builds context (paper_size LONG)', async () => {
  const out = await run('Website Prepare Scan', { body });
  CTX = out[0].json;
  assert.strictEqual(CTX.task_id, TASK);
  assert.strictEqual(CTX.paper_size, 'LONG');
  assert.strictEqual(CTX.image_base64, IMAGE);
  assert.strictEqual(CTX.staff_id, STAFF);
});
test('Prepare: paper_size from backend (SHORT/LONG), normalised, else LONG', async () => {
  const paper = async (v) => (await run('Website Prepare Scan', { body: { ...body, paper_size: v } }))[0].json.paper_size;
  assert.strictEqual(await paper('SHORT'), 'SHORT');
  assert.strictEqual(await paper('short '), 'SHORT');
  assert.strictEqual(await paper('LONG'), 'LONG');
  for (const v of [undefined, null, '', 'A4', 'legal', 13, {}]) assert.strictEqual(await paper(v), 'LONG');
});
test('Prepare: data: prefix stripped, task_id derived when missing', async () => {
  const out = await run('Website Prepare Scan', { body: { ...body, task_id: undefined, image: `data:image/jpeg;base64,${IMAGE}` } });
  assert.strictEqual(out[0].json.task_id, TASK);
  assert.strictEqual(out[0].json.image_base64, IMAGE);
});
test('Prepare: header_image passed through when valid, dropped when invalid/missing (backward compatible)', async () => {
  const withHeader = await run('Website Prepare Scan', { body: { ...body, header_image: `data:image/jpeg;base64,${IMAGE}` } });
  assert.strictEqual(withHeader[0].json.header_image_base64, IMAGE);
  const noHeader = await run('Website Prepare Scan', { body });
  assert.strictEqual(noHeader[0].json.header_image_base64, '');
  const badHeader = await run('Website Prepare Scan', { body: { ...body, header_image: 'not-base64!!' } });
  assert.strictEqual(badHeader[0].json.header_image_base64, '');
});
test('Prepare: invalid payload throws (-> error output)', async () => {
  await assert.rejects(run('Website Prepare Scan', { body: { ...body, instructor_id: 'nope' } }), /invalid_request: .*instructor_id/);
  await assert.rejects(run('Website Prepare Scan', { body: { ...body, image: '' } }), /image/);
});
test('Invalid Request Response: flagged, not saved, PERMANENT (not retryable)', async () => {
  const r = assertContract(await run('Website Invalid Request Response', { error: 'invalid_request: x' }, { 'SAST from Website': { body } }), 'flagged');
  assert.deepStrictEqual(r.review_reasons, ['invalid_request', 'not_saved_permanent']);
  assert.ok(!r.review_reasons.includes('internal_error_not_saved'));
});
test('Attach Image: binary field data', async () => {
  const out = await run('Website Attach Image', {}, { 'Website Prepare Scan': CTX });
  assert.strictEqual(out[0].binary.data.mimeType, 'image/jpeg');
  assert.strictEqual(out[0].binary.data.fileSize, 300);
});

// ---------------------------------------------------------------- responses before OCR
test('Duplicate Response: contract', async () => {
  const r = assertContract(await run('Website Duplicate Response', { in_raw: false, in_queue: true }, { 'Website Prepare Scan': CTX }), 'duplicate');
  assert.match(r.message, /review queue/);
});
test('Rejected Response: contract + Gemini reason', async () => {
  const resp = geminiResp({ document_check: { is_sast_form: false, reason: 'A selfie' } });
  const r = assertContract(await run('Website Rejected Response', resp, { 'Website Prepare Scan': CTX }), 'rejected');
  assert.match(r.message, /selfie/);
});

// ---------------------------------------------------------------- Reconcile
let REC;
test('Reconcile: engines agree -> no reasons, agreement 100%, text from Gemini', async () => {
  const out = await run('Website Reconcile OCR OMR', geminiResp(), { 'Website Python OMR': python(), 'Website AI Validation': geminiResp() });
  REC = out[0].json;
  assert.deepStrictEqual(REC.review_reasons, []);
  assert.strictEqual(REC._stage, 'reconciled');
  assert.strictEqual(REC.ocr_instructor, 'Dr. Juan A. Dela Cruz, MIT');
  assert.strictEqual(REC.ocr_subject, 'IT 1O1');
  assert.strictEqual(REC.student_id, '20210001');
  assert.strictEqual(REC.ratings.m1, 5);
  assert.strictEqual(REC.ratings.p5, 1);
  assert.strictEqual(REC.agreement_percent, 100);
  assert.strictEqual(REC.agreement_rows, 20);
  assert.strictEqual(REC.omr_agreement.agreement_percent, 100);
  assert.strictEqual(REC.omr_agreement.comparisons.length, 20);
  assert.strictEqual(REC.review_note, null, 'clean page: no review note');
  // name-matching keys are gone (the instructor/subject are not validated)
  for (const k of ['instructor_core', 'subject_name_key', 'subject_code_candidates']) assert.ok(!(k in REC), k);
});
test('Reconcile: blank instructor/subject is NOT a reason by itself', async () => {
  const g = geminiResp({ instructor: '', subject: 'N/A' });
  const out = await run('Website Reconcile OCR OMR', g, { 'Website Python OMR': python(), 'Website AI Validation': g });
  assert.deepStrictEqual(out[0].json.review_reasons, []);
  assert.strictEqual(out[0].json.ocr_subject, '');
});
test('Reconcile: Gemini call failure -> ai_validation_failed, Python ratings kept, no text', async () => {
  const out = await run('Website Reconcile OCR OMR', { error: 'timeout' },
    { 'Website Python OMR': python({ ocr: PY_OCR_GARBAGE }), 'Website AI Validation': undefined });
  const r = out[0].json;
  assert.ok(r.review_reasons.includes('ai_validation_failed'));
  assert.match(r.review_note, /timeout/);
  assert.strictEqual(r.ratings.m1, 5, 'ratings still come from Python');
  assert.strictEqual(r.agreement_percent, null, 'no second reader -> agreement unknown');
  for (const k of ['ocr_instructor', 'ocr_subject', 'ay_semester', 'remarks', 'date', 'student_id']) {
    assert.strictEqual(r[k], '', `${k} must be empty (no Python OCR fallback)`);
  }
});

// ---------------------------------------------------------------- OCR text only from Gemini
async function reconcile(py, g = geminiResp()) {
  return (await run('Website Reconcile OCR OMR', g, { 'Website Python OMR': py, 'Website AI Validation': g }))[0].json;
}
const QS = [...Array(10)].map((_, i) => `m${i + 1}`).concat([...Array(10)].map((_, i) => `p${i + 1}`));
// Gemini ratings that differ from Python's default letter (letters[i % 5]) on the first n rows:
// adjacent = exactly one level away (US -> F), far = two or more levels away
function adjacent(n) { return Object.fromEntries(QS.slice(0, n).map((q, i) => [q, letters[i % 5 === 4 ? 3 : (i % 5) + 1]])); }
function far(n) { return Object.fromEntries(QS.slice(0, n).map((q, i) => [q, letters[(i + 2) % 5]])); }
const WEAK_ROW = { a: 0.5, b: 0.49 };   // margin 0.01 < WEAK_MARGIN
const weakRows = (n, from = 0) => Object.fromEntries(Array.from({ length: n }, (_, i) => [from + i, WEAK_ROW]));
const blankRows = (n) => Object.fromEntries(Array.from({ length: n }, (_, i) => [i, '']));
function geminiWith(ratingsOver, fields = {}) {
  const r = JSON.parse(geminiResp().candidates[0].content.parts[0].text).ratings;
  return geminiResp({ ratings: { ...r, ...ratingsOver }, ...fields });
}
test('Gemini-only OCR (website): Gemini text blank + Python ocr_fields full -> every text field empty', async () => {
  const g = geminiResp({ instructor: '', subject: '', ay_semester: '', remarks: '', date: '', student_id: '' });
  const r = await reconcile(python({ ocr: PY_OCR_GARBAGE }), g);
  for (const k of ['ocr_instructor', 'ocr_subject', 'ay_semester', 'remarks', 'date', 'student_id']) {
    assert.strictEqual(r[k], '', `${k} took Python OCR text`);
  }
  assert.deepStrictEqual(r.review_reasons, [], 'blank text is not a website flag reason');
  const code = codeOf('Website Reconcile OCR OMR');
  assert.ok(!/py\.ocr_fields|pythonOcr/.test(code), 'reconciler must not read py.ocr_fields');
});
test('Gemini-only OCR (website): Gemini unparseable -> gemini_unavailable, text empty', async () => {
  const bad = { candidates: [{ content: { parts: [{ text: 'no json here' }] } }] };
  const r = await reconcile(python({ ocr: PY_OCR_GARBAGE }), bad);
  assert.ok(r.review_reasons.includes('gemini_unavailable'));
  assert.strictEqual(r.ocr_instructor, '');
  assert.strictEqual(r.student_id, '');
  assert.strictEqual(r.text_source, 'none');
});

// ---------------------------------------------------------------- agreement metric (AGREEMENT_MIN)
test('Agreement: AGREEMENT_MIN = 0.30 in both reconcilers; old count-based rules are gone', async () => {
  for (const name of ['Website Reconcile OCR OMR', 'reconcile ocr and omr1']) {
    const code = codeOf(name);
    assert.match(code, /^const AGREEMENT_MIN = 0\.30;/m, `${name}: one named constant`);
    assert.strictEqual(code.match(/^const AGREEMENT_MIN\b/gm).length, 1);
    const noComments = code.split('\n').map((l) => l.replace(/^\s*\/\/.*$/, '')).join('\n');
    for (const old of ['omr_disagreement_', 'severe_omr_disagreement_', 'low_confidence_omr_', 'FLAG_DIFF_COUNT',
      'SEVERE_CONFLICT_LIMIT', 'WEAK_CONFLICT_LIMIT', 'ocr_fields']) {
      assert.ok(!noComments.includes(old), `${name} still uses ${old}`);
    }
  }
});
test('Agreement: 6/20 (30%) passes, 5/20 (25%) -> omr_agreement_below_30 with only the counted rows in the note', async () => {
  // Python blank on 14 rows that Gemini reads -> 6 agree
  const r14 = await reconcile(python({ override: blankRows(14) }));
  assert.strictEqual(r14.agreement_rows, 6);
  assert.strictEqual(r14.agreement_percent, 30);
  assert.deepStrictEqual(r14.review_reasons, [], '30% is enough');
  const r15 = await reconcile(python({ override: blankRows(15) }));
  assert.strictEqual(r15.agreement_percent, 25);
  assert.deepStrictEqual(r15.review_reasons, ['omr_agreement_below_30']);
  assert.match(r15.review_note, /clearly disagree on 15 of 20 rows .*only 5 of 20 rows \(25%\) count as agreeing; at least 30% is required/);
  assert.match(r15.review_note, /m1 \(scanner blank, AI O\)/);
  assert.strictEqual(r15.ratings.m1, 5, 'Gemini fills the Python blank');
  assert.strictEqual(r15.omr_agreement.agreement_min_percent, 30);
  assert.strictEqual(r15.omr_agreement.flag_agreement_rows, 5);
  const m1 = r15.comparisons.find((c) => c.question === 'm1');
  assert.strictEqual(m1.agree, false);
  assert.strictEqual(m1.resolved_by, null, 'a blank vs a value is a real disagreement');
});
test('Agreement: confident scanner vs Gemini differing on every row -> scanner kept, NOT flagged', async () => {
  const r = await reconcile(python(), geminiWith(far(20)));
  assert.deepStrictEqual(r.review_reasons, []);
  assert.strictEqual(r.agreement_rows, 0, 'reported agreement is still the raw match count');
  assert.strictEqual(r.agreement_percent, 0);
  assert.strictEqual(r.omr_agreement.flag_agreement_rows, 20);
  assert.strictEqual(r.omr_agreement.resolved_differences, 20);
  assert.strictEqual(r.ratings.m1, 5, "Python's value is stored");
  for (const c of r.comparisons) {
    assert.strictEqual(c.agree, false);
    assert.strictEqual(c.resolved_by, 'python_confident', c.question);
  }
  assert.strictEqual(r.review_note, null);
  // Gemini blank on 15 rows a confident Python read -> scanner kept, not flagged
  const gBlank = await reconcile(python(), geminiWith(Object.fromEntries(QS.slice(0, 15).map((q) => [q, '']))));
  assert.strictEqual(gBlank.agreement_rows, 5);
  assert.deepStrictEqual(gBlank.review_reasons, []);
  assert.strictEqual(gBlank.comparisons[0].resolved_by, 'python_confident');
  assert.strictEqual(gBlank.ratings.m1, 5);
});
test('Agreement: weak scanner vs far-apart Gemini -> flagged; adjacent-level differences are not', async () => {
  const farW = await reconcile(python({ weak: weakRows(15) }), geminiWith(far(15)));
  assert.ok(farW.review_reasons.includes('omr_agreement_below_30'), farW.review_reasons.join(','));
  assert.ok(farW.review_reasons.includes('python_weak_disagreement_15_rows'));
  assert.strictEqual(farW.comparisons[0].resolved_by, null);
  const adj = await reconcile(python({ weak: weakRows(15) }), geminiWith(adjacent(15)));
  assert.deepStrictEqual(adj.review_reasons, ['low_confidence_ocr_15_rows'], 'only the weak-row count flags it');
  assert.strictEqual(adj.agreement_rows, 5);
  assert.strictEqual(adj.omr_agreement.flag_agreement_rows, 20);
  assert.strictEqual(adj.comparisons[0].resolved_by, 'adjacent_level');
  // S vs VS on a weak row -> one level apart -> nothing
  assert.deepStrictEqual((await reconcile(python({ weak: weakRows(3, 1) }), geminiWith({ m2: 'S', m3: 'VS', m4: 'S' }))).review_reasons, []);
});
test('Agreement: no confidence data for a row -> scanner not trusted over Gemini', async () => {
  const py = { ...python(), omr_debug: [] };
  const r = await reconcile(py, geminiWith(far(15)));
  assert.deepStrictEqual(r.review_reasons, ['omr_agreement_below_30']);
  assert.strictEqual(r.comparisons[0].resolved_by, null);
});
test('Agreement: note lists only the counted rows and mentions the scanner-kept ones', async () => {
  // rows 0..14 Python blank (counted); p6..p10 confident Python vs far Gemini (kept)
  const g = geminiWith(Object.fromEntries(QS.slice(15).map((q, k) => [q, letters[(15 + k + 2) % 5]])));
  const r = await reconcile(python({ override: blankRows(15) }), g);
  assert.deepStrictEqual(r.review_reasons, ['omr_agreement_below_30']);
  assert.strictEqual(r.agreement_rows, 0);
  assert.strictEqual(r.omr_agreement.flag_agreement_rows, 5);
  assert.match(r.review_note, /only 5 of 20 rows \(25%\) count as agreeing/);
  assert.ok(!/p6 \(/.test(r.review_note), 'a scanner-kept row is not listed as a difference');
  assert.match(r.review_note, /5 other differing row\(s\) were kept as the scanner read them/);
});
test('Agreement: both blank = same; Gemini fills a Python blank', async () => {
  // rows 0..3 blank for BOTH engines -> still 100% agreement
  const bothBlank = await reconcile(python({ override: { 0: '', 1: '', 2: '', 3: '' } }),
    geminiWith({ m1: 'blank', m2: 'blank', m3: 'blank', m4: 'blank' }));
  assert.strictEqual(bothBlank.agreement_percent, 100);
  assert.strictEqual(bothBlank.ratings.m1, null);
  const pyBlank = await reconcile(python({ override: blankRows(15) }));
  assert.strictEqual(pyBlank.agreement_rows, 5);
  assert.ok(pyBlank.review_reasons.includes('omr_agreement_below_30'));
  assert.strictEqual(pyBlank.ratings.m1, 5);
  assert.strictEqual(pyBlank.comparisons[0].source, 'gemini_fill');
});
test('Agreement: flagged for another reason -> the note still states the agreement', async () => {
  const r = await reconcile(python({ grid: 'fallback' }), geminiWith({ m1: 'VS' }));
  assert.deepStrictEqual(r.review_reasons, ['omr_fallback_grid']);
  assert.match(r.review_note, /Python and Gemini agree on 19 of 20 rows \(95%\)\./);
  assert.match(r.review_note, /1 other differing row\(s\) were kept as the scanner read them/);
});
test('Agreement: Python OMR failed -> omr_unavailable (Gemini alone never passes)', async () => {
  const r = await reconcile({ error: 'connect ECONNREFUSED omr-api:5000' });
  assert.ok(r.review_reasons.includes('omr_unavailable'), r.review_reasons.join(','));
  assert.strictEqual(r.agreement_percent, null);
  assert.match(r.review_note, /returned no ratings/);
});

// ---------------------------------------------------------------- website-only hardening (kept)
test('Reconcile hardening: strong agreement -> clean', async () => {
  const r = await reconcile(python());
  assert.deepStrictEqual(r.review_reasons, []);
});
test('Reconcile hardening: WEAK_BLANK_LIMIT / WEAK_DISAGREE_LIMIT = 3 (website)', async () => {
  const code = codeOf('Website Reconcile OCR OMR');
  assert.match(code, /^const WEAK_BLANK_LIMIT\s+= 3;/m);
  assert.match(code, /^const WEAK_DISAGREE_LIMIT = 3;/m);
});
test('Reconcile hardening: specks (weak Python rating, Gemini blank) on 3 rows -> python_weak_gemini_blank_3_rows', async () => {
  const specks = { m2: '', m3: '', m4: '' };
  const r = await reconcile(python({ weak: weakRows(3, 1) }), geminiWith(specks));
  assert.deepStrictEqual(r.review_reasons, ['python_weak_gemini_blank_3_rows'], '17/20 passes the agreement rule, the weak-row rule catches it');
  assert.match(r.review_note, /m2, m3, m4/);
  // 2 specks -> under the limit, clean and no note
  const two = await reconcile(python({ weak: weakRows(2, 1) }), geminiWith({ m2: '', m3: '' }));
  assert.deepStrictEqual(two.review_reasons, []);
  assert.strictEqual(two.review_note, null);
  // a STRONG Python row vs Gemini blank is not flagged by this rule
  assert.deepStrictEqual((await reconcile(python(), geminiWith(specks))).review_reasons, []);
});
test('Reconcile hardening: weak rows, ratings 2+ levels apart on 3 rows -> python_weak_disagreement_3_rows', async () => {
  // rows 10..12 = p1..p3: Python O / VS / S; Gemini S / F / US
  const g = geminiWith({ p1: 'S', p2: 'F', p3: 'US' });
  const r = await reconcile(python({ weak: weakRows(3, 10) }), g);
  assert.deepStrictEqual(r.review_reasons, ['python_weak_disagreement_3_rows']);
  assert.match(r.review_note, /unsure on p1, p2, p3 and the AI read a rating two or more levels away/);
  assert.strictEqual(r.ratings.p1, 5, 'scanner value stored');
  // 2 such rows -> under the limit
  assert.deepStrictEqual((await reconcile(python({ weak: weakRows(2, 10) }), geminiWith({ p1: 'S', p2: 'F' }))).review_reasons, []);
  // 3 weak rows one level apart -> not a conflict
  const adj = await reconcile(python({ weak: weakRows(3, 10) }), geminiWith({ p1: 'VS', p2: 'S', p3: 'F' }));
  assert.deepStrictEqual(adj.review_reasons, []);
  assert.ok(adj.comparisons.slice(10, 13).every((c) => c.resolved_by === 'adjacent_level'));
  // weak but agreeing -> clean
  assert.deepStrictEqual((await reconcile(python({ weak: weakRows(3, 10) }))).review_reasons, []);
});
test('Reconcile hardening: Python unsure on >= 4 rows -> low_confidence_ocr_4_rows even when Gemini agrees', async () => {
  const weak = { 0: { a: 0.04, b: 0.0 }, 1: { a: 0.04, b: 0.0 }, 2: { a: 0.04, b: 0.0 }, 3: { a: 0.04, b: 0.0 } };
  assert.deepStrictEqual((await reconcile(python({ weak }))).review_reasons, ['low_confidence_ocr_4_rows']);
});
test('Reconcile hardening: fixed fallback grid -> omr_fallback_grid', async () => {
  const r = await reconcile(python({ grid: 'fallback' }));
  assert.deepStrictEqual(r.review_reasons, ['omr_fallback_grid']);
  assert.deepStrictEqual((await reconcile(python({ grid: 'auto-detected' }))).review_reasons, []);
});
test('Reconcile hardening: Gemini ratings + no table -> grid_not_detected is kept', async () => {
  const r = await reconcile(python({ table: false, grid: 'fallback' }));
  assert.ok(r.review_reasons.includes('grid_not_detected'), r.review_reasons.join(','));
  assert.ok(r.review_reasons.includes('omr_fallback_grid'));
  assert.ok(Object.values(r.ratings).some((v) => v !== null), 'Gemini/Python ratings present, still flagged');
});
test('Reconcile: poor quality + critical issue -> poor_scan_quality; too many blanks -> too_many_blanks', async () => {
  const pq = await reconcile(python(), geminiResp({ scan_quality: { poor_quality: true, issues: ['low_light'], reason: 'Dark' } }));
  assert.deepStrictEqual(pq.review_reasons, ['poor_scan_quality']);
  const eleven = {};
  const gb = {};
  for (let i = 0; i < 11; i++) { eleven[i] = ''; gb[i < 10 ? `m${i + 1}` : `p${i - 9}`] = 'blank'; }
  const tb = await reconcile(python({ override: eleven }), geminiWith(gb));
  assert.deepStrictEqual(tb.review_reasons, ['too_many_blanks_11_of_20']);
});

// ---------------------------------------------------------------- phone reconciler ('reconcile ocr and omr1')
const PHONE_HOOK = { body: { task_id: 'phone-1', user_id: STAFF, term_id: TERM, image: 'data:image/jpeg;base64,AAAA' } };
async function phoneReconcile(py, g = geminiResp()) {
  return (await run('reconcile ocr and omr1', {}, {
    'image to ocr and omr python': py, 'ai validation ocr and omr': g, 'SAST image from phone': PHONE_HOOK,
  }))[0].json;
}
test('Phone reconcile: engines agree -> auto, same output fields as before + agreement_percent', async () => {
  const r = await phoneReconcile(python());
  assert.deepStrictEqual(r.review_reasons, []);
  assert.strictEqual(r.validation_status, 'auto');
  assert.strictEqual(r.needs_review, false);
  assert.strictEqual(r.agreement_percent, 100);
  for (const k of ['instructor', 'subject', 'ay_semester', 'remarks', 'date', 'student_id', 'ratings', 'document_check',
    'validation_status', 'needs_review', 'review_reasons', 'omr_agreement', 'grid_source', 'review_note', 'comparisons',
    'python_raw_ratings', 'summary', 'task_id', 'user_id', 'term_id', 'scan_image']) assert.ok(k in r, `missing ${k}`);
  assert.strictEqual(r.instructor, 'Dr. Juan A. Dela Cruz, MIT');
  assert.strictEqual(r.student_id, '2021 0001');
  assert.strictEqual(r.task_id, 'phone-1');
  assert.strictEqual(r.scan_image, 'AAAA');
  assert.strictEqual(r.review_note, null);
});
test('Phone reconcile: Gemini-only OCR -- blank Gemini text never falls back to Python ocr_fields', async () => {
  const g = geminiResp({ instructor: '', subject: '', ay_semester: '', remarks: '', date: '', student_id: '' });
  const r = await phoneReconcile(python({ ocr: PY_OCR_GARBAGE }), g);
  for (const k of ['instructor', 'subject', 'ay_semester', 'remarks', 'date', 'student_id']) assert.strictEqual(r[k], '', k);
  assert.deepStrictEqual(r.review_reasons, ['missing_instructor', 'missing_subject'], 'phone keeps the missing-name flags');
  const bad = { candidates: [{ content: { parts: [{ text: 'not json' }] } }] };
  const r2 = await phoneReconcile(python({ ocr: PY_OCR_GARBAGE }), bad);
  assert.ok(r2.review_reasons.includes('gemini_unavailable'));
  assert.strictEqual(r2.instructor, '');
  assert.strictEqual(r2.student_id, '');
});
test('Phone reconcile: agreement 6/20 passes, 5/20 -> omr_agreement_below_30 (replaces omr_disagreement)', async () => {
  assert.deepStrictEqual((await phoneReconcile(python({ override: blankRows(14) }))).review_reasons, []);
  const r = await phoneReconcile(python({ override: blankRows(15) }));
  assert.deepStrictEqual(r.review_reasons, ['omr_agreement_below_30']);
  assert.strictEqual(r.agreement_percent, 25);
  assert.strictEqual(r.validation_status, 'flagged');
  assert.match(r.review_note, /only 5 of 20 rows \(25%\) count as agreeing/);
});
test('Phone reconcile: confident scanner vs Gemini differing -> not flagged; weak + far apart -> flagged; adjacent -> not', async () => {
  const conf = await phoneReconcile(python(), geminiWith(far(20)));
  assert.deepStrictEqual(conf.review_reasons, []);
  assert.strictEqual(conf.agreement_percent, 0);
  assert.ok(conf.comparisons.every((c) => c.resolved_by === 'python_confident'));
  assert.strictEqual(conf.ratings.m1, 5);
  const farW = await phoneReconcile(python({ weak: weakRows(15) }), geminiWith(far(15)));
  assert.deepStrictEqual(farW.review_reasons, ['omr_agreement_below_30', 'low_confidence_ocr_15_rows']);
  const adj = await phoneReconcile(python({ weak: weakRows(15) }), geminiWith(adjacent(15)));
  assert.deepStrictEqual(adj.review_reasons, ['low_confidence_ocr_15_rows']);
  assert.strictEqual(adj.comparisons[0].resolved_by, 'adjacent_level');
});
test('Phone reconcile: Python OMR crashed -> omr_unavailable (no Gemini-only pass); weak rows still flagged', async () => {
  const r = await phoneReconcile({ error: 'Request failed with status code 400' });
  assert.ok(r.review_reasons.includes('omr_unavailable'), r.review_reasons.join(','));
  assert.strictEqual(r.needs_review, true);
  const weak = { 0: { a: 0.04, b: 0.0 }, 1: { a: 0.04, b: 0.0 }, 2: { a: 0.04, b: 0.0 }, 3: { a: 0.04, b: 0.0 } };
  const w = await phoneReconcile(python({ weak }));
  assert.deepStrictEqual(w.review_reasons, ['low_confidence_ocr_4_rows']);
  assert.match(w.review_note, /Python and Gemini agree on 20 of 20 rows \(100%\)\./);
});
test('Phone reconcile: grid_not_detected still dropped when Python read the grid and Gemini agrees (phone rule kept)', async () => {
  assert.deepStrictEqual((await phoneReconcile(python({ table: false }))).review_reasons, []);
});
test('Handle Gemini failure (phone): student id no longer taken from Python OCR', async () => {
  const code = codeOf('Handle Gemini failure');
  assert.ok(!code.includes('ocr_fields'), 'must not read py.ocr_fields');
  const out = await run('Handle Gemini failure', { error: { message: 'timeout' } }, {
    'SAST image from phone': PHONE_HOOK, 'image to ocr and omr python': python({ ocr: PY_OCR_GARBAGE }),
  });
  const r = out[0].json;
  assert.strictEqual(r.student_id, '');
  assert.deepStrictEqual(r.review_reasons, ['ai_validation_failed']);
  assert.strictEqual(r.task_id, 'phone-1');
});

// ---------------------------------------------------------------- layout safety net (layout_check.js)
// Python /process "layout" evidence. GOOD = a correctly read long-bond scan; OTHER = a scan of the
// form whose rating block is printed wider (2026-10-01 set): measured values from the real OMR.
const GOOD_LAYOUT = {
  grid_source: 'structure-fit', expected_columns: 5, expected_rows: 20, columns_detected: 5,
  column_rulings: [1494, 1604, 1715, 1825, 1936, 2044], column_boundaries_matched: 6, column_boundaries_total: 6,
  rows_detected: 20, unexplained_rulings: [], boundaries_supported: 22, boundaries_total: 22,
  structure_fit: { row_spacing: 37.1, units_per_section: [10, 12], body_units: 22, units_pinned: true, score: 21.67,
    score_per_boundary: 0.985 },
};
const OTHER_LAYOUT = {
  ...GOOD_LAYOUT, column_rulings: [1295, 1444, 1593, 1747, 1892, 2042], column_boundaries_matched: 2,
  rows_detected: 25, unexplained_rulings: [130, 250, 610, 690, 800], boundaries_supported: 16,
  structure_fit: { ...GOOD_LAYOUT.structure_fit, units_per_section: [12, 13], body_units: 25, score: 6.23, score_per_boundary: 0.283 },
};
const ORDER = ['O', 'VS', 'S', 'F', 'US'];
const pyLayout = (layout, opts = {}) => ({ ...python(opts), layout });
const BOTH_RECONCILERS = [['website', reconcile], ['phone', (py, g) => phoneReconcile(py, g)]];
test('Layout: matching layout (Python evidence + Gemini column_order O..US) -> no flag, both reconcilers', async () => {
  for (const [where, rec] of BOTH_RECONCILERS) {
    const r = await rec(pyLayout(GOOD_LAYOUT), geminiResp({ column_order: ORDER }));
    assert.deepStrictEqual(r.review_reasons, [], where);
    assert.deepStrictEqual(r.layout_check, { mismatch: false, signals: [], column_order: ORDER, python_layout: GOOD_LAYOUT }, where);
    // same order written differently: a string, full words, the printed numbers 5..1
    for (const co of ['O, VS, S, F, US', ['o', 'v.s.', 'S', 'F', 'U.S.'], ['Outstanding', 'Very Satisfactory', 'Satisfactory', 'Fair', 'Unsatisfactory'],
      ['5', '4', '3', '2', '1'], [5, 4, 3, 2, 1]]) {
      const x = await rec(pyLayout(GOOD_LAYOUT), geminiResp({ column_order: co }));
      assert.deepStrictEqual(x.review_reasons, [], `${where} ${JSON.stringify(co)}`);
    }
    // a confident scanner still wins over Gemini when the layout matches (rule unchanged)
    const conf = await rec(pyLayout(GOOD_LAYOUT), geminiWith(far(20), { column_order: ORDER }));
    assert.deepStrictEqual(conf.review_reasons, [], where);
    assert.ok(conf.comparisons.every((c) => c.resolved_by === 'python_confident'), where);
  }
});
test('Layout: reordered columns (Gemini column_order) -> layout_mismatch; the confident scanner no longer wins', async () => {
  for (const [where, rec] of BOTH_RECONCILERS) {
    for (const co of [['US', 'F', 'S', 'VS', 'O'], ['1', '2', '3', '4', '5'], ['VS', 'O', 'S', 'F', 'US']]) {
      const r = await rec(pyLayout(GOOD_LAYOUT), geminiResp({ column_order: co }));
      assert.deepStrictEqual(r.review_reasons, ['layout_mismatch'], `${where} ${co}`);
      assert.strictEqual(r.needs_review, true);
      assert.strictEqual(r.layout_check.mismatch, true);
      assert.deepStrictEqual(r.layout_check.signals, [`column_order_${r.layout_check.column_order.join('_')}`]);
      assert.match(r.review_note, /layout does not match the expected SAST form .*the AI read the column headers as /);
    }
    // reversed headers: Gemini reads every row by its header, the scanner by position (far apart).
    // Without the layout check this page is clean (python_confident on all 20 rows, see above).
    const rev = await rec(pyLayout(GOOD_LAYOUT), geminiWith(far(20), { column_order: ['US', 'F', 'S', 'VS', 'O'] }));
    assert.ok(rev.review_reasons.includes('layout_mismatch'), `${where}: ${rev.review_reasons}`);
    assert.ok(rev.review_reasons.includes('omr_agreement_below_30'), `${where}: no row is resolved as python_confident`);
    assert.ok(rev.comparisons.every((c) => c.resolved_by !== 'python_confident'), where);
    assert.strictEqual(rev.omr_agreement.resolved_differences, 0, where);
  }
});
test('Layout: wrong column count (Python columns_detected, Gemini header count) -> layout_mismatch', async () => {
  for (const [where, rec] of BOTH_RECONCILERS) {
    for (const n of [4, 6, 0]) {
      const r = await rec(pyLayout({ ...GOOD_LAYOUT, columns_detected: n }));
      assert.deepStrictEqual(r.review_reasons, ['layout_mismatch'], `${where} ${n}`);
      assert.deepStrictEqual(r.layout_check.signals, [`columns_detected_${n}`]);
      assert.match(r.review_note, new RegExp(`found ${n} rating columns instead of 5`));
    }
    for (const co of [['O', 'VS', 'S', 'F'], ['O', 'VS', 'S', 'F', 'US', 'NA']]) {
      const r = await rec(pyLayout(GOOD_LAYOUT), geminiResp({ column_order: co }));
      assert.deepStrictEqual(r.review_reasons, ['layout_mismatch'], `${where} ${co}`);
    }
  }
});
test('Layout: the other printed layout (real measurements) -> layout_mismatch on every Python signal', async () => {
  for (const [where, rec] of BOTH_RECONCILERS) {
    const r = await rec(pyLayout(OTHER_LAYOUT));
    assert.deepStrictEqual(r.review_reasons, ['layout_mismatch'], where);
    assert.deepStrictEqual(r.layout_check.signals, ['columns_misaligned', 'rows_detected_25', 'row_rulings_missing', 'structure_fit_poor']);
    assert.ok(r.comparisons.every((c) => c.resolved_by !== 'python_confident'));
    // each signal alone flags; one faint column ruling (5/6) or 19/22 row lines do not
    for (const [over, sig] of [[{ column_boundaries_matched: 4 }, 'columns_misaligned'], [{ rows_detected: 22 }, 'rows_detected_22'],
      [{ boundaries_supported: 18 }, 'row_rulings_missing'],
      [{ structure_fit: { ...GOOD_LAYOUT.structure_fit, score_per_boundary: 0.45 } }, 'structure_fit_poor']]) {
      assert.deepStrictEqual((await rec(pyLayout({ ...GOOD_LAYOUT, ...over }))).layout_check.signals, [sig], `${where} ${sig}`);
    }
    for (const over of [{ column_boundaries_matched: 5 }, { boundaries_supported: 19 }, { structure_fit: null },
      { structure_fit: { ...GOOD_LAYOUT.structure_fit, score_per_boundary: 0.645 } }]) {
      assert.deepStrictEqual((await rec(pyLayout({ ...GOOD_LAYOUT, ...over }))).review_reasons, [], `${where} ${JSON.stringify(over)}`);
    }
  }
});
test('Layout: fields absent (old Python service, Gemini without column_order) -> never flags', async () => {
  for (const [where, rec] of BOTH_RECONCILERS) {
    for (const layout of [undefined, null, {}, { error: 'boom' }, 'weird', [], { columns_detected: '6', rows_detected: null }]) {
      const py = layout === undefined ? python() : pyLayout(layout);
      for (const co of [undefined, null, [], '', {}, 42]) {
        const r = await rec(py, geminiResp(co === undefined ? {} : { column_order: co }));
        assert.deepStrictEqual(r.review_reasons, [], `${where} layout ${JSON.stringify(layout)} order ${JSON.stringify(co)}`);
        assert.strictEqual(r.layout_check.mismatch, false);
      }
    }
    // Gemini failed / not a form: its column_order is not used
    const bad = { candidates: [{ content: { parts: [{ text: 'no json here' }] } }] };
    assert.ok(!(await rec(pyLayout(GOOD_LAYOUT), bad)).review_reasons.includes('layout_mismatch'), where);
  }
});
test('Layout: fixed fallback grid + low row agreement -> layout_mismatch (phone too); fallback + high agreement -> no', async () => {
  // confident Python vs Gemini far apart on 10 rows: 10/20 agree (< 80%)
  const web = await reconcile(python({ grid: 'fallback' }), geminiWith(far(10)));
  assert.deepStrictEqual(web.review_reasons, ['omr_fallback_grid', 'layout_mismatch']);
  assert.deepStrictEqual(web.layout_check.signals, ['fallback_grid_low_agreement']);
  assert.ok(web.comparisons.every((c) => c.resolved_by !== 'python_confident'));
  const phone = await phoneReconcile(python({ grid: 'fallback' }), geminiWith(far(10)));
  assert.deepStrictEqual(phone.review_reasons, ['layout_mismatch']);
  assert.strictEqual(phone.validation_status, 'flagged');
  // 16/20 agree -> enough; the website still flags the fallback grid itself
  assert.deepStrictEqual((await reconcile(python({ grid: 'fallback' }), geminiWith(far(4)))).review_reasons, ['omr_fallback_grid']);
  assert.deepStrictEqual((await phoneReconcile(python({ grid: 'fallback' }), geminiWith(far(4)))).review_reasons, []);
  // same disagreement on a located grid: the confident scanner wins as before
  assert.deepStrictEqual((await reconcile(python(), geminiWith(far(10)))).review_reasons, []);
});
test('Layout: phone keeps layout_mismatch even when Gemini found answers (unlike grid_not_detected)', async () => {
  const r = await phoneReconcile(pyLayout(OTHER_LAYOUT, { table: false }));
  assert.deepStrictEqual(r.review_reasons, ['layout_mismatch']);
});
test('Layout: a layout_mismatch page is never credited (Decide -> flagged)', async () => {
  const rec = await reconcile(pyLayout(OTHER_LAYOUT));
  const d = await decide(identity(), rec);
  assert.strictEqual(d.decision, 'flagged');
  assert.ok(d.review_reasons.includes('layout_mismatch'));
  assert.match(d.review_note, /layout does not match the expected SAST form/);
});
test('Layout: both main Gemini prompts ask for the optional column_order; one identical layout_check.js copy per reconciler', async () => {
  for (const name of ['ai validation ocr and omr', 'Website AI Validation']) {
    const b = wf.nodes.find((x) => x.name === name).parameters.jsonBody;
    assert.ok(b.includes('- Return letters, not numbers.\\n- column_order: the rating column headers exactly as printed'), name);
    assert.ok(b.includes('"column_order": { "type": "ARRAY", "items": { "type": "STRING" } }'), name);
    const req = b.slice(b.lastIndexOf('"required"'));
    assert.ok(!req.includes('column_order'), `${name}: column_order is optional`);
  }
  const copies = ['Website Reconcile OCR OMR', 'reconcile ocr and omr1'].map((n) => {
    const c = codeOf(n);
    const i = c.indexOf('// layout check, from website_src/layout_check.js');
    return c.slice(i, c.indexOf('// end of layout check', i));
  });
  assert.ok(copies[0].length > 100 && copies[0] === copies[1]);
});

// ---------------------------------------------------------------- Decide
// Decide: its input is 'Website Form Match' (output 0 = row, output 1 = error item); the batch
// check row is read by name from 'Website Identity Check' (output 0, else its error output 1).
async function decide(idRow, rec = REC, fm = formMatch()) {
  return (await run('Website Decide', fm, {
    'Website Prepare Scan': CTX, 'Website Reconcile OCR OMR': rec, 'Website Identity Check': idRow,
  }))[0].json;
}
test('Decide: valid batch -> clean, batch name + ids, blank ratings stay null', async () => {
  const d = await decide(identity());
  assert.strictEqual(d.decision, 'clean');
  assert.strictEqual(d.instructor_name, 'Juan Dela Cruz');
  assert.strictEqual(d.term_id, TERM);
  assert.strictEqual(d.identity_check, null);
  assert.strictEqual(d.agreement_percent, 100);
  assert.strictEqual(d.partial_data.agreement_percent, 100);
  assert.strictEqual(d.partial_data.instructor_match, 'same');
  assert.strictEqual(d.partial_data.subject_match, 'same');
  assert.match(d.submitted_date, /^\d{4}-\d{2}-\d{2}$/);
  const withBlank = { ...REC, ratings: { ...REC.ratings, m3: null } };
  assert.strictEqual((await decide(identity(), withBlank)).ratings.m3, null);
});
test('Form check: same instructor (title / initials / misspelling / surname only) + batch subject -> clean, batch credited', async () => {
  // what 'Website Form Match' scores for "Dr. Juan A. Dela Cruz, MIT", "J. Dela Cruz", "Juan Dela Crus", "Dela Cruz"
  for (const [norm, batchScore, other] of [['juan a dela cruz', 0.95, 0.85], ['j dela cruz', 0.85, 0.63],
    ['juan dela crus', 0.95, 0.6], ['dela cruz', 0.7, 0.7]]) {
    const d = await decide(identity(), REC, formMatch({ i: norm, ic: [BATCH_I(batchScore), icand(ANA, 'Ana Dela Cruz', other)] }));
    assert.strictEqual(d.decision, 'clean', norm);
    assert.deepStrictEqual(d.review_reasons, []);
    assert.strictEqual(d.identity_check, null);
    assert.strictEqual(d.instructor_name, 'Juan Dela Cruz');
    assert.strictEqual(d.partial_data.instructor_match, 'same', `${norm}: batch wins ties`);
    assert.strictEqual(d.partial_data.suggested_instructor_id, INSTR);
    assert.ok(d.partial_data.instructor_match_confidence >= batchScore, `${norm}: SQL score or the higher token score`);
  }
  // batch subject found by its name (>= SUBJECT_MATCH_MIN) and no other subject code on the form
  const byName = await decide(identity(), REC, formMatch({ s: 'intro to computing', sc: [BATCH_S({ ns: 0.6667 })] }));
  assert.strictEqual(byName.decision, 'clean');
  assert.strictEqual(byName.partial_data.subject_match, 'same');
  assert.ok(byName.partial_data.subject_match_confidence >= 0.67, 'SQL score or the higher token score');
});
test('Form check: another instructor found -> instructor_mismatch_found, suggestion pre-filled, nothing credited', async () => {
  const rec = { ...REC, ocr_instructor: 'Prof. Pedro Reyes' };
  const fm = formMatch({ i: 'pedro reyes', ic: [icand(PEDRO, 'Pedro Reyes', 1), BATCH_I(0), icand(ANA, 'Ana Dela Cruz', 0.31)] });
  const d = await decide(identity(), rec, fm);
  assert.strictEqual(d.decision, 'flagged');
  assert.deepStrictEqual(d.review_reasons, ['instructor_mismatch_found']);
  const pd = d.partial_data;
  assert.strictEqual(pd.instructor, 'Pedro Reyes', 'Flutter correction screen defaults to the suggestion');
  assert.strictEqual(pd.subject, 'IT 101');
  assert.strictEqual(pd.instructor_match, 'found');
  assert.strictEqual(pd.suggested_instructor_id, PEDRO);
  assert.strictEqual(pd.suggested_instructor_name, 'Pedro Reyes');
  assert.strictEqual(pd.instructor_match_confidence, 1);
  assert.strictEqual(pd.suggested_subject_id, SUBJ);
  assert.strictEqual(pd.suggested_subject_code, 'IT 101');
  assert.strictEqual(pd.batch_instructor_name, 'Juan Dela Cruz');
  assert.strictEqual(pd.batch_instructor_id, INSTR);
  assert.strictEqual(pd.ocr_instructor, 'Prof. Pedro Reyes');
  assert.deepStrictEqual(pd.instructor_candidates.map((c) => [c.id, c.score]), [[PEDRO, 1], [ANA, 0.31]], 'score >= 0.30 only');
  assert.deepStrictEqual(Object.keys(pd.instructor_candidates[0]).sort(), ['id', 'is_batch', 'is_placeholder', 'name', 'score']);
  for (const k of FORM_PD_KEYS) assert.ok(k in pd, k);
  assert.match(d.review_note, /"Prof\. Pedro Reyes", which matches Pedro Reyes in the database, not the batch instructor Juan Dela Cruz/);
  assert.match(d.review_note, /Nothing was credited/);
  assert.deepStrictEqual(d.identity_check, { instructor: 'mismatch', subject: 'match', ocr_instructor: 'Prof. Pedro Reyes', ocr_subject: 'IT 1O1' });
  // a clean page is always credited to the batch; a found page is never clean
  assert.strictEqual(d.instructor_id, INSTR);
  // a placeholder instructor can be the suggestion too
  const ph = await decide(identity(), rec, formMatch({ i: 'nora villanueva', ic: [icand(NORA, 'Nora Villanueva', 1, { is_placeholder: true }), BATCH_I(0)] }));
  assert.strictEqual(ph.partial_data.suggested_instructor_id, NORA);
  assert.strictEqual(ph.partial_data.instructor_candidates[0].is_placeholder, true);
  // the best other one leads the second by exactly MATCH_MARGIN (float-safe) -> found
  const margin = await decide(identity(), rec, formMatch({ i: 'ana dela cruz', ic: [icand(ANA, 'Ana Dela Cruz', 0.95), icand(PEDRO, 'Pedro Reyes', 0.85), BATCH_I(0.6)] }));
  assert.strictEqual(margin.partial_data.instructor_match, 'found');
  assert.strictEqual(margin.partial_data.suggested_instructor_id, ANA);
});
test('Form check: low-confidence instructor/subject reading never produces a *_mismatch_* flag', async () => {
  const rec = { ...REC, ocr_instructor: 'Prof. Pedro Reyes', instructor_confidence: 'low' };
  const fm = formMatch({ i: 'pedro reyes', ic: [icand(PEDRO, 'Pedro Reyes', 1), BATCH_I(0), icand(ANA, 'Ana Dela Cruz', 0.31)] });
  const d = await decide(identity(), rec, fm);
  assert.strictEqual(d.decision, 'clean', 'low confidence -> never flagged for this alone');
  assert.deepStrictEqual(d.review_reasons, []);
  assert.strictEqual(d.identity_check, null);
  // the batch instructor is credited, not the low-confidence "found" instructor
  assert.strictEqual(d.instructor_id, INSTR);
  assert.strictEqual(d.partial_data.instructor, 'Juan Dela Cruz');
  assert.strictEqual(d.partial_data.suggested_instructor_id, INSTR);
  assert.strictEqual(d.partial_data.instructor_confidence, 'low');
  assert.match(d.review_note, /not confident in that reading, so it was not used to flag a mismatch/);
  // 'medium'/'high'/missing confidence still flags normally
  for (const c of ['medium', 'high', '', undefined, 'garbage']) {
    const d2 = await decide(identity(), { ...rec, instructor_confidence: c }, fm);
    assert.deepStrictEqual(d2.review_reasons, ['instructor_mismatch_found'], `confidence=${c}`);
  }
  // same gate for the subject
  const srec = { ...REC, ocr_subject: 'CC 101', subject_confidence: 'low' };
  const sfm = formMatch({ s: 'cc 101', sc: [scand(IT102, 'CC 101', 'Intro to Computing', { hit: true }), BATCH_S({ hit: false, ns: 0 })] });
  const sd = await decide(identity(), srec, sfm);
  assert.deepStrictEqual(sd.review_reasons, []);
  assert.strictEqual(sd.subject_code, 'IT 101', 'batch subject credited');
  assert.strictEqual(sd.partial_data.subject_confidence, 'low');
});
test('Form check: shared surname -> instructor_mismatch_ambiguous (batch pre-selected); unknown name -> not_found', async () => {
  // batch = Juan Dela Cruz; the form says "Santos" (Maria Santos and Juan Carlos Santos both 0.70)
  const amb = await decide(identity(), { ...REC, ocr_instructor: 'Santos' },
    formMatch({ i: 'santos', ic: [icand(JCARLOS, 'Juan Carlos Santos', 0.7), icand(MARIA, 'Maria Santos', 0.7), BATCH_I(0)] }));
  assert.deepStrictEqual(amb.review_reasons, ['instructor_mismatch_ambiguous']);
  assert.strictEqual(amb.partial_data.instructor_match, 'ambiguous');
  assert.strictEqual(amb.partial_data.suggested_instructor_id, INSTR, 'ambiguous -> the batch is pre-selected');
  assert.strictEqual(amb.partial_data.instructor, 'Juan Dela Cruz');
  assert.strictEqual(amb.partial_data.instructor_match_confidence, 0);
  assert.deepStrictEqual(amb.partial_data.instructor_candidates.map((c) => c.id), [JCARLOS, MARIA]);
  assert.match(amb.review_note, /several instructors about equally well \(Juan Carlos Santos, Maria Santos\)/);
  // two within MATCH_MARGIN (0.95 vs 0.86)
  const close = await decide(identity(), REC, formMatch({ i: 'a reyes', ic: [icand(ANA, 'Ana Dela Cruz', 0.95), icand(PEDRO, 'Pedro Reyes', 0.86), BATCH_I(0.5)] }));
  assert.strictEqual(close.partial_data.instructor_match, 'ambiguous');
  // nobody matches -> not flagged, the batch stays (staff picked it)
  const nf = await decide(identity(), { ...REC, ocr_instructor: 'Jose Rizal' },
    formMatch({ i: 'jose rizal', ic: [icand(PEDRO, 'Pedro Reyes', 0.05), BATCH_I(0.04)] }));
  assert.strictEqual(nf.decision, 'clean');
  assert.deepStrictEqual(nf.review_reasons, []);
  assert.strictEqual(nf.partial_data.instructor_match, 'not_found');
  assert.strictEqual(nf.partial_data.suggested_instructor_id, INSTR);
  assert.strictEqual(nf.partial_data.suggested_instructor_name, 'Juan Dela Cruz');
  assert.strictEqual(nf.partial_data.instructor, 'Juan Dela Cruz');
  assert.strictEqual(nf.partial_data.instructor_match_confidence, 0.04);
  assert.deepStrictEqual(nf.partial_data.instructor_candidates, []);
  assert.match(nf.review_note, /"Jose Rizal", which does not clearly match anyone else, so the batch instructor Juan Dela Cruz was kept/);
  assert.strictEqual(nf.identity_check, null);
  assert.strictEqual(nf.instructor_id, INSTR, 'credited to the batch');
  // a close other match with a weak batch: the batch is among the contenders -> ambiguous, not flagged
  const near = await decide(identity(), { ...REC, ocr_instructor: 'x' },
    formMatch({ i: 'some name', ic: [icand(PEDRO, 'Pedro Reyes', 0.75), BATCH_I(0.6)] }));
  assert.strictEqual(near.partial_data.instructor_match, 'ambiguous');
  assert.deepStrictEqual(near.review_reasons, []);
  assert.strictEqual(near.partial_data.suggested_instructor_id, INSTR);
  // another instructor must beat the batch by BATCH_MARGIN: 0.85 vs 0.72 is not enough
  const lead = await decide(identity(), REC, formMatch({ i: 'some name', ic: [icand(PEDRO, 'Pedro Reyes', 0.85), BATCH_I(0.72)] }));
  assert.strictEqual(lead.partial_data.instructor_match, 'same');
  assert.deepStrictEqual(lead.review_reasons, []);
  const beat = await decide(identity(), REC, formMatch({ i: 'some name', ic: [icand(PEDRO, 'Pedro Reyes', 0.95), BATCH_I(0.72)] }));
  assert.deepStrictEqual(beat.review_reasons, ['instructor_mismatch_found']);
});
test('Form check: blank / placeholder / too short instructor or subject -> unreadable, instructor_missing + subject_missing, batch pre-selected', async () => {
  const cases = [['', ''], ['n a', 'n a'], ['j c', 'x'], ['unknown', 'subject'], ['illegible', 'none']];
  for (const [i, s] of cases) {
    const d = await decide(identity(), { ...REC, ocr_instructor: i, ocr_subject: s },
      formMatch({ i, ic: [BATCH_I(0), icand(PEDRO, 'Pedro Reyes', 0.9)], s, sc: [BATCH_S({ ns: 0 }), scand(IT102, 'IT 102', 'x', { hit: true })] }));
    assert.strictEqual(d.decision, 'flagged', `${i} / ${s}`);
    assert.deepStrictEqual(d.review_reasons, ['instructor_missing', 'subject_missing']);
    assert.match(d.review_note, /instructor on the form is blank or could not be read.*batch instructor Juan Dela Cruz is pre-selected/);
    assert.match(d.review_note, /subject on the form is blank or could not be read.*batch subject IT 101 is pre-selected/);
    assert.strictEqual(d.partial_data.instructor, 'Juan Dela Cruz');
    assert.strictEqual(d.partial_data.subject, 'IT 101');
    assert.strictEqual(d.partial_data.instructor_match, 'unreadable');
    assert.strictEqual(d.partial_data.subject_match, 'unreadable');
    assert.strictEqual(d.partial_data.instructor_match_confidence, null);
    assert.strictEqual(d.partial_data.suggested_instructor_id, INSTR);
    assert.strictEqual(d.partial_data.suggested_subject_id, SUBJ);
    assert.strictEqual(d.identity_check, null);
  }
});
test('Form check: subject code hit / other code hit / name match / link bonus / not found / ambiguous', async () => {
  const run1 = (s, sc, extra = {}) => decide(identity(), { ...REC, ocr_subject: s }, formMatch({ s, sc, ...extra }));
  // batch code on the form -> same even when another name also scores well
  let d = await run1('it 101', [BATCH_S(), scand(IT102, 'IT 102', 'Computer Programming 1', { ns: 0.9 })]);
  assert.strictEqual(d.partial_data.subject_match, 'same');
  assert.strictEqual(d.partial_data.subject_match_confidence, 1);
  // another subject's code on the form -> found, even when the batch name also matches (a code hit wins)
  d = await run1('it 102 introduction to computing', [scand(IT102, 'IT 102', 'Computer Programming 1', { hit: true }), BATCH_S({ ns: 1 })]);
  assert.deepStrictEqual(d.review_reasons, ['subject_mismatch_found']);
  assert.strictEqual(d.partial_data.subject, 'IT 102', 'Flutter correction screen defaults to the suggestion');
  assert.strictEqual(d.partial_data.suggested_subject_id, IT102);
  assert.strictEqual(d.partial_data.suggested_subject_code, 'IT 102');
  assert.strictEqual(d.partial_data.suggested_subject_name, 'Computer Programming 1');
  assert.strictEqual(d.partial_data.subject_match_confidence, 1);
  assert.strictEqual(d.partial_data.suggested_instructor_id, INSTR, 'instructor unchanged');
  assert.deepStrictEqual(Object.keys(d.partial_data.subject_candidates[0]).sort(), ['code', 'id', 'is_batch', 'name', 'score']);
  assert.deepStrictEqual(d.identity_check, { instructor: 'match', subject: 'mismatch', ocr_instructor: 'Dr. Juan A. Dela Cruz, MIT', ocr_subject: 'it 102 introduction to computing' });
  // another subject by name (no code on the form)
  d = await run1('mathematics in the modern world', [scand(GE104, 'GE 104', 'Mathematics in the Modern World', { ns: 1 }), BATCH_S({ ns: 0.11 })]);
  assert.strictEqual(d.partial_data.subject_match, 'found');
  assert.strictEqual(d.partial_data.suggested_subject_id, GE104);
  // part of another title is not enough, even with the link bonus (subject taught by the target
  // instructor this term, +0.05 on its score) -> not flagged, batch kept
  const linked = [scand(IT102, 'IT 102', 'Computer Programming 1', { ns: 0.57, linked: [INSTR] }), BATCH_S({ ns: 0.08 })];
  d = await run1('programming', linked);
  assert.strictEqual(d.partial_data.subject_match, 'not_found');
  assert.deepStrictEqual(d.review_reasons, []);
  assert.strictEqual(d.partial_data.subject_candidates.find((c) => c.id === IT102).score, 0.71);
  // ... the bonus follows the TARGET instructor: found instructor Pedro is not linked -> no bonus
  d = await decide(identity(), REC, formMatch({ i: 'pedro reyes', ic: [icand(PEDRO, 'Pedro Reyes', 1), BATCH_I(0)], s: 'programming', sc: linked }));
  assert.deepStrictEqual(d.review_reasons, ['instructor_mismatch_found']);
  assert.strictEqual(d.partial_data.subject_candidates.find((c) => c.id === IT102).score, 0.66);
  assert.strictEqual(d.partial_data.suggested_subject_id, SUBJ, 'not found -> batch subject suggested');
  assert.strictEqual(d.partial_data.subject, 'IT 101');
  // nothing matches -> not flagged
  d = await run1('xyz 999', [BATCH_S({ ns: 0 })]);
  assert.deepStrictEqual(d.review_reasons, []);
  assert.strictEqual(d.partial_data.subject_match, 'not_found');
  assert.strictEqual(d.partial_data.suggested_subject_code, 'IT 101');
  assert.match(d.review_note, /"xyz 999", which does not clearly match another subject, so the batch subject IT 101 was kept/);
  // two names about equally good -> ambiguous
  d = await run1('computer programming', [scand(IT102, 'IT 102', 'Computer Programming 1', { ns: 0.83 }),
    scand(IT103, 'IT 103', 'Computer Programming 2', { ns: 0.83 }), BATCH_S({ ns: 0.1 })]);
  assert.deepStrictEqual(d.review_reasons, ['subject_mismatch_ambiguous']);
  assert.strictEqual(d.partial_data.suggested_subject_id, SUBJ);
  assert.match(d.review_note, /several subjects about equally well \(IT 102, IT 103\)/);
  // two other subject codes on the form -> ambiguous (never guessed)
  d = await run1('it 102 it 103', [scand(IT102, 'IT 102', 'a', { hit: true }), scand(IT103, 'IT 103', 'b', { hit: true }), BATCH_S({ ns: 0 })]);
  assert.strictEqual(d.partial_data.subject_match, 'ambiguous');
  // batch name >= 0.60 but another subject's code on the form -> that code wins (found)
  d = await run1('introduction to computing ge 104', [scand(GE104, 'GE 104', 'Math', { hit: true }), BATCH_S({ ns: 0.9 })]);
  assert.strictEqual(d.partial_data.subject_match, 'found');
  assert.strictEqual(d.partial_data.suggested_subject_id, GE104);
});
test('Form check: Website Form Match query error -> form_match_failed, held with the batch values', async () => {
  const d = (await run('Website Decide', { error: { message: 'function word_similarity does not exist' } }, {
    'Website Prepare Scan': CTX, 'Website Reconcile OCR OMR': { ...REC, ocr_instructor: 'Pedro Reyes' }, 'Website Identity Check': identity(),
  }))[0].json;
  assert.strictEqual(d.decision, 'flagged');
  assert.deepStrictEqual(d.review_reasons, ['form_match_failed']);
  assert.match(d.review_note, /could not be checked against the database \(function word_similarity does not exist\)/);
  const pd = d.partial_data;
  assert.strictEqual(pd.instructor_match, 'unchecked');
  assert.strictEqual(pd.subject_match, 'unchecked');
  assert.strictEqual(pd.suggested_instructor_id, INSTR);
  assert.strictEqual(pd.suggested_subject_id, SUBJ);
  assert.strictEqual(pd.instructor, 'Juan Dela Cruz');
  assert.strictEqual(pd.subject, 'IT 101');
  assert.strictEqual(pd.instructor_match_confidence, null);
  assert.deepStrictEqual(pd.instructor_candidates, []);
  assert.strictEqual(d.identity_check, null, 'no mismatch reason -> null');
});
test('Form check: duplicate_student_id (vs the batch) dropped when the suggestion differs, kept otherwise', async () => {
  const dupRow = identity({ duplicate_student_exists: true, duplicate_student_task_id: 'web:other' });
  const found = formMatch({ i: 'pedro reyes', ic: [icand(PEDRO, 'Pedro Reyes', 1), BATCH_I(0)] });
  const d = await decide(dupRow, REC, found);
  assert.deepStrictEqual(d.review_reasons, ['instructor_mismatch_found'], 'duplicate against the batch does not apply');
  assert.match(d.review_note, /checked again when it is approved/);
  const subjFound = formMatch({ s: 'it 102', sc: [scand(IT102, 'IT 102', 'Computer Programming 1', { hit: true }), BATCH_S({ ns: 0.04 })] });
  assert.deepStrictEqual((await decide(dupRow, REC, subjFound)).review_reasons, ['subject_mismatch_found']);
  // same target (or not found / ambiguous: the batch stays the suggestion) -> the duplicate stays a reason
  assert.deepStrictEqual((await decide(dupRow)).review_reasons, ['duplicate_student_id']);
  const nf = formMatch({ i: 'jose rizal', ic: [BATCH_I(0.04)] });
  assert.deepStrictEqual((await decide(dupRow, REC, nf)).review_reasons, ['duplicate_student_id']);
});
test('Missing header fields: blank / placeholder student ID -> student_id_missing (stored NULL), never a duplicate; instructor/subject only when unreadable', async () => {
  for (const sid of ['', '   ', 'N/A', 'n.a.', 'none', 'illegible', '-', '?']) {
    const d = await decide(identity({ duplicate_student_exists: true, duplicate_student_task_id: 'web:other' }), { ...REC, student_id: sid });
    assert.strictEqual(d.decision, 'flagged', `student id ${JSON.stringify(sid)}`);
    assert.deepStrictEqual(d.review_reasons, ['student_id_missing'], `a placeholder ID is no duplicate (${JSON.stringify(sid)})`);
    assert.strictEqual(d.student_id, null);
    assert.strictEqual(d.partial_data.student_id, '');
    assert.match(d.review_note, /student ID on the form is blank or could not be read/);
    assert.match(d.review_note, /can also be approved without one/);
    assert.strictEqual(d.identity_check, null);
    assert.strictEqual(d.instructor_id, INSTR);
  }
  // a real ID is kept as read; a real duplicate still flags
  const ok = await decide(identity(), { ...REC, student_id: '2021-0001' });
  assert.deepStrictEqual(ok.review_reasons, []);
  assert.strictEqual(ok.student_id, '2021-0001');
  const dup = await decide(identity({ duplicate_student_exists: true }), { ...REC, student_id: '2021-0001' });
  assert.deepStrictEqual(dup.review_reasons, ['duplicate_student_id']);
  // all three blank -> all three reasons, nothing saved
  const all = await decide(identity(), { ...REC, ocr_instructor: '', ocr_subject: '', student_id: '' },
    formMatch({ i: '', ic: [BATCH_I(0)], s: '', sc: [BATCH_S({ ns: 0 })] }));
  assert.strictEqual(all.decision, 'flagged');
  assert.deepStrictEqual(all.review_reasons, ['instructor_missing', 'subject_missing', 'student_id_missing']);
  // only the unreadable one: a readable batch instructor + unreadable subject
  const subjOnly = await decide(identity(), { ...REC, ocr_subject: '' }, formMatch({ s: '', sc: [BATCH_S({ ns: 0 })] }));
  assert.deepStrictEqual(subjOnly.review_reasons, ['subject_missing']);
  // never suppressed by a suggestion elsewhere: unreadable instructor + another subject found
  const withFound = await decide(identity({ duplicate_student_exists: true }), { ...REC, ocr_instructor: '' },
    formMatch({ i: '', ic: [BATCH_I(0)], s: 'it 102', sc: [scand(IT102, 'IT 102', 'Computer Programming 1', { hit: true }), BATCH_S({ ns: 0.04 })] }));
  assert.deepStrictEqual(withFound.review_reasons, ['subject_mismatch_found', 'instructor_missing'], 'duplicate dropped (target differs), missing kept');
  // the form check could not run: form_match_failed, not *_missing (nothing was checked)
  const unchecked = (await run('Website Decide', { error: { message: 'boom' } }, {
    'Website Prepare Scan': CTX, 'Website Reconcile OCR OMR': { ...REC, ocr_instructor: '' }, 'Website Identity Check': identity(),
  }))[0].json;
  assert.deepStrictEqual(unchecked.review_reasons, ['form_match_failed']);
  // a page with a missing field is never also a spot check
  for (let i = 0; i < 200; i++) {
    const out = (await run('Website Decide', formMatch(), {
      'Website Prepare Scan': { ...CTX, task_id: `spot-check-${i}` }, 'Website Reconcile OCR OMR': { ...REC, student_id: '' }, 'Website Identity Check': identity(),
    }))[0].json;
    assert.deepStrictEqual(out.review_reasons, ['student_id_missing']);
  }
});
test('Reused image: Prepare passes a valid reused_image through (else null); Decide flags reused_image, never rejects or saves it', async () => {
  const OLD = '99999999-9999-4999-8999-999999999999';
  const prep = async (v) => (await run('Website Prepare Scan', { body: { ...body, reused_image: v } }))[0].json.reused_image;
  assert.deepStrictEqual(await prep({ batch_id: OLD.toUpperCase(), status: 'Success', student_id: 'S1', extra: 'x' }), { batch_id: OLD, status: 'success' }, 'only batch_id + status kept');
  for (const st of ['flagged', 'duplicate']) assert.deepStrictEqual(await prep({ batch_id: OLD, status: st }), { batch_id: OLD, status: st });
  for (const v of [undefined, null, '', 'x', [], {}, { batch_id: 'nope', status: 'success' }, { batch_id: OLD, status: 'error' },
    { batch_id: OLD, status: 'processing' }, { batch_id: BATCH, status: 'success' }]) {
    assert.strictEqual(await prep(v), null, JSON.stringify(v));
  }
  assert.strictEqual(CTX.reused_image, null, 'no reused_image -> null (backward compatible)');
  const ctx = { ...CTX, reused_image: { batch_id: OLD, status: 'success' } };
  const d = (await run('Website Decide', formMatch(), {
    'Website Prepare Scan': ctx, 'Website Reconcile OCR OMR': REC, 'Website Identity Check': identity(),
  }))[0].json;
  assert.strictEqual(d.decision, 'flagged');
  assert.deepStrictEqual(d.review_reasons, ['reused_image']);
  assert.ok(d.review_note.includes(`already scanned in another batch (${OLD}, result: success)`), d.review_note);
  assert.strictEqual(d.identity_check, null);
  assert.strictEqual(d.partial_data.instructor, 'Juan Dela Cruz', 'pre-filled with the batch values');
  assert.strictEqual(d.partial_data.student_id, REC.student_id);
  const w = (await run('Website Flagged Record', d, { 'Website Prepare Scan': ctx }))[0].json;
  assert.deepStrictEqual(w.review_reasons, ['reused_image']);
  // combined with other reasons; never a spot check
  const both = (await run('Website Decide', formMatch(), {
    'Website Prepare Scan': ctx, 'Website Reconcile OCR OMR': { ...REC, student_id: '' }, 'Website Identity Check': identity(),
  }))[0].json;
  assert.deepStrictEqual(both.review_reasons, ['student_id_missing', 'reused_image']);
  for (let i = 0; i < 200; i++) {
    const out = (await run('Website Decide', formMatch(), {
      'Website Prepare Scan': { ...ctx, task_id: `spot-check-${i}` }, 'Website Reconcile OCR OMR': REC, 'Website Identity Check': identity(),
    }))[0].json;
    assert.deepStrictEqual(out.review_reasons, ['reused_image']);
  }
});
test('Form check: identity_check is non-null exactly when a *_mismatch_* reason exists (every combination)', async () => {
  const instr = {
    same: formMatch().instructor_candidates, unreadable: null,
    found: [icand(PEDRO, 'Pedro Reyes', 1), BATCH_I(0)], not_found: [BATCH_I(0.1)],
    ambiguous: [icand(MARIA, 'Maria Santos', 0.7), icand(JCARLOS, 'Juan Carlos Santos', 0.7), BATCH_I(0)],
  };
  const subj = {
    same: ['some subject', [BATCH_S()]], unreadable: ['', null],
    found: ['some subject', [scand(IT102, 'IT 102', 'P1', { hit: true }), BATCH_S({ ns: 0 })]],
    not_found: ['some subject', [BATCH_S({ ns: 0.1 })]],
    ambiguous: ['computer programming', [scand(IT102, 'IT 102', 'Computer Programming 1', { ns: 0.8 }),
      scand(IT103, 'IT 103', 'Computer Programming 2', { ns: 0.8 }), BATCH_S({ ns: 0 })]],
  };
  const extra = [['omr_agreement_below_30'], []];
  let n = 0;
  for (const [im, ic] of Object.entries(instr)) {
    for (const [sm, [s, sc]] of Object.entries(subj)) {
      for (const recReasons of extra) {
        const fm = formMatch({ i: ic ? 'some name' : '', ic: ic || [BATCH_I(0)], s, sc: sc || [BATCH_S()] });
        const d = await decide(identity(), { ...REC, review_reasons: recReasons }, fm);
        assert.strictEqual(d.partial_data.instructor_match, im);
        assert.strictEqual(d.partial_data.subject_match, sm);
        assertIdentity(d.identity_check, d.review_reasons);
        const w = (await run('Website Flagged Record', d, { 'Website Prepare Scan': CTX }))[0].json;
        assert.deepStrictEqual(w.identity_check, d.identity_check, 'Flagged Record passes identity_check through');
        // only a clear other match flags (not_found keeps the batch); a blank/unreadable one is held as *_missing
        const mismatch = ['found', 'ambiguous'].includes(im) || ['found', 'ambiguous'].includes(sm);
        const missing = im === 'unreadable' || sm === 'unreadable';
        assert.strictEqual(d.flagged, mismatch || missing || recReasons.length > 0);
        assert.strictEqual(d.review_reasons.includes('instructor_missing'), im === 'unreadable');
        assert.strictEqual(d.review_reasons.includes('subject_missing'), sm === 'unreadable');
        n++;
      }
    }
  }
  assert.strictEqual(n, 50);
  // the old removed identity reasons never come back
  const code = codeOf('Website Decide');
  for (const r of IDENTITY_REASONS) assert.ok(!code.includes(`'${r}'`), `Decide still emits ${r}`);
  for (const r of [...MISMATCH_REASONS, 'form_match_failed', ...MISSING_REASONS, 'reused_image']) assert.ok(code.includes(`'${r}'`), `Decide lacks ${r}`);
});
// ---------------------------------------------------------------- real batch from the live site (2026-09-28)
// Batch Rodz Harvey D Licayan / PC121 Discreet Math, 8 pages of the same instructor/subject; every page
// used to be flagged. Names/subjects = the live database; SQL scores 0 unless given (token scoring only).
const RODZ = 'Rodz Harvey d Licayan';
const LIVE_INSTRUCTORS = ['Adrian Benedict Enoc', 'Albarracin Pio', 'Banjiee Caruana', 'Elviss Preston', 'Evangeline Caruana',
  'hell ayehah', 'Hester Humber', 'instructor ctu', 'Jolene Parton', 'joline Yeah', 'Joseph King', 'mark medello',
  'Michael Thomas Gonzaga', 'Robert King', 'Ronald Kinggg licayan'];
const LIVE_SUBJECTS = [['ADBAS 301', 'Advanced Database Systems'], ['ASP 101', 'asp.net mvc'], ['CAPSTONE 401', 'Capstone Project 1'],
  ['CC 101', 'Introduction to Computing'], ['CSO 310', 'Cybersecurity Operations'], ['DBMGT 201', 'Database Management Systems'],
  ['HCI 302', 'Human Computer Interaction'], ['HELP 301', 'Helping with ethics And living Philippines'],
  ['IAS 301', 'Information Assurance and Security'], ['IDA 220', 'Instructional Design and Assessment'],
  ['ITSEC 415', 'Information Security Management'], ['JELL 291', 'Japaness Language'], ['MOBAPP 330', 'Mobile Application Development'],
  ['NETWORK 202', 'Networking 1'], ['NSF 205', 'Network Security Fundamentals'], ['OS 102', 'Operating Systems'],
  ['PC121', 'Discreet Math'], ['PELEC2', 'Subject name sample'], ['PF 102', 'Computer Programming 1'],
  ['QA 202', 'Quality Assurance and Software Testing'], ['SAD 301', 'Systems Analysis and Design'],
  ['SE 201', 'Software Engineering Principles'], ['SE101', 'Software Engineering'], ['SIA 302', 'Systems Integration and Architecture'],
  ['THESIS 402', 'Undergraduate Thesis Writing'], ['TWRM 210', 'Technical Writing and Research Methods'],
  ['WEBDEV 201', 'Web Systems and Technologies']];
const liveId = (p, k) => `${p}-${String(k + 1).padStart(12, '0')}`;
const liveIc = (sql = {}) => [icand(INSTR, RODZ, sql[RODZ] ?? 0),
  ...LIVE_INSTRUCTORS.map((n, k) => icand(liveId('bbbbbbbb-2222-4222-8222', k), n, sql[n] ?? 0))];
const liveSc = () => LIVE_SUBJECTS.map(([code, name], k) => scand(code === 'PC121' ? SUBJ : liveId('cccccccc-3333-4333-8333', k), code, name));
const liveInstructorId = (name) => liveIc().find((c) => c.name === name).id;
const liveSubjectId = (code) => liveSc().find((c) => c.code === code).id;
const REAL_I = ['Pich Harvey D. Lincayan', 'Kodi Harvey D. Licayan', 'Feda Harvey D. Ulayan', 'Kathe Harvey D. Hicayan',
  'Kobe Harvey D. Vicoyan', 'Proh Harvey D. Llegan', 'Harry D. Viiayan', 'Edg Harvey D. Vicojan'];
const REAL_S = ['Astrc Math', 'PO121', 'PCFP', 'IT1021', 'PC127', 'IT', 'ASP.NET', 'PC121 Discreet Math'];
// what 'Website Form Match' puts in ocr_*_norm
const iNorm = (s) => s.toLowerCase().replace(/[^a-z]+/g, ' ').trim();
const sNorm = (s) => s.toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
// recExtra: extra reconciler fields (e.g. a stale known_instructor, which the website no longer has)
async function realDecide(ocrI, ocrS, { knownS = '', ic = liveIc(), sc = liveSc(), recExtra = {} } = {}) {
  const batchNames = { batch_instructor_name: RODZ, batch_subject_code: 'PC121', batch_subject_name: 'Discreet Math' };
  const rec = { ...REC, ocr_instructor: ocrI, ocr_subject: ocrS, known_subject: knownS, ...recExtra };
  return decide(identity(batchNames), rec, formMatch({ i: iNorm(ocrI), ic, s: sNorm(ocrS), sc, ...batchNames }));
}
test('Real batch (Rodz Harvey D Licayan / PC121): all 8 instructor readings and every subject reading -> clean, batch credited', async () => {
  for (let k = 0; k < REAL_I.length; k++) {
    const d = await realDecide(REAL_I[k], REAL_S[k]);
    assert.strictEqual(d.decision, 'clean', `${REAL_I[k]} / ${REAL_S[k]}: ${d.review_reasons}`);
    assert.deepStrictEqual(d.review_reasons, []);
    assert.strictEqual(d.identity_check, null);
    assert.strictEqual(d.instructor_id, INSTR);
    assert.strictEqual(d.subject_id, SUBJ);
  }
  for (const i of REAL_I) {
    const d = await realDecide(i, 'PC121');
    assert.strictEqual(d.partial_data.instructor_match, 'same', i);
    assert.strictEqual(d.partial_data.suggested_instructor_id, INSTR);
    assert.strictEqual(d.partial_data.instructor_candidates[0].id, INSTR, `${i}: the batch instructor is the best match`);
  }
  const want = { 'Astrc Math': 'same', PO121: 'same', PCFP: 'not_found', IT1021: 'not_found', PC127: 'same', IT: 'not_found',
    'ASP.NET': 'not_found', 'PC121 Discreet Math': 'same' };
  for (const s of REAL_S) {
    const d = await realDecide('Kodi Harvey D. Licayan', s);
    assert.deepStrictEqual(d.review_reasons, [], s);
    assert.strictEqual(d.partial_data.subject_match, want[s], s);
    assert.strictEqual(d.partial_data.suggested_subject_id, SUBJ, s);
  }
  // one letter/digit off the batch code
  assert.strictEqual((await realDecide(RODZ, 'PO121')).partial_data.subject_match_confidence, 0.9);
  assert.strictEqual((await realDecide(RODZ, 'PC121')).partial_data.subject_match_confidence, 1);
});
test('Real batch: a genuinely different instructor/subject on the form is still flagged', async () => {
  for (const [name, sql] of [['Evangeline Caruana', {}], ['Joseph King', {}], ['Michael Thomas Gonzaga', {}],
    // same surname as the batch: the SQL gives the batch 0.60 (surname) and Ronald 0.95 (first + last)
    ['Ronald Licayan', { [RODZ]: 0.6, 'Ronald Kinggg licayan': 0.95 }]]) {
    const d = await realDecide(name, 'PC121 Discreet Math', { ic: liveIc(sql) });
    assert.deepStrictEqual(d.review_reasons, ['instructor_mismatch_found'], name);
    const who = name === 'Ronald Licayan' ? 'Ronald Kinggg licayan' : name;
    assert.strictEqual(d.partial_data.suggested_instructor_id, liveInstructorId(who), name);
    assert.strictEqual(d.identity_check.instructor, 'mismatch');
  }
  // surname only, shared by two instructors (SQL 0.70 each), neither is the batch -> ambiguous, flagged
  const amb = await realDecide('King', 'PC121', { ic: liveIc({ 'Joseph King': 0.7, 'Robert King': 0.7 }) });
  assert.deepStrictEqual(amb.review_reasons, ['instructor_mismatch_ambiguous']);
  assert.strictEqual(amb.partial_data.suggested_instructor_id, INSTR);
  for (const [s, code] of [['ASP 101 asp.net mvc', 'ASP 101'], ['SE 201', 'SE 201'], ['Web Systems and Technologies', 'WEBDEV 201'],
    ['Advanced Database Systems', 'ADBAS 301'], ['CC101 Intro to Computing', 'CC 101']]) {
    const d = await realDecide(RODZ, s);
    assert.deepStrictEqual(d.review_reasons, ['subject_mismatch_found'], s);
    assert.strictEqual(d.partial_data.suggested_subject_id, liveSubjectId(code), s);
    assert.strictEqual(d.partial_data.subject, code);
  }
  // a code that is in no subject never makes a title match a different subject
  const unknown = await realDecide(RODZ, 'IT1021 Web Systems and Technologies');
  assert.deepStrictEqual(unknown.review_reasons, []);
  assert.strictEqual(unknown.partial_data.subject_match, 'not_found');
  // a code one off another subject's code only ever supports the batch
  assert.deepStrictEqual((await realDecide(RODZ, 'SE 202')).review_reasons, []);
});
test('Known-list pick from Gemini (website: subjects only): counts for the batch; for another entry only with support from the raw reading', async () => {
  // weak raw reading, Gemini picked the batch subject; the instructor is matched against the DB only
  let d = await realDecide('Proh Harvey D. Llegan', 'Astrc Math', { knownS: 'PC121' });
  assert.strictEqual(d.partial_data.instructor_match, 'same');
  assert.ok(d.partial_data.instructor_match_confidence < 0.95, 'no Gemini instructor pick boosts the score');
  assert.strictEqual(d.partial_data.subject_match, 'same');
  assert.strictEqual(d.partial_data.subject_match_confidence, 0.95);
  assert.ok(!('known_instructor' in d.partial_data), 'website has no instructor pick');
  assert.strictEqual(d.partial_data.known_subject, 'PC121');
  // a stale known_instructor (e.g. an old reconciler output) is ignored by Decide
  for (const known of [RODZ, 'Joseph King', 'Somebody Else']) {
    const ref = await realDecide('Proh Harvey D. Llegan', 'PC121');
    d = await realDecide('Proh Harvey D. Llegan', 'PC121', { recExtra: { known_instructor: known } });
    assert.deepStrictEqual(d.partial_data.instructor_candidates, ref.partial_data.instructor_candidates, known);
    assert.strictEqual(d.partial_data.instructor_match, 'same', known);
    assert.deepStrictEqual(d.review_reasons, []);
  }
  // another subject that the raw reading supports (ASP.NET ~ asp.net mvc) -> found
  d = await realDecide(RODZ, 'ASP.NET', { knownS: 'ASP 101' });
  assert.deepStrictEqual(d.review_reasons, ['subject_mismatch_found']);
  assert.strictEqual(d.partial_data.suggested_subject_id, liveSubjectId('ASP 101'));
  // the batch code on the form beats a pick of another subject
  d = await realDecide(RODZ, 'PC121 Discreet Math', { knownS: 'ASP 101' });
  assert.strictEqual(d.partial_data.subject_match, 'same');
  assert.deepStrictEqual(d.review_reasons, []);
  // unreadable stays unreadable whatever Gemini picked
  d = await realDecide('', '', { knownS: 'PC121', recExtra: { known_instructor: RODZ } });
  assert.strictEqual(d.partial_data.instructor_match, 'unreadable');
  assert.strictEqual(d.partial_data.subject_match, 'unreadable');
});
test('Phone reconcile: Gemini\'s known pick replaces the raw instructor/subject only when it is on the list and fits', async () => {
  const known = { instructor_names: ['Joseph King', RODZ], subjects: [{ code: 'PC121', name: 'Discreet Math' }, { code: 'ASP 101', name: 'asp.net mvc' }] };
  const phone = async (fields, kn = known) => (await run('reconcile ocr and omr1', {}, {
    'image to ocr and omr python': python(), 'ai validation ocr and omr': geminiResp(fields), 'SAST image from phone': PHONE_HOOK,
    'Phone Known Names': kn,
  }))[0].json;
  let r = await phone({ instructor: 'Kodi Harvey D. Licayan', known_instructor: RODZ, subject: 'PO121', known_subject: 'PC121' });
  assert.strictEqual(r.instructor, RODZ);
  assert.strictEqual(r.subject, 'PC121');
  assert.deepStrictEqual(r.review_reasons, []);
  r = await phone({ instructor: 'Kodi Harvey D. Licayan', known_instructor: 'Joseph King', subject: 'IT1021', known_subject: 'ASP 101' });
  assert.strictEqual(r.instructor, 'Kodi Harvey D. Licayan', 'no support from the raw reading');
  assert.strictEqual(r.subject, 'IT1021');
  r = await phone({ instructor: 'Kodi Harvey D. Licayan', known_instructor: 'Somebody Else', subject: 'Astrc Math', known_subject: 'XX 999' });
  assert.strictEqual(r.instructor, 'Kodi Harvey D. Licayan', 'not on the list');
  assert.strictEqual(r.subject, 'Astrc Math');
  r = await phone({ instructor: 'Kodi Harvey D. Licayan', known_instructor: RODZ }, { error: 'connection refused' });
  assert.strictEqual(r.instructor, 'Kodi Harvey D. Licayan', 'lookup failed -> raw reading');
  r = await phone({ instructor: '', known_instructor: RODZ, subject: '', known_subject: 'PC121' });
  assert.deepStrictEqual([r.instructor, r.subject], ['', ''], 'nothing written -> nothing picked');
  assert.deepStrictEqual(r.review_reasons, ['missing_instructor', 'missing_subject']);
});
test('Main Gemini prompts: known instructors/subjects from Known Names, optional known_* fields, old fields unchanged', async () => {
  const known = { instructor_list: 'Joseph King; Rodz Harvey d Licayan', subject_list: 'PC121 = Discreet Math; SE101 = Software "Eng"' };
  const body = (name, kn, extra = {}) => {
    const nodes = { 'Website Prepare Scan': { image_base64: 'QUJD', mime_type: 'image/jpeg', ...extra }, 'SAST image from phone': { body: { image: 'QUJD' } },
      'Website Known Names': kn, 'Phone Known Names': kn };
    const $ = (ref) => ({ first: () => ({ json: nodes[ref] }) });
    const src = wf.nodes.find((x) => x.name === name).parameters.jsonBody.replace(/^=/, '');
    // eslint-disable-next-line no-new-func
    return JSON.parse(src.replace(/\{\{([\s\S]*?)\}\}/g, (m, js) => String(new Function('$', `return (${js});`)($))));
  };
  // phone: unchanged -- known_instructor + known_subject, instructor list sent, two image parts
  {
    const b = body('ai validation ocr and omr', known);
    const text = b.contents[0].parts[0].text;
    assert.match(text, /6\. student_id: digits only\.\n7\. known_instructor: .*\n8\. known_subject: /);
    assert.ok(text.includes('Known instructors: Joseph King; Rodz Harvey d Licayan'));
    assert.ok(text.includes('Known subjects (code = title): PC121 = Discreet Math; SE101 = Software "Eng"'));
    assert.ok(text.includes('2. subject: the course code and/or subject title, as written.'));
    assert.ok(!text.includes('DDMGT, IT, CSO'), 'no fixed code list in the hint any more');
    assert.strictEqual(b.contents[0].parts.length, 2);
    assert.strictEqual(b.contents[0].parts[1].inlineData.data, 'QUJD');
    const sch = b.generationConfig.responseSchema;
    assert.deepStrictEqual(sch.properties.known_instructor, { type: 'STRING' });
    assert.deepStrictEqual(sch.properties.known_subject, { type: 'STRING' });
    assert.deepStrictEqual(sch.required, ['instructor', 'subject', 'ay_semester', 'remarks', 'date', 'student_id', 'document_check',
      'scan_quality', 'ratings'], 'known_* are optional');
    for (const kn of [{ error: 'timeout' }, {}, undefined]) {
      const t = body('ai validation ocr and omr', kn).contents[0].parts[0].text;
      assert.ok(t.includes('6. student_id: digits only.\n\n--- BUBBLE GRID ---') && !t.includes('known_instructor'));
    }
  }
  // website: subject list only (privacy -- no instructor names sent to Gemini), optional
  // confidence fields, optional third image part for the header crop
  {
    const b = body('Website AI Validation', known);
    const text = b.contents[0].parts[0].text;
    assert.match(text, /6\. student_id: digits only\.\n7\. known_subject: /);
    assert.ok(!text.includes('known_instructor'));
    assert.ok(!text.includes('Known instructors'), 'no instructor name list sent (privacy)');
    assert.ok(text.includes('Known subjects (code = title): PC121 = Discreet Math; SE101 = Software "Eng"'));
    assert.ok(text.includes('2. subject: the course code and/or subject title, as written.'));
    assert.ok(!text.includes('DDMGT, IT, CSO'));
    assert.strictEqual(b.contents[0].parts.length, 2, 'no header image sent -> only the scan image');
    assert.strictEqual(b.contents[0].parts[1].inlineData.data, 'QUJD');
    assert.ok(!text.includes('HEADER CLOSE-UP'), 'no header hint text when no header image was sent');
    const sch = b.generationConfig.responseSchema;
    assert.strictEqual(sch.properties.known_instructor, undefined);
    assert.deepStrictEqual(sch.properties.known_subject, { type: 'STRING' });
    assert.deepStrictEqual(sch.properties.instructor_confidence, { type: 'STRING', enum: ['high', 'medium', 'low'] });
    assert.deepStrictEqual(sch.required, ['instructor', 'subject', 'ay_semester', 'remarks', 'date', 'student_id', 'document_check',
      'scan_quality', 'ratings'], 'known_subject / confidence fields are optional');
    for (const kn of [{ error: 'timeout' }, {}, undefined]) {
      const t = body('Website AI Validation', kn).contents[0].parts[0].text;
      assert.ok(t.includes('6. student_id: digits only.\n\n--- BUBBLE GRID ---') && !t.includes('known_subject'));
    }
    // header image present -> third part + hint text
    const b2 = body('Website AI Validation', known, { header_image_base64: 'SEVBREVS' });
    assert.strictEqual(b2.contents[0].parts.length, 3);
    assert.deepStrictEqual(b2.contents[0].parts[2], { inlineData: { mimeType: 'image/jpeg', data: 'SEVBREVS' } });
    assert.ok(b2.contents[0].parts[0].text.includes('HEADER CLOSE-UP'));
  }
});
test('Form check: a flagged page whose form agrees with the batch never mentions the form\'s instructor/subject', async () => {
  const d = await decide(identity(), { ...REC, ocr_instructor: 'AY/Semester: L U', ocr_subject: 'Mà essor',
    review_reasons: ['ai_validation_failed'], review_note: 'AI validation call failed.' });
  assert.deepStrictEqual(d.review_reasons, ['ai_validation_failed']);
  assert.ok(!/AY\/Semester|Mà essor|unverified|not verified/i.test(d.review_note), d.review_note);
  assert.strictEqual(d.identity_check, null);
});
test('Decide: batch references invalid -> batch_reference_invalid', async () => {
  const d = await decide(identity({ batch_instructor_disabled: true }));
  assert.ok(d.review_reasons.includes('batch_reference_invalid'));
  const d2 = await decide(identity({ batch_subject_exists: false, batch_subject_code: '', batch_subject_name: '' }));
  assert.ok(d2.review_reasons.includes('batch_reference_invalid'));
  const d3 = await decide(identity({ resolved_term_id: null }));
  assert.ok(d3.review_reasons.includes('batch_reference_invalid'));
  const d4 = await decide(identity({ batch_instructor_exists: false, batch_instructor_name: '' }));
  assert.ok(d4.review_reasons.includes('batch_reference_invalid'));
});
test('Decide: placeholder batch instructor is NOT batch_reference_invalid', async () => {
  const d = await decide(identity({ batch_instructor_disabled: false, batch_instructor_is_placeholder: true }));
  assert.strictEqual(d.decision, 'clean');
  // double guard: even if a row said disabled, a placeholder stays valid
  const d2 = await decide(identity({ batch_instructor_disabled: true, batch_instructor_is_placeholder: true }));
  assert.strictEqual(d2.decision, 'clean');
  assert.ok(!d2.review_reasons.includes('batch_reference_invalid'));
});
test('Decide: same student already stored by another page -> duplicate_student_id', async () => {
  const d = await decide(identity({ duplicate_student_exists: true, duplicate_student_task_id: 'web:other' }));
  assert.strictEqual(d.decision, 'flagged');
  assert.ok(d.review_reasons.includes('duplicate_student_id'));
  assert.match(d.review_note, /web:other/);
  assert.strictEqual((await decide(identity({ duplicate_student_exists: false }))).decision, 'clean');
});
test('Decide: batch check query error (Identity Check error output) -> batch_check_failed', async () => {
  const d = await decide({ __branches: [[], [{ error: { message: 'connection refused' } }]] });
  assert.deepStrictEqual(d.review_reasons, ['batch_check_failed']);
  assert.match(d.review_note, /connection refused/);
  assert.strictEqual(d.identity_check, null);
});
test('Decide: OMR reasons from the reconciler are kept', async () => {
  const d = await decide(identity(), { ...REC, review_reasons: ['poor_scan_quality'] });
  assert.strictEqual(d.decision, 'flagged');
  assert.deepStrictEqual(d.review_reasons, ['poor_scan_quality']);
});
test('Spot check: ~1 in 20 otherwise-clean pages get spot_check (deterministic by task_id), pre-filled with the batch, never combined with a real reason', async () => {
  assert.ok(codeOf('Website Decide').includes('SPOT_CHECK_RATE = 0.05'));
  const N = 4000;
  let hits = 0;
  for (let i = 0; i < N; i++) {
    const out = (await run('Website Decide', formMatch(), {
      'Website Prepare Scan': { ...CTX, task_id: `spot-check-${i}` }, 'Website Reconcile OCR OMR': REC, 'Website Identity Check': identity(),
    }))[0].json;
    if (out.review_reasons.includes('spot_check')) {
      hits++;
      assert.deepStrictEqual(out.review_reasons, ['spot_check']);
      assert.strictEqual(out.decision, 'flagged');
      assert.strictEqual(out.identity_check, null);
      assert.strictEqual(out.partial_data.instructor, 'Juan Dela Cruz', 'pre-filled with the batch instructor');
      assert.strictEqual(out.partial_data.subject, 'IT 101', 'pre-filled with the batch subject');
      assert.match(out.review_note, /spot check/i);
    }
  }
  const rate = hits / N;
  assert.ok(rate > 0.03 && rate < 0.08, `spot check rate ${rate} out of the expected ~0.05 range`);
  // the same task_id always gets the same outcome (retries never flip-flop)
  const again = (await run('Website Decide', formMatch(), {
    'Website Prepare Scan': { ...CTX, task_id: 'spot-check-0' }, 'Website Reconcile OCR OMR': REC, 'Website Identity Check': identity(),
  }))[0].json;
  const first = (await run('Website Decide', formMatch(), {
    'Website Prepare Scan': { ...CTX, task_id: 'spot-check-0' }, 'Website Reconcile OCR OMR': REC, 'Website Identity Check': identity(),
  }))[0].json;
  assert.strictEqual(again.review_reasons.includes('spot_check'), first.review_reasons.includes('spot_check'));
  // a page with a real problem never also gets spot_check
  const bad = (await run('Website Decide', formMatch(), {
    'Website Prepare Scan': CTX, 'Website Reconcile OCR OMR': REC, 'Website Identity Check': identity({ batch_instructor_exists: false }),
  }))[0].json;
  assert.ok(!bad.review_reasons.includes('spot_check'));
});

// ---------------------------------------------------------------- flagged path
const REC_DISAGREE = () => ({ ...REC, review_reasons: ['omr_agreement_below_30'], agreement_percent: 25,
  review_note: 'Python and Gemini agree on only 5 of 20 rows (25%); at least 30% is required.' });
test('Flagged Record: passes a flagged decision through (identity_check null, agreement kept)', async () => {
  const d = await decide(identity(), REC_DISAGREE());
  const out = await run('Website Flagged Record', d, { 'Website Prepare Scan': CTX });
  assert.strictEqual(out[0].json.task_id, TASK);
  assert.strictEqual(out[0].json.user_id, STAFF);   // failed_scan_queue.user_id = SAO staff
  assert.strictEqual(out[0].json._source, 'website');
  assert.strictEqual(out[0].json.identity_check, null);
  assert.strictEqual(out[0].json.partial_data.agreement_percent, 25);
  assert.match(out[0].json.partial_data.review_note, /5 of 20 rows \((25%)\)/);
});
test('Flagged Record: raw upsert found another page of the same student (saved=false) -> duplicate_student_id', async () => {
  const clean = await decide(identity());
  const out = await run('Website Flagged Record', { saved: false }, {
    'Website Prepare Scan': CTX, 'Website Decide': clean, 'Website Reconcile OCR OMR': REC, 'Website Identity Check': identity(),
  });
  const r = out[0].json;
  assert.strictEqual(r._source, 'website');
  assert.deepStrictEqual(r.review_reasons, ['duplicate_student_id']);
  assert.match(r.partial_data.summary, /flagged: duplicate_student_id/);
  assert.strictEqual(r.partial_data.instructor, 'Juan Dela Cruz');
  assert.strictEqual(r.partial_data.subject, 'IT 101');
});
test('Flagged Record: reconcile crash -> internal_error_reconcile with ctx ids', async () => {
  const out = await run('Website Flagged Record', { error: 'TypeError' }, {
    'Website Prepare Scan': CTX, 'Website Decide': undefined, 'Website Reconcile OCR OMR': undefined,
  });
  const r = out[0].json;
  assert.ok(r.review_reasons.includes('internal_error_reconcile'));
  assert.strictEqual(r.batch_id, BATCH);
  assert.strictEqual(r.term_id, TERM);
  assert.strictEqual(r.identity_check, null);
  assert.ok(!('identity_check' in r.partial_data));
  // the form was not checked: unchecked + the batch ids as the suggestion (no batch names known)
  for (const k of FORM_PD_KEYS) assert.ok(k in r.partial_data, k);
  assert.strictEqual(r.partial_data.instructor_match, 'unchecked');
  assert.strictEqual(r.partial_data.subject_match, 'unchecked');
  assert.strictEqual(r.partial_data.suggested_instructor_id, INSTR);
  assert.strictEqual(r.partial_data.suggested_subject_id, SUBJ);
  assert.strictEqual(r.partial_data.suggested_instructor_name, null);
  assert.deepStrictEqual(r.partial_data.subject_candidates, []);
});
test('Flagged Record: decide crash -> internal_error_decide, batch name from the batch check row', async () => {
  const out = await run('Website Flagged Record', { error: 'TypeError' }, {
    'Website Prepare Scan': CTX, 'Website Decide': undefined, 'Website Reconcile OCR OMR': REC,
    'Website Identity Check': identity(),
  });
  const r = out[0].json;
  assert.deepStrictEqual(r.review_reasons, ['internal_error_decide']);
  assert.strictEqual(r.partial_data.instructor, 'Juan Dela Cruz');
  assert.strictEqual(r.partial_data.subject, 'IT 101');
  assert.strictEqual(r.partial_data.agreement_percent, 100);
  assert.strictEqual(r.partial_data.instructor_match, 'unchecked');
  assert.strictEqual(r.partial_data.suggested_instructor_name, 'Juan Dela Cruz');
  assert.strictEqual(r.partial_data.suggested_subject_code, 'IT 101');
  assert.strictEqual(r.partial_data.suggested_subject_name, 'Introduction to Computing');
  assert.strictEqual(r.partial_data.batch_subject_name, 'Introduction to Computing');
  assert.strictEqual(r.identity_check, null);
  // Identity Check failed too: batch names from the Form Match row
  const out2 = await run('Website Flagged Record', { error: 'TypeError' }, {
    'Website Prepare Scan': CTX, 'Website Decide': undefined, 'Website Reconcile OCR OMR': REC,
    'Website Identity Check': { __branches: [[], [{ error: 'x' }]] }, 'Website Form Match': formMatch(),
  });
  assert.strictEqual(out2[0].json.partial_data.suggested_instructor_name, 'Juan Dela Cruz');
  assert.strictEqual(out2[0].json.term_id, TERM);
});
test('Flagged Record: saved=false race keeps the form check of the decision', async () => {
  const clean = await decide(identity(), REC, formMatch());
  const r = (await run('Website Flagged Record', { saved: false }, {
    'Website Prepare Scan': CTX, 'Website Decide': clean, 'Website Reconcile OCR OMR': REC, 'Website Identity Check': identity(),
  }))[0].json;
  assert.deepStrictEqual(r.review_reasons, ['duplicate_student_id']);
  assert.strictEqual(r.partial_data.instructor_match, 'same');
  assert.strictEqual(r.identity_check, null);
});
test('Not Saved Response: contract', async () => {
  const r = assertContract(await run('Website Not Saved Response', { error: 'ECONNREFUSED' }, { 'Website Prepare Scan': CTX }), 'flagged');
  assert.ok(r.review_reasons.includes('internal_error_not_saved'));
});

// ---------------------------------------------------------------- shared phone flagged path
const FLAGGED_COLUMNS = ['task_id', 'user_id', 'term_id', 'table_found', 'grid_source', 'partial_data', 'status',
  'review_reasons', 'omr_comparison', 'validation_status', 'scan_image_id', 'batch_id'];
test('build flagged record1: website record -> only real failed_scan_queue columns, batch name/code', async () => {
  const d = await decide(identity(), REC_DISAGREE());
  const wrec = (await run('Website Flagged Record', d, { 'Website Prepare Scan': CTX }))[0].json;
  const rec = (await run('build flagged record1', {}, { 'Website Flagged Record': wrec, 'Save scan error image': [{ id: 'img1' }] }))[0].json;
  assert.deepStrictEqual(Object.keys(rec).filter((k) => !FLAGGED_COLUMNS.includes(k)), []);
  assert.strictEqual(rec.status, 'pending');
  assert.strictEqual(rec.validation_status, 'flagged');
  assert.strictEqual(rec.user_id, STAFF);
  assert.strictEqual(rec.batch_id, BATCH);
  assert.strictEqual(rec.scan_image_id, 'img1');
  assert.strictEqual(rec.partial_data.instructor, 'Juan Dela Cruz');
  assert.strictEqual(rec.partial_data.subject, 'IT 101');
  assert.deepStrictEqual(rec.review_reasons, ['omr_agreement_below_30']);
  assert.strictEqual(rec.partial_data.agreement_percent, 25);
  assert.strictEqual(rec.omr_comparison.agreement_percent, 100, "omr_comparison = the reconciler's omr_agreement object");
  for (const k of FORM_PD_KEYS) assert.ok(k in rec.partial_data, k);
});
test('build flagged record1: a mismatch page stores the suggestion (partial_data.instructor/subject) + reasons', async () => {
  const fm = formMatch({ i: 'pedro reyes', ic: [icand(PEDRO, 'Pedro Reyes', 1), BATCH_I(0)],
    s: 'it 102', sc: [scand(IT102, 'IT 102', 'Computer Programming 1', { hit: true }), BATCH_S({ ns: 0.04 })] });
  const d = await decide(identity(), { ...REC, ocr_instructor: 'Pedro Reyes', ocr_subject: 'IT-102' }, fm);
  const wrec = (await run('Website Flagged Record', d, { 'Website Prepare Scan': CTX }))[0].json;
  const rec = (await run('build flagged record1', {}, { 'Website Flagged Record': wrec, 'Save scan error image': [{ id: 'img1' }] }))[0].json;
  assert.deepStrictEqual(Object.keys(rec).filter((k) => !FLAGGED_COLUMNS.includes(k)), []);
  assert.deepStrictEqual(rec.review_reasons, ['instructor_mismatch_found', 'subject_mismatch_found']);
  assert.strictEqual(rec.partial_data.instructor, 'Pedro Reyes');
  assert.strictEqual(rec.partial_data.subject, 'IT 102');
  assert.strictEqual(rec.partial_data.suggested_instructor_id, PEDRO);
  assert.strictEqual(rec.partial_data.suggested_subject_id, IT102);
  assert.strictEqual(rec.partial_data.batch_instructor_id, INSTR);
  assert.strictEqual(rec.partial_data.batch_subject_id, SUBJ);
  assert.strictEqual(rec.batch_id, BATCH);
});
test('build flagged record1: phone record unchanged (no batch_id, phone partial_data) + agreement_percent', async () => {
  const phone = { task_id: 'p1', user_id: STAFF, instructor: 'X', subject: 'Y', review_reasons: ['a'], omr_agreement: 0.5,
    agreement_percent: 85 };
  const rec = (await run('build flagged record1', {}, { 'reconcile ocr and omr1': phone, 'Save scan error image': [{ id: 'i' }] }))[0].json;
  assert.strictEqual(rec.batch_id, undefined);
  assert.strictEqual(rec.partial_data.instructor, 'X');
  assert.strictEqual(rec.omr_comparison, 0.5);
  assert.strictEqual(rec.partial_data.agreement_percent, 85);
  // the keys the Flutter correction screen reads are all still there
  for (const k of ['instructor', 'subject', 'ay_semester', 'remarks', 'date', 'student_id', 'ratings', 'summary',
    'python_raw_ratings', 'comparisons', 'review_note']) assert.ok(k in rec.partial_data, k);
  const old = (await run('build flagged record1', {}, { 'reconcile ocr and omr1': { task_id: 'p2' }, 'Save scan error image': [{ id: 'i' }] }))[0].json;
  assert.strictEqual(old.partial_data.agreement_percent, null);
});

function respondExpr() {
  const n = wf.nodes.find((x) => x.name === 'Respond to Webhook');
  const m = /^=\{\{([\s\S]*)\}\}$/.exec(n.parameters.responseBody.trim());
  if (!m) throw new Error('Respond to Webhook body is not an expression');
  return (json, nodes) => {
    const $ = (ref) => {
      if (!Object.prototype.hasOwnProperty.call(nodes, ref)) throw new Error(`unexecuted: ${ref}`);
      return { first: () => ({ json: nodes[ref] }) };
    };
    // eslint-disable-next-line no-new-func
    return new Function('$', '$json', `return (${m[1]});`)($, json);
  };
}
test('Respond to Webhook: website -> contract (flagged / internal_error_not_saved); phone -> same text as before', async () => {
  const evalBody = respondExpr();
  const d = await decide(identity(), { ...REC_DISAGREE(), ocr_instructor: 'AY/Semester: L U', ocr_subject: 'Mà essor' });
  const wrec = (await run('Website Flagged Record', d, { 'Website Prepare Scan': CTX }))[0].json;
  const ok = JSON.parse(evalBody({}, { 'Website Flagged Record': wrec }));
  assertContract([{ json: ok }], 'flagged');
  assert.deepStrictEqual(ok.review_reasons, ['omr_agreement_below_30']);
  assert.match(ok.message, /25%/);
  assert.ok(!/AY\/Semester|Mà essor/.test(ok.message), "the form's instructor/subject never appear as a problem");
  assert.ok(!ok.review_reasons.includes('internal_error_not_saved'));
  const failed = JSON.parse(evalBody({ error: 'HTTP 500' }, { 'Website Flagged Record': wrec }));
  assertContract([{ json: failed }], 'flagged');
  assert.ok(failed.review_reasons.includes('internal_error_not_saved'));
  assert.match(failed.message, /NOT SAVED/);
  // phone execution: 'Website Flagged Record' never ran -> the original fixed text
  assert.strictEqual(evalBody({ anything: 1 }, {}), '{{ JSON.stringify($json) }}');
  // mismatch: identity_check passed through, the note names the form's text
  const mm = await decide(identity(), { ...REC, ocr_instructor: 'Pedro Reyes' },
    formMatch({ i: 'pedro reyes', ic: [icand(PEDRO, 'Pedro Reyes', 1), BATCH_I(0)] }));
  const mrec = (await run('Website Flagged Record', mm, { 'Website Prepare Scan': CTX }))[0].json;
  const mr = assertContract([{ json: JSON.parse(evalBody({}, { 'Website Flagged Record': mrec })) }], 'flagged');
  assert.deepStrictEqual(mr.review_reasons, ['instructor_mismatch_found']);
  assert.deepStrictEqual(mr.identity_check, { instructor: 'mismatch', subject: 'match', ocr_instructor: 'Pedro Reyes', ocr_subject: 'IT 1O1' });
  assert.match(mr.message, /Flagged for manual review \(instructor_mismatch_found\)\..*matches Pedro Reyes/);
  const mfail = JSON.parse(evalBody({ error: 'HTTP 500' }, { 'Website Flagged Record': mrec }));
  assert.deepStrictEqual(mfail.review_reasons, ['instructor_mismatch_found', 'internal_error_not_saved']);
  assert.deepStrictEqual(mfail.identity_check, mr.identity_check);
});

// ---------------------------------------------------------------- clean path -> shared main flow
test('Website Map to Target Format: same shape as Map to Target Format3', async () => {
  const d = await decide(identity());
  const out = await run('Website Map to Target Format', { status: 'success' }, { 'Website Decide': d, 'Website Reconcile OCR OMR': REC });
  assert.strictEqual(out.length, 1);
  const r = out[0].json;
  const need = ['instructor_ID', 'true_subject_id', 'current_term_id', 'Student ID', 'Instructor/Professor', 'Subject Taught',
    'AY/Semester', 'Remarks & Suggestions', 'Date', 'task_id'];
  for (let i = 1; i <= 10; i++) need.push(`M${i}`, `P${i}`);
  for (const k of need) assert.ok(Object.prototype.hasOwnProperty.call(r, k), `missing ${k}`);
  // Map to Target Format3 produces exactly these keys too (scan_image, review_reasons, omr_comparisons, review_note extra)
  const phoneKeys = ['instructor_ID', 'true_subject_id', 'current_term_id', 'scan_image', 'review_reasons', 'omr_comparisons',
    'review_note', 'task_id', 'Student ID', 'Instructor/Professor', 'Subject Taught', 'AY/Semester', 'Remarks & Suggestions', 'Date'];
  for (let i = 1; i <= 10; i++) phoneKeys.push(`M${i}`, `P${i}`);
  assert.deepStrictEqual(Object.keys(r).sort(), phoneKeys.sort());
  assert.strictEqual(r.instructor_ID, INSTR);
  assert.strictEqual(r.true_subject_id, SUBJ);
  assert.strictEqual(r.current_term_id, TERM);
  assert.strictEqual(r.task_id, TASK);
  assert.strictEqual(r['Instructor/Professor'], 'Juan Dela Cruz');
  assert.strictEqual(r.M1, 5);
});
test('Code in JavaScript: website staff id + task_id; blank student -> NULL and never deduplicated', async () => {
  const d = await decide(identity());
  const mapped = (await run('Website Map to Target Format', {}, { 'Website Decide': d, 'Website Reconcile OCR OMR': REC }))[0].json;
  const out = await run('Code in JavaScript', null, { 'SAST from Website': { body } }, [mapped]);
  const row = out[0].json;
  assert.strictEqual(row.sao_staff_id, STAFF);
  assert.strictEqual(row.task_id, TASK);
  assert.strictEqual(row.instructor_ID, INSTR);
  assert.strictEqual(row.validation_status, 'auto');
  assert.strictEqual(row.submitted_date, d.submitted_date);
  const blanks = await run('Code in JavaScript', null, { 'SAST from Website': { body } },
    [{ ...mapped, 'Student ID': '   ' }, { ...mapped, 'Student ID': '' }, { ...mapped, 'Student ID': 'S1' }, { ...mapped, 'Student ID': 'S1' }]);
  assert.deepStrictEqual(blanks.map((i) => i.json.student_id), [null, null, 'S1']);
});
test('Code in JavaScript: website row already stored by Website Raw Upsert -> marked, shared upsert skipped', async () => {
  const d = await decide(identity(), { ...REC, student_id: '' });
  const mapped = (await run('Website Map to Target Format', {}, { 'Website Decide': d, 'Website Reconcile OCR OMR': REC }))[0].json;
  const out = await run('Code in JavaScript', null, { 'SAST from Website': { body }, 'Website Raw Upsert': { saved: true } }, [mapped]);
  assert.strictEqual(out.length, 1, 'still one item, so Loop Over Items3 runs and reaches done');
  assert.strictEqual(out[0].json._raw_already_stored, true);
  assert.strictEqual(out[0].json.student_id, null);
  const cond = wf.nodes.find((x) => x.name === 'Raw Row Already Stored?').parameters.conditions.conditions[0].leftValue;
  assert.strictEqual(cond, '={{ $json._raw_already_stored === true }}');
});
test('Code in JavaScript: phone / sheet unchanged (phone staff id, no task_id key for sheet rows)', async () => {
  const sheetRow = { instructor_ID: INSTR, true_subject_id: SUBJ, current_term_id: TERM, 'Student ID': 'A1', M1: 4, P1: 3, Date: 'September 20, 2026' };
  const phone = await run('Code in JavaScript', null, { 'SAST image from phone': { body: { user_id: STAFF } } }, [sheetRow]);
  assert.strictEqual(phone[0].json.sao_staff_id, STAFF);
  assert.ok(!('task_id' in phone[0].json));
  assert.ok(!('_raw_already_stored' in phone[0].json), 'phone/sheet rows go through the shared upsert');
  const corr = await run('Code in JavaScript', null, { 'sast-manual-correction1': { body: { user_id: STAFF } } }, [sheetRow]);
  assert.strictEqual(corr[0].json.validation_status, 'corrected');
});

// ---------------------------------------------------------------- respond after the website raw upsert
test('Website Success Response: contract, batch instructor/subject credited, no name noise (reached only when saved)', async () => {
  const d = await decide(identity(), { ...REC, ocr_instructor: 'AY/Semester: L U', ocr_subject: '' });
  const r = assertContract(await run('Website Success Response', { saved: true }, { 'Website Prepare Scan': CTX, 'Website Decide': d }), 'success');
  assert.match(r.message, /^Saved and credited to Juan Dela Cruz \(IT 101\)\./);
  assert.match(r.message, /agreed on 100% of the rows/);
  assert.ok(!/AY\/Semester|illegible|blank/.test(r.message), r.message);
});
test('Not Saved Response after a failed Website Raw Upsert: flagged + internal_error_not_saved (retryable)', async () => {
  const r = assertContract(await run('Website Not Saved Response', { error: 'deadlock detected' }, { 'Website Prepare Scan': CTX }), 'flagged');
  assert.deepStrictEqual(r.review_reasons, ['internal_error_not_saved']);
  assert.match(r.message, /deadlock/);
});
test('Website Raw Upsert replacement: blank student/remark -> NULL, ratings JSON, same row as Code in JavaScript', async () => {
  const n = wf.nodes.find((x) => x.name === 'Website Raw Upsert');
  const m = /^=\{\{([\s\S]*)\}\}$/.exec(n.parameters.options.queryReplacement.trim());
  const d = await decide(identity(), { ...REC, student_id: '   ', remarks: ' ' });
  const $ = (ref) => ({ first: () => ({ json: { 'Website Decide': d }[ref] }) });
  // eslint-disable-next-line no-new-func
  const v = new Function('$', '$json', `return (${m[1]});`)($, { saved: null });
  assert.strictEqual(v.length, 10);
  assert.deepStrictEqual(v.slice(0, 3), [INSTR, SUBJ, TERM]);
  assert.strictEqual(v[3], null);
  assert.strictEqual(v[4], 'Juan Dela Cruz');
  assert.strictEqual(v[5], null);
  assert.strictEqual(v[7], STAFF);
  assert.strictEqual(v[8], TASK);
  assert.strictEqual(JSON.parse(v[9]).m1, 5);
  // the shared Code in JavaScript builds the same values for this page
  const mapped = (await run('Website Map to Target Format', {}, { 'Website Decide': d, 'Website Reconcile OCR OMR': REC }))[0].json;
  const row = (await run('Code in JavaScript', null, { 'SAST from Website': { body } }, [mapped]))[0].json;
  assert.strictEqual(row.student_id, v[3]);
  assert.strictEqual(row.instructor, v[4]);
  assert.strictEqual(row.Remarks_and_Suggestions, v[5]);
  assert.strictEqual(row.submitted_date, v[6]);
  assert.strictEqual(row.sao_staff_id, v[7]);
  assert.strictEqual(row.task_id, v[8]);
  for (const k of Object.keys(d.ratings)) assert.strictEqual(row[k], JSON.parse(v[9])[k], k);
});

// ---------------------------------------------------------------- main-flow aggregate fix
test('Raw Upsert Done: re-emits every If1 item unchanged (input = loop "done" items)', async () => {
  const rows = [
    { instructor_ID: INSTR, true_subject_id: SUBJ, current_term_id: TERM, M1: 5, P1: 4, 'Student ID': '1' },
    { instructor_ID: INSTR, true_subject_id: SUBJ, current_term_id: TERM, M1: 3, P1: 2, 'Student ID': '2' },
  ];
  const out = await run('Raw Upsert Done', { id: 99, m1: 5 }, { If1: { __items: rows } });
  assert.deepStrictEqual(out.map((i) => i.json), rows);
});

// ---------------------------------------------------------------- migration 013: recompute_sast + blank ratings stay NULL
const SAST_DEAD = ['Management Node1', 'Performance1', 'calculate mangement1', 'calculate performance1', 'Loop Over Items4',
  'Loop Over Items5', 'send to managment table1', 'send to performance node1', 'calc1', 'update to overall total3',
  'update to overall_question_means2', 'update to overall_question_means3'];
function outs(name, idx = 0) {
  const l = (wf.connections[name] || {}).main || [];
  return (l[idx] || []).map((c) => c.node);
}
function ins(name) {
  const r = [];
  for (const [s, c] of Object.entries(wf.connections)) {
    (c.main || []).forEach((l, i) => (l || []).forEach((e) => { if (e.node === name) r.push(`${s}[${i}]`); }));
  }
  return r.sort();
}
test('Aggregates: Raw Upsert Done -> Code in JavaScript8 -> recompute_sast -> If28 (once); old chains gone', async () => {
  const names = new Set(wf.nodes.map((n) => n.name));
  for (const d of SAST_DEAD) assert.ok(!names.has(d), `${d} removed`);
  const txt = JSON.stringify(wf);
  for (const d of SAST_DEAD) assert.ok(!txt.includes(JSON.stringify(d).slice(1, -1)), `${d} not referenced`);
  assert.deepStrictEqual(outs('Raw Upsert Done'), ['Code in JavaScript8']);
  assert.deepStrictEqual(outs('Code in JavaScript8'), ['update to overall total2']);
  assert.deepStrictEqual(outs('update to overall total2'), ['If28']);
  assert.deepStrictEqual(ins('If28'), ['update to overall total2[0]']);
  assert.deepStrictEqual(ins('update to overall total2'), ['Code in JavaScript8[0]']);
  const n = wf.nodes.find((x) => x.name === 'update to overall total2');
  assert.strictEqual(n.parameters.query.trim(), 'SELECT * FROM public.recompute_sast($1::uuid, $2::uuid);');
  assert.strictEqual(n.parameters.options.queryReplacement, '={{ [$json.instructor_id, $json.term_id] }}');
  assert.strictEqual(n.onError, 'continueRegularOutput');
  // unique instructor+term pairs (one recompute and at most one alert each)
  const rows = [
    { instructor_ID: INSTR, current_term_id: TERM }, { instructor_ID: INSTR, current_term_id: TERM },
    { instructor_ID: SUBJ, current_term_id: TERM }, { instructor_ID: null, current_term_id: TERM },
  ];
  const pairs = await run('Code in JavaScript8', null, { If1: { __items: rows } });
  assert.deepStrictEqual(pairs.map((i) => i.json), [{ instructor_id: INSTR, term_id: TERM }, { instructor_id: SUBJ, term_id: TERM }]);
  // If28 reads what recompute_sast returns
  const if28 = JSON.stringify(wf.nodes.find((x) => x.name === 'If28').parameters);
  assert.ok(if28.includes('$json.combined_score_mean'));
});
test('Code in JavaScript: ratings are integers 1..5 or NULL (blank, 0, text, out of range -> NULL)', async () => {
  const row = { instructor_ID: INSTR, true_subject_id: SUBJ, current_term_id: TERM, 'Student ID': 'R1', Date: '2026-10-01' };
  const vals = [5, '4', 0, '', null, 'blank', 6, 3.0, ' 2 ', 4.5];
  const want = [5, 4, null, null, null, null, null, 3, 2, null];
  vals.forEach((v, i) => { row[`M${i + 1}`] = v; row[`P${i + 1}`] = v; });
  const out = await run('Code in JavaScript', null, { 'SAST image from phone': { body: { user_id: STAFF } } }, [row]);
  const j = out[0].json;
  want.forEach((w, i) => { assert.strictEqual(j[`m${i + 1}`], w, `m${i + 1}`); assert.strictEqual(j[`p${i + 1}`], w, `p${i + 1}`); });
  const missing = await run('Code in JavaScript', null, { 'SAST image from phone': { body: { user_id: STAFF } } },
    [{ ...row, M1: undefined, P10: undefined }]);
  assert.strictEqual(missing[0].json.m1, null);
  assert.strictEqual(missing[0].json.p10, null);
});
test('Phone scan: blank rows stay null through 2. Format Final Results1 -> Map to Target Format3; stats over answered rows', async () => {
  const ratings = {};
  for (let i = 1; i <= 10; i++) { ratings[`m${i}`] = 4; ratings[`p${i}`] = 2; }
  ratings.m3 = null; ratings.p7 = null;
  const f = (await run('2. Format Final Results1', null, { 'reconcile ocr and omr1': { ratings } }))[0].json;
  assert.strictEqual(f.m3, null);
  assert.strictEqual(f.p7, null);
  assert.strictEqual(f.m1, 4);
  assert.strictEqual(f.answered, 18);
  assert.strictEqual(f.total_score, 9 * 4 + 9 * 2);
  assert.strictEqual(f.average, 3);
  assert.strictEqual(f.percent, 60);
  const none = (await run('2. Format Final Results1', null, { 'reconcile ocr and omr1': { ratings: {} } }))[0].json;
  assert.strictEqual(none.answered, 0);
  assert.strictEqual(none.average, null);
  assert.strictEqual(none.percent, null);
  assert.strictEqual(none.m1, null);
  const m = (await run('Map to Target Format3', {}, {
    '2. Format Final Results1': { m1: 4 }, 'reconcile ocr and omr1': {}, 'get instructor id6': {}, 'get subject id4': {},
  }))[0].json;
  assert.strictEqual(m.M1, 4);
  assert.strictEqual(m.M2, null);
  assert.strictEqual(m.P10, null);
});
test('Website Map to Target Format: blank rating -> null (not 0)', async () => {
  const d = await decide(identity());
  const out = (await run('Website Map to Target Format', {}, {
    'Website Decide': { ...d, ratings: { ...d.ratings, m2: null, p5: undefined } }, 'Website Reconcile OCR OMR': REC }))[0].json;
  assert.strictEqual(out.M2, null);
  assert.strictEqual(out.P5, null);
  assert.strictEqual(out.M1, 5);
});
test('Corrections: 0 / blank / invalid ratings from Flutter or the website -> null (build the record1, Map target, Map target1)', async () => {
  const b = { term_id: TERM, user_id: STAFF, task_id: null, failed_scan_id: 7 };
  const vals = [5, '4', 0, '', null, 'blank', 6, 3.0, '0', 1];
  const want = [5, 4, null, null, null, null, null, 3, null, 1];
  vals.forEach((v, i) => { b[`m${i + 1}`] = v; b[`p${i + 1}`] = v; });
  const rec = (await run('build the record1', null, {
    'sast-manual-correction1': { body: b }, 'get instructor id7': { instructor_id: INSTR }, 'get subject id6': { subject_id: SUBJ },
  }))[0].json;
  want.forEach((w, i) => assert.strictEqual(rec.raw[`m${i + 1}`], w, `raw.m${i + 1}`));
  want.forEach((w, i) => assert.strictEqual(rec.scores[`p${i + 1}`], w, `scores.p${i + 1}`));
  const mt = (await run('Map target', rec, {
    'sast-manual-correction1': { body: b }, 'get instructor id7': { instructor_id: INSTR }, 'get subject id6': { subject_id: SUBJ },
    'get existing scan image id': {},
  }))[0].json;
  want.forEach((w, i) => {
    assert.strictEqual(mt[`M${i + 1}`], w, `M${i + 1}`);
    assert.strictEqual(mt.raw[`p${i + 1}`], w, `raw.p${i + 1}`);
  });
  const raw = { M1: 0, m2: '3', P1: 'blank', p2: 6, M3: '5' };
  const m1 = (await run('Map target1', null, {
    'import-error-correction1': { body: { raw_data: raw, corrected_subject_id: SUBJ, corrected_instructor_id: INSTR, term_id: TERM } },
    'get subject id5': { subject_id: SUBJ }, 'get instructor id9': { instructor_id: INSTR }, 'get existing scan image id1': {},
  }))[0].json;
  assert.strictEqual(m1.M1, null);
  assert.strictEqual(m1.M2, 3);
  assert.strictEqual(m1.M3, 5);
  assert.strictEqual(m1.P1, null);
  assert.strictEqual(m1.P2, null);
  assert.strictEqual(m1.P10, null);
});

// ---------------------------------------------------------------- corrections -> import_errors keep the scan image
// Evaluates an If node (v2, combinator and) the way n8n does for the operators used here.
function ifTrue(name, $json) {
  const c = wf.nodes.find((x) => x.name === name).parameters.conditions;
  assert.strictEqual(c.combinator, 'and');
  return c.conditions.every((cond) => {
    const m = /^=\{\{([\s\S]*)\}\}$/.exec(cond.leftValue.trim());
    // eslint-disable-next-line no-new-func
    const v = new Function('$json', `return (${m[1]});`)($json);
    const op = `${cond.operator.type}:${cond.operator.operation}`;
    if (op === 'string:exists') return v !== undefined && v !== null;
    if (op === 'boolean:true') return v === true;
    if (op === 'boolean:false') return v === false;
    throw new Error(`${name}: operator ${op} not mocked`);
  });
}
const ORIG_IMG = '50000000-0000-4000-8000-000000000001';
const NEW_IMG = '50000000-0000-4000-8000-000000000002';
async function correctionToImportError(lookupRow, extra = {}) {
  const hook = { body: { failed_scan_id: '60000000-0000-4000-8000-000000000001', task_id: 'phone-task-1',
    instructor: 'Wrong Name', subject: 'Wrong Subject', student_id: 'S-9', remarks: 'ok', m1: 4, p1: 3, ...extra } };
  const nodes = { 'sast-manual-correction1': hook, 'get existing scan image id': lookupRow,
    'get instructor id7': { instructor_id: null, first_name: null, last_name: null, match_score: 0 },
    'get subject id6': { subject_id: null, instructor_id: null } };
  const rec = (await run('build the record1', {}, nodes))[0].json;
  const mapped = (await run('Map target', { ...rec, current_term_id: TERM }, nodes))[0].json;
  const save = ifTrue('is data from google link?', mapped);
  let formatIn = mapped;
  if (save) {
    formatIn = (await run('Restore original fields after image save1', [{ id: NEW_IMG }],
      { 'is data from google link?': mapped }))[0].json;
  }
  const err = (await run('Format error for import_errors', formatIn))[0].json;
  return { mapped, save, err };
}
test('Correction with wrong instructor/subject -> import error points at the ORIGINAL scan image', async () => {
  const { mapped, save, err } = await correctionToImportError({ scan_image_id: ORIG_IMG });
  assert.strictEqual(mapped.existing_scan_image_id, ORIG_IMG);
  assert.strictEqual(save, false, 'no new scan_error_images row is inserted');
  assert.strictEqual(err.scan_image_id, ORIG_IMG);
  assert.strictEqual(err.source, 'scan');
  assert.strictEqual(err.error_type, 'instructor_and_subject_not_found');
});
test('Correction without any stored image -> no EMPTY scan_error_images row, scan_image_id NULL', async () => {
  const { save, err } = await correctionToImportError({ scan_image_id: null });
  assert.strictEqual(save, false, 'Map target carries no scan_image, so nothing is saved');
  assert.strictEqual(err.scan_image_id, null);
});
test('Correction chain: image lookup runs before Map target (webhook -> lookup -> get instructor id7)', async () => {
  const out = (n) => ((wf.connections[n] || {}).main || [[]])[0].map((c) => c.node);
  assert.deepStrictEqual(out('sast-manual-correction1'), ['get existing scan image id']);
  assert.deepStrictEqual(out('get existing scan image id'), ['get instructor id7']);
  assert.deepStrictEqual(out('import-error-correction1'), ['get existing scan image id1']);
  assert.deepStrictEqual(out('get existing scan image id1'), ['get instructor id9']);
  for (const n of ['get instructor id7', 'get instructor id9']) {
    const qr = wf.nodes.find((x) => x.name === n).parameters.options.queryReplacement;
    assert.ok(!qr.includes('$json.body'), `${n} must read the webhook by name`);
  }
});
function exprValue(nodeName, $json, nodes) {
  const n = wf.nodes.find((x) => x.name === nodeName);
  const m = /^=\{\{([\s\S]*)\}\}$/.exec(n.parameters.options.queryReplacement.trim());
  const $ = (ref) => ({ first: () => ({ json: nodes[ref] }) });
  // eslint-disable-next-line no-new-func
  return new Function('$', '$json', `return (${m[1]});`)($, $json);
}
test('Correction lookups: get instructor id7 / get subject id6 get array parameters with the body ids (Flutter payload unchanged)', async () => {
  const flutter = { body: { instructor: 'Juan Dela Cruz', instructor_id: null, subject: 'IT 101', subject_id: null } };
  assert.deepStrictEqual(exprValue('get instructor id7', { scan_image_id: null }, { 'sast-manual-correction1': flutter }),
    ['Juan Dela Cruz', '']);
  assert.deepStrictEqual(exprValue('get subject id6', { instructor_id: INSTR }, { 'sast-manual-correction1': flutter }),
    [INSTR, 'IT 101', '']);
  // Flutter [null] arrays and a missing instructor row never shift the parameters
  const odd = { body: { instructor: [null], subject: 'Math, Science', subject_id: [null] } };
  assert.deepStrictEqual(exprValue('get instructor id7', {}, { 'sast-manual-correction1': odd }), ['', '']);
  assert.deepStrictEqual(exprValue('get subject id6', { instructor_id: null }, { 'sast-manual-correction1': odd }), [null, 'Math, Science', '']);
  assert.deepStrictEqual(exprValue('get subject id6', { error: 'x' }, { 'sast-manual-correction1': odd }), [null, 'Math, Science', '']);
  const web = { body: { instructor: 'Pedro Reyes', instructor_id: PEDRO, subject: 'IT 102', subject_id: IT102 } };
  assert.deepStrictEqual(exprValue('get instructor id7', {}, { 'sast-manual-correction1': web }), ['Pedro Reyes', PEDRO]);
  assert.deepStrictEqual(exprValue('get subject id6', { instructor_id: PEDRO }, { 'sast-manual-correction1': web }), [PEDRO, 'IT 102', IT102]);
});
async function correctionMap(bodyExtra) {
  const hook = { body: { failed_scan_id: 'f1', task_id: 't1', instructor: 'Pedro Reyes', subject: 'IT 102', student_id: 'S',
    remarks: 'ok', term_id: TERM, m1: 5, p1: 4, ...bodyExtra } };
  const nodes = { 'sast-manual-correction1': hook, 'get existing scan image id': { scan_image_id: null },
    'get instructor id7': { instructor_id: PEDRO, first_name: 'Pedro', last_name: 'Reyes', match_score: 1 },
    'get subject id6': { subject_id: IT102, subject_name: 'Computer Programming 1', department_id: 1 } };
  const built = (await run('build the record1', {}, nodes))[0].json;
  const CURRENT = 'dddddddd-4444-4444-8444-999999999999';   // system_settings.current_term_id (GET TERM BY ID6)
  return { CURRENT, mapped: (await run('Map target', { ...built, current_term_id: CURRENT }, nodes))[0].json };
}
test('Correction term: website_review approvals credited to body.term_id; Flutter keeps the current term', async () => {
  let { CURRENT, mapped } = await correctionMap({ correction_source: 'website_review' });
  assert.strictEqual(mapped.current_term_id, TERM);
  assert.strictEqual(mapped.raw.term_id, TERM);
  assert.strictEqual(mapped.instructor_ID, PEDRO);
  assert.strictEqual(mapped.true_subject_id, IT102);
  assert.strictEqual(mapped['Instructor/Professor'], 'Pedro Reyes');
  ({ mapped } = await correctionMap({ correction_source: 'manual_text' }));   // Flutter
  assert.strictEqual(mapped.current_term_id, CURRENT);
  assert.strictEqual(mapped.raw.term_id, CURRENT);
  ({ mapped } = await correctionMap({}));
  assert.strictEqual(mapped.current_term_id, CURRENT);
  ({ mapped } = await correctionMap({ correction_source: 'website_review', term_id: 'not-a-uuid' }));
  assert.strictEqual(mapped.current_term_id, CURRENT, 'invalid term -> current term');
  ({ mapped } = await correctionMap({ correction_source: 'website_review', term_id: [null] }));
  assert.strictEqual(mapped.current_term_id, CURRENT);
  ({ mapped } = await correctionMap({ correction_source: 'website_review', term_id: ` ${TERM.toUpperCase()} ` }));
  assert.strictEqual(mapped.current_term_id, TERM);
});
test('Correction response: Map target -> Mark Queue Validated1 -> Respond to Webhook5 -> After Correction Response -> If19', async () => {
  const out = (n, i = 0) => (((wf.connections[n] || {}).main || [])[i] || []).map((c) => c.node);
  assert.deepStrictEqual(out('Map target'), ['Mark Queue Validated1'], 'Map target no longer feeds If19 directly');
  assert.deepStrictEqual(out('Mark Queue Validated1'), ['Respond to Webhook5']);
  assert.deepStrictEqual(out('Respond to Webhook5'), ['After Correction Response']);
  assert.deepStrictEqual(out('After Correction Response'), ['If19']);
  const { mapped } = await correctionMap({ correction_source: 'website_review' });
  const passed = await run('After Correction Response', { success: true }, { 'Map target': mapped });
  assert.deepStrictEqual(passed.map((i) => i.json), [mapped], 'If19 gets exactly the Map target items');
  const r5 = wf.nodes.find((x) => x.name === 'Respond to Webhook5');
  assert.strictEqual(r5.parameters.respondWith, 'json');
  assert.ok(Number(r5.typeVersion) >= 1.1, 'a Respond node that passes its item on');
});
test('Phone scan with image, instructor missing -> image saved once, import error points at it', async () => {
  const row = { task_id: 'phone-task-2', existing_scan_image_id: undefined, scan_image: 'iVBORw0KGgo=',
    instructor_ID: null, true_subject_id: SUBJ, source: 'scan', 'Student ID': 'S1' };
  assert.strictEqual(ifTrue('is data from google link?', row), true);
  const restored = (await run('Restore original fields after image save1', [{ id: NEW_IMG }],
    { 'is data from google link?': row }))[0].json;
  const err = (await run('Format error for import_errors', restored))[0].json;
  assert.strictEqual(err.scan_image_id, NEW_IMG);
  assert.strictEqual(err.error_type, 'instructor_not_found');
  // website rows set scan_image = null and never create an empty image row
  assert.strictEqual(ifTrue('is data from google link?', { ...row, scan_image: null }), false);
  assert.strictEqual(ifTrue('is data from google link?', { ...row, scan_image: '  ' }), false);
});

// ---------------------------------------------------------------- sexual-word filter (profanity.js)
// All strings below are synthetic test strings, not real student remarks.
const FILTER_RE = /\/\/ sexual-word filter, from [^\n]*\n([\s\S]*?)\/\/ end of sexual-word filter\n/;
function filterFrom(nodeName = 'Code in JavaScript', groups = null) {
  let block = FILTER_RE.exec(codeOf(nodeName))[1];
  if (groups) {
    for (const [g, on] of Object.entries(groups)) block = block.replace(new RegExp(`(\\n  ${g}: )(true|false),`), `$1${on},`);
  }
  // eslint-disable-next-line no-new-func
  return new Function(`${block}\nreturn { findProfanity, maskProfanity, profaneKeyword, PF_PATTERNS, PROFANITY_GROUPS };`)();
}
const PF = filterFrom();
function sqlPatternLines(pf = PF) {
  const tok = pf.PF_PATTERNS.token;
  const parts = [];
  for (let i = 0; i < tok.length; i += 90) parts.push(tok.slice(i, i + 90));
  const token = parts.map((s, i) => `    '${s}'${i < parts.length - 1 ? ' ||' : ''}`).join('\n');
  const phrases = pf.PF_PATTERNS.phrases.map(([a, b], i, all) => `      ('${a}', '${b}')${i < all.length - 1 ? ',' : ''}`).join('\n');
  return { token, phrases };
}
if (ARGS.includes('--print-profanity-patterns')) {
  const { token, phrases } = sqlPatternLines();
  console.log(`-- pg_temp.pf_token_pattern() body:\n${token}\n-- pg_temp.pf_is_phrase() VALUES:\n${phrases}`);
  process.exit(0);
}

const SEXUAL_POSITIVE = [
  // Bisaya / Cebuano (with affixes, repeats, separators, leet)
  'iyot', 'Iyot ka sir', 'giiyot', 'iyotan', 'kaiyot', 'maniyot', 'nag-iyot', 'iyooot', 'i.y.o.t', 'i-y-o-t',
  'i y o t', '1y0t', 'IYOT!!!', '(iyot)', 'sir.iyot', 'bilat', 'b1lat', 'bi1at', 'b!lat', 'bilatan', 'oten', 'otin',
  'utin', 'uten', '0t3n', 'salsal', 'nagsalsal', 'puday', 'tamod', 'bayag',
  // Tagalog
  'kantot', 'kinantot', 'kumantot', 'kantutan', 'nagkakantutan', 'kinakantot', 'k4nt0t', 'kantot-kantot', 'hindot',
  'hindutan', 'jakol', 'nagjajakol', 'tsupa', 'chupa', 'burat', 'titi', 'tite', 'etits', 'puke', 'puki', 'pekpek',
  'kepyas', 'kipay', 'betlog',
  // English
  'pussy', 'cunt', 'boobs', 'tits', 'porn', 'blowjob', 'horny', 'dildo', 'jizz',
  // 'fuck' group (on by default)
  'fuck', 'FUCK', 'f*ck', 'fvck', 'fuk', 'fck', 'phuck', 'fucking', "fuckin'", 'motherfucker', 'fuuuck', 'f u c k',
  'fak', 'pakyu', 'pak yu', 'Pak you', 'pakshet',
];
// Insults, curses and ordinary words that must stay exactly as written.
const MUST_STAY = [
  'idiot', 'kupal', 'gago ka', 'yawa', 'yawa ka', 'piste', 'pisti', 'bwisit', 'animal ka', 'buang', 'bogo', 'tanga',
  'atay', 'atay ka', 'lubot', 'yati', 'hayop', 'leche', 'shit', 'damn', 'bitch', 'asshole', 'stupid', 'bayot',
  // sexual curses group is OFF by default (allowed like other curses)
  'putangina', 'tangina', 'puta', 'pota', 'putang ina mo', 'tang ina', 'whore', 'slut',
  // ordinary Bisaya / Tagalog / English evaluation text and look-alikes
  'libog kaayo ang lesson', 'malibog ko sa discussion', "maayo kaayo mo explain si ma'am", 'dugangi pa unta ang examples',
  'klaro kaayo ang discussion', 'bayad sa project', 'puli-puli', 'okay ra man', 'katiting', 'titig', 'titser namin',
  'Hindi po malinaw', 'pakialam', 'pakyaw', 'tamo', 'salsa', 'putahe', 'inutil', 'sige po', 'cum laude', 'cocky',
  'faking', 'count', 'Putin', 'potent', 'petite', 'sex education', 'sexual harassment', 'Sir Dick', 'Dickens',
  'assessment', 'class', 'passing', 'button', 'therapist', 'cockpit', 'A B C D', 'O VS S F US', 'i am ok', '10/10',
  'grade 1.0', '',
];

test('Filter: sexual words (Bisaya, Tagalog, English, fuck group) -> same-length asterisks', async () => {
  for (const s of SEXUAL_POSITIVE) {
    const m = PF.maskProfanity(s);
    assert.notStrictEqual(m, s, `not masked: ${s}`);
    assert.strictEqual(m.length, s.length, `length changed: ${s}`);
  }
  assert.strictEqual(PF.maskProfanity('iyot'), '****');
  assert.strictEqual(PF.maskProfanity('Iyot ka sir'), '**** ka sir');
  assert.strictEqual(PF.maskProfanity('maayo si sir pero giiyot'), 'maayo si sir pero ******');
  assert.strictEqual(PF.maskProfanity('i y o t'), '* * * *', 'spaces kept');
  assert.strictEqual(PF.maskProfanity('pak yu sir'), '*** ** sir');
  assert.strictEqual(PF.maskProfanity('sir.iyot'), 'sir.****');
  assert.strictEqual(PF.maskProfanity('kantot-kantot'), '******-******');
});
test('Filter: insults, curses and ordinary evaluation text stay unchanged (idiot, kupal, gago ka, yawa, ...)', async () => {
  for (const s of MUST_STAY) assert.strictEqual(PF.maskProfanity(s), s, `false positive: ${s}`);
  const mixed = 'Gago ka sir, yawa! pero ang iyot dili maayo';
  assert.strictEqual(PF.maskProfanity(mixed), 'Gago ka sir, yawa! pero ang **** dili maayo', 'only the sexual word');
});
test('Filter: puta/putangina (sexual_curses) off by default; one toggle masks them; fuck group can be turned off', async () => {
  assert.deepStrictEqual(PF.PROFANITY_GROUPS, { sexual: true, fuck: true, sexual_curses: false });
  const on = filterFrom('Code in JavaScript', { sexual_curses: true });
  assert.strictEqual(on.maskProfanity('putangina mo'), '********* mo');
  assert.strictEqual(on.maskProfanity('putang ina mo'), '****** *** mo');
  assert.strictEqual(on.maskProfanity('gago ka'), 'gago ka', 'insults stay allowed');
  const noFuck = filterFrom('Code in JavaScript', { fuck: false });
  assert.strictEqual(noFuck.maskProfanity('fuck this'), 'fuck this');
  assert.strictEqual(noFuck.maskProfanity('iyot'), '****');
});
test('Filter: idempotent, non-strings untouched, keywords with a sexual or masked word are profane', async () => {
  for (const s of [...SEXUAL_POSITIVE, ...MUST_STAY]) {
    const m = PF.maskProfanity(s);
    assert.strictEqual(PF.maskProfanity(m), m, `not idempotent: ${s}`);
  }
  for (const v of [null, undefined, 5, '']) assert.strictEqual(PF.maskProfanity(v), v);
  assert.strictEqual(PF.profaneKeyword('Teaching clarity'), false);
  assert.strictEqual(PF.profaneKeyword('Kupal attitude'), false, 'insults are not filtered');
  assert.strictEqual(PF.profaneKeyword('iyot'), true);
  assert.strictEqual(PF.profaneKeyword('****'), true, 'a masked word is never a keyword');
  const t0 = Date.now();
  for (let i = 0; i < 200; i++) PF.maskProfanity(`${'i'.repeat(63)}x ${'ka'.repeat(30)} ${'a '.repeat(40)}`);
  assert.ok(Date.now() - t0 < 2000, 'no pathological regex backtracking');
});
test('Filter: every node that masks remarks carries the same copy', async () => {
  const copies = wf.nodes.filter((n) => (n.parameters.jsCode || '').includes('function maskProfanity'))
    .map((n) => [n.name, FILTER_RE.exec(n.parameters.jsCode)[1]]);
  assert.strictEqual(copies.length, 13);
  for (const [name, block] of copies) assert.strictEqual(block, copies[0][1], name);
});

// ---------------------------------------------------------------- remark re-check (scans)
const REMARK = 'Maayo si sir pero iyot ang iyang joke';
function verifyResp(words) {
  return { candidates: [{ content: { parts: [{ text: JSON.stringify({ words }) }] } }] };
}
test('Phone Remark Check: no flagged word -> no re-check; flagged word -> words + prompt + JSON schema', async () => {
  const none = (await run('Phone Remark Check', geminiResp(), { 'ai validation ocr and omr': geminiResp() }))[0].json;
  assert.strictEqual(none.remark_check_needed, false);
  assert.deepStrictEqual(none.words, []);
  assert.strictEqual(none.verify_prompt, null);
  const g = geminiResp({ remarks: REMARK });
  const hit = (await run('Phone Remark Check', g, { 'ai validation ocr and omr': g }))[0].json;
  assert.strictEqual(hit.remark_check_needed, true);
  assert.deepStrictEqual(hit.words, [{ index: 1, word: 'iyot' }]);
  assert.match(hit.verify_prompt, /\[1: iyot\]/);
  assert.deepStrictEqual(hit.verify_generation_config.responseSchema.properties.words.items.required,
    ['index', 'word', 'really_written', 'correct_reading']);
  const cond = wf.nodes.find((x) => x.name === 'Phone Remark Needs Check?').parameters.conditions.conditions[0].leftValue;
  assert.strictEqual(cond, '={{ $json.remark_check_needed === true }}');
});
test('Website Remark Check: flagged word -> re-check; no time left in the backend budget -> skipped (masked)', async () => {
  const g = geminiResp({ remarks: `  ${REMARK}\n` });
  const now = (await run('Website Remark Check', g, { 'Website AI Validation': g,
    'Website Prepare Scan': { ...CTX, received_at_ms: Date.now() } }))[0].json;
  assert.strictEqual(now.remark_check_needed, true);
  assert.deepStrictEqual(now.words, [{ index: 1, word: 'iyot' }]);
  const late = (await run('Website Remark Check', g, { 'Website AI Validation': g,
    'Website Prepare Scan': { ...CTX, received_at_ms: Date.now() - 330000 } }))[0].json;
  assert.strictEqual(late.remark_check_needed, false);
  assert.strictEqual(late.verify_skipped, 'time_budget');
  const clean = (await run('Website Remark Check', geminiResp(), { 'Website AI Validation': geminiResp(),
    'Website Prepare Scan': CTX }))[0].json;
  assert.strictEqual(clean.remark_check_needed, false);
  assert.ok(typeof CTX.received_at_ms === 'number', 'Website Prepare Scan records when the page arrived');
});
test('Verify Remark Words: one Gemini call with the same image + the check prompt, <= 15 s, no retry', async () => {
  const g = geminiResp({ remarks: REMARK });
  const item = (await run('Phone Remark Check', g, { 'ai validation ocr and omr': g }))[0].json;
  for (const [name, nodes, img, mime] of [
    ['Phone Verify Remark Words', { 'SAST image from phone': { body: { image: 'data:image/jpeg;base64,QUJD' } } }, 'QUJD', 'image/jpeg'],
    ['Website Verify Remark Words', { 'Website Prepare Scan': CTX }, IMAGE, 'image/jpeg']]) {
    const n = wf.nodes.find((x) => x.name === name);
    assert.strictEqual(n.onError, 'continueRegularOutput');
    assert.ok(!n.retryOnFail);
    assert.strictEqual(n.parameters.options.timeout, 15000);
    const m = /^=\{\{([\s\S]*)\}\}$/.exec(n.parameters.jsonBody.trim());
    const $ = (ref) => ({ first: () => ({ json: nodes[ref] }) });
    // eslint-disable-next-line no-new-func
    const body = JSON.parse(new Function('$', '$json', `return (${m[1]});`)($, item));
    assert.strictEqual(body.contents[0].parts[0].text, item.verify_prompt);
    assert.deepStrictEqual(body.contents[0].parts[1].inlineData, { mimeType: mime, data: img });
    assert.strictEqual(body.generationConfig.responseMimeType, 'application/json');
  }
});
async function phoneRemark(verify, remark = REMARK) {
  const nodes = { 'image to ocr and omr python': python(), 'ai validation ocr and omr': geminiResp({ remarks: remark }),
    'SAST image from phone': PHONE_HOOK };
  if (verify !== undefined) nodes['Phone Verify Remark Words'] = verify;
  return (await run('reconcile ocr and omr1', {}, nodes))[0].json;
}
test('Re-check (phone): confirmed -> masked; misread -> corrected and NOT masked; call failed -> masked', async () => {
  const confirmed = await phoneRemark(verifyResp([{ index: 1, word: 'iyot', really_written: true, correct_reading: 'iyot' }]));
  assert.strictEqual(confirmed.remarks, 'Maayo si sir pero **** ang iyang joke');
  assert.strictEqual(confirmed.remarks_masked, true);
  assert.deepStrictEqual(confirmed.remark_check, { candidates: 1, verification: 'ok', confirmed: 1, corrected: 0, unverified: 0 });
  const misread = await phoneRemark(verifyResp([{ index: 1, word: 'iyot', really_written: false, correct_reading: 'lipay' }]));
  assert.strictEqual(misread.remarks, 'Maayo si sir pero lipay ang iyang joke');
  assert.strictEqual(misread.remarks_masked, false);
  assert.strictEqual(misread.remark_check.corrected, 1);
  const failed = await phoneRemark({ error: { message: 'timeout of 15000ms exceeded' } });
  assert.strictEqual(failed.remarks, 'Maayo si sir pero **** ang iyang joke');
  assert.strictEqual(failed.remarks_masked, true);
  assert.strictEqual(failed.remark_check.verification, 'failed');
  assert.deepStrictEqual(failed.review_reasons, [], 'masking is never a flag reason');
});
test('Re-check (phone): corrected reading that is itself sexual -> masked; unparsable / not run / wrong word -> masked', async () => {
  const again = await phoneRemark(verifyResp([{ index: 1, word: 'iyot', really_written: false, correct_reading: 'bilat' }]));
  assert.strictEqual(again.remarks, 'Maayo si sir pero ***** ang iyang joke');
  assert.strictEqual(again.remarks_masked, true);
  const garbage = await phoneRemark({ candidates: [{ content: { parts: [{ text: 'sorry' }] } }] });
  assert.strictEqual(garbage.remarks, 'Maayo si sir pero **** ang iyang joke');
  const notRun = await phoneRemark(undefined);
  assert.strictEqual(notRun.remarks, 'Maayo si sir pero **** ang iyang joke');
  assert.strictEqual(notRun.remark_check.verification, 'not_run');
  const other = await phoneRemark(verifyResp([{ index: 1, word: 'something else', really_written: false, correct_reading: 'x' }]));
  assert.strictEqual(other.remarks, 'Maayo si sir pero **** ang iyang joke', 'an answer about another word is ignored');
  const huge = await phoneRemark(verifyResp([{ index: 1, word: 'iyot', really_written: false, correct_reading: 'x'.repeat(200) }]));
  assert.strictEqual(huge.remarks, 'Maayo si sir pero **** ang iyang joke', 'an implausible reading is not used');
  const clean = await phoneRemark(undefined, 'Very good teacher, kupal lang usahay');
  assert.strictEqual(clean.remarks, 'Very good teacher, kupal lang usahay');
  assert.deepStrictEqual(clean.remark_check, { candidates: 0, verification: 'not_needed', confirmed: 0, corrected: 0, unverified: 0 });
});
test('Re-check (website): confirmed / misread / failed; the re-check response never replaces the Gemini reading', async () => {
  const g = geminiResp({ remarks: REMARK });
  const rec = async (input, verify) => (await run('Website Reconcile OCR OMR', input,
    { 'Website Python OMR': python(), 'Website AI Validation': g, ...(verify ? { 'Website Verify Remark Words': verify } : {}) }))[0].json;
  const v1 = verifyResp([{ index: 1, word: 'iyot', really_written: true, correct_reading: 'iyot' }]);
  const confirmed = await rec(v1, v1);
  assert.strictEqual(confirmed.remarks, 'Maayo si sir pero **** ang iyang joke');
  assert.deepStrictEqual(confirmed.review_reasons, []);
  assert.strictEqual(confirmed.agreement_percent, 100, 'ratings still from the main Gemini reading');
  assert.strictEqual(confirmed.ocr_instructor, 'Dr. Juan A. Dela Cruz, MIT');
  const v2 = verifyResp([{ index: 1, word: 'iyot', really_written: false, correct_reading: 'lipay' }]);
  assert.strictEqual((await rec(v2, v2)).remarks, 'Maayo si sir pero lipay ang iyang joke');
  const err = { error: { message: 'The connection was aborted' } };
  const failed = await rec(err, err);
  assert.strictEqual(failed.remarks, 'Maayo si sir pero **** ang iyang joke');
  assert.ok(!failed.review_reasons.includes('ai_validation_failed'), 'a failed re-check is not a failed Gemini call');
  assert.strictEqual(failed.remarks_masked, true);
  const skipped = await rec({ _stage: 'remark_check', remark_check_needed: false, verify_skipped: 'time_budget' });
  assert.strictEqual(skipped.remarks, 'Maayo si sir pero **** ang iyang joke');
  assert.strictEqual(skipped.remark_check.verification, 'not_run');
});
test('Decide / Flagged Record / build flagged record1: masked remark + partial_data.remarks_masked, never unmasked', async () => {
  const d = await decide(identity(), { ...REC, remarks: 'kinantot ang klase', remarks_masked: false });
  assert.strictEqual(d.remark, '******** ang klase', 'Decide masks again (belt and braces)');
  assert.strictEqual(d.remarks_masked, true);
  assert.strictEqual(d.partial_data.remarks_masked, true);
  const clean = await decide(identity());
  assert.ok(!('remarks_masked' in clean.partial_data), 'no key when nothing was masked');
  const flagged = await decide(identity(), { ...REC_DISAGREE(), remarks: '**** ka', remarks_masked: true });
  const wrec = (await run('Website Flagged Record', flagged, { 'Website Prepare Scan': CTX }))[0].json;
  assert.strictEqual(wrec.partial_data.remarks_masked, true);
  // safety net for every failed_scan_queue row: an unmasked remark from any source is masked
  const phone = { task_id: 'p9', remarks: 'ang tite', review_reasons: ['x'] };
  const rec = (await run('build flagged record1', {}, { 'reconcile ocr and omr1': phone, 'Save scan error image': [{ id: 'i' }] }))[0].json;
  assert.strictEqual(rec.partial_data.remarks, 'ang ****');
  assert.strictEqual(rec.partial_data.remarks_masked, true);
  const phoneMasked = { task_id: 'p10', remarks: '**** ka', remarks_masked: true };
  const rec2 = (await run('build flagged record1', {}, { 'reconcile ocr and omr1': phoneMasked, 'Save scan error image': [{ id: 'i' }] }))[0].json;
  assert.strictEqual(rec2.partial_data.remarks_masked, true, 'flag from the phone reconciler kept');
  const website = (await run('build flagged record1', {}, { 'Website Flagged Record': { ...wrec, partial_data: { ...wrec.partial_data, remarks: 'jakol', remarks_masked: undefined } },
    'Save scan error image': [{ id: 'i' }] }))[0].json;
  assert.strictEqual(website.partial_data.remarks, '*****');
});
test('Typed text (sheet import, corrections, import errors): deterministic mask, insults kept', async () => {
  const row = { instructor_ID: INSTR, true_subject_id: SUBJ, current_term_id: TERM, 'Student ID': 'A1', M1: 4,
    'Remarks & Suggestions': 'Gago ka pero fuck', Date: 'September 20, 2026' };
  const raw = (await run('Code in JavaScript', null, { 'SAST image from phone': { body: { user_id: STAFF } } }, [row]))[0].json;
  assert.strictEqual(raw.Remarks_and_Suggestions, 'Gago ka pero ****');
  const sheet = await run('Map to Target Format', {}, {
    'Google LINK from phone1': { body: { term_id: TERM, semester: '1st', academic_year: '2026' } },
    'Get row(s) in sheet1': { __items: [{ 'Student ID': 'S1', 'Remarks & Suggestions': 'Buang ka, bilat', M1: 5 }, { Remarks: 12345 }] },
    'get instructor id5': { __items: [{ instructor_id: INSTR }, {}] }, 'get subject id7': { __items: [{ subject_id: SUBJ }, {}] },
    'GET TERM BY ID1': { __items: [{ current_term_id: TERM }, {}] } });
  assert.strictEqual(sheet[0].json['Remarks & Suggestions'], 'Buang ka, *****');
  assert.strictEqual(sheet[1].json['Remarks & Suggestions'], 12345, 'non-text cells unchanged');
  const hook = { body: { failed_scan_id: 'f1', task_id: 't1', instructor: 'X', subject: 'Y', student_id: 'S', remarks: 'yawa, iyot' } };
  const nodes = { 'sast-manual-correction1': hook, 'get existing scan image id': { scan_image_id: null },
    'get instructor id7': { instructor_id: null }, 'get subject id6': { subject_id: null } };
  const built = (await run('build the record1', {}, nodes))[0].json;
  const mapped = (await run('Map target', { ...built, current_term_id: TERM }, nodes))[0].json;
  assert.strictEqual(mapped['Remarks & Suggestions'], 'yawa, ****');
  assert.strictEqual(mapped.raw.Remarks_and_Suggestions, 'yawa, ****');
  const ie = (await run('Map target1', {}, { 'import-error-correction1': { body: { raw_data: { remarks: 'pekpek' }, term_id: TERM } },
    'get subject id5': { subject_id: SUBJ }, 'get existing scan image id1': { scan_image_id: null } }))[0].json;
  assert.strictEqual(ie['Remarks & Suggestions'], '******');
  const err = (await run('Format error for import_errors', { 'Remarks & Suggestions': 'porn', instructor_ID: null }))[0].json;
  assert.strictEqual(JSON.parse(err.raw_data)['Remarks & Suggestions'], '****');
});
test('Remark AI: profane or masked wordcloud keywords dropped, remark and AI text masked (unified + individual)', async () => {
  const feedback = [{ index: 1, remark: 'Maayo si sir', subject_id: SUBJ }, { index: 2, remark: 'kantot', subject_id: SUBJ }];
  const ai = { evaluation: { interpretation: 'ok', positives: 'clear', improvements: 'less bilat talk', suggestion: 'more examples' },
    overall_tone: 'Positive', remark_tones: [{ index: 1, tone: 'Positive' }, { index: 2, tone: 'Critical' }],
    keywords: [{ word: 'Teaching clarity', new_count: 1 }, { word: 'iyot', new_count: 1 }, { word: '****', new_count: 1 },
      { word: 'Kupal attitude', new_count: 1 }] };
  const out = (await run('Parse Unified AI Result1', { text: JSON.stringify(ai) },
    { 'Prepare Unified AI Input1': { instructor_ID: INSTR, term_id: TERM, feedback } })).map((i) => i.json);
  assert.deepStrictEqual(out.filter((r) => r.record_type === 'wordcloud').map((r) => r.word), ['Teaching clarity', 'Kupal attitude']);
  assert.deepStrictEqual(out.filter((r) => r.record_type === 'student_remark').map((r) => r.remark), ['Maayo si sir', '******']);
  assert.strictEqual(out.find((r) => r.record_type === 'evaluation').improvement_insights, 'less ***** talk');
  const ind = { overall_tone: 'Critical', keywords: [{ word: 'fuck' }, { word: '*****' }], ai_suggestion: 'no iyot jokes',
    student_interpretation: 's', positive_themes: ['ok'], improvement_insights: ['tite'] };
  const out2 = (await run('Add Parse Code node1', { text: JSON.stringify(ind) },
    { 'Prepare Individual AI Input1': { instructor_ID: INSTR, term_id: TERM, subject_id: SUBJ, new_student_remark: 'jakol si sir' } })).map((i) => i.json);
  assert.deepStrictEqual(out2.filter((r) => r.record_type === 'wordcloud').map((r) => r.word), ['Needs improvement'],
    'every keyword dropped -> the existing fallback theme');
  assert.strictEqual(out2.find((r) => r.record_type === 'student_remark').remark, '***** si sir');
  const ev = out2.find((r) => r.record_type === 'evaluation');
  assert.strictEqual(ev.ai_suggestion, 'no **** jokes');
  assert.deepStrictEqual(ev.improvement_insights, ['****']);
  for (const [name, rule] of [['Unified AI Analysis1', 'Prefer concise English theme names.\n\nNever use a vulgar or sexual word'],
    ['Individual AI Analysis1', 'Remove filler words.\n\nNever use a vulgar or sexual word']]) {
    assert.ok(wf.nodes.find((x) => x.name === name).parameters.text.includes(rule), name);
  }
});
test('Main Gemini prompts (phone + website): transcribe remarks uncensored', async () => {
  const phone = wf.nodes.find((x) => x.name === 'ai validation ocr and omr').parameters.jsonBody;
  const web = wf.nodes.find((x) => x.name === 'Website AI Validation').parameters.jsonBody;
  const rule = "don't translate or rewrite. Never censor or mask a word, even a vulgar one: it is filtered later.";
  assert.ok(phone.includes(rule) && web.includes(rule));
  assert.ok(!/replace every|asterisks/i.test(phone), 'Gemini is not asked to mask anything');
});
test('Website Gemini prompt only: optional confidence fields + header crop image part, no instructor name list', async () => {
  const web = wf.nodes.find((x) => x.name === 'Website AI Validation').parameters.jsonBody;
  const phone = wf.nodes.find((x) => x.name === 'ai validation ocr and omr').parameters.jsonBody;
  for (const field of ['instructor_confidence', 'subject_confidence', 'student_id_confidence']) {
    assert.ok(web.includes(`"${field}": { "type": "STRING", "enum": ["high", "medium", "low"] }`), field);
    assert.ok(!phone.includes(field), `phone unaffected: ${field}`);
  }
  assert.ok(web.includes('"known_subject": { "type": "STRING" }'));
  assert.ok(!web.includes('known_instructor'), 'no instructor list/pick sent to Gemini (privacy)');
  assert.ok(!web.includes('Known instructors'), 'no instructor name list in the prompt text');
  assert.ok(phone.includes('known_instructor') && phone.includes('Known instructors'), 'phone branch unchanged');
  assert.ok(web.includes('header_image_base64'), 'optional third image part for the header crop');
  assert.ok(web.includes('HEADER CLOSE-UP'), 'prompt mentions the header close-up when sent');
  assert.ok(!phone.includes('header_image_base64') && !phone.includes('HEADER CLOSE-UP'), 'phone unaffected');
  // required fields never include the optional known_*/confidence fields
  const req = JSON.parse('{' + web.slice(web.indexOf('"required"'), web.indexOf(']', web.indexOf('"required"')) + 1) + '}').required;
  for (const f of ['known_subject', 'instructor_confidence', 'subject_confidence', 'student_id_confidence']) {
    assert.ok(!req.includes(f), f);
  }
});
test('Website: no instructor name or list is sent to Gemini (Known Names SQL, AI Validation body, reconciler, Verify Remark Words)', async () => {
  const NAMES = ['Joseph King', RODZ, 'Evangeline Caruana'];
  // the lookup itself: subjects only, no parameters (it used to pass the batch instructor id)
  const kn = wf.nodes.find((x) => x.name === 'Website Known Names');
  const sql = kn.parameters.query.replace(/--[^\n]*/g, '');
  assert.ok(!/user_info|instructor/i.test(sql), 'Website Known Names does not read or return instructors');
  assert.ok(/\bAS\s+subject_list\b/.test(sql) && /\bAS\s+subjects\b/.test(sql), 'subject list kept');
  assert.strictEqual(kn.parameters.options.queryReplacement, undefined, 'no query parameters');
  const phoneKn = wf.nodes.find((x) => x.name === 'Phone Known Names').parameters.query;
  assert.ok(/AS instructor_list/.test(phoneKn), 'phone lookup unchanged');
  // the built request body: even if the lookup returned instructor names, none reaches Gemini
  const src = wf.nodes.find((x) => x.name === 'Website AI Validation').parameters.jsonBody.replace(/^=/, '');
  for (const known of [
    { subject_list: 'PC121 = Discreet Math', subjects: [{ code: 'PC121', name: 'Discreet Math' }] },
    { instructor_list: NAMES.join('; '), instructor_names: NAMES, subject_list: 'PC121 = Discreet Math' },
    { error: 'timeout' }]) {
    const nodes = { 'Website Prepare Scan': { image_base64: 'QUJD', mime_type: 'image/jpeg', header_image_base64: 'REVG' },
      'Website Known Names': known };
    const $ = (ref) => ({ first: () => ({ json: nodes[ref] }) });
    // eslint-disable-next-line no-new-func
    const text = src.replace(/\{\{([\s\S]*?)\}\}/g, (m, js) => String(new Function('$', `return (${js});`)($)));
    const body = JSON.parse(text);
    for (const name of NAMES) assert.ok(!text.includes(name), `${name} not in the Gemini body`);
    assert.ok(!/known_instructor|Known instructors|instructor_list|instructor_names/.test(text));
    assert.strictEqual(body.generationConfig.responseSchema.properties.known_instructor, undefined);
  }
  // reconciler: a known_instructor Gemini might still return is dropped, known_subject is kept
  const g = geminiResp({ known_instructor: RODZ, known_subject: 'PC121' });
  const rec = (await run('Website Reconcile OCR OMR', g, { 'Website Python OMR': python(), 'Website AI Validation': g }))[0].json;
  assert.ok(!('known_instructor' in rec));
  assert.strictEqual(rec.known_subject, 'PC121');
  // remark re-check: its prompt only carries the flagged remark words
  const rg = geminiResp({ remarks: REMARK, instructor: RODZ });
  const item = (await run('Website Remark Check', rg, { 'Website AI Validation': rg,
    'Website Prepare Scan': { ...CTX, received_at_ms: Date.now() } }))[0].json;
  assert.strictEqual(item.remark_check_needed, true);
  const vm = /^=\{\{([\s\S]*)\}\}$/.exec(wf.nodes.find((x) => x.name === 'Website Verify Remark Words').parameters.jsonBody.trim());
  const vnodes = { 'Website Prepare Scan': CTX };
  // eslint-disable-next-line no-new-func
  const vbody = new Function('$', '$json', `return (${vm[1]});`)((ref) => ({ first: () => ({ json: vnodes[ref] }) }), item);
  for (const name of NAMES) assert.ok(!vbody.includes(name), `${name} not in the re-check body`);
});

test('If28: alerts only for a real combined_score_mean below 3 (NULL/empty/undefined do not)', () => {
  const n = wf.nodes.find((x) => x.name === 'If28');
  const c = n.parameters.conditions.conditions[0];
  assert.deepStrictEqual(c.operator, { type: 'boolean', operation: 'true', singleValue: true });
  const m = /^=\{\{([\s\S]*)\}\}$/.exec(c.leftValue.trim());
  assert.ok(m, 'If28 leftValue is an expression');
  // eslint-disable-next-line no-new-func
  const f = new Function('$json', `return (${m[1]});`);
  const cases = [[null, false], ['', false], [undefined, false], ['2.99', true], [3, false], ['3', false], [4.5, false], [0, true], [1.5, true]];
  for (const [v, want] of cases) {
    assert.strictEqual(f({ combined_score_mean: v }), want, `combined_score_mean=${JSON.stringify(v)}`);
  }
  assert.strictEqual(f({}), false, 'missing field');
});

(async () => {
  let failed = 0;
  for (const t of tests) {
    try {
      await t.fn();
      console.log(`PASS  ${t.name}`);
    } catch (e) {
      failed++;
      console.log(`FAIL  ${t.name}\n      ${e && e.stack ? e.stack.split('\n').slice(0, 3).join('\n      ') : e}`);
    }
  }
  console.log(`\n${tests.length - failed}/${tests.length} tests passed`);
  process.exit(failed ? 1 : 0);
})();
