// Second Gemini look at flagged words so misreads aren't masked.
const PF_VERIFY_MAX_WORDS = 10;   // extra flagged words just get masked
const PF_VERIFY_GENERATION_CONFIG = {
  temperature: 0,
  maxOutputTokens: 2048,
  responseMimeType: 'application/json',
  responseSchema: {
    type: 'OBJECT',
    properties: {
      words: {
        type: 'ARRAY',
        items: {
          type: 'OBJECT',
          properties: {
            index: { type: 'INTEGER' },
            word: { type: 'STRING' },
            really_written: { type: 'BOOLEAN' },
            correct_reading: { type: 'STRING' },
          },
          required: ['index', 'word', 'really_written', 'correct_reading'],
        },
      },
    },
    required: ['words'],
  },
};

// e.g. "... [1: iyot] ..."
function pfMarkedRemark(text, spans) {
  let out = '';
  let pos = 0;
  spans.forEach((s, i) => {
    out += text.slice(pos, s.start) + '[' + (i + 1) + ': ' + s.text + ']';
    pos = s.end;
  });
  return out + text.slice(pos);
}

function pfVerifyPrompt(text, spans) {
  return [
    'You are double-checking the handwritten remarks on this scanned CTU Argao SAST instructor evaluation form.',
    'An automatic reading of the handwritten remarks/suggestions says:',
    '"' + pfMarkedRemark(text, spans) + '"',
    'The numbered words in [brackets] were flagged as possibly sexual or vulgar. Handwriting is often misread, '
      + 'so look again very carefully at exactly those spots in the remarks area of the form, letter by letter.',
    'For each numbered word return: index (its number); word (the flagged word exactly as given above); '
      + 'really_written (true only if the student clearly wrote that word or an obvious spelling of it, false if '
      + 'the handwriting actually shows a different word); correct_reading (the word exactly as it is written on '
      + 'the form; the same word when really_written is true).',
    'Judge only what is written on the paper. Do not censor, translate or explain. Return ONLY valid JSON matching the schema.',
  ].join('\n');
}

function pfRemarkCheckItem(text) {
  const spans = findProfanity(text).slice(0, PF_VERIFY_MAX_WORDS);
  return {
    _stage: 'remark_check',
    remark_check_needed: spans.length > 0,
    candidates: findProfanity(text).length,
    words: spans.map((s, i) => ({ index: i + 1, word: s.text })),
    verify_prompt: spans.length ? pfVerifyPrompt(text, spans) : null,
    verify_generation_config: spans.length ? PF_VERIFY_GENERATION_CONFIG : null,
  };
}

const pfKey = (s) => pfNorm(s).replace(/\s+/g, '');

// null on error, timeout or bad JSON
function pfParseVerification(resp) {
  try {
    const text = resp && resp.candidates && resp.candidates[0] && resp.candidates[0].content
      && resp.candidates[0].content.parts && resp.candidates[0].content.parts[0]
      && resp.candidates[0].content.parts[0].text;
    if (typeof text !== 'string') return null;
    const m = text.match(/\{[\s\S]*\}/);
    if (!m) return null;
    const j = JSON.parse(m[0]);
    return j && Array.isArray(j.words) ? j.words.filter((w) => w && typeof w === 'object') : null;
  } catch (e) {
    return null;
  }
}

function pfFindResult(results, index, word) {
  const key = pfKey(word);
  const byIndex = results.find((r) => Number(r.index) === index);
  if (byIndex && (typeof byIndex.word !== 'string' || pfKey(byIndex.word) === key)) return byIndex;
  return results.find((r) => typeof r.word === 'string' && pfKey(r.word) === key) || null;
}

// null means keep the word masked
function pfCleanReading(value, original) {
  if (typeof value !== 'string') return null;
  const s = value.replace(/\s+/g, ' ').trim();
  if (!s || s.length > Math.max(24, original.length * 3)) return null;
  if (pfKey(s) === pfKey(original)) return null;   // same word, so mask it
  return s;
}

// Fail safe: any word the re-check didn't clear stays masked.
function pfResolveRemark(raw, verifyNode) {
  const text = typeof raw === 'string' ? raw : String(raw ?? '');
  const spans = findProfanity(text);
  const check = { candidates: spans.length, verification: 'not_needed', confirmed: 0, corrected: 0, unverified: 0 };
  if (!spans.length) return { text, masked: false, check };
  let resp;
  let ran = false;
  try {
    const it = $(verifyNode).first();
    resp = it ? it.json : undefined;
    ran = true;
  } catch (e) {
    ran = false;   // verify node didn't run
  }
  const results = ran ? pfParseVerification(resp) : null;
  check.verification = !ran ? 'not_run' : (results ? 'ok' : 'failed');
  let out = '';
  let pos = 0;
  spans.forEach((s, i) => {
    out += text.slice(pos, s.start);
    const r = results && i < PF_VERIFY_MAX_WORDS ? pfFindResult(results, i + 1, s.text) : null;
    const reading = r && r.really_written === false ? pfCleanReading(r.correct_reading, s.text) : null;
    if (reading !== null) {
      out += reading;
      check.corrected++;
    } else {
      out += pfMaskSpan(s.text);
      if (r && r.really_written === true) check.confirmed++;
      else check.unverified++;
    }
    pos = s.end;
  });
  out += text.slice(pos);
  const final = maskProfanity(out);   // the corrected reading could be sexual too
  return { text: final, masked: check.confirmed + check.unverified > 0 || final !== out, check };
}
