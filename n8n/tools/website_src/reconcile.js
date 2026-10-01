// Python gives the ratings, Gemini cross-checks; text comes from Gemini.
const AGREEMENT_MIN = 0.30;   // rows that must not clearly conflict (see conflictRows)
// @@include profanity.js
// @@include remark_verify.js
// @@include layout_check.js

const QUESTIONS         = 10;
const TOTAL_ROWS        = QUESTIONS * 2;
const MAX_AGREED_BLANKS = 10;
const FLAG_DIFF_MARGIN  = 2;      // levels apart for a real conflict (S vs VS is not one)
const WEAK_TOP_SCORE    = 0.05;
const WEAK_MARGIN       = 0.02;
const WEAK_ROW_LIMIT    = 4;
const WEAK_BLANK_LIMIT    = 3;    // website only
const WEAK_DISAGREE_LIMIT = 3;    // website only
const FALLBACK_GRID_SOURCE  = 'fallback';
const CRITICAL_QUALITY_ISSUES = ['low_light', 'scribbled_grid', 'damaged_form'];
const AGREEMENT_MIN_ROWS = Math.ceil(AGREEMENT_MIN * TOTAL_ROWS - 1e-9);   // 0.30 -> 6 of 20
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

// layout safety net (layout_check.js): decided BEFORE the rows are compared, because on a form
// with another layout a confident scanner row is confidently wrong and must not win over Gemini
const layoutSignals = lcLayoutSignals(py, gemini, geminiOk);
let exactAgreeRows = 0;
for (const [section, prefix] of [['management', 'm'], ['performance', 'p']]) {
  for (let i = 0; i < QUESTIONS; i++) if (pythonScore(section, i) === geminiScore(`${prefix}${i + 1}`)) exactAgreeRows++;
}
if (lcFallbackSignal(py, geminiOk && pythonOk, exactAgreeRows, TOTAL_ROWS)) layoutSignals.push('fallback_grid_low_agreement');
const layoutMismatch = layoutSignals.length > 0;

const scores = {};
const comparisons = [];
let agreedBlanks = 0;
let filledByGemini = 0;
let realDifferences = 0;
let agreeRows = 0;
let weakRows = 0;
const conflictRows = [];   // differ AND count against the agreement rule
const resolvedRows = [];   // differ, but kept as the scanner read them
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
    // scanner sure of a mark -> its reading wins; no confidence data or a layout mismatch -> not sure
    const pythonConfident = !layoutMismatch && p !== null && confByRow[rowIndex] !== undefined && !conf.weak;
    const bothRead = p !== null && g !== null;
    const farApart = bothRead && Math.abs(p - g) >= FLAG_DIFF_MARGIN;
    let resolvedBy = null;
    if (!agree) {
      if (pythonConfident) resolvedBy = 'python_confident';
      else if (bothRead && !farApart) resolvedBy = 'adjacent_level';
      (resolvedBy ? resolvedRows : conflictRows).push(`${key} (scanner ${letterOf(p)}, AI ${letterOf(g)})`);
    }

    if (p !== null && conf.weak) weakRows++;

    let used;
    let source;
    if (p !== null) {
      used = p;
      source = 'python';
      if (g !== null) {
        if (farApart) realDifferences++;
        if (conf.weak && farApart) weakDisagreeRows.push(key);
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
    comparisons.push({ question: key, python: p, gemini: g, used, source, agree, python_weak: p !== null && conf.weak,
      resolved_by: resolvedBy });
  }
}

const agreementKnown = geminiOk && pythonOk;
const agreementPercent = agreementKnown ? Math.round((agreeRows / TOTAL_ROWS) * 100) : null;
// confident-scanner and one-level differences count as agreeing for the flag
const flagAgreeRows = TOTAL_ROWS - conflictRows.length;
const flagAgreePercent = Math.round((flagAgreeRows / TOTAL_ROWS) * 100);
const resolvedText = resolvedRows.length
  ? ` ${resolvedRows.length} other differing row(s) were kept as the scanner read them (scanner confident, or only one level apart).`
  : '';
