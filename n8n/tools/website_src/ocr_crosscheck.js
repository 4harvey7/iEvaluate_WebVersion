// OCR cross-check (2026-10-28, panel recommendations #10 and #11). Shared by both reconcilers.
//
// #10 The AI's student ID / remark is checked against the OMR's own ink check of that box
// (ocr_fields.ocr_meta.ink, OMR api.py 306667b9+). Only a CONFIDENT ink reading acts:
//   - the box was anchored to the printed layout (ocr_meta.located[field] === true), the table was found,
//   - ink <= OC_EMPTY_MAX  -> 'empty' (box blank), ink >= OC_INK_MIN -> 'ink' (box written),
//   - anything in between, no ocr_meta, an older OMR without ocr_meta.ink, a failed Gemini call
//     or a page that is not a SAST form -> null: do nothing.
// Thresholds from 134 labelled scanner pages (2026-10-28): remarks 'empty' 58/58 and 'ink' 40/40 right,
// student ID 'empty' 19/19 and 'ink' 43/44 right (one mislocated page); the rest stays undecided.
// Only the two MISMATCHES flag; a blank box with a blank AI read (the normal case) never does:
//   student ID: box empty + AI read an ID  -> the ID is dropped (stored blank), student_id_unverified
//               box written + AI read none -> student_id_missed (phone only; the website's Decide
//               already flags every blank ID as student_id_missing)
//   remarks:    box written + AI read none (raw text)              -> remarks_missed
//               box empty + AI read a real comment (not a placeholder such as 'N/A') -> remarks_unverified
// #11 White ink: Gemini lists in corrected_rows the rows where correction fluid/tape or a heavy
// cross-out covers a mark -> white_ink_correction and a note naming the rows. Ratings are not changed.
// All of these only send the scan to review; none of them discards it.
const OC_EMPTY_MAX = 0.001;
const OC_INK_MIN = 0.008;
const OC_ROW_RE = /^[mp](10|[1-9])$/;
const OC_PLACEHOLDERS = new Set(['n/a', 'na', 'n.a.', 'n.a', 'none', 'none.', 'nothing', 'no comment', 'no comments',
  'no remarks', 'wala', 'wala man', '-', '--', '.', '..', 'x', 'null', 'blank', 'empty']);
function ocInkState(py, field) {
  // the ONLY read of the OMR's ocr_fields: its ink meta, never its OCR text (text comes from Gemini only)
  const meta = py?.ocr_fields?.ocr_meta;
  if (!meta || typeof meta !== 'object' || !(Number(meta.version) >= 2)) return null;
  if (py.table_found === false) return null;
  if (!meta.located || meta.located[field] !== true) return null;
  const v = meta.ink && typeof meta.ink === 'object' ? meta.ink[field] : undefined;
  if (typeof v !== 'number' || !Number.isFinite(v) || v < 0) return null;
  if (v <= OC_EMPTY_MAX) return 'empty';
  if (v >= OC_INK_MIN) return 'ink';
  return null;
}
function ocText(v) {
  return (v === null || v === undefined || typeof v === 'object') ? '' : String(v).replace(/\s+/g, ' ').trim();
}
function ocRealRemark(t) {
  const key = t.toLowerCase().replace(/[‘’]/g, "'");
  return !!key && !OC_PLACEHOLDERS.has(key) && !/^[\p{P}\p{S}\s]+$/u.test(key);
}
function ocCorrectedRows(gemini, geminiOk) {
  if (!geminiOk || !gemini || !Array.isArray(gemini.corrected_rows)) return [];
  const seen = new Set();
  for (const r of gemini.corrected_rows) {
    const k = ocText(r).toLowerCase();
    if (OC_ROW_RE.test(k)) seen.add(k);
  }
  const order = (k) => (k[0] === 'm' ? 0 : 10) + Number(k.slice(1));
  return [...seen].sort((a, b) => order(a) - order(b));
}
// -> { reasons, notes, drop_student_id, check } ; studentIdMissed: emit student_id_missed (phone only)
function ocCrossCheck(py, gemini, geminiOk, { studentIdMissed = false } = {}) {
  const reasons = [];
  const notes = [];
  let dropStudentId = false;
  const formOk = geminiOk && !(gemini && gemini.document_check && gemini.document_check.is_sast_form === false);
  const sidState = formOk ? ocInkState(py, 'student_id') : null;
  const remState = formOk ? ocInkState(py, 'remarks') : null;
  const sid = formOk ? ocText(gemini.student_id) : '';
  const rem = formOk ? ocText(gemini.remarks) : '';
  if (sidState === 'empty' && /[0-9a-z]/i.test(sid)) {
    dropStudentId = true;
    reasons.push('student_id_unverified');
    notes.push('The AI read a student ID but the ID box looks empty. Check the sheet.');
  } else if (sidState === 'ink' && !sid && studentIdMissed) {
    reasons.push('student_id_missed');
    notes.push('The student ID box has writing but the AI read no student ID. Type it from the sheet.');
  }
  if (remState === 'ink' && !rem) {
    reasons.push('remarks_missed');
    notes.push('The remarks box has writing but the AI read no remark. Type it from the sheet if it is a comment.');
  } else if (remState === 'empty' && ocRealRemark(rem)) {
    reasons.push('remarks_unverified');
    notes.push('The AI read a remark but the remarks box looks empty. Check the sheet.');
  }
  const corrected = ocCorrectedRows(gemini, geminiOk);
  if (corrected.length) {
    reasons.push('white_ink_correction');
    notes.push(`Correction fluid/tape or a crossed-out mark was seen on ${corrected.join(', ')}. `
      + 'Check those rows on the sheet: only the remaining uncovered check counts.');
  }
  return {
    reasons, notes, drop_student_id: dropStudentId,
    check: { student_id_ink: sidState, remarks_ink: remState, corrected_rows: corrected,
      ...(dropStudentId ? { student_id_ai: sid.slice(0, 50) } : {}) },
  };
}
