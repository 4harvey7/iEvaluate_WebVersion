// Auto-credit unless OMR fails, a header field is blank/unreadable, the image was already scanned in
// another batch, or the form clearly names another instructor/subject.
const NAME_MATCH_MIN      = 0.70;   // another instructor needs this score to be a match
const MATCH_MARGIN        = 0.10;   // best other match must beat the second by this
const BATCH_MARGIN        = 0.20;   // and must beat the batch by this (the staff picked the batch)
const BATCH_SAME_MIN      = 0.40;   // batch instructor score that counts as the same person
const SUBJECT_SAME_MIN    = 0.35;   // batch subject score that counts as the same subject
const BATCH_BEST_MIN      = 0.25;   // batch is also the same when it is the best match with at least this
const KNOWN_SCORE         = 0.95;   // Gemini picked this subject from the known subject list
const MIN_NAME_LETTERS    = 3;      // fewer letters means unreadable
const SUBJECT_MATCH_MIN   = 0.60;   // subject name score; a code hit is 1.0
const SUBJECT_NAME_COVER  = 0.75;   // share of a subject title the form must show to name it
const SUBJECT_FUZZY_SCORE = 0.90;   // code one letter/digit off the batch code
const SUBJECT_LINK_BONUS  = 0.05;   // subject already linked to that instructor
const MIN_SUBJECT_CHARS   = 2;      // fewer letters/digits means unreadable
const MAX_CANDIDATES      = 5;      // candidates shown to the reviewer
const CANDIDATE_MIN_SCORE = 0.30;   // min score to be shown
const EPS = 1e-6;
const SPOT_CHECK_RATE = 0.05;   // ~1 in 20 otherwise-clean pages sent for a quick staff spot check
// Deterministic, not Math.random(): the same task_id always gets the same outcome (a retry of
// the same page never flip-flops), and it is reproducible in tests. FNV-1a hash -> [0, 1).
function spotCheckRoll(taskId) {
  let h = 2166136261;
  const s = String(taskId || '');
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return (h >>> 0) / 4294967296;
}
// @@include profanity.js
// @@include name_match.js
function errText(j) {
  if (!j) return '';
  const e = j.error;
  if (typeof e === 'string') return e;
  if (e && typeof e === 'object') return String(e.message || e.description || JSON.stringify(e)).slice(0, 300);
  return String(j.message || '').slice(0, 300);
}
// item may be on output 0 or the error output
function nodeItem(name) {
  for (const branch of [0, 1]) {
    try {
      const it = $(name).first(branch);
      if (it && it.json) return it.json;
    } catch (e) {}
  }
  return {};
}
const arr = (v) => {
  if (Array.isArray(v)) return v;
  if (typeof v === 'string') {
    try { const p = JSON.parse(v); return Array.isArray(p) ? p : []; } catch (e) { return []; }
  }
  return [];
};
const num = (v) => { const n = Number(v); return Number.isFinite(n) ? n : 0; };
const r2 = (v) => (typeof v === 'number' && Number.isFinite(v) ? Math.round(Math.min(1, Math.max(0, v)) * 100) / 100 : null);
const quote = (s) => `"${String(s).slice(0, 120)}"`;

const NAME_PLACEHOLDERS = new Set(['na', 'n a', 'none', 'nil', 'null', 'undefined', 'unknown', 'illegible',
  'unreadable', 'blank', 'tba', 'tbd', 'name', 'no name', 'anonymous', 'not applicable']);
const SUBJECT_PLACEHOLDERS = new Set([...NAME_PLACEHOLDERS, 'subject', 'no subject']);
const INSTRUCTOR_REASON = { found: 'instructor_mismatch_found', not_found: 'instructor_mismatch_not_found',
  ambiguous: 'instructor_mismatch_ambiguous' };
const SUBJECT_REASON = { found: 'subject_mismatch_found', not_found: 'subject_mismatch_not_found',
  ambiguous: 'subject_mismatch_ambiguous' };
const MISMATCH_RE = /^(instructor|subject)_mismatch_/;

