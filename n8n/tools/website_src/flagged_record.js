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

let d;
if (inp.decision === 'flagged') {
  d = inp;
} else {
  const msg = errText(inp) || 'unknown error';
  const decided = inp.saved === false ? tryFirst('Website Decide', (j) => typeof j.decision === 'string') : null;
  const rec = tryFirst('Website Reconcile OCR OMR', (j) => j._stage === 'reconciled');
  const ident = tryFirst('Website Identity Check', (j) => typeof j.batch_instructor_exists === 'boolean')
    || tryFirst('Website Form Match', (j) => Number(j.form_match_version) === 1);   // fallback for batch names

  if (decided && inp.saved === false) {
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
    review_reasons: Array.isArray(d.review_reasons) ? d.review_reasons : [],
    review_note: d.review_note || null,
    omr_agreement: d.omr_comparison ?? null,   // -> failed_scan_queue.omr_comparison
    identity_check: d.identity_check ?? null,
    partial_data: d.partial_data,
    scan_image: ctx.image_base64,              // -> scan_error_images.image_base64
  },
}];
