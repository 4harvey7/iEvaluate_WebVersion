// Phone scans, stamp rule (2026-10-16): the reconciler's record plus the result of 'Phone Stamp Check'.
// An ACCEPTED stamp (stamp_status 'ok': Gemini and the OMR's stamp reader agree) already stored for
// the term auto-rejects the scan (duplicate_stamp: 'build flagged record1' stores the queue row
// discarded/rejected, no staff push); a check that could not run sends the scan to review
// (stamp_check_failed) instead of crediting it unchecked. A stamp that was NOT accepted is checked
// as a hint (stamp_hint): a match only adds a note for staff (the scan is in review already).
// Same fields as the reconciler, so If31 and every node that reads the record keep working;
// 'build flagged record1' reads it here.
const rec = $('reconcile ocr and omr1').first().json;
const chk = $input.first()?.json ?? {};
const out = { ...rec, _stage: 'phone_stamp_checked' };

function asObject(v) {
  if (v && typeof v === 'object' && !Array.isArray(v)) return v;
  if (typeof v === 'string') {
    try { const o = JSON.parse(v); return o && typeof o === 'object' && !Array.isArray(o) ? o : null; } catch (e) { return null; }
  }
  return null;
}
function errText(j) {
  if (!j) return '';
  const e = j.error;
  if (typeof e === 'string') return e;
  if (e && typeof e === 'object') return String(e.message || e.description || JSON.stringify(e)).slice(0, 300);
  return '';
}

const stampOk = rec.stamp_status === 'ok' && /^[0-9]{3,12}$/.test(String(rec.stamp_no ?? ''));
const reasons = Array.isArray(rec.review_reasons) ? rec.review_reasons.slice() : [];
const note = (text) => [text, rec.review_note].filter(Boolean).join(' ');

if (stampOk && typeof chk.duplicate_stamp_exists !== 'boolean') {
  out.review_reasons = [...reasons, 'stamp_check_failed'];
  out.needs_review = true;
  out.validation_status = 'flagged';
  out.review_note = [rec.review_note, `The stamp ${rec.stamp_no} could not be checked against the stored forms`
    + ` (${errText(chk) || 'database error'}), so the scan is held for review. Check that this form was not counted already.`]
    .filter(Boolean).join(' ');
} else if (stampOk && chk.duplicate_stamp_exists === true) {
  const dup = asObject(chk.duplicate_stamp_of) || {};
  out.review_reasons = ['duplicate_stamp', ...reasons.filter((r) => r !== 'duplicate_stamp' && r !== 'reused_image' && r !== 'spot_check')];
  out.needs_review = true;
  out.validation_status = 'flagged';
  out.auto_reject = 'duplicate_stamp';     // beats the min-answered auto-discard
  out.auto_discard = false;
  out.duplicate_of = dup;
  out.review_note = note(`This form was already scanned: stamp ${rec.stamp_no} is already stored for this term`
    + (dup.submitted_date ? ` (saved ${dup.submitted_date})` : '')
    + '. This copy was discarded automatically and is not counted.');
  out.summary = `${String(rec.summary || '').replace(/ — (clean|flagged: .*|discarded: .*)$/, '')} — discarded: duplicate_stamp`;
} else if (!stampOk && /^[0-9]{3,12}$/.test(String(rec.stamp_hint ?? '')) && chk.duplicate_stamp_exists === true) {
  const dup = asObject(chk.duplicate_stamp_of) || {};
  out.stamp_duplicate_hint = dup;
  out.review_note = [rec.review_note, `Possible duplicate: a form with stamp ${rec.stamp_hint} is already stored for this term`
    + (dup.submitted_date ? ` (saved ${dup.submitted_date})` : '')
    + '. The stamp on this scan was not confirmed, so check the sheet before crediting it.'].filter(Boolean).join(' ');
}

return [{ json: out }];
