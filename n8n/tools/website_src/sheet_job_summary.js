// Google Sheet import job: the final counts for 'Mark Import Job Done' (sheet_import_jobs, migration 018).
// Runs once, after everything below If19 (the raw upsert loop, recompute, import_errors, the Gemini
// remark loop): it is the second child of 'After Sheet Response' and sits BELOW If19 on the canvas,
// and under executionOrder v1 n8n finishes the whole If19 branch before it starts a lower sibling.
// It only reads what the earlier nodes returned (every run of a loop node); it sends nothing to an AI.
// Counts (migration 021): imported_count = rows saved as NEW forms; error_count = rows refused only (rows an
// earlier import already stored are skipped, not errors). 'details' = the per-reason breakdown for the app:
//   {form_type, term_label, rows, new, already_imported, excluded_under_5, rejected,
//    reasons:[{reason, kind, plain_text, count, rows:[Google Sheets row numbers]}]}
// No student id and no instructor name goes into it.
// No job id (migration 018 missing, the job insert failed): returns no item, so nothing is updated.
const MAX_RUNS = 5000;

function jsonOf(item) {
  return (item && item.json) || {};
}

function nodeItems(name) {
  try {
    const node = $(name);
    if (!node || node.isExecuted === false) return [];
    return (node.all(0) || []).map(jsonOf);
  } catch (e) {
    return [];
  }
}

// every run of a node (a loop node runs once per pass); branch 1 = the error output
function nodeRuns(name, branch) {
  let node;
  try {
    node = $(name);
  } catch (e) {
    return [];
  }
  if (!node || node.isExecuted === false) return [];
  const runs = [];
  for (let r = 0; r < MAX_RUNS; r++) {
    let items;
    try {
      items = node.all(branch, r);
    } catch (e) {
      if (branch === 0) break;
      try {
        node.all(0, r);
      } catch (e2) {
        break;
      }
      items = [];
    }
    runs.push((items || []).map(jsonOf));
  }
  return runs;
}

const hasError = (j) => j && j.error !== undefined && j.error !== null;
const countItems = (runs, pred) => runs.reduce((n, run) => n + run.filter(pred).length, 0);
const failedRuns = (runs) => runs.filter((run) => run.length > 0).length;

// New 15-item forms (SET-15 / SUP-15): 'Save New Form Row' returns save_eval_response's answer per row.
// Migration 021: a row that an EARLIER import already stored comes back as reason 'already_imported': it is
// skipped (not saved again, not replaced) and it is NOT an error.
const ALREADY_IMPORTED_TEXT = 'already imported earlier – skipped';
const NEW_FORM_REASONS = {
  invalid_rating: 'a rating that is not 1 to 5',
  supervisor_not_found: 'the supervisor was not found (use the exact email or full name of a department head)',
  missing_evaluator: 'the supervisor is missing',
  self_evaluation: 'a supervisor cannot evaluate himself or herself',
  duplicate_stamp: 'this stamp number was already used this term',
  duplicate_student: 'this student already evaluated this instructor for this subject this term',
  duplicate_supervisor: 'this supervisor already evaluated this instructor and subject',
  instrument_not_for_term: 'the term uses a different form',
  missing_subject: 'the subject is missing',
  missing_instructor_or_term: 'the instructor or term is missing',
  invalid_answers: 'the answers could not be read',
  invalid_item: 'an unknown item column',
  duplicate_item: 'the same item column twice',
  unknown_reference: 'the instructor, subject or term was not found',
  task_id_conflict: 'this row was imported before as a different form',
};
const REASON_TEXT = {
  ...NEW_FORM_REASONS,
  already_imported: ALREADY_IMPORTED_TEXT,
  excluded_under_5: 'saved, but left out of the scores: fewer than 5 answers',
  unmatched: 'the instructor or subject was not found (sent to Import Errors)',
  database_error: 'the database refused the row',
  repeated_row_merged: 'the same student, instructor and subject twice in the sheet: the last row was kept',
};
const reasonText = (key) => REASON_TEXT[key] || key;
// the Google Sheets row number of a row ('Sheet Row' from Normalize Sheet Rows: the header is row 1)
const sheetRowOf = (r, i) => Number(r && r.sheet_row_no) || i + 2;
const rowsText = (rows) => rows.slice(0, 8).join(', ') + (rows.length > 8 ? ', ...' : '');
// skipped = an earlier import stored it; excluded = saved, not in the scores; merged = repeated SAST row; else refused
function kindOf(key) {
  if (key === 'already_imported') return 'skipped';
  if (key === 'excluded_under_5') return 'excluded';
  if (key === 'repeated_row_merged') return 'merged';
  return 'rejected';
}

