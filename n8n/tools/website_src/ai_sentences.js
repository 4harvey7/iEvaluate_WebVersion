// The AI's positives / improvements as separate points, stored in ONE text column (no migration):
// one clean sentence per line, joined with "\n". Accepts a string or an array (either prompt format,
// and the old run-on paragraph fed back as history). A string is split on newlines, bullets and
// semicolons only, never on commas (that would cut sentences). Capitalised, final period added,
// deduplicated, at most 5. Fewer than 3 are kept as given: never padded with invented text (the prompt asks for 3 to 5). Returns "" when nothing is left (the caller's non-empty check then fails).
// Copied into 'Parse Unified AI Result1' and 'Add Parse Code node1' (build_website_branch.py).
function aiSentences(value) {
  const parts = [];
  const add = (v) => {
    if (v === null || v === undefined) return;
    if (Array.isArray(v)) { v.forEach(add); return; }
    if (typeof v === 'object') return;
    String(v).split(/\r?\n|;|•/).forEach((p) => parts.push(p));
  };
  add(value);
  const seen = new Set();
  const out = [];
  for (const raw of parts) {
    let s = raw.replace(/^\s*(?:[-*–—]+|\d{1,2}[.)])\s+/, '').replace(/\s+/g, ' ').trim();
    if (!s || /^(?:null|undefined|n\/a|none)\.?$/i.test(s)) continue;
    s = s.charAt(0).toUpperCase() + s.slice(1);
    if (!/[.!?]$/.test(s)) s += '.';
    if (!/[\p{L}\p{N}*]/u.test(s)) continue;
    const key = s.toLowerCase().replace(/[^\p{L}\p{N}*]+/gu, ' ').trim();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(s);
    if (out.length === 5) break;
  }
  return out.join('\n');
}
