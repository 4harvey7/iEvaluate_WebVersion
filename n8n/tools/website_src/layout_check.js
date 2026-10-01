// Layout safety net for both reconcilers (website + phone). The OMR reads ratings by POSITION
// (column 1 = O ... column 5 = US, rows 1-20), so a form printed with another layout (columns in
// another order, an extra or missing column, extra rows) would be read confidently into the wrong
// ratings. These checks compare the evidence with the expected profile: 5 columns O, VS, S, F, US
// left to right, 20 rows (10 + 10). Every input is optional: an older Python OMR without `layout`
// or a Gemini answer without column_order never raises a flag.
const LC_EXPECTED_COLUMNS = 5;
const LC_EXPECTED_ROWS = 20;
const LC_EXPECTED_ORDER = ['O', 'VS', 'S', 'F', 'US'];
// Thresholds measured on 89 correctly read forms (42 phone, 22 long, 16 short scans, plus repeats)
// vs 17 scans of a form whose rating block is printed wider/elsewhere (2026-10-01 set):
// structure fit: ruling evidence per fitted row boundary (0..1); good >= 0.645, other layout <= 0.33
const LC_MIN_SCORE_PER_BOUNDARY = 0.50;
// share of the grid's row boundaries with a real ruling under them; good 22/22, other layout <= 16/22
const LC_MIN_ROW_SUPPORT = 0.85;
// column boundaries of the grid used that may miss their ruling (one faint line); good 6/6, other <= 2/6
const LC_MAX_COLUMN_MISSES = 1;
// fixed fallback grid: the rows may be shifted, so it also needs this much exact row agreement
const LC_FALLBACK_AGREEMENT_MIN = 0.80;
const LC_HEADER_WORDS = { OUTSTANDING: 'O', VERYSATISFACTORY: 'VS', SATISFACTORY: 'S', FAIR: 'F',
  UNSATISFACTORY: 'US', 5: 'O', 4: 'VS', 3: 'S', 2: 'F', 1: 'US' };
function lcHeader(v) {
  const s = String(v ?? '').toUpperCase().replace(/[^A-Z0-9]/g, '');
  return LC_HEADER_WORDS[s] || s;
}
// Gemini's column_order (array, or a comma/space separated string) -> header codes; null if absent
function lcColumnOrder(g) {
  const raw = g && g.column_order;
  const list = Array.isArray(raw) ? raw : (typeof raw === 'string' ? raw.split(/[,;|/]+|\s+/) : null);
  if (!list) return null;
  const out = list.map(lcHeader).filter(Boolean);
  return out.length ? out : null;
}
const lcInt = (v) => (typeof v === 'number' && Number.isInteger(v) ? v : null);
// Signals from the evidence available BEFORE the rows are compared: Python's layout object and
// Gemini's column_order. Each signal is a short code; any signal means layout_mismatch.
function lcLayoutSignals(py, gemini, geminiOk) {
  const signals = [];
  const lay = py && py.layout && typeof py.layout === 'object' && !Array.isArray(py.layout) && !py.layout.error
    ? py.layout : null;
  if (lay) {
    const cols = lcInt(lay.columns_detected);
    if (cols !== null && cols !== LC_EXPECTED_COLUMNS) signals.push(`columns_detected_${cols}`);
    const matched = lcInt(lay.column_boundaries_matched);
    const colTotal = lcInt(lay.column_boundaries_total);
    if (matched !== null && colTotal && matched < colTotal - LC_MAX_COLUMN_MISSES) signals.push('columns_misaligned');
    const rows = lcInt(lay.rows_detected);
    if (rows !== null && rows !== LC_EXPECTED_ROWS) signals.push(`rows_detected_${rows}`);
    const supported = lcInt(lay.boundaries_supported);
    const rowTotal = lcInt(lay.boundaries_total);
    if (supported !== null && rowTotal && supported < LC_MIN_ROW_SUPPORT * rowTotal) signals.push('row_rulings_missing');
    const sf = lay.structure_fit && typeof lay.structure_fit === 'object' ? lay.structure_fit : null;
    const spb = sf ? Number(sf.score_per_boundary) : NaN;
    if (sf && sf.score_per_boundary !== null && Number.isFinite(spb) && spb < LC_MIN_SCORE_PER_BOUNDARY) {
      signals.push('structure_fit_poor');
    }
  }
  const sast = !(gemini && gemini.document_check && gemini.document_check.is_sast_form === false);
  const order = geminiOk && sast ? lcColumnOrder(gemini) : null;
  if (order && order.join(',') !== LC_EXPECTED_ORDER.join(',')) signals.push(`column_order_${order.join('_')}`);
  return signals;
}
// Fixed fallback grid + low exact agreement with Gemini: the fallback rows are probably shifted.
function lcFallbackSignal(py, agreementKnown, agreeRows, totalRows) {
  return py && py.grid_source === 'fallback' && agreementKnown
    && agreeRows < Math.ceil(LC_FALLBACK_AGREEMENT_MIN * totalRows - 1e-9);
}
function lcNote(signals, order) {
  const parts = signals.map((s) => {
    if (s.startsWith('columns_detected_')) return `the scanner found ${s.slice(17)} rating columns instead of ${LC_EXPECTED_COLUMNS}`;
    if (s === 'columns_misaligned') return 'the rating columns were not where the scanner looked for them';
    if (s === 'row_rulings_missing') return 'many row lines were not where the scanner placed the rows';
    if (s.startsWith('rows_detected_')) return `the scanner found ${s.slice(14)} rating rows instead of ${LC_EXPECTED_ROWS}`;
    if (s === 'structure_fit_poor') return 'the rating grid did not fit the expected 10 + 10 rows well';
    if (s === 'fallback_grid_low_agreement') return 'a fixed fallback grid was used and its reading disagrees with the AI';
    if (s.startsWith('column_order_')) return `the AI read the column headers as ${(order || []).join(', ')} instead of ${LC_EXPECTED_ORDER.join(', ')}`;
    return s;
  });
  return `The form's layout does not match the expected SAST form (${LC_EXPECTED_COLUMNS} rating columns `
    + `${LC_EXPECTED_ORDER.join(', ')} left to right, ${LC_EXPECTED_ROWS} rows): ${parts.join('; ')}. `
    + 'The scanner reads ratings by position, so they may be in the wrong rows or columns. Verify every rating against the image.';
}