// items: the Save New Form Row answers; savedRows: the matched rows of Map to Target Format, in the same order
function summarizeNewForms(items, savedRows) {
  const out = { total: items.length, saved: 0, excluded: 0, already: 0, failed: 0, reasons: {} };
  const add = (key, row) => { (out.reasons[key] = out.reasons[key] || []).push(row); };
  items.forEach((it, i) => {
    const res = it.result && typeof it.result === 'object' ? it.result : null;
    const row = Number(it.row_no) || sheetRowOf(savedRows[i], i);
    if (hasError(it) || !res) {
      out.failed++;
      add('database_error', row);
    } else if (res.saved) {
      out.saved++;
      if (res.counted === false) {   // kept, but fewer than 5 answers: not in the scores
        out.excluded++;
        add('excluded_under_5', row);
      }
    } else if (res.reason === 'already_imported') {
      out.already++;
      add('already_imported', row);
    } else {
      out.failed++;
      add(String(res.reason || 'unknown'), row);
    }
  });
  return out;
}

function summarize() {
  const rows = nodeItems('Map to Target Format');
  const isMatched = (r) => !!(r.instructor_ID && r.true_subject_id);
  const matched = rows.filter(isMatched).length;
  const unmatched = rows.length - matched;
  const unmatchedRows = [];
  rows.forEach((r, i) => { if (!isMatched(r)) unmatchedRows.push(sheetRowOf(r, i)); });
  const formType = (rows.find((r) => r.instrument) || {}).instrument || 'SAST-20';
  const termId = String((rows.find((r) => r.current_term_id) || {}).current_term_id || '');

  // raw rows: 'Code in JavaScript' (dedupe) -> Loop Over Items3 -> 'Insert or update rows in a table1'
  // (continueRegularOutput: a failed row comes back as an item with .error)
  const toSave = nodeItems('Code in JavaScript').filter((r) => !r._raw_already_stored).length;
  const saveFailed = countItems(nodeRuns('Insert or update rows in a table1', 0), hasError);
  const nf = summarizeNewForms(nodeItems('Save New Form Row'), rows.filter(isMatched));
  const merged = matched > toSave && toSave > 0 ? matched - toSave : 0;
  const imported = Math.max(0, toSave - saveFailed) + nf.saved;
  const rejected = unmatched + saveFailed + nf.failed;
  const importErrorsFailed = countItems(nodeRuns('Insert import_errors1', 1), () => true);

  // grouped remark AI: one loop pass per instructor+term group; a pass fails at most once
  const aiGroups = nodeItems('Group feedback by instructor1').length;
  const aiFailed = ['Unified AI Analysis1', 'Parse Unified AI Result1', 'insert data to student remarks2']
    .reduce((n, name) => n + failedRuns(nodeRuns(name, 1)), 0);
  const otherFailed = {
    'store results2': failedRuns(nodeRuns('store results2', 1)),
    'insert to word cloud2': failedRuns(nodeRuns('insert to word cloud2', 1)),
    'update to overall total2': failedRuns(nodeRuns('update to overall total2', 1)),
  };

  const problems = [];
  let firstFailed = null;
  const note = (n, node, text) => {
    if (n > 0) {
      problems.push(text);
      if (!firstFailed) firstFailed = node;
    }
  };
  note(saveFailed, 'Insert or update rows in a table1', `${saveFailed} rows could not be saved`);
  note(importErrorsFailed, 'Insert import_errors1', `${importErrorsFailed} unmatched rows could not be saved to Import Errors`);
  note(aiFailed, 'Unified AI Analysis1', `AI remark analysis failed for ${aiFailed} of ${aiGroups} instructors`);
  note(otherFailed['update to overall total2'], 'update to overall total2', 'the dashboard totals could not be recalculated');
  note(otherFailed['store results2'], 'store results2', 'an AI summary could not be saved');
  note(otherFailed['insert to word cloud2'], 'insert to word cloud2', 'a word cloud could not be saved');

  // details (sheet_import_jobs.details, migration 021): one entry per reason, with the sheet row numbers
  const reasonRows = {};
  if (unmatchedRows.length) reasonRows.unmatched = unmatchedRows;
  for (const key of Object.keys(nf.reasons)) reasonRows[key] = (reasonRows[key] || []).concat(nf.reasons[key]);
  const reasons = Object.keys(reasonRows).map((key) => ({
    reason: key, kind: kindOf(key), plain_text: reasonText(key), count: reasonRows[key].length,
    rows: reasonRows[key].slice().sort((a, b) => a - b),
  }));
  if (saveFailed > 0) {   // the SAST raw upsert does not say which row failed
    const db = reasons.find((r) => r.reason === 'database_error');
    if (db) db.count += saveFailed;
    else reasons.push({ reason: 'database_error', kind: 'rejected', plain_text: reasonText('database_error'), count: saveFailed, rows: [] });
  }
  if (merged > 0) {
    reasons.push({ reason: 'repeated_row_merged', kind: 'merged', plain_text: reasonText('repeated_row_merged'), count: merged, rows: [] });
  }
  const order = { skipped: 0, excluded: 1, merged: 2, rejected: 3 };
  reasons.sort((a, b) => order[a.kind] - order[b.kind] || b.count - a.count);
  const details = {
    form_type: formType,
    term_label: null,   // filled in by 'Mark Import Job Done' from academic_terms
    rows: rows.length,
    new: imported,
    already_imported: nf.already,
    excluded_under_5: nf.excluded,
    rejected,
    reasons,
  };

  // the old one-line message (older app builds show only this)
  let message = `Import finished: ${imported} of ${rows.length} rows saved`;
  if (nf.already > 0) {
    message += `, ${nf.already} ${ALREADY_IMPORTED_TEXT} (row ${rowsText(nf.reasons.already_imported)})`;
  }
  if (unmatched > 0) message += `, ${unmatched} unmatched rows sent to Import Errors`;
  if (merged > 0) message += ` (${merged} repeated rows merged)`;
  if (nf.total > 0) {
    if (nf.excluded > 0) message += ` (${nf.excluded} saved but left out of the scores: fewer than 5 answers)`;
    const notes = Object.keys(nf.reasons)
      .filter((key) => kindOf(key) === 'rejected')
      .map((key) => `${nf.reasons[key].length} not saved: ${reasonText(key)} (row ${rowsText(nf.reasons[key])})`);
    if (notes.length) message += `. Not saved: ${notes.join('; ')}`;
  }
  // the AI comment analysis (word cloud, tone, summary) runs for SET comments and for the old SAST sheet,
  // not for the supervisor form; groups from SET rows are counted in aiGroups
  message += aiGroups > 0
    ? `. AI remark analysis done for ${aiGroups - aiFailed} of ${aiGroups} instructors`
      + (nf.total > 0 ? ' (SET comments; the supervisor form is not analysed by the AI).' : '.')
    : (nf.total > 0 ? '. The AI comment analysis runs for SET comments, not for the supervisor form.' : '.');
  if (problems.length) message += ` Problems: ${problems.join('; ')}.`;

  return {
    rows: rows.length,
    matched,
    unmatched,
    imported_count: imported,
    error_count: rejected,
    ai_groups: aiGroups,
    ai_failed: aiFailed,
    has_problems: problems.length > 0,
    first_failed_node: firstFailed,
    message: message.slice(0, 1000),
    term_id: termId,
    details,
  };
}

let jobId = null;
try {
  const job = $('Create Sheet Import Job').first();
  jobId = job && job.json && job.json.id ? String(job.json.id) : null;
} catch (e) {
  jobId = null;
}
if (!jobId) return [];
return [{ json: { job_id: jobId, ...summarize() } }];
