// Queue record for any flagged or failed website page.
function errText(j) {
  if (!j) return '';
  const e = j.error;
  if (typeof e === 'string') return e;
  if (e && typeof e === 'object') return String(e.message || e.description || JSON.stringify(e)).slice(0, 300);
  return String(j.message || '').slice(0, 300);
}
function tryFirst(name, check) {
  try {
    const it = $(name).first(0);
    const j = it && it.json ? it.json : null;
    return j && check(j) ? j : null;
  } catch (e) {
    return null;
  }
}

const ctx = $('Website Prepare Scan').first(0).json;
const inp = $input.first()?.json ?? {};

// form check didn't run, so suggest the batch
function uncheckedFormKeys(batchName, batchCode, batchSubjectName) {
  return {
    form_check_version: 1,
    instructor_match: 'unchecked',
    subject_match: 'unchecked',
    suggested_instructor_id: ctx.instructor_id,
    suggested_instructor_name: batchName || null,
    suggested_subject_id: ctx.subject_id,
    suggested_subject_code: batchCode || null,
    suggested_subject_name: batchSubjectName || null,
    instructor_match_confidence: null,
    subject_match_confidence: null,
    instructor_candidates: [],
    subject_candidates: [],
    batch_instructor_name: batchName || null,
    batch_subject_code: batchCode || null,
    batch_subject_name: batchSubjectName || null,
  };
}

function asObject(v) {
  if (v && typeof v === 'object' && !Array.isArray(v)) return v;
  if (typeof v === 'string') {
    try { const o = JSON.parse(v); return o && typeof o === 'object' && !Array.isArray(o) ? o : null; } catch (e) { return null; }
  }
  return null;
}

let d;
// min-answered rule: the reconciler decides; only a page Decide flagged normally is stored as
// discarded (an internal error always goes to review)
let autoDiscard = false;
let answeredCount = null;
// stamp rule (2026-10-16): a stamp already stored for the term auto-rejects the page; it beats the
// min-answered auto-discard
let autoReject = null;
let duplicateOf = null;
if (inp.decision === 'flagged') {
  d = inp;
  const reconciled = tryFirst('Website Reconcile OCR OMR', (j) => j._stage === 'reconciled');
  autoDiscard = !!(reconciled && reconciled.auto_discard === true);
  answeredCount = reconciled && Number.isInteger(reconciled.answered_count) ? reconciled.answered_count : null;
  if (d.auto_reject === 'duplicate_stamp') {
    autoReject = 'duplicate_stamp';
    duplicateOf = asObject(d.duplicate_of) || {};
  }
} else {
  const msg = errText(inp) || 'unknown error';
  const decided = inp.saved === false ? tryFirst('Website Decide', (j) => typeof j.decision === 'string') : null;
  const rec = tryFirst('Website Reconcile OCR OMR', (j) => j._stage === 'reconciled');
  const ident = tryFirst('Website Identity Check', (j) => typeof j.batch_instructor_exists === 'boolean')
    || tryFirst('Website Form Match', (j) => Number(j.form_match_version) === 1);   // fallback for batch names

  if (decided && inp.saved === false && inp.reason === 'duplicate_stamp') {
    // race: another page with this stamp was stored for the term after the Identity Check
    // (save_website_raw_row hit the unique stamp index) -> auto-rejected like a found duplicate
    autoReject = 'duplicate_stamp';
    duplicateOf = asObject(inp.duplicate_of) || {};
    const rest = (decided.review_reasons || []).filter((r) => r !== 'duplicate_stamp' && r !== 'reused_image' && r !== 'spot_check');
    const reasons = ['duplicate_stamp', ...rest];
    const note = [`This form was already scanned: stamp ${decided.stamp_no} is already stored for this term`
      + (duplicateOf.submitted_date ? ` (saved ${duplicateOf.submitted_date})` : '')
      + '. This copy was discarded automatically and is not counted.', decided.review_note].filter(Boolean).join(' ');
    d = {
      ...decided,
      decision: 'flagged',
      flagged: true,
      review_reasons: reasons,
      review_note: note,
      partial_data: {
        ...(decided.partial_data || {}),
        review_note: note,
        duplicate_of: duplicateOf,
        summary: `${String(decided.partial_data?.summary || 'scored').replace(/ — clean$/, '')} — discarded: duplicate_stamp`,
      },
    };
  } else if (decided && inp.saved === false) {
    // another page already stored this student's row
    const reasons = [...(decided.review_reasons || []), 'duplicate_student_id'];
    const note = [decided.review_note, `Another scanned form of student ${decided.student_id} for this instructor, subject and term is already stored, so this page was not saved over it. Check whether the student submitted twice.`]
      .filter(Boolean).join(' ');
    d = {
      ...decided,
      decision: 'flagged',
      flagged: true,
      review_reasons: reasons,
      review_note: note,
      partial_data: {
        ...(decided.partial_data || {}),
        review_note: note,
        summary: `${String(decided.partial_data?.summary || 'scored').replace(/ — clean$/, '')} — flagged: duplicate_student_id`,
      },
    };
  } else {
    const stage = rec ? 'internal_error_decide' : 'internal_error_reconcile';
    const r = rec || {};
    const ocrI = String(r.ocr_instructor || '');
    const ocrS = String(r.ocr_subject || '');
    const batchName = ident ? String(ident.batch_instructor_name || '').trim() : '';
    const batchCode = ident ? String(ident.batch_subject_code || '').trim() : '';
    const batchSubjectName = ident ? String(ident.batch_subject_name || '').trim() : '';
    const reasons = [...(Array.isArray(r.review_reasons) ? r.review_reasons : []), stage];
    const note = [r.review_note, `Automatic processing failed (${msg}), so the page was queued for review. Check every field.`]
      .filter(Boolean).join(' ');
    d = {
      decision: 'flagged',
      flagged: true,
      task_id: ctx.task_id,
      batch_id: ctx.batch_id,
      staff_id: ctx.staff_id,
      term_id: (ident && (ident.resolved_term_id || ident.term_id)) || ctx.term_id || null,
      // the stamp read on the page (a review may confirm it), never a duplicate check here
      stamp_no: r.stamp_status === 'ok' && /^[0-9]{3,12}$/.test(String(r.stamp_no ?? '')) ? String(r.stamp_no) : null,
      stamp_source: r.stamp_status === 'ok' ? (r.stamp_source || 'gemini') : null,
      review_reasons: reasons,
      review_note: note,
      identity_check: null,
      table_found: r.table_found ?? null,
      grid_source: r.grid_source ?? 'reconciler',
      omr_comparison: r.omr_agreement ?? null,
      partial_data: {
        instructor: batchName || ocrI,
        subject: batchCode || ocrS,
        ay_semester: r.ay_semester || '',
        remarks: String(r.remarks || '').trim(),   // already masked
        ...(r.remarks_masked === true ? { remarks_masked: true } : {}),
        date: r.date || '',
        student_id: r.student_id || '',
        ratings: r.ratings ?? null,
        summary: `${r.summary || 'not scored'} — flagged: ${stage}`,
        python_raw_ratings: r.python_raw_ratings ?? null,
        comparisons: r.comparisons ?? null,
        agreement_percent: typeof r.agreement_percent === 'number' ? r.agreement_percent : null,
        review_note: note,
        stamp_status: r.stamp_status || 'unreadable',
        stamp_candidates: Array.isArray(r.stamp_candidates) ? r.stamp_candidates : [],
        stamp_raw: r.stamp_raw ?? null,
        stamp_check: r.stamp_check ?? null,
        stamp_hint: r.stamp_hint ?? null,
        ocr_instructor: ocrI,
        ocr_subject: ocrS,
        source: 'website_batch',
        batch_id: ctx.batch_id,
        batch_instructor_id: ctx.instructor_id,
        batch_subject_id: ctx.subject_id,
        station_id: ctx.station_id,
        filename: ctx.filename,
        ...uncheckedFormKeys(batchName, batchCode, batchSubjectName),
      },
    };
  }
}

