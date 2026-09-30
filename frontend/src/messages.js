// One place that turns server codes and technical messages into plain English.
// Anything we don't recognise is shown as the server wrote it.

export const MARKING_OFF_TEXT =
  "Marking is switched off on this server. This page is saved and will be marked when marking is turned on.";

const CODE_TEXT = {
  pipeline_not_configured: MARKING_OFF_TEXT,
  pipeline_misconfigured: "Marking isn't set up correctly on the server. Ask IT to check it. This page will be tried again.",
  pipeline_timeout: "Marking took too long to answer. Trying again.",
  pipeline_unavailable: "Marking can't be reached right now. Trying again.",
  pipeline_error: "Marking had a problem with this page. Trying again.",
  pipeline_bad_response: "Marking gave an unexpected answer. Trying again.",
  pipeline_not_saved: "Marking couldn't save this page. Trying again.",
  internal_error_not_saved: "Marking couldn't save this page. Trying again.",
  pipeline_rejected_request: "This page couldn't be read. Please scan the sheet again.",
  not_saved_permanent: "This page couldn't be read. Please scan the sheet again.",
  invalid_request: "This page couldn't be read. Please scan the sheet again.",
  failed_after_retries: "Gave up after several tries. Please scan this sheet again.",
  file_predates_batch: "Scanned before this batch started",
  duplicate: "This sheet was already scanned",
  database_unreachable: "The server lost its connection for a moment. Trying again.",
  claim_failed: "The server lost its connection for a moment. Trying again.",
  in_progress: "This page is being worked on.",
  no_open_batch: "No batch was open when this page arrived.",
  invalid_image: "This file isn't a readable picture. Please scan the sheet again.",
  unsupported_image: "This file type can't be used. Save scans as JPEG or PNG.",
  image_too_large: "This file is too big. Scan at a lower resolution.",
};

