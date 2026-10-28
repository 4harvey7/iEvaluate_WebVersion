// Real word counts for the instructor word cloud, computed in code (no AI, no tokens).
// The AI themes ('Teaching clarity' ...) stay as they are; these counts are ADDED next to them:
//   remarkWords(remarks, nameTokens, isProfane, limit) -> [{ word, new_count }] (top `limit` when given)
//   mergeWordCounts(themes, words, field)       -> one list, one entry per word (case-insensitive)
// Copied into 'Parse Unified AI Result1' and 'Add Parse Code node1' (build_website_branch.py).
// The instructor's name tokens are looked up in the database and never sent to any AI.
// A word is counted once per remark. Dropped: stopwords (English, Cebuano/Bisaya, Tagalog), words
// under 3 letters, digits and punctuation, the instructor's own name tokens, profanity/masked
// words (isProfane), placeholders ('none', 'n/a', 'wala').
// A function declaration (not a const) keeps the helper copyable between Code nodes.
const RW_STOPWORDS = new Set((
  // English function words
  'a about above after again against all also am an and any are aren as at be because been before being below between ' +
  'both but by can cannot could couldn did didn do does doesn doing don down during each few for from further had hadn ' +
  'has hasn have haven having he her here hers herself him himself his how however i if in into is isn it its itself ' +
  'just let me might more most much must my myself no nor not now of off often on once one only or other ought our ' +
  'ours ourselves out over own same shall she should shouldn so some such than that the their theirs them themselves ' +
  'then there these they this those through to too under until up upon us very was wasn we were weren what when where ' +
  'which while who whom whose why will with won would wouldn you your yours yourself yourselves ' +
  'dont cant wont isnt arent wasnt werent didnt doesnt hasnt havent hadnt wouldnt couldnt shouldnt ive im ill id ' +
  'youre youll youve hes shes theyre theyll theyve weve thats whats lets theres ' +
  'really actually maybe perhaps quite rather something anything nothing everything someone anyone everyone ' +
  'yes yeah yep okay ok thing things way lot lots many get gets got getting make makes made making ' +
  'would also still even ever never always sometimes since though although yet else ' +
  'sir maam madam mam ma\'am mr mrs ms prof professor teacher instructor ' +
  // placeholders and filler
  'none nil null nan na wala walay walang wala\'y nothing comment comments suggestion suggestions remark remarks ' +
  'feedback additional others other thank thanks thankyou ' +
  // Cebuano / Bisaya
  'ang ang\'ng sa sang ni nila kini kana kanang kana kana kaayo kay ug og ug\'g nga na pa pud puro man lang ra lamang ' +
  'jud gyud gyod gihapon pod ba bitaw bitawng diay dayon unya karon kani kanang ngano unsa asa kinsa pila ' +
  'ako ko ka ikaw imo nimo niya siya sila kami namo mo atong amo among inyo ninyo namong ta tay kita mi mu nako ' +
  'nimo namo ato aton amoa amon ilang iyang iya akong imong atong ' +
  'mga si sila sina sulod gikan hangtod hangtud kung kon kun apan pero maong mao amo ' +
  'naa nay duna dili di dili\'ng wa wala ayaw palihug lagi lagi\'ng usab sad say sab kamo kayo kayo\'ng ' +
  'ingon ingnon gi gina gipa nag naga nagka nangayo ' +
  'sayo aron para pag kun ' +
  // Tagalog / Filipino
  'ng nang mga ang sa si ni kay at ay po opo ho naman talaga yung yun yon ung din rin daw raw lang lamang ' +
  'pa pang pag kasi kase dahil kung kapag pero ngunit subalit saka tapos tsaka sana siguro baka ' +
  'ako ikaw ka mo ko ako niya sila kami tayo kayo natin namin ninyo nila ito iyan iyon yan yon ayan ayon eto ' +
  'dito diyan doon doon dyan don dun ganito ganyan ganoon ganun gano ' +
  'ano sino saan kailan bakit paano ilan alin ' +
  'may mayroon meron wala hindi di huwag wag ' +
  'na nga pala ba kaya kaya\'t muna mismo lahat bawat iba ibang ' +
  'naging naka nag mag nakaka maka ma ' +
  'isa dalawa tatlo'
).split(/\s+/).map((w) => rwNormalize(w)).filter(Boolean));
function rwNormalize(text) {
  return String(text == null ? '' : text).toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '')
    .replace(/['‘’`]/g, '').replace(/[^\p{L}\s]+/gu, ' ').replace(/\s+/g, ' ').trim();
}
function rwLaughter(word) {
  return /^(?:ha|he|hi|ho|hu|ah|eh|ih|oh|uh)+h?$/.test(word) || /^(.)\1{2,}$/.test(word) || /^(?:lol|lmao|omg|wtf|xd)+$/.test(word);
}
function remarkWords(remarks, nameTokens, isProfane, limit) {
  const names = new Set();
  for (const t of Array.isArray(nameTokens) ? nameTokens : []) {
    for (const part of rwNormalize(t).split(' ')) if (part) names.add(part);
  }
  const counts = new Map();
  for (const remark of Array.isArray(remarks) ? remarks : []) {
    const seen = new Set();
    for (const w of rwNormalize(remark).split(' ')) {
      if (w.length < 3 || RW_STOPWORDS.has(w) || names.has(w) || rwLaughter(w)) continue;
      if (typeof isProfane === 'function' && isProfane(w)) continue;
      seen.add(w);
    }
    for (const w of seen) counts.set(w, (counts.get(w) || 0) + 1);
  }
  const list = Array.from(counts, ([word, new_count]) => ({ word, new_count }))
    .sort((a, b) => (b.new_count - a.new_count) || (a.word < b.word ? -1 : a.word > b.word ? 1 : 0));
  return limit > 0 ? list.slice(0, limit) : list;
}
function mergeWordCounts(themes, words, field) {
  const key = field || 'new_count';
  const merged = new Map();
  for (const item of [].concat(themes || [], words || [])) {
    const text = String(item && item.word != null ? item.word : '').trim();
    if (!text) continue;
    const k = text.toLowerCase();
    const n = Number(item[key]) || 0;
    if (merged.has(k)) merged.get(k)[key] += n;
    else merged.set(k, { word: text, [key]: n });
  }
  return Array.from(merged.values());
}
