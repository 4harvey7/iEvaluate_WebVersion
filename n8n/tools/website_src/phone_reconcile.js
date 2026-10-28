// Same rules as 'Website Reconcile OCR OMR', for phone scans.
const AGREEMENT_MIN = 0.30;   // rows that must not clearly conflict (see conflictRows)
// @@include profanity.js
// @@include remark_verify.js
// @@include name_match.js
// @@include layout_check.js
// @@include stamp.js
// @@include ocr_crosscheck.js

const QUESTIONS         = 10;
const TOTAL_ROWS        = QUESTIONS * 2;
const MAX_AGREED_BLANKS = 10;
const MIN_ANSWERED      = 5;  // teacher rule: fewer answered items -> the form is discarded
const FLAG_DIFF_MARGIN  = 2;  // levels apart for a real conflict (S vs VS is not one)
const WEAK_TOP_SCORE    = 0.05;
const WEAK_MARGIN       = 0.02;
const WEAK_ROW_LIMIT    = 4;  // review if Python is unsure on this many rows
const INVALID_MARK_TEXT = { x: 'an x', shade: 'a shaded bubble', circle: 'a circle', dot: 'a dot', double: 'two marks' };
const CRITICAL_QUALITY_ISSUES = ['low_light', 'scribbled_grid', 'damaged_form']; // both engines can agree on the same wrong answer
const AGREEMENT_MIN_ROWS = Math.ceil(AGREEMENT_MIN * TOTAL_ROWS - 1e-9);   // 0.30 -> 6 of 20
const AGREEMENT_MIN_PCT  = Math.round(AGREEMENT_MIN * 100);
const INSTRUCTOR_MATCH_MIN    = 0.70;   // local instructor match (same as Website Decide's NAME_MATCH_MIN)
const INSTRUCTOR_MATCH_MARGIN = 0.10;   // best must beat the next by this (Website Decide's MATCH_MARGIN)

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

// Python may have crashed; don't let that break this node
let py = {};
try {
  py = $('image to ocr and omr python').first().json || {};
} catch(e) {}
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

// Teacher rule (OMR, 2026-10-08): only a check or slash counts. The OMR returns a rejected row like
// a blank one (detected: false) and names the mark in omr_debug[].invalid_mark ('x' | 'shade' |
// 'circle' | 'dot'); an older OMR build sends no such field (= null). A rejected row stays blank:
// Gemini never fills it, it is not a Python-vs-Gemini conflict and not a blank for too_many_blanks.
const invalidByRow = {};
for (const d of (py.omr_debug ?? [])) {
  const ri = Number(d.row_index);
  const mark = d.invalid_mark === null || d.invalid_mark === undefined ? '' : String(d.invalid_mark).trim().toLowerCase();
  if (Number.isInteger(ri) && mark) invalidByRow[ri] = mark.slice(0, 20);
}
// a row Python read as a rating is never invalid, whatever omr_debug says
const invalidMarkOf = (p, rowIndex) => (p === null ? (invalidByRow[rowIndex] ?? null) : null);
const markText = (m) => INVALID_MARK_TEXT[m] || `a "${m}" mark`;

let gemini = {};
let geminiOk = false;
try {
  const text = $('ai validation ocr and omr').first().json?.candidates?.[0]?.content?.parts?.[0]?.text || '';
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
  for (let i = 0; i < QUESTIONS; i++) {
    const p = pythonScore(section, i);
    const g = invalidMarkOf(p, (prefix === 'm' ? 0 : QUESTIONS) + i) ? null : geminiScore(`${prefix}${i + 1}`);
    if (p === g) exactAgreeRows++;
  }
}
if (lcFallbackSignal(py, geminiOk && pythonOk, exactAgreeRows, TOTAL_ROWS)) layoutSignals.push('fallback_grid_low_agreement');
const layoutMismatch = layoutSignals.length > 0;

const scores         = {};
const comparisons    = [];
let agreedBlanks     = 0;
let filledByGemini   = 0;
let realDifferences  = 0;
let agreeRows        = 0;
let weakRows         = 0;
const conflictRows   = [];   // differ AND count against the agreement rule
const resolvedRows   = [];   // differ, but kept as the scanner read them
const invalidRows    = [];   // { key, mark }: a non-check mark, left blank

