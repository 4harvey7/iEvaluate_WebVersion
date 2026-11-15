// Stamp rule (2026-10-16, two readers 2026-10-17): the numbering-machine stamp of a SAST form. One
// copy of this helper is built into each reconciler (website reconcile.js, phone phone_reconcile.js).
// Two independent readers must agree before a stamp is saved without a person:
//   Gemini  - stamp_numbers (final answer), stamp_numbers_page (full page only) and
//             stamp_reads_tiles ([{tile, number}] from the OMR's tile sheet), digits as printed,
//             '?' for a digit it cannot read;
//   Python  - the OMR's stamp_candidates block (/process, STAMP_READER): python_numbers (complete
//             reads, no '?'), python_stamp_like (candidates with >= 4 sure digits), boxes[].python_read.
// stCheckStamp -> stamp_status, from the Python reader ONLY (2026-11-10; the AI's stamp reads are unreliable
// and are kept for the reviewer to see, never used):
//   ok          Python read exactly one complete number with exactly one stamp-like candidate and
//               (optional, ST_FORMAT_CHECK) the format fits. stamp_source 'gemini+python' (DB value for
//               "read automatically"). Only an ok stamp is saved / auto-rejected as a duplicate.
//   unconfirmed the reader did not run (old OMR / /process failed), one complete number but other
//               stamp-like marks too, or a format miss -> review; stamp_hint = Python's number (a hint).
//   unreadable  Python saw a stamp but no complete number ('?' digits).
//   multiple    Python read two or more complete numbers.
//   missing     Python saw no stamp at all.
const ST_STAMP_RE = /^[0-9]{3,12}$/;
const ST_MAX_ENTRIES = 20;
const ST_REASONS = { missing: 'stamp_missing', unreadable: 'stamp_unreadable', multiple: 'multiple_stamps',
  unconfirmed: 'stamp_unconfirmed' };
// Optional format check (OFF until the teacher gives the prefix/length list). A rule matches when the
// number has one of its lengths (if any) and starts with one of its prefixes (if any); a number that
// matches no rule is 'unconfirmed'. Example: [{ length: [7], prefix: ['00', '01', '12', '45'] }]
const ST_FORMAT_CHECK = false;
const ST_FORMAT_RULES = [];
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
  // Python is the ONLY source of the stamp number (2026-11-10): the AI's reads (stamp_raw, stamp_check.gemini*)
  // are kept for the reviewer to look at but never become stamp_no, a candidate or a duplicate hint.
  // stamp_source stays 'gemini+python' (the DB CHECK value for "read automatically", migration 015).
  const res = { stamp_no: null, stamp_status: 'missing', stamp_candidates: py.numbers.slice(0, 10),
    stamp_raw: g.stamp_raw, stamp_source: null, stamp_hint: null, stamp_check: check };
  const pyOne = py.ran && py.numbers.length === 1 ? py.numbers[0] : null;
  const unconfirmed = (why, hint) => Object.assign(res, { stamp_status: 'unconfirmed',
    stamp_hint: hint || null, stamp_check: { ...check, why } });
  if (!py.ran) return unconfirmed('python_not_run', null);
  if (pyOne && py.stamp_like === 1) {
    if (!stFormatOk(pyOne)) return unconfirmed('format', pyOne);
    check.agreed = true;
    check.why = 'python_trusted';
    return Object.assign(res, { stamp_no: pyOne, stamp_status: 'ok', stamp_source: 'gemini+python', stamp_check: check });
  }
  if (py.numbers.length >= 2) return Object.assign(res, { stamp_status: 'multiple', stamp_check: { ...check, why: 'python_several' } });
  if (pyOne) return unconfirmed('python_stamp_like', pyOne);   // one complete read plus other stamp-like marks
  if (py.reads.length || py.stamp_like > 0) return Object.assign(res, { stamp_status: 'unreadable', stamp_check: { ...check, why: 'python_incomplete' } });
  check.why = 'none';
  return res;
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
    return withReads('The scanner did not read one clear stamp number, so it was not saved automatically. Check it on the sheet and type it.');
  }
  if (st.stamp_status === 'multiple') {
    return withReads(`The scanner read more than one stamp number (${(st.stamp_candidates.length ? st.stamp_candidates : ['?']).join(', ')}). Type the right one from the sheet.`);
  }
  if (st.stamp_status === 'unreadable') {
    return withReads('The scanner could not read the whole stamp number. Type it from the sheet.');
  }
  return '';
}
