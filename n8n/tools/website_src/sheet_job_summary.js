// Google Sheet import job: the final counts for 'Mark Import Job Done' (sheet_import_jobs, migration 018).
// Runs once, after everything below If19 (the raw upsert loop, recompute, import_errors, the Gemini
// remark loop): it is the second child of 'After Sheet Response' and sits BELOW If19 on the canvas,
// and under executionOrder v1 n8n finishes the whole If19 branch before it starts a lower sibling.
// It only reads what the earlier nodes returned (every run of a loop node); it sends nothing to an AI.
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

function summarize() {
  const rows = nodeItems('Map to Target Format');
  const matched = rows.filter((r) => r.instructor_ID && r.true_subject_id).length;
  const unmatched = rows.length - matched;

  // raw rows: 'Code in JavaScript' (dedupe) -> Loop Over Items3 -> 'Insert or update rows in a table1'
  // (continueRegularOutput: a failed row comes back as an item with .error)
  const toSave = nodeItems('Code in JavaScript').filter((r) => !r._raw_already_stored).length;
  const saveFailed = countItems(nodeRuns('Insert or update rows in a table1', 0), hasError);
  const imported = Math.max(0, toSave - saveFailed);
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

  let message = `Import finished: ${imported} of ${rows.length} rows saved`;
  if (unmatched > 0) message += `, ${unmatched} unmatched rows sent to Import Errors`;
  if (matched > toSave && toSave > 0) message += ` (${matched - toSave} repeated rows merged)`;
  message += aiGroups > 0
    ? `. AI remark analysis done for ${aiGroups - aiFailed} of ${aiGroups} instructors.`
    : '.';
  if (problems.length) message += ` Problems: ${problems.join('; ')}.`;

  return {
    rows: rows.length,
    matched,
    unmatched,
    imported_count: imported,
    error_count: unmatched + saveFailed,
    ai_groups: aiGroups,
    ai_failed: aiFailed,
    has_problems: problems.length > 0,
    first_failed_node: firstFailed,
    message: message.slice(0, 1000),
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