return [{
  json: {
    _source: 'website',
    task_id: d.task_id || ctx.task_id,
    user_id: d.staff_id || null,            // SAO staff member
    term_id: d.term_id || null,
    batch_id: d.batch_id || ctx.batch_id,
    table_found: d.table_found ?? null,
    grid_source: d.grid_source ?? 'reconciler',
    validation_status: 'flagged',
    auto_discard: autoDiscard && !autoReject,  // 'build flagged record1' stores it as discarded
    answered_count: answeredCount,
    auto_reject: autoReject,                   // 'duplicate_stamp': stored discarded/rejected (beats auto_discard)
    duplicate_of: duplicateOf,                 // DUP: the stored form with the same stamp and term
    stamp_no: d.stamp_no || null,              // -> failed_scan_queue.stamp_no (accepted stamps only)
    stamp_source: d.stamp_no ? (d.stamp_source || 'gemini') : null,   // 'gemini+python' (both readers)
    stamp_check: d.partial_data?.stamp_check ?? null,                  // what both readers saw
    stamp_status: d.partial_data?.stamp_status ?? null,
    stamp_candidates: Array.isArray(d.partial_data?.stamp_candidates) ? d.partial_data.stamp_candidates : [],
    stamp_raw: d.partial_data?.stamp_raw ?? null,
    review_reasons: Array.isArray(d.review_reasons) ? d.review_reasons : [],
    review_note: d.review_note || null,
    omr_agreement: d.omr_comparison ?? null,   // -> failed_scan_queue.omr_comparison
    identity_check: d.identity_check ?? null,
    partial_data: d.partial_data,
    scan_image: ctx.image_base64,              // -> scan_error_images.image_base64
  },
}];
