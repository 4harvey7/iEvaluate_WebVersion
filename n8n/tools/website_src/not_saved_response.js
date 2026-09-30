// DB error, nothing stored, so the backend can retry.
function errText(j) {
  if (!j) return '';
  const e = j.error;
  if (typeof e === 'string') return e;
  if (e && typeof e === 'object') return String(e.message || e.description || JSON.stringify(e)).slice(0, 300);
  return String(j.message || '').slice(0, 300);
}

const j = $input.first()?.json ?? {};
let taskId = '';
try { taskId = $('Website Prepare Scan').first(0).json.task_id || ''; } catch (e) {}

return [{
  json: {
    status: 'flagged',
    flagged: true,
    task_id: taskId,
    review_reasons: ['internal_error_not_saved'],
    identity_check: null,
    message: `NOT SAVED: database error in n8n (${errText(j) || 'unknown error'}). Send this page again.`,
  },
}];