const ctx = $('Website Prepare Scan').first(0).json;
const rec = $('Website Reconcile OCR OMR').first(0).json;
const idIn = nodeItem('Website Identity Check');
const idOk = typeof idIn.batch_instructor_exists === 'boolean';
const id = idOk ? idIn : {};
const fmIn = $input.first()?.json ?? {};
const fmOk = Number(fmIn.form_match_version) === 1;
const fm = fmOk ? fmIn : {};

const reasons = Array.isArray(rec.review_reasons) ? rec.review_reasons.slice() : [];
const notes = [];   // shown to the reviewer

const ocrI = String(rec.ocr_instructor || '');
const ocrS = String(rec.ocr_subject || '');
// Gemini's closest entry from the known subject list (may be blank). There is no instructor
// pick: the website sends no instructor list to Gemini, the reading is matched against the DB.
const knownS = String(rec.known_subject || '');
// optional: how sure Gemini was of its own instructor/subject reading (website prompt only).
// A low-confidence reading can never produce a *_mismatch_* flag; it becomes a note instead.
const instrConfidence = String(rec.instructor_confidence || '');
const subjConfidence = String(rec.subject_confidence || '');
const isLowConfidence = (c) => c === 'low';
const batchName = String(id.batch_instructor_name || fm.batch_instructor_name || '').trim();
const batchCode = String(id.batch_subject_code || fm.batch_subject_code || '').trim();
const batchSubjectName = String(id.batch_subject_name || fm.batch_subject_name || '').trim();
const termId = (idOk ? id.resolved_term_id : ctx.term_id) || null;
const batchInstructorId = String(ctx.instructor_id || '').toLowerCase();

// Batch bias: only a clear winner over the batch counts. strong = others that could be a match.
function pickAgainstBatch(cands, strong, sameMin, matchMin) {
  const batch = cands.find((c) => c.is_batch) || null;
  const batchScore = batch ? batch.score : 0;
  const beating = strong.filter((c) => c.score - batchScore >= BATCH_MARGIN - EPS);
  if (beating.length === 1 || (beating.length > 1 && beating[0].score - beating[1].score >= MATCH_MARGIN - EPS)) {
    return { match: 'found', pick: beating[0], confidence: beating[0].score, contenders: [] };
  }
  if (beating.length > 1) {
    const contenders = beating.filter((c) => beating[0].score - c.score < MATCH_MARGIN - EPS);
    return { match: 'ambiguous', pick: null, confidence: batch ? batchScore : null, contenders };
  }
  // a close other match with a weak batch: batch stays, not flagged
  if (batch && strong.length && batchScore < matchMin - EPS) {
    return { match: 'ambiguous', pick: batch, confidence: batchScore, contenders: [batch, ...strong] };
  }
  const best = cands[0] || null;
  if (batch && (batchScore >= sameMin - EPS || (best === batch && batchScore >= BATCH_BEST_MIN - EPS))) {
    return { match: 'same', pick: batch, confidence: batchScore, contenders: [] };
  }
  return { match: 'not_found', pick: null, confidence: batch ? batchScore : null, contenders: [] };
}

function matchInstructor() {
  if (!fmOk) return { match: 'unchecked', pick: null, confidence: null, candidates: [], contenders: [] };
  const norm = String(fm.ocr_instructor_norm || '').trim();
  if (!norm || num(fm.ocr_instructor_letters) < MIN_NAME_LETTERS || NAME_PLACEHOLDERS.has(norm)) {
    return { match: 'unreadable', pick: null, confidence: null, candidates: [], contenders: [] };
  }
  const formToks = nmNameTokens(norm);
  const cands = arr(fm.instructor_candidates)
    .map((c) => {
      const name = String(c.name || '').trim();
      const toks = nmNameTokens(name);
      const score = Math.max(num(c.score), nmScore(toks, formToks).score);
      return { id: String(c.id || '').toLowerCase(), name, score,
        is_placeholder: c.is_placeholder === true, is_batch: c.is_batch === true };
    })
    .sort((a, b) => (b.score - a.score) || (Number(b.is_batch) - Number(a.is_batch)) || a.name.localeCompare(b.name));
  const shown = cands.filter((c) => c.score >= CANDIDATE_MIN_SCORE - EPS).slice(0, MAX_CANDIDATES)
    .map((c) => ({ ...c, score: r2(c.score) }));
  const strong = cands.filter((c) => !c.is_batch && c.score >= NAME_MATCH_MIN - EPS);
  return { ...pickAgainstBatch(cands, strong, BATCH_SAME_MIN, NAME_MATCH_MIN), candidates: shown };
}

