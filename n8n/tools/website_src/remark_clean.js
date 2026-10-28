// A comment that only says "there is no comment" is NULL in the database, not text: the apps would
// show "N/A" under the instructor's feedback. Conservative: only the WHOLE value, after trimming,
// collapsing whitespace and lowercasing, may be a placeholder; a real comment that merely contains
// one ("none of the above was clear", "wala koy problema") is never touched.
// Returns the trimmed text, or null for a placeholder, a blank or lone punctuation/symbols.
const REMARK_PLACEHOLDERS = new Set(['n/a', 'na', 'n.a.', 'n.a', 'none', 'none.', 'nothing', 'no comment', 'no comments',
  'no remarks', 'wala', 'wala man', '-', '--', '.', '..', 'x']);
function cleanRemark(v) {
  if (typeof v === 'number') v = String(v);
  if (typeof v !== 'string') return null;
  const key = v.replace(/\s+/g, ' ').trim().toLowerCase().replace(/[\u2018\u2019]/g, "'");
  if (!key || REMARK_PLACEHOLDERS.has(key) || /^[\p{P}\p{S}\s]+$/u.test(key)) return null;
  return v.trim();
}