for (const [section, prefix] of [['management', 'm'], ['performance', 'p']]) {
  for (let i = 0; i < QUESTIONS; i++) {
    const key = `${prefix}${i + 1}`;
    const rowIndex = (prefix === 'm' ? 0 : QUESTIONS) + i;
    const conf = confByRow[rowIndex] ?? { top: null, margin: null, weak: false };

    const p = pythonScore(section, i);
    const invalidMark = invalidMarkOf(p, rowIndex);
    const gRead = geminiScore(key);
    const g = invalidMark ? null : gRead;   // the check-only rule overrides Gemini's reading of that mark
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
      if (farApart) realDifferences++;
    } else if (invalidMark) {
      // the scanner saw an x, shade, circle or dot: not an answer, never filled from Gemini
      used = null;
      source = 'invalid_mark';
      invalidRows.push({ key, mark: invalidMark });
    } else if (g !== null) {
      // Python saw blank, Gemini saw a mark
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
      resolved_by: resolvedBy, invalid_mark: invalidMark, ...(invalidMark ? { gemini_raw: gRead } : {}) });
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
const gText = (k) => {
  const v = geminiOk ? gemini[k] : '';
  return (v === null || v === undefined || typeof v === 'object') ? '' : v.toString().trim();
};

// subject: Gemini's pick from the known SUBJECT list replaces the raw reading when it is on the
// list and fits it. instructor: Gemini gets no instructor list (privacy); the raw reading is
// matched locally against the creditable instructors ('Phone Instructor Candidates'). Only a clear
// match replaces it (exact database name, so 'get instructor id6' finds it); several close matches
// flag the scan for review; no match keeps the raw reading for 'get instructor id6' as before (not
// found there -> import error, reviewed by staff).
let known = {};
try { known = $('Phone Known Names').first().json || {}; } catch (e) {}
let instructorCands = null;   // null = lookup unavailable
try {
  const c = $('Phone Instructor Candidates').first().json || {};
  if (Number(c.candidates_version) === 1) instructorCands = nmList(c.instructor_names);
} catch (e) {}
const rawInstructor = gText('instructor');
const rawSubject    = gText('subject');
const instructorMatch = (rawInstructor && instructorCands)
  ? nmBestInstructor(rawInstructor, instructorCands, INSTRUCTOR_MATCH_MIN, INSTRUCTOR_MATCH_MARGIN)
  : { match: rawInstructor ? 'unavailable' : 'unreadable', name: '', score: null, contenders: [] };
const resolvedInstructor = (instructorMatch.match === 'found' && instructorMatch.name) || rawInstructor;
const resolvedSubject = (rawSubject && nmKnownSubject(gText('known_subject'), known.subjects, rawSubject)) || rawSubject;
// remark is masked after the re-check
const remarkResult = pfResolveRemark(gText('remarks'), 'Phone Verify Remark Words');

let reasons = [];
if (!geminiOk) reasons.push('gemini_unavailable');
if (!pythonOk) reasons.push('omr_unavailable');

// a blank instructor/subject would shift the lookup query params
if (!resolvedInstructor) reasons.push('missing_instructor');
// several instructors fit the reading about equally: review, never guess
if (instructorMatch.match === 'ambiguous') reasons.push('instructor_ambiguous');
if (!resolvedSubject) reasons.push('missing_subject');

if (geminiOk && gemini.document_check && gemini.document_check.is_sast_form === false) {
  reasons.push(`not_a_sast_form: ${gemini.document_check.reason || 'unrecognized image'}`);
}

if (py.table_found === false) reasons.push('grid_not_detected');

// poor quality only flags if it hurts agreement or is critical
const qualityIsCostingUsAgreement = realDifferences > 0 || agreedBlanks > MAX_AGREED_BLANKS;
const qualityIssues = (gemini.scan_quality && gemini.scan_quality.issues) || [];
const hasCriticalQualityIssue = Array.isArray(qualityIssues) && qualityIssues.some((iss) => CRITICAL_QUALITY_ISSUES.includes(iss));
if (geminiOk && gemini.scan_quality && gemini.scan_quality.poor_quality === true && (qualityIsCostingUsAgreement || hasCriticalQualityIssue)) {
  reasons.push('poor_scan_quality');
}

if (agreementKnown && flagAgreeRows < AGREEMENT_MIN_ROWS) reasons.push(`omr_agreement_below_${AGREEMENT_MIN_PCT}`);

if (weakRows >= WEAK_ROW_LIMIT) reasons.push(`low_confidence_ocr_${weakRows}_rows`);