// link bonus uses the instructor the page would go to
function matchSubject(targetInstructorId) {
  if (!fmOk) return { match: 'unchecked', pick: null, confidence: null, candidates: [], contenders: [] };
  const norm = String(fm.ocr_subject_norm || '').trim();
  if (!norm || num(fm.ocr_subject_alnum_len) < MIN_SUBJECT_CHARS || SUBJECT_PLACEHOLDERS.has(norm)) {
    return { match: 'unreadable', pick: null, confidence: null, candidates: [], contenders: [] };
  }
  const tid = String(targetInstructorId || '').toLowerCase();
  const knownKey = nmAlnum(knownS);
  const all = arr(fm.subject_candidates).map((c) => {
    const code = String(c.code || '').trim();
    const name = String(c.name || '').trim();
    const fit = nmSubjectFit(code, name, norm);
    const isBatch = c.is_batch === true;
    const linked = tid !== '' && arr(c.linked_instructor_ids).map((x) => String(x).toLowerCase()).includes(tid);
    const exact = c.code_hit === true || fit.exact;
    const nameScore = Math.max(num(c.name_score), fit.name_score);
    // a code one off only ever supports the batch (e.g. PO121 / PC127 for PC121)
    const fuzzy = fit.fuzzy && isBatch;
    let score = exact ? 1 : Math.min(1, Math.max(fuzzy ? SUBJECT_FUZZY_SCORE : 0, nameScore) + (linked ? SUBJECT_LINK_BONUS : 0));
    const known = knownKey !== '' && nmAlnum(code) === knownKey && (isBatch || score >= NM_KNOWN_RAW_MIN - EPS);
    if (known) score = Math.max(score, KNOWN_SCORE);
    return { id: String(c.id || '').toLowerCase(), code, name, is_batch: isBatch, code_hit: exact || known,
      name_cover: fit.name_cover, score };
  });
  // a course code on the form (letters + 2 or more digits) that is no subject
  const unknownCode = nmCodeKeys(norm).some((k) => /[A-Z]/.test(k) && /[0-9]{2}/.test(k)) && !all.some((c) => c.code_hit);
  const cands = all.sort((a, b) => (Number(b.code_hit) - Number(a.code_hit)) || (b.score - a.score)
    || (Number(b.is_batch) - Number(a.is_batch)) || a.code.localeCompare(b.code));
  const shown = cands.filter((c) => c.code_hit || c.score >= CANDIDATE_MIN_SCORE - EPS).slice(0, MAX_CANDIDATES)
    .map((c) => ({ id: c.id, code: c.code, name: c.name, score: r2(c.score), is_batch: c.is_batch }));
  const batch = cands.find((c) => c.is_batch) || null;
  const batchScore = batch ? batch.score : null;
  if (batch && batch.code_hit) return { match: 'same', pick: batch, confidence: batch.score, candidates: shown, contenders: [] };
  // another subject's code on the form wins
  const otherHits = cands.filter((c) => !c.is_batch && c.code_hit);
  if (otherHits.length === 1) return { match: 'found', pick: otherHits[0], confidence: 1, candidates: shown, contenders: [] };
  if (otherHits.length > 1) return { match: 'ambiguous', pick: null, confidence: batchScore, candidates: shown, contenders: otherHits };
  // by title: most of the title must be on the form, and no unknown code next to it
  const strong = unknownCode ? [] : cands.filter((c) => !c.is_batch && c.score >= SUBJECT_MATCH_MIN - EPS
    && c.name_cover >= SUBJECT_NAME_COVER - EPS);
  return { ...pickAgainstBatch(cands, strong, SUBJECT_SAME_MIN, SUBJECT_MATCH_MIN), candidates: shown };
}

// only a clear other match flags; not_found and an ambiguous set with the batch in it keep the batch
const flags = (m) => m.match === 'found' || (m.match === 'ambiguous' && !m.contenders.some((c) => c.is_batch));

