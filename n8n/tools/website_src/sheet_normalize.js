// Google Sheet import: turns every row of the "SAST Survey Data" tab into the one shape the rest of
// the import reads ('Student ID', 'Instructor/Professor', 'Subject Taught', M1..M10, P1..P10,
// 'Remarks & Suggestions', 'Date'). Columns are matched by NORMALISED HEADER, never by position, so
// both layouts work:
//   old: Student ID | Instructor/Professor | Subject Taught | M1..M10 | P1..P10 | Remarks & Suggestions | Date
//   new: ID Number | Section | Name of the Instructor/Professor (...) | <combined> | Course Handled by ... |
//        "1. gives ..." .. "20. delivers ..." | Comments
// New items 1..10 are Management (M1..M10), 11..20 are Performance (P1..P10), like the paper form.
// New 15-item forms (import request form_type 'SET-15' student / 'SUP-15' supervisor): the tab holds
//   Instructor | Subject | Q1..Q15 (or 1..15, Item 1..Item 15) | Comments | Stamp No (| Supervisor, SUP-15 only)
// Values 5..1, an empty cell = blank. They come out as 'Instrument', 'Answers' ({"1":5,...}), 'Stamp No' and
// 'Supervisor' (e-mail if there is a column for it, else the name). A student id column is NEVER read for
// these forms in 'Student ID' (that stays blank). For SET-15 only, the id goes into 'Details'.student_id, which
// the database keeps for the SAO office only (duplicate check). 'Details' (SET-15 / SUP-15 only) also carries
// section, program_level, program_year, submitted_date (ISO, from 'Date'), and for SUP-15 academic_rank and
// designation. The answers are checked by save_eval_response. Any other unknown column is ignored. The
// combined "<instructor> - <COURSE CODE> <COURSE TITLE>" column is only a fallback when the
// instructor or course column is empty. Names stay inside n8n (database lookups); no AI gets them.
// 'Sheet Row' = the row number in Google Sheets (n8n's row_number, else header row 1 + position), for messages.
// form_type 'SET' / 'SUP' are aliases of 'SET-15' / 'SUP-15'. Before anything is saved, the headers must fit the
// chosen form, else the import stops with a clear message (Sheet Import Error Response, nothing saved):
//   SET-15 / SUP-15 chosen, headers with items 16..20 or M1..P10 -> "looks like the old SAST form"
//   SET-15 chosen, a Supervisor Email column                      -> "looks like the supervisor form"
//   old SAST chosen, numbered items 1..15 only                    -> "looks like the SET form" (SUP: Supervisor Email)
// @@include remark_clean.js
function normHeader(h) {
  return String(h === undefined || h === null ? '' : h)
    .replace(/[‘’‚‛′`´]/g, "'")
    .replace(/[“”„‟″]/g, '"')
    .replace(/[–—]/g, '-')
    .replace(/\s+/g, ' ')
    .trim()
    .toLowerCase();
}
function cellText(v) {
  if (v === undefined || v === null || typeof v === 'boolean') return '';
  return String(v).replace(/\s+/g, ' ').trim();
}
function classifyHeader(n, newForm) {
  if (!n) return null;
  if (newForm) {
    if (n.indexOf('designation') >= 0) return { k: 'designation' };
    if (n.indexOf('academic rank') >= 0 || n === 'rank') return { k: 'rank' };
    if (n.startsWith('program level') || n === 'level') return { k: 'program_level' };
    if (n.startsWith('program year') || n === 'year level' || n === 'year') return { k: 'program_year' };
    if (n === 'stamp' || n === 'stamp no' || n === 'stamp no.' || n === 'stamp number' || n === 'stamp #') return { k: 'stamp' };
    if (/^(supervisor|evaluator|department head|dept head|name of the supervisor)\b/.test(n)) {
      return { k: n.indexOf('email') >= 0 || n.indexOf('e-mail') >= 0 ? 'supervisor_email' : 'supervisor' };
    }
    const q = n.match(/^(?:q|item|question|no\.?|#)?\s*#?\s*(\d{1,2})(?:\s*[.):-].*)?$/);
    if (q) return +q[1] >= 1 && +q[1] <= 15 ? { k: 'item', n: +q[1] } : null;
  }
  if (n.startsWith('id number') || n === 'student id' || n === 'student_id' || n === 'student number' || n === 'id no' || n === 'id no.') return { k: 'id' };
  if (n.startsWith('section')) return { k: 'section' };
  if (n.startsWith('name of the instructor') || n === 'instructor/professor' || n === 'instructor' || n === 'instructor professor') return { k: 'instructor' };
  if (n.startsWith('course handled') || n === 'subject taught' || n === 'subject' || n === 'course') return { k: 'course' };
  if (n.startsWith('comment') || n === 'remarks & suggestions' || n === 'remarks') return { k: 'remarks' };
  if (n === 'date') return { k: 'date' };
  let m = n.match(/^(\d{1,2})\s*[.)]/);
  if (m && +m[1] >= 1 && +m[1] <= 20) return { k: 'item', n: +m[1] };
  m = n.match(/^([mp])\s*(\d{1,2})$/);
  if (m && +m[2] >= 1 && +m[2] <= 10) return { k: 'item', n: (m[1] === 'm' ? 0 : 10) + +m[2] };
  return null;
}
// "Family, First M." -> "First Family"; "Juan Dela Cruz" stays as is
function tidyInstructor(s) {
  let t = cellText(s);
  const c = t.indexOf(',');
  if (c > 0) {
    const family = t.slice(0, c).trim();
    const first = t.slice(c + 1).trim().replace(/(\s+[A-Za-z]\.?)+$/, '').trim();
    if (family && first) t = first + ' ' + family;
  }
  return t;
}
// "MOBAPP 330 MOBILE APPLICATION DEVELOPMENT" -> "MOBILE APPLICATION DEVELOPMENT (MOBAPP 330)"
// (the subject lookup reads a code in the last parentheses, else the whole text as a name)
function tidyCourse(s) {
  const t = cellText(s);
  if (!t || /\([^()]*\)\s*$/.test(t)) return t;
  const w = t.split(' ');
  let n = 0;
  if (/\d/.test(w[0]) && /[A-Za-z]/.test(w[0])) n = 1;
  else if (w.length > 1 && /^\d+[A-Za-z]?$/.test(w[1]) && /[A-Za-z]/.test(w[0])) n = 2;
  if (!n) return t;
  const code = w.slice(0, n).join(' ');
  const title = w.slice(n).join(' ').replace(/^[-:]\s*/, '').trim();
  return title ? title + ' (' + code + ')' : code;
}
// a sheet date (Excel serial, Date, 'YYYY-MM-DD', 'M/D/YYYY', 'Oct 30, 2026') -> 'YYYY-MM-DD', or '' when unreadable
function isoDate(v) {
  const pad = (x) => String(x).padStart(2, '0');
  const ok = (y, m, d) => {
    const dt = new Date(Date.UTC(y, m - 1, d));
    return y >= 1990 && y <= 2100 && dt.getUTCMonth() === m - 1 && dt.getUTCDate() === d ? y + '-' + pad(m) + '-' + pad(d) : '';
  };
  if (v instanceof Date) return isNaN(v.getTime()) ? '' : v.toISOString().slice(0, 10);
  if (typeof v === 'number') {
    if (!(v > 20000 && v < 80000)) return '';
    return new Date(Math.round((v - 25569) * 86400000)).toISOString().slice(0, 10);
  }
  const t = cellText(v);
  if (!t) return '';
  if (/^\d{5}$/.test(t)) return isoDate(+t);
  let m = t.match(/^(\d{4})-(\d{1,2})-(\d{1,2})(?:[T\s].*)?$/);
  if (m) return ok(+m[1], +m[2], +m[3]);
  m = t.match(/^(\d{1,2})\/(\d{1,2})\/(\d{2}|\d{4})$/);   // Google Sheets: month/day/year
  if (m) return ok(+m[3] < 100 ? 2000 + +m[3] : +m[3], +m[1], +m[2]);
  const ms = Date.parse(t + ' UTC');
  return isNaN(ms) ? '' : new Date(ms).toISOString().slice(0, 10);
}
function normalizeSheetRow(row, instrument, index) {
  const newForm = instrument === 'SET-15' || instrument === 'SUP-15';
  const out = {};
  const seen = {};
  let combined = '';
  for (const key of Object.keys(row)) {
    const v = row[key];
    const c = classifyHeader(normHeader(key), newForm);
    if (!c) {
      const t = cellText(v);
      if (!combined && t.indexOf(' - ') > 0) combined = t;   // the unnamed "<instructor> - <course>" column
      continue;
    }
    const slot = c.k === 'item' ? 'i' + c.n : c.k;
    if (c.k === 'item') {
      const blank = v === undefined || v === null || (typeof v === 'string' && v.trim() === '');
      if (seen[slot] !== undefined && (blank || seen[slot] !== null)) continue;
      seen[slot] = blank ? null : (typeof v === 'string' ? v.trim() : v);
    } else if (c.k === 'date') {
      if (seen.date === undefined || cellText(seen.date) === '') seen.date = v;
    } else if (cellText(seen[slot]) === '') {
      seen[slot] = cellText(v);
    }
  }
  let instructor = cellText(seen.instructor);
  let course = cellText(seen.course);
  if ((!instructor || !course) && combined) {
    const at = combined.indexOf(' - ');
    if (!instructor) instructor = combined.slice(0, at).trim();
    if (!course) course = combined.slice(at + 3).trim();
  }
  out['Student ID'] = newForm ? '' : cellText(seen.id);
  out['Instructor/Professor'] = tidyInstructor(instructor);
  out['Subject Taught'] = tidyCourse(course);
  out['Remarks & Suggestions'] = cleanRemark(seen.remarks) || '';   // 'N/A' etc. -> blank
  if (seen.date !== undefined) out['Date'] = seen.date;
  if (newForm) {
    const answers = {};
    for (let j = 1; j <= 15; j++) answers[String(j)] = seen['i' + j] !== undefined ? seen['i' + j] : null;
    out['Instrument'] = instrument;
    out['Answers'] = answers;
    out['Stamp No'] = cellText(seen.stamp);
    out['Supervisor'] = cellText(seen.supervisor_email) || cellText(seen.supervisor);
    const sup = instrument === 'SUP-15';
    out['Details'] = {
      student_id: sup ? '' : cellText(seen.id),   // SET-15 only; SAO office only, never shown to instructors
      section: cellText(seen.section),
      program_level: cellText(seen.program_level),
      program_year: cellText(seen.program_year),
      submitted_date: isoDate(seen.date),
      academic_rank: sup ? cellText(seen.rank) : '',
      designation: sup ? cellText(seen.designation) : '',
    };
  }
  for (let j = 1; j <= 10; j++) {
    out['M' + j] = !newForm && seen['i' + j] !== undefined ? seen['i' + j] : null;
    out['P' + j] = !newForm && seen['i' + (10 + j)] !== undefined ? seen['i' + (10 + j)] : null;
  }
  const rn = Number(row.row_number);
  out['Sheet Row'] = Number.isInteger(rn) && rn >= 2 ? rn : index + 2;
  return out;
}

// the form the import request named: 'SET-15' / 'SUP-15' (also 'SET' / 'SUP'); anything else = the old SAST sheet
function formTypeOf(v) {
  const t = String(v === undefined || v === null ? '' : v).trim().toUpperCase();
  if (t === 'SET-15' || t === 'SET') return 'SET-15';
  if (t === 'SUP-15' || t === 'SUP') return 'SUP-15';
  return 'SAST-20';
}
// the item number of a header ("Q16", "16", "Item 16", "16. delivers ...") or null
function headerItemNo(n) {
  const q = n.match(/^(?:q|item|question|no\.?|#)?\s*#?\s*(\d{1,2})(?:\s*[.):-].*)?$/);
  return q ? +q[1] : null;
}
const OLD_SAST_FORM = 'This sheet looks like the old SAST form (20 items) – choose Old SAST form.';
const SET_FORM = 'This sheet looks like the SET form (15 items) – choose SET.';
const SUP_FORM = 'This sheet looks like the supervisor form (SUP, 15 items) – choose SUP.';
// '' when the headers fit the chosen form, else the message for the app
function formTypeProblem(headers, instrument) {
  const names = headers.map(normHeader).filter(Boolean);
  const items = names.map(headerItemNo).filter((x) => x !== null && x >= 1 && x <= 20);
  const oldColumns = names.some((n) => /^[mp]\s*(\d{1,2})$/.test(n)) || items.some((x) => x >= 16);
  const supEmail = names.some((n) => { const c = classifyHeader(n, true); return c && c.k === 'supervisor_email'; });
  if (instrument === 'SET-15' || instrument === 'SUP-15') {
    if (oldColumns) return OLD_SAST_FORM;
    if (instrument === 'SET-15' && supEmail) return SUP_FORM;
    return '';
  }
  if (!oldColumns && items.length > 0) return supEmail ? SUP_FORM : SET_FORM;
  return '';
}

// which form the import request named ('clean data1' passes it on); anything else = the old SAST sheet
let sheetInstrument = 'SAST-20';
try {
  sheetInstrument = formTypeOf(($('clean data1').first().json || {}).instrument);
} catch (e) {
  sheetInstrument = 'SAST-20';
}
const sheetRows = $input.all();
// zero rows: 'Get row(s) in sheet1' emits one empty item (alwaysOutputData); answer with a clear error
if (!sheetRows.some((it) => it.json && Object.keys(it.json).length > 0)) {
  throw new Error('The sheet has no data rows');
}
// wrong form chosen: stop before anything is looked up or saved
const sheetHeaders = [...new Set(sheetRows.flatMap((it) => Object.keys(it.json || {})))].filter((h) => h !== 'row_number');
const formProblem = formTypeProblem(sheetHeaders, sheetInstrument);
if (formProblem) throw new Error(formProblem);
return sheetRows.map((item, i) => ({ json: normalizeSheetRow(item.json || {}, sheetInstrument, i), pairedItem: { item: i } }));
