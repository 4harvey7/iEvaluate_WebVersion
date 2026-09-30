// Flags sexual words so Gemini can re-read just those spots.
// @@include profanity.js
// @@include remark_verify.js

let remark = '';
try {
  const text = $('ai validation ocr and omr').first().json?.candidates?.[0]?.content?.parts?.[0]?.text || '';
  const match = String(text).match(/\{[\s\S]*\}/);
  const parsed = match ? JSON.parse(match[0]) : null;
  const v = parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed.remarks : '';
  remark = (v === null || v === undefined || typeof v === 'object') ? '' : v.toString().trim();
} catch (e) {
  remark = '';   // no Gemini JSON, the reconciler flags it
}

return [{ json: pfRemarkCheckItem(remark) }];