const instr = matchInstructor();
// a low-confidence reading never wins a suggestion either -- it degrades to the batch, same
// as if nothing else had matched
const foundInstructor = (instr.match === 'found' && !isLowConfidence(instrConfidence)) ? instr.pick : null;
const subj = matchSubject(foundInstructor ? foundInstructor.id : batchInstructorId);
const foundSubject = (subj.match === 'found' && !isLowConfidence(subjConfidence)) ? subj.pick : null;
const targetDiffers = !!(foundInstructor || foundSubject);

if (!idOk) {
  reasons.push('batch_check_failed');
  notes.push(`The batch check could not run (${errText(idIn) || 'database error'}), so it is not confirmed that the batch instructor/subject/term are still valid or that this student's form is not already stored.`);
} else {
  const invalid = [];
  if (!id.batch_instructor_exists) invalid.push('the batch instructor no longer exists in user_info');
  // placeholder instructors are disabled by design but still valid
  else if (id.batch_instructor_disabled === true && id.batch_instructor_is_placeholder !== true) invalid.push(`the batch instructor account (${batchName || 'no name'}) is disabled`);
  if (!id.batch_subject_exists) invalid.push('the batch subject no longer exists in subjects');
  if (!termId) invalid.push('the batch has no term and no current term is configured');
  if (invalid.length) {
    reasons.push('batch_reference_invalid');
    notes.push(`Batch reference problem: ${invalid.join('; ')}.`);
  }
}

const batchInstrLabel = batchName || 'the batch instructor';
const batchSubjLabel = batchCode || batchSubjectName || 'the batch subject';
const instrWouldFlag = flags(instr);
const subjWouldFlag = flags(subj);
const instrFlags = instrWouldFlag && !isLowConfidence(instrConfidence);
const subjFlags = subjWouldFlag && !isLowConfidence(subjConfidence);
if (instrFlags) {
  reasons.push(INSTRUCTOR_REASON[instr.match]);
  if (instr.match === 'found') {
    notes.push(`The form names the instructor ${quote(ocrI)}, which matches ${instr.pick.name} in the database, not the batch instructor ${batchInstrLabel}. Suggested: credit ${instr.pick.name}.`);
  } else {
    notes.push(`The form names the instructor ${quote(ocrI)}, which matches several instructors about equally well (${instr.contenders.map((c) => c.name).join(', ')}). Pick the right one; the batch instructor ${batchInstrLabel} is pre-selected.`);
  }
} else if (instrWouldFlag) {
  // AI was unsure of its own reading: never flag on it, keep the batch, but say so
  notes.push(`The instructor on the form reads ${quote(ocrI)}, but the AI was not confident in that reading, so it was not used to flag a mismatch; the batch instructor ${batchInstrLabel} was kept.`);
} else if (instr.match === 'not_found' || instr.match === 'ambiguous') {
  // not flagged: the batch instructor stays
  notes.push(`The instructor on the form reads ${quote(ocrI)}, which does not clearly match anyone else, so the batch instructor ${batchInstrLabel} was kept.`);
}
if (subjFlags) {
  reasons.push(SUBJECT_REASON[subj.match]);
  if (subj.match === 'found') {
    notes.push(`The form names the subject ${quote(ocrS)}, which matches ${subj.pick.code || subj.pick.name}${subj.pick.name && subj.pick.code ? ` (${subj.pick.name})` : ''} in the database, not the batch subject ${batchSubjLabel}. Suggested: credit ${subj.pick.code || subj.pick.name}.`);
  } else {
    notes.push(`The form names the subject ${quote(ocrS)}, which matches several subjects about equally well (${subj.contenders.map((c) => c.code || c.name).join(', ')}). Pick the right one; the batch subject ${batchSubjLabel} is pre-selected.`);
  }
} else if (subjWouldFlag) {
  notes.push(`The subject on the form reads ${quote(ocrS)}, but the AI was not confident in that reading, so it was not used to flag a mismatch; the batch subject ${batchSubjLabel} was kept.`);
} else if (subj.match === 'not_found' || subj.match === 'ambiguous') {
  notes.push(`The subject on the form reads ${quote(ocrS)}, which does not clearly match another subject, so the batch subject ${batchSubjLabel} was kept.`);
}
if (!fmOk) {
  reasons.push('form_match_failed');
  notes.push(`The instructor/subject written on the form could not be checked against the database (${errText(fmIn) || 'database error'}), so the page is held for review with the batch instructor/subject.`);
}
if (reasons.some((r) => MISMATCH_RE.test(r))) {
  notes.push('Nothing was credited: approve the suggestion or choose the right instructor/subject.');
}

