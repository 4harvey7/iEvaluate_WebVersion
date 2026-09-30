// Masks sexual words only; insults and curses are allowed.
const PROFANITY_GROUPS = {
  sexual: true,
  fuck: true,            // masked even when used as a plain curse
  sexual_curses: false,  // puta, tangina etc. stay allowed like other curses
};

// [group, affix mode, spellings]; 'none' matches the exact word only
const PROFANITY_WORDS = [
  // Bisaya / Cebuano
  ['sexual', 'fil', 'iyot', 'iyut'],
  ['sexual', 'fil', 'bilat'],
  ['sexual', 'fil', 'oten', 'otin', 'uten', 'utin'],
  ['sexual', 'fil', 'salsal'],
  ['sexual', 'none', 'puday'],
  ['sexual', 'none', 'tamod', 'tamud'],
  ['sexual', 'none', 'bayag'],
  // Tagalog
  ['sexual', 'fil', 'kantot', 'kantut'],
  ['sexual', 'fil', 'hindot', 'hindut'],
  ['sexual', 'fil', 'jakol', 'jakul', 'dyakol'],
  ['sexual', 'fil', 'tsupa', 'chupa'],
  ['sexual', 'none', 'burat', 'titi', 'tite', 'etits'],             // exact only, titi+ng = katiting
  ['sexual', 'none', 'puke', 'puki', 'pekpek', 'kepyas', 'kipay'],
  ['sexual', 'none', 'betlog'],
  // English
  ['sexual', 'none', 'pussy', 'pussies', 'cunt', 'cunts'],
  ['sexual', 'none', 'boobs', 'boobies', 'tits', 'titties'],
  ['sexual', 'none', 'porn', 'porno', 'blowjob', 'blowjobs', 'handjob', 'handjobs'],
  ['sexual', 'none', 'horny', 'dildo', 'dildos', 'jizz'],
  ['fuck', 'en', 'fuck', 'fuk', 'fck', 'phuck'],
  ['fuck', 'none', 'fak', 'pakyu', 'pakshet', 'pakshit'],           // exact only, fak+ing = faking
  ['sexual_curses', 'none', 'puta', 'pota', 'putangina', 'potangina', 'putanginamo', 'tangina', 'tanginamo'],
  ['sexual_curses', 'none', 'whore', 'whores', 'slut', 'sluts'],
];

// Two-word forms like "pak yu"; both words get masked.
const PROFANITY_PHRASES = [
  ['fuck', 'pak', 'yu'], ['fuck', 'pak', 'you'],
  ['sexual_curses', 'putang', 'ina'], ['sexual_curses', 'potang', 'ina'], ['sexual_curses', 'tang', 'ina'],
];

// Left out on purpose: libog, clinical words, words with normal meanings.

const PF_CLASS = { a: 'a4@*', e: 'e3*', i: 'i1!|*', o: 'o0*', u: 'uv*', s: 's5$', t: 't7', l: 'l1|', b: 'b8' };
const PF_FIL_PREFIXES = ['gi', 'gina', 'gipa', 'ga', 'na', 'nag', 'naga', 'nagka', 'naka', 'nakaka', 'nang', 'nan',
  'ni', 'ma', 'mag', 'maga', 'magka', 'maka', 'mang', 'man', 'mi', 'pa', 'pag', 'pang', 'paka', 'ka', 'kina',
  'i', 'ipa', 'in', 'um', 'nakig', 'makig', 'pakig'];
