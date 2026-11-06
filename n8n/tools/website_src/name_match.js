// Token fuzzy matching for instructor names and subjects (Website Decide, phone reconciler).
// Each database token is paired with its best form token (edit distance, initials, prefixes);
// the score is the weighted share of matched tokens (0.75 database side + 0.25 form side).
const NM_TOKEN_MIN = 0.55;       // weaker token pairs count as no match
const NM_KNOWN_RAW_MIN = 0.30;   // Gemini's pick from the subject list needs this much support from the raw reading
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
// Filipino sound-alike spelling, both sides folded the same way: e/i, o/u, c/k/q, ph/f, v/b, z/s, y/i,
// double letters (Licayan / Licayen, Kristoffer / Cristofer).
function nmSoundAlike(tok) {
  return tok.replace(/ph/g, 'f').replace(/[cq]/g, 'k').replace(/v/g, 'b').replace(/z/g, 's')
    .replace(/e/g, 'i').replace(/o/g, 'u').replace(/y/g, 'i').replace(/(.)\1+/g, '$1');
}
// Phone reconciler: the database instructor the raw reading names, matched locally (Gemini never
// sees an instructor list). found = one entry scores >= minScore and beats every other by margin;
// ambiguous = several entries are that close (also two people with the same name); none = no entry
// reaches minScore. Only 'found' returns a name.
// raw: one reading or several (best score over all of them). names: strings, or { name, subjects }
// (subject codes linked to that person this term). opts (phone only): soundAlike also scores the
// sound-alike spelling; subjects = codes read on the form: a linked entry gets +bonus for the ranking
// only, never for minScore, and an entry that is not linked still competes (never a filter).
function nmBestInstructor(raw, names, minScore, margin, opts = {}) {
  const reads = (Array.isArray(raw) ? raw : [raw]).map(nmNameTokens).filter((t) => t.length);
  const keys = new Set((opts.subjects || []).map(nmAlnum).filter(Boolean));
  const nameScore = (name) => {
    const want = nmNameTokens(name);
    const wantS = want.map(nmSoundAlike);
    let best = 0;
    for (const got of reads) {
      best = Math.max(best, nmScore(want, got).score);
      // sound-alike only replaces a form token that sounds exactly like a database token (no looser fuzzy match)
      const same = got.map((t) => { const i = wantS.indexOf(nmSoundAlike(t)); return i >= 0 ? want[i] : t; });
      if (opts.soundAlike) best = Math.max(best, nmScore(want, same).score);
    }
    return best;
  };
  const scored = !reads.length ? [] : nmList(names)
    .map((n) => (n && typeof n === 'object' ? n : { name: n }))
    .map((n) => ({ name: String(n.name ?? '').trim(), subjects: nmList(n.subjects) }))
    .filter((c) => c.name)
    .map((c) => {
      const linked = keys.size > 0 && c.subjects.some((x) => keys.has(nmAlnum(x)));
      const score = nameScore(c.name);
      return { name: c.name, score, linked, rank: score + (linked ? (opts.bonus || 0) : 0) };
    })
    .filter((c) => c.score >= minScore - 1e-6)
    .sort((a, b) => (b.rank - a.rank) || a.name.localeCompare(b.name));
  if (!scored.length) return { match: 'none', name: '', score: null, contenders: [] };
  const close = scored.filter((c) => scored[0].rank - c.rank < margin - 1e-6);
  if (close.length > 1) return { match: 'ambiguous', name: '', score: scored[0].score, contenders: close.map((c) => c.name) };
  return { match: 'found', name: scored[0].name, score: scored[0].score, contenders: [], linked: scored[0].linked };
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