// a blank/unreadable header field is held for review, never filled in silently from the batch
if (instr.match === 'unreadable') {
  reasons.push('instructor_missing');
  notes.push(`The instructor on the form is blank or could not be read${ocrI.trim() ? ` (${quote(ocrI)})` : ''}. Check the sheet; the batch instructor ${batchInstrLabel} is pre-selected.`);
}
if (subj.match === 'unreadable') {
  reasons.push('subject_missing');
  notes.push(`The subject on the form is blank or could not be read${ocrS.trim() ? ` (${quote(ocrS)})` : ''}. Check the sheet; the batch subject ${batchSubjLabel} is pre-selected.`);
}
// blank -> null so it never collides; a placeholder (N/A, none, ...) or no letter/digit counts as blank
const studentIdIn = String(rec.student_id || '').trim();
const studentIdNorm = studentIdIn.toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
const studentId = (studentIdNorm && !NAME_PLACEHOLDERS.has(studentIdNorm)) ? studentIdIn : null;
if (!studentId) {
  reasons.push('student_id_missing');
  notes.push(`The student ID on the form is blank or could not be read${studentIdIn ? ` (${quote(studentIdIn)})` : ''}. Type it from the sheet if it is there; the page can also be approved without one.`);
}

// the same image file was already marked in another batch (backend lookup by sha256)
const reused = ctx.reused_image;
if (reused) {
  reasons.push('reused_image');
  notes.push(`This exact image file was already scanned in another batch (${reused.batch_id}, result: ${reused.status}). Check that it is not the same student's form counted twice before approving.`);
}

// duplicate check ran against the batch instructor/subject/term (a placeholder student ID is no duplicate)
if (idOk && id.duplicate_student_exists === true && studentId) {
  const who = String(rec.student_id || '').trim();
  if (targetDiffers) {
    // suggested elsewhere, so the batch duplicate doesn't apply
    notes.push(`A form of student ${who} is already stored for the batch instructor, subject and term; that does not apply if the page is credited to the suggestion, and duplicates are checked again when it is approved.`);
  } else {
    reasons.push('duplicate_student_id');
    notes.push(`A form of student ${who} for this instructor, subject and term is already stored`
      + (id.duplicate_student_task_id ? ` (task ${id.duplicate_student_task_id})` : ' (from a phone scan or sheet import)')
      + '. This page was not saved over it; check whether the student submitted twice or the student ID was misread.');
  }
}

// spot check: only ever applied to an otherwise-clean page, pre-filled with the batch values
// (nothing else changes the suggestion), so staff can approve it with one click
if (reasons.length === 0 && spotCheckRoll(ctx.task_id) < SPOT_CHECK_RATE) {
  reasons.push('spot_check');
  notes.push('Randomly selected for a quick spot check; nothing looked wrong with this page. Confirm the instructor/subject/ratings shown (the batch values) and approve.');
}

const flagged = reasons.length > 0;
// mismatch only when flagged; a kept batch is a match, no clear reading is unreadable
const verdict = (m) => (flags(m) ? 'mismatch' : (m.match === 'same' || m.match === 'ambiguous') ? 'match' : 'unreadable');
// only set when the form disagrees with the batch
const identityCheck = reasons.some((r) => MISMATCH_RE.test(r))
  ? { instructor: verdict(instr), subject: verdict(subj), ocr_instructor: ocrI, ocr_subject: ocrS }
  : null;

// 1..5, blank row -> null
const ratings = {};
for (const prefix of ['m', 'p']) {
  for (let i = 1; i <= 10; i++) {
    const v = Number(rec.ratings?.[`${prefix}${i}`]);
    ratings[`${prefix}${i}`] = Number.isFinite(v) && v >= 1 && v <= 5 ? v : null;
  }
}
const answered = Object.values(ratings).filter((v) => v !== null).length;

