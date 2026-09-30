// Python gives the ratings, Gemini cross-checks; text comes from Gemini.
const AGREEMENT_MIN = 0.80;   // rows Python and Gemini must agree on
// @@include profanity.js
// @@include remark_verify.js

const QUESTIONS         = 10;
const TOTAL_ROWS        = QUESTIONS * 2;
const MAX_AGREED_BLANKS = 10;
const FLAG_DIFF_MARGIN  = 2;      // only used for poor_scan_quality
const WEAK_TOP_SCORE    = 0.05;
const WEAK_MARGIN       = 0.02;
const WEAK_ROW_LIMIT    = 4;
const WEAK_BLANK_LIMIT    = 1;    // website only
const WEAK_DISAGREE_LIMIT = 1;    // website only
const FALLBACK_GRID_SOURCE  = 'fallback';
const CRITICAL_QUALITY_ISSUES = ['low_light', 'scribbled_grid', 'damaged_form'];
const AGREEMENT_MIN_ROWS = Math.ceil(AGREEMENT_MIN * TOTAL_ROWS - 1e-9);   // 0.80 -> 16 of 20
const AGREEMENT_MIN_PCT  = Math.round(AGREEMENT_MIN * 100);

const LETTER_TO_SCORE = { O: 5, VS: 4, S: 3, F: 2, US: 1 };
const SCORE_TO_LETTER = { 5: 'O', 4: 'VS', 3: 'S', 2: 'F', 1: 'US' };

function toScore(value) {
  if (value === null || value === undefined) return null;
  const raw = String(value).trim();
  if (raw === '' || raw.toLowerCase() === 'blank') return null;
  const letter = LETTER_TO_SCORE[raw.toUpperCase()];
  if (letter !== undefined) return letter;
  const n = Number(raw);
  return Number.isFinite(n) && n >= 1 && n <= 5 ? n : null;
}
const letterOf = (s) => (s === null ? 'blank' : SCORE_TO_LETTER[s] || String(s));

function errText(j) {
  if (!j) return '';
  const e = j.error;
  if (typeof e === 'string') return e;
  if (e && typeof e === 'object') return String(e.message || e.description || JSON.stringify(e)).slice(0, 300);
  return String(j.message || '').slice(0, 300);
}

// on failure the OMR item is just { error }
let py = {};
try { py = $('Website Python OMR').first(0).json || {}; } catch (e) {}
const pythonOk = !!(py && py.ratings && typeof py.ratings === 'object'
  && (Array.isArray(py.ratings.management) || Array.isArray(py.ratings.performance)));

function pythonScore(section, index) {
  const cell = (py.ratings?.[section] ?? [])[index];
  if (!cell || !cell.detected) return null;
  return toScore(cell.rating_name ?? cell.score ?? cell.answer);
}

const confByRow = {};
for (const d of (py.omr_debug ?? [])) {
  const ri = Number(d.row_index);
  if (!Number.isInteger(ri)) continue;
  const sorted = Object.values(d.scores ?? {}).map(Number).filter(Number.isFinite).sort((a, b) => b - a);
  const top = sorted[0] ?? 0;
  const second = sorted[1] ?? 0;
  confByRow[ri] = {
    top: Number(top.toFixed(4)),
    margin: Number((top - second).toFixed(4)),
    weak: top < WEAK_TOP_SCORE || (top - second) < WEAK_MARGIN,
  };
}

// read Gemini by name; the input may be the re-check item
const gIn = $input.first()?.json ?? {};
let geminiCallFailed = false;
let geminiErr = '';
let gResp = null;
try {
  const j = $('Website AI Validation').first(0).json;
  if (j && j.candidates) gResp = j;
} catch (e) {}
if (!gResp && gIn.error !== undefined && !gIn.candidates) {
  geminiCallFailed = true;
  geminiErr = errText(gIn);
}

let gemini = {};
let geminiOk = false;
try {
  const text = gResp?.candidates?.[0]?.content?.parts?.[0]?.text || '';
  const match = String(text).match(/\{[\s\S]*\}/);
  if (match) {
    const parsed = JSON.parse(match[0]);
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      gemini = parsed;
      geminiOk = true;
    }
  }
} catch (e) {
  console.log('Gemini parse failed:', e.message);
}

function geminiScore(key) {
  if (!geminiOk) return null;
  return toScore(gemini.ratings?.[key]);
}

const scores = {};
const comparisons = [];
let agreedBlanks = 0;
let filledByGemini = 0;
let realDifferences = 0;
let agreeRows = 0;
let weakRows = 0;
const differRows = [];
const weakGeminiBlankRows = [];
const weakDisagreeRows = [];

