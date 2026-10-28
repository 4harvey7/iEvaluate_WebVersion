// sast-manual-correction: the instructor / subject ids for 'get instructor id7' / 'get subject id6'.
// A valid id in the body (the website review page, the current app) is passed on as it is (the SQL
// still checks it). Without one (older app versions send only the typed names) the names are matched
// LOCALLY against 'Correction Candidates' with name_match.js: only a clear match gives an id; several
// close matches give no id AND no name for the SQL name lookup (it would just pick one), so the
// correction is refused with HTTP 422 and the candidates ('Correction Not Found'). No match keeps the
// typed text for the SQL lookup, as before. Nothing here is sent to Gemini.
// @@include name_match.js

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const INSTRUCTOR_MATCH_MIN    = 0.70;   // same as the phone reconciler / Website Decide
const INSTRUCTOR_MATCH_MARGIN = 0.10;
const SUBJECT_MATCH_MIN       = 0.60;   // title score (a code hit is exact)
const SUBJECT_NAME_COVER      = 0.75;   // share of the subject title the text must show
const SUBJECT_MATCH_MARGIN    = 0.10;
const MAX_CANDIDATES          = 5;
const CANDIDATE_MIN_SCORE     = 0.30;
const EPS = 1e-6;

function cmText(v) {
  return (typeof v === 'string' || typeof v === 'number') ? String(v).trim() : '';
}
function cmId(v) {
  const s = cmText(v).toLowerCase();
  return UUID_RE.test(s) ? s : '';
}
const r2 = (x) => (typeof x === 'number' ? Math.round(x * 100) / 100 : null);

let body = {};
try { body = $('sast-manual-correction1').first().json.body || {}; } catch (e) {}
let cands = null;   // null = the candidate lookup failed: the SQL lookups run on the body as before
try {
  const c = $('Correction Candidates').first().json || {};
  if (Number(c.candidates_version) === 1) cands = c;
} catch (e) {}

const instructors = cands ? nmList(cands.instructors)
  .filter((x) => x && UUID_RE.test(String(x.id || '').toLowerCase()) && String(x.name || '').trim())
  .map((x) => ({ id: String(x.id).toLowerCase(), name: String(x.name).trim() })) : null;
const subjects = cands ? nmList(cands.subjects)
  .filter((x) => x && UUID_RE.test(String(x.id || '').toLowerCase()) && (String(x.code || '').trim() || String(x.name || '').trim()))
  .map((x) => ({ id: String(x.id).toLowerCase(), code: String(x.code || '').trim(), name: String(x.name || '').trim() })) : null;

// ---- instructor
function matchInstructor() {
  const raw = cmText(body.instructor);
  const id = cmId(body.instructor_id);
  if (id) return { match: 'id', id, sqlName: raw, score: null, candidates: [] };
  if (!raw) return { match: 'missing', id: '', sqlName: '', score: null, candidates: [] };
  if (!instructors) return { match: 'unavailable', id: '', sqlName: raw, score: null, candidates: [] };
  const best = nmBestInstructor(raw, instructors.map((x) => x.name), INSTRUCTOR_MATCH_MIN, INSTRUCTOR_MATCH_MARGIN);
  const got = nmNameTokens(raw);
  const scored = instructors
    .map((x) => ({ ...x, score: nmScore(nmNameTokens(x.name), got).score }))
    .sort((a, b) => (b.score - a.score) || a.name.localeCompare(b.name) || a.id.localeCompare(b.id));
  if (best.match === 'found') {
    const hits = instructors.filter((x) => x.name === best.name);
    if (hits.length === 1) return { match: 'found', id: hits[0].id, sqlName: raw, score: r2(best.score), candidates: [] };
  }
  if (best.match === 'ambiguous' || best.match === 'found') {
    const names = new Set(best.match === 'found' ? [best.name] : best.contenders);
    const close = scored.filter((x) => names.has(x.name)).slice(0, MAX_CANDIDATES)
      .map((x) => ({ id: x.id, name: x.name, score: r2(x.score) }));
    return { match: 'ambiguous', id: '', sqlName: '', score: r2(best.score), candidates: close };
  }
  const shown = scored.filter((x) => x.score >= CANDIDATE_MIN_SCORE - EPS).slice(0, MAX_CANDIDATES)
    .map((x) => ({ id: x.id, name: x.name, score: r2(x.score) }));
  return { match: 'none', id: '', sqlName: raw, score: null, candidates: shown };
}

