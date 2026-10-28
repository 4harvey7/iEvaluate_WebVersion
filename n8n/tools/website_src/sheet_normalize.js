// Google Sheet import: turns every row of the "SAST Survey Data" tab into the one shape the rest of
// the import reads ('Student ID', 'Instructor/Professor', 'Subject Taught', M1..M10, P1..P10,
// 'Remarks & Suggestions', 'Date'). Columns are matched by NORMALISED HEADER, never by position, so
// both layouts work:
//   old: Student ID | Instructor/Professor | Subject Taught | M1..M10 | P1..P10 | Remarks & Suggestions | Date
//   new: ID Number | Section | Name of the Instructor/Professor (...) | <combined> | Course Handled by ... |
//        "1. gives ..." .. "20. delivers ..." | Comments
// New items 1..10 are Management (M1..M10), 11..20 are Performance (P1..P10), like the paper form.
// Section and any other unknown column are ignored (the raw table has no place for them). The
// combined "<instructor> - <COURSE CODE> <COURSE TITLE>" column is only a fallback when the
// instructor or course column is empty. Names stay inside n8n (database lookups); no AI gets them.
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
function classifyHeader(n) {
  if (!n) return null;
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
function normalizeSheetRow(row) {
  const out = {};
  const seen = {};
  let combined = '';
  for (const key of Object.keys(row)) {
    const v = row[key];
    const c = classifyHeader(normHeader(key));
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
  out['Student ID'] = cellText(seen.id);
  out['Instructor/Professor'] = tidyInstructor(instructor);
  out['Subject Taught'] = tidyCourse(course);
  out['Remarks & Suggestions'] = cleanRemark(seen.remarks) || '';   // 'N/A' etc. -> blank
  if (seen.date !== undefined) out['Date'] = seen.date;
  for (let j = 1; j <= 10; j++) {
    out['M' + j] = seen['i' + j] !== undefined ? seen['i' + j] : null;
    out['P' + j] = seen['i' + (10 + j)] !== undefined ? seen['i' + (10 + j)] : null;
  }
  return out;
}

const sheetRows = $input.all();
// zero rows: 'Get row(s) in sheet1' emits one empty item (alwaysOutputData); answer with a clear error
if (!sheetRows.some((it) => it.json && Object.keys(it.json).length > 0)) {
  throw new Error('The sheet has no data rows');
}
return sheetRows.map((item, i) => ({ json: normalizeSheetRow(item.json || {}), pairedItem: { item: i } }));
