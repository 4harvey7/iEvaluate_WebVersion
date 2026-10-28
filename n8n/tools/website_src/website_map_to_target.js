// Same fields as 'Map to Target Format3', credited to the batch.
const d = $('Website Decide').first(0).json;
const rec = $('Website Reconcile OCR OMR').first(0).json;

const result = {};
result['instructor_ID'] = d.instructor_id;
result['true_subject_id'] = d.subject_id;
result['current_term_id'] = d.term_id;
result['scan_image'] = null;           // website rows never reach import_errors
result['review_reasons'] = [];
result['omr_comparisons'] = rec.comparisons || null;
result['review_note'] = d.review_note || null;
result['task_id'] = d.task_id;
// stamp rule: already stored by Website Raw Upsert; mapped so the shared row is the same
if (d.stamp_no) {
  result['stamp_no'] = d.stamp_no;
  result['stamp_source'] = d.stamp_source || 'gemini';
}

result['Student ID'] = d.student_id || '';
result['Instructor/Professor'] = d.instructor_name || '';
result['Subject Taught'] = d.subject_name || d.subject_code || '';
result['AY/Semester'] = rec.ay_semester || '';
result['Remarks & Suggestions'] = d.remark || '';
// YYYY-MM-DD so the date never shifts with the server timezone
result['Date'] = d.submitted_date;

for (let i = 1; i <= 10; i++) {
  // blank row stays null (stored as NULL, never 0)
  result[`M${i}`] = d.ratings?.[`m${i}`] ?? null;
  result[`P${i}`] = d.ratings?.[`p${i}`] ?? null;
}

return [{ json: result }];
