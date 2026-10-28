// A remark tone from Gemini as one of the three stored values (database and app use exactly these):
// Positive, Critical, Neutral. Case-insensitive and trimmed; "negative" and "mixed" (praise plus a
// problem) count as Critical. Anything else returns null: the unified parser then throws (as before),
// the single-scan parser falls back to Neutral (as before).
// Copied into 'Parse Unified AI Result1' and 'Add Parse Code node1' (build_website_branch.py).
const AI_TONES = { positive: 'Positive', critical: 'Critical', neutral: 'Neutral', negative: 'Critical', mixed: 'Critical' };
function aiTone(value) {
  if (typeof value !== 'string') return null;
  const key = value.trim().toLowerCase();
  return Object.prototype.hasOwnProperty.call(AI_TONES, key) ? AI_TONES[key] : null;
}