for (const [section, prefix] of [['management', 'm'], ['performance', 'p']]) {
  for (let i = 0; i < QUESTIONS; i++) {
    const key = `${prefix}${i + 1}`;
    const rowIndex = (prefix === 'm' ? 0 : QUESTIONS) + i;
    const conf = confByRow[rowIndex] ?? { top: null, margin: null, weak: false };

    const p = pythonScore(section, i);
    const g = geminiScore(key);
    const agree = p === g;   // both blank counts as agreeing
    if (agree) agreeRows++;
    else differRows.push(`${key} (scanner ${letterOf(p)}, AI ${letterOf(g)})`);

    if (p !== null && conf.weak) weakRows++;

    let used;
    let source;
    if (p !== null) {
      used = p;
      source = 'python';
      if (g !== null) {
        if (Math.abs(p - g) >= FLAG_DIFF_MARGIN) realDifferences++;
        if (conf.weak && p !== g) weakDisagreeRows.push(key);
      } else if (geminiOk && conf.weak) {
        weakGeminiBlankRows.push(key);
      }
    } else if (g !== null) {
      used = g;
      source = 'gemini_fill';
      filledByGemini++;
    } else {
      used = null;
      source = 'blank';
      agreedBlanks++;
    }
    scores[key] = used;
    comparisons.push({ question: key, python: p, gemini: g, used, source, agree, python_weak: p !== null && conf.weak });
  }
}

const agreementKnown = geminiOk && pythonOk;
const agreementPercent = agreementKnown ? Math.round((agreeRows / TOTAL_ROWS) * 100) : null;
const agreementText = agreementKnown
  ? `Python and Gemini agree on ${agreeRows} of ${TOTAL_ROWS} rows (${agreementPercent}%).`
  : null;

// text fields come only from Gemini
const PLACEHOLDERS = new Set(['', 'na', 'n/a', 'n.a.', 'none', 'nil', 'null', 'undefined', 'blank',
  'illegible', 'unreadable', 'unknown', '-', '--', '?', 'x', 'true', 'false']);
const oneLine = (v) => String(v ?? '').replace(/\s+/g, ' ').trim();
const cleanOcr = (v) => {
  const s = oneLine(v);
  return PLACEHOLDERS.has(s.toLowerCase()) ? '' : s;
};
const clean = (v) => {
  const s = oneLine(v);
  return (s === 'true' || s === 'false' || s === 'null' || s === 'undefined') ? '' : s;
};
const gText = (k) => (geminiOk && gemini[k] !== null && gemini[k] !== undefined && typeof gemini[k] !== 'object'
  ? gemini[k] : '');
const CONFIDENCE_LEVELS = new Set(['high', 'medium', 'low']);
// optional per-field confidence (website prompt only); anything else -> null (unknown)
const gConfidence = (k) => {
  const v = String(gText(k) || '').toLowerCase();
  return CONFIDENCE_LEVELS.has(v) ? v : null;
};

const ocrInstructor = cleanOcr(gText('instructor')).slice(0, 200);
const ocrSubject    = cleanOcr(gText('subject')).slice(0, 200);
// Gemini's closest entry from the known lists, used by Decide when it fits
const knownInstructor = cleanOcr(gText('known_instructor')).slice(0, 200);
const knownSubject    = cleanOcr(gText('known_subject')).slice(0, 60);
// optional: how sure Gemini was of its own reading (used by Decide to gate mismatch flags)
const instructorConfidence = gConfidence('instructor_confidence');
const subjectConfidence    = gConfidence('subject_confidence');
const studentIdConfidence  = gConfidence('student_id_confidence');
// remark is masked after the re-check
const remarkResult  = pfResolveRemark(oneLine(gText('remarks')).slice(0, 4000), 'Website Verify Remark Words');
const remarks       = remarkResult.text;

const reasons = [];
if (geminiCallFailed) reasons.push('ai_validation_failed');
else if (!geminiOk) reasons.push('gemini_unavailable');
if (!pythonOk) reasons.push('omr_unavailable');

if (geminiOk && gemini.document_check && gemini.document_check.is_sast_form === false) {
  reasons.push(`not_a_sast_form: ${gemini.document_check.reason || 'unrecognized image'}`);
}
if (py.table_found === false) reasons.push('grid_not_detected');

const qualityIsCostingUsAgreement = realDifferences > 0 || agreedBlanks > MAX_AGREED_BLANKS;
const qualityIssues = (gemini.scan_quality && gemini.scan_quality.issues) || [];
const hasCriticalQualityIssue = Array.isArray(qualityIssues) && qualityIssues.some((iss) => CRITICAL_QUALITY_ISSUES.includes(iss));
if (geminiOk && gemini.scan_quality && gemini.scan_quality.poor_quality === true && (qualityIsCostingUsAgreement || hasCriticalQualityIssue)) {
  reasons.push('poor_scan_quality');
}
if (agreementKnown && agreeRows < AGREEMENT_MIN_ROWS) reasons.push(`omr_agreement_below_${AGREEMENT_MIN_PCT}`);
if (weakRows >= WEAK_ROW_LIMIT) reasons.push(`low_confidence_ocr_${weakRows}_rows`);
if (agreedBlanks > MAX_AGREED_BLANKS) reasons.push(`too_many_blanks_${agreedBlanks}_of_20`);
// unlike phone, grid_not_detected stays even with Gemini ratings
if (weakGeminiBlankRows.length >= WEAK_BLANK_LIMIT) reasons.push(`python_weak_gemini_blank_${weakGeminiBlankRows.length}_rows`);
if (weakDisagreeRows.length >= WEAK_DISAGREE_LIMIT) reasons.push(`python_weak_disagreement_${weakDisagreeRows.length}_rows`);
if (py.grid_source === FALLBACK_GRID_SOURCE) reasons.push('omr_fallback_grid');

