// Token fuzzy matching for instructor names and subjects (Website Decide, phone reconciler).
// Each database token is paired with its best form token (edit distance, initials, prefixes);
// the score is the weighted share of matched tokens (0.75 database side + 0.25 form side).
const NM_TOKEN_MIN = 0.55;       // weaker token pairs count as no match
const NM_KNOWN_RAW_MIN = 0.30;   // Gemini's pick from the list needs this much support from the raw reading
const NM_TITLES = new Set(['prof', 'professor', 'dr', 'doc', 'mr', 'mrs', 'ms', 'miss', 'madam', 'maam', 'sir', 'engr',
  'eng', 'atty', 'arch', 'rev', 'fr', 'hon', 'instr', 'instructor', 'teacher', 'phd', 'edd', 'dba', 'mba', 'mit', 'mat',
  'maed', 'lpt', 'rn', 'cpa', 'jr', 'sr', 'ii', 'iii', 'iv']);
const NM_STOP = new Set(['and', 'of', 'the', 'to', 'in', 'for', 'with', 'on', 'a', 'an', 'at']);
function nmFold(s) {
  return String(s ?? '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
}
// person name -> tokens, titles and degrees dropped
function nmNameTokens(s) {
  return nmFold(s).replace(/\bma['’‘`´]?am\b/g, ' maam ').replace(/[^a-z]+/g, ' ').split(' ')
    .filter((w) => w && !NM_TITLES.has(w));
}
// subject title -> word tokens; tokens mixing letters and digits are codes and left out
function nmSubjectTokens(s) {
  return nmFold(s).replace(/[^a-z0-9]+/g, ' ').split(' ')
    .filter((w) => w && !NM_STOP.has(w) && !(/[a-z]/.test(w) && /[0-9]/.test(w)));
}
function nmAlnum(s) {
  return nmFold(s).replace(/[^a-z0-9]/g, '').toUpperCase();
}
// code-like keys on the form (letters + digits), e.g. "PC 121" -> PC121
function nmCodeKeys(s) {
  const up = nmFold(s).toUpperCase();
  const keys = new Set();
  for (const m of up.matchAll(/\b([A-Z]{2,8})[ .-]?([0-9]{1,4}[A-Z]?)\b/g)) keys.add(m[1] + m[2]);
  const whole = nmAlnum(s);
  if (/[A-Z]/.test(whole) && /[0-9]/.test(whole) && whole.length <= 12) keys.add(whole);
  return [...keys].filter((k) => k.length >= 3);
}
function nmEdit(a, b) {
  let prev = Array.from({ length: b.length + 1 }, (_, j) => j);
  for (let i = 1; i <= a.length; i++) {
    const cur = [i];
    for (let j = 1; j <= b.length; j++) {
      cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
    }
    prev = cur;
  }
  return prev[b.length];
}
// want = database token, got = form token
function nmTokenSim(want, got) {
  if (want === got) return 1;
  if (want.length === 1 || got.length === 1) return want[0] === got[0] ? 0.7 : 0;
  if (got.length >= 4 && want.startsWith(got)) return 0.85;   // abbreviation, e.g. prog / intro
  const r = 1 - nmEdit(want, got) / Math.max(want.length, got.length);
  return r >= NM_TOKEN_MIN ? r : 0;
}
function nmScore(wantToks, gotToks) {
  if (!wantToks.length || !gotToks.length) return { score: 0, cover: 0 };
  const w = (t) => (t.length === 1 ? 1 : t.length);
  const pairs = [];
  wantToks.forEach((a, i) => gotToks.forEach((b, j) => {
    const s = nmTokenSim(a, b);
    if (s > 0) pairs.push([s, i, j]);
  }));
  pairs.sort((x, y) => (y[0] - x[0]) || (x[1] - y[1]) || (x[2] - y[2]));
  const usedA = new Set();
  const usedB = new Set();
  let hitA = 0;
  let hitB = 0;
  for (const [s, i, j] of pairs) {
    if (usedA.has(i) || usedB.has(j)) continue;
    usedA.add(i);
    usedB.add(j);
    hitA += s * w(wantToks[i]);
    hitB += s * w(gotToks[j]);
  }
  const sum = (ts) => ts.reduce((t, x) => t + w(x), 0);
  const cover = hitA / sum(wantToks);
  const score = 0.75 * cover + 0.25 * (hitB / sum(gotToks));
  return { score: Math.round(score * 10000) / 10000, cover: Math.round(cover * 10000) / 10000 };
}
// how well a subject (code + title) fits the form text
function nmSubjectFit(code, name, text) {
  const key = nmAlnum(code);
  const keys = nmCodeKeys(text);
  const exact = key !== '' && (keys.includes(key) || nmAlnum(text) === key);
  const fuzzy = !exact && key.length >= 4 && keys.some((k) => k.length >= 4 && nmEdit(k, key) <= 1);
  const title = nmScore(nmSubjectTokens(name), nmSubjectTokens(text));
  return { exact, fuzzy, name_score: title.score, name_cover: title.cover };
}
function nmList(v) {
  if (Array.isArray(v)) return v;
  try { const p = JSON.parse(String(v ?? '')); return Array.isArray(p) ? p : []; } catch (e) { return []; }
}
// Gemini's pick from the known instructor list, only when it is on the list and fits the raw reading
function nmKnownInstructor(known, names, raw) {
  const key = nmNameTokens(known).join(' ');
  if (!key) return '';
  const hit = nmList(names).find((n) => nmNameTokens(n).join(' ') === key);
  if (!hit) return '';
  return nmScore(nmNameTokens(hit), nmNameTokens(raw)).score >= NM_KNOWN_RAW_MIN - 1e-6 ? String(hit) : '';
}
// same for the subject: known is a subject code from the list
function nmKnownSubject(known, subjects, raw) {
  const key = nmAlnum(known);
  if (!key) return '';
  const hit = nmList(subjects).find((s) => s && nmAlnum(s.code) === key);
  if (!hit) return '';
  const fit = nmSubjectFit(hit.code, hit.name, raw);
  return fit.exact || fit.fuzzy || fit.name_score >= NM_KNOWN_RAW_MIN - 1e-6 ? String(hit.code) : '';
}
