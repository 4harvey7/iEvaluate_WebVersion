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
  not_saved_needs_review:
    "Marking couldn't save this page after several tries. It is waiting on the review page: try again from there, or type in the answers. No need to scan it again.",
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
  [/^not saved by marking; waiting on the review page/i, "not_saved_needs_review"],
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
  // Blank or unreadable on the sheet: this batch's values are pre-selected; check them and approve.
  instructor_missing: "The instructor on the sheet is blank or couldn't be read. Check it; this batch's instructor is pre-selected.",
  subject_missing: "The subject on the sheet is blank or couldn't be read. Check it; this batch's subject is pre-selected.",
  student_id_missing: "The student ID on the sheet is blank or couldn't be read. Type it in if it's on the sheet.",
  reused_image:
    "This exact picture was already scanned in another batch. Check it isn't the same form counted twice before approving.",
  // Numbering-machine stamp on the sheet (one stamp = one paper, counted once per term).
  duplicate_stamp: "Already scanned: a form with this stamp number is already saved for this term, so this one is not counted.",
  stamp_missing: "No stamp number was found on the sheet. Type it in if there is one, or choose \"Form has no stamp\".",
  stamp_unreadable: "The stamp number couldn't be read. Type it in from the sheet, or choose \"Form has no stamp\".",
  multiple_stamps: "More than one stamp number was found on the sheet. Pick the right one or type it in.",
  stamp_unconfirmed: "The two stamp readers (the AI and the scanner) did not agree on the stamp number. Check it on the sheet and type it in.",
  stamp_check_failed: "The stamp check couldn't run; check the stamp number before crediting.",
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
  not_saved_by_pipeline:
    "Marking couldn't save this page, so nothing was read into the form. Type the student ID, remarks and every rating from the scan.",
};

// "Couldn't be saved" pages on the review page (scan_unsaved_pages.status / outcome).
const UNSAVED_OUTCOME_TEXT = {
  success: "Saved and scored",
  flagged: "Saved - now under Needs review",
  rejected: "Marking says this isn't an evaluation form",
  duplicate: "Already saved earlier",
  refused: "Marking refused this page",
};

export function unsavedStateText(row) {
  if (!row) return "";
  if (row.status === "sending") return "Sending again...";
  if (row.status === "retrying") return "The laptop is still trying";
  if (row.status === "manual") return "Moved to Needs review";
  if (row.status === "saved") return UNSAVED_OUTCOME_TEXT[row.outcome] || "Saved";
  return "Couldn't be saved";
}

// Reasons that carry a number, e.g. "omr_agreement_below_80".
const REASON_PATTERNS = [
  [/^omr_agreement_below_(\d+)$/, (n) => `The answer reader and the AI agree on fewer than ${n}% of the answers.`],
  [/^low_confidence_ocr_(\d+)_rows$/, (n) => `The answer reader was unsure on ${n} answer${n === "1" ? "" : "s"}.`],
  [/^too_many_blanks_(\d+)_of_20$/, (n) => `${n} of the 20 answers are blank.`],
  [/^too_few_answers_(\d+)_of_20$/, (n) => `Fewer than 5 of the 20 items were answered (only ${n}), so this form can't be counted.`],
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
  not_your_batch: "Only the person who started this batch, or an SAO admin, can do this.",
  invalid_stamp: "The stamp number must be 3 to 12 digits (numbers only), exactly as printed on the sheet.",
  stamp_required: "Type the stamp number from the sheet, or choose \"Form has no stamp\".",
  note_required: "Write why this form should be counted after all.",
  not_auto_duplicate: "This form isn't waiting as \"already scanned\" any more, so nothing was changed.",
  stamp_migration_required: "Stamp numbers need a database update first. Ask IT to apply migration 015.",
  admin_only: "Only an SAO admin can do this.",
};

// The saved form a stamp number clashes with (409 duplicate_stamp / an auto-rejected form).
export function duplicateOfText(dup) {
  if (!dup) return "";
  const parts = [];
  if (dup.submitted_date) parts.push(`saved ${dup.submitted_date}`);
  if (dup.instructor_name) parts.push(`for ${dup.instructor_name}${dup.subject_code ? ` · ${dup.subject_code}` : ""}`);
  else if (dup.subject_code) parts.push(`for ${dup.subject_code}`);
  if (!dup.from_website && dup.task_id) parts.push("from the phone app");
  return parts.join(", ");
}

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