const noteParts = [];
if (geminiOk && gemini.document_check && gemini.document_check.is_sast_form === false) {
  noteParts.push(gemini.document_check.reason || 'This does not look like a SAST evaluation form.');
}
if (reasons.includes('poor_scan_quality')) {
  noteParts.push(gemini.scan_quality.reason || 'The scan quality looked poor.');
}
if (reasons.some((r) => r.startsWith('omr_agreement_below_'))) {
  noteParts.push(`Python and Gemini agree on only ${agreeRows} of ${TOTAL_ROWS} rows (${agreementPercent}%); at least ${AGREEMENT_MIN_PCT}% is required. They differ on ${differRows.join(', ')}. Check those answers.`);
}
if (reasons.some((r) => r.startsWith('low_confidence_ocr_'))) {
  noteParts.push(`The scanner itself was unsure of its reading on ${weakRows} row(s), even where it matched the AI's read. Please double-check those answers.`);
}
if (py.table_found === false) {
  noteParts.push(
    py.grid_source
      ? `Table corners were not found — ${String(py.grid_source).replace(/_/g, ' ')} was used instead. All data needs verification.`
      : 'Table corners were not found in the scan, so an estimated crop was used. All data needs verification.'
  );
}
if (weakGeminiBlankRows.length) {
  noteParts.push(`The scanner read a faint mark on ${weakGeminiBlankRows.join(', ')} that the AI sees as blank (possibly a speck). Check whether those questions were really answered.`);
}
if (weakDisagreeRows.length) {
  noteParts.push(`The scanner was unsure on ${weakDisagreeRows.join(', ')} and the AI read a different rating. Check those answers.`);
}
if (py.grid_source === FALLBACK_GRID_SOURCE && py.table_found !== false) {
  noteParts.push('The rating grid could not be located, so a fixed fallback grid was used. The row positions may be wrong; verify every rating.');
}
if (!pythonOk) {
  noteParts.push(`The OMR scanner returned no ratings for this page${errText(py) ? ` (${errText(py)})` : ''}, so the answers shown come from the AI only. Verify every rating.`);
}
if (geminiCallFailed) {
  noteParts.push(`AI validation call failed, so this scan could not be cross-checked and no text (student ID, remarks, date) was read: ${geminiErr || 'Gemini request failed'}.`);
} else if (!geminiOk) {
  noteParts.push('The AI cross-check did not return a result for this scan, so the OMR reading has not been verified against a second source and no text (student ID, remarks, date) was read. Please check it manually.');
}

const needsReview = reasons.length > 0;
// always show agreement on flagged pages
if (needsReview && agreementText && !reasons.some((r) => r.startsWith('omr_agreement_below_'))) {
  noteParts.push(agreementText);
}
const reviewNote = noteParts.length ? noteParts.join(' ') : null;

return [{
  json: {
    _stage: 'reconciled',
    ocr_instructor: ocrInstructor,   // checked later by Website Form Match
    ocr_subject: ocrSubject,
    known_instructor: knownInstructor,
    known_subject: knownSubject,
    instructor_confidence: instructorConfidence,
    subject_confidence: subjectConfidence,
    student_id_confidence: studentIdConfidence,
    ay_semester: oneLine(gText('ay_semester')).slice(0, 100),
    remarks,
    remarks_masked: remarkResult.masked,
    remark_check: remarkResult.check,
    date: clean(gText('date')).slice(0, 50),
    student_id: clean(gText('student_id')).replace(/\s+/g, '').slice(0, 50),
    text_source: geminiOk ? 'gemini' : 'none',

    ratings: scores,
    document_check: gemini.document_check ?? null,
    needs_review: needsReview,
    review_reasons: reasons,
    agreement_percent: agreementPercent,
    agreement_rows: agreementKnown ? agreeRows : null,
    omr_agreement: {
      cross_checked: agreementKnown,
      agreement_rows: agreementKnown ? agreeRows : null,
      agreement_percent: agreementPercent,
      agreement_min_percent: AGREEMENT_MIN_PCT,
      real_differences: realDifferences,
      filled_from_gemini: filledByGemini,
      comparisons,   // Flutter review screen lists these
    },
    table_found: typeof py.table_found === 'boolean' ? py.table_found : null,
    grid_source: py.grid_source ?? null,
    review_note: reviewNote,
    comparisons,
    python_raw_ratings: py.ratings ?? null,
    summary: `${Object.values(scores).filter((s) => s !== null).length}/${TOTAL_ROWS} answered`
      + (agreementPercent !== null ? `, agreement ${agreementPercent}%` : '')
      + (needsReview ? ` — flagged: ${reasons.join(', ')}` : ' — clean'),
  },
}];
