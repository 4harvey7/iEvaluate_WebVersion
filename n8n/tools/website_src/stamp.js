// Stamp rule (2026-10-16, two readers 2026-10-17): the numbering-machine stamp of a SAST form. One
// copy of this helper is built into each reconciler (website reconcile.js, phone phone_reconcile.js).
// Two independent readers must agree before a stamp is saved without a person:
//   Gemini  - stamp_numbers (final answer), stamp_numbers_page (full page only) and
//             stamp_reads_tiles ([{tile, number}] from the OMR's tile sheet), digits as printed,
//             '?' for a digit it cannot read;
//   Python  - the OMR's stamp_candidates block (/process, STAMP_READER): python_numbers (complete
//             reads, no '?'), python_stamp_like (candidates with >= 4 sure digits), boxes[].python_read.
// stCheckStamp -> stamp_status:
//   ok          Gemini gives exactly one number (no '?', nothing else), the full-page read and every
//               tile read are that number, Python's complete reads are exactly that number with
//               exactly one stamp-like candidate, and (optional, ST_FORMAT_CHECK) the format fits.
//               stamp_source 'gemini+python'. Only an ok stamp is saved / auto-rejected as a duplicate.
//   unconfirmed the readers disagree, Python's read is incomplete ('?') or missing, the page and tile
//               reads differ, or the stamp reader did not run (old OMR / /process failed) -> review;
//               stamp_hint = the number to check for a possible duplicate (a hint, never a reject).
//   unreadable  Gemini failed, or read only '?' / broken entries.
//   multiple    two or more different numbers (Gemini's answer, its page or tile reads, or Python).
//   missing     no stamp at all (Gemini read none and Python read no complete number).
// stReadStamps (Gemini's stamp_numbers alone) is the first step and the old one-reader rule.
const ST_STAMP_RE = /^[0-9]{3,12}$/;
const ST_MAX_ENTRIES = 20;
const ST_REASONS = { missing: 'stamp_missing', unreadable: 'stamp_unreadable', multiple: 'multiple_stamps',
  unconfirmed: 'stamp_unconfirmed' };
