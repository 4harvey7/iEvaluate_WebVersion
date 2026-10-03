// One SAST rating (m1..m10 / p1..p10): an integer 1..5, anything else -> null.
// A blank answer is NULL in the database, never 0: the means skip NULLs and the
// table has CHECK (1..5), so a 0 would fail the insert. Handles '', ' ', 0, '0',
// 'blank', 6, 4.5, arrays/objects (e.g. Flutter's [null]); '4' and 3.0 are kept.
// Flutter and the website backend send 0 for a blank row, so 0 must give null.
function rating(v) {
  if (typeof v === 'string') v = v.trim();
  else if (typeof v !== 'number') return null;
  if (v === '') return null;
  const n = Number(v);
  return Number.isInteger(n) && n >= 1 && n <= 5 ? n : null;
}