// ---- subject (code on the text first, then the title)
function matchSubject() {
  const raw = cmText(body.subject);
  const id = cmId(body.subject_id);
  if (id) return { match: 'id', id, sqlText: raw, score: null, candidates: [] };
  if (!raw) return { match: 'missing', id: '', sqlText: '', score: null, candidates: [] };
  if (!subjects) return { match: 'unavailable', id: '', sqlText: raw, score: null, candidates: [] };
  const all = subjects.map((s) => {
    const fit = nmSubjectFit(s.code, s.name, raw);
    const score = fit.exact ? 1 : Math.max(fit.fuzzy ? 0.9 : 0, fit.name_score);
    return { ...s, exact: fit.exact, fuzzy: fit.fuzzy, cover: fit.name_cover, title: fit.name_score, score };
  }).sort((a, b) => (Number(b.exact) - Number(a.exact)) || (b.score - a.score) || a.code.localeCompare(b.code)
    || a.id.localeCompare(b.id));
  const out = (x) => ({ id: x.id, code: x.code, name: x.name, score: r2(x.score) });
  const exact = all.filter((x) => x.exact);
  if (exact.length === 1) return { match: 'found', id: exact[0].id, sqlText: raw, score: 1, candidates: [] };
  if (exact.length > 1) {
    return { match: 'ambiguous', id: '', sqlText: '', score: 1, candidates: exact.slice(0, MAX_CANDIDATES).map(out) };
  }
  // a course code on the text that is no subject: never pick a subject by its title instead
  const unknownCode = nmCodeKeys(raw).some((k) => /[A-Z]/.test(k) && /[0-9]{2}/.test(k));
  const strong = unknownCode ? [] : all.filter((x) => x.title >= SUBJECT_MATCH_MIN - EPS && x.cover >= SUBJECT_NAME_COVER - EPS)
    .sort((a, b) => b.title - a.title);
  if (strong.length === 1 || (strong.length > 1 && strong[0].title - strong[1].title >= SUBJECT_MATCH_MARGIN - EPS)) {
    return { match: 'found', id: strong[0].id, sqlText: raw, score: r2(strong[0].title), candidates: [] };
  }
  if (strong.length > 1) {
    const close = strong.filter((x) => strong[0].title - x.title < SUBJECT_MATCH_MARGIN - EPS);
    return { match: 'ambiguous', id: '', sqlText: '', score: r2(strong[0].title), candidates: close.slice(0, MAX_CANDIDATES).map(out) };
  }
  const shown = all.filter((x) => x.fuzzy || x.score >= CANDIDATE_MIN_SCORE - EPS).slice(0, MAX_CANDIDATES).map(out);
  return { match: 'none', id: '', sqlText: raw, score: null, candidates: shown };
}

const instr = matchInstructor();
const subj = matchSubject();

return [{
  json: {
    correction_match_version: 1,
    instructor_id: instr.id,          // -> get instructor id7 $2 ('' = none)
    instructor_name: instr.sqlName,   // -> get instructor id7 $1 ('' = no name lookup)
    subject_id: subj.id,              // -> get subject id6 $3
    subject_text: subj.sqlText,       // -> get subject id6 $2
    instructor_match: { match: instr.match, score: instr.score, candidates: instr.candidates },
    subject_match: { match: subj.match, score: subj.score, candidates: subj.candidates },
  },
}];
