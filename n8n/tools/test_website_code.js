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
  const fn = new AsyncFunction('$', '$input', 'Buffer', 'console', 'items', '$json', codeOf(name));
  return fn.call({ helpers }, $, $input, Buffer, quiet, list.map((json) => ({ json })), list[0]);
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
  // stamp rule (2026-10-16): the flagged and success answers also carry auto_rejected + stamp_no (additive)
  const stampKeys = 'auto_rejected' in r || 'stamp_no' in r ? ['auto_rejected', 'stamp_no'] : [];
  assert.deepStrictEqual(Object.keys(r).sort(),
    ['flagged', 'identity_check', 'message', 'review_reasons', 'status', 'task_id', ...stampKeys].sort());
  if (stampKeys.length) {
    assert.ok(r.auto_rejected === null || r.auto_rejected === 'duplicate_stamp', `auto_rejected ${r.auto_rejected}`);
    assert.ok(r.stamp_no === null || /^[0-9]{3,12}$/.test(r.stamp_no), `stamp_no ${r.stamp_no}`);
  }
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
// invalid[i] = 'x' | 'shade' | 'circle' | 'dot' (or null): the check-only OMR rejected that row's mark;
// it returns the row exactly like a blank one and names the mark in omr_debug[i].invalid_mark.
// Without invalid the omr_debug entries carry no invalid_mark field at all (an older OMR build).
// The OMR's stamp block (/process, STAMP_FINDER + STAMP_READER, 2026-10-17): reads = the Python reader's
// number per stamp-like candidate ('?' = unsure digit); complete reads go to python_numbers.
function pyStamp(reads = ['0100005'], { like = reads.length, reader = true, sheet = true, extraBoxes = 2 } = {}) {
  const boxes = reads.map((n, i) => ({ id: `T${i + 1}`, tiles: [`T${i + 1}`], python_read: { version: 2, number: n,
    complete: !n.includes('?'), stamp_like: true, n_digits: n.length } }));
  for (let i = 0; i < extraBoxes; i++) {
    boxes.push({ id: `T${boxes.length + 1}`, tiles: [`T${boxes.length + 1}`], python_read: { version: 2, number: '???????',
      complete: false, stamp_like: false, n_digits: 7 } });
  }
  return { version: 2, enabled: true, python_reader: reader, python_reader_version: 2, count: boxes.length, boxes,
    tile_sheet_b64: sheet ? 'A'.repeat(400) : null, dpi: 200,
    ...(reader ? { python_numbers: [...new Set(reads.filter((n) => !n.includes('?')))], python_stamp_like: like, python_read_ms: 420 } : {}),
    ms: 900 };
}
function python({ table = true, override = {}, weak = {}, grid = 'structure-fit', ocr = {}, invalid = {}, stamp } = {}) {
  const rejected = (i) => Object.prototype.hasOwnProperty.call(invalid, i) && invalid[i] !== null;
  const cell = (i) => (override[i] === '' || rejected(i)
    ? { detected: false, rating_name: null, score: 0 }
    : { detected: true, rating_name: override[i] || letters[i % 5] });
  return {
    table_found: table,
    grid_source: grid,
    ratings: {
      management: Array.from({ length: 10 }, (_, i) => cell(i)),
      performance: Array.from({ length: 10 }, (_, i) => cell(i + 10)),
    },
    omr_debug: Array.from({ length: 20 }, (_, i) => ({ row_index: i, scores: weak[i] || { a: 0.9, b: 0.1 },
      ...(Object.prototype.hasOwnProperty.call(invalid, i) ? { invalid_mark: invalid[i] } : {}) })),
    ocr_fields: { instructor: '', subject: '', student_id: '', ...ocr },
    ...(stamp === null ? {} : { stamp_candidates: stamp || pyStamp() }),
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
    stamp_numbers: ['0100005'],   // the numbering-machine stamp (stamp rule): one readable stamp
    stamp_numbers_page: ['0100005'],                          // two readers: full-page read
    stamp_reads_tiles: [{ tile: 'T1', number: '0100005' }],   // two readers: tile-sheet read
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
    // the one allowed read of ocr_fields is the OMR's ink meta (ocr_crosscheck.js), never its OCR text
    const noComments = code.split('\n').map((l) => l.replace(/^\s*\/\/.*$/, '')).join('\n')
      .replace('py?.ocr_fields?.ocr_meta', '');
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
// ---------------------------------------------------------------- check-only rule (omr_debug[].invalid_mark), both reconcilers
const RECONCILERS = [['website', (py, g) => reconcile(py, g)], ['phone', (py, g) => phoneReconcile(py, g)]];
// the Python OMR counts only a check/slash: m3 is an x, which Gemini reads as 'O'
test('Check-only: an x row + a Gemini letter stays blank (source invalid_mark), no conflict, no fill, no too_many_blanks', async () => {
  for (const [which, rec] of RECONCILERS) {
    const r = await rec(python({ invalid: { 2: 'x' } }), geminiWith({ m3: 'O' }));
    const c = r.comparisons.find((x) => x.question === 'm3');
    assert.strictEqual(r.ratings.m3, null, which);
    assert.strictEqual(c.source, 'invalid_mark', which);
    assert.strictEqual(c.invalid_mark, 'x', which);
    assert.strictEqual(c.gemini_raw, 5, `${which}: Gemini's own reading kept for the record`);
    // nothing for a downstream fallback (Flutter _extractScore / backend _one_rating: used -> python -> gemini)
    assert.deepStrictEqual([c.used, c.python, c.gemini], [null, null, null], which);
    assert.strictEqual(c.agree, true, `${which}: not a Python-vs-Gemini conflict`);
    assert.strictEqual(c.resolved_by, null, which);
    assert.deepStrictEqual(r.review_reasons, [], `${which}: one x row alone is not flagged`);
    assert.strictEqual(r.needs_review, false, which);
    assert.strictEqual(r.agreement_percent, 100, which);
    assert.strictEqual(r.omr_agreement.filled_from_gemini, 0, which);
    assert.strictEqual(r.omr_agreement.invalid_mark_rows, 1, which);
    assert.strictEqual(r.review_note,
      'm3: scanner saw an x (not counted). Only a check or slash mark counts, so those answers stay blank.', which);
    assert.match(r.summary, /^19\/20 answered, 1 non-check mark\(s\) not counted, agreement 100% — clean$/, which);
    // the other rows are untouched
    assert.strictEqual(r.ratings.m1, 5, which);
    assert.ok(r.comparisons.filter((x) => x.question !== 'm3').every((x) => x.invalid_mark === null && !('gemini_raw' in x)), which);
  }
});
test('Check-only: invalid_mark missing (older OMR) or null -> old behaviour, Gemini fills a Python blank', async () => {
  for (const [which, rec] of RECONCILERS) {
    for (const py of [python({ override: { 2: '' } }), python({ override: { 2: '' }, invalid: { 2: null } })]) {
      const r = await rec(py, geminiWith({ m3: 'O' }));
      const c = r.comparisons.find((x) => x.question === 'm3');
      assert.strictEqual(r.ratings.m3, 5, which);
      assert.strictEqual(c.source, 'gemini_fill', which);
      assert.strictEqual(c.invalid_mark, null, which);
      assert.strictEqual(r.omr_agreement.filled_from_gemini, 1, which);
      assert.strictEqual(r.omr_agreement.invalid_mark_rows, 0, which);
      assert.strictEqual(r.review_note, null, which);
    }
  }
});
test('Check-only: a row Python read as a rating ignores a stray invalid_mark (Python counted it)', async () => {
  for (const [which, rec] of RECONCILERS) {
    const py = python();
    py.omr_debug[0].invalid_mark = 'dot';
    const r = await rec(py, geminiWith({ m1: 'F' }));
    assert.strictEqual(r.ratings.m1, 5, which);
    assert.strictEqual(r.comparisons[0].source, 'python', which);
    assert.strictEqual(r.comparisons[0].invalid_mark, null, which);
  }
});
test('Check-only: mix of valid, blank and invalid rows -> invalid never counts as a blank for too_many_blanks', async () => {
  for (const [which, rec] of RECONCILERS) {
    // rows 0..5 truly blank (both engines), rows 6..11 x/shade/circle/dot with Gemini letters, rows 12..19 valid
    const marks = ['x', 'shade', 'circle', 'dot', 'x', 'x'];
    const invalid = Object.fromEntries(marks.map((m, k) => [6 + k, m]));
    const gBlank = Object.fromEntries(QS.slice(0, 6).map((q) => [q, 'blank']));
    const gInvalid = Object.fromEntries(QS.slice(6, 12).map((q) => [q, 'VS']));
    const r = await rec(python({ override: blankRows(6), invalid }), geminiWith({ ...gBlank, ...gInvalid }));
    const by = Object.fromEntries(r.comparisons.map((c) => [c.question, c]));
    for (const q of QS.slice(0, 6)) assert.deepStrictEqual([r.ratings[q], by[q].source], [null, 'blank'], `${which} ${q}`);
    for (const q of QS.slice(6, 12)) assert.deepStrictEqual([r.ratings[q], by[q].source], [null, 'invalid_mark'], `${which} ${q}`);
    for (const q of QS.slice(12)) assert.strictEqual(by[q].source, 'python', `${which} ${q}`);
    // 12 empty rows in all, but only 6 real blanks: no too_many_blanks; 6 invalid rows: no review flag
    assert.deepStrictEqual(r.review_reasons, [], which);
    assert.strictEqual(r.needs_review, false, which);
    assert.strictEqual(r.agreement_percent, 100, which);
    assert.strictEqual(r.omr_agreement.filled_from_gemini, 0, which);
    assert.strictEqual(r.omr_agreement.invalid_mark_rows, 6, which);
    assert.match(r.review_note, new RegExp('^m7: scanner saw an x \\(not counted\\); m8: scanner saw a shaded bubble \\(not counted\\); '
      + 'm9: scanner saw a circle \\(not counted\\); m10: scanner saw a dot \\(not counted\\); p1: scanner saw an x \\(not counted\\); '
      + 'p2: scanner saw an x \\(not counted\\)\\. Only a check or slash mark counts'), which);
    // 11 invalid rows (Gemini blank): still no too_many_blanks, no flag
    const all11 = Object.fromEntries(Array.from({ length: 11 }, (_, i) => [i, 'dot']));
    const r11 = await rec(python({ invalid: all11 }), geminiWith(Object.fromEntries(QS.slice(0, 11).map((q) => [q, 'blank']))));
    assert.deepStrictEqual(r11.review_reasons, [], which);
    assert.strictEqual(r11.needs_review, false, which);
  }
});
test('Check-only: invalid marks never flag a review (2 rows and 3 rows -> note only)', async () => {
  for (const [which, rec] of RECONCILERS) {
    const two = await rec(python({ invalid: { 0: 'x', 10: 'shade' } }), geminiWith({ m1: 'S', p1: 'O' }));
    assert.deepStrictEqual(two.review_reasons, [], which);
    assert.strictEqual(two.needs_review, false, which);
    assert.match(two.review_note, /^m1: scanner saw an x \(not counted\); p1: scanner saw a shaded bubble \(not counted\)\./, which);
    const three = await rec(python({ invalid: { 0: 'x', 10: 'shade', 19: 'circle' } }), geminiWith({ m1: 'S', p1: 'O', p10: 'F' }));
    assert.deepStrictEqual(three.review_reasons, [], which);
    assert.strictEqual(three.needs_review, false, which);
    if (which === 'phone') assert.notStrictEqual(three.validation_status, 'flagged');
    assert.match(three.review_note, /p10: scanner saw a circle \(not counted\)\. Only a check or slash mark counts, so those answers stay blank\.$/, which);   // unflagged page: no agreement sentence appended
    // an unknown mark name from a newer OMR is still not counted
    const odd = await rec(python({ invalid: { 4: 'Scribble' } }), geminiWith({ m5: 'O' }));
    assert.strictEqual(odd.ratings.m5, null, which);
    assert.match(odd.review_note, /^m5: scanner saw a "scribble" mark \(not counted\)/, which);
  }
});
test('Check-only: a fully dotted sheet (20 dots) -> all blank, no invalid-mark flag; 0 answered -> discarded (min-answered rule)', async () => {
  for (const [which, rec] of RECONCILERS) {
    const dots = Object.fromEntries(Array.from({ length: 20 }, (_, i) => [i, 'dot']));
    const r = await rec(python({ invalid: dots }), geminiResp());   // Gemini reads every dot as a letter
    assert.ok(Object.values(r.ratings).every((v) => v === null), which);
    // the dots themselves raise nothing; the form has no answer left, so the min-answered rule discards it
    assert.deepStrictEqual(r.review_reasons, ['too_few_answers_0_of_20'], which);
    assert.strictEqual(r.auto_discard, true, which);
    assert.strictEqual(r.agreement_percent, 100, which);
    assert.match(r.summary, /^0\/20 answered, 20 non-check mark\(s\) not counted/, which);
  }
});
test('Check-only: both main Gemini prompts carry the one check-only line (no names, schema unchanged)', () => {
  const LINE = "- Only a check mark or a slash (/) counts as an answer; read an x, a fully shaded bubble, a circle or a dot as 'blank'.";
  for (const name of ['ai validation ocr and omr', 'Website AI Validation']) {
    const body = wf.nodes.find((x) => x.name === name).parameters.jsonBody;
    assert.strictEqual(body.split(LINE).length - 1, 1, name);
    assert.ok(body.includes(`${LINE}\\n- Two bubbles shaded in one row -> 'blank'.`), name);
  }
});
test('Handle Gemini failure (phone): student id no longer taken from Python OCR', async () => {
  const code = codeOf('Handle Gemini failure');
  assert.ok(!code.includes('ocr_fields'), 'must not read py.ocr_fields');
  const out = await run('Handle Gemini failure', { error: { message: 'timeout' } }, {
    'SAST image from phone': PHONE_HOOK, 'image to ocr and omr python': python({ ocr: PY_OCR_GARBAGE }),
  });
  const r = out[0].json;
  assert.strictEqual(r.student_id, '');
  // stamp rule: nothing was read, so the stamp is unreadable too
  assert.deepStrictEqual(r.review_reasons, ['ai_validation_failed', 'stamp_unreadable']);
  assert.strictEqual(r.stamp_status, 'unreadable');
  assert.strictEqual(r.stamp_no, null);
  assert.strictEqual(r.task_id, 'phone-1');
});

// ---------------------------------------------------------------- OCR cross-check (ocr_crosscheck.js, panel #2/#10/#11)
// OMR ocr_meta with the ink score (api.py 306667b9+): ink[f] <= 0.001 = empty, >= 0.008 = written, else unsure
function withInk(py, ink, { located = {}, version = 2, noInk = false } = {}) {
  const fields = ['student_id', 'instructor', 'subject', 'date', 'remarks'];
  const meta = { version, located: Object.fromEntries(fields.map((f) => [f, located[f] ?? true])),
    present: Object.fromEntries(fields.map((f) => [f, (ink[f] ?? 0.05) >= 0.004])), boxes: {}, paper_wh: [1700, 2200] };
  if (!noInk) meta.ink = Object.fromEntries(fields.map((f) => [f, ink[f] ?? 0.05]));
  py.ocr_fields = { ...py.ocr_fields, ocr_meta: meta };
  return py;
}
const OC_REASONS = ['student_id_unverified', 'student_id_missed', 'remarks_missed', 'remarks_unverified', 'white_ink_correction'];
const ocOnly = (r) => r.review_reasons.filter((x) => OC_REASONS.includes(x));
test('OCR cross-check: blank ID box + blank remarks box read blank by the AI -> no reason, no note (both reconcilers)', async () => {
  for (const [which, rec] of RECONCILERS) {
    const r = await rec(withInk(python(), { student_id: 0, remarks: 0 }), geminiResp({ student_id: '', remarks: '' }));
    assert.deepStrictEqual(ocOnly(r), [], which);
    assert.ok(!/student ID box|remarks box|ID box looks empty|Correction fluid/.test(r.review_note || ''), which);
    assert.deepStrictEqual(r.ocr_check, { student_id_ink: 'empty', remarks_ink: 'empty', corrected_rows: [] }, which);
    if (which === 'phone') {
      assert.deepStrictEqual(r.review_reasons, [], 'phone: a blank form header is normal');
      assert.strictEqual(r.needs_review, false);
      assert.strictEqual(r.review_note, null);
    } else {
      assert.deepStrictEqual(r.review_reasons, [], 'website: student_id_missing is added later by Website Decide, unchanged');
    }
    // written boxes read by the AI: also nothing
    const ok = await rec(withInk(python(), { student_id: 0.04, remarks: 0.02 }), geminiResp());
    assert.deepStrictEqual(ok.review_reasons, [], which);
    assert.strictEqual(ok.student_id.replace(/\s+/g, ''), '20210001', which);
  }
});
test('OCR cross-check: empty ID box but the AI read an ID -> ID dropped (blank), student_id_unverified + note; never discarded', async () => {
  for (const [which, rec] of RECONCILERS) {
    const r = await rec(withInk(python(), { student_id: 0.0004 }), geminiResp({ student_id: '2021 0001' }));
    assert.deepStrictEqual(r.review_reasons, ['student_id_unverified'], which);
    assert.strictEqual(r.needs_review, true, which);
    assert.strictEqual(r.auto_discard, false, which);
    assert.strictEqual(r.student_id, '', `${which}: the AI's ID is not stored`);
    assert.strictEqual(r.ocr_check.student_id_ai, '2021 0001', `${which}: kept for the reviewer`);
    assert.ok(r.review_note.includes('The AI read a student ID but the ID box looks empty. Check the sheet.'), which);
    if (which === 'phone') assert.strictEqual(r.validation_status, 'flagged');
  }
});
test('OCR cross-check: written ID box read empty -> student_id_missed on the phone only (website keeps student_id_missing in Decide)', async () => {
  const py = () => withInk(python(), { student_id: 0.03 });
  const phone = await phoneReconcile(py(), geminiResp({ student_id: '' }));
  assert.deepStrictEqual(phone.review_reasons, ['student_id_missed']);
  assert.ok(phone.review_note.includes('The student ID box has writing but the AI read no student ID.'));
  const web = await reconcile(py(), geminiResp({ student_id: '' }));
  assert.deepStrictEqual(web.review_reasons, [], 'no duplicate of student_id_missing');
  const d = await decide(identity(), { ...REC, student_id: web.student_id });
  assert.ok(d.review_reasons.includes('student_id_missing'), 'Decide still flags the blank ID');
});
test('OCR cross-check: remarks -> ink + AI blank = remarks_missed; no ink + AI text = remarks_unverified (text kept)', async () => {
  for (const [which, rec] of RECONCILERS) {
    const missed = await rec(withInk(python(), { remarks: 0.02 }), geminiResp({ remarks: '' }));
    assert.deepStrictEqual(missed.review_reasons, ['remarks_missed'], which);
    assert.ok(missed.review_note.includes('The remarks box has writing but the AI read no remark.'), which);
    // a student's "N/A" is writing the AI read (raw text): not missed
    const na = await rec(withInk(python(), { remarks: 0.02 }), geminiResp({ remarks: 'N/A' }));
    assert.deepStrictEqual(ocOnly(na), [], which);
    const unv = await rec(withInk(python(), { remarks: 0 }), geminiResp({ remarks: 'Very good teacher' }));
    assert.deepStrictEqual(unv.review_reasons, ['remarks_unverified'], which);
    assert.strictEqual(unv.remarks, 'Very good teacher', `${which}: review only, text not changed`);
    assert.ok(unv.review_note.includes('The AI read a remark but the remarks box looks empty. Check the sheet.'), which);
    // a placeholder the AI wrote for an empty box is not a remark (stored as NULL anyway)
    for (const ph of ['N/A', 'none', '-', '  ']) {
      const p = await rec(withInk(python(), { remarks: 0 }), geminiResp({ remarks: ph }));
      assert.deepStrictEqual(ocOnly(p), [], `${which} ${JSON.stringify(ph)}`);
    }
  }
});
test('OCR cross-check: unsure or missing ink data -> nothing (old OMR, no ocr_meta, mid score, not located, no table, AI failed)', async () => {
  const g = geminiResp({ student_id: '2021 0001', remarks: '' });
  for (const [which, rec] of RECONCILERS) {
    const cases = {
      'no ocr_meta (older OMR)': python(),
      'ocr_meta without ink (deployed OMR)': withInk(python(), { student_id: 0, remarks: 0.05 }, { noInk: true }),
      'ink between the thresholds': withInk(python(), { student_id: 0.003, remarks: 0.005 }),
      'box not located': withInk(python(), { student_id: 0, remarks: 0.05 }, { located: { student_id: false, remarks: false } }),
      'ocr_meta version 1': withInk(python(), { student_id: 0, remarks: 0.05 }, { version: 1 }),
      'ink not a number': (() => { const p = withInk(python(), {}); p.ocr_fields.ocr_meta.ink = { student_id: '0', remarks: null }; return p; })(),
      'ocr_meta null': (() => { const p = python(); p.ocr_fields.ocr_meta = null; return p; })(),
    };
    for (const [label, py] of Object.entries(cases)) {
      const r = await rec(py, g);
      assert.deepStrictEqual(ocOnly(r), [], `${which}: ${label}`);
      assert.strictEqual(r.student_id.replace(/\s+/g, ''), '20210001', `${which}: ${label}`);
    }
    // no table: ratings untrusted anyway; the ink boxes are not trusted either
    const nt = await rec(withInk(python({ table: false }), { student_id: 0, remarks: 0.05 }), g);
    assert.ok(!nt.review_reasons.some((x) => OC_REASONS.includes(x)), `${which}: table not found`);
    // Gemini failed: its blank text is not an AI read
    const bad = { candidates: [{ content: { parts: [{ text: 'not json' }] } }] };
    const gf = await rec(withInk(python(), { student_id: 0.05, remarks: 0.05 }), bad);
    assert.deepStrictEqual(ocOnly(gf), [], `${which}: Gemini unparseable`);
    // not a SAST form: no cross-check noise
    const nf = await rec(withInk(python(), { student_id: 0, remarks: 0 }),
      geminiResp({ student_id: '123', remarks: 'hello', document_check: { is_sast_form: false, reason: 'a receipt' } }));
    assert.deepStrictEqual(ocOnly(nf), [], `${which}: not a SAST form`);
  }
});
test('White ink: corrected_rows -> white_ink_correction + note naming the rows; ratings unchanged; junk ignored', async () => {
  for (const [which, rec] of RECONCILERS) {
    const r = await rec(python(), geminiResp({ corrected_rows: ['p7', 'M3', 'x9', 'm3', 'p10', 7, null] }));
    assert.deepStrictEqual(r.review_reasons, ['white_ink_correction'], which);
    assert.deepStrictEqual(r.ocr_check.corrected_rows, ['m3', 'p7', 'p10'], which);
    assert.ok(r.review_note.includes('Correction fluid/tape or a crossed-out mark was seen on m3, p7, p10. '
      + 'Check those rows on the sheet: only the remaining uncovered check counts.'), which);
    const plain = await rec(python(), geminiResp());
    assert.deepStrictEqual(r.ratings, plain.ratings, `${which}: no automatic score change`);
    assert.strictEqual(r.auto_discard, false, which);
    for (const cr of [[], undefined, 'm3', ['', 'q1', 'm11']]) {
      const n = await rec(python(), geminiResp({ corrected_rows: cr }));
      assert.deepStrictEqual(ocOnly(n), [], `${which} ${JSON.stringify(cr)}`);
    }
  }
});
test('Double mark (OMR invalid_mark "double"): row blank, never filled from Gemini, "two marks" note, no flag', async () => {
  for (const [which, rec] of RECONCILERS) {
    const r = await rec(python({ invalid: { 4: 'double' } }), geminiWith({ m5: 'O' }));
    const c = r.comparisons.find((x) => x.question === 'm5');
    assert.strictEqual(r.ratings.m5, null, which);
    assert.strictEqual(c.source, 'invalid_mark', which);
    assert.strictEqual(c.invalid_mark, 'double', which);
    assert.deepStrictEqual(r.review_reasons, [], which);
    assert.strictEqual(r.review_note, 'm5: scanner saw two marks (not counted). Only a check or slash mark counts, '
      + 'so those answers stay blank. A row with two marks has no single answer.', which);
  }
});
test('OCR cross-check: both Gemini prompts carry the white-ink line once (after the two-bubbles rule) and a required corrected_rows; no names', () => {
  const LINE = '- A mark covered by correction fluid or tape (white-out), or heavily crossed out, is erased and is not an answer: '
    + 'read only the remaining uncovered check, and list that row (m1-m10, p1-p10) in corrected_rows ([] if none).';
  for (const name of ['ai validation ocr and omr', 'Website AI Validation']) {
    const body = wf.nodes.find((x) => x.name === name).parameters.jsonBody;
    assert.strictEqual(body.split(LINE).length - 1, 1, name);
    assert.ok(body.includes(`- Two bubbles shaded in one row -> 'blank'.\\n${LINE}\\n- A faint mark`), name);
    assert.ok(body.includes('"corrected_rows": { "type": "ARRAY", "items": { "type": "STRING" } },'), name);
    assert.ok(!/Known instructors|known_instructor/.test(body), `${name}: no instructor names`);
  }
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
test('Phone reconcile: Gemini\'s known SUBJECT pick replaces the raw subject only when it is on the list and fits', async () => {
  const known = { subjects: [{ code: 'PC121', name: 'Discreet Math' }, { code: 'ASP 101', name: 'asp.net mvc' }] };
  const phone = async (fields, kn = known) => (await run('reconcile ocr and omr1', {}, {
    'image to ocr and omr python': python(), 'ai validation ocr and omr': geminiResp(fields), 'SAST image from phone': PHONE_HOOK,
    'Phone Known Names': kn,
  }))[0].json;
  let r = await phone({ subject: 'PO121', known_subject: 'PC121' });
  assert.strictEqual(r.subject, 'PC121');
  r = await phone({ subject: 'IT1021', known_subject: 'ASP 101' });
  assert.strictEqual(r.subject, 'IT1021', 'no support from the raw reading');
  r = await phone({ subject: 'Astrc Math', known_subject: 'XX 999' });
  assert.strictEqual(r.subject, 'Astrc Math', 'not on the list');
  r = await phone({ subject: 'PO121', known_subject: 'PC121' }, { error: 'connection refused' });
  assert.strictEqual(r.subject, 'PO121', 'lookup failed -> raw reading');
  r = await phone({ instructor: '', subject: '', known_subject: 'PC121' });
  assert.deepStrictEqual([r.instructor, r.subject], ['', ''], 'nothing written -> nothing picked');
  assert.deepStrictEqual(r.review_reasons, ['missing_instructor', 'missing_subject']);
});
test('Phone reconcile: the instructor is matched locally against Phone Instructor Candidates (no Gemini pick)', async () => {
  const cands = { candidates_version: 1, instructor_names: ['Joseph King', RODZ, 'Evangeline Caruana'] };
  const phone = async (fields, cn = cands) => {
    const nodes = { 'image to ocr and omr python': python(), 'ai validation ocr and omr': geminiResp(fields),
      'SAST image from phone': PHONE_HOOK, 'Phone Known Names': { subjects: [] } };
    if (cn !== null) nodes['Phone Instructor Candidates'] = cn;
    return (await run('reconcile ocr and omr1', {}, nodes))[0].json;
  };
  // messy reading, one clear database match -> the exact database name (found by 'get instructor id6')
  let r = await phone({ instructor: 'Kodi Harvey D. Licayan' });
  assert.strictEqual(r.instructor, RODZ);
  assert.strictEqual(r.instructor_match.match, 'found');
  assert.strictEqual(r.instructor_match.raw, 'Kodi Harvey D. Licayan');
  assert.deepStrictEqual(r.review_reasons, []);
  r = await phone({ instructor: 'Prof. Joseph King, MIT' });
  assert.strictEqual(r.instructor, 'Joseph King', 'titles and degrees ignored');
  // a known_instructor Gemini might still return is ignored
  r = await phone({ instructor: 'Kodi Harvey D. Licayan', known_instructor: 'Joseph King' });
  assert.strictEqual(r.instructor, RODZ);
  // no database entry close enough -> the raw reading goes on to 'get instructor id6' as before
  r = await phone({ instructor: 'Licayan' });
  assert.strictEqual(r.instructor, 'Licayan');
  assert.strictEqual(r.instructor_match.match, 'none');
  assert.deepStrictEqual(r.review_reasons, [], 'not found here is decided downstream (import error), not flagged');
  r = await phone({ instructor: 'Somebody Else' });
  assert.strictEqual(r.instructor, 'Somebody Else');
  // two people fit equally (same name) -> flagged for review, never a guess
  r = await phone({ instructor: 'Joseph King' }, { candidates_version: 1, instructor_names: ['Joseph King', 'Joseph King', RODZ] });
  assert.strictEqual(r.instructor_match.match, 'ambiguous');
  assert.strictEqual(r.instructor, 'Joseph King', 'raw reading kept');
  assert.ok(r.review_reasons.includes('instructor_ambiguous'));
  assert.strictEqual(r.needs_review, true);
  assert.ok(/fits more than one instructor/.test(r.review_note));
  // lookup failed / not executed -> the raw reading, as before
  for (const cn of [{ error: 'connection refused' }, null]) {
    r = await phone({ instructor: 'Kodi Harvey D. Licayan' }, cn);
    assert.strictEqual(r.instructor, 'Kodi Harvey D. Licayan');
    assert.strictEqual(r.instructor_match.match, 'unavailable');
    assert.deepStrictEqual(r.review_reasons, []);
  }
  // nothing written -> missing_instructor, as before
  r = await phone({ instructor: '' });
  assert.ok(r.review_reasons.includes('missing_instructor'));
  assert.strictEqual(r.instructor_match.match, 'unreadable');
});
test('Main Gemini prompts: known subjects only from Known Names, optional known_subject, old fields unchanged', async () => {
  const known = { instructor_list: 'Joseph King; Rodz Harvey d Licayan', subject_list: 'PC121 = Discreet Math; SE101 = Software "Eng"' };
  const body = (name, kn, extra = {}) => {
    const nodes = { 'Website Prepare Scan': { image_base64: 'QUJD', mime_type: 'image/jpeg', ...extra }, 'SAST image from phone': { body: { image: 'QUJD' } },
      'Website Known Names': kn, 'Phone Known Names': kn };
    const $ = (ref) => ({ first: () => ({ json: nodes[ref] }) });
    const src = wf.nodes.find((x) => x.name === name).parameters.jsonBody.replace(/^=/, '');
    // eslint-disable-next-line no-new-func
    return JSON.parse(src.replace(/\{\{([\s\S]*?)\}\}/g, (m, js) => String(new Function('$', `return (${js});`)($))));
  };
  // phone: subject list only (privacy -- no instructor names sent to Gemini), two image parts
  {
    const b = body('ai validation ocr and omr', known);
    const text = b.contents[0].parts[0].text;
    assert.match(text, /6\. student_id: digits only\.\n7\. known_subject: /);
    assert.ok(!text.includes('known_instructor'));
    assert.ok(!text.includes('Known instructors') && !text.includes('Joseph King'), 'no instructor name list sent (privacy)');
    assert.ok(text.includes('Known subjects (code = title): PC121 = Discreet Math; SE101 = Software "Eng"'));
    assert.ok(text.includes('2. subject: the course code and/or subject title, as written.'));
    assert.ok(!text.includes('DDMGT, IT, CSO'), 'no fixed code list in the hint any more');
    assert.strictEqual(b.contents[0].parts.length, 2);
    assert.strictEqual(b.contents[0].parts[1].inlineData.data, 'QUJD');
    const sch = b.generationConfig.responseSchema;
    assert.strictEqual(sch.properties.known_instructor, undefined);
    assert.deepStrictEqual(sch.properties.known_subject, { type: 'STRING' });
    assert.deepStrictEqual(sch.required, ['instructor', 'subject', 'ay_semester', 'remarks', 'date', 'student_id', 'stamp_numbers',
      'stamp_numbers_page', 'stamp_reads_tiles', 'document_check', 'scan_quality', 'corrected_rows', 'ratings'], 'known_subject is optional; stamp_numbers and corrected_rows are required');
    assert.deepStrictEqual(sch.properties.stamp_numbers, { type: 'ARRAY', items: { type: 'STRING' } });
    for (const kn of [{ error: 'timeout' }, {}, undefined]) {
      const t = body('ai validation ocr and omr', kn).contents[0].parts[0].text;
      assert.ok(t.includes('6. student_id: digits only.\n\n--- STAMP ---\nstamp_numbers: ') && !t.includes('known_subject'));
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
    assert.deepStrictEqual(sch.required, ['instructor', 'subject', 'ay_semester', 'remarks', 'date', 'student_id', 'stamp_numbers',
      'stamp_numbers_page', 'stamp_reads_tiles', 'document_check', 'scan_quality', 'corrected_rows', 'ratings'], 'known_subject / confidence fields are optional; stamp_numbers and corrected_rows are required');
    assert.deepStrictEqual(sch.properties.stamp_numbers, { type: 'ARRAY', items: { type: 'STRING' } });
    for (const kn of [{ error: 'timeout' }, {}, undefined]) {
      const t = body('Website AI Validation', kn).contents[0].parts[0].text;
      assert.ok(t.includes('6. student_id: digits only.\n\n--- STAMP ---\nstamp_numbers: ') && !t.includes('known_subject'));
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
  'review_reasons', 'omr_comparison', 'validation_status', 'scan_image_id', 'batch_id',
  'stamp_no', 'stamp_source'];   // migration 015
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

// ---------------------------------------------------------------- min-answered rule (teacher, 2026-10-08)
// fewer than 5 of the 20 items answered -> the form is discarded (stored in the queue as 'discarded')
const geminiBlank = (qs) => Object.fromEntries(qs.map((q) => [q, 'blank']));
// Python reads rows 0..n-1 blank and Gemini agrees: 20 - n answered rows
const answeredOnly = (n, rec) => rec(python({ override: blankRows(20 - n) }), geminiWith(geminiBlank(QS.slice(0, 20 - n))));
const DISCARD_COLUMNS = [...FLAGGED_COLUMNS, 'review_action', 'review_correction'];
function fcmParams() {
  const qr = wf.nodes.find((x) => x.name === 'Get staff FCM for flagged scan').parameters.options.queryReplacement;
  const m = /^=\{\{([\s\S]*)\}\}$/.exec(qr.trim());
  if (!m) throw new Error('FCM queryReplacement is not an expression');
  return (record) => new Function('$', `return (${m[1]});`)(() => ({ first: () => ({ json: record }) }));
}
test('Min answered: 4 answered + 16 blank on a reliable scan -> auto_discard, too_few_answers_4_of_20 (no too_many_blanks)', async () => {
  for (const [which, rec] of RECONCILERS) {
    const r = await answeredOnly(4, rec);
    assert.strictEqual(Object.values(r.ratings).filter((v) => v !== null).length, 4, which);
    assert.deepStrictEqual(r.review_reasons, ['too_few_answers_4_of_20'], which);
    assert.strictEqual(r.needs_review, true, which);
    assert.strictEqual(r.auto_discard, true, which);
    assert.strictEqual(r.answered_count, 4, which);
    assert.strictEqual(r.omr_agreement.answered_count, 4, which);
    assert.match(r.review_note, /^Only 4 of the 20 items were answered \(at least 5 are needed\), so this form was discarded automatically and is not counted\./, which);
    assert.match(r.summary, /^4\/20 answered, agreement 100% — discarded: too_few_answers_4_of_20$/, which);
  }
});
test('Min answered: exactly 5 answered -> counted as before (too_many_blanks_15_of_20 review, not discarded)', async () => {
  for (const [which, rec] of RECONCILERS) {
    const r = await answeredOnly(5, rec);
    assert.deepStrictEqual(r.review_reasons, ['too_many_blanks_15_of_20'], which);
    assert.strictEqual(r.auto_discard, false, which);
    assert.strictEqual(r.answered_count, 5, which);
    assert.ok(!/discarded/.test(r.review_note || ''), which);
    // 10 answered: a clean page, nothing changes
    const ten = await answeredOnly(10, rec);
    assert.deepStrictEqual(ten.review_reasons, [], which);
    assert.strictEqual(ten.auto_discard, false, which);
  }
});
test('Min answered: Gemini-filled rows count (union of both readers); without them the form is discarded', async () => {
  for (const [which, rec] of RECONCILERS) {
    // Python reads only p9, p10; Gemini also reads p6..p8 (gemini_fill) -> 5 answered
    const filled = await rec(python({ override: blankRows(18) }), geminiWith(geminiBlank(QS.slice(0, 15))));
    assert.strictEqual(filled.omr_agreement.filled_from_gemini, 3, which);
    assert.strictEqual(filled.answered_count, 5, which);
    assert.strictEqual(filled.auto_discard, false, which);
    assert.ok(!filled.review_reasons.some((x) => x.startsWith('too_few_answers_')), which);
    // Gemini reads those rows blank too -> 2 answered -> discarded
    const two = await rec(python({ override: blankRows(18) }), geminiWith(geminiBlank(QS.slice(0, 18))));
    assert.strictEqual(two.answered_count, 2, which);
    assert.strictEqual(two.auto_discard, true, which);
    assert.deepStrictEqual(two.review_reasons, ['too_few_answers_2_of_20'], which);
    // a disagreement on an answered row changes the rating, never the count
    const differ = await rec(python({ override: blankRows(16) }), geminiWith({ ...geminiBlank(QS.slice(0, 16)), p7: 'O', p8: 'US' }));
    assert.strictEqual(differ.answered_count, 4, which);
    assert.strictEqual(differ.auto_discard, true, which);
  }
});
test('Min answered: valid checks + x/dot/shade/circle marks below 5 -> discarded (invalid marks are not answers, no extra flag)', async () => {
  for (const [which, rec] of RECONCILERS) {
    const marks = ['x', 'dot', 'shade', 'circle'];
    const invalid = Object.fromEntries(Array.from({ length: 17 }, (_, i) => [i, marks[i % 4]]));
    const r = await rec(python({ invalid }), geminiResp());   // Gemini reads every mark as a letter
    assert.strictEqual(r.answered_count, 3, which);
    assert.strictEqual(r.omr_agreement.invalid_mark_rows, 17, which);
    assert.strictEqual(r.auto_discard, true, which);
    assert.deepStrictEqual(r.review_reasons, ['too_few_answers_3_of_20'], which);
  }
});
test('Min answered: an unreliable scan with < 5 answers goes to review (too_few_answers, auto_discard false), never discarded', async () => {
  const empty = () => python({ override: blankRows(20) });
  const gEmpty = (fields = {}) => geminiWith(geminiBlank(QS), fields);
  const notJson = { candidates: [{ content: { parts: [{ text: 'no json here' }] } }] };
  const cases = [
    ['OMR failed', { error: 'OMR down' }, gEmpty(), 'omr_unavailable'],
    ['Gemini unusable', empty(), notJson, 'gemini_unavailable'],
    ['grid not found', python({ table: false, override: blankRows(20) }), gEmpty(), 'grid_not_detected'],
    ['fallback grid', python({ grid: 'fallback', override: blankRows(20) }), gEmpty(), null],
    ['layout mismatch', pyLayout(OTHER_LAYOUT, { override: blankRows(20) }), gEmpty(), 'layout_mismatch'],
    ['poor scan quality', empty(), gEmpty({ scan_quality: { poor_quality: true, issues: ['low_light'], reason: 'Dark' } }), 'poor_scan_quality'],
    ['not a SAST form', empty(), gEmpty({ document_check: { is_sast_form: false, reason: 'A receipt' } }), 'not_a_sast_form'],
  ];
  for (const [which, rec] of RECONCILERS) {
    for (const [label, py, g, reason] of cases) {
      const r = await rec(py, g);
      const tag = `${which}: ${label}`;
      assert.strictEqual(r.auto_discard, false, tag);
      assert.strictEqual(r.needs_review, true, tag);
      assert.ok(r.review_reasons.includes('too_few_answers_0_of_20'), `${tag}: ${r.review_reasons}`);
      if (reason) assert.ok(r.review_reasons.some((x) => x.startsWith(reason)), `${tag}: ${r.review_reasons}`);
      assert.match(r.review_note, /^Only 0 of the 20 items have an answer \(at least 5 are needed to count a form\), but the scan could not be fully checked\./, tag);
      assert.match(r.summary, / — flagged: /, tag);
    }
  }
});
test('Min answered (website): Decide flags it, Flagged Record passes auto_discard, queue row stored discarded, answer says so, no push', async () => {
  const rec = await answeredOnly(4, (py, g) => reconcile(py, g));
  const d = await decide(identity(), rec);
  assert.strictEqual(d.decision, 'flagged');
  assert.deepStrictEqual(d.review_reasons, ['too_few_answers_4_of_20'], 'never a spot_check on top');
  const wrec = (await run('Website Flagged Record', d, { 'Website Prepare Scan': CTX, 'Website Reconcile OCR OMR': rec }))[0].json;
  assert.strictEqual(wrec.auto_discard, true);
  assert.strictEqual(wrec.answered_count, 4);
  const q = (await run('build flagged record1', {}, { 'Website Flagged Record': wrec, 'Save scan error image': [{ id: 'img1' }] }))[0].json;
  assert.deepStrictEqual(Object.keys(q).filter((k) => !DISCARD_COLUMNS.includes(k)), []);
  assert.strictEqual(q.status, 'discarded');
  assert.strictEqual(q.review_action, 'rejected');
  assert.strictEqual(q.validation_status, 'flagged');
  assert.deepStrictEqual(q.review_reasons, ['too_few_answers_4_of_20']);
  assert.strictEqual(q.scan_image_id, 'img1', 'the image is kept');
  assert.strictEqual(q.batch_id, BATCH);
  const c = q.review_correction;
  assert.strictEqual(c.action, 'rejected');
  assert.strictEqual(c.auto, true);
  assert.strictEqual(c.reviewed_by, null);
  assert.strictEqual(c.reviewed_by_name, 'Automatic rule');
  assert.strictEqual(c.note, 'Fewer than 5 of the 20 items were answered (only 4), so this form was discarded automatically and is not counted.');
  assert.ok(!Number.isNaN(Date.parse(c.at)));
  // the website answer: still the 'flagged' contract (the backend counts it), with a plain message
  const evalBody = respondExpr();
  const ok = assertContract([{ json: JSON.parse(evalBody({}, { 'Website Flagged Record': wrec })) }], 'flagged');
  assert.deepStrictEqual(ok.review_reasons, ['too_few_answers_4_of_20']);
  assert.strictEqual(ok.message, 'Discarded automatically: fewer than 5 of the 20 items were answered (only 4), so this form is not counted.');
  const failed = JSON.parse(evalBody({ error: 'HTTP 500' }, { 'Website Flagged Record': wrec }));
  assert.ok(failed.review_reasons.includes('internal_error_not_saved'));
  assert.match(failed.message, /^NOT SAVED/);
  // no staff push for a discarded form; a pending one still notifies its staff member
  const fcm = fcmParams();
  assert.deepStrictEqual(fcm(q), [null]);
  const pending = (await run('build flagged record1', {}, { 'Website Flagged Record': (await run('Website Flagged Record',
    await decide(identity(), REC_DISAGREE()), { 'Website Prepare Scan': CTX, 'Website Reconcile OCR OMR': REC }))[0].json,
  'Save scan error image': [{ id: 'img1' }] }))[0].json;
  assert.strictEqual(pending.status, 'pending');
  assert.ok(!('review_correction' in pending) && !('review_action' in pending));
  assert.deepStrictEqual(fcm(pending), [STAFF]);
});
test('Min answered (website): an internal error is never discarded (review), even when the reconciler said auto_discard', async () => {
  const rec = await answeredOnly(2, (py, g) => reconcile(py, g));
  assert.strictEqual(rec.auto_discard, true);
  const wrec = (await run('Website Flagged Record', { error: 'Decide crashed' },
    { 'Website Prepare Scan': CTX, 'Website Reconcile OCR OMR': rec }))[0].json;
  assert.strictEqual(wrec.auto_discard, false);
  assert.ok(wrec.review_reasons.includes('internal_error_decide'));
  const q = (await run('build flagged record1', {}, { 'Website Flagged Record': wrec, 'Save scan error image': [{ id: 'i' }] }))[0].json;
  assert.strictEqual(q.status, 'pending');
});
test('Min answered (phone): reconcile -> build flagged record1 stores it discarded; Gemini-failure records stay pending', async () => {
  const r = await answeredOnly(3, (py, g) => phoneReconcile(py, g));
  assert.strictEqual(r.validation_status, 'flagged');
  assert.strictEqual(r.auto_discard, true);
  const q = (await run('build flagged record1', {}, { 'reconcile ocr and omr1': r, 'Save scan error image': [{ id: 'i' }] }))[0].json;
  assert.deepStrictEqual(Object.keys(q).filter((k) => !DISCARD_COLUMNS.includes(k)), []);
  assert.strictEqual(q.status, 'discarded');
  assert.strictEqual(q.review_action, 'rejected');
  assert.strictEqual(q.review_correction.note, 'Fewer than 5 of the 20 items were answered (only 3), so this form was discarded automatically and is not counted.');
  assert.strictEqual(q.user_id, STAFF);
  assert.deepStrictEqual(fcmParams()(q), [null]);
  // phone answer text unchanged (Flutter only checks the HTTP status)
  assert.strictEqual(respondExpr()({ anything: 1 }, {}), '{{ JSON.stringify($json) }}');
  const gf = (await run('build flagged record1', {}, { 'Handle Gemini failure': { task_id: 'p9', review_reasons: ['ai_validation_failed'] },
    'Save scan error image': [{ id: 'i' }] }))[0].json;
  assert.strictEqual(gf.status, 'pending');
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
    'review_note', 'task_id', 'Student ID', 'Instructor/Professor', 'Subject Taught', 'AY/Semester', 'Remarks & Suggestions', 'Date',
    'stamp_no', 'stamp_source'];   // stamp rule: a clean page always has its one stamp
  for (let i = 1; i <= 10; i++) phoneKeys.push(`M${i}`, `P${i}`);
  assert.deepStrictEqual(Object.keys(r).sort(), phoneKeys.sort());
  assert.strictEqual(r.instructor_ID, INSTR);
  assert.strictEqual(r.true_subject_id, SUBJ);
  assert.strictEqual(r.current_term_id, TERM);
  assert.strictEqual(r.task_id, TASK);
  assert.strictEqual(r['Instructor/Professor'], 'Juan Dela Cruz');
  assert.strictEqual(r.M1, 5);
  assert.strictEqual(r.stamp_no, '0100005');
  assert.strictEqual(r.stamp_source, 'gemini+python');
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
  assert.strictEqual(v.length, 12);
  assert.deepStrictEqual(v.slice(10), ['0100005', 'gemini+python'], 'stamp_no + stamp_source (save_website_raw_row)');
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
  assert.strictEqual(row.stamp_no, v[10]);
  assert.strictEqual(row.stamp_source, v[11]);
  // no stamp on the page -> NULL stamp, NULL source
  const dn = await decide(identity(), { ...REC, stamp_no: null, stamp_status: 'missing', review_reasons: ['stamp_missing'] });
  const vn = new Function('$', '$json', `return (${m[1]});`)((ref) => ({ first: () => ({ json: { 'Website Decide': dn }[ref] }) }), {});
  assert.deepStrictEqual(vn.slice(10), [null, null]);
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
  // the SAST recompute and (2026-10-30) the SET-15 low-score push, which runs once per import
  assert.deepStrictEqual(ins('If28'), ['SET Low Score Push[0]', 'update to overall total2[0]']);
  assert.deepStrictEqual(ins('update to overall total2'), ['Code in JavaScript8[0]']);
  const n = wf.nodes.find((x) => x.name === 'update to overall total2');
  assert.strictEqual(n.parameters.query.trim(), 'SELECT * FROM public.recompute_sast($1::uuid, $2::uuid);');
  assert.strictEqual(n.parameters.options.queryReplacement, '={{ [$json.instructor_id, $json.term_id] }}');
  // error output (2026-10-18): a failed recompute is recorded on the Google Sheet import job (migration 018)
  assert.strictEqual(n.onError, 'continueErrorOutput');
  assert.deepStrictEqual(outs('update to overall total2', 1), ['Describe Import Failure']);
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
function ifTrue(name, $json, $ = () => { throw new Error('$() is not mocked in this test'); }) {
  const c = wf.nodes.find((x) => x.name === name).parameters.conditions;
  assert.strictEqual(c.combinator, 'and');
  return c.conditions.every((cond) => {
    const m = /^=\{\{([\s\S]*)\}\}$/.exec(cond.leftValue.trim());
    // eslint-disable-next-line no-new-func
    const v = new Function('$json', '$', `return (${m[1]});`)($json, $);
    const op = `${cond.operator.type}:${cond.operator.operation}`;
    if (op === 'string:exists') return v !== undefined && v !== null;
    if (op === 'string:notEmpty') return v !== undefined && v !== null && String(v) !== '';
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
  assert.deepStrictEqual(out('get existing scan image id'), ['Correction Candidates']);
  assert.deepStrictEqual(out('Correction Candidates'), ['Correction Name Match']);
  assert.deepStrictEqual(out('Correction Name Match'), ['get instructor id7']);
  assert.deepStrictEqual(out('import-error-correction1'), ['Import Fix New Form?']);   // guard: SET-15 / SUP-15 -> 422
  assert.deepStrictEqual(out('Import Fix New Form?'), ['Respond new_form_not_fixable']);
  assert.deepStrictEqual(((wf.connections['Import Fix New Form?'] || {}).main || [])[1].map((c) => c.node), ['get existing scan image id1']);
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
// 'Correction Name Match' run on a webhook body (cands: the 'Correction Candidates' row; null = node not run)
async function nameMatch(body, cands = CORR_CANDS) {
  const nodes = { 'sast-manual-correction1': { body } };
  if (cands !== null) nodes['Correction Candidates'] = cands;
  return (await run('Correction Name Match', { scan_image_id: null }, nodes))[0].json;
}
test('Correction lookups: get instructor id7 / get subject id6 get array parameters from Correction Name Match (body when it failed)', async () => {
  const flutter = { body: { instructor: 'Juan Dela Cruz', instructor_id: null, subject: 'IT 101', subject_id: null } };
  const nodes = (body, cm) => ({ 'sast-manual-correction1': body, 'Correction Name Match': cm });
  // the match node failed (onError continue: an error item): the webhook body exactly as before
  assert.deepStrictEqual(exprValue('get instructor id7', { error: 'x' }, nodes(flutter, { error: 'x' })), ['Juan Dela Cruz', '']);
  assert.deepStrictEqual(exprValue('get subject id6', { instructor_id: INSTR }, nodes(flutter, { error: 'x' })), [INSTR, 'IT 101', '']);
  // Flutter [null] arrays and a missing instructor row never shift the parameters
  const odd = { body: { instructor: [null], subject: 'Math, Science', subject_id: [null] } };
  const oddMatch = await nameMatch(odd.body);
  assert.deepStrictEqual(exprValue('get instructor id7', {}, nodes(odd, oddMatch)), ['', '']);
  assert.deepStrictEqual(exprValue('get subject id6', { instructor_id: null }, nodes(odd, oddMatch)), [null, 'Math, Science', '']);
  assert.deepStrictEqual(exprValue('get subject id6', { error: 'x' }, nodes(odd, oddMatch)), [null, 'Math, Science', '']);
  const web = { body: { instructor: 'Pedro Reyes', instructor_id: PEDRO, subject: 'IT 102', subject_id: IT102 } };
  const webMatch = await nameMatch(web.body);
  assert.deepStrictEqual(exprValue('get instructor id7', {}, nodes(web, webMatch)), ['Pedro Reyes', PEDRO]);
  assert.deepStrictEqual(exprValue('get subject id6', { instructor_id: PEDRO }, nodes(web, webMatch)), [PEDRO, 'IT 102', IT102]);
  // older app: names only -> the local match supplies the ids
  const old = { body: { instructor: 'Prof. Pedro Reyes', subject: 'IT 102 - Computer Programming 1' } };
  const oldMatch = await nameMatch(old.body);
  assert.deepStrictEqual(exprValue('get instructor id7', {}, nodes(old, oldMatch)), ['Prof. Pedro Reyes', PEDRO]);
  assert.deepStrictEqual(exprValue('get subject id6', { instructor_id: PEDRO }, nodes(old, oldMatch)),
    [PEDRO, 'IT 102 - Computer Programming 1', IT102]);
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
test('Correction response: Map target -> (ids found, stamp check) -> Mark Queue Validated1 -> Respond to Webhook5 -> After Correction Response -> If19', async () => {
  const out = (n, i = 0) => (((wf.connections[n] || {}).main || [])[i] || []).map((c) => c.node);
  assert.deepStrictEqual(out('Map target'), ['Correction Ids Found?'], 'Map target no longer feeds If19 directly');
  assert.deepStrictEqual(out('Correction Ids Found?', 0), ['Correction Stamp Check']);
  assert.deepStrictEqual(out('Correction Ids Found?', 1), ['Correction Not Found']);
  assert.deepStrictEqual(out('Correction Stamp Check'), ['Correction Stamp Duplicate?']);
  assert.deepStrictEqual(out('Correction Stamp Duplicate?', 1), ['Mark Queue Validated1']);
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

// ---- corrections never validate an unresolved scan (2026-10-24): local name match + 422
const JUAN2 = 'bbbbbbbb-2222-4222-8222-00000000000b';
const CORR_CANDS = {
  candidates_version: 1,
  instructors: [
    { id: INSTR, name: 'Juan Dela Cruz' }, { id: PEDRO, name: 'Pedro Reyes' }, { id: MARIA, name: 'Maria Santos' },
    { id: ANA, name: 'Mario Santos' }, { id: NORA, name: 'Nora Villanueva' },
  ],
  subjects: [
    { id: SUBJ, code: 'IT 101', name: 'Introduction to Computing' },
    { id: IT102, code: 'IT 102', name: 'Computer Programming 1' },
    { id: IT103, code: 'IT 103', name: 'Computer Programming 2' },
    { id: GE104, code: 'GE 104', name: 'Mathematics in the Modern World' },
  ],
};
test('Correction Name Match: body ids pass through; a clear text match gives the id; ambiguous gives no id and no name for the SQL guess', async () => {
  // website review / current app: ids win (the SQL still checks them), text untouched
  let m = await nameMatch({ instructor: 'Pedro Reyes', instructor_id: PEDRO.toUpperCase(), subject: 'IT 102', subject_id: IT102 });
  assert.deepStrictEqual([m.instructor_id, m.instructor_name, m.subject_id, m.subject_text], [PEDRO, 'Pedro Reyes', IT102, 'IT 102']);
  assert.strictEqual(m.instructor_match.match, 'id');
  assert.strictEqual(m.subject_match.match, 'id');
  // older app: OCR-ish text only
  m = await nameMatch({ instructor: 'Mr. Pedro Reyess', subject: 'it-102' });
  assert.strictEqual(m.instructor_id, PEDRO);
  assert.strictEqual(m.instructor_match.match, 'found');
  assert.strictEqual(m.subject_id, IT102);
  // the old Flutter picker text "CODE — Name"
  m = await nameMatch({ instructor: 'Juan Dela Cruz', subject: 'IT 101 — Introduction to Computing' });
  assert.deepStrictEqual([m.instructor_id, m.subject_id], [INSTR, SUBJ]);
  // title only, clearly one subject
  m = await nameMatch({ instructor: 'Nora Villanueva', subject: 'Mathematics in the Modern World' });
  assert.strictEqual(m.subject_id, GE104);
  // "Mari Santos" fits Maria and Mario Santos equally: ambiguous -> no id, no name (the SQL must not pick one)
  m = await nameMatch({ instructor: 'Mari Santos', subject: 'IT 102' });
  assert.strictEqual(m.instructor_match.match, 'ambiguous');
  assert.strictEqual(m.instructor_id, '');
  assert.strictEqual(m.instructor_name, '');
  assert.deepStrictEqual(m.instructor_match.candidates.map((c) => c.id).sort(), [ANA, MARIA].sort());
  // two people with the same name: never guessed
  m = await nameMatch({ instructor: 'Juan Dela Cruz', subject: 'IT 101' },
    { ...CORR_CANDS, instructors: [...CORR_CANDS.instructors, { id: JUAN2, name: 'Juan Dela Cruz' }] });
  assert.strictEqual(m.instructor_match.match, 'ambiguous');
  assert.deepStrictEqual(m.instructor_match.candidates.map((c) => c.id).sort(), [INSTR, JUAN2].sort());
  // "Computer Programming" fits IT 102 and IT 103 equally: ambiguous subject
  m = await nameMatch({ instructor: 'Pedro Reyes', subject: 'Computer Programming' });
  assert.strictEqual(m.subject_match.match, 'ambiguous');
  assert.deepStrictEqual([m.subject_id, m.subject_text], ['', '']);
  assert.deepStrictEqual(m.subject_match.candidates.map((c) => c.id).sort(), [IT102, IT103].sort());
  // a code that is no subject never picks a subject by title; one letter off is only a candidate
  m = await nameMatch({ instructor: 'Pedro Reyes', subject: 'IT 109 Computer Programming 1' });
  assert.strictEqual(m.subject_match.match, 'none');
  assert.strictEqual(m.subject_text, 'IT 109 Computer Programming 1', 'no match keeps the text for the old SQL lookup');
  // nobody fits: none, text kept for the old strict SQL lookup, up to 5 candidates
  m = await nameMatch({ instructor: 'Xyz Qwerty', subject: '' });
  assert.deepStrictEqual([m.instructor_match.match, m.instructor_id, m.instructor_name], ['none', '', 'Xyz Qwerty']);
  assert.ok(m.instructor_match.candidates.length <= 5);
  assert.strictEqual(m.subject_match.match, 'missing');
  // candidate lookup failed / not run: body values as before (old SQL behaviour)
  for (const c of [null, { error: 'db down' }]) {
    m = await nameMatch({ instructor: 'Pedro Reyes', subject: 'IT 102' }, c);
    assert.deepStrictEqual([m.instructor_id, m.instructor_name, m.subject_id, m.subject_text], ['', 'Pedro Reyes', '', 'IT 102']);
    assert.strictEqual(m.instructor_match.match, 'unavailable');
  }
  // Flutter [null] arrays / junk ids
  m = await nameMatch({ instructor: [null], instructor_id: [null], subject: null, subject_id: 'not-a-uuid' });
  assert.deepStrictEqual([m.instructor_id, m.instructor_name, m.subject_id, m.subject_text], ['', '', '', '']);
});
test('Correction Ids Found?: both ids set -> stamp check / Mark Queue Validated1; else 422 instructor_not_found | subject_not_found | instructor_and_subject_not_found, nothing saved', async () => {
  assert.strictEqual(ifTrue('Correction Ids Found?', { instructor_ID: PEDRO, true_subject_id: IT102 }), true);
  for (const j of [{ instructor_ID: null, true_subject_id: IT102 }, { instructor_ID: PEDRO, true_subject_id: null },
    { instructor_ID: '', true_subject_id: '' }, {}]) {
    assert.strictEqual(ifTrue('Correction Ids Found?', j), false, JSON.stringify(j));
  }
  const outs2 = (n, i = 0) => (((wf.connections[n] || {}).main || [])[i] || []).map((c) => c.node);
  assert.deepStrictEqual(outs2('Correction Not Found'), ['Correction Not Found Response']);
  assert.deepStrictEqual(outs2('Correction Not Found Response'), [], 'nothing after the 422 answer: not validated, no import error');
  const resp = wf.nodes.find((x) => x.name === 'Correction Not Found Response');
  assert.strictEqual(resp.parameters.options.responseCode, 422);
  const bodyOf = (j) => JSON.parse(evalExpr(resp.parameters.responseBody, { $json: j }));
  const hook = (b) => ({ body: { failed_scan_id: 'f1', task_id: 't1', ...b } });
  async function notFound(b, mapped, cands = CORR_CANDS) {
    const match = await nameMatch(hook(b).body, cands);
    const out = await run('Correction Not Found', mapped, { 'sast-manual-correction1': hook(b), 'Correction Name Match': match });
    return bodyOf(out[0].json);
  }
  let r = await notFound({ instructor: 'Mari Santos', subject: 'IT 102' }, { instructor_ID: null, true_subject_id: IT102 });
  assert.strictEqual(r.success, false);
  assert.strictEqual(r.code, 'instructor_not_found');
  assert.strictEqual(r.instructor_match, 'ambiguous');
  assert.ok(/Several instructors fit "Mari Santos"/.test(r.message), r.message);
  assert.deepStrictEqual(r.candidates.instructors.map((c) => c.id).sort(), [ANA, MARIA].sort());
  assert.ok(!('subjects' in r.candidates));
  assert.ok(r.candidates.instructors.every((c) => Object.keys(c).sort().join() === 'id,name'));
  r = await notFound({ instructor: 'Pedro Reyes', subject: 'Computer Programming' }, { instructor_ID: PEDRO, true_subject_id: null });
  assert.strictEqual(r.code, 'subject_not_found');
  assert.deepStrictEqual(r.candidates.subjects.map((c) => c.code).sort(), ['IT 102', 'IT 103']);
  r = await notFound({ instructor: 'Xyz Qwerty', subject: 'ZZ 999' }, { instructor_ID: null, true_subject_id: null });
  assert.strictEqual(r.code, 'instructor_and_subject_not_found');
  assert.ok(/was not found/.test(r.message) && /Pick the instructor and the subject/.test(r.message), r.message);
  assert.ok(r.candidates.instructors.length <= 5 && r.candidates.subjects.length <= 5);
  // the match node failed: still a clear 422 with no candidates
  const out = await run('Correction Not Found', { instructor_ID: null, true_subject_id: SUBJ },
    { 'sast-manual-correction1': hook({ instructor: 'Someone' }), 'Correction Name Match': { error: 'x' } });
  r = bodyOf(out[0].json);
  assert.strictEqual(r.code, 'instructor_not_found');
  assert.deepStrictEqual(r.candidates, { instructors: [] });
});
test('Correction chain (simulated): a text-only correction from an older app resolves locally and reaches Mark Queue Validated1', async () => {
  const b = { failed_scan_id: 'f1', task_id: 't1', instructor: 'Prof Pedro Reyes', subject: 'IT102', student_id: 'S', m1: 4 };
  const match = await nameMatch(b);
  // the SQL lookups return the row for the id they were given
  const nodes = { 'sast-manual-correction1': { body: b }, 'get existing scan image id': { scan_image_id: null },
    'Correction Name Match': match,
    'get instructor id7': { instructor_id: match.instructor_id, first_name: 'Pedro', last_name: 'Reyes', match_score: 1 },
    'get subject id6': { subject_id: match.subject_id, subject_name: 'Computer Programming 1', department_id: 1 } };
  const built = (await run('build the record1', {}, nodes))[0].json;
  const mapped = (await run('Map target', { ...built, current_term_id: TERM }, nodes))[0].json;
  assert.strictEqual(mapped.instructor_ID, PEDRO);
  assert.strictEqual(mapped.true_subject_id, IT102);
  assert.strictEqual(ifTrue('Correction Ids Found?', mapped), true);
  assert.strictEqual(ifTrue('If19', mapped), true, 'credited, never an import error');
});
test('Correction candidates: never reach a Gemini node (privacy)', () => {
  const reach = (start) => {
    const seen = new Set();
    const stack = [start];
    while (stack.length) {
      const c = stack.pop();
      if (seen.has(c) || c === 'If19') continue;
      seen.add(c);
      for (const o of Object.values(wf.connections[c] || {})) for (const l of o) for (const e of l || []) stack.push(e.node);
    }
    return seen;
  };
  const r = reach('Correction Candidates');
  const ai = wf.nodes.filter((n) => r.has(n.name) && (/langchain|gemini/i.test(n.type)
    || /generativelanguage/.test(JSON.stringify(n.parameters))));
  assert.deepStrictEqual(ai.map((n) => n.name), []);
  const q = wf.nodes.find((n) => n.name === 'Correction Candidates');
  assert.deepStrictEqual(q.parameters.options, {}, 'no query parameters');
  assert.strictEqual(q.onError, 'continueRegularOutput');
  assert.strictEqual(q.alwaysOutputData, true);
  assert.strictEqual(wf.nodes.find((n) => n.name === 'Correction Name Match').onError, 'continueRegularOutput');
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
    'Normalize Sheet Rows': { __items: [{ 'Student ID': 'S1', 'Remarks & Suggestions': 'Buang ka, bilat', M1: 5 }, { Remarks: 12345 }] },
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
  assert.strictEqual(out.find((r) => r.record_type === 'evaluation').improvement_insights, 'Less ***** talk.');
  const ind = { overall_tone: 'Critical', keywords: [{ word: 'fuck' }, { word: '*****' }], ai_suggestion: 'no iyot jokes',
    student_interpretation: 's', positive_themes: ['ok'], improvement_insights: ['tite'] };
  const out2 = (await run('Add Parse Code node1', { text: JSON.stringify(ind) },
    { 'Prepare Individual AI Input1': { instructor_ID: INSTR, term_id: TERM, subject_id: SUBJ, new_student_remark: 'jakol si sir' } })).map((i) => i.json);
  assert.deepStrictEqual(out2.filter((r) => r.record_type === 'wordcloud').map((r) => r.word), ['Needs improvement'],
    'every keyword dropped -> the existing fallback theme');
  assert.strictEqual(out2.find((r) => r.record_type === 'student_remark').remark, '***** si sir');
  const ev = out2.find((r) => r.record_type === 'evaluation');
  assert.strictEqual(ev.ai_suggestion, 'no **** jokes');
  assert.strictEqual(ev.improvement_insights, '****.');
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
  assert.ok(!phone.includes('known_instructor') && !phone.includes('Known instructors'), 'phone: no instructor list either');
  assert.ok(web.includes('header_image_base64'), 'optional third image part for the header crop');
  assert.ok(web.includes('HEADER CLOSE-UP'), 'prompt mentions the header close-up when sent');
  assert.ok(!phone.includes('header_image_base64') && !phone.includes('HEADER CLOSE-UP'), 'phone unaffected');
  // required fields never include the optional known_*/confidence fields
  const req = JSON.parse('{' + web.slice(web.indexOf('"required"'), web.indexOf(']', web.indexOf('"required"')) + 1) + '}').required;
  for (const f of ['known_subject', 'instructor_confidence', 'subject_confidence', 'student_id_confidence']) {
    assert.ok(!req.includes(f), f);
  }
});
test('No instructor name or list is sent to Gemini (Known Names SQL, both OCR bodies, reconciler, Verify Remark Words, remark AI)', async () => {
  const NAMES = ['Joseph King', RODZ, 'Evangeline Caruana'];
  // the lookup itself: subjects only, no parameters (it used to pass the batch instructor id)
  const kn = wf.nodes.find((x) => x.name === 'Website Known Names');
  const sql = kn.parameters.query.replace(/--[^\n]*/g, '');
  assert.ok(!/user_info|instructor/i.test(sql), 'Website Known Names does not read or return instructors');
  assert.ok(/\bAS\s+subject_list\b/.test(sql) && /\bAS\s+subjects\b/.test(sql), 'subject list kept');
  assert.strictEqual(kn.parameters.options.queryReplacement, undefined, 'no query parameters');
  // phone: the same subjects-only lookup, no parameters
  const phoneKnNode = wf.nodes.find((x) => x.name === 'Phone Known Names');
  const phoneKn = phoneKnNode.parameters.query.replace(/--[^\n]*/g, '');
  assert.ok(!/user_info|instructor/i.test(phoneKn), 'Phone Known Names does not read or return instructors');
  assert.ok(/\bAS\s+subject_list\b/.test(phoneKn), 'phone subject list kept');
  assert.strictEqual((phoneKnNode.parameters.options || {}).queryReplacement, undefined, 'no query parameters');
  // phone Gemini body: even if a lookup returned instructor names, none reaches Gemini
  const phoneSrc = wf.nodes.find((x) => x.name === 'ai validation ocr and omr').parameters.jsonBody.replace(/^=/, '');
  for (const known of [
    { subject_list: 'PC121 = Discreet Math' },
    { instructor_list: NAMES.join('; '), instructor_names: NAMES, subject_list: 'PC121 = Discreet Math' },
    { error: 'timeout' }]) {
    const nodes = { 'SAST image from phone': { body: { image: 'QUJD' } }, 'Phone Known Names': known,
      'Phone Instructor Candidates': { candidates_version: 1, instructor_names: NAMES } };
    const $ = (ref) => ({ first: () => ({ json: nodes[ref] }) });
    // eslint-disable-next-line no-new-func
    const text = phoneSrc.replace(/\{\{([\s\S]*?)\}\}/g, (m, js) => String(new Function('$', `return (${js});`)($)));
    const body = JSON.parse(text);
    for (const name of NAMES) assert.ok(!text.includes(name), `${name} not in the phone Gemini body`);
    assert.ok(!/known_instructor|Known instructors|instructor_list|instructor_names/.test(text));
    assert.strictEqual(body.generationConfig.responseSchema.properties.known_instructor, undefined);
  }
  // the phone candidates lookup runs AFTER the Gemini call and no Gemini node reads it
  assert.deepStrictEqual(wf.connections['Is not a SAST form?'].main[1].map((c) => c.node), ['Phone Instructor Candidates']);
  assert.deepStrictEqual(wf.connections['Phone Instructor Candidates'].main[0].map((c) => c.node), ['Phone Remark Check']);
  for (const n of wf.nodes) {
    const p = JSON.stringify(n.parameters);
    if (/generativelanguage\.googleapis\.com/.test(p) || n.type.startsWith('@n8n/n8n-nodes-langchain.')) {
      assert.ok(!/Phone Instructor Candidates|Website Form Match|instructor_names|instructor_list|first_name|last_name/.test(p),
        `${n.name}: no instructor names in a Gemini request`);
    }
  }
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

// ---------------------------------------------------------------- stamp rule (2026-10-16, migration 015)
// Gemini's stamp_numbers -> one readable stamp (ok) or a review reason; a stamp already stored for
// the term auto-rejects the page (duplicate_stamp), above reused_image and the min-answered discard.
const DUP = { raw_id: 77, task_id: 'web:aaaaaaaa-0000-4000-8000-000000000001:0123456789abcdef',
  batch_id: 'aaaaaaaa-0000-4000-8000-000000000001', submitted_date: '2026-10-01', instructor_id: INSTR,
  subject_id: SUBJ, term_id: TERM, stamp_no: '0100005' };
const STAMP_DUP_MESSAGE = 'Discarded automatically: this form was already scanned (stamp 0100005), so it is not counted.';
// Gemini's final answer = stamps; its page/tile reads and the Python reader agree with the readable
// numbers (cands) - this checks the parsing of Gemini's answer; the two-reader rule is tested below
const stampRec = async (rec, stamps, cands = []) => rec(python({ stamp: pyStamp(cands) }), geminiResp({
  stamp_numbers: stamps, stamp_numbers_page: cands, stamp_reads_tiles: cands.map((n, i) => ({ tile: `T${i + 1}`, number: n })) }));
test('Stamp: Gemini prompt (both) carries the verbatim stamp paragraph; stamp_numbers required; no instructor names', () => {
  const PARA = 'stamp_numbers: every number printed on the page by a numbering-machine stamp. A numbering stamp is a machine-printed '
    + 'number in ink (usually black) made of evenly spaced, same-size digits, often 5 to 9 digits long and sometimes starting with '
    + 'zeros. It can be anywhere on the page (top, bottom, a margin, inside or over the table) and in any orientation (upright, '
    + 'sideways, upside down or tilted); read it in its own orientation. Check marks, signatures or handwriting may cover part of '
    + 'it. Return each stamped number as a string of digits only, exactly as printed, keeping leading zeros; write ? for a digit '
    + 'you cannot read. If there are several stamped numbers, return all of them. Return [] when the page has no stamp. Do NOT '
    + 'include the student ID, dates, phone numbers, handwritten numbers, rating numbers, or the printed form codes and texts '
    + '(for example "SS Form2", "Revision 3", "February 4, 2009").';
  for (const name of ['ai validation ocr and omr', 'Website AI Validation']) {
    const body = wf.nodes.find((x) => x.name === name).parameters.jsonBody;
    assert.strictEqual(body.split(JSON.stringify(PARA).slice(1, -1)).length - 1, 1, `${name}: paragraph exactly once, verbatim`);
    assert.strictEqual(body.split('"stamp_numbers": { "type": "ARRAY", "items": { "type": "STRING" } }').length - 1, 1, name);
    assert.ok(body.includes('"student_id",\n        "stamp_numbers",\n        "stamp_numbers_page",\n        "stamp_reads_tiles",\n'
      + '        "document_check",'), `${name}: required`);
    for (const m of ['known_instructor', 'Known instructors', "$('Phone Instructor Candidates')", 'first_name']) {
      assert.ok(!body.includes(m), `${name}: ${m}`);
    }
  }
});
test('Stamp: both reconcilers -- ok (leading zeros kept, de-duplicated, separators stripped), missing, unreadable, multiple', async () => {
  for (const [which, rec] of RECONCILERS) {
    const ok = await stampRec(rec, ['0100005'], ['0100005']);
    assert.strictEqual(ok.stamp_status, 'ok', which);
    assert.strictEqual(ok.stamp_no, '0100005', `${which}: leading zero kept`);
    assert.deepStrictEqual(ok.stamp_candidates, ['0100005'], which);
    assert.deepStrictEqual(ok.stamp_raw, ['0100005'], which);
    assert.deepStrictEqual(ok.review_reasons, [], which);
    assert.strictEqual(ok.stamp_source, 'gemini+python', which);
    const cases = [
      [['0100005', ' 0100005 '], 'ok', '0100005', ['0100005']],
      [['No. 0100-005'], 'ok', '0100005', ['0100005']],
      [['123'], 'ok', '123', ['123']],
      [['000000000012'], 'ok', '000000000012', ['000000000012']],
      [[], 'missing', null, []],
      [['', '   '], 'missing', null, []],
      [undefined, 'missing', null, []],
      ['0100005', 'missing', null, []],                 // not an array = []
      [['01?0005'], 'unreadable', null, []],
      [['12'], 'unreadable', null, []],                 // too short
      [['1234567890123'], 'unreadable', null, []],      // 13 digits: too long
      [['abc'], 'unreadable', null, []],
      [['0100005', '0200006'], 'multiple', null, ['0100005', '0200006']],
      [['0100005', '01?0006'], 'multiple', null, ['0100005']],
      [['0100005', '12'], 'multiple', null, ['0100005']],
    ];
    const REASON = { ok: null, missing: 'stamp_missing', unreadable: 'stamp_unreadable', multiple: 'multiple_stamps' };
    for (const [stamps, status, no, cands] of cases) {
      const r = await stampRec(rec, stamps, cands);
      const tag = `${which}: ${JSON.stringify(stamps)}`;
      assert.strictEqual(r.stamp_status, status, tag);
      assert.strictEqual(r.stamp_no, no, tag);
      assert.deepStrictEqual(r.stamp_candidates, cands, tag);
      if (REASON[status]) {
        assert.deepStrictEqual(r.review_reasons, [REASON[status]], `${tag}: a stamp reason sends the page to review`);
        assert.strictEqual(r.needs_review, true, tag);
        assert.match(r.review_note, /stamp/i, tag);
      } else {
        assert.deepStrictEqual(r.review_reasons, [], tag);
      }
    }
    // Gemini answered without JSON -> unreadable (the stamp could not be read)
    const nj = await rec(python(), { candidates: [{ content: { parts: [{ text: 'no json' }] } }] });
    assert.strictEqual(nj.stamp_status, 'unreadable', which);
    assert.ok(nj.review_reasons.includes('stamp_unreadable') && nj.review_reasons.includes('gemini_unavailable'), which);
  }
  // website: the Gemini call itself failed -> unreadable
  const failed = (await run('Website Reconcile OCR OMR', { error: 'timeout' },
    { 'Website Python OMR': python(), 'Website AI Validation': undefined }))[0].json;
  assert.strictEqual(failed.stamp_status, 'unreadable');
  assert.deepStrictEqual(failed.review_reasons, ['ai_validation_failed', 'stamp_unreadable']);
  // one copy of website_src/stamp.js in each reconciler
  const copies = ['Website Reconcile OCR OMR', 'reconcile ocr and omr1'].map((n) => {
    const c = codeOf(n);
    const i = c.indexOf('// stamp reader, from website_src/stamp.js');
    return c.slice(i, c.indexOf('// end of stamp reader', i));
  });
  assert.ok(copies[0].length > 100 && copies[0] === copies[1]);
});
test('Stamp: Website Identity Check gets the stamp as $6 ("" unless exactly one stamp was read)', () => {
  const ctx = { instructor_id: INSTR, subject_id: SUBJ, term_id: TERM, task_id: TASK };
  const v = exprValue('Website Identity Check', { student_id: ' 1 ', stamp_status: 'ok', stamp_no: '0100005' }, { 'Website Prepare Scan': ctx });
  assert.deepStrictEqual(v, [INSTR, SUBJ, TERM, '1', TASK, '0100005']);
  for (const st of ['missing', 'unreadable', 'multiple']) {
    assert.strictEqual(exprValue('Website Identity Check', { stamp_status: st, stamp_no: null }, { 'Website Prepare Scan': ctx })[5], '');
  }
});
test('Stamp: Decide -- duplicate in the term -> auto_reject duplicate_stamp first, reused_image dropped; stamp kept on clean pages', async () => {
  // clean page: the stamp travels to the raw row (gemini)
  const clean = await decide(identity({ duplicate_stamp_exists: false, duplicate_stamp_of: null }));
  assert.strictEqual(clean.flagged, false);
  assert.strictEqual(clean.stamp_no, '0100005');
  assert.strictEqual(clean.stamp_source, 'gemini+python');
  assert.strictEqual(clean.auto_reject, null);
  assert.strictEqual(clean.partial_data.stamp_status, 'ok');
  // duplicate (and a reused image, which never goes with it)
  const run2 = async (ctxExtra, recExtra = {}) => (await run('Website Decide', formMatch(), {
    'Website Prepare Scan': { ...CTX, ...ctxExtra }, 'Website Reconcile OCR OMR': { ...REC, ...recExtra },
    'Website Identity Check': identity({ duplicate_stamp_exists: true, duplicate_stamp_of: DUP }),
  }))[0].json;
  const d = await run2({ reused_image: { batch_id: BATCH, status: 'success' } });
  assert.strictEqual(d.flagged, true);
  assert.deepStrictEqual(d.review_reasons, ['duplicate_stamp']);
  assert.strictEqual(d.auto_reject, 'duplicate_stamp');
  assert.deepStrictEqual(d.duplicate_of, DUP);
  assert.deepStrictEqual(d.partial_data.duplicate_of, DUP);
  assert.match(d.review_note, /^This form was already scanned: stamp 0100005 is already stored for this term \(saved 2026-10-01\)\./);
  assert.match(d.partial_data.summary, / — discarded: duplicate_stamp$/);
  // other reasons stay after it; a JSON-string DUP (driver) is parsed
  const d2 = await run2({}, { student_id: '', review_reasons: ['omr_agreement_below_30'] });
  assert.deepStrictEqual(d2.review_reasons, ['duplicate_stamp', 'omr_agreement_below_30', 'student_id_missing']);
  const d3 = (await run('Website Decide', formMatch(), { 'Website Prepare Scan': CTX, 'Website Reconcile OCR OMR': REC,
    'Website Identity Check': identity({ duplicate_stamp_exists: true, duplicate_stamp_of: JSON.stringify(DUP) }) }))[0].json;
  assert.deepStrictEqual(d3.duplicate_of, DUP);
  // the identity check is only trusted for a page with one readable stamp
  const d4 = await run2({}, { stamp_status: 'missing', stamp_no: null, review_reasons: ['stamp_missing'] });
  assert.deepStrictEqual(d4.review_reasons, ['stamp_missing']);
  assert.strictEqual(d4.auto_reject, null);
  assert.strictEqual(d4.stamp_no, null);
});
// Website page with a duplicate stamp, end to end through the shared flagged path and the answer.
async function websiteDuplicate(rec = REC) {
  const d = (await run('Website Decide', formMatch(), { 'Website Prepare Scan': CTX, 'Website Reconcile OCR OMR': rec,
    'Website Identity Check': identity({ duplicate_stamp_exists: true, duplicate_stamp_of: DUP }) }))[0].json;
  const wrec = (await run('Website Flagged Record', d, { 'Website Prepare Scan': CTX, 'Website Reconcile OCR OMR': rec }))[0].json;
  const q = (await run('build flagged record1', {}, { 'Website Flagged Record': wrec, 'Save scan error image': [{ id: 'img1' }] }))[0].json;
  return { d, wrec, q };
}
function assertAutoRejected(q, extraReasons = []) {
  assert.deepStrictEqual(Object.keys(q).filter((k) => !DISCARD_COLUMNS.includes(k)), []);
  assert.strictEqual(q.status, 'discarded');
  assert.strictEqual(q.review_action, 'rejected');
  assert.strictEqual(q.validation_status, 'flagged');
  assert.deepStrictEqual(q.review_reasons, ['duplicate_stamp', ...extraReasons]);
  assert.strictEqual(q.stamp_no, '0100005');
  assert.strictEqual(q.stamp_source, 'gemini+python');
  assert.deepStrictEqual(q.partial_data.duplicate_of, DUP);
  assert.strictEqual(q.partial_data.stamp_status, 'ok');
  const c = q.review_correction;
  assert.deepStrictEqual([c.action, c.auto, c.rule, c.reviewed_by, c.reviewed_by_name],
    ['rejected', true, 'duplicate_stamp', null, 'Automatic rule']);
  assert.strictEqual(c.note, 'This form was already scanned (stamp 0100005), saved 2026-10-01, so this copy was discarded automatically and is not counted.');
  assert.ok(!Number.isNaN(Date.parse(c.at)));
  assert.deepStrictEqual(fcmParams()(q), [null], 'no staff push for an auto-rejected form');
}
test('Stamp (website, identity check): duplicate -> queue row discarded/rejected (rule duplicate_stamp), answer flagged + auto_rejected, no push', async () => {
  const { wrec, q } = await websiteDuplicate();
  assert.strictEqual(wrec.auto_reject, 'duplicate_stamp');
  assert.strictEqual(wrec.auto_discard, false);
  assertAutoRejected(q);
  const evalBody = respondExpr();
  const ok = assertContract([{ json: JSON.parse(evalBody({}, { 'Website Flagged Record': wrec })) }], 'flagged');
  assert.deepStrictEqual(ok.review_reasons, ['duplicate_stamp']);
  assert.strictEqual(ok.auto_rejected, 'duplicate_stamp');
  assert.strictEqual(ok.stamp_no, '0100005');
  assert.strictEqual(ok.message, STAMP_DUP_MESSAGE);
  // queue insert failed: retryable, never 'auto_rejected'
  const failed = JSON.parse(evalBody({ error: 'HTTP 500' }, { 'Website Flagged Record': wrec }));
  assert.ok(failed.review_reasons.includes('internal_error_not_saved'));
  assert.strictEqual(failed.auto_rejected, null);
  assert.match(failed.message, /^NOT SAVED/);
  // every other flagged page: auto_rejected null, stamp_no = the stamp read on the page
  const plain = (await run('Website Flagged Record', await decide(identity(), REC_DISAGREE()), { 'Website Prepare Scan': CTX }))[0].json;
  const pr = assertContract([{ json: JSON.parse(evalBody({}, { 'Website Flagged Record': plain })) }], 'flagged');
  assert.strictEqual(pr.auto_rejected, null);
  assert.strictEqual(pr.stamp_no, '0100005');
  const pq = (await run('build flagged record1', {}, { 'Website Flagged Record': plain, 'Save scan error image': [{ id: 'i' }] }))[0].json;
  assert.strictEqual(pq.status, 'pending');
  assert.strictEqual(pq.stamp_no, '0100005');
  assert.strictEqual(pq.stamp_source, 'gemini+python');
  assert.deepStrictEqual(pq.partial_data.stamp_candidates, ['0100005']);
  assert.deepStrictEqual(pq.partial_data.stamp_raw, ['0100005']);
  assert.ok(!('duplicate_of' in pq.partial_data));
});
test('Stamp (website): duplicate beats the min-answered auto-discard', async () => {
  const rec = await answeredOnly(3, (py, g) => reconcile(py, g));
  assert.strictEqual(rec.auto_discard, true);
  const { wrec, q } = await websiteDuplicate(rec);
  assert.strictEqual(wrec.auto_discard, false);
  assertAutoRejected(q, ['too_few_answers_3_of_20']);
  assert.strictEqual(JSON.parse(respondExpr()({}, { 'Website Flagged Record': wrec })).message, STAMP_DUP_MESSAGE);
});
test('Stamp (website, race): Website Raw Upsert says duplicate_stamp -> Raw Stored? false -> same auto-reject', async () => {
  assert.deepStrictEqual(outs('Website Raw Stored?', 1), ['Website Flagged Record']);
  assert.strictEqual(ifTrue('Website Raw Stored?', { saved: false, reason: 'duplicate_stamp', duplicate_of: DUP }), false);
  const clean = await decide(identity({ duplicate_stamp_exists: false, duplicate_stamp_of: null }));
  const wrec = (await run('Website Flagged Record', { saved: false, reason: 'duplicate_stamp', duplicate_of: DUP }, {
    'Website Prepare Scan': CTX, 'Website Decide': clean, 'Website Reconcile OCR OMR': REC, 'Website Identity Check': identity(),
  }))[0].json;
  assert.strictEqual(wrec.auto_reject, 'duplicate_stamp');
  assert.deepStrictEqual(wrec.review_reasons, ['duplicate_stamp']);
  assert.match(wrec.review_note, /^This form was already scanned: stamp 0100005/);
  const q = (await run('build flagged record1', {}, { 'Website Flagged Record': wrec, 'Save scan error image': [{ id: 'img1' }] }))[0].json;
  assertAutoRejected(q);
  assert.strictEqual(JSON.parse(respondExpr()({}, { 'Website Flagged Record': wrec })).auto_rejected, 'duplicate_stamp');
  // the existing duplicate_student race is unchanged
  const ds = (await run('Website Flagged Record', { saved: false, reason: 'duplicate_student' }, {
    'Website Prepare Scan': CTX, 'Website Decide': clean, 'Website Reconcile OCR OMR': REC, 'Website Identity Check': identity(),
  }))[0].json;
  assert.deepStrictEqual(ds.review_reasons, ['duplicate_student_id']);
  assert.strictEqual(ds.auto_reject, null);
});
test('Stamp (phone): reconcile -> Phone Stamp Check -> Phone Stamp Verdict -> If31; parameters; verdict outcomes', async () => {
  assert.deepStrictEqual(outs('reconcile ocr and omr1'), ['Phone Stamp Check']);
  assert.deepStrictEqual(outs('Phone Stamp Check'), ['Phone Stamp Verdict']);
  assert.deepStrictEqual(outs('Phone Stamp Verdict'), ['If31']);
  const chk = wf.nodes.find((x) => x.name === 'Phone Stamp Check');
  assert.strictEqual(chk.onError, 'continueRegularOutput');
  assert.strictEqual(chk.alwaysOutputData, true);
  assert.strictEqual(chk.credentials.postgres.name, 'Postgres account');
  const r = await phoneReconcile(python());
  assert.deepStrictEqual(exprValue('Phone Stamp Check', {}, { 'reconcile ocr and omr1': r }), ['', '0100005', 'phone-1']);
  const none = await phoneReconcile(python({ stamp: pyStamp([]) }),
    geminiResp({ stamp_numbers: [], stamp_numbers_page: [], stamp_reads_tiles: [] }));
  assert.deepStrictEqual(exprValue('Phone Stamp Check', {}, { 'reconcile ocr and omr1': none }), ['', '', 'phone-1']);
  const verdict = async (rec, check) => (await run('Phone Stamp Verdict', check, { 'reconcile ocr and omr1': rec }))[0].json;
  // not a duplicate -> unchanged, still clean (If31 true)
  const v0 = await verdict(r, { duplicate_stamp_exists: false, duplicate_stamp_of: null });
  assert.strictEqual(v0.needs_review, false);
  assert.deepStrictEqual(v0.review_reasons, []);
  assert.strictEqual(v0._stage, 'phone_stamp_checked');
  // check failed (error item / empty item) -> review, never credited unchecked
  for (const bad of [{ error: { message: 'connection refused' } }, {}]) {
    const v1 = await verdict(r, bad);
    assert.strictEqual(v1.needs_review, true);
    assert.deepStrictEqual(v1.review_reasons, ['stamp_check_failed']);
    assert.strictEqual(v1.validation_status, 'flagged');
  }
  // no stamp: the check result does not matter
  const v2 = await verdict(none, {});
  assert.deepStrictEqual(v2.review_reasons, ['stamp_missing']);
  // duplicate -> auto-reject
  const v3 = await verdict(r, { duplicate_stamp_exists: true, duplicate_stamp_of: DUP });
  assert.strictEqual(v3.needs_review, true);
  assert.strictEqual(v3.auto_reject, 'duplicate_stamp');
  assert.deepStrictEqual(v3.review_reasons, ['duplicate_stamp']);
  assert.deepStrictEqual(v3.duplicate_of, DUP);
});
test('Stamp (phone): duplicate -> queue row discarded/rejected, phone answer JSON with the message, no push; beats min-answered', async () => {
  const r = await phoneReconcile(python());
  const v = (await run('Phone Stamp Verdict', { duplicate_stamp_exists: true, duplicate_stamp_of: DUP }, { 'reconcile ocr and omr1': r }))[0].json;
  const q = (await run('build flagged record1', {}, { 'Phone Stamp Verdict': v, 'reconcile ocr and omr1': r,
    'Save scan error image': [{ id: 'i' }] }))[0].json;
  assertAutoRejected(q);
  assert.strictEqual(q.user_id, STAFF);
  assert.strictEqual(q.term_id, TERM);
  const ans = respondExpr()({}, { 'build flagged record1': q });
  assert.deepStrictEqual(JSON.parse(ans), { status: 'discarded', flagged: true, auto_rejected: 'duplicate_stamp', task_id: 'phone-1',
    stamp_no: '0100005', duplicate_of: DUP, review_reasons: ['duplicate_stamp'], message: STAMP_DUP_MESSAGE });
  // the phone's other answers keep the original fixed text
  const pend = (await run('build flagged record1', {}, { 'reconcile ocr and omr1': { ...r, review_reasons: ['x'] },
    'Save scan error image': [{ id: 'i' }] }))[0].json;
  assert.strictEqual(respondExpr()({}, { 'build flagged record1': pend }), '{{ JSON.stringify($json) }}');
  assert.strictEqual(respondExpr()({ error: 'HTTP 409' }, { 'build flagged record1': q }), '{{ JSON.stringify($json) }}');
  // min-answered + duplicate: the duplicate wins
  const few = await answeredOnly(3, (py, g) => phoneReconcile(py, g));
  const vf = (await run('Phone Stamp Verdict', { duplicate_stamp_exists: true, duplicate_stamp_of: DUP }, { 'reconcile ocr and omr1': few }))[0].json;
  assert.strictEqual(vf.auto_discard, false);
  const qf = (await run('build flagged record1', {}, { 'Phone Stamp Verdict': vf, 'Save scan error image': [{ id: 'i' }] }))[0].json;
  assertAutoRejected(qf, ['too_few_answers_3_of_20']);
  // a phone queue row without a duplicate carries the read stamp; a Gemini failure has none
  const pq = (await run('build flagged record1', {}, { 'Phone Stamp Verdict': { ...v, auto_reject: undefined, review_reasons: ['x'] },
    'Save scan error image': [{ id: 'i' }] }))[0].json;
  assert.strictEqual(pq.status, 'pending');
  assert.strictEqual(pq.stamp_no, '0100005');
  assert.strictEqual(pq.partial_data.stamp_status, 'ok');
  const gfail = (await run('Handle Gemini failure', { error: { message: 'timeout' } }, {
    'SAST image from phone': PHONE_HOOK, 'image to ocr and omr python': python() }))[0].json;
  const gq = (await run('build flagged record1', {}, { 'Handle Gemini failure': gfail, 'Save scan error image': [{ id: 'i' }] }))[0].json;
  assert.strictEqual(gq.stamp_no, null);
  assert.strictEqual(gq.stamp_source, null);
  assert.strictEqual(gq.partial_data.stamp_status, 'unreadable');
  assert.deepStrictEqual(gq.review_reasons, ['ai_validation_failed', 'stamp_unreadable']);
});
test('Stamp: raw rows -- phone/website rows carry the stamp (gemini); sheet rows never get the key; corrections map body.stamp_no', async () => {
  // phone clean scan: Map to Target Format3 -> Code in JavaScript
  const r = await phoneReconcile(python());
  const fr = { m1: 5, p1: 4 };
  const mapped = (await run('Map to Target Format3', { current_term_id: TERM }, { '2. Format Final Results1': fr,
    'reconcile ocr and omr1': r, 'get instructor id6': { instructor_id: INSTR }, 'get subject id4': { subject_id: SUBJ } }))[0].json;
  assert.strictEqual(mapped.stamp_no, '0100005');
  assert.strictEqual(mapped.stamp_source, 'gemini+python');
  const row = (await run('Code in JavaScript', null, { 'SAST image from phone': { body: { user_id: STAFF } } }, [mapped]))[0].json;
  assert.strictEqual(row.stamp_no, '0100005');
  assert.strictEqual(row.stamp_source, 'gemini+python');
  // Google Sheet row: no stamp key at all (an upsert never clears a stamp)
  const sheet = (await run('Code in JavaScript', null, { 'SAST image from phone': { body: { user_id: STAFF } } },
    [{ instructor_ID: INSTR, true_subject_id: SUBJ, current_term_id: TERM, 'Student ID': 'A1', M1: 4 }]))[0].json;
  assert.ok(!('stamp_no' in sheet) && !('stamp_source' in sheet));
  // corrections: body.stamp_no / body.stamp_source
  const cases = [
    [{ stamp_no: '0100005', stamp_source: 'staff' }, '0100005', 'staff'],
    [{ stamp_no: '0100005', stamp_source: 'override' }, '0100005', 'override'],
    [{ stamp_no: ' 0100005 ', stamp_source: 'gemini' }, '0100005', 'staff'],   // anything else -> staff
    [{ stamp_no: '0100005' }, '0100005', 'staff'],
    [{ stamp_no: '' }, null, null],
    [{ stamp_no: null, stamp_source: 'staff' }, null, null],
    [{ stamp_no: '01-00005' }, null, null],                                   // not digits only -> no stamp
    [{ stamp_no: '12' }, null, null],
    [{}, null, null],                                                        // older client
  ];
  for (const [extra, no, src] of cases) {
    const { mapped: m } = await correctionMap(extra);
    const tag = JSON.stringify(extra);
    assert.strictEqual(m.stamp_no, no, tag);
    assert.strictEqual(m.stamp_source, src, tag);
    assert.strictEqual(m.raw.stamp_no, no, tag);
    const cr = (await run('Code in JavaScript', null, { 'sast-manual-correction1': { body: {} } }, [m]))[0].json;
    assert.strictEqual(cr.stamp_no, no, `${tag}: raw row`);
    assert.strictEqual(cr.stamp_source, src, `${tag}: raw row`);
  }
});
test('Stamp (corrections): check against raw for the term before Mark Queue Validated1; duplicate -> HTTP 409, nothing saved', async () => {
  assert.deepStrictEqual(outs('Map target'), ['Correction Ids Found?']);
  assert.deepStrictEqual(outs('Correction Ids Found?', 0), ['Correction Stamp Check']);
  assert.deepStrictEqual(outs('Correction Stamp Check'), ['Correction Stamp Duplicate?']);
  assert.deepStrictEqual(outs('Correction Stamp Duplicate?', 0), ['Correction Duplicate Stamp Response']);
  assert.deepStrictEqual(outs('Correction Stamp Duplicate?', 1), ['Mark Queue Validated1']);
  assert.deepStrictEqual(outs('Correction Duplicate Stamp Response'), [], 'nothing after the 409 answer');
  const chk = wf.nodes.find((x) => x.name === 'Correction Stamp Check');
  assert.strictEqual(chk.onError, 'continueRegularOutput');
  assert.strictEqual(chk.alwaysOutputData, true);
  const { mapped } = await correctionMap({ correction_source: 'website_review', stamp_no: '0100005', stamp_source: 'staff' });
  assert.deepStrictEqual(exprValue('Correction Stamp Check', mapped, {}), [TERM, '0100005', 't1']);
  const { mapped: noStamp, CURRENT } = await correctionMap({});
  assert.deepStrictEqual(exprValue('Correction Stamp Check', noStamp, {}), [CURRENT, '', 't1']);
  assert.strictEqual(ifTrue('Correction Stamp Duplicate?', { duplicate_stamp_exists: true, duplicate_stamp_of: DUP }), true);
  assert.strictEqual(ifTrue('Correction Stamp Duplicate?', { duplicate_stamp_exists: false }), false);
  // fail safe: a check that could not run stops a correction that carries a stamp (nothing saved),
  // but never one without a stamp (there is nothing to check)
  const $ = (ref) => ({ first: () => ({ json: { 'Map target': mapped }[ref] }) });
  const $noStamp = (ref) => ({ first: () => ({ json: { 'Map target': noStamp }[ref] }) });
  assert.strictEqual(ifTrue('Correction Stamp Duplicate?', { error: 'db down' }, $), true, 'a failed check blocks a correction with a stamp');
  assert.strictEqual(ifTrue('Correction Stamp Duplicate?', { error: 'db down' }, $noStamp), false, 'a failed check never blocks a correction without a stamp');
  const resp = wf.nodes.find((x) => x.name === 'Correction Duplicate Stamp Response');
  assert.strictEqual(resp.type, 'n8n-nodes-base.respondToWebhook');
  assert.strictEqual(resp.parameters.respondWith, 'json');
  const code = /^=\{\{([\s\S]*)\}\}$/.exec(String(resp.parameters.options.responseCode).trim());
  // eslint-disable-next-line no-new-func
  const codeOf = (j) => new Function('$json', `return (${code[1]});`)(j);
  assert.strictEqual(codeOf({ duplicate_stamp_exists: true, duplicate_stamp_of: DUP }), 409);
  assert.strictEqual(codeOf({ error: 'db down' }), 503);
  const m = /^=\{\{([\s\S]*)\}\}$/.exec(resp.parameters.responseBody.trim());
  // eslint-disable-next-line no-new-func
  assert.deepStrictEqual(JSON.parse(new Function('$', '$json', `return (${m[1]});`)($, { error: 'db down' })),
    { success: false, code: 'stamp_check_failed',
      message: 'The stamp check could not run, so nothing was saved. Try again in a moment.', duplicate_of: null });
  // eslint-disable-next-line no-new-func
  const ans = JSON.parse(new Function('$', '$json', `return (${m[1]});`)($, { duplicate_stamp_exists: true, duplicate_stamp_of: DUP }));
  assert.deepStrictEqual(ans, { success: false, code: 'duplicate_stamp',
    message: 'This form was already scanned: stamp 0100005 is already stored for this term (saved 2026-10-01). Nothing was saved.',
    duplicate_of: DUP });
  // success path: the queue row also gets the stamp (only when the caller sent the stamp_no key)
  const mk = wf.nodes.find((x) => x.name === 'Mark Queue Validated1');
  assert.match(mk.parameters.query, /stamp_no = CASE WHEN \$4 = 'true' THEN NULLIF\(\$2, ''\) ELSE stamp_no END/);
  const hook = (body) => ({ 'sast-manual-correction1': { body }, 'Map target': mapped });
  assert.deepStrictEqual(exprValue('Mark Queue Validated1', {}, hook({ failed_scan_id: 'f1', stamp_no: '0100005' })),
    ['f1', '0100005', 'staff', 'true']);
  assert.deepStrictEqual(exprValue('Mark Queue Validated1', {}, { 'sast-manual-correction1': { body: { failed_scan_id: 'f1' } },
    'Map target': noStamp }), ['f1', '', '', 'false']);
});

// ---------------------------------------------------------------- two readers (2026-10-17)
// A stamp is accepted only when Gemini (final answer, full page, tiles) and the OMR's Python reader
// agree on one number; everything else goes to review; a duplicate auto-reject only for an accepted one.
const gem2 = ({ final = ['0100005'], page = ['0100005'], tiles = ['0100005'] } = {}) => geminiResp({
  stamp_numbers: final, stamp_numbers_page: page, stamp_reads_tiles: tiles.map((n, i) => ({ tile: `T${i + 1}`, number: n })) });
test('Stamp (two readers): every decision path in both reconcilers', async () => {
  const cases = [
    // [tag, python block, gemini, status, stamp_no, hint, why, source]
    ['agree', pyStamp(['0100005']), gem2(), 'ok', '0100005', null, 'agree', 'gemini+python'],
    ['disagree', pyStamp(['0100006']), gem2(), 'unconfirmed', null, '0100005', 'python_differs', null],
    ['python ?', pyStamp(['01?0005']), gem2(), 'unconfirmed', null, '0100005', 'python_incomplete', null],
    ['python read nothing', pyStamp([]), gem2(), 'unconfirmed', null, '0100005', 'python_incomplete', null],
    ['python block missing (old OMR / failed)', null, gem2(), 'unconfirmed', null, '0100005', 'python_not_run', null],
    ['python reader off', pyStamp(['0100005'], { reader: false }), gem2(), 'unconfirmed', null, '0100005', 'python_not_run', null],
    ['python 2 stamp-like, 1 complete', pyStamp(['0100005', '01?0005']), gem2(), 'unconfirmed', null, '0100005', 'python_stamp_like', null],
    ['gemini ?', pyStamp(['0100005']), gem2({ final: ['01?0005'], page: ['01?0005'], tiles: ['01?0005'] }), 'unreadable', null, '0100005', 'gemini_unreadable', null],
    ['gemini nothing, python read', pyStamp(['0100005']), gem2({ final: [], page: [], tiles: [] }), 'unconfirmed', null, '0100005', 'gemini_none_python_read', null],
    ['nothing at all', pyStamp([]), gem2({ final: [], page: [], tiles: [] }), 'missing', null, null, 'none', null],
    ['page differs', pyStamp(['0100005']), gem2({ page: ['0100006'] }), 'unconfirmed', null, '0100005', 'page_differs', null],
    ['page ?', pyStamp(['0100005']), gem2({ page: ['01?0005'] }), 'unconfirmed', null, '0100005', 'page_differs', null],
    ['tiles differ', pyStamp(['0100005']), gem2({ tiles: ['0100006'] }), 'unconfirmed', null, '0100005', 'tiles_differ', null],
    ['tile ?', pyStamp(['0100005']), gem2({ tiles: ['0100?05'] }), 'unconfirmed', null, '0100005', 'tiles_differ', null],
    ['no tile sheet', pyStamp(['0100005'], { sheet: false }), gem2({ tiles: [] }), 'unconfirmed', null, '0100005', 'no_tile_sheet', null],
    ['gemini multiple', pyStamp(['0100005']), gem2({ final: ['0100005', '0200006'] }), 'multiple', null, null, 'gemini_multiple', null],
    ['page multiple', pyStamp(['0100005']), gem2({ page: ['0100005', '0200006'] }), 'multiple', null, null, 'readers_see_several', null],
    ['python multiple', pyStamp(['0100005', '0200006']), gem2(), 'multiple', null, null, 'readers_see_several', null],
    ['format check off by default: 5 digits', pyStamp(['12345']), gem2({ final: ['12345'], page: ['12345'], tiles: ['12345'] }), 'ok', '12345', null, 'agree', 'gemini+python'],
    ['9 digits', pyStamp(['012345678']), gem2({ final: ['012345678'], page: ['012345678'], tiles: ['012345678'] }), 'ok', '012345678', null, 'agree', 'gemini+python'],
    ['length differs', pyStamp(['0100005']), gem2({ final: ['01000050'], page: ['01000050'], tiles: ['01000050'] }), 'unconfirmed', null, '01000050', 'python_differs', null],
  ];
  const REASON = { ok: null, unconfirmed: 'stamp_unconfirmed', unreadable: 'stamp_unreadable', multiple: 'multiple_stamps', missing: 'stamp_missing' };
  for (const [which, rec] of RECONCILERS) {
    for (const [tag, sc, g, status, no, hint, why, source] of cases) {
      const r = await rec(python({ stamp: sc }), g);
      const t = `${which}: ${tag}`;
      assert.strictEqual(r.stamp_status, status, t);
      assert.strictEqual(r.stamp_no, no, t);
      assert.strictEqual(r.stamp_hint, hint, t);
      assert.strictEqual(r.stamp_source, source, t);
      assert.strictEqual(r.stamp_check.why, why, t);
      assert.strictEqual(r.stamp_check.agreed, status === 'ok', t);
      if (REASON[status]) {
        assert.deepStrictEqual(r.review_reasons, [REASON[status]], `${t}: review`);
        assert.strictEqual(r.needs_review, true, t);
      } else {
        assert.deepStrictEqual(r.review_reasons, [], t);
      }
    }
    // the reviewer sees both reads, with the reader's '?' positions
    const q = await rec(python({ stamp: pyStamp(['01?0005']) }), gem2());
    assert.match(q.review_note, /not confirmed by both readers/);
    assert.match(q.review_note, /AI read: 0100005\. Scanner read: 01\?0005 \(\? = a digit it was not sure of\)\./, which);
    assert.deepStrictEqual(q.stamp_check.python, ['01?0005']);
    assert.deepStrictEqual(q.stamp_check.gemini_tiles, ['0100005']);
    const old = await rec(python({ stamp: null }), gem2());
    assert.match(old.review_note, /Scanner read: did not run/);
    const pg = await rec(python({ stamp: pyStamp(['0100005']) }), gem2({ page: ['0100006'] }));
    assert.match(pg.review_note, /AI read: 0100005 \(full page 0100006\)\./);
  }
});
test('Stamp (two readers): format check and the Gemini-only fallback are config switches, both OFF', () => {
  const src = fs.readFileSync(path.join(__dirname, 'website_src', 'stamp.js'), 'utf8');
  assert.match(src, /^const ST_FORMAT_CHECK = false;$/m);
  assert.match(src, /^const ST_ALLOW_GEMINI_ONLY = false;$/m);
  const load = (s) => new Function(`${s}\nreturn stCheckStamp;`)();   // eslint-disable-line no-new-func
  const G = (n) => ({ stamp_numbers: [n], stamp_numbers_page: [n], stamp_reads_tiles: [{ tile: 'T1', number: n }] });
  const fmt = load(src.replace('const ST_FORMAT_CHECK = false;', 'const ST_FORMAT_CHECK = true;')
    .replace('const ST_FORMAT_RULES = [];', "const ST_FORMAT_RULES = [{ length: [7], prefix: ['01', '45'] }];"));
  assert.strictEqual(fmt(G('0100005'), true, pyStamp(['0100005'])).stamp_status, 'ok');
  const bad = fmt(G('9900005'), true, pyStamp(['9900005']));
  assert.deepStrictEqual([bad.stamp_status, bad.stamp_check.why, bad.stamp_hint], ['unconfirmed', 'format', '9900005']);
  assert.strictEqual(fmt(G('01000051'), true, pyStamp(['01000051'])).stamp_check.why, 'format');
  const fb = load(src.replace('const ST_ALLOW_GEMINI_ONLY = false;', 'const ST_ALLOW_GEMINI_ONLY = true;'));
  const f1 = fb({ stamp_numbers: ['0100005'] }, true, undefined);
  assert.deepStrictEqual([f1.stamp_status, f1.stamp_no, f1.stamp_source, f1.stamp_check.why], ['ok', '0100005', 'gemini', 'gemini_only_fallback']);
  // with the reader present the fallback changes nothing: a disagreement still goes to review
  assert.strictEqual(fb(G('0100005'), true, pyStamp(['0100006'])).stamp_status, 'unconfirmed');
  const off = load(src)({ stamp_numbers: ['0100005'] }, true, undefined);
  assert.deepStrictEqual([off.stamp_status, off.stamp_source], ['unconfirmed', null]);
});
test('Stamp (two readers): duplicate auto-reject only for an accepted stamp; an unconfirmed match is a hint (website + phone)', async () => {
  const ctx = { instructor_id: INSTR, subject_id: SUBJ, term_id: TERM, task_id: TASK };
  const unc = await reconcile(python({ stamp: pyStamp(['01?0005']) }), gem2());
  assert.strictEqual(unc.stamp_status, 'unconfirmed');
  // Identity Check $6: the accepted stamp, else the hint
  assert.strictEqual(exprValue('Website Identity Check', unc, { 'Website Prepare Scan': ctx })[5], '0100005');
  assert.strictEqual(exprValue('Website Identity Check', { ...unc, stamp_hint: null }, { 'Website Prepare Scan': ctx })[5], '');
  const d = await decide(identity({ duplicate_stamp_exists: true, duplicate_stamp_of: DUP }), unc);
  assert.strictEqual(d.flagged, true);
  assert.strictEqual(d.auto_reject, null, 'never auto-rejected on an unconfirmed stamp');
  assert.strictEqual(d.stamp_no, null);
  assert.strictEqual(d.stamp_source, null);
  assert.ok(d.review_reasons.includes('stamp_unconfirmed') && !d.review_reasons.includes('duplicate_stamp'));
  assert.match(d.review_note, /Possible duplicate: a form with stamp 0100005 is already stored for this term \(saved 2026-10-01\)/);
  assert.deepStrictEqual(d.partial_data.stamp_duplicate_hint, DUP);
  assert.strictEqual(d.partial_data.stamp_hint, '0100005');
  assert.strictEqual(d.partial_data.stamp_check.why, 'python_incomplete');
  // no duplicate -> no hint note
  const d0 = await decide(identity({ duplicate_stamp_exists: false, duplicate_stamp_of: null }), unc);
  assert.ok(!/Possible duplicate/.test(d0.review_note) && !('stamp_duplicate_hint' in d0.partial_data));
  // the queue row: pending (review), no stamp column, the readers' reads + the hint in partial_data
  const wrec = (await run('Website Flagged Record', d, { 'Website Prepare Scan': CTX, 'Website Reconcile OCR OMR': unc }))[0].json;
  const q = (await run('build flagged record1', {}, { 'Website Flagged Record': wrec, 'Save scan error image': [{ id: 'i' }] }))[0].json;
  assert.strictEqual(q.status, 'pending');
  assert.strictEqual(q.stamp_no, null);
  assert.strictEqual(q.stamp_source, null);
  assert.strictEqual(q.partial_data.stamp_status, 'unconfirmed');
  assert.deepStrictEqual(q.partial_data.stamp_check.python, ['01?0005']);
  assert.deepStrictEqual(q.partial_data.stamp_duplicate_hint, DUP);
  // accepted stamp + duplicate -> auto-reject (unchanged), source gemini+python
  const acc = await reconcile(python(), gem2());
  const da = await decide(identity({ duplicate_stamp_exists: true, duplicate_stamp_of: DUP }), acc);
  assert.strictEqual(da.auto_reject, 'duplicate_stamp');
  // phone: the hint goes to Phone Stamp Check; a match adds a note, never an auto-reject
  const pu = await phoneReconcile(python({ stamp: pyStamp(['0100006']) }), gem2());
  assert.deepStrictEqual(exprValue('Phone Stamp Check', {}, { 'reconcile ocr and omr1': pu }), ['', '0100005', 'phone-1']);
  const v = (await run('Phone Stamp Verdict', { duplicate_stamp_exists: true, duplicate_stamp_of: DUP }, { 'reconcile ocr and omr1': pu }))[0].json;
  assert.strictEqual(v.auto_reject, undefined);
  assert.strictEqual(v.needs_review, true);
  assert.deepStrictEqual(v.review_reasons, ['stamp_unconfirmed']);
  assert.match(v.review_note, /Possible duplicate: a form with stamp 0100005/);
  assert.deepStrictEqual(v.stamp_duplicate_hint, DUP);
  const pq = (await run('build flagged record1', {}, { 'Phone Stamp Verdict': v, 'Save scan error image': [{ id: 'i' }] }))[0].json;
  assert.strictEqual(pq.status, 'pending');
  assert.strictEqual(pq.stamp_no, null);
  assert.deepStrictEqual(pq.partial_data.stamp_duplicate_hint, DUP);
  assert.strictEqual(pq.partial_data.stamp_check.why, 'python_differs');
  // a failed check on a hint does not add stamp_check_failed (the scan is in review anyway)
  const vf = (await run('Phone Stamp Verdict', {}, { 'reconcile ocr and omr1': pu }))[0].json;
  assert.deepStrictEqual(vf.review_reasons, ['stamp_unconfirmed']);
});
test('Stamp (two readers): Gemini requests attach the tile sheet as the LAST image only when /process sent one', () => {
  const render = (name, nodes) => {
    const $ = (ref) => ({ first: () => ({ json: nodes[ref] }) });
    const src = wf.nodes.find((x) => x.name === name).parameters.jsonBody.replace(/^=/, '');
    // eslint-disable-next-line no-new-func
    return JSON.parse(src.replace(/\{\{([\s\S]*?)\}\}/g, (m, js) => String(new Function('$', `return (${js});`)($))));
  };
  const SHEET = 'QUJD'.repeat(60);
  const kn = { subject_list: 'IT 101 = Intro' };
  const STRICT = 'STAMP READING RULES (strict):\n- Read each digit of the numbering-machine stamp independently.\n';
  for (const [name, omr, base] of [
    ['ai validation ocr and omr', 'image to ocr and omr python', { 'SAST image from phone': { body: { image: 'UEFHRQ==' } }, 'Phone Known Names': kn }],
    ['Website AI Validation', 'Website Python OMR', { 'Website Prepare Scan': { image_base64: 'UEFHRQ==', mime_type: 'image/jpeg', header_image_base64: 'SEVBRA==' },
      'Website Known Names': kn }],
  ]) {
    const nHeader = name === 'Website AI Validation' ? 1 : 0;
    const withSheet = render(name, { ...base, [omr]: { stamp_candidates: { ...pyStamp(), tile_sheet_b64: SHEET } } });
    const parts = withSheet.contents[0].parts;
    assert.strictEqual(parts.length, 3 + nHeader, name);
    assert.deepStrictEqual(parts[parts.length - 1], { inlineData: { mimeType: 'image/jpeg', data: SHEET } }, `${name}: tile sheet last`);
    assert.strictEqual(parts[1].inlineData.data, 'UEFHRQ==');
    const text = parts[0].text;
    assert.ok(text.includes('Return ONLY valid JSON matching the schema.\n\n--- STAMP TILES (the LAST image) ---\nThe last image is a tile sheet made by our scanner software.')
      && text.endsWith('printed form codes and texts ("SS Form2", "Revision 3", "February 4, 2009").'), `${name}: tile paragraph at the end`);
    assert.ok(text.includes(`${STRICT}`) && text.indexOf(STRICT) < text.indexOf('--- BUBBLE GRID ---')
      && text.indexOf(STRICT) > text.indexOf('--- STAMP ---\nstamp_numbers: '), `${name}: strict rules end the stamp section`);
    const sch = withSheet.generationConfig.responseSchema;
    assert.deepStrictEqual(sch.properties.stamp_numbers_page, { type: 'ARRAY', items: { type: 'STRING' } });
    assert.deepStrictEqual(sch.properties.stamp_reads_tiles, { type: 'ARRAY', items: { type: 'OBJECT', properties: {
      tile: { type: 'STRING' }, number: { type: 'STRING' } }, required: ['tile', 'number'] } });
    assert.ok(/flash-lite/.test(wf.nodes.find((x) => x.name === name).parameters.url), `${name}: model unchanged`);
    // no sheet (old OMR, /process failed, finder found nothing, not base64) -> no part, no tile paragraph
    for (const omrJson of [{ ratings: {} }, { error: 'ECONNREFUSED' }, undefined, { stamp_candidates: pyStamp([], { sheet: false }) },
      { stamp_candidates: { ...pyStamp(), tile_sheet_b64: 'abc"def'.repeat(30) } }, { stamp_candidates: { ...pyStamp(), tile_sheet_b64: 'QUJD' } }]) {
      const b = render(name, { ...base, [omr]: omrJson });
      assert.strictEqual(b.contents[0].parts.length, 2 + nHeader, `${name}: ${String(JSON.stringify(omrJson)).slice(0, 60)}`);
      assert.ok(!b.contents[0].parts[0].text.includes('STAMP TILES'), name);
      assert.ok(b.contents[0].parts[0].text.includes(STRICT), `${name}: strict rules always`);
    }
  }
});

// ---------------------------------------------------------------- AI sentences (ai_sentences.js)
test('AI sentences: both parsers store one sentence per line (array or string input, old paragraph, dedupe, cap 5)', async () => {
  const feedback = [{ index: 1, remark: 'Maayo si sir', subject_id: SUBJ }];
  const base = { evaluation: { interpretation: 'ok', suggestion: 's' }, overall_tone: 'Positive',
    remark_tones: [{ index: 1, tone: 'Positive' }], keywords: [{ word: 'Teaching clarity', new_count: 1 }] };
  const uni = async (positives, improvements) => (await run('Parse Unified AI Result1',
    { text: JSON.stringify({ ...base, evaluation: { ...base.evaluation, positives, improvements } }) },
    { 'Prepare Unified AI Input1': { instructor_ID: INSTR, term_id: TERM, feedback } })).map((i) => i.json)
    .find((r) => r.record_type === 'evaluation');
  let ev = await uni(['clear explanations.', '  Fair grading  ', 'Clear explanations', '- patient with students;', 'a', 'b', 'c'], 'x');
  assert.strictEqual(ev.positive_themes, 'Clear explanations.\nFair grading.\nPatient with students.\nA.\nB.');
  ev = await uni('Clear explanations, strong preparedness, fair grading', '1. Slow down.\n* More examples• Shorter quizzes;');
  assert.strictEqual(ev.positive_themes, 'Clear explanations, strong preparedness, fair grading.', 'old paragraph: one item, commas kept');
  assert.strictEqual(ev.improvement_insights, 'Slow down.\nMore examples.\nShorter quizzes.');
  const ind = async (p, i) => (await run('Add Parse Code node1', { text: JSON.stringify({ overall_tone: 'Positive',
    keywords: [{ word: 'Teaching clarity' }], ai_suggestion: 's', student_interpretation: 'i', positive_themes: p, improvement_insights: i }) },
    { 'Prepare Individual AI Input1': { instructor_ID: INSTR, term_id: TERM, subject_id: SUBJ, new_student_remark: 'ok' } }))
    .map((x) => x.json).find((r) => r.record_type === 'evaluation');
  ev = await ind(['Teaching clarity', 'Explains well.'], 'Needs more examples; Pace is fast');
  assert.strictEqual(ev.positive_themes, 'Teaching clarity.\nExplains well.');
  assert.strictEqual(ev.improvement_insights, 'Needs more examples.\nPace is fast.');
  ev = await ind(undefined, null);
  assert.strictEqual(ev.positive_themes, '');
  assert.strictEqual(ev.improvement_insights, '');
  await assert.rejects(uni([], 'x'), /Empty positives/);
  await assert.rejects(uni('x', ['  ']), /Empty improvements/);
});
test('AI sentences: both prompts ask for 2-5 complete sentences as an array; one identical helper copy per parser', () => {
  for (const n of ['Unified AI Analysis1', 'Individual AI Analysis1']) {
    const t = wf.nodes.find((x) => x.name === n).parameters.text;
    assert.strictEqual(t.split('3 to 5 short, complete sentences').length - 1, 2, n);
    assert.ok(/may hold this as one paragraph or as separate lines/.test(t), n);
  }
  const u = wf.nodes.find((x) => x.name === 'Unified AI Analysis1').parameters.text;
  assert.ok(/"positives": \[/.test(u) && /"improvements": \[/.test(u) && !/evaluation fields are strings/.test(u));
  const re = /\/\/ AI sentences, from website_src\/ai_sentences\.js\n[\s\S]*?\/\/ end of AI sentences\n/;
  const a = re.exec(codeOf('Parse Unified AI Result1'))[0];
  assert.strictEqual(re.exec(codeOf('Add Parse Code node1'))[0], a);
});

// ---------------------------------------------------------------- interpretation summary (ai_interpretation.js)
// Synthetic remarks only; no names.
const NODE = Object.fromEntries(wf.nodes.map((n) => [n.name, n]));
const AIINT_RE = /\/\/ AI interpretation, from website_src\/ai_interpretation\.js\n([\s\S]*?)\/\/ end of AI interpretation\n/;
function aiIntFrom(nodeName) {
  // eslint-disable-next-line no-new-func
  return new Function(`${AIINT_RE.exec(codeOf(nodeName))[1]}\nreturn { aiInterpretation, AI_INTERPRETATION_MAX };`)();
}
const longPara = (n) => Array.from({ length: n }, (_, i) => `Most students say lesson ${i + 1} was clear and well paced.`).join(' ');
test('AI interpretation: one paragraph, whitespace normalised, capped at a sentence end (~1500 chars); identical copy per parser', () => {
  const { aiInterpretation, AI_INTERPRETATION_MAX } = aiIntFrom('Parse Unified AI Result1');
  assert.strictEqual(AI_INTERPRETATION_MAX, 1500);
  assert.strictEqual(aiInterpretation('  Most students\n\n praise the   lessons.\r\n- A few ask for more examples.  '),
    'Most students praise the lessons. A few ask for more examples.');
  assert.strictEqual(aiInterpretation(['Most students are happy.', '  Several want slower pacing. ']),
    'Most students are happy. Several want slower pacing.');
  for (const v of [null, undefined, '', '   ', 'null', 'N/A', {}, ['', null], '...']) assert.strictEqual(aiInterpretation(v), '', String(v));
  const long = longPara(60);
  assert.ok(long.length > 1500);
  const cut = aiInterpretation(long);
  assert.ok(cut.length <= 1500 && cut.length > 1400, `length ${cut.length}`);
  assert.ok(cut.endsWith('was clear and well paced.') && long.startsWith(cut), 'cut at a sentence end');
  const noStop = 'word '.repeat(400).trim();
  const c2 = aiInterpretation(noStop);
  assert.ok(c2.length <= 1501 && c2.endsWith('word.') && !/\s{2}/.test(c2), 'no sentence end: cut at a space, period added');
  assert.strictEqual(aiInterpretation(longPara(3)), longPara(3), 'short text unchanged');
  assert.strictEqual(AIINT_RE.exec(codeOf('Add Parse Code node1'))[0], AIINT_RE.exec(codeOf('Parse Unified AI Result1'))[0]);
});
test('AI interpretation: unified parser cleans/caps it and still throws on empty; single-scan parser keeps the stored one when Gemini returns none', async () => {
  const feedback = [{ index: 1, remark: 'Maayo si sir', subject_id: SUBJ }];
  const uni = async (interpretation) => (await run('Parse Unified AI Result1',
    { text: JSON.stringify({ evaluation: { interpretation, positives: 'a', improvements: 'b', suggestion: 's' },
      overall_tone: 'Positive', remark_tones: [{ index: 1, tone: 'Positive' }], keywords: [] }) },
    { 'Prepare Unified AI Input1': { instructor_ID: INSTR, term_id: TERM, feedback } })).map((i) => i.json)
    .find((r) => r.record_type === 'evaluation');
  assert.strictEqual((await uni('Most students\n praise the lessons.')).student_interpretation, 'Most students praise the lessons.');
  assert.ok((await uni(longPara(60))).student_interpretation.length <= 1500);
  await assert.rejects(uni('  \n '), /Empty interpretation/);
  const ind = async (studentInterpretation, existing) => (await run('Add Parse Code node1', { text: JSON.stringify({
    overall_tone: 'Positive', keywords: [{ word: 'Teaching clarity' }], ai_suggestion: 's',
    student_interpretation: studentInterpretation, positive_themes: ['A.'], improvement_insights: ['B.'] }) },
  { 'Prepare Individual AI Input1': { instructor_ID: INSTR, term_id: TERM, subject_id: SUBJ, new_student_remark: 'ok',
    existing_ai: existing === undefined ? undefined : { student_interpretation: existing } } }))
    .map((x) => x.json).find((r) => r.record_type === 'evaluation');
  assert.strictEqual((await ind('Most students\n\nlike the class.', 'Old summary.')).student_interpretation, 'Most students like the class.');
  assert.strictEqual((await ind('', 'Most students like the class.')).student_interpretation, 'Most students like the class.', 'kept');
  assert.strictEqual((await ind(undefined, 'Kept  summary.')).student_interpretation, 'Kept summary.');
  assert.strictEqual((await ind('   ', undefined)).student_interpretation, '', 'nothing stored either');
  assert.ok((await ind(longPara(60), '')).student_interpretation.length <= 1500);
});
test('Interpretation summary: single-scan path gets previous_remarks (text + tone only) through Merge2 into the prompt', async () => {
  const q = NODE['Get Existing AI Suggestion1'];
  const sql = q.parameters.query;
  assert.ok(sql.includes('FROM (SELECT $1::uuid AS instructor_id, $2::uuid AS term_id) p') && /LEFT JOIN LATERAL/.test(sql),
    'always one row, even without an ai_suggestions row');
  assert.ok(/jsonb_build_object\('remark', x\.remark, 'tone', x\.tone\)/.test(sql) && /LIMIT 100/.test(sql)
    && /ORDER BY x\.created_at DESC/.test(sql) && !/student_id|user_info|first_name/.test(sql), 'remark + tone only, newest first, last 100');
  assert.strictEqual(q.alwaysOutputData, true);
  const m = NODE.Merge2.parameters;
  assert.deepStrictEqual([m.mode, m.combineBy, m.numberInputs], ['combine', 'combineByPosition', 3]);
  assert.deepStrictEqual(outs('Prepare Existing AI1'), ['Merge2']);
  assert.ok(wf.connections['Prepare Existing AI1'].main[0].every((e) => e.index === 2), 'Merge2 input 2 (position kept)');
  const rows = [{ remark: 'Maayo  kaayo\nmo explain.', tone: 'Positive', student_id: '2023-0001', subject_id: SUBJ },
    { remark: '  ', tone: 'Neutral' }, { remark: 'Dugangi ang examples.', tone: 'Critical', created_at: 'x' }];
  const pe = async (row) => (await run('Prepare Existing AI1', row))[0].json;
  let ex = await pe({ instructor_id: INSTR, term_id: TERM, student_interpretation: 'Old.', previous_remarks: rows });
  assert.strictEqual(ex.existing_ai.student_interpretation, 'Old.');
  assert.strictEqual(ex.previous_remarks.length, 3);
  assert.strictEqual((await pe({ previous_remarks: JSON.stringify(rows) })).previous_remarks.length, 3, 'json text parsed');
  ex = await pe({ instructor_id: INSTR, term_id: TERM, ai_suggestion: null, previous_remarks: [] });
  assert.deepStrictEqual(ex.previous_remarks, []);
  assert.strictEqual(ex.existing_ai.overall_tone, 'Neutral');
  assert.deepStrictEqual((await pe({ previous_remarks: 'not json' })).previous_remarks, []);
  // Merge2 combine by position = the three items' fields in one item
  const merged = { wordcloud: [], instructor_name_tokens: ['zorro'], instructor_ID: INSTR, current_term_id: TERM,
    true_subject_id: SUBJ, 'Remarks & Suggestions': 'Paspas kaayo.', ...(await pe({ student_interpretation: 'Old.', previous_remarks: rows })) };
  const inp = (await run('Prepare Individual AI Input1', merged))[0].json;
  assert.deepStrictEqual(inp.previous_remarks, [{ remark: 'Maayo kaayo mo explain.', tone: 'Positive' },
    { remark: 'Dugangi ang examples.', tone: 'Critical' }], 'text + tone only, blanks dropped');
  assert.strictEqual(inp.existing_ai.student_interpretation, 'Old.');
  // render the prompt: previous remarks in their block, no student id, no name tokens
  const t = NODE['Individual AI Analysis1'].parameters.text.replace(/^=/, '');
  // eslint-disable-next-line no-new-func
  const rendered = t.replace(/\{\{([\s\S]*?)\}\}/g, (s, js) => String(new Function('$json', `return (${js});`)(inp)));
  const block = rendered.split('PREVIOUS STUDENT REMARKS\n')[2];
  assert.ok(block && block.includes('"remark":"Dugangi ang examples."'), 'block filled');
  assert.ok(!rendered.includes('2023-0001') && !rendered.includes('zorro') && !rendered.includes(SUBJ), 'no ids or names');
  assert.ok(rendered.indexOf('PREVIOUS STUDENT REMARKS\n=') < rendered.indexOf('NEW STUDENT FEEDBACK\n='));
});
test('Interpretation summary: both prompts ask for one paragraph about ALL students (5-8 sentences, no names, no bullets)', () => {
  const u = NODE['Unified AI Analysis1'].parameters.text;
  const i = NODE['Individual AI Analysis1'].parameters.text;
  for (const [n, t] of [['unified', u], ['individual', i]]) {
    for (const s of ['Write ONE paragraph of 5 to 8 complete sentences (about 120 to 200 words)', '"most students", "several students", "a few students"',
      'Summarise Bisaya/Cebuano and Tagalog comments in English.', 'Never use an instructor name or a student name.',
      'Never quote a vulgar or sexual word', 'No bullet points', 'Do not mention AI, surveys, batches', 'What Your Students Are Saying']) {
      assert.ok(t.includes(s), `${n}: ${s}`);
    }
  }
  assert.ok(u.includes('INTERPRETATION (evaluation.interpretation)') && !u.includes('INSTRUCTOR INTERPRETATION'));
  assert.ok(u.includes('the historical analysis, the historical remarks and the new feedback'));
  assert.ok(u.includes('"interpretation": "One paragraph of 5 to 8 complete sentences summarising what students say.",'));
  assert.ok(u.includes('- interpretation is ONE paragraph of 5 to 8 complete sentences'));
  assert.ok(i.includes('UPDATED OVERALL interpretation of what ALL students are saying') && !i.includes('Write a short interpretation'));
  assert.ok(i.includes('{{ JSON.stringify($json.previous_remarks || []) }}') && i.includes('You have four sources of information'));
  assert.ok(i.includes('"student_interpretation": "One paragraph of 5 to 8 complete sentences summarising what all students say.",'));
  assert.ok(i.includes('Determine the tone of the NEW student feedback.'), 'overall_tone stays about the new student');
});

// ---------------------------------------------------------------- remark tone (ai_tone.js)
const TONE_RE = /\/\/ AI tone, from website_src\/ai_tone\.js\n([\s\S]*?)\/\/ end of AI tone\n/;
test('AI tone: case-insensitive, trimmed; negative / mixed -> Critical; anything else null; identical copy per parser', () => {
  // eslint-disable-next-line no-new-func
  const { aiTone } = new Function(`${TONE_RE.exec(codeOf('Parse Unified AI Result1'))[1]}\nreturn { aiTone };`)();
  for (const [v, want] of [['Positive', 'Positive'], [' positive ', 'Positive'], ['CRITICAL', 'Critical'], ['neutral\n', 'Neutral'],
    ['Negative', 'Critical'], ['mixed', 'Critical'], [' MIXED ', 'Critical'], ['angry', null], ['', null], [null, null],
    [undefined, null], [1, null], ['constructor', null], ['toString', null]]) assert.strictEqual(aiTone(v), want, String(v));
  assert.strictEqual(TONE_RE.exec(codeOf('Add Parse Code node1'))[0], TONE_RE.exec(codeOf('Parse Unified AI Result1'))[0]);
});
test('AI tone: unified parser maps remark_tones / overall_tone (invalid still throws); single-scan parser maps or falls back to Neutral', async () => {
  const feedback = [1, 2, 3].map((index) => ({ index, remark: `remark ${index}`, subject_id: SUBJ }));
  const uni = async (tones, overall = 'Positive') => (await run('Parse Unified AI Result1',
    { text: JSON.stringify({ evaluation: { interpretation: 'Most students are happy.', positives: 'a', improvements: 'b', suggestion: 's' },
      overall_tone: overall, remark_tones: tones.map((tone, i) => ({ index: i + 1, tone })), keywords: [] }) },
    { 'Prepare Unified AI Input1': { instructor_ID: INSTR, term_id: TERM, feedback } })).map((i) => i.json);
  const out = await uni([' POSITIVE ', 'negative', 'Mixed'], ' neutral');
  assert.deepStrictEqual(out.filter((r) => r.record_type === 'student_remark').map((r) => r.tone), ['Positive', 'Critical', 'Critical']);
  assert.strictEqual(out.find((r) => r.record_type === 'evaluation').overall_tone, 'Neutral');
  assert.strictEqual((await uni(['Positive', 'Critical', 'Neutral'], 'NEGATIVE')).find((r) => r.record_type === 'evaluation').overall_tone, 'Critical');
  await assert.rejects(uni(['Positive', 'Angry', 'Neutral']), /Invalid tone for remark 2/);
  await assert.rejects(uni(['Positive', 'Critical', 'Neutral'], 'happy'), /Invalid overall_tone/);
  const ind = async (tone) => (await run('Add Parse Code node1', { text: JSON.stringify({ overall_tone: tone, keywords: [],
    ai_suggestion: 's', student_interpretation: 'i', positive_themes: ['A.'], improvement_insights: ['B.'] }) },
  { 'Prepare Individual AI Input1': { instructor_ID: INSTR, term_id: TERM, subject_id: SUBJ, new_student_remark: 'Sana po bagalan.' } }))
    .map((x) => x.json);
  for (const [tone, want] of [['NEGATIVE', 'Critical'], [' mixed', 'Critical'], ['positive', 'Positive'], ['happy', 'Neutral'], [undefined, 'Neutral']]) {
    const o = await ind(tone);
    assert.strictEqual(o.find((r) => r.record_type === 'evaluation').overall_tone, want, String(tone));
    assert.strictEqual(o.find((r) => r.record_type === 'student_remark').tone, want, String(tone));
  }
  assert.strictEqual((await ind('critical')).find((r) => r.record_type === 'wordcloud').word, 'Needs improvement', 'fallback keyword uses the mapped tone');
});
test('AI tone: both prompts carry the any-language rule, the decision rules and all 15 examples (old examples gone)', () => {
  const ex = [["Maayo kaayo mo explain si ma'am.", 'Positive'], ['Ang galing magturo ni sir, sobrang linaw.', 'Positive'],
    ['Salamat sa pasensya, sir!', 'Positive'], ['Keep it up po!', 'Positive'], ['Very approachable and fair.', 'Positive'],
    ['Muy buen profesor.', 'Positive'], ['Paspas kaayo mo discuss, dili mi ka sabay.', 'Critical'],
    ['Sana po bagalan ang pagtuturo.', 'Critical'], ['Dugangi pa unta ang examples.', 'Critical'],
    ['Maayo ra man siya pero kanunay late.', 'Critical'], ['Galing mo sir, wala kaming natutunan.', 'Critical'],
    ['Hindi po malinaw ang grading.', 'Critical'], ['Mas maayo unta kung naay review before exam.', 'Critical'],
    ['Okay ra.', 'Neutral'], ['Every MWF ang klase.', 'Neutral']];
  for (const n of ['Unified AI Analysis1', 'Individual AI Analysis1']) {
    const t = NODE[n].parameters.text;
    for (const s of ['Hiligaynon', 'Ilocano', 'Taglish and Bislish', 'Translate it to English in your head before deciding.',
      'Never decide from one keyword.', 'unta, sana, dapat, palihug, please, pls', 'Praise plus a specific problem or request:\nCritical.',
      'only a greeting, or too short to judge', 'Emoji only:', 'Positive\nCritical\nNeutral']) assert.ok(t.includes(s), `${n}: ${s}`);
    for (const [r, tone] of ex) assert.ok(t.includes(`"${r}"\n→ ${tone}\n`), `${n}: ${r}`);
    for (const old of ['"His class is every Monday."', '"Okay ra man."', '"Mas maayo unta kung hinay-hinayan ang discussion."', '"Please give more examples."']) {
      assert.ok(!t.includes(old), `${n}: old example ${old} removed`);
    }
  }
  const u = NODE['Unified AI Analysis1'].parameters.text;
  assert.ok(u.includes('Classify EVERY NEW feedback item.') && u.includes('If there is a tie, return Neutral.'), 'unified count rule kept');
  assert.strictEqual(u.split('Never decide from one keyword.').length, 2);
});

// ---------------------------------------------------------------- word cloud words (remark_words.js)
// Synthetic remarks and a synthetic name only.
const RW_RE = /\/\/ remark words, from [^\n]*\n([\s\S]*?)\/\/ end of remark words\n/;
function rwFrom(nodeName) {
  // eslint-disable-next-line no-new-func
  return new Function(`${RW_RE.exec(codeOf(nodeName))[1]}\nreturn { remarkWords, mergeWordCounts, RW_STOPWORDS };`)();
}
test('remark_words: stopwords (EN/Bisaya/Tagalog), short words, digits, name tokens, profanity, placeholders; once per remark', () => {
  const RW = rwFrom('Parse Unified AI Result1');
  const remarks = ['Si Maam Zorro maayo kaayo mutudlo, very PATIENT patient patient 123!',
    "Ang patient nga teacher, naman talaga po. Don't rush.", 'N/A', 'none', 'wala', 'Wala', 'hahaha ok', 'iyot lecture',
    '****** explains clearly', 'Sir Mañana is fine'];
  const out = RW.remarkWords(remarks, ['zorro', 'Mañana'], PF.profaneKeyword);
  const m = Object.fromEntries(out.map((w) => [w.word, w.new_count]));
  assert.strictEqual(m.patient, 2, 'once per remark, two remarks');
  for (const w of ['maayo', 'mutudlo', 'rush', 'lecture', 'explains', 'clearly', 'fine']) assert.strictEqual(m[w], 1, w);
  for (const w of ['zorro', 'manana', 'iyot', 'kaayo', 'very', 'ang', 'nga', 'naman', 'talaga', 'teacher', 'dont', 'none', 'wala',
    'hahaha', 'ok', 'sir', 'maam', 'si', '123', 'n']) assert.strictEqual(m[w], undefined, `${w} dropped`);
  assert.ok(out.every((w) => w.word.length >= 3 && Number.isInteger(w.new_count) && w.new_count >= 1));
  assert.deepStrictEqual(RW.remarkWords(['a b c none wala n/a'], [], null), []);
  assert.deepStrictEqual(RW.remarkWords(null, null), []);
  assert.strictEqual(RW.remarkWords(['alpha beta gamma delta'], [], null, 2).length, 2, 'limit');
  assert.deepStrictEqual(RW.mergeWordCounts([{ word: 'Patient', new_count: 2 }, { word: 'Teaching clarity', new_count: 1 }],
    [{ word: 'patient', new_count: 3 }, { word: 'clear', new_count: 1 }], 'new_count'),
  [{ word: 'Patient', new_count: 5 }, { word: 'Teaching clarity', new_count: 1 }, { word: 'clear', new_count: 1 }],
  'same text (any case) merges into one entry, the theme spelling wins');
});
test('remark_words: one identical copy in both remark parse nodes, after the sexual-word filter', () => {
  const a = RW_RE.exec(codeOf('Parse Unified AI Result1'))[0];
  const b = RW_RE.exec(codeOf('Add Parse Code node1'))[0];
  assert.strictEqual(a, b);
  for (const n of ['Parse Unified AI Result1', 'Add Parse Code node1']) {
    const c = codeOf(n);
    assert.ok(c.indexOf('// end of sexual-word filter') < c.indexOf('// remark words, from'), n);
    assert.strictEqual(c.split('// remark words, from').length, 2, `${n}: once`);
  }
});
test('Word cloud words: unified path merges words into the theme items (one row per word)', async () => {
  const feedback = [{ index: 1, remark: 'Patient teacher, patient explains', subject_id: SUBJ },
    { index: 2, remark: 'Zorro is patient. Maayo kaayo!', subject_id: SUBJ }];
  const ai = { evaluation: { interpretation: 'ok', positives: 'clear', improvements: 'more', suggestion: 'more examples' },
    overall_tone: 'Positive', remark_tones: [{ index: 1, tone: 'Positive' }, { index: 2, tone: 'Positive' }],
    keywords: [{ word: 'Patient', new_count: 2 }, { word: 'Teaching clarity', new_count: 1 }] };
  const base = { instructor_ID: INSTR, term_id: TERM, feedback };
  const wc = async (extra) => (await run('Parse Unified AI Result1', { text: JSON.stringify(ai) },
    { 'Prepare Unified AI Input1': { ...base, ...extra } })).map((i) => i.json).filter((r) => r.record_type === 'wordcloud');
  const withWords = await wc({ instructor_name_tokens: ['zorro'] });
  const m = Object.fromEntries(withWords.map((r) => [r.word, r.new_count]));
  assert.strictEqual(m.Patient, 4, 'theme 2 + word 2, merged into one row');
  assert.strictEqual(m['Teaching clarity'], 1, 'the AI theme is kept as is');
  assert.strictEqual(m.explains, 1);
  assert.strictEqual(m.maayo, 1);
  assert.strictEqual(m.zorro, undefined, 'instructor name token dropped');
  assert.strictEqual(new Set(withWords.map((r) => r.word.toLowerCase())).size, withWords.length, 'one row per word');
  assert.ok(withWords.every((r) => r.instructor_id === INSTR && r.term_id === TERM && Number.isInteger(r.new_count)));
  // no name tokens (lookup missing) -> themes only, never words that could be a name
  assert.deepStrictEqual((await wc({})).map((r) => r.word), ['Patient', 'Teaching clarity']);
  assert.deepStrictEqual((await wc({ instructor_name_tokens: null })).map((r) => r.word), ['Patient', 'Teaching clarity']);
});
test('Word cloud words: unified path keeps at most 60 words next to the themes', async () => {
  const letters = 'abcdefghijklmnopqrstuvwxyz';
  const text = Array.from({ length: 100 }, (_, i) => `zebra${letters[i % 26]}${letters[Math.floor(i / 26)]}x`).join(' ');
  const feedback = [{ index: 1, remark: text, subject_id: SUBJ }];
  const ai = { evaluation: { interpretation: 'ok', positives: 'p', improvements: 'i', suggestion: 's' }, overall_tone: 'Neutral',
    remark_tones: [{ index: 1, tone: 'Neutral' }], keywords: [{ word: 'Teaching clarity', new_count: 1 }] };
  const out = (await run('Parse Unified AI Result1', { text: JSON.stringify(ai) },
    { 'Prepare Unified AI Input1': { instructor_ID: INSTR, term_id: TERM, feedback, instructor_name_tokens: [] } }))
    .map((i) => i.json).filter((r) => r.record_type === 'wordcloud');
  assert.strictEqual(out.length, 61);
});
test('Word cloud words: individual path merges words into the theme items (count field), fallback theme still works', async () => {
  const wc = async (ind, remark, extra) => (await run('Add Parse Code node1', { text: JSON.stringify(ind) },
    { 'Prepare Individual AI Input1': { instructor_ID: INSTR, term_id: TERM, subject_id: SUBJ, new_student_remark: remark, ...extra } }))
    .map((i) => i.json).filter((r) => r.record_type === 'wordcloud');
  const ai = { overall_tone: 'Positive', keywords: [{ word: 'Patient', new_count: 1 }, { word: 'Teaching clarity', new_count: 1 }],
    ai_suggestion: 's', student_interpretation: 'i', positive_themes: ['p'], improvement_insights: ['i'] };
  const out = await wc(ai, 'Zorro is patient and explains well', { instructor_name_tokens: ['zorro'] });
  assert.deepStrictEqual(Object.fromEntries(out.map((r) => [r.word, r.count])),
    { Patient: 2, 'Teaching clarity': 1, explains: 1, well: 1 });
  assert.ok(out.every((r) => r.instructor_id === INSTR && r.term_id === TERM));
  const none = await wc({ ...ai, keywords: [] }, 'Zorro is patient', { instructor_name_tokens: ['zorro'] });
  assert.deepStrictEqual(Object.fromEntries(none.map((r) => [r.word, r.count])), { 'Positive feedback': 1, patient: 1 },
    'fallback theme plus the real word');
  assert.deepStrictEqual((await wc(ai, 'Zorro is patient', {})).map((r) => [r.word, r.count]),
    [['Patient', 1], ['Teaching clarity', 1]], 'no tokens -> themes only, exactly as before');
});
test('Word cloud words: name tokens come from the database to the parsers, never to an AI node', () => {
  const by = (n) => wf.nodes.find((x) => x.name === n);
  for (const n of ['Get Previous Instructor Analysis1', 'Get the old wordcloud1']) {
    const q = by(n).parameters.query;
    assert.ok(/instructor_name_tokens/.test(q) && /public\.user_info/.test(q) && /\$1::uuid/.test(q), n);
  }
  for (const n of ['Prepare Unified AI Input1', 'Prepare Existing Wordcloud1', 'Prepare Individual AI Input1']) {
    assert.ok(/instructor_name_tokens/.test(codeOf(n)), n);
  }
  for (const n of ['Unified AI Analysis1', 'Individual AI Analysis1']) {
    assert.ok(!/name_tokens|first_name|last_name|user_info/.test(JSON.stringify(by(n).parameters)), `${n}: no name tokens in the AI prompt`);
  }
});
test('Gemini free tier: Wait1 paces the remark loop (>= 6 s), AI nodes retry, failures skip the instructor via Wait1', () => {
  const by = (n) => wf.nodes.find((x) => x.name === n);
  const w = by('Wait1').parameters;
  assert.strictEqual(w.unit, 'seconds');
  assert.ok(w.amount >= 6);
  for (const n of ['Unified AI Analysis1', 'Individual AI Analysis1']) {
    const nd = by(n);
    assert.ok(nd.retryOnFail === true && nd.maxTries === 3 && nd.waitBetweenTries === 5000, n);
  }
  for (const n of ['Unified AI Analysis1', 'Parse Unified AI Result1']) {
    assert.strictEqual(by(n).onError, 'continueErrorOutput', n);
    // + the sheet import job's failure recorder (migration 018); Wait1 still advances the loop
    assert.deepStrictEqual(wf.connections[n].main[1].map((c) => c.node), ['Wait1', 'Describe Import Failure'], n);
  }
  assert.strictEqual(by('Get Previous Instructor Analysis1').onError, 'continueRegularOutput');
});
test('Word cloud words: Prepare Existing Wordcloud1 passes the tokens, also when the instructor has no wordcloud rows yet', async () => {
  const out = (await run('Prepare Existing Wordcloud1', undefined, {}, [{ word: null, count: null, instructor_name_tokens: ['zorro', 'santos'] }]))[0].json;
  assert.deepStrictEqual(out.wordcloud, []);
  assert.deepStrictEqual(out.instructor_name_tokens, ['zorro', 'santos']);
  const out2 = (await run('Prepare Existing Wordcloud1', undefined, {}, [{ word: 'Patient', count: '3', instructor_name_tokens: ['zorro'] }]))[0].json;
  assert.deepStrictEqual(out2.wordcloud, [{ word: 'Patient', count: 3 }]);
});

// ---- Google Sheet import: header matching (old and new layout), no database change
const NEW_ITEM_HEADERS = ['1. gives reasonable course/subject assignments', '2. earns appreciation and kind attention from the students',
  '3. gives orientation about the subject and how the students are evaluated', '4. gives tests and/or projects which are within the objectives of the course',
  '5. shows deep interest and concern in assisting the students', '6. manifests sympathetic insight into students’ feelings',
  '7. checks and records test papers/term papers', '8. is on time and regular in meeting the class', '9. apportions fair subject/course assignments',
  '10. sustains the attention of the class for the whole period', '11. presents lesson clearly, methodically, and substantially',
  '12. motivates the students to learn', '13. facilitates learning with the application of appropriate educational methods and techniques',
  '14. shows mastery of the lesson', '15. is ready for the class', '16. inspires students’ self-reliance in their quest for knowledge',
  '17. knows when the students have difficulty understanding the lesson and find ways to make it easy', '18. integrates values into the lesson',
  '19. speaks the language of instruction (english or filipino) clearly and fluently', '20. delivers thought provoking questions'];
const SH_INSTR = 'Name of the Instructor/Professor\n(Family Name, First Name,  Middle I.)';
const SH_COURSE = 'Course Handled by the above-mentioned Instructor/Professor (Ex. CC214 DATA STRUCTURES AND ALGORITHM)';
function newSheetRow(over = {}, ratings = {}) {
  const r = { 'ID Number': 7000001, 'Section': 'BIT-3A', [SH_INSTR]: 'Ana Reyes-Test',
    '': 'Ana Reyes-Test - MOBAPP 330 MOBILE APPLICATION DEVELOPMENT',
    [SH_COURSE]: 'MOBAPP 330 MOBILE APPLICATION DEVELOPMENT', 'Comments': 'MAAYO MAGTURO' };
  NEW_ITEM_HEADERS.forEach((h, i) => { r[(i % 2 ? h.toUpperCase() : h)] = ratings[i + 1] === undefined ? 3 : ratings[i + 1]; });
  return { ...r, ...over };
}
async function normRows(rows) {
  return (await run('Normalize Sheet Rows', undefined, {}, rows)).map((x) => x.json);
}
test('Sheet import (new layout): columns found by normalised header, not position', async () => {
  const [r] = await normRows([newSheetRow({}, { 2: 2, 11: 2 })]);
  assert.strictEqual(r['Student ID'], '7000001');
  assert.strictEqual(r['Instructor/Professor'], 'Ana Reyes-Test');
  assert.strictEqual(r['Subject Taught'], 'MOBILE APPLICATION DEVELOPMENT (MOBAPP 330)');
  assert.strictEqual(r['Remarks & Suggestions'], 'MAAYO MAGTURO');
  assert.strictEqual(r.M1, 3); assert.strictEqual(r.M2, 2); assert.strictEqual(r.M10, 3);
  assert.strictEqual(r.P1, 2, 'item 11 is P1'); assert.strictEqual(r.P10, 3, 'item 20 is P10');
  assert.ok(!('Section' in r), 'section has no place in the database: ignored');
});
test('Sheet import (new layout): shuffled column order, extra whitespace, curly quotes and a blank rating', async () => {
  const base = newSheetRow({}, { 5: '', 6: ' ' });
  const shuffled = {};
  for (const k of Object.keys(base).reverse()) shuffled[k.replace(/ /g, '  ')] = base[k];
  const [r] = await normRows([shuffled]);
  assert.strictEqual(r['Student ID'], '7000001');
  assert.strictEqual(r.M5, null, 'blank stays null, never 0'); assert.strictEqual(r.M6, null);
  assert.strictEqual(r.M4, 3); assert.strictEqual(r.P5, 3);
  assert.strictEqual(r['Instructor/Professor'], 'Ana Reyes-Test');
});
test('Sheet import (new layout): course codes with and without a space, family-name-first instructor', async () => {
  const rows = await normRows([
    newSheetRow({ [SH_COURSE]: 'PC121 DISCREET MATH' }),
    newSheetRow({ [SH_COURSE]: 'SE101 SOFTWARE ENGINEERING', [SH_INSTR]: 'Dela Testa, Maria L.' }),
    newSheetRow({ [SH_COURSE]: 'DATA STRUCTURES (CC214)' }),
  ]);
  assert.strictEqual(rows[0]['Subject Taught'], 'DISCREET MATH (PC121)');
  assert.strictEqual(rows[1]['Subject Taught'], 'SOFTWARE ENGINEERING (SE101)');
  assert.strictEqual(rows[1]['Instructor/Professor'], 'Maria Dela Testa');
  assert.strictEqual(rows[2]['Subject Taught'], 'DATA STRUCTURES (CC214)', 'already has a code in parentheses: kept');
});
test('Sheet import (new layout): the combined column is only a fallback for an empty instructor or course', async () => {
  const [a, b, c] = await normRows([
    newSheetRow({ [SH_INSTR]: '', [SH_COURSE]: '' }),
    newSheetRow({ [SH_INSTR]: 'Ben Cruz-Test', '': 'Someone Else - SE101 SOFTWARE ENGINEERING', [SH_COURSE]: '' }),
    newSheetRow({ [SH_INSTR]: 'Ben Cruz-Test', '': 'Someone Else - SE101 SOFTWARE ENGINEERING' }),
  ]);
  assert.strictEqual(a['Instructor/Professor'], 'Ana Reyes-Test');
  assert.strictEqual(a['Subject Taught'], 'MOBILE APPLICATION DEVELOPMENT (MOBAPP 330)');
  assert.strictEqual(b['Instructor/Professor'], 'Ben Cruz-Test', 'column 3 wins');
  assert.strictEqual(b['Subject Taught'], 'SOFTWARE ENGINEERING (SE101)');
  assert.strictEqual(c['Subject Taught'], 'MOBILE APPLICATION DEVELOPMENT (MOBAPP 330)', 'column 5 wins');
});
test('Sheet import (old layout): same output as before', async () => {
  const old = { 'Student ID': 'S9', 'Instructor/Professor': 'Ana Reyes-Test', 'Subject Taught': 'Software Engineering (SE101)', M1: 4, M2: '', P10: 5,
    'Remarks & Suggestions': 'Good', Date: 'September 20, 2026' };
  const [r] = await normRows([old]);
  assert.strictEqual(r['Student ID'], 'S9');
  assert.strictEqual(r['Instructor/Professor'], 'Ana Reyes-Test');
  assert.strictEqual(r['Subject Taught'], 'Software Engineering (SE101)');
  assert.strictEqual(r.M1, 4); assert.strictEqual(r.M2, null); assert.strictEqual(r.M3, null); assert.strictEqual(r.P10, 5);
  assert.strictEqual(r['Remarks & Suggestions'], 'Good'); assert.strictEqual(r.Date, 'September 20, 2026');
  const [lc] = await normRows([{ student_id: 'S8', Instructor: 'Ana Reyes-Test', m1: 2, p1: 1, Remarks: 'ok' }]);
  assert.strictEqual(lc['Student ID'], 'S8'); assert.strictEqual(lc.M1, 2); assert.strictEqual(lc.P1, 1);
});
test('Sheet import (new layout): Map to Target Format -> null-aware ratings, no stamp key, dedupe per student+instructor+course', async () => {
  const rows = await normRows([newSheetRow({}, { 1: '' }), newSheetRow({ 'ID Number': 7000002 }, { 1: 4 })]);
  const mapped = await run('Map to Target Format', {}, {
    'Google LINK from phone1': { body: { term_id: TERM, semester: '1st', academic_year: '2026' } },
    'Normalize Sheet Rows': { __items: rows },
    'get instructor id5': { __items: [{ instructor_id: INSTR }, { instructor_id: INSTR }] },
    'get subject id7': { __items: [{ subject_id: SUBJ }, { subject_id: SUBJ }] },
    'GET TERM BY ID1': { __items: [{ current_term_id: TERM }, { current_term_id: TERM }] } });
  assert.strictEqual(mapped.length, 2);
  const nodes = { 'SAST image from phone': { body: { user_id: STAFF } } };
  const raw = await run('Code in JavaScript', null, nodes, mapped.map((x) => x.json));
  assert.strictEqual(raw.length, 2, 'two students are two rows');
  assert.strictEqual(raw[0].json.m1, null, 'blank rating is NULL, never 0');
  assert.strictEqual(raw[1].json.m1, 4);
  assert.strictEqual(raw[0].json.student_id, '7000001');
  assert.ok(!('stamp_no' in raw[0].json), 'sheet rows have no stamp number');
  const dup = await run('Code in JavaScript', null, nodes, [mapped[0].json, mapped[0].json]);
  assert.strictEqual(dup.length, 1, 'same student + instructor + course + term is deduped');
});
test('Sheet import: the workflow reads the sheet through Normalize Sheet Rows only', () => {
  const node = (n) => wf.nodes.find((x) => x.name === n);
  assert.ok(wf.connections['Get row(s) in sheet1'].main[0].some((c) => c.node === 'Normalize Sheet Rows'));
  assert.ok(wf.connections['Normalize Sheet Rows'].main[0].some((c) => c.node === 'get instructor id5'));
  assert.ok(!codeOf('Map to Target Format').includes("$('Get row(s) in sheet1')"));
  assert.ok(!node('get subject id7').parameters.options.queryReplacement.includes("$('Get row(s) in sheet1')"));
  assert.ok(!/gemini|openrouter/i.test(codeOf('Normalize Sheet Rows')), 'no AI call in the normaliser');
});

test('Sheet import: answers before the Gemini loop (responseNode), clear error answer, zero rows throw', async () => {
  const hook = wf.nodes.find((x) => x.name === 'Google LINK from phone1');
  assert.strictEqual(hook.parameters.responseMode, 'responseNode');
  // the job row (migration 018) sits between Map to Target Format and the answer; the done marker after If19
  assert.deepStrictEqual(wf.connections['Map to Target Format'].main[0].map((c) => c.node), ['Create Sheet Import Job']);
  assert.deepStrictEqual(wf.connections['Create Sheet Import Job'].main[0].map((c) => c.node), ['Sheet Import Response']);
  assert.deepStrictEqual(wf.connections['Sheet Import Response'].main[0].map((c) => c.node), ['After Sheet Response']);
  assert.deepStrictEqual(wf.connections['After Sheet Response'].main[0].map((c) => c.node), ['If19', 'Sheet Import Summary']);
  for (const n of ['Get row(s) in sheet1', 'Normalize Sheet Rows']) {
    assert.ok(wf.connections[n].main[1].some((c) => c.node === 'Sheet Import Error Response'), n + ' error output');
  }
  const mapped = [{ instructor_ID: 'i', true_subject_id: 's' }, { instructor_ID: null, true_subject_id: 's' }, { instructor_ID: 'i', true_subject_id: 's' }];
  const ctx = { 'Map to Target Format': { __items: mapped } };
  const evalExpr = async (nodeName, nodes, $json) => {
    const raw = wf.nodes.find((x) => x.name === nodeName).parameters.responseBody;
    const m = raw.match(/^=\{\{([\s\S]*)\}\}$/);
    const $ = (ref) => ({ all: () => ((nodes[ref] || {}).__items || []).map((j) => ({ json: j })) });
    return new Function('$', '$json', `return (${m[1]});`)($, $json);
  };
  const body = await evalExpr('Sheet Import Response', ctx, {});
  assert.strictEqual(body.status, 'success');
  assert.deepStrictEqual([body.rows, body.matched, body.unmatched, body.imported_count, body.error_count], [3, 2, 1, 2, 1]);
  assert.ok(/AI remark analysis continues in the background/.test(body.message));
  const err403 = await evalExpr('Sheet Import Error Response', {}, { error: { message: 'The caller does not have permission [403]' } });
  assert.ok(/Share the sheet with the service account email as Viewer/.test(err403.error));
  await assert.rejects(() => run('Normalize Sheet Rows', undefined, {}, [{}]), /no data rows/);
});

// ---- placeholder comments ("N/A", "none", ...) are NULL, never text
const PLACEHOLDERS = ['', ' ', 'N/A', 'n/a', 'NA', 'N.A.', 'n.a', 'NONE', 'None.', 'nothing', 'No Comment', 'no comments', 'NO  REMARKS',
  'wala', 'Wala man', '-', '--', '.', '..', 'x', 'X', '???', '...', ' - ', '\u2019'];
const REAL_COMMENTS = ['None of the lessons were clear', 'wala koy problema', 'N/A for me, but good class', 'nothing beats this teacher', 'Good', 'x2', 'No comment on grading, but he is late', 'na-late siya'];
test('Placeholder comments: only whole-value matches become NULL (sheet, phone/corrections raw row, remark AI input)', async () => {
  const sheetRows = PLACEHOLDERS.concat(REAL_COMMENTS).map((c) => newSheetRow({ Comments: c }));
  const norm = await normRows(sheetRows);
  PLACEHOLDERS.forEach((c, i) => assert.strictEqual(norm[i]['Remarks & Suggestions'], '', `sheet: ${JSON.stringify(c)} is blank`));
  REAL_COMMENTS.forEach((c, i) => assert.strictEqual(norm[PLACEHOLDERS.length + i]['Remarks & Suggestions'], c, `sheet: ${c} kept`));
  const nodes = { 'SAST image from phone': { body: { user_id: STAFF } } };
  const rowOf = (c) => ({ instructor_ID: INSTR, true_subject_id: SUBJ, current_term_id: TERM, 'Student ID': 'A1', M1: 4, 'Remarks & Suggestions': c });
  for (const c of PLACEHOLDERS) {
    const raw = (await run('Code in JavaScript', null, nodes, [rowOf(c)]))[0].json;
    assert.strictEqual(raw.Remarks_and_Suggestions, null, `raw row: ${JSON.stringify(c)} -> NULL`);
  }
  for (const c of REAL_COMMENTS) {
    const raw = (await run('Code in JavaScript', null, nodes, [rowOf(c)]))[0].json;
    assert.strictEqual(raw.Remarks_and_Suggestions, c, `raw row: ${c} kept`);
  }
  const grouped = await run('Group feedback by instructor1', undefined, {}, [rowOf('N/A'), rowOf('None'), rowOf('Very patient teacher')]);
  const remarks = grouped.flatMap((g) => g.json.remarks.map((r) => r.remark));
  assert.deepStrictEqual(remarks, ['Very patient teacher'], 'the remark AI never sees a placeholder');
  assert.ok(/cleanRemark\(\s*data\["Remarks & Suggestions"\]/.test(codeOf('Prepare New Student Data1')), 'single-remark AI path cleans too');
});

// ---- Google Sheet import job (migration 018): the app polls sheet_import_jobs until the flow really ends
// Run-aware mock: nodes = { name: { runs: [[out0, out1], ...] } } (a loop node has one entry per pass) or
// { items: [...] } (one run, output 0). Unknown node = unexecuted (throws like n8n). all(b, r) for a run
// that does not exist throws ("Run r of node ... not found"), unless lenient (returns [] instead).
function jobMock(nodes, { lenient = false } = {}) {
  return (ref) => {
    if (!Object.prototype.hasOwnProperty.call(nodes, ref)) throw new Error(`Referenced node is unexecuted: ${ref}`);
    const v = nodes[ref];
    const runs = v.runs || [[v.items || []]];
    const all = (b = 0, r) => {
      const ri = r === undefined ? runs.length - 1 : r;
      if (ri >= runs.length) {
        if (lenient) return [];
        throw new Error(`Run ${ri} of node "${ref}" not found`);
      }
      return (runs[ri][b] || []).map((json) => ({ json }));
    };
    return { all, first: (b) => all(b)[0], isExecuted: true };
  };
}
async function runJob(name, nodes, { input = [], prevNode, lenient } = {}) {
  const $input = { all: () => input.map((json) => ({ json })), first: () => (input[0] ? { json: input[0] } : undefined) };
  const fn = new AsyncFunction('$', '$input', '$prevNode', codeOf(name));
  return fn(jobMock(nodes, { lenient }), $input, prevNode);
}
const JOB_ID = '11111111-2222-4333-8444-555555555555';
function sheetJobNodes(extra = {}) {
  const row = (ok) => (ok ? { instructor_ID: INSTR, true_subject_id: SUBJ } : { instructor_ID: '', true_subject_id: SUBJ });
  return {
    'Create Sheet Import Job': { items: [{ id: JOB_ID }] },
    'Map to Target Format': { items: [row(1), row(1), row(1), row(1), row(0)] },
    'Code in JavaScript': { items: [{}, {}, {}, {}] },
    'Insert or update rows in a table1': { runs: [[[{ id: 1 }, { id: 2 }]], [[{ id: 3 }, { id: 4 }]], [[{}]]] },
    'Insert import_errors1': { runs: [[[{}], []]] },
    'Group feedback by instructor1': { items: [{ g: 1 }, { g: 2 }] },
    'Unified AI Analysis1': { runs: [[[{ text: 'a' }], []], [[{ text: 'b' }], []]] },
    'Parse Unified AI Result1': { runs: [[[{}], []], [[{}], []]] },
    'insert data to student remarks2': { runs: [[[{ success: true }], []], [[{ success: true }], []]] },
    'store results2': { runs: [[[{ success: true }], []], [[{ success: true }], []]] },
    'insert to word cloud2': { runs: [[[{ success: true }], []], [[{ success: true }], []]] },
    'update to overall total2': { runs: [[[{ ok: 1 }], []]] },
    ...extra,
  };
}

test('Sheet import job: the summary gives the real counts after a clean import', async () => {
  const out = await runJob('Sheet Import Summary', sheetJobNodes());
  assert.strictEqual(out.length, 1);
  const s = out[0].json;
  assert.strictEqual(s.job_id, JOB_ID);
  assert.deepStrictEqual([s.rows, s.matched, s.unmatched, s.imported_count, s.error_count, s.ai_groups, s.ai_failed],
    [5, 4, 1, 4, 1, 2, 0]);
  assert.strictEqual(s.has_problems, false);
  assert.strictEqual(s.first_failed_node, null);
  assert.ok(/4 of 5 rows saved/.test(s.message) && /1 unmatched rows sent to Import Errors/.test(s.message), s.message);
  assert.ok(/AI remark analysis done for 2 of 2 instructors/.test(s.message), s.message);
});

test('Sheet import job: the summary counts failed rows, AI failures and other failed saves over every loop run', async () => {
  const nodes = sheetJobNodes({
    'Insert or update rows in a table1': { runs: [[[{ id: 1 }, { error: 'duplicate key' }]], [[{ id: 3 }, { id: 4 }]]] },
    'Unified AI Analysis1': { runs: [[[{ text: 'a' }], []], [[], [{ error: '429 Too Many Requests' }]], [[{ text: 'c' }], []]] },
    'Group feedback by instructor1': { items: [{}, {}, {}] },
    'insert data to student remarks2': { runs: [[[], [{ error: 'violates check' }]]] },
    'store results2': { runs: [[[{ success: true }], []], [[], [{ error: 'x' }]]] },
  });
  const s = (await runJob('Sheet Import Summary', nodes))[0].json;
  assert.strictEqual(s.imported_count, 3);
  assert.strictEqual(s.error_count, 2, 'unmatched + the row that failed');
  assert.strictEqual(s.ai_failed, 2, 'one AI failure after retries + one failed remark insert');
  assert.strictEqual(s.ai_groups, 3);
  assert.strictEqual(s.has_problems, true);
  assert.strictEqual(s.first_failed_node, 'Insert or update rows in a table1');
  assert.ok(/Problems: 1 rows could not be saved; AI remark analysis failed for 2 of 3 instructors; an AI summary could not be saved/.test(s.message), s.message);
});

test('Sheet import job: the summary survives unexecuted nodes, a lenient run lookup, and no job id', async () => {
  // every row unmatched: the raw upsert path and the remark loop never ran
  const nodes = {
    'Create Sheet Import Job': { items: [{ id: JOB_ID }] },
    'Map to Target Format': { items: [{ instructor_ID: '' }, { instructor_ID: '' }] },
    'Insert import_errors1': { runs: [[[{}, {}], []]] },
  };
  const s = (await runJob('Sheet Import Summary', nodes))[0].json;
  assert.deepStrictEqual([s.rows, s.matched, s.unmatched, s.imported_count, s.error_count, s.ai_groups, s.has_problems],
    [2, 0, 2, 0, 2, 0, false]);
  assert.ok(/0 of 2 rows saved, 2 unmatched/.test(s.message), s.message);
  // an n8n that returns [] for a missing run instead of throwing: bounded, same result
  const t = (await runJob('Sheet Import Summary', sheetJobNodes(), { lenient: true }))[0].json;
  assert.deepStrictEqual([t.imported_count, t.ai_failed, t.has_problems], [4, 0, false]);
  // the job insert failed (migration 018 missing): nothing to update
  assert.deepStrictEqual(await runJob('Sheet Import Summary', sheetJobNodes({ 'Create Sheet Import Job': { items: [{ error: 'relation does not exist' }] } })), []);
  const noJob = sheetJobNodes();
  delete noJob['Create Sheet Import Job'];
  assert.deepStrictEqual(await runJob('Sheet Import Summary', noJob), []);
});

test('Sheet import job: Describe Import Failure names the failed step for staff', async () => {
  const ai = (await runJob('Describe Import Failure', {}, { input: [{ error: '429 Too Many Requests' }], prevNode: { name: 'Unified AI Analysis1', outputIndex: 1 } }))[0].json;
  assert.strictEqual(ai.failed_node, 'Unified AI Analysis1');
  assert.ok(/after 3 tries/.test(ai.message) && /survey rows are saved/.test(ai.message) && /Error: 429 Too Many Requests$/.test(ai.message), ai.message);
  const raw = (await runJob('Describe Import Failure', {}, { input: [{ error: { message: 'duplicate key value' } }, { error: 'y' }], prevNode: { name: 'If14' } }))[0].json;
  assert.strictEqual(raw.failed_node, 'Insert or update rows in a table1');
  assert.ok(/could not be saved to the database\. \(2 items\) Error: duplicate key value/.test(raw.message), raw.message);
  assert.strictEqual(raw.failed_items, 2);
  const unknown = (await runJob('Describe Import Failure', {}, { input: [{}] }))[0].json;
  assert.strictEqual(unknown.failed_node, 'unknown step');
  const long = (await runJob('Describe Import Failure', {}, { input: [{ error: 'e'.repeat(5000) }], prevNode: { name: 'store results2' } }))[0].json;
  assert.ok(long.message.length <= 1000);
});

function evalExpr(expr, scope) {
  const src = expr.replace(/^=\{\{/, '').replace(/\}\}$/, '');
  const names = Object.keys(scope);
  return new Function(...names, `return (${src});`)(...names.map((k) => scope[k]));
}

test('Sheet import job: the early answer carries job_id + status processing, and falls back without a job', () => {
  const body = wf.nodes.find((n) => n.name === 'Sheet Import Response').parameters.responseBody;
  const nodes = sheetJobNodes();
  const r = evalExpr(body, { $: jobMock(nodes) });
  assert.strictEqual(r.status, 'processing');
  assert.strictEqual(r.job_id, JOB_ID);
  assert.strictEqual(r.final, false);
  assert.deepStrictEqual([r.rows, r.matched, r.unmatched, r.imported_count, r.error_count, r.unmatched_count], [5, 4, 1, 4, 1, 1]);
  assert.ok(/not final/.test(r.message));
  const old = evalExpr(body, { $: jobMock({ 'Map to Target Format': nodes['Map to Target Format'] }) });
  assert.strictEqual(old.status, 'success');
  assert.strictEqual(old.job_id, undefined);
  const failed = evalExpr(body, { $: jobMock(sheetJobNodes({ 'Create Sheet Import Job': { items: [{ error: 'no table' }] } })) });
  assert.strictEqual(failed.status, 'success');
});

test('Sheet import job: the job row gets the execution id and only a uuid requester', () => {
  const p = wf.nodes.find((n) => n.name === 'Create Sheet Import Job').parameters.options.queryReplacement;
  const base = sheetJobNodes();
  const withUser = (uid) => evalExpr(p, { $: jobMock({ ...base, 'Google LINK from phone1': { items: [{ body: { user_id: uid } }] } }), $execution: { id: '4321' } });
  assert.deepStrictEqual(withUser(JOB_ID), [5, 4, 1, '4321', JOB_ID]);
  assert.deepStrictEqual(withUser('robert'), [5, 4, 1, '4321', '']);
  const stopped = wf.nodes.find((n) => n.name === 'Mark Import Job Stopped').parameters.options.queryReplacement;
  const sv = evalExpr(stopped, { $json: { execution: { id: '77', lastNodeExecuted: 'Group feedback by instructor1', error: { message: 'Cannot read properties of undefined' } } } });
  assert.strictEqual(sv[0], '77');
  assert.strictEqual(sv[1], 'Group feedback by instructor1');
  assert.ok(/stopped at step "Group feedback by instructor1": Cannot read properties of undefined/.test(sv[2]), sv[2]);
  const done = wf.nodes.find((n) => n.name === 'Mark Import Job Done').parameters.options.queryReplacement;
  const dv = evalExpr(done, { $json: { job_id: JOB_ID, rows: 5, matched: 4, unmatched: 1, imported_count: 4, error_count: 1, ai_groups: 2, ai_failed: 0, has_problems: false, first_failed_node: null, message: 'm',
    details: { form_type: 'SET-15', rows: 5 }, term_id: TERM } });
  assert.deepStrictEqual(dv, [JOB_ID, 5, 4, 1, 4, 1, 2, 0, false, '', 'm', '{"form_type":"SET-15","rows":5}', TERM]);
  const dv0 = evalExpr(done, { $json: { job_id: JOB_ID, rows: 0, message: 'm' } });
  assert.deepStrictEqual(dv0.slice(11), ['{}', ''], 'no details: {} keeps the column (the SQL checks for {})');
});

test('Sheet import job: wiring and execution order (done marker last, Wait1 feeds kept, no AI involved)', () => {
  const byName = Object.fromEntries(wf.nodes.map((n) => [n.name, n]));
  const tgt = (s, i) => ((wf.connections[s] || {}).main || [])[i] ? wf.connections[s].main[i].map((c) => c.node) : [];
  assert.deepStrictEqual(tgt('Map to Target Format', 0), ['Create Sheet Import Job']);
  assert.deepStrictEqual(tgt('Create Sheet Import Job', 0), ['Sheet Import Response']);
  assert.deepStrictEqual(tgt('After Sheet Response', 0), ['If19', 'Sheet Import Summary']);
  assert.ok(byName['Sheet Import Summary'].position[1] > byName.If19.position[1], 'summary below If19: runs after the whole If19 branch');
  assert.ok(byName['Describe Import Failure'].position[1] < byName.Wait1.position[1], 'failure recorded before the loop goes on');
  assert.strictEqual(wf.settings.executionOrder, 'v1');
  for (const [s, i] of [['Unified AI Analysis1', 1], ['Parse Unified AI Result1', 1], ['insert data to student remarks2', 1],
    ['insert data to student remarks2', 0]]) {
    assert.ok(tgt(s, i).includes('Wait1'), `${s}[${i}] still feeds Wait1`);
  }
  assert.strictEqual(byName['insert data to student remarks2'].onError, 'continueErrorOutput');
  assert.ok(!('queryBatching' in (byName['insert data to student remarks2'].parameters.options || {})),
    'single query: success OR error output fires, never both (Wait1 fed once)');
  const reach = (start) => {
    const seen = new Set();
    const stack = [start];
    while (stack.length) {
      const c = stack.pop();
      if (seen.has(c)) continue;
      seen.add(c);
      for (const outs of Object.values(wf.connections[c] || {})) for (const l of outs) for (const e of l || []) stack.push(e.node);
    }
    return seen;
  };
  const ai = wf.nodes.filter((n) => /langchain|googleGemini|gemini/i.test(n.type) || /generativelanguage/.test(JSON.stringify(n.parameters))).map((n) => n.name);
  for (const start of ['Sheet Import Summary', 'Describe Import Failure', 'Import Job Error Trigger']) {
    const hit = ai.filter((a) => reach(start).has(a) && a !== start);
    assert.deepStrictEqual(hit, [], `${start} reaches no AI node`);
  }
  assert.strictEqual(byName['Import Job Error Trigger'].type, 'n8n-nodes-base.errorTrigger');
  assert.ok(!wf.settings.errorWorkflow, 'no other error workflow (the own Error Trigger runs)');
});

// ---- Google Sheet import of the new 15-item forms (SET-15 student, SUP-15 supervisor)
async function normNew(rows, instrument) {
  const nodes = instrument === undefined ? {} : { 'clean data1': { instrument } };
  return (await run('Normalize Sheet Rows', undefined, nodes, rows)).map((x) => x.json);
}
const SET_BASE = { 'Instructor/Professor': 'Ana Reyes', 'Subject Taught': 'MOBAPP 330', Comments: 'Good teacher', 'Stamp No': '0100005' };
test('New forms: Q1..Q15, 1..15 and Item 1..Item 15 headers all give the same answers; blank stays null', async () => {
  const mk = (head) => {
    const r = { ...SET_BASE };
    for (let i = 1; i <= 15; i++) r[head(i)] = i === 4 ? '' : (i % 5) + 1;
    return r;
  };
  const expected = {};
  for (let i = 1; i <= 15; i++) expected[String(i)] = i === 4 ? null : (i % 5) + 1;
  for (const head of [(i) => 'Q' + i, (i) => String(i), (i) => 'Item ' + i, (i) => i + '. text of the item']) {
    const [r] = await normNew([mk(head)], 'SET-15');
    assert.strictEqual(r.Instrument, 'SET-15');
    assert.deepStrictEqual(r.Answers, expected, head(1));
    assert.strictEqual(r['Stamp No'], '0100005');
    assert.strictEqual(r['Remarks & Suggestions'], 'Good teacher');
    assert.strictEqual(r['Instructor/Professor'], 'Ana Reyes');
    assert.strictEqual(r.M1, null);
  }
});
test('New forms: the student id never goes into the Student ID field (only Details, SET-15); invalid values reach the database unchanged to be refused', async () => {
  const [r] = await normNew([{ ...SET_BASE, 'Student ID': '2021-0001', Q1: 6, Q2: 'x', Q3: 0, Q4: '5' }], 'SET-15');
  assert.strictEqual(r['Student ID'], '');
  assert.strictEqual(r.Details.student_id, '2021-0001');
  assert.deepStrictEqual([r.Answers['1'], r.Answers['2'], r.Answers['3'], r.Answers['4'], r.Answers['5']], [6, 'x', 0, '5', null]);
  assert.strictEqual(Object.keys(r.Answers).length, 15, 'items 1..15 only');
});
test('New forms: Details = student id (SET only), section, program level/year, ISO date, rank and designation (SUP only)', async () => {
  const set = { ...SET_BASE, 'ID Number': 7000001, Section: ' A1 ', 'Program Level': 'Undergraduate', 'Program Year': '3rd', Date: '10/30/2026', Q1: 5 };
  const [a] = await normNew([set], 'SET-15');
  assert.deepStrictEqual(a.Details, { student_id: '7000001', section: 'A1', program_level: 'Undergraduate', program_year: '3rd',
    submitted_date: '2026-10-30', academic_rank: '', designation: '' });
  const sup = { ...SET_BASE, 'Supervisor Email': 'c@x.ph', 'Student ID': 'S9', Section: 'B', 'Academic Rank': 'Assistant Professor II',
    'Supervisor Designation': 'Department Head', Date: 46325, Q1: 5 };
  const [s] = await normNew([sup], 'SUP-15');
  assert.strictEqual(s.Details.student_id, '', 'no student id on a supervisor form');
  assert.deepStrictEqual([s.Details.academic_rank, s.Details.designation, s.Details.section, s.Details.submitted_date],
    ['Assistant Professor II', 'Department Head', 'B', '2026-10-30']);
  assert.strictEqual(s.Supervisor, 'c@x.ph', 'a designation column is not the supervisor name');
  for (const [d, iso] of [['2026-10-30', '2026-10-30'], ['Oct 30, 2026', '2026-10-30'], ['13/45/2026', ''], ['soon', ''], ['', '']]) {
    assert.strictEqual((await normNew([{ ...SET_BASE, Date: d, Q1: 5 }], 'SET-15'))[0].Details.submitted_date, iso, d);
  }
  const old = (await normNew([{ 'Student ID': 'S1', 'Instructor/Professor': 'Ana Reyes', Section: 'A', M1: 5 }]))[0];
  assert.ok(!('Details' in old), 'the old SAST sheet gets no Details');
});
test('New forms: SUP-15 supervisor = email column if there is one, else the name column', async () => {
  const [a] = await normNew([{ ...SET_BASE, Supervisor: 'Dr. Cruz', 'Supervisor Email': 'cruz@ctu.edu.ph', Q1: 5 }], 'SUP-15');
  assert.strictEqual(a.Supervisor, 'cruz@ctu.edu.ph');
  const [b] = await normNew([{ ...SET_BASE, 'Supervisor Name': 'Dr. Cruz', Q1: 5 }], 'SUP-15');
  assert.strictEqual(b.Supervisor, 'Dr. Cruz');
  const [c] = await normNew([{ ...SET_BASE, Q1: 5 }], 'SUP-15');
  assert.strictEqual(c.Supervisor, '');
});
test('New forms: the old SAST sheet is unchanged (no form_type, SAST-20, or an unknown value)', async () => {
  const row = { 'Student ID': 'S1', 'Instructor/Professor': 'Ana Reyes', 'Subject Taught': 'MOBAPP 330', M1: 5, P10: 4, Q1: 3, 'Remarks & Suggestions': 'ok' };
  const old = (await normNew([row]))[0];
  for (const inst of ['', 'SAST-20', 'bogus']) {
    const r = (await normNew([row], inst))[0];
    assert.deepStrictEqual(r, old, inst);
  }
  assert.ok(!('Instrument' in old) && old['Student ID'] === 'S1' && old.M1 === 5 && old.P10 === 4);
});
test('New forms: Map to Target Format carries answers, stamp, supervisor and a row number; masks comments; SAST rows get none', async () => {
  const sheet = { 'Student ID': '', 'Instructor/Professor': 'Ana Reyes', 'Subject Taught': 'MOBAPP 330', Instrument: 'SUP-15',
    Answers: { 1: 5 }, 'Stamp No': '0100005', Supervisor: 'cruz@ctu.edu.ph', 'Remarks & Suggestions': 'Buang ka, bilat',
    Details: { section: 'A' } };
  const nodes = {
    'Google LINK from phone1': { body: { semester: '1st', academic_year: '2026', type: 'google_form_import', term_id: TERM } },
    'Normalize Sheet Rows': { __items: [sheet, { ...sheet, Instrument: undefined, Answers: undefined }] },
    'get instructor id5': { __items: [{ instructor_id: INSTR }, { instructor_id: INSTR }] },
    'get subject id7': { __items: [{ subject_id: SUBJ }, { subject_id: SUBJ }] },
    'GET TERM BY ID1': { __items: [{ current_term_id: TERM }, { current_term_id: TERM }] },
  };
  const out = (await run('Map to Target Format', {}, nodes)).map((x) => x.json);
  assert.strictEqual(out[0].instrument, 'SUP-15');
  assert.deepStrictEqual(out[0].answers, { 1: 5 });
  assert.deepStrictEqual([out[0].stamp_no, out[0].supervisor, out[0].sheet_row], ['0100005', 'cruz@ctu.edu.ph', 1]);
  assert.strictEqual(out[0]['Remarks & Suggestions'], 'Buang ka, *****');
  assert.deepStrictEqual(out[0].details, { section: 'A' });
  assert.ok(!('instrument' in out[1]) && !('answers' in out[1]) && !('details' in out[1]), 'old rows get no new keys');
});
test('Import-error fix: SET-15 / SUP-15 rows answer 422 and never reach the old SAST correction path', () => {
  const tgt = (n, i) => ((wf.connections[n] || {}).main[i] || []).map((c) => c.node);
  assert.deepStrictEqual(tgt('import-error-correction1', 0), ['Import Fix New Form?']);
  assert.deepStrictEqual(tgt('Import Fix New Form?', 0), ['Respond new_form_not_fixable']);
  assert.deepStrictEqual(tgt('Import Fix New Form?', 1), ['get existing scan image id1']);
  const ifn = wf.nodes.find((n) => n.name === 'Import Fix New Form?');
  const expr = ifn.parameters.conditions.conditions[0].leftValue.replace(/^=\{\{/, '').replace(/\}\}$/, '');
  const check = (instrument) => new Function('$json', 'return ' + expr)({ body: { raw_data: { instrument } } });
  assert.strictEqual(check('SET-15'), true);
  assert.strictEqual(check('SUP-15'), true);
  assert.strictEqual(check(undefined), false, 'old SAST rows keep the old path');
  assert.strictEqual(new Function('$json', 'return ' + expr)({ body: {} }), false);
  const res = wf.nodes.find((n) => n.name === 'Respond new_form_not_fixable');
  assert.strictEqual(res.parameters.options.responseCode, 422);
  const body = JSON.parse(res.parameters.responseBody.replace(/^=/, ''));
  assert.deepStrictEqual([body.success, body.code], [false, 'new_form_not_fixable']);
  assert.ok(/Fix the instructor or subject name in the Google Sheet/.test(body.message));
});

test('New forms: wiring (If19 -> Sheet New Form? -> Save / If1), the save SQL, and the row parameters', () => {
  const tgt = (n, i) => ((wf.connections[n] || {}).main[i] || []).map((c) => c.node);
  assert.deepStrictEqual(tgt('If19', 0), ['Sheet New Form?']);
  assert.deepStrictEqual(tgt('Sheet New Form?', 0), ['Save New Form Row']);
  assert.deepStrictEqual(tgt('Sheet New Form?', 1), ['If1']);
  assert.deepStrictEqual(tgt('If19', 1), ['is data from google link?'], 'unmatched rows keep the old error path');
  const save = wf.nodes.find((n) => n.name === 'Save New Form Row');
  assert.strictEqual(save.onError, 'continueRegularOutput');
  const q = save.parameters.query;
  assert.ok(/save_eval_response\(/.test(q) && /'sheet'/.test(q) && /supervisor_not_found/.test(q) && /DEPARTMENT_HEAD/.test(q));
  assert.ok(/NULLIF\(\$13, ''\)::jsonb/.test(q) && !/student_id/i.test(q.replace(/^--.*$/gm, '')), 'details are passed on as one json parameter');
  const expr = save.parameters.options.queryReplacement.replace(/^=\{\{/, '').replace(/\}\}$/, '');
  const row = { instrument: 'SUP-15', instructor_ID: INSTR, true_subject_id: SUBJ, current_term_id: TERM, answers: { 1: 5, 2: null },
    'Remarks & Suggestions': 'fine', stamp_no: '', supervisor: 'cruz@ctu.edu.ph', sheet_row: 3,
    details: { section: 'A', academic_rank: 'Instructor I' } };
  const mock = (ref) => ({ first: () => ({ json: ref === 'Google LINK from phone1'
    ? { body: { link: 'https://docs.google.com/spreadsheets/d/SHEETID123/edit#gid=0', user_id: JOB_ID } } : { id: JOB_ID } }) });
  const p = new Function('$', '$json', 'return ' + expr)(mock, row);
  assert.strictEqual(p.length, 13);
  assert.deepStrictEqual([p[0], p[4], p[7], p[8], p[9], p[10], p[11], p[12]],
    ['SUP-15', '{"1":5,"2":null}', 'cruz@ctu.edu.ph', `sheet:SHEETID123:${TERM}:3`, JOB_ID, '3', JOB_ID,
      '{"section":"A","academic_rank":"Instructor I"}']);
  assert.strictEqual(new Function('$', '$json', 'return ' + expr)(mock, { ...row, details: undefined })[12], '{}');
  assert.ok(/instrument: payload\.form_type/.test(codeOf('clean data1')));
});
test('New forms: the job summary counts saved, left-out and failed rows with reasons in plain words', async () => {
  const res = (r, n) => ({ result: r, row_no: n });
  const nodes = sheetJobNodes({
    'Map to Target Format': { items: [1, 2, 3, 4, 5, 6].map(() => ({ instructor_ID: INSTR, true_subject_id: SUBJ })) },
    'Save New Form Row': { items: [res({ saved: true, counted: true }, 1), res({ saved: true, counted: false }, 2),
      res({ saved: false, reason: 'invalid_rating', item_no: 3 }, 3), res({ saved: false, reason: 'supervisor_not_found' }, 4),
      { error: 'boom', row_no: 5 }, res({ saved: false, reason: 'duplicate_student' }, 6)] },
  });
  for (const k of ['Code in JavaScript', 'Insert or update rows in a table1', 'Insert import_errors1', 'Group feedback by instructor1',
    'Unified AI Analysis1', 'Parse Unified AI Result1', 'insert data to student remarks2', 'store results2', 'insert to word cloud2',
    'update to overall total2']) delete nodes[k];
  const s = (await runJob('Sheet Import Summary', nodes, { lenient: true }))[0].json;
  assert.deepStrictEqual([s.rows, s.imported_count, s.error_count], [6, 2, 4]);
  assert.ok(/already evaluated this instructor for this subject this term \(row 6\)/.test(s.message), s.message);
  assert.ok(/1 saved but left out of the scores: fewer than 5 answers/.test(s.message), s.message);
  assert.ok(/not 1 to 5 \(row 3\)/.test(s.message) && /supervisor was not found.*\(row 4\)/.test(s.message) && /database refused the row \(row 5\)/.test(s.message), s.message);
  assert.ok(/runs for SET comments, not for the supervisor form/.test(s.message), s.message);
  assert.ok(!/not run for the new forms/.test(s.message), s.message);
});
test('New forms: the summary counts SET AI groups with the old ones', async () => {
  const nodes = sheetJobNodes({ 'Save New Form Row': { items: [{ result: { saved: true, counted: true }, row_no: 1 }] } });
  const s = (await runJob('Sheet Import Summary', nodes, { lenient: true }))[0].json;
  assert.strictEqual(s.ai_groups, 2);
  assert.ok(/AI remark analysis done for 2 of 2 instructors \(SET comments; the supervisor form is not analysed/.test(s.message), s.message);
});

test('SET comments for AI: only SET-15, saved + counted, not updated, non-empty comment; one batch', async () => {
  const mk = (instrument, remark, extra = {}) => ({ instrument, instructor_ID: INSTR, true_subject_id: SUBJ, current_term_id: TERM,
    'Subject Taught': 'IT 101', 'Remarks & Suggestions': remark, ...extra });
  const src = [
    mk('SET-15', 'Good teacher'),                 // 0 passes
    mk('SUP-15', 'Supervisor comment'),           // 1 supervisor: no
    mk('SET-15', 'Under five answers'),           // 2 counted false: no
    mk('SET-15', 'Re-import'),                    // 3 updated: no
    mk('SET-15', 'N/A'),                          // 4 placeholder: no
    mk('SET-15', ''),                             // 5 empty: no
    mk('SET-15', 'Not saved'),                    // 6 not saved: no
    mk('SET-15', 'Late but ok'),                  // 7 passes (paired by pairedItem)
  ];
  const ok = (r) => ({ result: { saved: true, counted: true, updated: false, ...r } });
  const res = [ok({}), ok({}), ok({ counted: false }), ok({ updated: true }), ok({}), ok({}), { result: { saved: false, reason: 'invalid_rating' } }, ok({})];
  const saved = res.map((j, i) => ({ json: j, pairedItem: { item: i } }));
  const $ = (ref) => { assert.strictEqual(ref, 'Sheet New Form?'); return { all: () => src.map((json) => ({ json })) }; };
  const fn = new AsyncFunction('$', '$input', codeOf('SET Comments for AI'));
  const out = await fn($, { all: () => saved });
  assert.deepStrictEqual(out.map((o) => o.json['Remarks & Suggestions']), ['Good teacher', 'Late but ok']);
  assert.deepStrictEqual(Object.keys(out[0].json).sort(), ['Remarks & Suggestions', 'Subject Taught', 'current_term_id', 'instructor_ID', 'true_subject_id']);
  assert.strictEqual(out[0].json.instructor_ID, INSTR);
  // nothing qualifies: no items, so the AI loop does not run
  assert.strictEqual((await fn($, { all: () => [{ json: { result: { saved: true, counted: false } } }] })).length, 0);
});
test('SET comments for AI: wiring (Save -> SET Comments for AI -> Instructor message2 -> grouped AI loop; summary still below If19)', () => {
  const tgt = (n, i = 0) => ((wf.connections[n] || {}).main[i] || []).map((c) => c.node);
  assert.deepStrictEqual(tgt('Save New Form Row'), ['SET Comments for AI', 'SET Low Score Push']);
  assert.deepStrictEqual(tgt('SET Comments for AI'), ['Instructor message2']);
  assert.deepStrictEqual(tgt('If21'), ['Instructor message2'], 'old SAST path unchanged');
  assert.deepStrictEqual(tgt('Instructor message2'), ['Group feedback by instructor1']);
  const n = wf.nodes.find((x) => x.name === 'SET Comments for AI');
  assert.ok(!n.parameters.mode || n.parameters.mode === 'runOnceForAllItems');
  const pos = (name) => wf.nodes.find((x) => x.name === name).position;
  assert.ok(pos('Sheet Import Summary')[1] > pos('If19')[1] && pos('Sheet Import Summary')[1] > pos('SET Comments for AI')[1]);
  assert.deepStrictEqual(tgt('After Sheet Response'), ['If19', 'Sheet Import Summary']);
  const code = n.parameters.jsCode;
  assert.ok(!/first_name|last_name|Instructor\/Professor/.test(code), 'no instructor name in the AI path');
});

// ---- 2026-10-30: re-import skips rows already stored (migration 021), details, form-type guard, SET low-score push
const OLD_SAST_MSG = 'This sheet looks like the old SAST form (20 items) – choose Old SAST form.';
const SET_MSG = 'This sheet looks like the SET form (15 items) – choose SET.';
const SUP_MSG = 'This sheet looks like the supervisor form (SUP, 15 items) – choose SUP.';
const qRow = (n, extra = {}) => {
  const r = { ...SET_BASE, ...extra };
  for (let i = 1; i <= n; i++) r['Q' + i] = 4;
  return r;
};
test('Form-type guard: SET/SUP chosen on an old SAST sheet (Q16..Q20 or M/P columns) stops with a clear message', async () => {
  for (const ft of ['SET-15', 'SUP-15', 'SET', 'sup']) {
    await assert.rejects(() => normNew([qRow(20)], ft), (e) => e.message === OLD_SAST_MSG, ft + ' + Q1..Q20');
    await assert.rejects(() => normNew([newSheetRow()], ft), (e) => e.message === OLD_SAST_MSG, ft + ' + "16. ..." headers');
    await assert.rejects(() => normNew([{ ...SET_BASE, M1: 4, P1: 5 }], ft), (e) => e.message === OLD_SAST_MSG, ft + ' + M1/P1');
  }
  // a header only in a later row still counts; n8n's row_number key is never a header
  await assert.rejects(() => normNew([qRow(15, { row_number: 2 }), { ...qRow(15), Q17: '' }], 'SET-15'), (e) => e.message === OLD_SAST_MSG);
  // SET chosen on a supervisor sheet
  await assert.rejects(() => normNew([qRow(15, { 'Supervisor Email': 'c@x.ph' })], 'SET-15'), (e) => e.message === SUP_MSG);
  // the right choice passes
  assert.strictEqual((await normNew([qRow(15)], 'SET-15'))[0].Instrument, 'SET-15');
  assert.strictEqual((await normNew([qRow(15, { 'Supervisor Email': 'c@x.ph' })], 'SUP-15'))[0].Instrument, 'SUP-15');
});
test('Form-type guard: the old SAST form chosen on a 15-item sheet stops (SET, or SUP when there is a Supervisor Email column)', async () => {
  for (const ft of [undefined, '', 'SAST-20']) {
    await assert.rejects(() => normNew([qRow(15)], ft), (e) => e.message === SET_MSG, String(ft));
    await assert.rejects(() => normNew([{ ...SET_BASE, '1. gives': 4, '15. is ready': 5 }], ft), (e) => e.message === SET_MSG);
    await assert.rejects(() => normNew([qRow(15, { 'Supervisor Email': 'c@x.ph' })], ft), (e) => e.message === SUP_MSG, String(ft));
  }
  // real old SAST sheets pass: "1. .." .. "20. ..", M1..P10, or no numbered column at all
  assert.strictEqual((await normNew([newSheetRow()]))[0].M1, 3);
  assert.strictEqual((await normNew([{ 'Student ID': 'S1', 'Instructor/Professor': 'Ana', M1: 5, P10: 4 }], 'SAST-20'))[0].P10, 4);
  assert.strictEqual((await normNew([{ 'Student ID': 'S1', 'Instructor/Professor': 'Ana' }]))[0]['Student ID'], 'S1');
});
test('Form-type guard: bare SET / SUP are aliases of SET-15 / SUP-15 (any case); unknown values stay the old SAST sheet', async () => {
  for (const [ft, want] of [['SET', 'SET-15'], ['set', 'SET-15'], [' SUP ', 'SUP-15'], ['sup-15', 'SUP-15'], ['SET-15', 'SET-15']]) {
    const [r] = await normNew([qRow(15, { 'Supervisor Email': want === 'SUP-15' ? 'c@x.ph' : undefined })].map((x) => {
      if (x['Supervisor Email'] === undefined) delete x['Supervisor Email'];
      return x;
    }), ft);
    assert.strictEqual(r.Instrument, want, ft);
  }
  const [old] = await normNew([{ 'Student ID': 'S1', M1: 5 }], 'SETX');
  assert.ok(!('Instrument' in old));
});
test('Form-type guard: Sheet Import Error Response answers 422 with the exact message (n8n adds [line N]); nothing saved', () => {
  const raw = wf.nodes.find((x) => x.name === 'Sheet Import Error Response');
  assert.strictEqual(raw.parameters.options.responseCode, 422);
  const m = raw.parameters.responseBody.match(/^=\{\{([\s\S]*)\}\}$/);
  const answer = ($json) => new Function('$json', `return (${m[1]});`)($json);
  for (const msg of [OLD_SAST_MSG, SET_MSG, SUP_MSG]) {
    for (const err of [msg, msg + ' [line 232]', { message: msg + ' [line 9]' }]) {
      const r = answer({ error: err });
      assert.deepStrictEqual(r, { status: 'error', code: 'wrong_form_type', error: msg, message: msg });
    }
  }
  assert.ok(/no data rows/.test(answer({ error: 'The sheet has no data rows' }).message), 'other errors unchanged');
  assert.strictEqual(answer({ error: 'The sheet has no data rows' }).code, undefined);
  // the guard runs in Normalize Sheet Rows, before the lookups, the job row and every save
  const tgt = (n, i) => (((wf.connections[n] || {}).main || [])[i] || []).map((c) => c.node);
  assert.deepStrictEqual(tgt('Normalize Sheet Rows', 1), ['Sheet Import Error Response']);
  assert.deepStrictEqual(tgt('Sheet Import Error Response', 0), []);
});
test('Sheet row numbers: Normalize gives the Google Sheets row (row_number, else header + position); Save New Form Row reports it, the task id keeps the old row key', async () => {
  const rows = await normNew([qRow(15, { row_number: 2 }), qRow(15, { row_number: 5 }), qRow(15)], 'SET-15');
  assert.deepStrictEqual(rows.map((r) => r['Sheet Row']), [2, 5, 4]);
  const nodes = {
    'Google LINK from phone1': { body: { term_id: TERM, semester: '1st', academic_year: '2026' } },
    'Normalize Sheet Rows': { __items: rows },
    'get instructor id5': { __items: rows.map(() => ({ instructor_id: INSTR })) },
    'get subject id7': { __items: rows.map(() => ({ subject_id: SUBJ })) },
    'GET TERM BY ID1': { __items: rows.map(() => ({ current_term_id: TERM })) },
  };
  const mapped = (await run('Map to Target Format', {}, nodes)).map((x) => x.json);
  assert.deepStrictEqual(mapped.map((r) => [r.sheet_row, r.sheet_row_no]), [[1, 2], [2, 5], [3, 4]]);
  const save = wf.nodes.find((n) => n.name === 'Save New Form Row');
  const expr = save.parameters.options.queryReplacement.replace(/^=\{\{/, '').replace(/\}\}$/, '');
  const mock = (ref) => ({ first: () => ({ json: ref === 'Google LINK from phone1'
    ? { body: { link: 'https://docs.google.com/spreadsheets/d/SHEETID123/edit', user_id: JOB_ID } } : { id: JOB_ID } }) });
  const p = new Function('$', '$json', 'return ' + expr)(mock, mapped[1]);
  assert.strictEqual(p[8], `sheet:SHEETID123:${TERM}:2`, 'task id unchanged (rows stored before 021 still match)');
  assert.strictEqual(p[10], '5', 'row_no = the Google Sheets row');
  assert.strictEqual(p.length, 13);
});

const resRow = (result, row_no) => ({ result, row_no });
function newFormJob(saveItems, mapRows) {
  const nodes = sheetJobNodes({ 'Map to Target Format': { items: mapRows }, 'Save New Form Row': { items: saveItems } });
  for (const k of ['Code in JavaScript', 'Insert or update rows in a table1', 'Insert import_errors1', 'Group feedback by instructor1',
    'Unified AI Analysis1', 'Parse Unified AI Result1', 'insert data to student remarks2', 'store results2', 'insert to word cloud2',
    'update to overall total2']) delete nodes[k];
  return nodes;
}
const setMap = (n, unmatchedAt = []) => Array.from({ length: n }, (_, i) => ({
  instrument: 'SET-15', current_term_id: TERM, sheet_row: i + 1, sheet_row_no: i + 2,
  instructor_ID: unmatchedAt.includes(i + 2) ? null : INSTR, true_subject_id: SUBJ }));
test('Re-import (incident 2026-10-30): the same 40-row SET sheet again = 0 new, 38 already imported (skipped, not errors), 2 refused', async () => {
  // sheet rows 2..41; row 39 unmatched (never reaches the save); row 38 a bad rating; every other row is stored already
  const map = setMap(40, [39]);
  const saves = map.filter((r) => r.instructor_ID).map((r) => (r.sheet_row_no === 38
    ? resRow({ saved: false, reason: 'invalid_rating', item_no: 4 }, r.sheet_row_no)
    : resRow({ saved: false, reason: 'already_imported', match: r.sheet_row_no < 30 ? 'task_id' : 'stamp', response_id: r.sheet_row_no }, r.sheet_row_no)));
  const s = (await runJob('Sheet Import Summary', newFormJob(saves, map), { lenient: true }))[0].json;
  assert.deepStrictEqual([s.rows, s.matched, s.unmatched, s.imported_count, s.error_count], [40, 39, 1, 0, 2]);
  assert.strictEqual(s.term_id, TERM);
  const d = s.details;
  assert.deepStrictEqual(Object.keys(d), ['form_type', 'term_label', 'rows', 'new', 'already_imported', 'excluded_under_5', 'rejected', 'reasons']);
  assert.deepStrictEqual([d.form_type, d.term_label, d.rows, d.new, d.already_imported, d.excluded_under_5, d.rejected],
    ['SET-15', null, 40, 0, 38, 0, 2]);
  assert.strictEqual(d.new + d.already_imported + d.rejected, d.rows);
  assert.deepStrictEqual(d.reasons.map((r) => [r.reason, r.kind, r.count]),
    [['already_imported', 'skipped', 38], ['unmatched', 'rejected', 1], ['invalid_rating', 'rejected', 1]]);
  const already = d.reasons[0];
  assert.strictEqual(already.plain_text, 'already imported earlier – skipped');
  assert.deepStrictEqual(already.rows, Array.from({ length: 40 }, (_, i) => i + 2).filter((r) => r !== 38 && r !== 39));
  assert.deepStrictEqual(d.reasons[1].rows, [39]);
  assert.deepStrictEqual(d.reasons[2].rows, [38]);
  for (const r of d.reasons) assert.deepStrictEqual(Object.keys(r), ['reason', 'kind', 'plain_text', 'count', 'rows']);
  assert.ok(/^Import finished: 0 of 40 rows saved, 38 already imported earlier – skipped \(row 2, 3, 4, 5, 6, 7, 8, 9, \.\.\.\)/.test(s.message), s.message);
  assert.ok(/1 unmatched rows sent to Import Errors/.test(s.message) && /Not saved: 1 not saved: a rating that is not 1 to 5 \(row 38\)/.test(s.message), s.message);
  assert.ok(!/already imported[^;]*Not saved/.test(s.message.split('Not saved:')[1] || ''), 'already imported is not listed as not saved');
  assert.ok(!/student|2021-/i.test(JSON.stringify(d)), 'no student id in details');
});
test('First import: details count new (incl. under-5), refused reasons with sheet rows; imported = new, errors = refused only', async () => {
  const map = setMap(6, [7]);
  const saves = [
    resRow({ saved: true, counted: true, updated: false }, 2), resRow({ saved: true, counted: false }, 3),
    resRow({ saved: false, reason: 'duplicate_student' }, 4), resRow({ saved: false, reason: 'duplicate_stamp', duplicate_of: { source: 'sast' } }, 5),
    { error: 'boom', row_no: 6 },
  ];
  const s = (await runJob('Sheet Import Summary', newFormJob(saves, map), { lenient: true }))[0].json;
  const d = s.details;
  assert.deepStrictEqual([s.imported_count, s.error_count, d.new, d.already_imported, d.excluded_under_5, d.rejected], [2, 4, 2, 0, 1, 4]);
  assert.deepStrictEqual(d.reasons.map((r) => [r.reason, r.kind, r.rows]),
    [['excluded_under_5', 'excluded', [3]], ['unmatched', 'rejected', [7]], ['duplicate_student', 'rejected', [4]],
      ['duplicate_stamp', 'rejected', [5]], ['database_error', 'rejected', [6]]]);
  assert.ok(!/already imported/.test(s.message), s.message);
  assert.ok(/2 of 6 rows saved/.test(s.message) && /this stamp number was already used this term \(row 5\)/.test(s.message), s.message);
});
test('Old SAST sheet: details too (form_type SAST-20, unmatched rows by sheet row, failed saves counted)', async () => {
  const nodes = sheetJobNodes({
    'Map to Target Format': { items: [{ instructor_ID: INSTR, true_subject_id: SUBJ, current_term_id: TERM }, { instructor_ID: '', true_subject_id: SUBJ },
      { instructor_ID: INSTR, true_subject_id: SUBJ }, { instructor_ID: INSTR, true_subject_id: SUBJ }, { instructor_ID: INSTR, true_subject_id: SUBJ }] },
    'Insert or update rows in a table1': { runs: [[[{ id: 1 }, { error: 'duplicate key' }]], [[{ id: 3 }, { id: 4 }]]] },
  });
  const s = (await runJob('Sheet Import Summary', nodes))[0].json;
  const d = s.details;
  assert.deepStrictEqual([d.form_type, d.rows, d.new, d.already_imported, d.rejected, s.imported_count, s.error_count], ['SAST-20', 5, 3, 0, 2, 3, 2]);
  assert.deepStrictEqual(d.reasons.map((r) => [r.reason, r.count, r.rows]), [['unmatched', 1, [3]], ['database_error', 1, []]]);
  assert.strictEqual(s.term_id, TERM);
});
test('Mark Import Job Done writes details (+ term_label from academic_terms) and keeps the old message', () => {
  const n = wf.nodes.find((x) => x.name === 'Mark Import Job Done');
  const q = n.parameters.query;
  assert.ok(/details = CASE WHEN \$12::jsonb = '\{\}'::jsonb THEN details/.test(q), 'no details: column kept');
  assert.ok(/jsonb_set\(\$12::jsonb, '\{term_label\}'/.test(q) && /public\.academic_terms/.test(q) && /\$13::text/.test(q));
  assert.ok(/message = CASE/.test(q) && /imported_count = \$5::int/.test(q) && /error_count = \$6::int/.test(q));
  assert.ok(!/\$1[4-9]/.test(q), '13 parameters');
});

function if28Fires($json) {
  const c = wf.nodes.find((x) => x.name === 'If28').parameters.conditions;
  assert.strictEqual(c.combinator, 'or');
  return c.conditions.some((cond) => {
    const m = /^=\{\{([\s\S]*)\}\}$/.exec(cond.leftValue.trim());
    assert.deepStrictEqual(cond.operator, { type: 'boolean', operation: 'true', singleValue: true });
    return new Function('$json', `return (${m[1]});`)($json) === true;
  });
}
test('SET low-score push: once per instructor per import, pooled overall_pct < 60, SET-15 only, no student data', async () => {
  const A = INSTR, B = SUBJ, C = STAFF, D = ANA, E = '99999999-0000-4000-8000-000000000009';
  const src = (instrument, instructor) => ({ instrument, instructor_ID: instructor, current_term_id: TERM, true_subject_id: SUBJ,
    details: { student_id: '2021-0001' }, 'Remarks & Suggestions': 'secret comment' });
  const ok = (pct, extra = {}) => ({ result: { saved: true, instrument: extra.instrument || 'SET-15', counted: true,
    result: { respondents: 3, overall_mean: pct === null ? null : pct / 20, overall_pct: pct, verbal_label: 'x' } } });
  const sources = [src('SET-15', A), src('SET-15', B), src('SET-15', A), src('SUP-15', C), src('SET-15', D), src('SET-15', E), src('SET-15', B)];
  const saved = [ok(70), ok(75), ok(55.5), ok(40, { instrument: 'SUP-15' }),
    { result: { saved: false, reason: 'already_imported', response_id: 9 } }, ok(null),
    { result: { saved: false, reason: 'invalid_rating' } }];
  const $ = (ref) => { assert.strictEqual(ref, 'Sheet New Form?'); return { all: () => sources.map((json) => ({ json })) }; };
  const fn = new AsyncFunction('$', '$input', codeOf('SET Low Score Push'));
  const out = (await fn($, { all: () => saved.map((json, i) => ({ json, pairedItem: { item: i } })) })).map((o) => o.json);
  assert.deepStrictEqual(out, [{ set_low_score: true, instrument: 'SET-15', instructor_id: A, term_id: TERM, overall_pct: 55.5,
    overall_mean: 2.775, respondents: 3 }], 'A once (its LAST pooled value); B 75 %; C supervisor; D nothing new; E no score');
  assert.ok(!/2021-0001|secret/.test(JSON.stringify(out)));
  // 60 % exactly is not low; a re-import that saves nothing sends nothing
  assert.deepStrictEqual(await fn($, { all: () => [{ json: ok(60), pairedItem: { item: 0 } }] }), []);
  assert.deepStrictEqual(await fn($, { all: () => [{ json: { result: { saved: false, reason: 'already_imported' } }, pairedItem: { item: 0 } }] }), []);
  // If28 (the same push): fires for that item, and still for the SAST mean only below 3
  assert.strictEqual(if28Fires(out[0]), true);
  assert.strictEqual(if28Fires({ ...out[0], overall_pct: 60 }), false);
  assert.strictEqual(if28Fires({ ...out[0], overall_pct: null }), false);
  assert.strictEqual(if28Fires({ overall_pct: 10, instructor_id: A }), false, 'only SET Low Score Push items count');
  assert.strictEqual(if28Fires({ combined_score_mean: '2.5', instructor_id: A }), true);
  assert.strictEqual(if28Fires({ combined_score_mean: null, instructor_id: A }), false);
  assert.strictEqual(if28Fires({ combined_score_mean: 3, instructor_id: A }), false);
});
test('SET low-score push: wiring (Save New Form Row -> SET Low Score Push -> If28 -> the existing FCM push; summary still last)', () => {
  const tgt = (n, i = 0) => ((wf.connections[n] || {}).main[i] || []).map((c) => c.node);
  assert.deepStrictEqual(tgt('SET Low Score Push'), ['If28']);
  assert.deepStrictEqual(tgt('If28'), ['who is the departmeht head5', "Get the Phone's Token from Supabase5"]);
  for (const n of ['who is the departmeht head5', "Get the Phone's Token from Supabase5"]) {
    assert.ok(/\$json\.instructor_id/.test(wf.nodes.find((x) => x.name === n).parameters.options.queryReplacement), n + ' reads instructor_id');
  }
  const node = wf.nodes.find((x) => x.name === 'SET Low Score Push');
  assert.ok(!node.parameters.mode || node.parameters.mode === 'runOnceForAllItems');
  const pos = (name) => wf.nodes.find((x) => x.name === name).position;
  assert.ok(pos('Sheet Import Summary')[1] > pos('SET Low Score Push')[1]);
  assert.ok(!/student_id|Student ID|Remarks|first_name|last_name/.test(node.parameters.jsCode.replace(/^\/\/.*$/gm, '')));
});
test('Migration 021: save_eval_response keeps 16 args and skips rows from an earlier import (task_id, supervisor, stamp, student) as already_imported', () => {
  const sql = fs.readFileSync(path.join(__dirname, '..', '..', 'migrations', '021_eval_import_skip.sql'), 'utf8');
  assert.ok(/create or replace function public\.save_eval_response\(\s*p_instrument_code text, p_instructor_id text, p_subject_id text, p_term_id text, p_answers jsonb,\s*p_comments text, p_source text, p_stamp_no text default null, p_stamp_source text default null,\s*p_evaluator_user_id text default null, p_import_job_id text default null, p_task_id text default null,\s*p_created_by text default null, p_status text default 'submitted', p_recompute boolean default true,\s*p_details jsonb default null\)/.test(sql));
  assert.ok(!/drop function/i.test(sql), 'no drop: same signature, grants kept');
  assert.ok(/v_old\.import_job_id is distinct from v_job/.test(sql) && /'match', 'stamp'/.test(sql) && /'match', 'student_id'/.test(sql));
  assert.ok(/x\.instrument_id = v_inst\.id and x\.instructor_id = v_instructor\s+and x\.subject_id is not distinct from v_subject/.test(sql), 'stamp skip only on the same instrument + instructor + subject');
  assert.ok(/return jsonb_build_object\('saved', false, 'reason', 'duplicate_student'\)/.test(sql) && /'reason', 'duplicate_stamp'/.test(sql));
  assert.ok(/add column if not exists details jsonb/.test(sql));
  const body = sql.slice(0, sql.indexOf('-- Verification'));
  assert.ok(/\nrollback;\n-- commit;/.test(body), 'preview first (016-020 convention)');
  assert.ok(/^[\x00-\x7f]*$/.test(sql), 'ASCII only, like 019 / 020');
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
