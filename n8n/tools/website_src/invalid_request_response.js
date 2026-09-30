// Resending the same payload fails again, so not retryable.
function errText(j) {
  if (!j) return '';
  const e = j.error;
  if (typeof e === 'string') return e;
  if (e && typeof e === 'object') return String(e.message || e.description || JSON.stringify(e)).slice(0, 300);
  return String(j.message || '').slice(0, 300);
}

const j = $input.first()?.json ?? {};
let body = {};
try { body = $('SAST from Website').first(0).json.body ?? {}; } catch (e) {}
const taskId = typeof body.task_id === 'string' ? body.task_id.slice(0, 200) : '';

return [{
  json: {
    status: 'flagged',
    flagged: true,
    task_id: taskId,
    review_reasons: ['invalid_request', 'not_saved_permanent'],
    identity_check: null,
    message: `NOT SAVED: the request was rejected by n8n (${errText(j) || 'invalid payload'}). Resending the same payload will fail again; fix the request.`,
  },
}];
