// Google Sheet import, SET-15 (student form): the low-score push, once per instructor per import.
// Runs ONCE for all rows (after 'Save New Form Row' has saved every row) and feeds If28, the same push as the
// old SAST path (If28 -> department head + instructor FCM tokens -> "below 3.0" push). save_eval_response
// returns, for every saved row, the instructor's POOLED overall result for the term after that row
// (recompute_eval, all subjects); the last saved row of an instructor holds the final value of this import.
// An instructor is passed on when that pooled overall_pct is below 60 (= a mean below 3.0 on the 1..5 scale).
// Only rows this import saved count (reason already_imported / refused rows have no result), so a re-import
// that adds nothing sends no push. SUP-15 (supervisor form) never pushes here.
// Privacy: only the instructor id, term id and the pooled score move on; never a student id, name or comment.
const LOW_PCT = 60;

const saved = $input.all();
let sources = [];
try {
  sources = $('Sheet New Form?').all(0);
} catch (e) {
  sources = [];
}

const last = new Map();   // instructor|term -> the pooled result after this import's last saved row
saved.forEach((item, i) => {
  const r = (item.json && item.json.result) || {};
  if (r.saved !== true || r.instrument !== 'SET-15' || !r.result || typeof r.result !== 'object') return;
  const pi = item.pairedItem;
  const idx = pi && typeof pi.item === 'number' ? pi.item : i;
  const src = (sources[idx] && sources[idx].json) || {};
  if (src.instrument !== 'SET-15' || !src.instructor_ID || !src.current_term_id) return;
  const key = src.instructor_ID + '|' + src.current_term_id;
  last.delete(key);   // keep the insertion order of the LAST row
  last.set(key, { instructor_id: src.instructor_ID, term_id: src.current_term_id, result: r.result });
});

const out = [];
for (const v of last.values()) {
  const pct = v.result.overall_pct;
  if (pct === null || pct === undefined || pct === '' || !(Number(v.result.respondents) > 0)) continue;
  if (!(Number(pct) < LOW_PCT)) continue;
  out.push({
    json: {
      set_low_score: true,
      instrument: 'SET-15',
      instructor_id: v.instructor_id,
      term_id: v.term_id,
      overall_pct: Number(pct),
      overall_mean: v.result.overall_mean === null || v.result.overall_mean === undefined ? null : Number(v.result.overall_mean),
      respondents: Number(v.result.respondents),
    },
  });
}
return out;