const remarkIn = String(rec.remarks || '').trim();
const remark = maskProfanity(remarkIn);
const remarksMasked = rec.remarks_masked === true || remark !== remarkIn;
const agreementPercent = typeof rec.agreement_percent === 'number' ? rec.agreement_percent : null;

// today's date in Manila time
const dp = Object.fromEntries(
  new Intl.DateTimeFormat('en-US', { timeZone: 'Asia/Manila', year: 'numeric', month: '2-digit', day: '2-digit' })
    .formatToParts(new Date())
    .map((p) => [p.type, p.value])
);
const today = `${dp.year}-${dp.month}-${dp.day}`;

const reviewNote = [rec.review_note, ...notes].filter(Boolean).join(' ') || null;

return [{
  json: {
    decision: flagged ? 'flagged' : 'clean',
    flagged,
    task_id: ctx.task_id,
    batch_id: ctx.batch_id,
    staff_id: ctx.staff_id,
    station_id: ctx.station_id,
    filename: ctx.filename,
    // clean pages always go to the batch instructor/subject
    instructor_id: ctx.instructor_id,
    subject_id: ctx.subject_id,
    term_id: termId,
    instructor_name: batchName || ocrI,
    subject_code: batchCode,
    subject_name: batchSubjectName,
    student_id: studentId,
    remark,
    remarks_masked: remarksMasked,
    submitted_date: today,
    ratings,
    review_reasons: reasons,
    review_note: reviewNote,
    agreement_percent: agreementPercent,
    identity_check: identityCheck,
    table_found: rec.table_found ?? null,
    grid_source: rec.grid_source ?? 'reconciler',
    omr_comparison: rec.omr_agreement ?? null,
    // instructor/subject hold the suggestion for the correction screen
    partial_data: {
      instructor: foundInstructor ? foundInstructor.name : (batchName || ocrI),
      subject: foundSubject ? (foundSubject.code || foundSubject.name) : (batchCode || batchSubjectName || ocrS),
      ay_semester: rec.ay_semester || '',
      remarks: remark,
      ...(remarksMasked ? { remarks_masked: true } : {}),
      date: rec.date || '',
      student_id: studentId || '',
      ratings: rec.ratings ?? null,
      summary: `${answered}/20 answered`
        + (agreementPercent !== null ? `, agreement ${agreementPercent}%` : '')
        + (flagged ? ` — flagged: ${reasons.join(', ')}` : ' — clean'),
      python_raw_ratings: rec.python_raw_ratings ?? null,
      comparisons: rec.comparisons ?? null,
      agreement_percent: agreementPercent,
      review_note: reviewNote,
      ocr_instructor: ocrI,
      ocr_subject: ocrS,
      known_subject: knownS,
      instructor_confidence: instrConfidence || null,
      subject_confidence: subjConfidence || null,
      student_id_confidence: String(rec.student_id_confidence || '') || null,
      source: 'website_batch',
      batch_id: ctx.batch_id,
      batch_instructor_id: ctx.instructor_id,
      batch_subject_id: ctx.subject_id,
      station_id: ctx.station_id,
      filename: ctx.filename,
      form_check_version: 1,
      instructor_match: instr.match,
      subject_match: subj.match,
      suggested_instructor_id: foundInstructor ? foundInstructor.id : ctx.instructor_id,
      suggested_instructor_name: foundInstructor ? foundInstructor.name : (batchName || null),
      suggested_subject_id: foundSubject ? foundSubject.id : ctx.subject_id,
      suggested_subject_code: foundSubject ? (foundSubject.code || null) : (batchCode || null),
      suggested_subject_name: foundSubject ? (foundSubject.name || null) : (batchSubjectName || null),
      instructor_match_confidence: r2(instr.confidence),
      subject_match_confidence: r2(subj.confidence),
      instructor_candidates: instr.candidates,
      subject_candidates: subj.candidates,
      batch_instructor_name: batchName || null,
      batch_subject_code: batchCode || null,
      batch_subject_name: batchSubjectName || null,
    },
  },
}];