// Technical messages the server stores on a page, matched to a code above.
const MESSAGE_PATTERNS = [
  [/scoring pipeline (is )?not configured/i, "pipeline_not_configured"],
  [/rejected the server's credentials|misconfigured/i, "pipeline_misconfigured"],
  [/timed out/i, "pipeline_timeout"],
  [/pipeline is unavailable/i, "pipeline_unavailable"],
  [/internal error; page not saved|could not save this page/i, "pipeline_not_saved"],
  [/unexpected response/i, "pipeline_bad_response"],
  [/pipeline returned (an error|http)/i, "pipeline_error"],
  [/rejected by pipeline|invalid request/i, "pipeline_rejected_request"],
  [/created before the current batch started/i, "file_predates_batch"],
  // Not plain "database": flagged notes say "in the database" (a lookup, not an outage).
  [/database (error|unreachable|unavailable)|(can't|cannot|could not) reach the database|connection (was )?(lost|refused|reset)|could not connect/i, "database_unreachable"],
];

export function friendlyCode(code) {
  if (!code) return null;
  return CODE_TEXT[String(code).trim().toLowerCase()] || null;
}

// The code behind a technical message, or null if we don't recognise it.
export function codeFromMessage(message) {
  if (!message) return null;
  const m = String(message);
  const hit = MESSAGE_PATTERNS.find(([re]) => re.test(m));
  return hit ? hit[1] : null;
}

export function friendlyMessage(message) {
  if (!message) return "";
  const m = String(message).trim();
  if (/^simulated result/i.test(m)) return "Practice result – not really marked.";
  const failedAfter = /^failed after (\d+) attempts?/i.exec(m);
  if (failedAfter) return `Marking failed after ${failedAfter[1]} tries. Please scan this sheet again.`;
  return friendlyCode(codeFromMessage(m)) || friendlyCode(m) || m;
}

// Review reasons are short codes such as "low_confidence". Known ones get a
// sentence; others are shown as words ("low confidence").
const REASON_TEXT = {
  ...CODE_TEXT,
  identity_mismatch: "This sheet may be for a different instructor or subject.",
  instructor_mismatch: "The instructor on the sheet may not match this batch.",
  subject_mismatch: "The subject on the sheet may not match this batch.",
  low_confidence: "Some answers were hard to read.",
  multiple_marks: "Some questions have more than one answer marked.",
  blank_answers: "Some answers were left blank.",
  orientation_uncertain: "This page may be sideways or upside down.",
  // Instructor / subject written on the sheet vs the batch (the review page shows the suggestion).
  instructor_mismatch_found: "The instructor written on the sheet looks like a different instructor.",
  instructor_mismatch_not_found: "The instructor written on the sheet doesn't match this batch or anyone else we know.",
  instructor_mismatch_ambiguous: "The instructor written on the sheet could be more than one person.",
  subject_mismatch_found: "The subject written on the sheet looks like a different subject.",
  subject_mismatch_not_found: "The subject written on the sheet doesn't match this batch or any subject we know.",
  subject_mismatch_ambiguous: "The subject written on the sheet could be more than one subject.",
  form_match_failed: "The instructor and subject on the sheet couldn't be checked, so this batch's are used. Please check them.",
  duplicate_student_id: "Another form from this student is already saved for this instructor and subject.",
  batch_reference_invalid: "This batch's instructor, subject or term is no longer valid.",
  batch_check_failed: "The batch check couldn't run for this page. Please check it.",
  internal_error_decide: "Automatic checking stopped part-way for this page. Please check every field.",
  internal_error_reconcile: "Automatic checking stopped part-way for this page. Please check every field.",
  grid_not_detected: "The answer table wasn't found on the page. Please check every rating.",
  omr_unavailable: "The answer reader gave no ratings, so the AI's reading is shown. Please check every rating.",
  omr_fallback_grid: "The answer grid had to be estimated. Please check every rating.",
  gemini_unavailable: "The AI double-check didn't run, so the student ID and remarks weren't read.",
  ai_validation_failed: "The AI double-check didn't run, so the student ID and remarks weren't read.",
  poor_scan_quality: "The scan looks blurry or faint.",
  spot_check: "Randomly picked for a quick check. Nothing looked wrong — confirm the details shown and approve.",
};

// Reasons that carry a number, e.g. "omr_agreement_below_80".
const REASON_PATTERNS = [
  [/^omr_agreement_below_(\d+)$/, (n) => `The answer reader and the AI agree on fewer than ${n}% of the answers.`],
  [/^low_confidence_ocr_(\d+)_rows$/, (n) => `The answer reader was unsure on ${n} answer${n === "1" ? "" : "s"}.`],
  [/^too_many_blanks_(\d+)_of_20$/, (n) => `${n} of the 20 answers are blank.`],
  [/^python_weak_gemini_blank_(\d+)_rows$/, (n) => `${n} faint mark${n === "1" ? "" : "s"} may be specks, not answers.`],
  [/^python_weak_disagreement_(\d+)_rows$/, (n) => `The answer reader and the AI read ${n} unclear answer${n === "1" ? "" : "s"} differently.`],
  [/^not_a_sast_form\b/, () => "This doesn't look like an evaluation form."],
];

export function friendlyReason(reason) {
  const r = String(reason ?? "").trim();
  if (!r) return "";
  const lower = r.toLowerCase();
  if (REASON_TEXT[lower]) return REASON_TEXT[lower];
  for (const [re, text] of REASON_PATTERNS) {
    const m = re.exec(lower);
    if (m) return text(m[1]);
  }
  return (friendlyCode(lower) || r).replace(/_/g, " ");
}

// n8n's message on a flagged/rejected page: "Flagged for manual review (codes). <note>".
// Returns just the note; the codes are shown through friendlyReason.
export function outcomeNote(message) {
  const m = String(message ?? "").trim();
  return m.replace(/^flagged for manual review \([^)]*\)\.?\s*/i, "").replace(/^rejected, nothing was saved:\s*/i, "").trim();
}

// ---------------------------------------------------------------- review page
// What the check of the sheet's instructor / subject found (partial_data.*_match).
const MATCH_TEXT = {
  instructor: {
    same: "Matches this batch",
    found: "Looks like a different instructor",
    not_found: "Doesn't match anyone we know",
    ambiguous: "Could be more than one person",
    unreadable: "Couldn't be read",
    unchecked: "Not checked",
  },
  subject: {
    same: "Matches this batch",
    found: "Looks like a different subject",
    not_found: "Doesn't match any subject we know",
    ambiguous: "Could be more than one subject",
    unreadable: "Couldn't be read",
    unchecked: "Not checked",
  },
};

export function matchText(kind, value) {
  const t = MATCH_TEXT[kind] || MATCH_TEXT.instructor;
  return t[value] || t.unchecked;
}

const IMPORT_ERROR_TEXT = {
  instructor_not_found: "Instructor not found",
  subject_not_found: "Subject not found",
  instructor_and_subject_not_found: "Instructor and subject not found",
};

export function importErrorText(type) {
  const t = String(type || "").toLowerCase();
  return IMPORT_ERROR_TEXT[t] || (t ? t.replace(/_/g, " ") : "Couldn't be saved");
}

// Review action errors (routers/reviews.py codes). Anything else: the server's own message.
const REVIEW_ERROR_TEXT = {
  stale_row:
    "Someone changed this form while you were editing it (for example on the phone app). Your edits are kept. Check the new version, then try again.",
  already_resolved: "This form was already finished somewhere else, so nothing was changed.",
  in_progress: "This form is being sent right now. Wait a moment, it will update by itself.",
  migration_required: "Saving needs a database update first. Ask IT to apply migration 005.",
  correction_not_configured: "Sending corrections isn't set up on this server yet. Ask IT to set it up.",
  invalid_ratings: "Each rating must be 1 to 5, or left blank.",
  not_your_batch: "Only the person who started this batch, the person logged in at its laptop, or an SAO admin can do this.",
};

export function reviewErrorText(err) {
  if (!err) return "";
  return REVIEW_ERROR_TEXT[err.code] || err.message || "Something went wrong. Please try again.";
}

export const REVIEW_MISMATCH_REASONS = [
  "instructor_mismatch_found",
  "instructor_mismatch_not_found",
  "instructor_mismatch_ambiguous",
  "subject_mismatch_found",
  "subject_mismatch_not_found",
  "subject_mismatch_ambiguous",
];

// Rotation the server applied before marking (degrees clockwise).
export function rotationText(deg) {
  const d = ((Number(deg) || 0) % 360 + 360) % 360;
  if (!d) return "";
  if (d === 180) return "Page was upside down – fixed automatically";
  return "Page was sideways – fixed automatically";
}

// The code that best describes a page's problem (explicit fields first,
// then its reasons, then its message).
export function itemCode(item) {
  if (!item) return null;
  const explicit = item.error_code || item.reason_code || item.code;
  if (explicit) return String(explicit).toLowerCase();
  const reasons = (item.review_reasons || []).map((r) => String(r).toLowerCase());
  const known = reasons.find((r) => CODE_TEXT[r]);
  if (known) return known;
  // Only error pages carry a transport/pipeline message; flagged notes are not errors.
  if (item.status && item.status !== "error") return null;
  return codeFromMessage(item.message);
}