// Optional format check (OFF until the teacher gives the prefix/length list). A rule matches when the
// number has one of its lengths (if any) and starts with one of its prefixes (if any); a number that
// matches no rule is 'unconfirmed'. Example: [{ length: [7], prefix: ['00', '01', '12', '45'] }]
const ST_FORMAT_CHECK = false;
const ST_FORMAT_RULES = [];
// Fallback when the OMR sent no stamp reader result (old OMR, /process failed, STAMP_READER off):
// false (default) = review; true = Gemini's one number alone is accepted (stamp_source 'gemini').
const ST_ALLOW_GEMINI_ONLY = false;
function stReadStamps(raw, geminiOk) {
  // Gemini's array as received (bounded), for the reviewer
  const stampRaw = Array.isArray(raw)
    ? raw.slice(0, ST_MAX_ENTRIES).map((e) => (typeof e === 'string' ? e.slice(0, 40) : e))
    : (raw === undefined ? null : raw);
  if (!geminiOk) return { stamp_no: null, stamp_status: 'unreadable', stamp_candidates: [], stamp_raw: stampRaw };
  const list = Array.isArray(raw) ? raw.slice(0, ST_MAX_ENTRIES) : [];
  const candidates = [];
  let entries = 0;
  let unreadable = 0;
  for (const e of list) {
    const s = (e === null || e === undefined || typeof e === 'object') ? '' : String(e).trim();
    if (!s) continue;
    entries++;
    if (s.includes('?')) { unreadable++; continue; }
    const digits = s.replace(/[^0-9]/g, '');
    if (!ST_STAMP_RE.test(digits)) { unreadable++; continue; }
    if (!candidates.includes(digits)) candidates.push(digits);
  }
  let status;
  if (entries === 0) status = 'missing';
  else if (candidates.length === 1 && unreadable === 0) status = 'ok';
  else if (candidates.length >= 2 || (candidates.length === 1 && unreadable > 0)) status = 'multiple';
  else status = 'unreadable';
  return { stamp_no: status === 'ok' ? candidates[0] : null, stamp_status: status, stamp_candidates: candidates,
    stamp_raw: stampRaw };
}
// Gemini's page / tile reads: strings (or {number}); spaces and dashes dropped; any other character
// but digits and '?' makes the entry 'bad' (a letter O / l is never fixed silently).
function stReadList(values) {
  const reads = [];
  let bad = 0;
  for (const v of (Array.isArray(values) ? values.slice(0, ST_MAX_ENTRIES) : [])) {
    const x = (v && typeof v === 'object' && !Array.isArray(v)) ? v.number : v;
    const s = (x === null || x === undefined || typeof x === 'object') ? '' : String(x).replace(/[\s-]/g, '');
    if (!s) continue;
    if (!/^[0-9?]{1,20}$/.test(s)) { bad++; continue; }
    reads.push(s);
  }
  return { reads, bad, list: Array.isArray(values) };
}
// The OMR's stamp reader result. ran = the reader ran on this page (its numbers can be trusted).
function stPython(sc) {
  const py = { ran: false, numbers: [], reads: [], stamp_like: 0, version: null };
  if (!sc || typeof sc !== 'object' || Array.isArray(sc)) return py;
  if (sc.python_reader !== true || !Array.isArray(sc.python_numbers) || typeof sc.python_stamp_like !== 'number') return py;
  py.ran = true;
  py.version = sc.python_reader_version ?? null;
  py.stamp_like = sc.python_stamp_like;
  for (const n of sc.python_numbers.slice(0, ST_MAX_ENTRIES)) {
    const s = String(n ?? '');
    if (ST_STAMP_RE.test(s) && !py.numbers.includes(s)) py.numbers.push(s);
    else if (!ST_STAMP_RE.test(s)) py.ran = false;   // not what the reader sends: do not trust it
  }
  for (const b of (Array.isArray(sc.boxes) ? sc.boxes.slice(0, ST_MAX_ENTRIES) : [])) {
    const r = b && b.python_read;
    if (r && r.stamp_like === true && /^[0-9?]{3,20}$/.test(String(r.number ?? '')) && py.reads.length < 6) {
      py.reads.push(String(r.number));
    }
  }
  return py;
}
function stFormatOk(num) {
  if (!ST_FORMAT_CHECK || !ST_FORMAT_RULES.length) return true;
  return ST_FORMAT_RULES.some((r) => {
    const lens = Array.isArray(r.length) ? r.length : (r.length ? [r.length] : []);
    const pre = Array.isArray(r.prefix) ? r.prefix : (r.prefix ? [r.prefix] : []);
    return (!lens.length || lens.includes(num.length)) && (!pre.length || pre.some((p) => num.startsWith(p)));
  });
}
const stDistinct = (reads) => [...new Set(reads.filter((s) => ST_STAMP_RE.test(s)))];
// gemini: Gemini's parsed answer; geminiOk: it answered with JSON; sc: the OMR's stamp_candidates block
function stCheckStamp(gemini, geminiOk, sc) {
  const g0 = (gemini && typeof gemini === 'object') ? gemini : {};
  const g = stReadStamps(geminiOk ? g0.stamp_numbers : undefined, geminiOk);
  const page = stReadList(geminiOk ? g0.stamp_numbers_page : undefined);
  const tiles = stReadList(geminiOk ? g0.stamp_reads_tiles : undefined);
  const py = stPython(sc);
  const check = {
    version: 1,
    gemini: Array.isArray(g.stamp_raw) ? g.stamp_raw.filter((e) => typeof e === 'string').slice(0, 6) : [],
    gemini_page: page.reads.slice(0, 6),
    gemini_tiles: tiles.reads.slice(0, 6),
    tile_sheet: !!(sc && typeof sc === 'object' && typeof sc.tile_sheet_b64 === 'string' && sc.tile_sheet_b64),
    python_ran: py.ran,
    python: py.reads,                 // stamp-like reads, '?' = a digit the reader was not sure of
    python_complete: py.numbers,
    python_stamp_like: py.ran ? py.stamp_like : null,
    agreed: false,
    why: '',
  };
  const res = { ...g, stamp_source: null, stamp_hint: null, stamp_check: check };
  const pyOne = py.ran && py.numbers.length === 1 ? py.numbers[0] : null;
  const unconfirmed = (why, hint) => Object.assign(res, { stamp_no: null, stamp_status: 'unconfirmed',
    stamp_hint: hint || null, stamp_check: { ...check, why } });
  // Python is the source of truth (2026-11-06, from the user's live export): when the Python reader found
  // exactly one complete stamp that passes the format rule, use it even if the AI read something else
  // or nothing. Several stamps seen by Python still go to review.
  if (py.ran && py.numbers.length === 1 && py.stamp_like === 1 && stFormatOk(py.numbers[0])) {
    check.agreed = true;
    check.why = 'python_trusted';
    return Object.assign(res, { stamp_no: py.numbers[0], stamp_status: 'ok', stamp_candidates: [py.numbers[0]],
      stamp_hint: null, stamp_source: 'gemini+python', stamp_check: check });
  }
  if (g.stamp_status === 'missing') {
    if (pyOne) return unconfirmed('gemini_none_python_read', pyOne);
    check.why = 'none';
    return res;
  }
  if (g.stamp_status === 'unreadable') {
    res.stamp_hint = pyOne;
    check.why = 'gemini_unreadable';
    return res;
  }
  if (g.stamp_status === 'multiple') {
    check.why = 'gemini_multiple';
    return res;
  }
  const n = g.stamp_no;
  if (!py.ran) {
    if (ST_ALLOW_GEMINI_ONLY) {
      check.why = 'gemini_only_fallback';
      return Object.assign(res, { stamp_source: 'gemini' });
    }
    return unconfirmed('python_not_run', n);
  }
  const seen = [...new Set([n, ...stDistinct(page.reads), ...stDistinct(tiles.reads), ...py.numbers])];
  if (stDistinct(page.reads).length >= 2 || stDistinct(tiles.reads).length >= 2
      || (py.numbers.length >= 2 && py.stamp_like >= 2)) {
    return Object.assign(res, { stamp_no: null, stamp_status: 'multiple', stamp_candidates: seen.slice(0, 10),
      stamp_check: { ...check, why: 'readers_see_several' } });
  }
  const allAre = (r) => r.list && r.bad === 0 && r.reads.length >= 1 && r.reads.every((s) => s === n);
  if (!allAre(tiles)) return unconfirmed(check.tile_sheet ? 'tiles_differ' : 'no_tile_sheet', n);
  if (!allAre(page)) return unconfirmed('page_differs', n);
  if (!py.numbers.length) return unconfirmed('python_incomplete', n);
  if (py.numbers.length !== 1 || py.numbers[0] !== n) return unconfirmed('python_differs', n);
  if (py.stamp_like !== 1) return unconfirmed('python_stamp_like', n);
  if (!stFormatOk(n)) return unconfirmed('format', n);
  check.agreed = true;
  check.why = 'agree';
  return Object.assign(res, { stamp_source: 'gemini+python' });
}
// the review reason for a stamp result ('' when the stamp is ok)
function stReason(st) {
  return ST_REASONS[st.stamp_status] || '';
}
// what the two readers saw, for the reviewer ('' when nothing to show)
function stReads(st) {
  const c = st.stamp_check;
  if (!c) return '';
  const gem = c.gemini.length ? c.gemini.join(', ') : 'nothing';
  const gp = c.gemini_page.join(', ');
  const gt = c.gemini_tiles.join(', ');
  const extra = [gp && gp !== gem ? `full page ${gp}` : '', gt && gt !== gem ? `close-up ${gt}` : ''].filter(Boolean).join('; ');
  const py = !c.python_ran ? 'did not run (older scanner or a scanner error)'
    : (c.python.length ? c.python.join(', ') : (c.python_complete.length ? c.python_complete.join(', ') : 'nothing'));
  return `AI read: ${gem}${extra ? ` (${extra})` : ''}. Scanner read: ${py}${c.python_ran && c.python.some((s) => s.includes('?')) ? ' (? = a digit it was not sure of)' : ''}.`;
}
// the reviewer's note for a stamp result ('' when the stamp is ok)
function stNote(st) {
  const reads = stReads(st);
  const withReads = (text) => (reads ? `${text} ${reads}` : text);
  if (st.stamp_status === 'missing') {
    return 'No numbering-machine stamp was found on this page. Type the stamp number from the sheet, or approve it without one if it has none.';
  }
  if (st.stamp_status === 'unconfirmed') {
    return withReads('The stamp number was not confirmed by both readers, so it was not saved automatically. Check it on the sheet and type it.');
  }
  if (st.stamp_status === 'multiple') {
    return withReads(`More than one stamp number was read (${(st.stamp_candidates.length ? st.stamp_candidates : ['?']).join(', ')}`
      + `${Array.isArray(st.stamp_raw) && st.stamp_raw.length > st.stamp_candidates.length ? ', plus unreadable ones' : ''}). Type the right one from the sheet.`);
  }
  if (st.stamp_status === 'unreadable') {
    const seen = Array.isArray(st.stamp_raw) ? st.stamp_raw.filter((e) => typeof e === 'string' && e.trim()) : [];
    return withReads(`The stamp number could not be read${seen.length ? ` (read as ${seen.slice(0, 3).map((e) => `"${e.trim()}"`).join(', ')})` : ''}. Type it from the sheet.`);
  }
  return '';
}
