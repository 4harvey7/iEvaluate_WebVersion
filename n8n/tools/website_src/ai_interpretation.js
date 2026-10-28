// The AI's student_interpretation ("What Your Students Are Saying") as ONE clean paragraph:
// whitespace and newlines collapsed to single spaces, list markers dropped, capped at about 1500
// characters. A longer text is cut at the last sentence end (. ! ?) before the cap; with no sentence
// end in the second half of the cap, at the last space, with a period added. Accepts a string or an
// array of strings (joined with spaces). Returns "" when nothing is left (the unified parser then
// throws "Empty interpretation."; the individual parser keeps the stored interpretation instead).
// Copied into 'Parse Unified AI Result1' and 'Add Parse Code node1' (build_website_branch.py).
const AI_INTERPRETATION_MAX = 1500;
function aiInterpretation(value, max = AI_INTERPRETATION_MAX) {
  const parts = [];
  const add = (v) => {
    if (v === null || v === undefined) return;
    if (Array.isArray(v)) { v.forEach(add); return; }
    if (typeof v === 'object') return;
    String(v).split(/\r?\n/).forEach((p) => parts.push(p.replace(/^\s*(?:[-*•–—]+|\d{1,2}[.)])\s+/, '')));
  };
  add(value);
  let s = parts.join(' ').replace(/\s+/g, ' ').trim();
  if (!s || /^(?:null|undefined|n\/a|none)\.?$/i.test(s) || !/[\p{L}\p{N}]/u.test(s)) return '';
  if (s.length <= max) return s;
  const head = s.slice(0, max + 1);
  let cut = -1;
  const re = /[.!?]["')\]]*(?=\s|$)/g;
  let m;
  while ((m = re.exec(head)) !== null) {
    if (m.index + m[0].length <= max) cut = m.index + m[0].length;
  }
  if (cut >= max / 2) return s.slice(0, cut).trim();
  const space = s.lastIndexOf(' ', max);
  s = s.slice(0, space > 0 ? space : max).replace(/[\s,;:–—-]+$/, '');
  return /[.!?]$/.test(s) ? s : `${s}.`;
}
