// Google Sheet import, new 15-item forms: which SET-15 comments go to the student-remark AI.
// Runs ONCE for all rows (after 'Save New Form Row' has saved every row) and feeds 'Instructor message2', so the
// grouped Gemini loop (word cloud, tone, student_remarks, critical-feedback pushes) runs once per import, as for
// the old SAST sheet. A row is passed on only when
//   - it is a SET-15 form (supervisor SUP-15 comments never go to the student-remark AI),
//   - the database saved it and counted it (result.saved && result.counted; forms under 5 answers are left out),
//   - it is not a re-import of a stored row (result.updated !== true: no double word cloud, no repeated pushes),
//   - its comment is not empty or a placeholder (N/A, none, ...), by the same cleanRemark as the other paths.
// Privacy: only the comment text and the ids move on; no instructor name is added to anything.
// @@include remark_clean.js

const saved = $input.all();
let sources = [];
try {
  sources = $('Sheet New Form?').all(0);
} catch (e) {
  sources = [];
}

const out = [];
saved.forEach((item, i) => {
  const r = (item.json && item.json.result) || {};
  if (r.saved !== true || r.counted !== true || r.updated === true) return;
  const pi = item.pairedItem;
  const idx = pi && typeof pi.item === 'number' ? pi.item : i;
  const src = (sources[idx] && sources[idx].json) || {};
  if (src.instrument !== 'SET-15') return;
  const remark = cleanRemark(src['Remarks & Suggestions']);
  if (!remark) return;
  out.push({
    json: {
      instructor_ID: src.instructor_ID,
      current_term_id: src.current_term_id,
      true_subject_id: src.true_subject_id,
      'Subject Taught': src['Subject Taught'] || '',
      'Remarks & Suggestions': remark,
    },
  });
});
return out;