// Teacher rule (2026-10-08): fewer than MIN_ANSWERED of the 20 items answered -> the form is discarded
// and never counted. An answer is a stored rating: Python's, or Gemini's on a row Python read blank,
// so a row either engine read as a valid mark counts (a disagreement never turns an answer into a
// blank). Rows the OMR rejected (invalid_mark) are not answers. The scan is discarded automatically
// only when both readers worked on the expected grid; otherwise staff check it (same reason).
const answeredCount = Object.values(scores).filter((s) => s !== null).length;
const tooFewAnswers = answeredCount < MIN_ANSWERED;
const scanReliable = pythonOk && geminiOk && !layoutMismatch && py.table_found !== false
  && py.grid_source !== 'fallback' && !reasons.includes('poor_scan_quality')
  && !reasons.some((r) => r.startsWith('not_a_sast_form'));
const autoDiscard = tooFewAnswers && scanReliable;
if (tooFewAnswers) reasons.push(`too_few_answers_${answeredCount}_of_20`);
// one message for an (almost) empty form: too_few_answers replaces too_many_blanks
else if (agreedBlanks > MAX_AGREED_BLANKS) reasons.push(`too_many_blanks_${agreedBlanks}_of_20`);

// never dropped (unlike grid_not_detected below)
if (layoutMismatch) reasons.push('layout_mismatch');

// phone only: drop grid_not_detected when Gemini found answers
if (geminiOk && Object.values(scores).some(s => s !== null)) {
  reasons = reasons.filter(r => r !== 'grid_not_detected');
}

// stamp rule (2026-10-16, two readers 2026-10-17): one stamp both readers agree on, else review (stamp.js); a stamp
// already stored for the term is caught after this node ('Phone Stamp Check' -> 'Phone Stamp Verdict')
const stamp = stCheckStamp(gemini, geminiOk, py.stamp_candidates);
if (stReason(stamp)) reasons.push(stReason(stamp));

// OCR cross-check (ocr_crosscheck.js): the AI's student ID / remark vs the OMR's ink check of the box,
// and Gemini's corrected_rows (white ink). Review only, never a discard; nothing without ocr_meta.ink.
const ocrCheck = ocCrossCheck(py, gemini, geminiOk, { studentIdMissed: true });
reasons.push(...ocrCheck.reasons);

// review note for failed_scan_queue and import_errors
const noteParts = [];
if (autoDiscard) {
  noteParts.push(`Only ${answeredCount} of the ${TOTAL_ROWS} items were answered (at least ${MIN_ANSWERED} are needed), so this form was discarded automatically and is not counted.`);
} else if (tooFewAnswers) {
  noteParts.push(`Only ${answeredCount} of the ${TOTAL_ROWS} items have an answer (at least ${MIN_ANSWERED} are needed to count a form), but the scan could not be fully checked. Compare with the sheet: fill in any answers that were missed, or discard the form.`);
}
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
if (invalidRows.length) {
  noteParts.push(`${invalidRows.map((r) => `${r.key}: scanner saw ${markText(r.mark)} (not counted)`).join('; ')}. `
    + 'Only a check or slash mark counts, so those answers stay blank.'
    + (invalidRows.some((r) => r.mark === 'double') ? ' A row with two marks has no single answer.' : ''));
}
if (layoutMismatch) noteParts.push(lcNote(layoutSignals, geminiOk ? lcColumnOrder(gemini) : null));
if (!pythonOk) noteParts.push('The OMR scanner returned no ratings for this scan, so the answers shown come from the AI only. Verify every rating.');
if (!resolvedInstructor) noteParts.push('No instructor name could be read from this form.');
if (instructorMatch.match === 'ambiguous') {
  noteParts.push(`The instructor name "${rawInstructor.slice(0, 120)}" fits more than one instructor `
    + `(${instructorMatch.contenders.slice(0, 3).join('; ')}). Pick the right one.`);
}
if (!resolvedSubject) noteParts.push('No subject or course code could be read from this form.');
if (stNote(stamp)) noteParts.push(stNote(stamp));
noteParts.push(...ocrCheck.notes);
if (!geminiOk) noteParts.push('The AI cross-check did not return a result for this scan, so the OMR reading has not been verified against a second source and no text was read. Please check it manually.');

const needsReview = reasons.length > 0;
const validationStatus = needsReview ? 'flagged' : 'auto';
// always show agreement on flagged scans
if (needsReview && agreementText && !reasons.some((r) => r.startsWith('omr_agreement_below_'))) {
  noteParts.push(agreementText);
}
const reviewNote = noteParts.length ? noteParts.join(' ') : null;

