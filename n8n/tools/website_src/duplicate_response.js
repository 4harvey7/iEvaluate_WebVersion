const ctx = $('Website Prepare Scan').first(0).json;
const j = $input.first()?.json ?? {};

let message;
if (j.in_queue === true && j.in_raw !== true) {
  message = 'Already in the flagged-scan review queue (same task_id). Nothing was changed.';
} else {
  message = 'Already processed and saved (same task_id). Nothing was changed.';
}

return [{
  json: {
    status: 'duplicate',
    flagged: false,
    task_id: ctx.task_id,
    review_reasons: [],
    identity_check: null,
    message,
  },
}];
