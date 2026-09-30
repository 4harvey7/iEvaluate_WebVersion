const ctx = $('Website Prepare Scan').first(0).json;

let reason = 'This image does not appear to be a SAST evaluation form.';
try {
  const text = $input.first().json?.candidates?.[0]?.content?.parts?.[0]?.text || '';
  const match = String(text).match(/\{[\s\S]*\}/);
  if (match) {
    const g = JSON.parse(match[0]);
    reason = g?.document_check?.reason || reason;
  }
} catch (e) {}

return [{
  json: {
    status: 'rejected',
    flagged: false,
    task_id: ctx.task_id,
    review_reasons: ['not_a_sast_form'],
    identity_check: null,
    message: `Rejected, nothing was saved: ${String(reason).slice(0, 300)}`,
  },
}];