// SAST Guidelines screen. Same text as the mobile app.
export const SAST_GUIDE = {
  title: "SAST Guidelines",
  subtitle: "Read this every semester. Following it keeps SAST results accurate and saves review work.",
  footer: "Questions? Contact the SAO admin.",
  servicePlaceholder: "(ask the SAO admin for the service account email)",
  sections: [
    {
      title: "1. Preparing the SAST forms (stamp)",
      dos: [
        "Stamp every form with the numbering machine BEFORE handing it out. One stamp per form.",
        "Stamp upright, in the same spot on every form (top margin, away from the table and the student ID).",
        "Use dark, fresh ink so every digit is sharp and complete.",
        "Check the stamp: all digits visible, nothing smudged or cut off.",
      ],
      donts: [
        "Don't stamp a form twice or stamp over the rating table, the header or the student ID.",
        "Don't photocopy stamped forms or reuse a stamp number. A stamp number already saved this semester is rejected automatically as a duplicate.",
        "Don't let a worn or dry stamp pad print faint or broken digits; replace the ink first.",
      ],
    },
    {
      title: "2. Filling in the form (for students; read it to them)",
      dos: [
        "Use a black or blue ballpen.",
        "Write the instructor's full name, the subject code and title, and your student ID clearly in the header.",
        "Mark each answer with a check (✓) or a slash (/) inside the box of the column you choose.",
        "Make the mark about the size of the box: not a tiny dot, and not so long that it runs into the next box or row.",
        "Give exactly one mark per row and answer all 20 items. A form with fewer than 5 answers is not counted.",
        "Write comments only in the Remarks box. Leave it blank if you have none.",
      ],
      donts: [
        "Don't use an X, shading, a circle or a dot. These are NOT counted.",
        "Don't put two marks in one row.",
        "Don't cross out or scribble over an answer; ask for a new form instead.",
        "Don't use pencil (light pencil can be missed), and don't write over the stamp or the table lines.",
        "Don't fold, staple or tear the form through the table.",
      ],
    },
    {
      title: "3. Scanning with the phone or the scanner",
      dos: [
        "Lay the form flat on a plain, darker surface so the paper edges stand out.",
        "Use even light with no shadows or glare. Turn off the flash if it makes a shiny spot.",
        "Fit the whole page inside the frame, hold the phone straight above the page and keep it still.",
        "Pick the right paper size (Short or Long).",
        "Check the preview: the stamp, the header and all 20 rows must be readable.",
      ],
      donts: [
        "Don't scan tilted, curled or folded pages, or with fingers or objects on the paper.",
        "Don't scan the same form twice.",
        "Don't crop off the stamp or the edges of the table.",
      ],
    },
    {
      title: "4. Reviewing flagged scans",
      dos: [
        "Open the scan image and compare every flagged item with the paper.",
        "Type the stamp number exactly as printed when it was not read, including leading zeros.",
        "Fill in answers the scan missed; discard forms that break the rules above.",
      ],
      donts: [
        "Don't approve a form you have not checked against the image.",
        "Don't type a stamp number you cannot read on the paper; check the physical form first.",
      ],
    },
    {
      title: "5. Importing a Google Sheet",
      dos: [
        "Name the tab exactly: SAST Survey Data",
        "Put the column headers in the first row. New layout: ID Number, Name of the Instructor/Professor, Course Handled by the Instructor, items \"1. …\" to \"20. …\", Comments. Old layout: Student ID, Instructor/Professor, Subject Taught, M1–M10, P1–P10, Remarks & Suggestions, Date.",
        "Items 1–10 are Management and 11–20 are Performance, like the paper form.",
        "Enter ratings as numbers 1 to 5 (5 = Outstanding, 4 = Very Satisfactory, 3 = Satisfactory, 2 = Fair, 1 = Unsatisfactory).",
        "Write the instructor as \"Family, First M.\" or \"First Family\", the same name as in iEvaluate.",
        "Write the course as \"CODE TITLE\", for example: MOBAPP 330 MOBILE APPLICATION DEVELOPMENT",
        "Use one row per student evaluation.",
        "Share the sheet with the iEvaluate service account as Viewer: {SERVICE_ACCOUNT_EMAIL}",
        "Paste the full link (https://docs.google.com/spreadsheets/d/…) and wait until the app says the import finished. It can take several minutes; unmatched rows go to Import Errors for review.",
      ],
      donts: [
        "Don't rename the tab, merge cells, or add title rows, total rows or empty rows between the data.",
        "Don't type words (like \"Outstanding\") instead of numbers in the rating columns.",
        "Don't leave the Comments empty with spaces or dashes; leave it truly blank. \"N/A\" is fine (it is saved as empty).",
        "Don't import the same sheet twice.",
        "If the app says Google denied access, the sheet was not shared with the service account.",
      ],
    },
  ],
};