const PF_FIL_SUFFIXES = ['an', 'han', 'on', 'hon', 'a', 'ha', 'ay', 'in', 'hin', 'ero', 'era', 'eros', 'eras'];
const PF_EN_PREFIXES = ['mother', 'motha', 'mutha', 'muther', 'mada'];
const PF_EN_SUFFIXES = ['s', 'ing', 'in', 'ed', 'er', 'ers', 'face', 'head'];
const PF_WS = ' \\t\\n\\r\\f\\v\\u00A0\\u1680\\u2000-\\u200A\\u2028\\u2029\\u202F\\u205F\\u3000\\uFEFF';
const PF_WORDCH = 'A-Za-z0-9\\u00C0-\\u024F\\u1E00-\\u1EFF@$';
const PF_DELIM_RE = new RegExp('([' + PF_WS + ',;:/\\\\()\\[\\]{}"\\u201C\\u201D\\u00AB\\u00BB<>?]+)');
const PF_LEAD_RE = new RegExp('^[^' + PF_WORDCH + ']*');
const PF_TRAIL_RE = new RegExp('[^' + PF_WORDCH + ']*$');
const PF_ONLY_WS_RE = new RegExp('^[' + PF_WS + ']+$');
const PF_NON_WS_RE = new RegExp('[^' + PF_WS + ']', 'gu');
const PF_SEP_RE = /[.\-_'‘’`]/;
const PF_PART_RE = /[^.\-_'‘’`]+/g;
const PF_MAX_TOKEN = 64;
const PF_MAX_RUN = 20;

function pfLetters(word) {
  return Array.from(word).map((ch) => (PF_CLASS[ch] ? '[' + PF_CLASS[ch] + ']' : ch) + '+').join('');
}
function pfAlt(list) {
  return '(?:' + list.map(pfLetters).join('|') + ')';
}
function pfFilRoot(word) {
  if (/^[aeiou]/.test(word)) return pfLetters(word);
  const withInfix = (w) => pfLetters(w[0]) + '(?:in|um)?' + pfLetters(w.slice(1));
  const syl = (word.match(/^[^aeiou]+[aeiou]+/) || [word[0]])[0];
  return '(?:' + withInfix(syl) + ')?' + withInfix(word);
}
function pfPatterns(groups) {
  const roots = { fil: [], en: [], none: [] };
  for (const [group, affix, ...words] of PROFANITY_WORDS) {
    if (groups[group]) for (const w of words) roots[affix].push(affix === 'fil' ? pfFilRoot(w) : pfLetters(w));
  }
  const alts = [];
  if (roots.fil.length) alts.push(pfAlt(PF_FIL_PREFIXES) + '?(?:' + roots.fil.join('|') + ')' + pfAlt(PF_FIL_SUFFIXES) + '?');
  if (roots.en.length) alts.push(pfAlt(PF_EN_PREFIXES) + '?(?:' + roots.en.join('|') + ')' + pfAlt(PF_EN_SUFFIXES) + '?');
  if (roots.none.length) alts.push('(?:' + roots.none.join('|') + ')');
  return {
    token: alts.length ? '^(?:' + alts.join('|') + ')$' : '^(?!)$',
    phrases: PROFANITY_PHRASES.filter(([g]) => groups[g]).map(([, a, b]) => ['^' + pfLetters(a) + '$', '^' + pfLetters(b) + '$']),
  };
}
const PF_PATTERNS = pfPatterns(PROFANITY_GROUPS);
const PF_TOKEN_RE = new RegExp(PF_PATTERNS.token);
const PF_PHRASE_RES = PF_PATTERNS.phrases.map(([a, b]) => [new RegExp(a), new RegExp(b)]);

// lowercase, no accents, no separators
function pfNorm(s) {
  return String(s).normalize('NFD').toLowerCase().replace(/[̀-ͯ.\-_'‘’`]/g, '');
}
function pfIsWord(core) {
  return core.length <= PF_MAX_TOKEN && PF_TOKEN_RE.test(pfNorm(core));
}
function pfMaskSpan(s) {
  return String(s).replace(PF_NON_WS_RE, '*');
}

// Returns sorted, non-overlapping spans of sexual words.
function findProfanity(text) {
  if (typeof text !== 'string' || !text) return [];
  const pieces = text.split(PF_DELIM_RE);   // even = word chunk, odd = delimiters
  const chunks = [];
  let pos = 0;
  for (let i = 0; i < pieces.length; i++) {
    const p = pieces[i];
    if (i % 2 === 0 && p) {
      const lead = PF_LEAD_RE.exec(p)[0].length;
      const core = p.slice(lead, p.length - PF_TRAIL_RE.exec(p.slice(lead))[0].length);
      chunks.push({ start: pos + lead, core, after: pieces[i + 1] || '', masked: false });
    }
    pos += p.length;
  }
  const spans = [];
  const add = (start, end) => spans.push({ start, end });
  // whole words, else each part split by separators
  for (const c of chunks) {
    if (!c.core) continue;
    if (pfIsWord(c.core)) { add(c.start, c.start + c.core.length); c.masked = true; continue; }
    if (!PF_SEP_RE.test(c.core)) continue;
    PF_PART_RE.lastIndex = 0;
    let m;
    while ((m = PF_PART_RE.exec(c.core))) {
      if (pfIsWord(m[0])) { add(c.start + m.index, c.start + m.index + m[0].length); c.masked = true; }
    }
  }
  // spaced-out letters like "i y o t", longest first
  const single = (c) => !c.masked && Array.from(c.core).length === 1;
  for (let k = 0; k < chunks.length;) {
    if (!single(chunks[k])) { k++; continue; }
    let e = k;
    while (e + 1 < chunks.length && single(chunks[e + 1]) && PF_ONLY_WS_RE.test(chunks[e].after)) e++;
    for (let s = k; s <= e;) {
      let len = Math.min(e - s + 1, PF_MAX_RUN);
      for (; len >= 3; len--) {
        if (pfIsWord(chunks.slice(s, s + len).map((c) => c.core).join(''))) break;
      }
      if (len >= 3) {
        const last = chunks[s + len - 1];
        add(chunks[s].start, last.start + last.core.length);
        for (let j = s; j < s + len; j++) chunks[j].masked = true;
        s += len;
      } else {
        s++;
      }
    }
    k = e + 1;
  }
  // two-word forms like "pak yu"
  for (let k = 0; k + 1 < chunks.length; k++) {
    const a = chunks[k];
    const b = chunks[k + 1];
    if (a.masked || b.masked || !a.core || !b.core || !PF_ONLY_WS_RE.test(a.after)) continue;
    const na = pfNorm(a.core);
    const nb = pfNorm(b.core);
    if (PF_PHRASE_RES.some(([ra, rb]) => ra.test(na) && rb.test(nb))) {
      add(a.start, b.start + b.core.length);
      a.masked = true;
      b.masked = true;
      k++;
    }
  }
  spans.sort((x, y) => x.start - y.start);
  return spans.map((s) => ({ start: s.start, end: s.end, text: text.slice(s.start, s.end) }));
}

// Same-length asterisks; non-strings pass through unchanged.
function maskProfanity(text) {
  if (typeof text !== 'string' || !text) return text;
  const spans = findProfanity(text);
  if (!spans.length) return text;
  let out = '';
  let pos = 0;
  for (const s of spans) {
    out += text.slice(pos, s.start) + pfMaskSpan(s.text);
    pos = s.end;
  }
  return out + text.slice(pos);
}

// true if a keyword has a sexual or masked word
function profaneKeyword(word) {
  return typeof word === 'string' && (word.includes('*') || maskProfanity(word) !== word);
}
