// Google Sheet import job: describes a failure for 'Mark Import Job Failed' (sheet_import_jobs, migration 018).
// Fed by the error outputs of the nodes after 'Sheet Import Response' that can fail without stopping the
// import (the AI call after its retries, the parse, the inserts, the recompute) and by If14 (a raw row
// that could not be saved). Those error outputs still go to Wait1 where they did before, so the paced
// remark loop goes on. The text is for SAO staff; no instructor name is added (the error text is the
// node's own). Phone / website / correction executions also reach the shared nodes: the UPDATE then
// matches no job row and changes nothing.
const FAILURES = {
  'Unified AI Analysis1': ['Unified AI Analysis1',
    'The AI remark analysis failed for one instructor after 3 tries (Gemini limit or error). That instructor\'s comments summary was skipped; the survey rows are saved.'],
  'Parse Unified AI Result1': ['Parse Unified AI Result1',
    'The AI answer for one instructor could not be read. That instructor\'s comments summary was skipped; the survey rows are saved.'],
  'insert data to student remarks2': ['insert data to student remarks2',
    'The analysed comments of one instructor could not be saved.'],
  'store results2': ['store results2', 'The AI summary of one instructor could not be saved.'],
  'insert to word cloud2': ['insert to word cloud2', 'The word cloud of one instructor could not be saved.'],
  'If14': ['Insert or update rows in a table1', 'Some survey rows could not be saved to the database.'],
  'Insert or update rows in a table1': ['Insert or update rows in a table1', 'Some survey rows could not be saved to the database.'],
  'Insert import_errors1': ['Insert import_errors1', 'Some unmatched rows could not be saved to Import Errors.'],
  'update to overall total2': ['update to overall total2', 'The instructor totals (dashboard) could not be recalculated.'],
};

function errorText(e) {
  if (e === undefined || e === null) return '';
  if (typeof e === 'string') return e;
  if (typeof e === 'object') return String(e.message || e.description || JSON.stringify(e));
  return String(e);
}

let prev = '';
try {
  prev = (typeof $prevNode !== 'undefined' && $prevNode && $prevNode.name) || '';
} catch (e) {
  prev = '';
}
const items = $input.all();
const first = items.length ? (items[0].json || {}) : {};
const detail = errorText(first.error !== undefined ? first.error : first.message).replace(/\s+/g, ' ').trim();
const known = FAILURES[prev] || [prev || 'unknown step', 'A step of the import failed.'];
let message = known[1];
if (items.length > 1) message += ` (${items.length} items)`;
if (detail) message += ` Error: ${detail}`;
return [{ json: { failed_node: known[0], message: message.slice(0, 1000), failed_items: items.length } }];
