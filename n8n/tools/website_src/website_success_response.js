const ctx = $('Website Prepare Scan').first(0).json;
const d = $('Website Decide').first(0).json;

const parts = [`Saved and credited to ${d.instructor_name || 'the batch instructor'} (${d.subject_code || d.subject_name || 'batch subject'}).`];
if (typeof d.agreement_percent === 'number') parts.push(`Python and Gemini agreed on ${d.agreement_percent}% of the rows.`);

return [{
  json: {
    status: 'success',
    flagged: false,
    task_id: ctx.task_id,
    review_reasons: [],
    identity_check: null,
    auto_rejected: null,
    stamp_no: d.stamp_no || null,   // the stamp stored with the raw row
    message: parts.join(' ').slice(0, 1000),
  },
}];