const agreementText = agreementKnown
  ? `Python and Gemini agree on ${agreeRows} of ${TOTAL_ROWS} rows (${agreementPercent}%).${resolvedText}`
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
// Gemini's closest entry from the known subject list, used by Decide when it fits. No instructor
// list is sent to Gemini on the website, so there is no instructor pick: the form's instructor
// is matched against the database by Website Form Match / Decide only.
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
if (agreementKnown && flagAgreeRows < AGREEMENT_MIN_ROWS) reasons.push(`omr_agreement_below_${AGREEMENT_MIN_PCT}`);
if (weakRows >= WEAK_ROW_LIMIT) reasons.push(`low_confidence_ocr_${weakRows}_rows`);
if (agreedBlanks > MAX_AGREED_BLANKS) reasons.push(`too_many_blanks_${agreedBlanks}_of_20`);
// unlike phone, grid_not_detected stays even with Gemini ratings
if (weakGeminiBlankRows.length >= WEAK_BLANK_LIMIT) reasons.push(`python_weak_gemini_blank_${weakGeminiBlankRows.length}_rows`);
if (weakDisagreeRows.length >= WEAK_DISAGREE_LIMIT) reasons.push(`python_weak_disagreement_${weakDisagreeRows.length}_rows`);
if (py.grid_source === FALLBACK_GRID_SOURCE) reasons.push('omr_fallback_grid');
if (layoutMismatch) reasons.push('layout_mismatch');

const noteParts = [];
if (geminiOk && gemini.document_check && gemini.document_check.is_sast_form === false) {
  noteParts.push(gemini.document_check.reason || 'This does not look like a SAST evaluation form.');
}
if (reasons.includes('poor_scan_quality')) {
  noteParts.push(gemini.scan_quality.reason || 'The scan quality looked poor.');
}
if (reasons.some((r) => r.startsWith('omr_agreement_below_'))) {
  noteParts.push(`Python and Gemini clearly disagree on ${conflictRows.length} of ${TOTAL_ROWS} rows where the scanner was unsure or read nothing, so only ${flagAgreeRows} of ${TOTAL_ROWS} rows (${flagAgreePercent}%) count as agreeing; at least ${AGREEMENT_MIN_PCT}% is required. They differ on ${conflictRows.join(', ')}. Check those answers.${resolvedText}`);
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
if (weakGeminiBlankRows.length >= WEAK_BLANK_LIMIT) {
  noteParts.push(`The scanner read a faint mark on ${weakGeminiBlankRows.join(', ')} that the AI sees as blank (possibly a speck). Check whether those questions were really answered.`);
}
if (weakDisagreeRows.length >= WEAK_DISAGREE_LIMIT) {
  noteParts.push(`The scanner was unsure on ${weakDisagreeRows.join(', ')} and the AI read a rating two or more levels away. Check those answers.`);
}
if (py.grid_source === FALLBACK_GRID_SOURCE && py.table_found !== false) {
  noteParts.push('The rating grid could not be located, so a fixed fallback grid was used. The row positions may be wrong; verify every rating.');
}
if (layoutMismatch) noteParts.push(lcNote(layoutSignals, geminiOk ? lcColumnOrder(gemini) : null));
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
      flag_agreement_rows: agreementKnown ? flagAgreeRows : null,   // what the agreement rule checks
      resolved_differences: resolvedRows.length,
      real_differences: realDifferences,
      filled_from_gemini: filledByGemini,
      comparisons,   // Flutter review screen lists these
    },
    table_found: typeof py.table_found === 'boolean' ? py.table_found : null,
    grid_source: py.grid_source ?? null,
    layout_check: {
      mismatch: layoutMismatch,
      signals: layoutSignals,
      column_order: geminiOk ? lcColumnOrder(gemini) : null,   // Gemini's header reading, if any
      python_layout: py.layout ?? null,                         // the OMR's layout evidence, if any
    },
    review_note: reviewNote,
    comparisons,
    python_raw_ratings: py.ratings ?? null,
    summary: `${Object.values(scores).filter((s) => s !== null).length}/${TOTAL_ROWS} answered`
      + (agreementPercent !== null ? `, agreement ${agreementPercent}%` : '')
      + (needsReview ? ` — flagged: ${reasons.join(', ')}` : ' — clean'),
  },
}];