const clean = (v) => {
  const s = (v ?? '').toString().trim();
  return (s === 'true' || s === 'false' || s === 'null' || s === 'undefined') ? '' : s;
};

// metadata from the webhook, works even if Python crashed
let scanMeta = {};
try {
  scanMeta = $('SAST image from phone').first().json.body ?? {};
} catch (e) {}

if (!scanMeta.task_id) {
  try {
    scanMeta = $('Google LINK from phone1').first().json.body ?? {};
  } catch (e) {}
}

// take the image from the webhook body, binary data gets dropped
let scanImageBase64 = null;
try {
  const rawImage = $('SAST image from phone').first().json.body.image;
  if (rawImage) {
    // strip the data URL prefix
    scanImageBase64 = rawImage.includes(',') ? rawImage.split(',')[1] : rawImage;
  }
} catch (e) {}

return [{
  json: {
    instructor:  resolvedInstructor,
    instructor_match: { match: instructorMatch.match, score: instructorMatch.score, raw: rawInstructor },
    subject:     resolvedSubject,
    ay_semester: gText('ay_semester'),
    remarks:     remarkResult.text,
    remarks_masked: remarkResult.masked,
    remark_check:   remarkResult.check,
    date:        clean(gText('date')),
    student_id:  ocrCheck.drop_student_id ? '' : clean(gText('student_id')),   // box empty: the AI's ID is not kept
    ocr_check:   ocrCheck.check,
    text_source: geminiOk ? 'gemini' : 'none',
    stamp_no:         stamp.stamp_no,           // the one stamp read on the page (status 'ok'), else null
    stamp_status:     stamp.stamp_status,       // ok | unconfirmed | missing | unreadable | multiple
    stamp_candidates: stamp.stamp_candidates,   // every readable stamp number, in order
    stamp_raw:        stamp.stamp_raw,          // Gemini's stamp_numbers as received
    stamp_source:     stamp.stamp_source,       // 'gemini+python' when ok (both readers agree)
    stamp_hint:       stamp.stamp_hint,         // not ok: the number to check for a duplicate (hint only)
    stamp_check:      stamp.stamp_check,        // what both readers saw (review screens)

    ratings: scores,
    document_check: gemini.document_check ?? null,
    validation_status: validationStatus,
    needs_review:      needsReview,
    review_reasons:    reasons,
    answered_count:    answeredCount,
    auto_discard:      autoDiscard,   // read by 'build flagged record1': stored as discarded
    agreement_percent: agreementPercent,
    agreement_rows:    agreementKnown ? agreeRows : null,
    omr_agreement: {
      cross_checked:         agreementKnown,
      agreement_rows:        agreementKnown ? agreeRows : null,
      agreement_percent:     agreementPercent,
      agreement_min_percent: AGREEMENT_MIN_PCT,
      flag_agreement_rows:   agreementKnown ? flagAgreeRows : null,   // what the agreement rule checks
      resolved_differences:  resolvedRows.length,
      real_differences:      realDifferences,
      filled_from_gemini:    filledByGemini,
      invalid_mark_rows:     invalidRows.length,   // rows left blank by the check-only rule
      answered_count:        answeredCount,        // rows with a stored rating (min-answered rule)
      comparisons,   // Flutter review screen lists these
    },

    // read by 'build flagged record1'
    grid_source: py.grid_source ?? null,
    layout_check: {
      mismatch:      layoutMismatch,
      signals:       layoutSignals,
      column_order:  geminiOk ? lcColumnOrder(gemini) : null,   // Gemini's header reading, if any
      python_layout: py.layout ?? null,                         // the OMR's layout evidence, if any
    },
    review_note: reviewNote,
    comparisons,
    python_raw_ratings: py.ratings ?? null,
    summary: `${answeredCount}/${TOTAL_ROWS} answered`
             + (invalidRows.length ? `, ${invalidRows.length} non-check mark(s) not counted` : '')
             + (agreementPercent !== null ? `, agreement ${agreementPercent}%` : '')
             + (autoDiscard ? ` — discarded: ${reasons.join(', ')}`
               : needsReview ? ` — flagged: ${reasons.join(', ')}` : ' — clean'),
    task_id: scanMeta.task_id ?? null,
    user_id: scanMeta.user_id ?? null,
    term_id: scanMeta.term_id ?? null,
    scan_image: scanImageBase64
  }
}];
