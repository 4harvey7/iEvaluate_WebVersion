// Skip the re-check if it would outlast the backend's wait.
const VERIFY_TIMEOUT_MS = 15000;    // same as the Verify node timeout
const VERIFY_DEADLINE_MS = 340000;  // OMR 60s + Gemini 3x90s + 2x5s waits
// @@include profanity.js
// @@include remark_verify.js

// same text the reconciler reads
let remark = '';
try {
  const text = $('Website AI Validation').first(0).json?.candidates?.[0]?.content?.parts?.[0]?.text || '';
  const match = String(text).match(/\{[\s\S]*\}/);
  const parsed = match ? JSON.parse(match[0]) : null;
  const v = parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed.remarks : '';
  remark = (v === null || v === undefined || typeof v === 'object') ? '' : String(v).replace(/\s+/g, ' ').trim().slice(0, 4000);
} catch (e) {
  remark = '';
}

const item = pfRemarkCheckItem(remark);
if (item.remark_check_needed) {
  let receivedAt = NaN;
  try { receivedAt = Number($('Website Prepare Scan').first(0).json.received_at_ms); } catch (e) {}
  const elapsed = Number.isFinite(receivedAt) && receivedAt > 0 ? Date.now() - receivedAt : Infinity;
  if (elapsed + VERIFY_TIMEOUT_MS > VERIFY_DEADLINE_MS) {
    item.remark_check_needed = false;
    item.verify_skipped = 'time_budget';   // reconciler just masks the words
  }
}
return [{ json: item }];
