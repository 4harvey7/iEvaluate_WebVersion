#!/usr/bin/env python3
"""Build n8n/perfect-2.1.json from the original n8n export.

Re-runnable:  python build_website_branch.py [INPUT_JSON] [OUTPUT_JSON] [LAYOUT_JSON]
Live export:  python build_website_branch.py --live LIVE_EXPORT_JSON [OUTPUT_JSON]
              applies only steps 6 to 10 below (apply_sast_v2, apply_phone_name_privacy,
              apply_check_only_rule, apply_min_answers_rule, apply_stamp_rule) to a
              live n8n export of this build (e.g. 'perfect 2.1.json' -> ../perfect-2.1.json);
              ids/positions/name/id kept.

Defaults:
  INPUT  = C:\\Users\\rodzh\\Downloads\\perfect 101 (4).json   (read only, never modified)
  OUTPUT = ../perfect-2.1.json (next to this tools/ folder)
  LAYOUT = C:\\Users\\rodzh\\Downloads\\perfect 101 (5).json   (optional, read only): the LIVE
           export. Only the canvas positions of same-named nodes are taken from it, so a
           re-import keeps the layout the user arranged by hand. INPUT stays the patch base:
           the live export already contains this build (the draft nodes and every
           replace_once anchor are gone), so it cannot be patched again.

What it does
  1. Replaces the draft "SAST from Website" branch (webhook sast-batch-scan) with
     a front end that only does what is website specific: validation, duplicate
     check, OCR/OMR (same as phone), not-a-form reject, reconcile, batch integrity check,
     the form's instructor/subject vs the database ('Website Form Match') and the
     clean/flagged decision. Code/SQL live in ./website_src/. A page is credited
     automatically to the batch instructor/subject only when the form agrees with them (a
     blank/unreadable instructor, subject or student id, or an image already marked in another
     batch, is held for review: *_missing / reused_image); a form naming another instructor/subject is
     flagged with the matched one pre-filled as the suggestion, and nothing is credited until
     staff approve it (decide.js). The batch wins unless another instructor/subject clearly beats
     it (token fuzzy scoring in name_match.js); a form that matches nobody is not flagged. No
     Gemini call ever gets instructor names (privacy): both main prompts get the known SUBJECTS
     only ('Phone Known Names', known_names.sql / 'Website Known Names', website_known_names.sql)
     and return known_subject. The form's instructor is matched locally against the database
     (website: 'Website Form Match' + decide.js; phone: 'Phone Instructor Candidates',
     phone_instructor_candidates.sql, after the Gemini call + nmBestInstructor in the phone
     reconciler). OMR ratings come from Python, cross-checked by Gemini (row
     agreement >= AGREEMENT_MIN, see reconcile.js); all text comes only from Gemini.
  2. Clean pages: 'Website Raw Upsert' stores the raw row (same row/key as the shared
     upsert, never over another page's row), 'Website Respond Saved' answers the website
     webhook, and only then 'Website Map to Target Format' feeds the EXISTING 'If19'
     (the entry 'Map to Target Format3' uses). 'Code in JavaScript' marks the row as
     stored and 'Raw Row Already Stored?' skips the shared upsert for it; aggregates, overall totals, question means, If28 alerts and the remark
     AI path exist only once. No Respond node is downstream of If19, so the lastNode
     webhook 'Google LINK from phone1' never sees a Respond node.
  3. Flagged pages reuse the phone flagged path: 'Website Flagged Record' ->
     'Save scan error image' -> 'build flagged record1' ->
     'save flagged to failed_scan_queue1' -> 'Respond to Webhook'.
  4. Shared-node edits (minimal, backward compatible):
       * 'Code in JavaScript': staff id from the website webhook; task_id mapped
         into the raw row (only when present); blank/whitespace student id -> NULL,
         and NULL-student rows are never collapsed by the in-execution dedupe.
       * 'build flagged record1': reads 'Website Flagged Record' first (website
         executions only); adds batch_id and the website partial_data.
       * 'Respond to Webhook': website executions get the backend contract object;
         phone executions get exactly the same text as before.
       * Aggregate fix: the aggregates start from the "done" output of the raw
         upsert loop ('Loop Over Items3' -> 'Raw Upsert Done'; see 6. for the chain
         after it). Every upsert result goes
         back into the loop (a failed batch no longer stops it), rows are upserted
         independently (queryBatching 'independently'; the earlier 'independent' ran no
         query at all and returned 0 items, which dead-ended the loop), the upsert has
         alwaysOutputData so an empty run still re-feeds the loop.
       * 'Loop Over Items8' (grouped remark AI) is fed back once per group: only by
         'insert data to student remarks2' (the critical-remark alert branch no longer
         feeds Wait1).
       * 'Get staff FCM for flagged scan' reads user_id from 'build flagged record1' (the
         queue POST returns no body); 'get subject id4' gets an explicit, null-safe array.
       * 'get instructor id7' / 'get instructor id9' (corrections) accept placeholder
         instructors.
       * Corrections (sast-manual-correction / import-error-correction): the scan image lookup
         ('get existing scan image id' / '...id1') runs BEFORE the instructor lookup (it ran
         last under executionOrder v1, so 'Map target' saw no image id and the import error
         pointed at a new, EMPTY scan_error_images row); 'is data from google link?' only saves
         an image when the row carries one.
       * Corrections of a flagged scan (sast-manual-correction; the website review page and
         Flutter): 'get instructor id7' / 'get subject id6' use body.instructor_id /
         body.subject_id when it is the id of a creditable instructor / an existing subject,
         else the name/code lookup exactly as before (parameters as arrays); 'Map target' credits
         website_review approvals to body.term_id (the page's term; Flutter keeps the current
         term); the webhook is answered BEFORE the shared flow (Map target -> Mark Queue
         Validated1 -> Respond to Webhook5 -> 'After Correction Response' -> If19).
       * 'Map to Target Format' (Google Sheet import) sets import_type on every row, so
         sheet rows take If21's true branch (grouped remark AI) instead of the per-remark
         path meant for single scans. Phone/website/correction rows stay without it.
       * Phone reconciler 'reconcile ocr and omr1' (website_src/phone_reconcile.js replaces the
         original code, guarded by its hash): text only from Gemini (no Python ocr_fields
         fallback); the row-agreement rule replaces the count-based disagreement rules;
         omr_unavailable when the Python OMR returned no ratings. 'Handle Gemini failure' no
         longer takes the student id from Python OCR. 'build flagged record1' adds
         agreement_percent to the phone partial_data.
       * Sexual-word filter (website_src/profanity.js, copied into every node that stores or
         analyses a remark): sexual words -> same-length asterisks; insults/curses are allowed.
         Scans (phone + website): 'Remark Check' -> IF -> 'Verify Remark Words' (one extra Gemini
         call, only when a word was flagged) -> the reconciler stores confirmed words masked,
         misread words corrected, and masks everything when the re-check failed (fail safe).
         Typed text (sheet imports, corrections): deterministic masking only. Also in 'Code in
         JavaScript', 'build flagged record1', 'Format error for import_errors', the three
         mappers, and the two remark-AI parse nodes (a profane/masked wordcloud keyword is
         dropped). Both main Gemini prompts: transcribe without censoring.
       * Layout safety net (website_src/layout_check.js, one copy in each reconciler):
         layout_mismatch when the OMR's optional 'layout' evidence or Gemini's optional
         column_order (both main prompts ask for it) disagree with 5 columns O, VS, S, F, US x
         20 rows; the confident-scanner rule is then off for the page.
  5. Removes every hardcoded Supabase key: HTTP Request nodes that sent
     apikey/Authorization headers use the credential "Supabase iEvaluate (secret key)"
     (type Supabase API). Credentials are referenced by name AND by the live credential id
     (from the live export), so a re-import into the same n8n keeps them linked.
  6. Migration 013 (apply_sast_v2; needs public.recompute_sast in the database first):
       * One aggregate path: 'Raw Upsert Done' -> 'Code in JavaScript8' (unique instructor+term
         pairs) -> 'update to overall total2' running SELECT * FROM public.recompute_sast($1, $2)
         -> 'If28'. The two parallel Management/Performance chains (same recompute twice, If28 and
         its push alerts fired twice) are removed: Management Node1, Performance1, calculate
         mangement1/performance1, Loop Over Items4/5, send to managment table1/performance node1,
         calc1, update to overall total3, update to overall_question_means2/3.
       * Blank ratings stay NULL, never 0 (CHECK 1..5): website_src/rating.js (integer 1..5, else
         null) in 'Code in JavaScript', '2. Format Final Results1', 'build the record1' and
         'Map target1'; 'Map to Target Format3', 'Website Map to Target Format' and 'Map target'
         default a missing rating to null.
  7. No instructor names to Gemini on the phone either (apply_phone_name_privacy; idempotent):
       * 'Phone Known Names' runs known_names.sql (subjects only, no parameters); the phone prompt
         ('ai validation ocr and omr') lists only the known subjects and asks only for known_subject.
       * 'Phone Instructor Candidates' (phone_instructor_candidates.sql, continue on error) sits
         between 'Is not a SAST form?' (false) and 'Phone Remark Check', i.e. AFTER the Gemini call;
         the phone reconciler matches the raw instructor reading against it locally
         (name_match.js nmBestInstructor): a clear match -> the exact database name (found by 'get
         instructor id6'); several close matches -> flagged 'instructor_ambiguous'; no match -> the
         raw reading, as before. Every copy of name_match.js is refreshed.
       * assert_no_instructor_names_to_gemini: no Gemini request may carry an instructor list.
  8. Check-only rule (apply_check_only_rule; idempotent): only a check or slash mark counts. The
     OMR returns a rejected row (x, shade, circle, dot) as blank and names it in
     omr_debug[].invalid_mark; both reconcilers keep that row blank (source 'invalid_mark', never
     gemini_fill, no conflict, not a blank for too_many_blanks), add a per-row review note (no
     review flag for them). Both main Gemini prompts get
     one line (CHECK_ONLY_LINE); 'Website Reconcile OCR OMR' is refreshed from reconcile.js.
  9. Min-answered rule (apply_min_answers_rule; idempotent): fewer than 5 of 20 answered on a reliable
     scan -> the queue row is stored discarded, the website answer says so, no staff push.
 10. Stamp rule (apply_stamp_rule; idempotent; needs migration 015): both Gemini prompts ask for the
     required stamp_numbers (STAMP_PROMPT_PARAGRAPH); both reconcilers keep one readable stamp
     (website_src/stamp.js) or flag stamp_missing / stamp_unreadable / multiple_stamps; a stamp already
     stored for the term auto-rejects the page as duplicate_stamp (website: 'Website Identity Check' $6
     and the 'Website Raw Upsert' race via public.save_website_raw_row; phone: 'Phone Stamp Check' ->
     'Phone Stamp Verdict' before If31); 'build flagged record1' stores it discarded/rejected (after and
     above the min-answered block) with stamp_no/stamp_source; raw rows get the stamp ('Code in
     JavaScript' only when the mapper sent one, so sheet rows never clear it); corrections take
     body.stamp_no / body.stamp_source and answer HTTP 409 duplicate_stamp ('Correction Stamp Check' ->
     'Correction Stamp Duplicate?' -> 'Correction Duplicate Stamp Response') before 'Mark Queue
     Validated1', which also writes the stamp to the queue row.
 11. OCR cross-check (apply_ocr_crosscheck; idempotent; 2026-10-28): website_src/ocr_crosscheck.js in both
     reconcilers: with the OMR's confident ink check of the box (ocr_meta.ink, located), an AI student ID
     on an empty ID box is dropped (student_id_unverified), a written ID box read empty is
     student_id_missed (phone), remarks_missed / remarks_unverified likewise; Gemini's corrected_rows
     (white ink) -> white_ink_correction. Review only; an older OMR without ocr_meta.ink changes nothing.
     INVALID_MARK_TEXT names the OMR's new 'double' mark. Both Gemini prompts get WHITE_INK_LINE and a
     required corrected_rows array.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import re
import sys
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "website_src"
DEFAULT_INPUT = Path(r"C:\Users\rodzh\Downloads\perfect 101 (4).json")
DEFAULT_OUTPUT = HERE.parent / "perfect-2.1.json"
DEFAULT_LAYOUT = Path(r"C:\Users\rodzh\Downloads\perfect 101 (5).json")   # live export (positions only)

POSTGRES_CRED = {"postgres": {"id": "BEB0zDVaiSFMfjaE", "name": "Postgres account"}}
SUPABASE_CRED_NAME = "Supabase iEvaluate (secret key)"   # exact name of the live credential
WEBHOOK_CRED_NAME = "iEvaluate website webhook secret"
# Live credential ids (not secrets), from the live export 'perfect 101 (5).json'. With the id
# set, a re-import into the same n8n links the credential directly (no "issues" markers).
SUPABASE_CRED_ID = "t8WKxORo7BHCziqr"
WEBHOOK_CRED_ID = "cGPn1nyAiZqSUx40"

# Website OCR/OMR timing. A Gemini call that took longer than the old 30 s timeout was aborted
# by axios (code ECONNABORTED, shown by n8n as "The connection was aborted, perhaps the server
# is offline"), on both tries, and the page was flagged ai_validation_failed. Worst case now:
# OMR 60 s + Gemini 3 x 90 s + 2 x 5 s waits = 340 s, plus a few seconds of database work;
# the backend waits N8N_TIMEOUT_SECONDS (default 360 s) for the answer.
WEB_OMR_TIMEOUT_MS = 60_000
WEB_GEMINI_TIMEOUT_MS = 90_000
WEB_GEMINI_MAX_TRIES = 3
WEB_GEMINI_WAIT_MS = 5_000
# Remark re-check (one Gemini call, no retry, only when the sexual-word filter flagged a word).
# Phone: Flutter waits 60 s for the whole sast-scan-upload call. Website: it only starts when it
# can end by WEB_VERIFY_DEADLINE_MS after the page arrived (= the OMR + Gemini worst case the
# backend's 360 s already covers), so it never lengthens the worst case (website_remark_check.js).
PHONE_VERIFY_TIMEOUT_MS = 15_000
WEB_VERIFY_TIMEOUT_MS = 15_000
WEB_VERIFY_DEADLINE_MS = WEB_OMR_TIMEOUT_MS + WEB_GEMINI_MAX_TRIES * WEB_GEMINI_TIMEOUT_MS \
    + (WEB_GEMINI_MAX_TRIES - 1) * WEB_GEMINI_WAIT_MS

# Draft website nodes that are replaced (the webhook itself is kept and reconfigured).
OLD_WEBSITE_NODES = [
    "Prepare Website Scan",
    "Website Python OMR",
    "Website AI Validation",
    "Website Reconcile OCR OMR",
    "Website If Flagged?",
    "Website Queue Flagged",
    "Website Respond Flagged",
    "Website Respond Success",
    "Ready for Main Flow (Website)",
]
WEBHOOK_NAME = "SAST from Website"
FORM_MATCH = "Website Form Match"

# Shared nodes the website front end feeds (inputs added, node itself unchanged).
SHARED_ENTRY_CLEAN = "If19"
SHARED_ENTRY_FLAGGED = "Save scan error image"

# Shared nodes that are edited (see the patch_* functions).
CODE_IN_JS = "Code in JavaScript"
BUILD_FLAGGED = "build flagged record1"
FLAGGED_RESPOND = "Respond to Webhook"
SHEET_MAP = "Map to Target Format"       # Google Sheet import -> If19 (not '...3' / 'Website ...')

# Main-flow aggregate fix.
MAIN_RECOMPUTE_NODES = ["send to managment table1", "send to performance node1"]
RAW_LOOP = "Loop Over Items3"            # output 0 = "done" after every raw upsert
RAW_ERROR_IF = "If14"                    # output 0 = the raw upsert returned an error item
RAW_UPSERT_GATE = "Raw Upsert Done"
RAW_UPSERT = "Insert or update rows in a table1"
RAW_SKIP_IF = "Raw Row Already Stored?"      # website rows: skip the shared upsert, keep looping
RAW_UPSERT_BATCHING = "independently"        # exact n8n Postgres v2 option value (NOT 'independent')
AGG_CHAIN_HEADS = ["Management Node1", "Performance1"]

# SAST aggregates (migration 013): ONE call of public.recompute_sast per (instructor, term) rebuilds
# management_results, performance_results, overall_total_survey, overall_question_means and the
# instructor_subjects links from the raw rows (blanks = NULL, excluded). Replaces the two parallel
# Management/Performance chains (both ran the same recompute, and each ran 'overall total' -> If28,
# so every low-score alert was pushed twice). See apply_sast_recompute_flow.
SAST_PAIRS = "Code in JavaScript8"             # unique {instructor_id, term_id} from If1 (kept as is)
SAST_RECOMPUTE = "update to overall total2"    # reused (same credentials), now runs recompute_sast
SAST_ALERT_IF = "If28"                         # combined_score_mean < 3 -> department head / phone alerts
SAST_RECOMPUTE_SQL = "SELECT * FROM public.recompute_sast($1::uuid, $2::uuid);\n"
SAST_RECOMPUTE_PARAMS = "={{ [$json.instructor_id, $json.term_id] }}"
SAST_DEAD_NODES = [
    "Management Node1", "Performance1", "calculate mangement1", "calculate performance1",
    "Loop Over Items4", "Loop Over Items5", "send to managment table1", "send to performance node1",
    "calc1", "update to overall total3", "update to overall_question_means2", "update to overall_question_means3",
]
# Rating nodes: a blank answer stays NULL (never 0); anything outside 1..5 becomes NULL before the
# insert (the table gets CHECK 1..5 in migration 013). See patch_null_ratings / website_src/rating.js.
FORMAT_FINAL = "2. Format Final Results1"
PHONE_MAP = "Map to Target Format3"
WEBSITE_MAP = "Website Map to Target Format"
CORRECTION_BUILD = "build the record1"
NODE_REF_RE = re.compile(r"""\$\(\s*(['"])(.+?)\1\s*\)|\$node\[\s*(['"])(.+?)\3\s*\]""")

# Grouped remark AI loop (Google Sheet imports, If21 true branch).
REMARK_LOOP = "Loop Over Items8"
REMARK_LOOP_WAIT = "Wait1"                   # Wait1 -> Loop Over Items8 (next group)
REMARK_LOOP_FEED = "insert data to student remarks2"
REMARK_ALERT_FEEDS = ["If25", "PushNotificaton for sentiment analysisi5"]   # extra feeds, removed

# Flagged-scan staff push: its input (the queue POST, Prefer: return=minimal) has no body.
FLAGGED_FCM = "Get staff FCM for flagged scan"
# Before the min-answered rule; a live export with it is upgraded by apply_min_answers_rule.
FLAGGED_FCM_PARAMS_V1 = "={{ [ $('build flagged record1').first().json.user_id ?? null ] }}"
# A form stored as discarded (min-answered rule) needs nobody: NULL matches no row, so no push.
FLAGGED_FCM_PARAMS = ("={{ [ $('build flagged record1').first().json.status === 'discarded' ? null "
                      ": ($('build flagged record1').first().json.user_id ?? null) ] }}")
# Phone subject lookup: its input ('get instructor id6', alwaysOutputData) is an EMPTY item when
# the instructor is not matched, and n8n drops empty values from a '{{ }}' parameter list.
PHONE_SUBJECT_LOOKUP = "get subject id4"
PHONE_SUBJECT_PARAMS = ("={{ [ $json.instructor_id ?? null, "
                        "$json.subject_search_term ?? $('If31').first().json.subject ?? null ] }}")

PHONE_GEMINI_NODE = "ai validation ocr and omr"
PHONE_NOT_FORM_NODE = "Is not a SAST form?"
# Phone reconciler + Gemini-failure handler: replaced/edited only when they are exactly the
# code of the original export (so a hand edit in n8n is never silently overwritten).
PHONE_RECONCILE = "reconcile ocr and omr1"
PHONE_RECONCILE_BASE_SHA256 = "dcfa519b6c2efe4395472c33e3b58ab78f28a548bb2f0b4a6dc77da24312572a"
PHONE_GEMINI_FAILURE = "Handle Gemini failure"

# Phone remark re-check, between 'Is not a SAST form?' (false) and the reconciler.
PHONE_REMARK_CHECK = "Phone Remark Check"
PHONE_REMARK_IF = "Phone Remark Needs Check?"
PHONE_REMARK_VERIFY = "Phone Verify Remark Words"
# Sexual-word filter: base-workflow nodes that get maskProfanity (see patch_* below).
IMPORT_ERROR_FORMAT = "Format error for import_errors"
CORRECTION_MAPS = ["Map target", "Map target1"]          # sast-manual-correction / import-error-correction
UNIFIED_PARSE = "Parse Unified AI Result1"
INDIVIDUAL_PARSE = "Add Parse Code node1"
UNIFIED_AI = "Unified AI Analysis1"
INDIVIDUAL_AI = "Individual AI Analysis1"
GEMINI_PROMPT_ANCHOR = "don't translate or rewrite."
GEMINI_PROMPT_ADD = " Never censor or mask a word, even a vulgar one: it is filtered later."
KEYWORD_RULE = "Never use a vulgar or sexual word, or a masked word (asterisks such as ****), as a keyword.\n"
HELPER_HEADERS = {
    "profanity.js": "sexual-word filter",
    "remark_verify.js": "remark re-check",
    "name_match.js": "name matcher",
    "layout_check.js": "layout check",
    "rating.js": "SAST rating",
    "stamp.js": "stamp reader",
    "remark_words.js": "remark words",
    "remark_clean.js": "remark placeholders",
    "ai_sentences.js": "AI sentences",
    "ai_interpretation.js": "AI interpretation",
    "ai_tone.js": "AI tone",
    "ocr_crosscheck.js": "OCR cross-check",
}
# Known names for the main Gemini OCR prompt: a new Postgres node right before the Gemini call;
# Gemini returns the raw reading plus its closest known SUBJECT. Subjects only on both branches
# (phone: known_names.sql, website: website_known_names.sql): no instructor names reach Gemini.
WEB_KNOWN_NAMES = "Website Known Names"
PHONE_KNOWN_NAMES = "Phone Known Names"
# Phone: creditable instructors for the LOCAL match in the reconciler, queried after the Gemini call.
PHONE_CANDIDATES = "Phone Instructor Candidates"
# Must never appear in a Gemini request (privacy; assert_no_instructor_names_to_gemini).
INSTRUCTOR_LIST_MARKERS = ("known_instructor", "Known instructors", "instructor_list", "instructor_names",
                           "$('" + PHONE_CANDIDATES + "')", "$('Website Form Match')", "first_name", "last_name")
PHONE_OMR_NODE = "image to ocr and omr python"
SUBJECT_HINT_OLD = "2. subject: CTU course code (DDMGT, IT, CSO, HCI, ADBAS, IDA + digits)."
SUBJECT_HINT_NEW = "2. subject: the course code and/or subject title, as written."
KNOWN_ANCHOR = "6. student_id: digits only."
KNOWN_HEAD = (
    "\n7. known_subject: the code of the one subject in the list below that the written subject "
    "refers to, copied exactly from the list; empty if none clearly fits or no subject is written.\n"
    "The list only helps to read messy handwriting: subject above stays exactly as written.\n"
)
KNOWN_SCHEMA_ANCHOR = '"document_check": {'
# Layout safety net (both main Gemini prompts): Gemini also reports the rating column headers as
# printed, left to right; the reconcilers flag layout_mismatch when they are not O, VS, S, F, US
# (website_src/layout_check.js). Optional: an answer without it never flags. The prompt text is
# authored with literal backslash-n, like the rest of the prompt (see HEADER_BUBBLE_ANCHOR).
COLUMN_ORDER_ANCHOR = "- Return letters, not numbers."
COLUMN_ORDER_ADD = (
    r"\n- column_order: the rating column headers exactly as printed above the bubbles, left to right "
    r"(normally O, VS, S, F, US; a header printed as a number is returned as that number). Leave it "
    r"empty if you cannot read the headers. Report each row's rating by the header of the column its "
    r"mark is in, not by its position."
)
COLUMN_ORDER_SCHEMA_ADD = '"column_order": { "type": "ARRAY", "items": { "type": "STRING" } },\n        '
KNOWN_SCHEMA_ADD = '"known_subject": { "type": "STRING" },\n        '
# Phone prompt as built before apply_phone_name_privacy (only used to migrate a live export).
OLD_PHONE_KNOWN_SCHEMA = '"known_instructor": { "type": "STRING" },\n        '
OLD_PHONE_KNOWN_EXPR_RE = re.compile(r"\{\{ JSON\.stringify\(\(\(\) => \{ try \{ const k = \$\('"
                                     + re.escape(PHONE_KNOWN_NAMES) + r"'\)[\s\S]*?\.slice\(1, -1\) \}\}")
OLD_PHONE_RECONCILE_MARK = "nmKnownInstructor(gText('known_instructor')"
# phone_reconcile.js as built by apply_phone_name_privacy (local instructor match), before the
# check-only rule; a live export with this code is replaced by the current phone_reconcile.js.
PRIVACY_PHONE_RECONCILE_MARK = "$('" + "Phone Instructor Candidates" + "')"
# Check-only rule (OMR 2026-10-08: only a check or slash counts; the OMR names a rejected mark in
# omr_debug[].invalid_mark). One line in both main Gemini prompts (no names, no schema change); both
# reconcilers keep such a row blank (website_src/reconcile.js, phone_reconcile.js: source 'invalid_mark').
WEB_RECONCILE = "Website Reconcile OCR OMR"
WEB_GEMINI_NODE = "Website AI Validation"
WEB_RECONCILE_MARK = "// Python gives the ratings, Gemini cross-checks; text comes from Gemini.\n"
CHECK_ONLY_ANCHOR = "- Two bubbles shaded in one row -> 'blank'."
CHECK_ONLY_LINE = ("- Only a check mark or a slash (/) counts as an answer; read an x, a fully shaded bubble, "
                   "a circle or a dot as 'blank'.")
# Min-answered rule (teacher, 2026-10-08): fewer than 5 of the 20 items answered -> the form is
# discarded. The reconcilers decide (auto_discard); 'build flagged record1' then stores the queue row
# already discarded, the website answer says so, and no staff push is sent.
WEB_FLAGGED_RECORD = "Website Flagged Record"
WEB_FLAGGED_RECORD_MARK = "// Queue record for any flagged or failed website page.\n"
MIN_ANSWERS_MARK = "// min-answered rule (teacher, 2026-10-08)"
MIN_ANSWERS_ANCHOR = "\nreturn [{ json: record }];"
MIN_ANSWERS_BLOCK = (
    "\n" + MIN_ANSWERS_MARK + ": fewer than 5 of the 20 items answered on a reliable scan\n"
    "// (the reconciler's auto_discard). Stored already discarded, like the website Discard button, so it\n"
    "// is never counted and nobody has to review it.\n"
    "if (rec.auto_discard === true) {\n"
    "  const answered = Number.isInteger(rec.answered_count) ? rec.answered_count : null;\n"
    "  record.status = 'discarded';\n"
    "  record.review_action = 'rejected';\n"
    "  record.review_correction = {\n"
    "    action: 'rejected',\n"
    "    auto: true,\n"
    "    rule: 'min_answered_5',\n"
    "    note: 'Fewer than 5 of the 20 items were answered' + (answered !== null ? ' (only ' + answered + ')' : '')\n"
    "      + ', so this form was discarded automatically and is not counted.',\n"
    "    reviewed_by: null,\n"
    "    reviewed_by_name: 'Automatic rule',\n"
    "    at: new Date().toISOString(),\n"
    "  };\n"
    "}\n"
)
PHONE_IMAGE_ITEM = "$('SAST image from phone').item.json.body.image"
PHONE_IMAGE_FIRST = "$('SAST image from phone').first().json.body.image"
INCLUDE_RE = re.compile(r"^// @@include ([\w.]+)\n", re.M)


def read(name: str) -> str:
    return (SRC / name).read_text(encoding="utf-8").replace("\r\n", "\n").rstrip() + "\n"


def helper_block(name: str) -> str:
    """A shared helper (website_src/profanity.js, remark_verify.js) as copied into a node:
    full-line comments and blank lines removed (the documentation stays in the source file),
    between two marker lines. Every copy is identical (validate_workflow.py checks it)."""
    body = [ln for ln in read(name).split("\n") if ln.strip() and not ln.lstrip().startswith("//")]
    title = HELPER_HEADERS[name]
    return (f"// {title}, from website_src/{name}\n"
            + "\n".join(body) + f"\n// end of {title}\n")


def read_js(name: str) -> str:
    """A Code node source with its '// @@include <helper>' lines expanded."""
    code = INCLUDE_RE.sub(lambda m: helper_block(m.group(1)), read(name))
    if "@@include" in code:
        raise SystemExit(f"{name}: unexpanded @@include")
    return code


def prepend_filter(code: str, where: str) -> str:
    """Base-workflow node: put the sexual-word filter in front of its code (once)."""
    if "function maskProfanity" in code:
        raise SystemExit(f"{where}: already contains maskProfanity; the input changed")
    return helper_block("profanity.js") + "\n" + code


def patch_gemini_prompt(body: str, where: str) -> str:
    """Main Gemini OCR prompt (phone + website): transcribe remarks without censoring, so the
    deterministic filter and the re-check see what the student wrote; and ask for the optional
    column_order (the rating headers left to right, layout safety net: layout_check.js). The only
    schema change is that one optional field."""
    body = replace_once(body, GEMINI_PROMPT_ANCHOR, GEMINI_PROMPT_ANCHOR + GEMINI_PROMPT_ADD, where)
    body = replace_once(body, COLUMN_ORDER_ANCHOR, COLUMN_ORDER_ANCHOR + COLUMN_ORDER_ADD, where)
    return replace_once(body, KNOWN_SCHEMA_ANCHOR, COLUMN_ORDER_SCHEMA_ADD + KNOWN_SCHEMA_ANCHOR, where)


def known_names_expression(node_name: str) -> str:
    """Prompt text with the known SUBJECT list from the Known Names node (JSON-escaped for the body);
    never an instructor name (privacy). Empty when the lookup failed or there are no subjects, so
    Gemini then just reads the form as before."""
    js = ("JSON.stringify((() => { try { const k = $('" + node_name + "').first().json || {}; "
          "if (!k.subject_list) return ''; "
          "return " + json.dumps(KNOWN_HEAD) + " + 'Known subjects (code = title): ' + (k.subject_list || 'none'); "
          "} catch (e) { return ''; } })()).slice(1, -1)")
    if "{{" in js or "}}" in js:
        raise SystemExit("known names expression must not contain '{{' or '}}'")
    return "{{ " + js + " }}"


def patch_gemini_known(body: str, node_name: str, where: str, extra_schema: str = "") -> str:
    """Main Gemini OCR prompt (phone + website): neutral subject hint, the known SUBJECT list, one
    optional schema field (known_subject) plus extra_schema; every existing field stays as it was."""
    body = replace_once(body, SUBJECT_HINT_OLD, SUBJECT_HINT_NEW, where)
    body = replace_once(body, KNOWN_ANCHOR, KNOWN_ANCHOR + known_names_expression(node_name), where)
    return replace_once(body, KNOWN_SCHEMA_ANCHOR, extra_schema + KNOWN_SCHEMA_ADD + KNOWN_SCHEMA_ANCHOR, where)


# --- Website AI Validation only: header crop, confidence, few-shot examples. Like the phone, no
# instructor name list is sent to Gemini (privacy); local fuzzy matching against the batch/creditable
# instructors (form_match.sql, decide.js) does the instructor match.
CONFIDENCE_SCHEMA_ADD = (
    '"instructor_confidence": { "type": "STRING", "enum": ["high", "medium", "low"] },\n        '
    '"subject_confidence": { "type": "STRING", "enum": ["high", "medium", "low"] },\n        '
    '"student_id_confidence": { "type": "STRING", "enum": ["high", "medium", "low"] },\n        '
)
# The prompt's "text" field is authored with literal backslash-n (not real newlines) so the
# whole node body stays on few lines in the exported JSON; matched/produced the same way here.
HEADER_BUBBLE_ANCHOR = r"\n\n--- BUBBLE GRID ---"
HEADER_HINT_TEXT = (
    "\n\n--- HEADER CLOSE-UP --- A second, zoomed and contrast-boosted image of the handwritten "
    "header follows. Read instructor, subject, and student_id from it when it is clearer than "
    "the first image; the two should agree.\n"
)
OCR_EXAMPLES_DIR = HERE / "ocr_examples"
MAX_OCR_EXAMPLES = 5
OCR_EXAMPLE_IMAGE_EXTS = (".jpg", ".jpeg", ".png")


def patch_gemini_known_website(body: str, node_name: str, where: str) -> str:
    """Website: as the phone (known SUBJECT list, known_subject) plus the optional per-field
    confidence enums."""
    return patch_gemini_known(body, node_name, where, CONFIDENCE_SCHEMA_ADD)


def header_image_part_expr(page_json_path: str) -> str:
    """Optional third Gemini image part (website only): the zoomed, upscaled, contrast-boosted
    header crop from Website Prepare Scan, appended to 'parts' only when the backend sent one
    (header_image_base64; backward compatible with a backend that doesn't)."""
    js = ("(() => { const h = " + page_json_path + ".header_image_base64; "
          "return h ? ',\\n        { \"inlineData\": { \"mimeType\": \"image/jpeg\", \"data\": \"' + h "
          "+ '\" } }' : ''; })()")
    if "{{" in js or "}}" in js:
        raise SystemExit("header image part expression must not contain '{{' or '}}'")
    return "{{ " + js + " }}"


def header_hint_expr(page_json_path: str) -> str:
    """Website only: tell Gemini about the second, zoomed header image only when one was sent.
    JSON.stringify + slice matches the escaping the surrounding static prompt text already uses
    (see known_names_expression) so a real newline never lands unescaped inside the JSON body."""
    js = ("(() => { const h = " + page_json_path + ".header_image_base64; "
          "return h ? JSON.stringify(" + json.dumps(HEADER_HINT_TEXT) + ").slice(1, -1) : ''; })()")
    if "{{" in js or "}}" in js:
        raise SystemExit("header hint expression must not contain '{{' or '}}'")
    return "{{ " + js + " }}"


def load_ocr_examples() -> list[tuple[bytes, str, dict]]:
    """Few-shot examples for the website prompt: n8n/tools/ocr_examples/<name>.jpg|.png +
    <name>.json pairs (see ocr_examples/README.md). Empty by default; a name missing either
    file, or an unparsable/empty JSON, is skipped. At most MAX_OCR_EXAMPLES, by filename."""
    if not OCR_EXAMPLES_DIR.is_dir():
        return []
    out: list[tuple[bytes, str, dict]] = []
    for json_path in sorted(OCR_EXAMPLES_DIR.glob("*.json")):
        img_path = next((p for ext in OCR_EXAMPLE_IMAGE_EXTS
                         if (p := json_path.with_suffix(ext)).is_file()), None)
        if img_path is None:
            continue
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or not data:
            continue
        mime = "image/png" if img_path.suffix.lower() == ".png" else "image/jpeg"
        out.append((img_path.read_bytes(), mime, data))
        if len(out) >= MAX_OCR_EXAMPLES:
            break
    return out


def few_shot_contents_prefix(examples: list[tuple[bytes, str, dict]]) -> str:
    """The example user/model turns (static, base64-embedded at build time) as raw JSON text, to
    prepend inside the 'contents' array before the real request; '' when there are none (default:
    ocr_examples/ is empty, so the body is byte-identical to before this feature)."""
    if not examples:
        return ""
    turns = []
    for raw, mime, data in examples:
        b64 = base64.b64encode(raw).decode("ascii")
        user = {"role": "user", "parts": [
            {"text": "Example of a correctly read header (instructor/subject/student_id):"},
            {"inlineData": {"mimeType": mime, "data": b64}},
        ]}
        model = {"role": "model", "parts": [{"text": json.dumps(data)}]}
        turns.append(json.dumps(user))
        turns.append(json.dumps(model))
    return ",\n    ".join(turns) + ",\n    "


def known_names_node(name, pos):
    """Phone: known SUBJECTS for the prompt (no instructor names reach Gemini), no query
    parameters. A failure goes on with an empty list."""
    return pg_node(name, pos, read("known_names.sql"), None, "continueRegularOutput", always_output=True)


def phone_candidates_node(name, pos):
    """Phone: creditable instructors for the reconciler's local match (never sent to Gemini), no
    query parameters. A failure goes on (the reconciler then keeps the raw reading)."""
    return pg_node(name, pos, read("phone_instructor_candidates.sql"), None, "continueRegularOutput",
                   always_output=True)


def website_known_names_node(name, pos):
    """Website: known SUBJECTS only (no instructor names reach Gemini), no query parameters. A
    failure goes on with an empty list."""
    return pg_node(name, pos, read("website_known_names.sql"), None, "continueRegularOutput",
                   always_output=True)


def verify_body_expression(mime_js: str, image_js: str) -> str:
    """JSON body of a 'Verify Remark Words' node: the prompt and generationConfig built by the
    Remark Check node ($json), plus the scan image (read by name, never copied into an item)."""
    js = ("JSON.stringify({ contents: [ { parts: [ { text: $json.verify_prompt }, "
          "{ inlineData: { mimeType: " + mime_js + ", data: " + image_js + " } } ] } ], "
          "generationConfig: $json.verify_generation_config })")
    if "{{" in js or "}}" in js:
        raise SystemExit("verify body expression must not contain '{{' or '}}'")
    return "={{ " + js + " }}"


def verify_node(name, pos, gemini, mime_js, image_js, timeout_ms):
    """Remark re-check: one Gemini call (same model + credential as the main OCR call), no retry.
    A failure or timeout goes on (continueRegularOutput) and the reconciler masks the words."""
    return {
        "parameters": {
            "method": "POST",
            "url": gemini["parameters"]["url"],
            "authentication": "predefinedCredentialType",
            "nodeCredentialType": gemini["parameters"]["nodeCredentialType"],
            "sendBody": True,
            "specifyBody": "json",
            "jsonBody": verify_body_expression(mime_js, image_js),
            "options": {"timeout": timeout_ms},
        },
        "type": "n8n-nodes-base.httpRequest",
        "typeVersion": gemini["typeVersion"],
        "position": pos,
        "id": nid(name),
        "name": name,
        "credentials": copy.deepcopy(gemini["credentials"]),
        "onError": "continueRegularOutput",
    }


def nid(name: str) -> str:
    """Stable node id so re-running the build gives the same ids."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "ievaluate-website-branch/" + name))


def assert_no_param_in_sql_comments(name: str, sql: str) -> None:
    """pg-promise substitutes $n everywhere, including inside SQL comments; a
    multi-line OCR value substituted into a -- comment would escape it."""
    for line in sql.split("\n"):
        if "--" in line and re.search(r"\$\d", line.split("--", 1)[1]):
            raise SystemExit(f"{name}: '$n' inside an SQL comment: {line!r}")
    if "/*" in sql:
        raise SystemExit(f"{name}: block comments are not allowed (pg-promise $n substitution)")


def replace_once(text: str, old: str, new: str, where: str) -> str:
    if text.count(old) != 1:
        raise SystemExit(f"{where}: expected exactly one occurrence of {old[:60]!r}; the input changed")
    return text.replace(old, new)


def if_node(name, pos, expr, cond_id):
    return {
        "parameters": {
            "conditions": {
                "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose", "version": 3},
                "conditions": [{
                    "id": cond_id,
                    "leftValue": expr,
                    "rightValue": "",
                    "operator": {"type": "boolean", "operation": "true", "singleValue": True},
                }],
                "combinator": "and",
            },
            "looseTypeValidation": True,
            "options": {},
        },
        "type": "n8n-nodes-base.if",
        "typeVersion": 2.3,
        "position": pos,
        "id": nid(name),
        "name": name,
    }


def code_node(name, pos, src_file, on_error=None):
    node = {
        "parameters": {"jsCode": read_js(src_file)},
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
        "position": pos,
        "id": nid(name),
        "name": name,
    }
    if on_error:
        node["onError"] = on_error
    return node


def pg_node(name, pos, sql, replacement, on_error, always_output=False):
    assert_no_param_in_sql_comments(name, sql)
    node = {
        "parameters": {
            "operation": "executeQuery",
            "query": sql,
            "options": {"queryReplacement": replacement} if replacement is not None else {},
        },
        "type": "n8n-nodes-base.postgres",
        "typeVersion": 2.6,
        "position": pos,
        "id": nid(name),
        "name": name,
        "credentials": copy.deepcopy(POSTGRES_CRED),
        "onError": on_error,
    }
    if always_output:
        node["alwaysOutputData"] = True
    return node


def respond_node(name, pos):
    return {
        "parameters": {"respondWith": "firstIncomingItem", "options": {"responseCode": 200}},
        "type": "n8n-nodes-base.respondToWebhook",
        "typeVersion": 1.5,
        "position": pos,
        "id": nid(name),
        "name": name,
    }


# 'Website Identity Check': batch ids, term, student id, task_id, and (stamp rule) the page's stamp
# ('' when the reconciler did not read exactly one stamp). $json = the reconciler's record.
_P = "$('Website Prepare Scan').first(0).json"
IDENTITY_CHECK_PARAMS = "={{ [ " + ", ".join([
    _P + ".instructor_id",
    _P + ".subject_id",
    _P + ".term_id ?? null",
    "String($json.student_id ?? '').trim()",
    _P + ".task_id",
    # two-reader rule: an accepted stamp (auto-reject on a match) or else the stamp_hint (only a note)
    "($json.stamp_status === 'ok' ? String($json.stamp_no ?? '') : String($json.stamp_hint ?? ''))",
]) + " ] }}"
# 'Website Raw Upsert' -> public.save_website_raw_row (migration 015): the Decide record + its stamp.
_D = "$('Website Decide').first(0).json"
RAW_UPSERT_PARAMS = "={{ [ " + ", ".join([
    _D + ".instructor_id",
    _D + ".subject_id",
    _D + ".term_id",
    "(String(" + _D + ".student_id ?? '').trim() || null)",
    "(String(" + _D + ".instructor_name ?? '').trim() || null)",
    "(String(" + _D + ".remark ?? '').trim() || null)",
    _D + ".submitted_date",
    "(" + _D + ".staff_id || null)",
    _D + ".task_id",
    "JSON.stringify(" + _D + ".ratings ?? {})",
    "(" + _D + ".stamp_no || null)",
    "(" + _D + ".stamp_no ? (" + _D + ".stamp_source || 'gemini') : null)",
]) + " ] }}"


def build_website_nodes(orig_by_name):
    P = "$('Website Prepare Scan').first(0).json"
    nodes = []

    # --- entry --------------------------------------------------------------
    nodes.append(code_node("Website Prepare Scan", [-53760, 11904], "prepare_scan.js", "continueErrorOutput"))
    nodes.append(code_node("Website Invalid Request Response", [-53536, 11584], "invalid_request_response.js"))
    nodes.append(pg_node(
        "Website Duplicate Check", [-53536, 11904], read("duplicate_check.sql"),
        "={{ [ $json.task_id ] }}", "continueErrorOutput"))
    nodes.append(code_node("Website Not Saved Response", [-53312, 11584], "not_saved_response.js"))
    nodes.append(if_node("Website Is Duplicate?", [-53312, 11904],
                         "={{ $json.in_raw === true || $json.in_queue === true }}",
                         "8f0b6c1e-web0-4d01-9a01-000000000001"))
    nodes.append(code_node("Website Duplicate Response", [-53088, 11744], "duplicate_response.js"))
    nodes.append(code_node("Website Attach Image", [-53088, 12000], "attach_image.js", "continueErrorOutput"))

    # --- OCR / OMR (mirrors the phone branch) --------------------------------
    phone_omr = orig_by_name["image to ocr and omr python"]
    nodes.append({
        "parameters": {
            "method": "POST",
            "url": phone_omr["parameters"]["url"],
            "sendBody": True,
            "contentType": "multipart-form-data",
            "bodyParameters": {"parameters": [
                {"parameterType": "formBinaryData", "name": "image", "inputDataFieldName": "data"},
                {"name": "task_id", "value": "={{ $json.task_id }}"},
                {"name": "user_id", "value": "={{ $json.staff_id || 'website-batch' }}"},
                {"name": "paper_size", "value": "={{ $json.paper_size }}"},
                {"name": "term_id", "value": "={{ $json.term_id || '' }}"},
                {"name": "filename", "value": "={{ $json.filename }}"},
            ]},
            "options": {"timeout": WEB_OMR_TIMEOUT_MS},
        },
        "type": "n8n-nodes-base.httpRequest",
        "typeVersion": 4.4,
        "position": [-52864, 12000],
        "id": nid("Website Python OMR"),
        "name": "Website Python OMR",
        "onError": "continueRegularOutput",
    })

    nodes.append(website_known_names_node(WEB_KNOWN_NAMES, [-52752, 12192]))
    gem = copy.deepcopy(orig_by_name[PHONE_GEMINI_NODE])
    body = gem["parameters"]["jsonBody"]
    old_img = "{{ $('SAST image from phone').item.json.body.image }}"
    if body.count(old_img) != 1 or body.count('"mimeType": "image/jpeg"') != 1:
        raise SystemExit("Phone Gemini body changed; update the image/mimeType replacement in the build script")
    body = body.replace(old_img, "{{ " + P + ".image_base64 }}")
    body = body.replace('"mimeType": "image/jpeg"', '"mimeType": "{{ ' + P + '.mime_type }}"')
    # optional third image part: the zoomed header crop, appended only when the backend sent one
    header_anchor = '"data": "{{ ' + P + '.image_base64 }}"\n          }\n        }\n      ]'
    body = replace_once(body, header_anchor, '"data": "{{ ' + P + '.image_base64 }}"\n          }\n        }'
                        + header_image_part_expr(P) + "\n      ]", "Website AI Validation")
    body = replace_once(body, HEADER_BUBBLE_ANCHOR, header_hint_expr(P) + HEADER_BUBBLE_ANCHOR, "Website AI Validation")
    # few-shot examples (n8n/tools/ocr_examples/): static, base64-embedded at build time; no-op
    # while the folder is empty (the default)
    examples = load_ocr_examples()
    if examples:
        contents_anchor = '"contents": [\n    {\n      "parts": ['
        body = replace_once(body, contents_anchor, '"contents": [\n    ' + few_shot_contents_prefix(examples)
                            + '{\n      "role": "user",\n      "parts": [', "Website AI Validation")
    body = patch_gemini_known_website(body, WEB_KNOWN_NAMES, "Website AI Validation")
    gem["parameters"]["jsonBody"] = patch_gemini_prompt(body, "Website AI Validation")
    gem["parameters"]["options"] = {**gem["parameters"].get("options", {}), "timeout": WEB_GEMINI_TIMEOUT_MS}
    gem.update({"retryOnFail": True, "maxTries": WEB_GEMINI_MAX_TRIES, "waitBetweenTries": WEB_GEMINI_WAIT_MS})
    gem.update({"id": nid("Website AI Validation"), "name": "Website AI Validation", "position": [-52640, 12000]})
    nodes.append(gem)

    not_form = copy.deepcopy(orig_by_name[PHONE_NOT_FORM_NODE])
    not_form["parameters"]["conditions"]["conditions"][0]["id"] = "8f0b6c1e-web0-4d01-9a01-000000000002"
    not_form.update({"id": nid("Website Is Not A Form?"), "name": "Website Is Not A Form?", "position": [-52416, 12000]})
    nodes.append(not_form)
    nodes.append(code_node("Website Rejected Response", [-52192, 11744], "rejected_response.js"))

    # --- remark re-check: only when the sexual-word filter flagged a word in Gemini's remark ---
    src = read("website_remark_check.js")
    for const, val in (("VERIFY_TIMEOUT_MS", WEB_VERIFY_TIMEOUT_MS), ("VERIFY_DEADLINE_MS", WEB_VERIFY_DEADLINE_MS)):
        if f"const {const} = {val};" not in src:
            raise SystemExit(f"website_remark_check.js: {const} must be {val} (build constants)")
    nodes.append(code_node("Website Remark Check", [-52304, 12256], "website_remark_check.js", "continueRegularOutput"))
    nodes.append(if_node("Website Remark Needs Check?", [-52192, 12400], "={{ $json.remark_check_needed === true }}",
                         "8f0b6c1e-web0-4d01-9a01-000000000007"))
    nodes.append(verify_node("Website Verify Remark Words", [-52080, 12256], orig_by_name[PHONE_GEMINI_NODE],
                             P + ".mime_type", P + ".image_base64", WEB_VERIFY_TIMEOUT_MS))
    nodes.append(code_node("Website Reconcile OCR OMR", [-52192, 12096], "reconcile.js", "continueErrorOutput"))

    # --- batch integrity check, the form's instructor/subject vs the database, decision ---
    nodes.append(pg_node(
        "Website Identity Check", [-51968, 12096], read("identity_check.sql"),
        IDENTITY_CHECK_PARAMS, "continueErrorOutput"))
    # The form's instructor/subject (Gemini) vs the database: scores only; Decide applies the
    # thresholds. Input: 'Website Identity Check' (either output); its error output -> Decide
    # (form_match_failed). Parameters as an array (n8n drops empty '{{ }}' values).
    nodes.append(pg_node(
        FORM_MATCH, [-51856, 12256], read("form_match.sql"),
        "={{ [ " + ", ".join([
            P + ".instructor_id",
            P + ".subject_id",
            "String($('Website Reconcile OCR OMR').first(0).json.ocr_instructor ?? '')",
            "String($('Website Reconcile OCR OMR').first(0).json.ocr_subject ?? '')",
            "($json.resolved_term_id ?? " + P + ".term_id ?? null)",
        ]) + " ] }}",
        "continueErrorOutput"))
    nodes.append(code_node("Website Decide", [-51744, 12096], "decide.js", "continueErrorOutput"))
    nodes.append(if_node("Website Is Flagged?", [-51520, 12096], "={{ $json.flagged === true }}",
                         "8f0b6c1e-web0-4d01-9a01-000000000003"))

    # --- clean: store the raw row, answer, then hand off to the shared flow ---
    nodes.append(pg_node(
        "Website Raw Upsert", [-51296, 12000], read("website_raw_upsert.sql"),
        RAW_UPSERT_PARAMS, "continueErrorOutput"))
    nodes.append(if_node("Website Raw Stored?", [-51072, 12000], "={{ $json.saved === true }}",
                         "8f0b6c1e-web0-4d01-9a01-000000000004"))
    nodes.append(code_node("Website Success Response", [-50848, 11904], "website_success_response.js"))
    nodes.append(respond_node("Website Respond Saved", [-50624, 11904]))
    nodes.append(code_node("Website Map to Target Format", [-50400, 11904], "website_map_to_target.js"))
    nodes.append(code_node("Website Flagged Record", [-51072, 12288], "flagged_record.js"))
    nodes.append(respond_node("Website Respond", [-49808, 11744]))

    # No sticky note: the live workflow has none (it was removed there); see WEBSITE_BRANCH.md.
    return nodes


WEBSITE_CONNECTIONS = [
    # (source, output index, target, target input index)
    ("SAST from Website", 0, "Website Prepare Scan", 0),
    ("Website Prepare Scan", 0, "Website Duplicate Check", 0),
    ("Website Prepare Scan", 1, "Website Invalid Request Response", 0),
    ("Website Invalid Request Response", 0, "Website Respond", 0),
    ("Website Duplicate Check", 0, "Website Is Duplicate?", 0),
    ("Website Duplicate Check", 1, "Website Not Saved Response", 0),
    ("Website Not Saved Response", 0, "Website Respond", 0),
    ("Website Is Duplicate?", 0, "Website Duplicate Response", 0),
    ("Website Is Duplicate?", 1, "Website Attach Image", 0),
    ("Website Duplicate Response", 0, "Website Respond", 0),
    ("Website Attach Image", 0, "Website Python OMR", 0),
    ("Website Attach Image", 1, "Website Invalid Request Response", 0),
    ("Website Python OMR", 0, WEB_KNOWN_NAMES, 0),
    (WEB_KNOWN_NAMES, 0, "Website AI Validation", 0),
    ("Website AI Validation", 0, "Website Is Not A Form?", 0),
    ("Website AI Validation", 1, "Website Reconcile OCR OMR", 0),
    ("Website Is Not A Form?", 0, "Website Rejected Response", 0),
    ("Website Is Not A Form?", 1, "Website Remark Check", 0),
    ("Website Remark Check", 0, "Website Remark Needs Check?", 0),
    ("Website Remark Needs Check?", 0, "Website Verify Remark Words", 0),
    ("Website Remark Needs Check?", 1, "Website Reconcile OCR OMR", 0),
    ("Website Verify Remark Words", 0, "Website Reconcile OCR OMR", 0),
    ("Website Rejected Response", 0, "Website Respond", 0),
    ("Website Reconcile OCR OMR", 0, "Website Identity Check", 0),
    ("Website Reconcile OCR OMR", 1, "Website Flagged Record", 0),
    ("Website Identity Check", 0, FORM_MATCH, 0),
    ("Website Identity Check", 1, FORM_MATCH, 0),
    (FORM_MATCH, 0, "Website Decide", 0),
    (FORM_MATCH, 1, "Website Decide", 0),
    ("Website Decide", 0, "Website Is Flagged?", 0),
    ("Website Decide", 1, "Website Flagged Record", 0),
    ("Website Is Flagged?", 0, "Website Flagged Record", 0),
    ("Website Is Flagged?", 1, "Website Raw Upsert", 0),
    ("Website Raw Upsert", 0, "Website Raw Stored?", 0),
    ("Website Raw Upsert", 1, "Website Not Saved Response", 0),
    ("Website Raw Stored?", 0, "Website Success Response", 0),
    ("Website Raw Stored?", 1, "Website Flagged Record", 0),
    ("Website Success Response", 0, "Website Respond Saved", 0),
    # the website webhook is answered; only then the page enters the shared main flow
    ("Website Respond Saved", 0, "Website Map to Target Format", 0),
    ("Website Map to Target Format", 0, SHARED_ENTRY_CLEAN, 0),
    ("Website Flagged Record", 0, SHARED_ENTRY_FLAGGED, 0),
]


def add_connection(conns, src, out_idx, dst, in_idx):
    outs = conns.setdefault(src, {}).setdefault("main", [])
    while len(outs) <= out_idx:
        outs.append([])
    if outs[out_idx] is None:
        outs[out_idx] = []
    entry = {"node": dst, "type": "main", "index": in_idx}
    if entry not in outs[out_idx]:
        outs[out_idx].append(entry)


def remove_node_connections(conns, names):
    names = set(names)
    for src in list(conns):
        if src in names:
            del conns[src]
            continue
        for ctype, outs in conns[src].items():
            conns[src][ctype] = [[c for c in (lst or []) if c["node"] not in names] for lst in outs]


# ---------------------------------------------------------------- shared-node patches
def patch_code_in_javascript(node):
    """Website staff id, task_id into the raw row, blank student id -> NULL."""
    code = node["parameters"]["jsCode"]
    anchor = "// Attempt 3: 'sast-manual-correction' node\n"
    code = replace_once(code, anchor, (
        "// Attempt 2b: 'SAST from Website' node\n"
        "if (!staffId) {\n"
        "  try {\n"
        "    const webNode = $('SAST from Website').first();\n"
        "    if (webNode && webNode.json.body && webNode.json.body.staff_id) {\n"
        "      staffId = webNode.json.body.staff_id;\n"
        "    }\n"
        "  } catch (error) {\n"
        "    // Not a website execution\n"
        "  }\n"
        "}\n\n" + anchor), CODE_IN_JS)
    code = replace_once(
        code,
        "      student_id: r['Student ID'] || null,\n",
        "      // blank student id -> NULL so it never matches another row\n"
        "      student_id: (typeof r['Student ID'] === 'string' ? r['Student ID'].trim() : r['Student ID']) || null,\n",
        CODE_IN_JS)
    code = replace_once(
        code,
        "      validation_status: isManuallyCorrected ? 'corrected' : 'auto'\n    }\n",
        "      validation_status: isManuallyCorrected ? 'corrected' : 'auto',\n\n"
        "      // only when present, so sheet imports never clear task_id\n"
        "      ...(r['task_id'] && !Array.isArray(r['task_id']) ? { task_id: String(r['task_id']) } : {})\n"
        "    }\n",
        CODE_IN_JS)
    code = replace_once(code, "// Map the raw data and include the new database flags\n", (
        "// website rows are already stored, so skip the shared upsert\n"
        "let websiteRawStored = false;\n"
        "try {\n"
        "  const w = $('Website Raw Upsert').first();\n"
        "  websiteRawStored = !!(w && w.json && w.json.saved === true);\n"
        "} catch (error) {\n"
        "  // Not a website execution\n"
        "}\n\n"
        "// Map the raw data and include the new database flags\n"), CODE_IN_JS)
    code = replace_once(
        code,
        "      ...(r['task_id'] && !Array.isArray(r['task_id']) ? { task_id: String(r['task_id']) } : {})\n",
        "      ...(r['task_id'] && !Array.isArray(r['task_id']) ? { task_id: String(r['task_id']) } : {}),\n"
        "      ...(websiteRawStored ? { _raw_already_stored: true } : {})\n",
        CODE_IN_JS)
    code = replace_once(
        code,
        "  if (!seen.has(uniqueKey)) {\n",
        "  // rows without a student id are never deduped\n"
        "  if (j.student_id === null || !seen.has(uniqueKey)) {\n",
        CODE_IN_JS)
    # Sexual words -> asterisks in the stored raw row (phone/website remarks are already masked by
    # the reconcilers; sheet imports and corrections are typed text, so no re-check there).
    code = replace_once(
        code,
        "      Remarks_and_Suggestions: String(r['Remarks & Suggestions'] ?? '').trim() || null,\n",
        "      // mask sexual words before storing\n"
        "      Remarks_and_Suggestions: maskProfanity(String(r['Remarks & Suggestions'] ?? '').trim()) || null,\n",
        CODE_IN_JS)
    node["parameters"]["jsCode"] = prepend_filter(code, CODE_IN_JS)


def patch_build_flagged_record(node):
    """Website executions: take the record from 'Website Flagged Record'."""
    code = node["parameters"]["jsCode"]
    code = replace_once(code, "let rec;\ntry {\n  rec = $('reconcile ocr and omr1').first().json;\n", (
        "// website runs: use the record from 'Website Flagged Record'\n"
        "let webRec = null;\n"
        "try {\n"
        "  const w = $('Website Flagged Record').first();\n"
        "  webRec = (w && w.json && w.json._source === 'website') ? w.json : null;\n"
        "} catch (eW) {}\n\n"
        "let rec;\n"
        "if (webRec) {\n"
        "  rec = webRec;\n"
        "} else try {\n"
        "  rec = $('reconcile ocr and omr1').first().json;\n"), BUILD_FLAGGED)
    code = replace_once(code, "if (rec.term_id) record.term_id = rec.term_id;\n", (
        "if (rec.term_id) record.term_id = rec.term_id;\n"
        "// website only\n"
        "if (rec.partial_data) record.partial_data = rec.partial_data;\n"
        "if (rec.batch_id) record.batch_id = rec.batch_id;\n"), BUILD_FLAGGED)
    # Phone: Python/Gemini row agreement (reconcile ocr and omr1) for the reviewer. An extra
    # partial_data key; the Flutter correction screen only reads the keys it knows.
    code = replace_once(code, "    review_note: rec.review_note ?? null,\n  },\n", (
        "    review_note: rec.review_note ?? null,\n"
        "    agreement_percent: rec.agreement_percent ?? null,\n"
        "    ...(rec.remarks_masked === true ? { remarks_masked: true } : {}),\n  },\n"), BUILD_FLAGGED)
    # Every failed_scan_queue row (phone and website) passes here: final sexual-word mask.
    code = replace_once(code, "if (rec.batch_id) record.batch_id = rec.batch_id;\n", (
        "if (rec.batch_id) record.batch_id = rec.batch_id;\n"
        "// last safety net: mask remarks on every queued row\n"
        "if (record.partial_data && typeof record.partial_data.remarks === 'string') {\n"
        "  const maskedRemarks = maskProfanity(record.partial_data.remarks);\n"
        "  if (maskedRemarks !== record.partial_data.remarks) {\n"
        "    record.partial_data = { ...record.partial_data, remarks: maskedRemarks, remarks_masked: true };\n"
        "  }\n"
        "}\n"), BUILD_FLAGGED)
    node["parameters"]["jsCode"] = prepend_filter(code, BUILD_FLAGGED)


def patch_phone_reconcile(node):
    """Phone reconciler: replaced by website_src/phone_reconcile.js (same output fields plus
    agreement_percent / agreement_rows / text_source). Text only from Gemini; the row-agreement
    rule (AGREEMENT_MIN) replaces omr_disagreement / severe_omr_disagreement / low_confidence_omr;
    omr_unavailable when Python returned no ratings; missing_instructor / missing_subject kept."""
    code = node["parameters"]["jsCode"]
    if hashlib.sha256(code.encode("utf-8")).hexdigest() != PHONE_RECONCILE_BASE_SHA256:
        raise SystemExit(f"{PHONE_RECONCILE}: code differs from the original export; the input changed "
                         "(update website_src/phone_reconcile.js and PHONE_RECONCILE_BASE_SHA256)")
    node["parameters"]["jsCode"] = read_js("phone_reconcile.js")


def patch_phone_gemini_failure(node):
    """'Handle Gemini failure' (phone): text comes only from Gemini, so the student id is no
    longer taken from the Python OCR (ocr_fields) when the Gemini call failed."""
    code = node["parameters"]["jsCode"]
    code = replace_once(code, "    student_id: py.ocr_fields?.student_id ?? '',\n",
                        "    student_id: '',   // text only comes from Gemini\n",
                        PHONE_GEMINI_FAILURE)
    node["parameters"]["jsCode"] = code


def patch_sheet_map_to_target(node):
    """Google Sheet rows: carry import_type into the main flow. 'clean data1' sets it, but this
    node builds brand-new result objects and dropped it, so If21 ('import_type is not empty')
    sent every sheet row to the per-remark AI path (If22) instead of the grouped one
    ('Instructor message2'). Only sheet rows get the field: 'Code in JavaScript' maps explicit
    columns (the raw upsert never sees it) and the Set nodes downstream keep only their fields."""
    code = node["parameters"]["jsCode"]
    if "const webhookData = $('Google LINK from phone1').first().json.body;\n" not in code:
        raise SystemExit(f"{SHEET_MAP}: webhookData definition changed; the input changed")
    code = replace_once(code, "  newItems.push({ json: result });\n", (
        "  // lets If21 send sheet rows to the grouped remark AI\n"
        "  result[\"import_type\"] = webhookData.type || \"google_sheet\";\n\n"
        "  newItems.push({ json: result });\n"), SHEET_MAP)
    code = replace_once(
        code,
        '  result["Remarks & Suggestions"] = data["Remarks & Suggestions"] || data["Remarks"] || "";\n',
        '  // mask sexual words in typed remarks\n'
        '  result["Remarks & Suggestions"] = maskProfanity(data["Remarks & Suggestions"] || data["Remarks"] || "");\n',
        SHEET_MAP)
    node["parameters"]["jsCode"] = prepend_filter(code, SHEET_MAP)


# ---------------------------------------------------------------- Google Sheet header matching (2026-10-18)
# The instructor changed the sheet layout. 'Normalize Sheet Rows' (website_src/sheet_normalize.js) sits
# between 'Get row(s) in sheet1' and 'get instructor id5' and matches columns by normalised header, so
# the old and the new layout both reach the unchanged lookups / 'Map to Target Format'. No database change.
SHEET_GET_ROWS = "Get row(s) in sheet1"
SHEET_NORMALIZE = "Normalize Sheet Rows"
SHEET_INSTRUCTOR_LOOKUP = "get instructor id5"
SHEET_SUBJECT_LOOKUP = "get subject id7"


def apply_sheet_header_match(out) -> list[str]:
    """Idempotent (full build and live export). Returns what changed."""
    by_name = {n["name"]: n for n in out["nodes"]}
    for name in (SHEET_GET_ROWS, SHEET_INSTRUCTOR_LOOKUP, SHEET_SUBJECT_LOOKUP, SHEET_MAP):
        if name not in by_name:
            raise SystemExit(f"apply_sheet_header_match: no node named {name!r}")
    done = []
    new_code = read_js("sheet_normalize.js")
    if SHEET_NORMALIZE not in by_name:
        pos = by_name[SHEET_GET_ROWS]["position"]
        node = code_node(SHEET_NORMALIZE, [pos[0] + 224, pos[1] + 176], "sheet_normalize.js")
        out["nodes"].append(node)
        conns = out["connections"]
        old = conns.get(SHEET_GET_ROWS, {}).get("main", [[]])
        if old != [[{"node": SHEET_INSTRUCTOR_LOOKUP, "type": "main", "index": 0}]]:
            raise SystemExit(f"{SHEET_GET_ROWS}: unexpected connections; the input changed")
        conns[SHEET_GET_ROWS]["main"] = [[{"node": SHEET_NORMALIZE, "type": "main", "index": 0}]]
        add_connection(conns, SHEET_NORMALIZE, 0, SHEET_INSTRUCTOR_LOOKUP, 0)
        done.append(f"{SHEET_NORMALIZE}: new node between {SHEET_GET_ROWS} and {SHEET_INSTRUCTOR_LOOKUP}")
    else:
        node = by_name[SHEET_NORMALIZE]
        if node["parameters"]["jsCode"] != new_code:
            node["parameters"]["jsCode"] = new_code
            done.append(f"{SHEET_NORMALIZE}: refreshed from sheet_normalize.js")
    old_ref = "$('Get row(s) in sheet1')"
    new_ref = "$('Normalize Sheet Rows')"
    mp = by_name[SHEET_MAP]["parameters"]
    if old_ref in mp["jsCode"]:
        if mp["jsCode"].count(old_ref + ".all()") != 1:
            raise SystemExit(f"{SHEET_MAP}: sheet reference changed; the input changed")
        mp["jsCode"] = mp["jsCode"].replace(old_ref, new_ref)
        done.append(f"{SHEET_MAP}: reads {SHEET_NORMALIZE}")
    opts = by_name[SHEET_SUBJECT_LOOKUP]["parameters"].setdefault("options", {})
    qr = opts.get("queryReplacement", "")
    if old_ref in qr:
        if qr.count(old_ref) != 1:
            raise SystemExit(f"{SHEET_SUBJECT_LOOKUP}: sheet reference changed; the input changed")
        opts["queryReplacement"] = qr.replace(old_ref, new_ref)
        done.append(f"{SHEET_SUBJECT_LOOKUP}: reads {SHEET_NORMALIZE}")
    return done


# ---------------------------------------------------------------- Google Sheet early answer (2026-10-18)
# 'Google LINK from phone1' used responseMode lastNode: the app's request waited for the whole workflow,
# including the per-instructor Gemini remark loop (6 s Wait1 each), and the proxy returned 504 while n8n
# kept running. Now: responseNode. 'Sheet Import Response' answers right after the rows are read, matched
# and mapped, then 'After Sheet Response' re-emits the mapped rows into If19 (same pattern as
# 'Respond to Webhook5' -> 'After Correction Response'). A failure before that (Google 403, zero rows)
# goes through the error outputs of 'Get row(s) in sheet1' / 'Normalize Sheet Rows' to
# 'Sheet Import Error Response' (HTTP 422). Failures in the database lookups between still give n8n's
# own 500. No Respond node is downstream of If19.
SHEET_HOOK = "Google LINK from phone1"
SHEET_RESPONSE = "Sheet Import Response"
SHEET_AFTER = "After Sheet Response"
SHEET_ERROR_RESPONSE = "Sheet Import Error Response"
SHEET_AFTER_CODE = (
    "// webhook already answered, now pass the mapped sheet rows to If19\n"
    "return $('Map to Target Format').all().map((item) => ({ json: item.json }));\n")
# With 'Create Sheet Import Job' (apply_sheet_import_jobs, migration 018) the answer carries job_id and
# status 'processing': the counts are what was found, not the final result (the app polls the job). No
# job (node missing or its insert failed): the earlier answer, unchanged.
SHEET_RESPONSE_BODY = (
    "={{ (() => {\n"
    "  const rows = $('Map to Target Format').all().map((i) => i.json);\n"
    "  const matched = rows.filter((r) => r.instructor_ID && r.true_subject_id).length;\n"
    "  const unmatched = rows.length - matched;\n"
    "  let jobId = null;\n"
    "  try { const j = $('Create Sheet Import Job').first(); jobId = j && j.json && j.json.id ? String(j.json.id) : null; } catch (e) { jobId = null; }\n"
    "  if (jobId) {\n"
    "    return {\n"
    "      status: 'processing',\n"
    "      job_id: jobId,\n"
    "      final: false,\n"
    "      rows: rows.length,\n"
    "      matched: matched,\n"
    "      unmatched: unmatched,\n"
    "      imported_count: matched,\n"
    "      error_count: unmatched,\n"
    "      unmatched_count: unmatched,\n"
    "      message: 'Found ' + rows.length + ' rows (' + matched + ' matched, ' + unmatched + ' unmatched). "
    "Saving the rows and the AI remark analysis are still running; these numbers are not final.'\n"
    "    };\n"
    "  }\n"
    "  return {\n"
    "    status: 'success',\n"
    "    rows: rows.length,\n"
    "    matched: matched,\n"
    "    unmatched: unmatched,\n"
    "    imported_count: matched,\n"
    "    error_count: unmatched,\n"
    "    unmatched_count: unmatched,\n"
    "    message: 'Imported ' + rows.length + ' rows. Unmatched rows go to Import Errors. "
    "The AI remark analysis continues in the background.'\n"
    "  };\n"
    "})() }}")
SHEET_ERROR_BODY = (
    "={{ (() => {\n"
    "  const raw = $json.error;\n"
    "  const text = String((raw && (raw.message || raw.description)) || raw || '');\n"
    "  let message = 'The sheet could not be read: ' + (text || 'unknown error');\n"
    "  if (/403|permission|forbidden|not have access/i.test(text)) {\n"
    "    message = 'Google denied access to the sheet. Share the sheet with the service account email as Viewer, then import again.';\n"
    "  } else if (/404|not found/i.test(text)) {\n"
    "    message = 'The sheet was not found. Check the link and the tab name (SAST Survey Data).';\n"
    "  } else if (/no data rows/i.test(text)) {\n"
    "    message = 'The sheet has no data rows (tab SAST Survey Data).';\n"
    "  }\n"
    "  return { status: 'error', error: message, message: message };\n"
    "})() }}")


def apply_sheet_early_response(out) -> list[str]:
    """Idempotent. Returns what changed."""
    by_name = {n["name"]: n for n in out["nodes"]}
    for name in (SHEET_HOOK, SHEET_MAP, SHARED_ENTRY_CLEAN, SHEET_GET_ROWS, SHEET_NORMALIZE):
        if name not in by_name:
            raise SystemExit(f"apply_sheet_early_response: no node named {name!r}")
    conns = out["connections"]
    done = []
    hook = by_name[SHEET_HOOK]["parameters"]
    if hook.get("responseMode") != "responseNode":
        hook["responseMode"] = "responseNode"
        done.append(f"{SHEET_HOOK}: responseMode responseNode")
    mp = by_name[SHEET_MAP]["position"]
    if SHEET_RESPONSE not in by_name:
        out["nodes"].append({
            "parameters": {"respondWith": "json", "responseBody": SHEET_RESPONSE_BODY,
                           "options": {"responseCode": 200}},
            "type": "n8n-nodes-base.respondToWebhook", "typeVersion": 1.5,
            "position": [mp[0] + 224, mp[1] + 176], "id": nid(SHEET_RESPONSE), "name": SHEET_RESPONSE})
        out["nodes"].append({
            "parameters": {"jsCode": SHEET_AFTER_CODE}, "type": "n8n-nodes-base.code", "typeVersion": 2,
            "position": [mp[0] + 448, mp[1] + 176], "id": nid(SHEET_AFTER), "name": SHEET_AFTER})
        out["nodes"].append({
            "parameters": {"respondWith": "json", "responseBody": SHEET_ERROR_BODY,
                           "options": {"responseCode": 422}},
            "type": "n8n-nodes-base.respondToWebhook", "typeVersion": 1.5,
            "position": [mp[0] - 448, mp[1] + 352], "id": nid(SHEET_ERROR_RESPONSE),
            "name": SHEET_ERROR_RESPONSE})
        main = conns.get(SHEET_MAP, {}).get("main", [])
        if len(main) != 1 or [c["node"] for c in main[0]] != [SHARED_ENTRY_CLEAN]:
            raise SystemExit(f"{SHEET_MAP}: expected a single connection to {SHARED_ENTRY_CLEAN}; the input changed")
        conns[SHEET_MAP]["main"] = [[]]
        add_connection(conns, SHEET_MAP, 0, SHEET_RESPONSE, 0)
        add_connection(conns, SHEET_RESPONSE, 0, SHEET_AFTER, 0)
        add_connection(conns, SHEET_AFTER, 0, SHARED_ENTRY_CLEAN, 0)
        done.append(f"{SHEET_MAP} -> {SHEET_RESPONSE} -> {SHEET_AFTER} -> {SHARED_ENTRY_CLEAN}")
    else:
        by_name[SHEET_RESPONSE]["parameters"]["responseBody"] = SHEET_RESPONSE_BODY
        by_name[SHEET_ERROR_RESPONSE]["parameters"]["responseBody"] = SHEET_ERROR_BODY
        by_name[SHEET_AFTER]["parameters"]["jsCode"] = SHEET_AFTER_CODE
    # error outputs -> clear 422 answer; zero rows: Get rows emits one empty item, Normalize throws
    get_rows = by_name[SHEET_GET_ROWS]
    if not get_rows.get("alwaysOutputData"):
        get_rows["alwaysOutputData"] = True
        done.append(f"{SHEET_GET_ROWS}: alwaysOutputData (zero rows reach {SHEET_NORMALIZE})")
    for name in (SHEET_GET_ROWS, SHEET_NORMALIZE):
        n = by_name[name]
        if n.get("onError") != "continueErrorOutput":
            n["onError"] = "continueErrorOutput"
            done.append(f"{name}: onError continueErrorOutput")
        main = conns.setdefault(name, {}).setdefault("main", [[]])
        while len(main) < 2:
            main.append([])
        if not any(c["node"] == SHEET_ERROR_RESPONSE for c in main[1]):
            main[1].append({"node": SHEET_ERROR_RESPONSE, "type": "main", "index": 0})
            done.append(f"{name} error output -> {SHEET_ERROR_RESPONSE}")
    return done


# ---------------------------------------------------------------- Google Sheet import job (2026-10-18)
# The early answer made the app say "finished" while n8n was still saving rows and running the Gemini
# remark loop. Now the import writes public.sheet_import_jobs (migration 018) and the app polls it:
#   Map to Target Format -> 'Create Sheet Import Job' (status processing, $execution.id) -> Sheet Import
#   Response (+ job_id) -> After Sheet Response -> If19 ... and, as After Sheet Response's SECOND child,
#   placed BELOW If19: 'Sheet Import Summary' -> 'Mark Import Job Done' (finished_at + real counts).
#   executionOrder v1 runs a node's children top to bottom and depth first, so the whole If19 branch
#   (raw upsert loop, recompute, import_errors, the paced remark loop incl. Wait1) ends before it.
# Error outputs after the answer -> 'Describe Import Failure' -> 'Mark Import Job Failed' (status
# error, first failed node + message). Wait1 keeps every feed it had (insert data to student remarks2
# now has an error output, and it goes to Wait1 too), so the paced loop still advances once per group.
# A node without an error output that throws, Stop and Error, or a timeout fails the execution: the
# workflow's own 'Import Job Error Trigger' (n8n runs a failed workflow's Error Trigger when no other
# error workflow is set) -> 'Mark Import Job Stopped' (status error, failed_node = the last node).
SHEET_JOB_CREATE = "Create Sheet Import Job"
SHEET_JOB_SUMMARY = "Sheet Import Summary"
SHEET_JOB_DONE = "Mark Import Job Done"
SHEET_JOB_DESCRIBE = "Describe Import Failure"
SHEET_JOB_FAILED = "Mark Import Job Failed"
SHEET_JOB_TRIGGER = "Import Job Error Trigger"
SHEET_JOB_STOPPED = "Mark Import Job Stopped"
SHEET_IF19 = "If19"
# (node, output that carries the failure, onError it needs or None = keep, its error output also feeds Wait1)
SHEET_JOB_FAIL_SOURCES = [
    (UNIFIED_AI, 1, None, True),
    (UNIFIED_PARSE, 1, None, True),
    ("insert data to student remarks2", 1, "continueErrorOutput", True),
    ("store results2", 1, "continueErrorOutput", False),
    ("insert to word cloud2", 1, "continueErrorOutput", False),
    ("update to overall total2", 1, "continueErrorOutput", False),
    ("Insert import_errors1", 1, None, False),
    ("If14", 0, None, False),   # If14 true = a raw row came back from the upsert with .error
]
_UUID_RE_JS = r"/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i"
SHEET_JOB_CREATE_PARAMS = (
    "={{ (() => {\n"
    "  const rows = $('Map to Target Format').all().map((i) => i.json);\n"
    "  const matched = rows.filter((r) => r.instructor_ID && r.true_subject_id).length;\n"
    "  const hook = $('Google LINK from phone1').first().json || {};\n"
    "  const uid = String((hook.body || {}).user_id || '');\n"
    "  return [rows.length, matched, rows.length - matched, String($execution.id || ''), "
    + _UUID_RE_JS + ".test(uid) ? uid : ''];\n"
    "})() }}")
SHEET_JOB_FAILED_PARAMS = "={{ [ String($execution.id || ''), String($json.failed_node || ''), String($json.message || '') ] }}"
SHEET_JOB_DONE_PARAMS = ("={{ [ $json.job_id, $json.rows, $json.matched, $json.unmatched, $json.imported_count, "
                         "$json.error_count, $json.ai_groups, $json.ai_failed, $json.has_problems ? true : false, "
                         "String($json.first_failed_node || ''), String($json.message || '') ] }}")
SHEET_JOB_STOPPED_PARAMS = (
    "={{ (() => {\n"
    "  const ex = $json.execution || {};\n"
    "  const node = String(ex.lastNodeExecuted || '');\n"
    "  const err = ex.error || {};\n"
    "  const text = String(err.message || err.description || 'unknown error').replace(/\\s+/g, ' ').slice(0, 400);\n"
    "  return [String(ex.id || ''), node, 'The import stopped at step \"' + (node || 'unknown') + '\": ' + text + "
    "'. Rows saved before that step are kept. Check Import Errors and the dashboard before importing this sheet again.'];\n"
    "})() }}")


def _sheet_job_order(by_name) -> list[str]:
    """Positions that the execution order depends on (executionOrder v1: siblings run top first).
    'Sheet Import Summary' must be BELOW If19 (it runs after the whole If19 branch); 'Describe Import
    Failure' ABOVE Wait1 (the failure is recorded before the loop goes on). Moves only what is wrong."""
    done = []
    if19 = by_name[SHEET_IF19]["position"]
    for name, dx in ((SHEET_JOB_SUMMARY, 0), (SHEET_JOB_DONE, 224)):
        n = by_name.get(name)
        if n and n["position"][1] <= if19[1]:
            base = by_name[SHEET_AFTER]["position"]
            n["position"] = [base[0] + 224 + dx, max(base[1], if19[1]) + 224]
            done.append(f"{name} moved below {SHEET_IF19}")
    wait = by_name[REMARK_LOOP_WAIT]["position"]
    for name, dx in ((SHEET_JOB_DESCRIBE, 224), (SHEET_JOB_FAILED, 448)):
        n = by_name.get(name)
        if n and n["position"][1] >= wait[1]:
            n["position"] = [wait[0] + dx, wait[1] - 560]
            done.append(f"{name} moved above {REMARK_LOOP_WAIT}")
    return done


def apply_sheet_import_jobs(out) -> list[str]:
    """Idempotent (full build and live export; after apply_sheet_early_response and
    apply_gemini_rate_limit). Returns what changed."""
    by_name = {n["name"]: n for n in out["nodes"]}
    for name in (SHEET_MAP, SHEET_RESPONSE, SHEET_AFTER, SHEET_IF19, REMARK_LOOP_WAIT,
                 *[s[0] for s in SHEET_JOB_FAIL_SOURCES]):
        if name not in by_name:
            raise SystemExit(f"apply_sheet_import_jobs: no node named {name!r}")
    if (out.get("settings") or {}).get("executionOrder") != "v1":
        raise SystemExit("apply_sheet_import_jobs: needs settings.executionOrder v1 (the done marker runs last)")
    if (out.get("settings") or {}).get("errorWorkflow"):
        raise SystemExit("apply_sheet_import_jobs: an error workflow is set; the own Error Trigger would never run")
    conns = out["connections"]
    done = []

    def ensure_pg(name, pos, sql_file, params, execute_once=True):
        sql = read(sql_file)
        n = by_name.get(name)
        if n is None:
            n = pg_node(name, pos, sql, params, "continueRegularOutput")
            if execute_once:
                n["executeOnce"] = True
            out["nodes"].append(n)
            by_name[name] = n
            done.append(f"{name}: new node ({sql_file})")
            return n
        assert_no_param_in_sql_comments(name, sql)
        p = n["parameters"]
        if p.get("query") != sql or p.get("options", {}).get("queryReplacement") != params:
            p["query"] = sql
            p.setdefault("options", {})["queryReplacement"] = params
            done.append(f"{name}: refreshed from {sql_file}")
        if execute_once and not n.get("executeOnce"):
            n["executeOnce"] = True
            done.append(f"{name}: executeOnce")
        if n.get("onError") != "continueRegularOutput":
            n["onError"] = "continueRegularOutput"
            done.append(f"{name}: onError continueRegularOutput")
        return n

    def ensure_code(name, pos, src_file):
        code = read_js(src_file)
        n = by_name.get(name)
        if n is None:
            n = code_node(name, pos, src_file, "continueRegularOutput")
            out["nodes"].append(n)
            by_name[name] = n
            done.append(f"{name}: new node ({src_file})")
        elif n["parameters"].get("jsCode") != code:
            n["parameters"]["jsCode"] = code
            done.append(f"{name}: refreshed from {src_file}")
        return n

    def connect(src, idx, dst):
        before = json.dumps(conns.get(src), sort_keys=True)
        add_connection(conns, src, idx, dst, 0)
        if json.dumps(conns.get(src), sort_keys=True) != before:
            done.append(f"{src}[{idx}] -> {dst}")

    # 1. the job row, before the early answer
    mp = by_name[SHEET_MAP]["position"]
    ensure_pg(SHEET_JOB_CREATE, [mp[0] + 112, mp[1] + 352], "sheet_job_create.sql", SHEET_JOB_CREATE_PARAMS)
    if targets_of(conns, SHEET_MAP, 0) == [SHEET_RESPONSE]:
        conns[SHEET_MAP]["main"][0] = []
        done.append(f"{SHEET_MAP} -> {SHEET_JOB_CREATE} -> {SHEET_RESPONSE}")
    elif targets_of(conns, SHEET_MAP, 0) != [SHEET_JOB_CREATE]:
        raise SystemExit(f"{SHEET_MAP}: expected one connection to {SHEET_RESPONSE}; the input changed")
    add_connection(conns, SHEET_MAP, 0, SHEET_JOB_CREATE, 0)
    connect(SHEET_JOB_CREATE, 0, SHEET_RESPONSE)
    if targets_of(conns, SHEET_JOB_CREATE, 0) != [SHEET_RESPONSE]:
        raise SystemExit(f"{SHEET_JOB_CREATE}: unexpected connections; the input changed")

    # 2. the done marker: After Sheet Response's second child, below If19
    if targets_of(conns, SHEET_AFTER, 0) not in ([SHEET_IF19], [SHEET_IF19, SHEET_JOB_SUMMARY]):
        raise SystemExit(f"{SHEET_AFTER}: unexpected connections; the input changed")
    ap, if19 = by_name[SHEET_AFTER]["position"], by_name[SHEET_IF19]["position"]
    low = max(ap[1], if19[1]) + 224
    ensure_code(SHEET_JOB_SUMMARY, [ap[0] + 224, low], "sheet_job_summary.js")
    ensure_pg(SHEET_JOB_DONE, [ap[0] + 448, low], "sheet_job_done.sql", SHEET_JOB_DONE_PARAMS)
    connect(SHEET_AFTER, 0, SHEET_JOB_SUMMARY)
    connect(SHEET_JOB_SUMMARY, 0, SHEET_JOB_DONE)

    # 3. failures after the answer that do not stop the import
    wp = by_name[REMARK_LOOP_WAIT]["position"]
    ensure_code(SHEET_JOB_DESCRIBE, [wp[0] + 224, wp[1] - 560], "sheet_job_failure.js")
    ensure_pg(SHEET_JOB_FAILED, [wp[0] + 448, wp[1] - 560], "sheet_job_failed.sql", SHEET_JOB_FAILED_PARAMS)
    connect(SHEET_JOB_DESCRIBE, 0, SHEET_JOB_FAILED)
    for src, idx, mode, feeds_wait in SHEET_JOB_FAIL_SOURCES:
        n = by_name[src]
        if mode and n.get("onError") != mode:
            if idx == 1 and targets_of(conns, src, 1):
                raise SystemExit(f"{src}: output 1 already used; the input changed")
            n["onError"] = mode
            done.append(f"{src}: onError {mode}")
        if idx == 1 and n["type"] != "n8n-nodes-base.if" and n.get("onError") != "continueErrorOutput":
            raise SystemExit(f"{src}: has no error output; the input changed")
        if feeds_wait:
            connect(src, idx, REMARK_LOOP_WAIT)
        connect(src, idx, SHEET_JOB_DESCRIBE)

    # 4. the execution itself failed: this workflow's own Error Trigger
    if SHEET_JOB_TRIGGER not in by_name:
        n = {"parameters": {}, "type": "n8n-nodes-base.errorTrigger", "typeVersion": 1,
             "position": [ap[0], low + 224], "id": nid(SHEET_JOB_TRIGGER), "name": SHEET_JOB_TRIGGER}
        out["nodes"].append(n)
        by_name[SHEET_JOB_TRIGGER] = n
        done.append(f"{SHEET_JOB_TRIGGER}: new node")
    ensure_pg(SHEET_JOB_STOPPED, [ap[0] + 224, low + 224], "sheet_job_stopped.sql", SHEET_JOB_STOPPED_PARAMS)
    connect(SHEET_JOB_TRIGGER, 0, SHEET_JOB_STOPPED)

    done += _sheet_job_order(by_name)
    return done


def targets_of(conns, src, idx):
    outs = conns.get(src, {}).get("main", [])
    return [c["node"] for c in (outs[idx] if idx < len(outs) and outs[idx] else [])]


# ---------------------------------------------------------------- placeholder comments -> NULL (2026-10-18)
# 'N/A', 'none', ... are not comments: the raw row stores NULL (Code in JavaScript, Website Decide) and the
# remark AI paths never see them (so student_remarks gets no "N/A" row). website_src/remark_clean.js.
REMARK_CLEAN_MARK = "// remark placeholders, from website_src/remark_clean.js"
REMARK_CLEAN_EDITS = [
    (CODE_IN_JS, "maskProfanity(String(r['Remarks & Suggestions'] ?? '').trim()) || null",
     "maskProfanity(cleanRemark(r['Remarks & Suggestions']) || '') || null"),
    ("Group feedback by instructor1", 'String(\n      data["Remarks & Suggestions"] ??\n      data.remark ??\n      ""\n    ).trim()',
     'cleanRemark(\n      data["Remarks & Suggestions"] ??\n      data.remark ??\n      ""\n    ) || ""'),
    ("Prepare New Student Data1", 'String(\n      data["Remarks & Suggestions"] || ""\n    ).trim()',
     'cleanRemark(\n      data["Remarks & Suggestions"] || ""\n    ) || ""'),
]


def apply_remark_placeholders(out) -> list[str]:
    """Idempotent (full build and live export). Website Decide / Normalize Sheet Rows get the helper from
    their sources (refreshed elsewhere). Returns what changed."""
    by_name = {n["name"]: n for n in out["nodes"]}
    done = []
    for name, old, new in REMARK_CLEAN_EDITS:
        if name not in by_name:
            raise SystemExit(f"apply_remark_placeholders: no node named {name!r}")
        code = by_name[name]["parameters"]["jsCode"]
        if REMARK_CLEAN_MARK in code:
            continue
        code = replace_once(code, old, new, name)
        by_name[name]["parameters"]["jsCode"] = helper_block("remark_clean.js") + "\n" + code
        done.append(f"{name}: placeholder comments -> blank/NULL")
    wd = by_name.get("Website Decide")
    if wd is not None and wd["parameters"]["jsCode"] != read_js("decide.js"):
        raise SystemExit("Website Decide: not the current decide.js; run the stamp refresh first")
    return done


def patch_correction_map(node):
    """'Map target' (sast-manual-correction) / 'Map target1' (import-error-correction): the
    corrected remark is typed text -> deterministic sexual-word mask (no image re-check). These
    items feed If19 -> the raw upsert, the remark AI (If21) and, unresolved, import_errors."""
    code = node["parameters"]["jsCode"]
    name = node["name"]
    if name == "Map target":
        for old in ("    'Remarks & Suggestions': data.raw.Remarks_and_Suggestions ?? '',\n",
                    "      Remarks_and_Suggestions: data.raw.Remarks_and_Suggestions ?? '',\n"):
            key, val = old.rstrip(",\n").split(": ", 1)
            code = replace_once(code, old, f"{key}: maskProfanity({val}),\n", name)
    else:
        code = replace_once(code, "    'Remarks & Suggestions': raw['Remarks & Suggestions'] || raw.remarks || '',\n",
                            "    'Remarks & Suggestions': maskProfanity(raw['Remarks & Suggestions'] || raw.remarks || ''),\n",
                            name)
    node["parameters"]["jsCode"] = prepend_filter(code, name)


def patch_import_error_format(node):
    """'Format error for import_errors': every import_errors.raw_data passes here."""
    code = replace_once(node["parameters"]["jsCode"],
                        "        'Remarks & Suggestions': j['Remarks & Suggestions'],\n",
                        "        'Remarks & Suggestions': maskProfanity(j['Remarks & Suggestions']),\n",
                        IMPORT_ERROR_FORMAT)
    node["parameters"]["jsCode"] = prepend_filter(code, IMPORT_ERROR_FORMAT)


MASK_AI_TEXT_UNIFIED = (
    "// mask sexual words in the AI output too\n"
    "for (const k of Object.keys(evaluation)) evaluation[k] = maskProfanity(evaluation[k]);\n\n")
MASK_AI_TEXT_INDIVIDUAL = (
    "// mask sexual words in the AI output too\n"
    "for (const k of ['ai_suggestion', 'student_interpretation', 'positive_themes', 'improvement_insights']) {\n"
    "  if (typeof result[k] === 'string') result[k] = maskProfanity(result[k]);\n"
    "  else if (Array.isArray(result[k])) result[k] = result[k].map((v) => (typeof v === 'string' ? maskProfanity(v) : v));\n"
    "}\n\n")


def patch_unified_parse(node):
    """'Parse Unified AI Result1' (grouped remark AI): drop a wordcloud keyword that contains a
    sexual word or a masked one; mask the stored remark and the AI texts (belt and braces)."""
    code = node["parameters"]["jsCode"]
    code = replace_once(code, "  if (!word) continue;\n", (
        "  if (!word) continue;\n"
        "  // skip keywords with sexual or masked words\n"
        "  if (profaneKeyword(word)) continue;\n"), UNIFIED_PARSE)
    code = replace_once(code, '        String(\n          item.remark ?? ""\n        ).trim(),\n',
                        '        maskProfanity(String(\n          item.remark ?? ""\n        ).trim()),\n', UNIFIED_PARSE)
    code = replace_once(code, "if (!evaluation.interpretation) {\n",
                        MASK_AI_TEXT_UNIFIED + "if (!evaluation.interpretation) {\n", UNIFIED_PARSE)
    node["parameters"]["jsCode"] = prepend_filter(code, UNIFIED_PARSE)


def patch_individual_parse(node):
    """'Add Parse Code node1' (per-remark AI): same as the unified parser. Keywords are filtered
    BEFORE the 'at least one keyword' fallback, so a dropped keyword still leaves a theme."""
    code = node["parameters"]["jsCode"]
    code = replace_once(code, "let keywords = result.keywords || [];\n", (
        "let keywords = result.keywords || [];\n"
        "// skip keywords with sexual or masked words\n"
        "if (Array.isArray(keywords)) keywords = keywords.filter((k) => !(k && profaneKeyword(String(k.word ?? ''))));\n"),
        INDIVIDUAL_PARSE)
    code = replace_once(code, "    remark: original.new_student_remark,\n",
                        "    remark: maskProfanity(original.new_student_remark),\n", INDIVIDUAL_PARSE)
    anchor = 'output.push({\n  json: {\n    record_type: "evaluation",\n'
    code = replace_once(code, anchor, MASK_AI_TEXT_INDIVIDUAL + anchor, INDIVIDUAL_PARSE)
    node["parameters"]["jsCode"] = prepend_filter(code, INDIVIDUAL_PARSE)


def patch_remark_ai_prompt(node, anchor):
    """Remark AI prompts: never a vulgar/sexual or masked keyword (the parsers drop them anyway)."""
    node["parameters"]["text"] = replace_once(node["parameters"]["text"], anchor, anchor + "\n" + KEYWORD_RULE,
                                              node["name"])


def add_phone_remark_check(out):
    """Phone scans: 'Is not a SAST form?' (false) -> 'Phone Remark Check' -> 'Phone Remark Needs
    Check?' -> (true) 'Phone Verify Remark Words' -> 'reconcile ocr and omr1'; (false) straight to
    the reconciler. The reconciler reads every node by name, so its input does not matter."""
    conns = out["connections"]
    by_name = {n["name"]: n for n in out["nodes"]}
    outs = conns[PHONE_NOT_FORM_NODE]["main"]
    if [c["node"] for c in outs[1]] != [PHONE_RECONCILE]:
        raise SystemExit(f"{PHONE_NOT_FORM_NODE}[1] is not just {PHONE_RECONCILE}: {outs[1]}; the input changed")
    outs[1] = []
    out["nodes"].append(code_node(PHONE_REMARK_CHECK, [-44656, 20208], "phone_remark_check.js", "continueRegularOutput"))
    out["nodes"].append(if_node(PHONE_REMARK_IF, [-44544, 20048], "={{ $json.remark_check_needed === true }}",
                                "8f0b6c1e-web0-4d01-9a01-000000000008"))
    out["nodes"].append(verify_node(PHONE_REMARK_VERIFY, [-44432, 20208], by_name[PHONE_GEMINI_NODE], "'image/jpeg'",
                                    "String($('SAST image from phone').first().json.body.image || '')"
                                    ".replace(/^data:[^,]*,/, '')", PHONE_VERIFY_TIMEOUT_MS))
    add_connection(conns, PHONE_NOT_FORM_NODE, 1, PHONE_REMARK_CHECK, 0)
    add_connection(conns, PHONE_REMARK_CHECK, 0, PHONE_REMARK_IF, 0)
    add_connection(conns, PHONE_REMARK_IF, 0, PHONE_REMARK_VERIFY, 0)
    add_connection(conns, PHONE_REMARK_IF, 1, PHONE_RECONCILE, 0)
    add_connection(conns, PHONE_REMARK_VERIFY, 0, PHONE_RECONCILE, 0)


def add_phone_known_names(out):
    """Phone scans: 'image to ocr and omr python' -> 'Phone Known Names' -> the Gemini call, whose
    prompt lists the known subjects (no instructor names). The Gemini body reads the phone image with
    .first() (one scan per execution) instead of .item, so the extra node never breaks item pairing."""
    conns = out["connections"]
    by_name = {n["name"]: n for n in out["nodes"]}
    outs = conns[PHONE_OMR_NODE]["main"]
    if len(outs) != 1 or [c["node"] for c in outs[0]] != [PHONE_GEMINI_NODE]:
        raise SystemExit(f"{PHONE_OMR_NODE} does not feed only {PHONE_GEMINI_NODE}: {outs}; the input changed")
    outs[0] = []
    a, b = by_name[PHONE_OMR_NODE]["position"], by_name[PHONE_GEMINI_NODE]["position"]
    out["nodes"].append(known_names_node(PHONE_KNOWN_NAMES, [(a[0] + b[0]) // 2, max(a[1], b[1]) + 176]))
    add_connection(conns, PHONE_OMR_NODE, 0, PHONE_KNOWN_NAMES, 0)
    add_connection(conns, PHONE_KNOWN_NAMES, 0, PHONE_GEMINI_NODE, 0)
    gem = by_name[PHONE_GEMINI_NODE]["parameters"]
    body = replace_once(gem["jsonBody"], PHONE_IMAGE_ITEM, PHONE_IMAGE_FIRST, PHONE_GEMINI_NODE)
    gem["jsonBody"] = patch_gemini_known(body, PHONE_KNOWN_NAMES, PHONE_GEMINI_NODE)


def add_phone_instructor_candidates(out):
    """Phone scans: 'Is not a SAST form?' (false) -> 'Phone Instructor Candidates' -> 'Phone Remark
    Check'. After the Gemini call, so the list can never be put into a prompt; the remark check and
    the reconciler read their inputs by node name, so the extra node changes nothing else."""
    conns = out["connections"]
    by_name = {n["name"]: n for n in out["nodes"]}
    outs = conns[PHONE_NOT_FORM_NODE]["main"]
    if len(outs) < 2 or [c["node"] for c in outs[1]] != [PHONE_REMARK_CHECK]:
        raise SystemExit(f"{PHONE_NOT_FORM_NODE}[1] is not just {PHONE_REMARK_CHECK}: {outs}; the input changed")
    outs[1] = []
    a, b = by_name[PHONE_NOT_FORM_NODE]["position"], by_name[PHONE_REMARK_CHECK]["position"]
    out["nodes"].append(phone_candidates_node(PHONE_CANDIDATES, [(a[0] + b[0]) // 2, max(a[1], b[1]) + 176]))
    add_connection(conns, PHONE_NOT_FORM_NODE, 1, PHONE_CANDIDATES, 0)
    add_connection(conns, PHONE_CANDIDATES, 0, PHONE_REMARK_CHECK, 0)


def refresh_helper_copies(out, name: str) -> list[str]:
    """Replace every copy of a shared helper (website_src/<name>, between its marker lines) in the
    Code nodes with the current source. Returns the node names that changed."""
    title = HELPER_HEADERS[name]
    start, end = f"// {title}, from website_src/{name}\n", f"// end of {title}\n"
    changed = []
    for n in out["nodes"]:
        code = n.get("parameters", {}).get("jsCode")
        if not isinstance(code, str) or start not in code:
            continue
        i = code.index(start)
        j = code.index(end, i) + len(end)
        new = code[:i] + helper_block(name) + code[j:]
        if new != code:
            n["parameters"]["jsCode"] = new
            changed.append(n["name"])
    return changed


def assert_no_instructor_names_to_gemini(out):
    """Privacy: no Gemini request (HTTP body or LLM chain prompt) may carry an instructor list or
    read a node that returns instructor names."""
    for n in out["nodes"]:
        p = n.get("parameters", {})
        is_gemini = ("generativelanguage.googleapis.com" in str(p.get("url", ""))
                     or n.get("type", "").startswith("@n8n/n8n-nodes-langchain."))
        if not is_gemini:
            continue
        text = json.dumps(p, ensure_ascii=False)
        hits = [m for m in INSTRUCTOR_LIST_MARKERS if m in text or json.dumps(m)[1:-1] in text]
        if hits:
            raise SystemExit(f"{n['name']}: Gemini request carries instructor names ({hits}); privacy rule")


def apply_phone_name_privacy(out) -> list[str]:
    """No instructor names to Gemini on the phone branch (idempotent; works on the full build and
    on a live export of an earlier build). Returns what changed."""
    by_name = {n["name"]: n for n in out["nodes"]}
    done = []
    kn = by_name[PHONE_KNOWN_NAMES]
    if kn["parameters"].get("query") != read("known_names.sql") or kn["parameters"].get("options"):
        assert_no_param_in_sql_comments(PHONE_KNOWN_NAMES, read("known_names.sql"))
        kn["parameters"]["query"] = read("known_names.sql")
        kn["parameters"]["options"] = {}
        done.append(f"{PHONE_KNOWN_NAMES}: known_names.sql (subjects only, no parameters)")
    gem = by_name[PHONE_GEMINI_NODE]["parameters"]
    body = gem["jsonBody"]
    if OLD_PHONE_KNOWN_SCHEMA in body or "Known instructors" in body:
        if len(OLD_PHONE_KNOWN_EXPR_RE.findall(body)) != 1:
            raise SystemExit(f"{PHONE_GEMINI_NODE}: old known-names expression not found exactly once; the input changed")
        body = OLD_PHONE_KNOWN_EXPR_RE.sub(lambda m: known_names_expression(PHONE_KNOWN_NAMES), body)
        body = replace_once(body, OLD_PHONE_KNOWN_SCHEMA, "", PHONE_GEMINI_NODE)
        gem["jsonBody"] = body
        done.append(f"{PHONE_GEMINI_NODE}: subject list only, no known_instructor")
    rec = by_name[PHONE_RECONCILE]["parameters"]
    new_rec = read_js("phone_reconcile.js")
    if rec["jsCode"] != new_rec:
        if OLD_PHONE_RECONCILE_MARK not in rec["jsCode"] and PRIVACY_PHONE_RECONCILE_MARK not in rec["jsCode"]:
            raise SystemExit(f"{PHONE_RECONCILE}: neither a previous build nor phone_reconcile.js (hand edit?)")
        rec["jsCode"] = new_rec
        done.append(f"{PHONE_RECONCILE}: phone_reconcile.js (local instructor match, check-only rule)")
    changed = [n for n in refresh_helper_copies(out, "name_match.js") if n != PHONE_RECONCILE]
    if changed:
        done.append(f"name_match.js refreshed in {', '.join(changed)}")
    if PHONE_CANDIDATES not in by_name:
        add_phone_instructor_candidates(out)
        done.append(f"added {PHONE_CANDIDATES}: {PHONE_NOT_FORM_NODE}[false] -> {PHONE_CANDIDATES} -> {PHONE_REMARK_CHECK}")
    assert_no_instructor_names_to_gemini(out)
    return done


def apply_check_only_rule(out) -> list[str]:
    """Check-only rule (idempotent; full build and live export): one line in both main Gemini prompts,
    and the website reconciler refreshed from website_src/reconcile.js (the phone one is refreshed by
    apply_phone_name_privacy). Returns what changed."""
    by_name = {n["name"]: n for n in out["nodes"]}
    done = []
    for name in (PHONE_GEMINI_NODE, WEB_GEMINI_NODE):
        gem = by_name[name]["parameters"]
        if CHECK_ONLY_LINE not in gem["jsonBody"]:
            gem["jsonBody"] = replace_once(gem["jsonBody"], CHECK_ONLY_ANCHOR,
                                           CHECK_ONLY_LINE + r"\n" + CHECK_ONLY_ANCHOR, name)
            done.append(f"{name}: check-only prompt line")
    rec = by_name[WEB_RECONCILE]["parameters"]
    new_rec = read_js("reconcile.js")
    if rec["jsCode"] != new_rec:
        if not rec["jsCode"].startswith(WEB_RECONCILE_MARK):
            raise SystemExit(f"{WEB_RECONCILE}: not a previous build of reconcile.js (hand edit?)")
        rec["jsCode"] = new_rec
        done.append(f"{WEB_RECONCILE}: reconcile.js (check-only rule)")
    if CHECK_ONLY_LINE not in by_name[PHONE_GEMINI_NODE]["parameters"]["jsonBody"]:
        raise SystemExit("check-only prompt line missing")
    assert_no_instructor_names_to_gemini(out)
    return done


def apply_min_answers_rule(out) -> list[str]:
    """Min-answered rule (idempotent; full build and live export): a form with fewer than 5 of the
    20 items answered on a reliable scan is stored in failed_scan_queue already discarded. The two
    reconcilers (refreshed by apply_check_only_rule / apply_phone_name_privacy) set auto_discard;
    here: 'Website Flagged Record' passes it on, 'build flagged record1' stores the row discarded,
    the website answer says so, and the staff push is skipped. Returns what changed."""
    by_name = {n["name"]: n for n in out["nodes"]}
    done = []
    wfr = by_name[WEB_FLAGGED_RECORD]["parameters"]
    new_wfr = read_js("flagged_record.js")
    if wfr["jsCode"] != new_wfr:
        if not wfr["jsCode"].startswith(WEB_FLAGGED_RECORD_MARK):
            raise SystemExit(f"{WEB_FLAGGED_RECORD}: not a previous build of flagged_record.js (hand edit?)")
        wfr["jsCode"] = new_wfr
        done.append(f"{WEB_FLAGGED_RECORD}: flagged_record.js (auto_discard, answered_count)")
    bf = by_name[BUILD_FLAGGED]["parameters"]
    if MIN_ANSWERS_MARK not in bf["jsCode"]:
        if bf["jsCode"].count(MIN_ANSWERS_ANCHOR) != 1:
            raise SystemExit(f"{BUILD_FLAGGED}: return line not found exactly once; the input changed")
        bf["jsCode"] = bf["jsCode"].replace(MIN_ANSWERS_ANCHOR, MIN_ANSWERS_BLOCK + MIN_ANSWERS_ANCHOR)
        done.append(f"{BUILD_FLAGGED}: auto_discard -> status 'discarded'")
    resp = by_name[FLAGGED_RESPOND]["parameters"]
    new_resp = flagged_respond_expression()
    if resp.get("responseBody") != new_resp:
        body = str(resp.get("responseBody", ""))
        if "$('Website Flagged Record')" not in body or "Flagged for manual review" not in body:
            raise SystemExit(f"{FLAGGED_RESPOND}: not a previous build of the website contract (hand edit?)")
        resp["responseBody"] = new_resp
        done.append(f"{FLAGGED_RESPOND}: 'Discarded automatically' message")
    opts = by_name[FLAGGED_FCM]["parameters"].setdefault("options", {})
    if opts.get("queryReplacement") != FLAGGED_FCM_PARAMS:
        if opts.get("queryReplacement") != FLAGGED_FCM_PARAMS_V1:
            raise SystemExit(f"{FLAGGED_FCM}: unexpected parameters; the input changed")
        opts["queryReplacement"] = FLAGGED_FCM_PARAMS
        done.append(f"{FLAGGED_FCM}: no push for a discarded form")
    for name, src in ((WEB_RECONCILE, "reconcile.js"), (PHONE_RECONCILE, "phone_reconcile.js")):
        if by_name[name]["parameters"]["jsCode"] != read_js(src):
            raise SystemExit(f"{name}: not the current {src}; run the reconciler refresh first")
    return done


# Word cloud words (apply_wordcloud_words): real word counts computed in code, added next to the AI themes.
GET_PREVIOUS_ANALYSIS = "Get Previous Instructor Analysis1"
PREPARE_UNIFIED_INPUT = "Prepare Unified AI Input1"
GET_OLD_WORDCLOUD = "Get the old wordcloud1"
PREPARE_EXISTING_WORDCLOUD = "Prepare Existing Wordcloud1"
PREPARE_INDIVIDUAL_INPUT = "Prepare Individual AI Input1"
WORDS_MARK = "instructor_name_tokens"
WORDS_LIMIT_UNIFIED = 60   # batch path: the top N real words per instructor and batch (plus the themes)
# The instructor's name tokens (lowercase letters only), looked up in the database for the word counter.
# They stay inside n8n: no AI node reads them.
NAME_TOKENS_SQL = (
    "COALESCE((SELECT jsonb_agg(DISTINCT t.tok) FROM (SELECT lower(x) AS tok FROM public.user_info ui, "
    "regexp_split_to_table(coalesce(ui.first_name, '') || ' ' || coalesce(ui.last_name, ''), '[^[:alpha:]]+') AS x "
    "WHERE ui.id = $1::uuid) t WHERE length(t.tok) >= 2), '[]'::jsonb)")
GET_OLD_WORDCLOUD_QUERY_V1 = ("SELECT\n    word,\n    count\nFROM public.instructor_wordcloud\n"
                              "WHERE instructor_id = $1::uuid\n  AND term_id = $2::uuid\nORDER BY count DESC;")
GET_OLD_WORDCLOUD_QUERY = (
    "SELECT w.word, w.count, n.tokens AS instructor_name_tokens\n"
    "FROM (SELECT " + NAME_TOKENS_SQL + " AS tokens) n\n"
    "LEFT JOIN public.instructor_wordcloud w\n"
    "  ON w.instructor_id = $1::uuid AND w.term_id = $2::uuid\n"
    "ORDER BY w.count DESC NULLS LAST;")
WORDS_BLOCK_UNIFIED = (
    "const finalKeywords =\n  keywords.slice(0, 10);\n\n"
    "// real word counts from the remarks (code only), merged with the themes before the upsert\n"
    "let cloudWords = finalKeywords;\n"
    "if (Array.isArray(original.instructor_name_tokens)) {\n"
    "  cloudWords = mergeWordCounts(finalKeywords, remarkWords(feedback.map((f) => f.remark), "
    "original.instructor_name_tokens, profaneKeyword, " + str(WORDS_LIMIT_UNIFIED) + "), 'new_count');\n"
    "}\n")
WORDS_BLOCK_INDIVIDUAL = (
    "// real word counts from this remark (code only), merged with the themes before the upsert\n"
    "if (Array.isArray(original.instructor_name_tokens)) {\n"
    "  const themes = keywords.filter((k) => k && k.word).map((k) => ({ word: String(k.word).trim(), count: 1 }));\n"
    "  const words = remarkWords([original.new_student_remark], original.instructor_name_tokens, profaneKeyword)\n"
    "    .map((w) => ({ word: w.word, count: w.new_count }));\n"
    "  keywords = mergeWordCounts(themes, words, 'count');\n"
    "}\n\n")


def add_remark_words_helper(code: str, where: str) -> str:
    """Put website_src/remark_words.js after the sexual-word filter (once; refreshed when present)."""
    title = HELPER_HEADERS["remark_words.js"]
    start, end = f"// {title}, from website_src/remark_words.js\n", f"// end of {title}\n"
    if start in code:
        i = code.index(start)
        j = code.index(end, i) + len(end)
        return code[:i] + helper_block("remark_words.js") + code[j:]
    return replace_once(code, "// end of sexual-word filter\n",
                        "// end of sexual-word filter\n" + helper_block("remark_words.js"), where)


# ---------------------------------------------------------------- AI sentences (2026-10-18)
# Root cause of the run-on paragraph: the grouped path asked Gemini for ONE string, which came back as
# comma-joined fragments; the single-scan path asked for arrays of short theme names and the Postgres
# node (`$5::text`) turned the array into "a,b,c". Both prompts now ask for 2-5 full sentences as a
# JSON array; both parsers store them as ONE text value, one sentence per line (website_src/ai_sentences.js).
AI_SENTENCES_MARK = "// AI sentences, from website_src/ai_sentences.js"
SENTENCE_RULES = (
    "Return {what} as a JSON array of 3 to 5 short, complete sentences.\n\n"
    "Each sentence is one separate point: a full sentence in plain English that ends with a period.\n\n"
    "Never one long paragraph, never comma-joined fragments, never bullets or numbering inside a sentence.\n\n"
    "Never use an instructor name. Do not invent information.\n\n"
    "{hist}\n")
SENTENCE_HISTORY = (
    "The {prev} may hold this as one paragraph or as separate lines: use it only as evidence "
    "and write it again as separate sentences, merged with the new evidence.")
UNIFIED_EXAMPLE_V1 = '    "positives": ["Clear, complete sentence."],\n    "improvements": ["Clear, complete sentence."],\n'
UNIFIED_EXAMPLE_NEW = (
    '    "positives": [\n      "First complete sentence.",\n      "Second complete sentence.",\n      "Third complete sentence."\n    ],\n'
    '    "improvements": [\n      "First complete sentence.",\n      "Second complete sentence.",\n      "Third complete sentence."\n    ],\n')
INDIVIDUAL_EXAMPLE_V1 = (
    '  "positive_themes": [\n    "The instructor explains difficult topics clearly."\n  ],\n'
    '  "improvement_insights": [\n    "More worked examples would help students follow the lessons."\n  ],\n')
INDIVIDUAL_EXAMPLE_NEW = (
    '  "positive_themes": [\n    "The instructor explains difficult topics clearly.",\n'
    '    "The lessons are well prepared and organized.",\n    "Grading is fair and consistent."\n  ],\n'
    '  "improvement_insights": [\n    "More worked examples would help students follow the lessons.",\n'
    '    "The pace of some discussions could be slower.",\n    "Quizzes could be shorter."\n  ],\n')
UNIFIED_AI_EDITS = [
    ("Do not invent strengths.\n",
     "Do not invent strengths.\n\n"
     + SENTENCE_RULES.format(what='"positives"', hist=SENTENCE_HISTORY.format(prev="historical analysis"))),
    ("Do not invent problems.\n",
     "Do not invent problems.\n\n"
     + SENTENCE_RULES.format(what='"improvements"', hist=SENTENCE_HISTORY.format(prev="historical analysis"))),
    ('    "positives": "",\n    "improvements": "",\n',
     '    "positives": ["Clear, complete sentence."],\n    "improvements": ["Clear, complete sentence."],\n'),
    ("- evaluation fields are strings\n",
     "- interpretation and suggestion are strings\n"
     "- positives and improvements are arrays of 3 to 5 complete sentences, each ending with a period\n"),
]
INDIVIDUAL_AI_EDITS = [
    ("Avoid duplicates and different phrases describing the same concept.\n\n"
     "==================================================\nIMPROVEMENT INSIGHTS\n",
     "Avoid duplicates and different phrases describing the same concept.\n\n"
     + SENTENCE_RULES.format(what='"positive_themes"', hist=SENTENCE_HISTORY.format(prev="existing AI analysis"))
     + "\n==================================================\nIMPROVEMENT INSIGHTS\n"),
    ("Avoid duplicates and different phrases describing the same concept.\n\n"
     "==================================================\nAI SUGGESTION\n",
     "Avoid duplicates and different phrases describing the same concept.\n\n"
     + SENTENCE_RULES.format(what='"improvement_insights"', hist=SENTENCE_HISTORY.format(prev="existing AI analysis"))
     + "\n==================================================\nAI SUGGESTION\n"),
    ('  "positive_themes": [\n    "Teaching clarity"\n  ],\n  "improvement_insights": [\n    "Examples"\n  ],\n',
     '  "positive_themes": [\n    "The instructor explains difficult topics clearly."\n  ],\n'
     '  "improvement_insights": [\n    "More worked examples would help students follow the lessons."\n  ],\n'),
]
AI_SENTENCES_PARSE_EDITS = {
    UNIFIED_PARSE: [
        ("    cleanText(\n      rawEvaluation.positives\n    )", "    aiSentences(\n      rawEvaluation.positives\n    )"),
        ("    cleanText(\n      rawEvaluation.improvements\n    )", "    aiSentences(\n      rawEvaluation.improvements\n    )")],
    INDIVIDUAL_PARSE: [
        ("    positive_themes: result.positive_themes || [],\n",
         "    positive_themes: aiSentences(result.positive_themes),\n"),
        ("    improvement_insights: result.improvement_insights || [],\n",
         "    improvement_insights: aiSentences(result.improvement_insights),\n")],
}


def apply_ai_sentences(out) -> list[str]:
    """Idempotent (full build and live export). Prompts ask for sentence arrays; both parsers turn the
    answer (array or string, old format included) into one clean sentence per line. Returns what changed."""
    by_name = {n["name"]: n for n in out["nodes"]}
    for name in (UNIFIED_AI, INDIVIDUAL_AI, UNIFIED_PARSE, INDIVIDUAL_PARSE):
        if name not in by_name:
            raise SystemExit(f"apply_ai_sentences: no node named {name!r}")
    done = []
    for name, edits in ((UNIFIED_AI, UNIFIED_AI_EDITS), (INDIVIDUAL_AI, INDIVIDUAL_AI_EDITS)):
        text = by_name[name]["parameters"]["text"]
        if "2 to 5 short, complete sentences" in text:
            text = text.replace("2 to 5", "3 to 5").replace(UNIFIED_EXAMPLE_V1, UNIFIED_EXAMPLE_NEW)                 .replace(INDIVIDUAL_EXAMPLE_V1, INDIVIDUAL_EXAMPLE_NEW)
            by_name[name]["parameters"]["text"] = text
            done.append(f"{name}: prompt now asks for 3 to 5 sentences")
        elif "3 to 5 short, complete sentences" not in text:
            for old, new in edits:
                text = replace_once(text, old, new, name)
            by_name[name]["parameters"]["text"] = text
            done.append(f"{name}: prompt asks for sentence arrays")
    title = HELPER_HEADERS["ai_sentences.js"]
    end = f"// end of {title}\n"
    for name, edits in AI_SENTENCES_PARSE_EDITS.items():
        old_code = by_name[name]["parameters"]["jsCode"]
        code = old_code
        if AI_SENTENCES_MARK in code:
            i = code.index(AI_SENTENCES_MARK)
            code = code[:i] + helper_block("ai_sentences.js") + code[code.index(end, i) + len(end):]
        else:
            for old, repl in edits:
                code = replace_once(code, old, repl, name)
            code = replace_once(code, "// end of remark words\n",
                                "// end of remark words\n" + helper_block("ai_sentences.js"), name)
        if code != old_code:
            by_name[name]["parameters"]["jsCode"] = code
            done.append(f"{name}: sentences, one per line")
    return done


def apply_wordcloud_words(out) -> list[str]:
    """Word cloud words (idempotent; full build and live export). The AI themes stay exactly as they
    are; real word counts (website_src/remark_words.js: no AI, no tokens) are merged into the same
    wordcloud items before the upsert, so a theme and a word with the same text never produce two
    rows (counts are summed). The instructor's name tokens come from the database (user_info) through
    the existing lookup nodes and are never read by an AI node. Missing tokens -> no words (fail safe).
    Returns what changed."""
    by_name = {n["name"]: n for n in out["nodes"]}
    for name in (GET_PREVIOUS_ANALYSIS, PREPARE_UNIFIED_INPUT, GET_OLD_WORDCLOUD, PREPARE_EXISTING_WORDCLOUD,
                 PREPARE_INDIVIDUAL_INPUT, UNIFIED_PARSE, INDIVIDUAL_PARSE):
        if name not in by_name:
            raise SystemExit(f"apply_wordcloud_words: no node named {name!r}")
    done = []

    def set_param(name, key, new):
        p = by_name[name]["parameters"]
        if p[key] != new:
            p[key] = new
            done.append(f"{name}: {key}")

    # unified (batch) path: name tokens ride along Get Previous -> Prepare Unified -> Parse Unified
    q = by_name[GET_PREVIOUS_ANALYSIS]["parameters"]["query"]
    if WORDS_MARK not in q:
        q = replace_once(q, ") AS previous_remarks;",
                         ") AS previous_remarks,\n\n    " + NAME_TOKENS_SQL + " AS instructor_name_tokens;",
                         GET_PREVIOUS_ANALYSIS)
    set_param(GET_PREVIOUS_ANALYSIS, "query", q)
    code = by_name[PREPARE_UNIFIED_INPUT]["parameters"]["jsCode"]
    if WORDS_MARK not in code:
        code = replace_once(code, "      feedback_count:\n        feedback.length\n",
                            "      feedback_count:\n        feedback.length,\n\n"
                            "      instructor_name_tokens:\n        Array.isArray(previous.instructor_name_tokens)\n"
                            "          ? previous.instructor_name_tokens\n          : null\n", PREPARE_UNIFIED_INPUT)
    set_param(PREPARE_UNIFIED_INPUT, "jsCode", code)
    code = by_name[UNIFIED_PARSE]["parameters"]["jsCode"]
    code = add_remark_words_helper(code, UNIFIED_PARSE)
    if "cloudWords" not in code:
        code = replace_once(code, "const finalKeywords =\n  keywords.slice(0, 10);\n", WORDS_BLOCK_UNIFIED, UNIFIED_PARSE)
        code = replace_once(code, "for (\n  const keyword of finalKeywords\n) {",
                            "for (\n  const keyword of cloudWords\n) {", UNIFIED_PARSE)
    set_param(UNIFIED_PARSE, "jsCode", code)

    # individual (single scan) path: Get the old wordcloud1 -> Prepare Existing Wordcloud1 -> Merge2 ->
    # Prepare Individual AI Input1 -> Add Parse Code node1
    q = by_name[GET_OLD_WORDCLOUD]["parameters"]["query"]
    if WORDS_MARK not in q:
        if q != GET_OLD_WORDCLOUD_QUERY_V1:
            raise SystemExit(f"{GET_OLD_WORDCLOUD}: unexpected query; the input changed")
        q = GET_OLD_WORDCLOUD_QUERY
    set_param(GET_OLD_WORDCLOUD, "query", q)
    code = by_name[PREPARE_EXISTING_WORDCLOUD]["parameters"]["jsCode"]
    if WORDS_MARK not in code:
        code = replace_once(code, "          count: Number(item.json.count || 0)\n        }))\n",
                            "          count: Number(item.json.count || 0)\n        })),\n\n"
                            "      instructor_name_tokens:\n        rows.map((item) => item.json.instructor_name_tokens)\n"
                            "          .find((t) => Array.isArray(t)) || null\n", PREPARE_EXISTING_WORDCLOUD)
    set_param(PREPARE_EXISTING_WORDCLOUD, "jsCode", code)
    code = by_name[PREPARE_INDIVIDUAL_INPUT]["parameters"]["jsCode"]
    if WORDS_MARK not in code:
        code = replace_once(code, "      existing_wordcloud: data.wordcloud || []\n",
                            "      existing_wordcloud: data.wordcloud || [],\n\n"
                            "      instructor_name_tokens: Array.isArray(data.instructor_name_tokens)\n"
                            "        ? data.instructor_name_tokens\n        : null\n", PREPARE_INDIVIDUAL_INPUT)
    set_param(PREPARE_INDIVIDUAL_INPUT, "jsCode", code)
    code = by_name[INDIVIDUAL_PARSE]["parameters"]["jsCode"]
    code = add_remark_words_helper(code, INDIVIDUAL_PARSE)
    if "real word counts from this remark" not in code:
        code = replace_once(code, "for (const keyword of keywords) {\n",
                            WORDS_BLOCK_INDIVIDUAL + "for (const keyword of keywords) {\n", INDIVIDUAL_PARSE)
        code = replace_once(code, "      word: String(keyword.word).trim(),\n      count: 1\n",
                            "      word: String(keyword.word).trim(),\n      count: keyword.count || 1\n", INDIVIDUAL_PARSE)
    set_param(INDIVIDUAL_PARSE, "jsCode", code)
    assert_no_instructor_names_to_gemini(out)
    return done


# ---------------------------------------------------------------- interpretation summary (2026-10-24)
# student_interpretation ("What Your Students Are Saying" in the app) was a short note about ONE
# student on the single-scan path. Both prompts now ask for one paragraph (5 to 8 sentences) about ALL
# students' comments for the instructor and term. The single-scan path gets the stored remarks too:
# 'Get Existing AI Suggestion1' (website_src/existing_ai_suggestion.sql) always returns one row with
# previous_remarks (last 100, remark + tone only) -> 'Prepare Existing AI1' -> Merge2 (input 2) ->
# 'Prepare Individual AI Input1' -> prompt block PREVIOUS STUDENT REMARKS. Both parsers clean and cap
# the paragraph (website_src/ai_interpretation.js); the single-scan parser keeps the stored text when
# Gemini returns none.
GET_EXISTING_AI = "Get Existing AI Suggestion1"
PREPARE_EXISTING_AI = "Prepare Existing AI1"
INTERP_MARK = "5 to 8 complete sentences"
INTERP_HELPER_MARK = "// AI interpretation, from website_src/ai_interpretation.js"
GET_EXISTING_AI_QUERY_V1 = (
    "SELECT\n    instructor_id,\n    term_id,\n    ai_suggestion,\n    student_interpretation,\n"
    "    positive_themes,\n    improvement_insights,\n    overall_tone\nFROM public.instructor_ai_suggestions\n"
    "WHERE instructor_id = $1::uuid\n  AND term_id = $2::uuid\nLIMIT 1;")
GET_EXISTING_AI_PARAMS = "={{ [$json.instructor_ID, $json.current_term_id] }}"
INTERP_RULES = (
    "Write ONE paragraph of 5 to 8 complete sentences (about 120 to 200 words) in plain English.\n\n"
    "Summarise what students say across ALL the evidence: {sources}.\n\n"
    "Cover:\n"
    "- what students praise most, and how common it is (\"most students\", \"several students\", \"a few students\");\n"
    "- the main concerns or requests (for example pacing, examples, grading, attitude);\n"
    "- specific things students mention, such as teaching methods or class activities;\n"
    "- the overall mood of the comments (mostly positive, mixed, or critical).\n\n"
    "Summarise Bisaya/Cebuano and Tagalog comments in English.\n\n"
    "Rules:\n"
    "- Never use an instructor name or a student name.\n"
    "- Never invent anything: every statement must be supported by the evidence. When there are too few "
    "comments for 5 sentences, write fewer sentences instead of inventing.\n"
    "- Never quote a vulgar or sexual word, or a masked word (asterisks such as ****).\n"
    "- No bullet points, no numbering, no headings, no line breaks.\n"
    "- Do not mention AI, surveys, batches, historical or previous data, updates, or the word cloud.\n")
SEP60 = "=" * 60
SEP50 = "=" * 50
UNIFIED_INTERP_V1 = (
    "INSTRUCTOR INTERPRETATION\n" + SEP60 + "\n\nUse:\n\n- previous_analysis\n- previous_wordcloud\n"
    "- previous_remarks\n- feedback\n\nThe result should represent the instructor's overall evaluation based on "
    "combined evidence.\n\nDo NOT mention:\n\n- previous surveys\n- historical data\n- new batch\n- updated analysis\n"
    "- AI\n- wordcloud\n\nWrite naturally as an instructor evaluation summary.\n\nDo not invent information.\n")
UNIFIED_INTERP_NEW = (
    "INTERPRETATION (evaluation.interpretation)\n" + SEP60 + "\n\n"
    "\"interpretation\" is the summary the instructor reads under \"What Your Students Are Saying\".\n\n"
    + INTERP_RULES.format(sources="the historical analysis, the historical remarks and the new feedback"))
UNIFIED_INTERP_EDITS = [
    (UNIFIED_INTERP_V1, UNIFIED_INTERP_NEW),
    ('    "interpretation": "",\n',
     '    "interpretation": "One paragraph of 5 to 8 complete sentences summarising what students say.",\n'),
    ("- interpretation and suggestion are strings\n",
     "- interpretation and suggestion are strings\n"
     "- interpretation is ONE paragraph of 5 to 8 complete sentences (about 120 to 200 words) about ALL "
     "students' comments, with no names, no bullets and nothing invented\n"),
]
INDIVIDUAL_INTERP_EDITS = [
    ("You have three sources of information:\n\n1. EXISTING AI ANALYSIS\n2. EXISTING WORDCLOUD\n"
     "3. NEW STUDENT FEEDBACK\n",
     "You have four sources of information:\n\n1. EXISTING AI ANALYSIS\n2. EXISTING WORDCLOUD\n"
     "3. PREVIOUS STUDENT REMARKS\n4. NEW STUDENT FEEDBACK\n"),
    (SEP50 + "\nNEW STUDENT FEEDBACK\n",
     SEP50 + "\nPREVIOUS STUDENT REMARKS\n" + SEP50 + "\n\n"
     "Earlier students' remarks for this instructor and term (newest first, at most 100; remark text and tone "
     "only). They are already processed: use them only as evidence for the student interpretation. Never take "
     "keywords from them and never use them for overall_tone.\n\n"
     "{{ JSON.stringify($json.previous_remarks || []) }}\n\n"
     + SEP50 + "\nNEW STUDENT FEEDBACK\n"),
    ("Write a short interpretation of what the NEW student is communicating.\n\nDo not invent information.\n",
     "Write an UPDATED OVERALL interpretation of what ALL students are saying, not only the new student.\n\n"
     "\"student_interpretation\" is the summary the instructor reads under \"What Your Students Are Saying\".\n\n"
     + INTERP_RULES.format(sources="the EXISTING AI ANALYSIS (its student_interpretation is the previous summary), "
                                   "the PREVIOUS STUDENT REMARKS and the NEW STUDENT FEEDBACK")
     + "\nRewrite the previous summary so it also reflects the new feedback; do not just add a sentence about "
       "the new student.\n"),
    ('  "student_interpretation": "Short interpretation of the new student feedback.",\n',
     '  "student_interpretation": "One paragraph of 5 to 8 complete sentences summarising what all students say.",\n'),
    ("- overall_tone MUST be exactly Positive, Critical, or Neutral.\n",
     "- overall_tone MUST be exactly Positive, Critical, or Neutral.\n"
     "- student_interpretation MUST be ONE paragraph of 5 to 8 complete sentences about ALL students "
     "(existing analysis, previous remarks and the new feedback), not only the new student.\n"),
]
PREPARE_EXISTING_AI_EDIT = (
    '        overall_tone: item?.json?.overall_tone || "Neutral"\n      }\n    }\n',
    '        overall_tone: item?.json?.overall_tone || "Neutral"\n      },\n\n'
    "      // earlier students' remarks (text + tone) for the interpretation summary\n"
    "      previous_remarks: (() => {\n"
    "        let r = item?.json?.previous_remarks;\n"
    "        if (typeof r === \"string\") { try { r = JSON.parse(r); } catch (e) { r = []; } }\n"
    "        return Array.isArray(r) ? r : [];\n"
    "      })()\n    }\n")
PREPARE_INDIVIDUAL_INTERP_EDIT = (
    "      existing_wordcloud: data.wordcloud || [],\n",
    "      existing_wordcloud: data.wordcloud || [],\n\n"
    "      // remark text and tone only (no student id, no names) for the interpretation summary\n"
    "      previous_remarks: (Array.isArray(data.previous_remarks) ? data.previous_remarks : [])\n"
    "        .map((r) => ({ remark: String(r?.remark ?? \"\").replace(/\\s+/g, \" \").trim(), tone: r?.tone ?? null }))\n"
    "        .filter((r) => r.remark)\n"
    "        .slice(0, 100),\n")
INTERP_PARSE_EDITS = {
    UNIFIED_PARSE: [("  interpretation:\n    cleanText(\n      rawEvaluation.interpretation\n    ),\n",
                     "  interpretation:\n    aiInterpretation(\n      rawEvaluation.interpretation\n    ),\n")],
    INDIVIDUAL_PARSE: [('    student_interpretation: result.student_interpretation || "",\n',
                        "    // empty answer: keep the stored summary instead of saving a blank\n"
                        "    student_interpretation: aiInterpretation(result.student_interpretation)\n"
                        "      || aiInterpretation(original.existing_ai && original.existing_ai.student_interpretation),\n")],
}


def apply_interpretation_summary(out) -> list[str]:
    """Idempotent (full build and live export / repo perfect-2.1.json). student_interpretation becomes a
    paragraph about ALL students' comments on both remark AI paths. Returns what changed."""
    by_name = {n["name"]: n for n in out["nodes"]}
    for name in (UNIFIED_AI, INDIVIDUAL_AI, UNIFIED_PARSE, INDIVIDUAL_PARSE, GET_EXISTING_AI,
                 PREPARE_EXISTING_AI, PREPARE_INDIVIDUAL_INPUT, "Merge2"):
        if name not in by_name:
            raise SystemExit(f"apply_interpretation_summary: no node named {name!r}")
    done = []

    # prompts
    for name, edits, mark in ((UNIFIED_AI, UNIFIED_INTERP_EDITS, INTERP_MARK),
                              (INDIVIDUAL_AI, INDIVIDUAL_INTERP_EDITS, "PREVIOUS STUDENT REMARKS")):
        text = by_name[name]["parameters"]["text"]
        if mark not in text:
            for old, new in edits:
                text = replace_once(text, old, new, name)
            by_name[name]["parameters"]["text"] = text
            done.append(f"{name}: interpretation = paragraph about all students")

    # single-scan path: one row with previous_remarks -> Prepare Existing AI1 -> Merge2 -> Prepare Individual
    node = by_name[GET_EXISTING_AI]
    sql = read("existing_ai_suggestion.sql")
    assert_no_param_in_sql_comments(GET_EXISTING_AI, sql)
    p = node["parameters"]
    if p.get("query") != sql:
        if "previous_remarks" not in p.get("query", "") and p.get("query", "").strip() != GET_EXISTING_AI_QUERY_V1:
            raise SystemExit(f"{GET_EXISTING_AI}: unexpected query; the input changed")
        p["query"] = sql
        done.append(f"{GET_EXISTING_AI}: one row + previous_remarks")
    if p.get("options", {}).get("queryReplacement") != GET_EXISTING_AI_PARAMS:
        raise SystemExit(f"{GET_EXISTING_AI}: unexpected query parameters; the input changed")
    if not node.get("alwaysOutputData"):
        node["alwaysOutputData"] = True
        done.append(f"{GET_EXISTING_AI}: alwaysOutputData")
    merge = by_name["Merge2"]["parameters"]
    if (merge.get("mode"), merge.get("combineBy"), merge.get("numberInputs")) != ("combine", "combineByPosition", 3):
        raise SystemExit("Merge2: expected combine by position with 3 inputs; the input changed")
    for name, (old, new) in ((PREPARE_EXISTING_AI, PREPARE_EXISTING_AI_EDIT),
                             (PREPARE_INDIVIDUAL_INPUT, PREPARE_INDIVIDUAL_INTERP_EDIT)):
        code = by_name[name]["parameters"]["jsCode"]
        if "previous_remarks" not in code:
            by_name[name]["parameters"]["jsCode"] = replace_once(code, old, new, name)
            done.append(f"{name}: previous_remarks")

    # parsers: clean + cap the paragraph (helper after the AI sentences helper; refreshed when present)
    title = HELPER_HEADERS["ai_interpretation.js"]
    end = f"// end of {title}\n"
    for name, edits in INTERP_PARSE_EDITS.items():
        old_code = by_name[name]["parameters"]["jsCode"]
        code = old_code
        if INTERP_HELPER_MARK in code:
            i = code.index(INTERP_HELPER_MARK)
            code = code[:i] + helper_block("ai_interpretation.js") + code[code.index(end, i) + len(end):]
        else:
            for old, repl in edits:
                code = replace_once(code, old, repl, name)
            code = replace_once(code, "// end of AI sentences\n",
                                "// end of AI sentences\n" + helper_block("ai_interpretation.js"), name)
        if code != old_code:
            by_name[name]["parameters"]["jsCode"] = code
            done.append(f"{name}: interpretation cleaned and capped")
    assert_no_instructor_names_to_gemini(out)
    return done


# ---------------------------------------------------------------- remark tone rules (2026-10-24)
# remark_tones (Unified AI Analysis1) and the new remark's overall_tone (Individual AI Analysis1):
# meaning first in any language, then one shared rule set and example list. Stored values stay exactly
# Positive / Critical / Neutral; both parsers read Gemini's value with website_src/ai_tone.js
# (case-insensitive, negative/mixed -> Critical).
TONE_MARK = "Never decide from one keyword."
TONE_HELPER_MARK = "// AI tone, from website_src/ai_tone.js"
TONE_EXAMPLES = [
    ("Maayo kaayo mo explain si ma'am.", "Positive"), ("Ang galing magturo ni sir, sobrang linaw.", "Positive"),
    ("Salamat sa pasensya, sir!", "Positive"), ("Keep it up po!", "Positive"),
    ("Very approachable and fair.", "Positive"), ("Muy buen profesor.", "Positive"),
    ("Paspas kaayo mo discuss, dili mi ka sabay.", "Critical"), ("Sana po bagalan ang pagtuturo.", "Critical"),
    ("Dugangi pa unta ang examples.", "Critical"), ("Maayo ra man siya pero kanunay late.", "Critical"),
    ("Galing mo sir, wala kaming natutunan.", "Critical"), ("Hindi po malinaw ang grading.", "Critical"),
    ("Mas maayo unta kung naay review before exam.", "Critical"),
    ("Okay ra.", "Neutral"), ("Every MWF ang klase.", "Neutral"),
]
TONE_RULES = (
    "First understand what the remark means, in ANY language: English, Tagalog, Bisaya/Cebuano, Hiligaynon, "
    "Ilocano, Taglish and Bislish mixes, other languages, slang, misspellings, text-speak and emojis. "
    "Translate it to English in your head before deciding. " + TONE_MARK + "\n\n"
    "The value MUST be exactly one of:\n\nPositive\nCritical\nNeutral\n\n"
    "Positive:\nMainly praise, thanks or satisfaction. A \"keep it up\" or \"more of the same\" request is still Positive.\n\n"
    "Critical:\nAny complaint, problem, unmet need, or request to change something. Request markers include unta, "
    "sana, dapat, palihug, please, pls, \"mas maayo kung\", \"sana po\". Negated praise (dili maayo, hindi malinaw, "
    "not clear) and sarcasm (\"galing mo sir, wala kaming natutunan\") are Critical too.\n\n"
    "Praise plus a specific problem or request:\nCritical.\n\n"
    "Neutral:\nFactual, unclear, off-topic, only a greeting, or too short to judge.\n\n"
    "Emoji only:\nJudge by the meaning of the emoji.\n\n"
    "Examples:\n\n" + "".join(f"\"{r}\"\n→ {t}\n\n" for r, t in TONE_EXAMPLES))
UNIFIED_TONE_NEW = "REMARK TONE\n" + SEP60 + "\n\nClassify EVERY NEW feedback item.\n\n" + TONE_RULES
INDIVIDUAL_TONE_NEW = "OVERALL TONE\n" + SEP50 + "\n\nDetermine the tone of the NEW student feedback.\n\n" + TONE_RULES
UNIFIED_TONE_FN_V1 = (
    "function normalizeTone(value) {\n\n  if (\n    typeof value === \"string\"\n  ) {\n\n    const tone =\n"
    "      value.trim();\n\n    if (\n      VALID_TONES.includes(tone)\n    ) {\n      return tone;\n    }\n\n  }\n\n"
    "  return null;\n}\n")
TONE_PARSE_EDITS = {
    UNIFIED_PARSE: [(UNIFIED_TONE_FN_V1,
                     "// case-insensitive; negative / mixed -> Critical (website_src/ai_tone.js)\n"
                     "function normalizeTone(value) {\n  return aiTone(value);\n}\n")],
    INDIVIDUAL_PARSE: [('let tone = result.overall_tone || "Neutral";\n\n'
                        'if (!["Positive", "Critical", "Neutral"].includes(tone)) {\n  tone = "Neutral";\n}\n',
                        "// case-insensitive; negative / mixed -> Critical; anything else -> Neutral (website_src/ai_tone.js)\n"
                        'let tone = aiTone(result.overall_tone) || "Neutral";\n')],
}


def _replace_section(text: str, start: str, stop: str, must: list[str], new: str, where: str) -> str:
    """Replace text[start .. stop) (start included, stop excluded); both must occur once and the old
    section must still hold the `must` anchors (else the input changed)."""
    if text.count(start) != 1:
        raise SystemExit(f"{where}: expected exactly one {start[:40]!r}; the input changed")
    i = text.index(start)
    j = text.find(stop, i)
    if j < 0 or any(m not in text[i:j] for m in must):
        raise SystemExit(f"{where}: section {start[:40]!r} changed; the input changed")
    return text[:i] + new + text[j:]


def apply_remark_tone_rules(out) -> list[str]:
    """Idempotent (full build and live export / repo perfect-2.1.json). Shared tone rules and examples in
    both remark prompts; tolerant tone parsing in both parsers. Returns what changed."""
    by_name = {n["name"]: n for n in out["nodes"]}
    for name in (UNIFIED_AI, INDIVIDUAL_AI, UNIFIED_PARSE, INDIVIDUAL_PARSE):
        if name not in by_name:
            raise SystemExit(f"apply_remark_tone_rules: no node named {name!r}")
    done = []
    for name, start, stop, must, new in (
            (UNIFIED_AI, "REMARK TONE\n" + SEP60 + "\n\n", SEP60 + "\nOVERALL TONE\n",
             ["Classify EVERY NEW feedback item.", '"Okay ra man."\nNeutral\n'], UNIFIED_TONE_NEW),
            (INDIVIDUAL_AI, "OVERALL TONE\n" + SEP50 + "\n\n", SEP50 + "\nWORDCLOUD\n",
             ["Determine the tone of the NEW student feedback.", '"His class is every Monday."'], INDIVIDUAL_TONE_NEW)):
        text = by_name[name]["parameters"]["text"]
        if TONE_MARK not in text:
            by_name[name]["parameters"]["text"] = _replace_section(text, start, stop, must, new, name)
            done.append(f"{name}: tone rules (any language, request = Critical, examples)")
    title = HELPER_HEADERS["ai_tone.js"]
    end = f"// end of {title}\n"
    for name, edits in TONE_PARSE_EDITS.items():
        old_code = by_name[name]["parameters"]["jsCode"]
        code = old_code
        if TONE_HELPER_MARK in code:
            i = code.index(TONE_HELPER_MARK)
            code = code[:i] + helper_block("ai_tone.js") + code[code.index(end, i) + len(end):]
        else:
            for old, repl in edits:
                code = replace_once(code, old, repl, name)
            code = replace_once(code, "// end of AI interpretation\n",
                                "// end of AI interpretation\n" + helper_block("ai_tone.js"), name)
        if code != old_code:
            by_name[name]["parameters"]["jsCode"] = code
            done.append(f"{name}: tone read case-insensitively (negative/mixed -> Critical)")
    assert_no_instructor_names_to_gemini(out)
    return done


def flagged_respond_expression() -> str:
    """Body of the shared 'Respond to Webhook'. Website executions: the backend
    contract object. Phone executions: exactly the text the node always sent
    (its body was the fixed string '{{ JSON.stringify($json) }}', not an expression), except a scan
    auto-rejected by the stamp rule (duplicate_stamp), which gets a JSON object with its message."""
    phone_literal = "'{' + '{ JSON.stringify($json) }' + '}'"
    js = (
        "(() => { "
        "let w = null; "
        "try { const it = $('Website Flagged Record').first(); "
        "w = (it && it.json && it.json._source === 'website') ? it.json : null; } catch (e) { w = null; } "
        "const failed = !!($json && $json.error !== undefined); "
        "if (!w) { "
        "let pr = null; "
        "try { const b = $('build flagged record1').first(); pr = (b && b.json) ? b.json : null; } catch (e) { pr = null; } "
        "const rc = (pr && pr.review_correction) || null; "
        "if (!failed && rc && rc.rule === 'duplicate_stamp') { "
        "return JSON.stringify({ status: 'discarded', flagged: true, auto_rejected: 'duplicate_stamp', "
        "task_id: String(pr.task_id || ''), stamp_no: (pr.stamp_no || null), "
        "duplicate_of: ((pr.partial_data && pr.partial_data.duplicate_of) || null), "
        "review_reasons: (Array.isArray(pr.review_reasons) ? pr.review_reasons : ['duplicate_stamp']), "
        "message: 'Discarded automatically: this form was already scanned (stamp ' + (pr.stamp_no || '?') "
        "+ '), so it is not counted.' }); "
        "} "
        f"return {phone_literal}; "
        "} "
        "const reasons = Array.isArray(w.review_reasons) ? w.review_reasons.slice() : []; "
        "if (failed) { reasons.push('internal_error_not_saved'); } "
        "const note = String(w.review_note || '').trim(); "
        "const dupStamp = !failed && w.auto_reject === 'duplicate_stamp'; "
        "const discarded = !failed && !dupStamp && w.auto_discard === true; "
        "const answered = Number.isInteger(w.answered_count) ? w.answered_count : null; "
        "const message = failed "
        "? 'NOT SAVED: inserting the page into the review queue failed in n8n. Send this page again.' "
        ": dupStamp "
        "? ('Discarded automatically: this form was already scanned (stamp ' + (w.stamp_no || '?') "
        "+ '), so it is not counted.') "
        ": discarded "
        "? ('Discarded automatically: fewer than 5 of the 20 items were answered' "
        "+ (answered !== null ? ' (only ' + answered + ')' : '') + ', so this form is not counted.') "
        ": ('Flagged for manual review (' + (reasons.join(', ') || 'unspecified') + ').' + (note ? ' ' + note : '')).slice(0, 1000); "
        "return JSON.stringify({ status: 'flagged', flagged: true, task_id: String(w.task_id || ''), "
        "review_reasons: reasons, identity_check: (w.identity_check || null), "
        "auto_rejected: (dupStamp ? 'duplicate_stamp' : null), stamp_no: (w.stamp_no || null), "
        "message: message }); "
        "})()"
    )
    if "{{" in js or "}}" in js:
        raise SystemExit("flagged respond expression must not contain '{{' or '}}'")
    return "={{ " + js + " }}"


def patch_flagged_respond(node):
    p = node["parameters"]
    if p.get("respondWith") != "text" or p.get("responseBody") != "{{ JSON.stringify($json) }}":
        raise SystemExit(f"{FLAGGED_RESPOND}: unexpected parameters; the input changed")
    p["responseBody"] = flagged_respond_expression()


def apply_main_flow_aggregate_fix(out):
    """Phone / sheet / correction / website: recompute aggregates from the raw
    table, strictly after the raw upsert of the execution.

    * Ordering: with executionOrder v1, If1's branches run in canvas order, so the
      raw upsert only ran first because 'Code in JavaScript' sat highest on the
      canvas. Now 'If1' no longer feeds 'Management Node1' / 'Performance1'; they
      are fed by 'Raw Upsert Done' on the "done" output of 'Loop Over Items3'.
    * The aggregate chain itself is replaced afterwards by apply_sast_recompute_flow
      (Raw Upsert Done -> Code in JavaScript8 -> recompute_sast -> If28); the old
      'send to ...' nodes (the old aggregate SQL) are removed there.
    """
    by_name = {n["name"]: n for n in out["nodes"]}
    for name in [*MAIN_RECOMPUTE_NODES, RAW_LOOP, "If1", "Insert or update rows in a table1", *AGG_CHAIN_HEADS]:
        if name not in by_name:
            raise SystemExit(f"Input workflow has no node named {name!r}")

    conns = out["connections"]
    if1_main = conns["If1"]["main"]
    if1_main[0] = [c for c in if1_main[0] if c["node"] not in AGG_CHAIN_HEADS]
    loop_main = conns.get(RAW_LOOP, {}).get("main", [])
    if loop_main and loop_main[0]:
        raise SystemExit(f"{RAW_LOOP} 'done' output is already connected: {loop_main[0]}")
    out["nodes"].append(code_node(RAW_UPSERT_GATE, [-50480, 13328], "raw_upsert_done.js"))
    add_connection(conns, RAW_LOOP, 0, RAW_UPSERT_GATE, 0)
    for head in AGG_CHAIN_HEADS:
        add_connection(conns, RAW_UPSERT_GATE, 0, head, 0)

    # A failed upsert batch used to stop the loop (If14 only fed successful items back), so
    # the "done" output -- and the recompute -- never ran. Now EVERY upsert result (row or
    # error item) goes straight back into the loop, so "done" fires exactly once per execution
    # after all batches were attempted; If14 keeps only its error-flag branch. One input per
    # iteration, so the loop never advances twice. Rows are upserted independently, so one bad
    # row no longer fails the other rows of its batch.
    if14_main = conns[RAW_ERROR_IF]["main"]
    if [c["node"] for c in if14_main[1]] != [RAW_LOOP]:
        raise SystemExit(f"{RAW_ERROR_IF}[1] is not just {RAW_LOOP}: {if14_main[1]}")
    if14_main[1] = []
    add_connection(conns, RAW_UPSERT, 0, RAW_LOOP, 0)
    # The Postgres node (v2) option value is 'independently'. Any other value (an earlier build
    # wrote 'independent') matches none of its single/transaction/independently branches: the
    # node then sends NO query and returns 0 items ("Success", Output: 0 items). Nothing was
    # stored, nothing went back into the loop, and 'done' -> Raw Upsert Done never fired.
    by_name[RAW_UPSERT]["parameters"].setdefault("options", {})["queryBatching"] = RAW_UPSERT_BATCHING
    # Belt and braces: splitInBatches only runs again when an input arrives, so an upsert run that
    # returns 0 items would still dead-end the loop. With alwaysOutputData an empty run emits one
    # empty item: it goes back into the loop, and If14 ($json.error !== undefined -> false) routes
    # it to its unconnected false output, so 'Loop1 Error Flag1' never fires for it.
    by_name[RAW_UPSERT]["alwaysOutputData"] = True

    # Website rows are already stored by 'Website Raw Upsert': skip the shared upsert for them
    # (never insert a second NULL-student copy) but still go round the loop, so "done" fires.
    loop_main = conns[RAW_LOOP]["main"]
    if [c["node"] for c in loop_main[1]] != [RAW_UPSERT]:
        raise SystemExit(f"{RAW_LOOP}[1] is not just {RAW_UPSERT}: {loop_main[1]}")
    loop_main[1] = []
    out["nodes"].append(if_node(RAW_SKIP_IF, [-50704, 13100], "={{ $json._raw_already_stored === true }}",
                                "8f0b6c1e-web0-4d01-9a01-000000000005"))
    add_connection(conns, RAW_LOOP, 1, RAW_SKIP_IF, 0)
    add_connection(conns, RAW_SKIP_IF, 0, RAW_LOOP, 0)
    add_connection(conns, RAW_SKIP_IF, 1, RAW_UPSERT, 0)


def apply_sast_recompute_flow(out):
    """One aggregate path after the raw upsert (needs migration 013: public.recompute_sast).

    Before: 'Raw Upsert Done' fed two parallel chains, Management Node1 -> calculate mangement1 ->
    Loop Over Items4 -> send to managment table1 (-> done: Code in JavaScript8 -> update to overall
    total2 -> update to overall_question_means2 + If28) and the same for Performance1 (-> calc1 ->
    update to overall total3 -> ...means3 + If28). Both 'send to ...' nodes ran the same recompute
    (both tables, twice), the calculate* means were discarded, and 'overall total' ran twice, so
    If28 pushed every low-score alert twice.

    After: Raw Upsert Done -> Code in JavaScript8 (unique {instructor_id, term_id} from If1) ->
    'update to overall total2' running SELECT * FROM public.recompute_sast($1, $2) (one row per
    pair: instructor_id, term_id, total_responses, management/performance/combined_score_mean) ->
    If28 (reads $json.combined_score_mean and $json.instructor_id). The 12 dead nodes are removed.
    Works on the build output and on a live export of that build (node ids/positions untouched)."""
    by_name = {n["name"]: n for n in out["nodes"]}
    missing = [n for n in [RAW_UPSERT_GATE, SAST_PAIRS, SAST_RECOMPUTE, SAST_ALERT_IF, *SAST_DEAD_NODES]
               if n not in by_name]
    if missing:
        raise SystemExit(f"apply_sast_recompute_flow: no node named {missing} (already applied?)")
    conns = out["connections"]

    def tg(name, idx=0):
        lists = conns.get(name, {}).get("main", [])
        return sorted(c["node"] for c in (lists[idx] if idx < len(lists) else []) or [])
    # the chain must be exactly the one described above (a hand edit in n8n is never overwritten)
    expected = {(RAW_UPSERT_GATE, 0): sorted(AGG_CHAIN_HEADS),
                (SAST_PAIRS, 0): [SAST_RECOMPUTE],
                (SAST_RECOMPUTE, 0): sorted(["update to overall_question_means2", SAST_ALERT_IF]),
                ("Loop Over Items4", 0): [SAST_PAIRS]}
    wrong = {f"{k[0]}[{k[1]}]": tg(*k) for k, v in expected.items() if tg(*k) != v}
    if wrong:
        raise SystemExit(f"apply_sast_recompute_flow: aggregate chain changed: {wrong}")
    dead = set(SAST_DEAD_NODES)
    refs = sorted({f"{n['name']} -> {m.group(2) or m.group(4)}" for n in out["nodes"] if n["name"] not in dead
                   for s in _strings(n.get("parameters", {})) for m in NODE_REF_RE.finditer(s)
                   if (m.group(2) or m.group(4)) in dead})
    if refs:
        raise SystemExit(f"apply_sast_recompute_flow: kept nodes reference removed nodes: {refs}")

    out["nodes"] = [n for n in out["nodes"] if n["name"] not in dead]
    remove_node_connections(conns, SAST_DEAD_NODES)
    conns[RAW_UPSERT_GATE]["main"][0] = []
    add_connection(conns, RAW_UPSERT_GATE, 0, SAST_PAIRS, 0)
    conns[SAST_RECOMPUTE]["main"] = [[]]
    add_connection(conns, SAST_RECOMPUTE, 0, SAST_ALERT_IF, 0)

    node = by_name[SAST_RECOMPUTE]
    p = node["parameters"]
    p["operation"] = "executeQuery"
    p["query"] = SAST_RECOMPUTE_SQL
    p.setdefault("options", {})["queryReplacement"] = SAST_RECOMPUTE_PARAMS
    node["onError"] = "continueRegularOutput"


def _strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v)


def _with_rating(code: str, where: str) -> str:
    """Put website_src/rating.js in front of a node's code (once)."""
    if "function rating(" in code:
        raise SystemExit(f"{where}: already contains rating(); the input changed")
    return helper_block("rating.js") + "\n" + code


FORMAT_FINAL_OLD = """// The reconciler emits a flat object, m1..m10 / p1..p10, where null means both
// engines agreed the row is genuinely blank. Downstream arithmetic sums these,
// so a blank has to become 0, not null.
let answered = 0;
let sum = 0;
for (let i = 1; i <= 10; i++) {
  for (const prefix of ['m', 'p']) {
    const key = `${prefix}${i}`;
    const v = ratings[key];
    const score = (v === null || v === undefined) ? 0 : Number(v) || 0;
    result[key] = score;
    if (score > 0) { answered++; sum += score; }
  }
}

// Recomputed from the reconciled values. Python's own summary predates the
// blank-filling, so reusing it would disagree with the scores just above it.
result.answered    = answered;
result.total_score = sum;
result.average     = answered ? Number((sum / answered).toFixed(2)) : 0;
result.percent     = Number(((sum / (20 * 5)) * 100).toFixed(2));
"""
FORMAT_FINAL_NEW = """// The reconciler emits a flat object, m1..m10 / p1..p10, where null means both
// engines agreed the row is genuinely blank. A blank stays null (stored as NULL,
// never 0): the database means skip NULLs, and a 0 would fail its CHECK (1..5).
let answered = 0;
let sum = 0;
for (let i = 1; i <= 10; i++) {
  for (const prefix of ['m', 'p']) {
    const key = `${prefix}${i}`;
    const score = rating(ratings[key]);
    result[key] = score;
    if (score !== null) { answered++; sum += score; }
  }
}

// Recomputed from the reconciled values, over the answered rows only (blanks
// are skipped, not counted as 0).
result.answered    = answered;
result.total_score = sum;
result.average     = answered ? Number((sum / answered).toFixed(2)) : null;
result.percent     = answered ? Number((sum / (answered * 5) * 100).toFixed(2)) : null;
"""


def patch_null_ratings(out):
    """Blank / invalid ratings stay NULL all the way to the raw row (never 0). Every mapper that
    used to turn a missing rating into 0 now keeps null, and every node that reads ratings typed
    or sent by a client uses rating() (website_src/rating.js): an integer 1..5, else null. The
    Flutter app and the website backend send 0 for a blank row: rating(0) = null.
    'Code in JavaScript' (the last step before the shared raw upsert, every path goes through it)
    applies rating() to all 20 columns, so nothing outside 1..5 reaches the CHECK constraint."""
    by_name = {n["name"]: n for n in out["nodes"]}
    for name in [CODE_IN_JS, FORMAT_FINAL, PHONE_MAP, WEBSITE_MAP, CORRECTION_BUILD, *CORRECTION_MAPS]:
        if name not in by_name:
            raise SystemExit(f"patch_null_ratings: no node named {name!r}")

    def code_of(name):
        return by_name[name]["parameters"]["jsCode"]

    def set_code(name, code):
        by_name[name]["parameters"]["jsCode"] = code

    # 'Code in JavaScript': Number(r['M1']) || null -> rating(r['M1'])   (0/'blank'/6 -> null)
    code = code_of(CODE_IN_JS)
    for p in "mp":
        for i in range(1, 11):
            code = replace_once(code, f"      {p}{i}: Number(r['{p.upper()}{i}']) || null,\n",
                                f"      {p}{i}: rating(r['{p.upper()}{i}']),\n", CODE_IN_JS)
    set_code(CODE_IN_JS, _with_rating(code, CODE_IN_JS))

    # '2. Format Final Results1' (phone scan): blank -> null, stats over the answered rows only
    set_code(FORMAT_FINAL, _with_rating(replace_once(code_of(FORMAT_FINAL), FORMAT_FINAL_OLD, FORMAT_FINAL_NEW,
                                                     FORMAT_FINAL), FORMAT_FINAL))

    # 'Map to Target Format3' (phone scan): a missing score defaults to null, not 0
    code = code_of(PHONE_MAP)
    for p in "mp":
        code = replace_once(
            code,
            f"result[`{p.upper()}${{i}}`] = formattedResults[`{p}${{i}}`] !== undefined ? formattedResults[`{p}${{i}}`] : 0;",
            f"result[`{p.upper()}${{i}}`] = formattedResults[`{p}${{i}}`] !== undefined ? formattedResults[`{p}${{i}}`] : null;",
            PHONE_MAP)
    set_code(PHONE_MAP, code)

    # 'Website Map to Target Format': ?? 0 -> ?? null (website_src/website_map_to_target.js). The build
    # generates it from the source already; a live export still has the old code.
    # (apply_stamp_rule then refreshes the whole node from the source, which already has ?? null)
    want = read_js("website_map_to_target.js")
    code = code_of(WEBSITE_MAP)
    if code != want and "d.ratings?.[`m${i}`] ?? 0;" in code:
        code = replace_once(code, "  // blank row -> 0, stored as NULL later\n",
                            "  // blank row stays null (stored as NULL, never 0)\n", WEBSITE_MAP)
        code = replace_once(code, "d.ratings?.[`m${i}`] ?? 0;", "d.ratings?.[`m${i}`] ?? null;", WEBSITE_MAP)
        code = replace_once(code, "d.ratings?.[`p${i}`] ?? 0;", "d.ratings?.[`p${i}`] ?? null;", WEBSITE_MAP)
        set_code(WEBSITE_MAP, code)

    # 'build the record1' (sast-manual-correction): parseInt(..) || 0 -> rating(..)
    code = code_of(CORRECTION_BUILD)
    for p in "mp":
        code = replace_once(code, f"scores[`{p}${{i}}`] = parseInt(body[`{p}${{i}}`]) || 0;",
                            f"scores[`{p}${{i}}`] = rating(body[`{p}${{i}}`]);", CORRECTION_BUILD)
    set_code(CORRECTION_BUILD, _with_rating(code, CORRECTION_BUILD))

    # 'Map target' (sast-manual-correction): data.raw.mN ?? 0 -> ?? null (top level M/P and raw.*)
    code, n = re.subn(r"(data\.raw\.[mp]\d+\s*\?\?) 0\b", r"\1 null", code_of("Map target"))
    if n != 40:
        raise SystemExit(f"Map target: expected 40 'data.raw.mN ?? 0', found {n}; the input changed")
    set_code("Map target", code)

    # 'Map target1' (import-error-correction): Number(raw.M1 ?? raw.m1 ?? 0) -> rating(raw.M1 ?? raw.m1)
    code, n = re.subn(r"Number\((raw\.[MP]\d+\s*\?\?\s*raw\.[mp]\d+)\s*\?\?\s*0\)", r"rating(\1)",
                      code_of("Map target1"))
    if n != 20:
        raise SystemExit(f"Map target1: expected 20 'Number(raw.MN ?? raw.mN ?? 0)', found {n}; the input changed")
    set_code("Map target1", _with_rating(code, "Map target1"))


SAST_ALERT_EXPR = ("={{ $json.combined_score_mean !== null && $json.combined_score_mean !== undefined "
                   "&& $json.combined_score_mean !== '' && Number($json.combined_score_mean) < 3 }}")


def patch_alert_if(out):
    """recompute_sast returns combined_score_mean = NULL when no form is answered; with loose type
    validation the old 'number lt 3' coerced NULL to 0 and pushed a false 'below 3.0' alert (numerics
    may also arrive as strings). If28 is now true only for a real number below 3 (boolean 'true',
    like the other boolean IFs). id, name, position and connections are unchanged."""
    node = next((n for n in out["nodes"] if n["name"] == SAST_ALERT_IF), None)
    if node is None:
        raise SystemExit(f"Input workflow has no node named {SAST_ALERT_IF!r}")
    conds = node["parameters"]["conditions"]["conditions"]
    if len(conds) != 1 or conds[0]["leftValue"] != "={{ $json.combined_score_mean }}":
        raise SystemExit(f"{SAST_ALERT_IF}: unexpected condition; the input changed")
    conds[0]["leftValue"] = SAST_ALERT_EXPR
    conds[0]["rightValue"] = ""
    conds[0]["operator"] = {"type": "boolean", "operation": "true", "singleValue": True}


def apply_sast_v2(out):
    """Migration-013 workflow changes: one recompute_sast path + blanks stay NULL."""
    apply_sast_recompute_flow(out)
    patch_null_ratings(out)
    patch_alert_if(out)


def fix_remark_loop_single_feed(out):
    """'Loop Over Items8' (grouped remark AI, one iteration per instructor+term group) must be
    fed back exactly once per iteration. It was fed by 'insert data to student remarks2' AND by
    the critical-remark alert branch ('If25' true / 'PushNotificaton for sentiment analysisi5'),
    so a group with a Critical remark advanced the loop twice: the alert branch (higher on the
    canvas, so it runs first) started the NEXT group before this group's remarks were inserted,
    the groups ran nested and out of order, and the surplus feeds re-emitted 'done'. The alert
    branch is now fire-and-forget (like its sibling If27 -> push to the department head).

    The remaining feed always fires once per iteration: 'Parse Unified AI Result1' throws or
    emits one student_remark per remark, 'Group feedback by instructor1' only emits groups with at
    least one remark (length >= 2) and 'Prepare Unified AI Input1' keeps them, so 'Branch 3 --
    Student Remarks2' is never empty; the INSERT (no RETURNING, onError continueRegularOutput)
    always returns one item ({success: true} or the error item)."""
    conns = out["connections"]
    by_name = {n["name"]: n for n in out["nodes"]}
    for name in [REMARK_LOOP, REMARK_LOOP_WAIT, REMARK_LOOP_FEED, *REMARK_ALERT_FEEDS]:
        if name not in by_name:
            raise SystemExit(f"Input workflow has no node named {name!r}")
    feeders = sorted((s, i) for s, v in conns.items() for i, lst in enumerate(v.get("main", []))
                     for c in (lst or []) if c["node"] == REMARK_LOOP_WAIT)
    if feeders != sorted([(REMARK_LOOP_FEED, 0), (REMARK_ALERT_FEEDS[0], 0), (REMARK_ALERT_FEEDS[1], 0)]):
        raise SystemExit(f"{REMARK_LOOP_WAIT} inputs changed: {feeders}; the input changed")
    for src in REMARK_ALERT_FEEDS:
        outs = conns[src]["main"]
        outs[0] = [c for c in outs[0] if c["node"] != REMARK_LOOP_WAIT]
    if [c["node"] for c in conns[REMARK_LOOP_WAIT]["main"][0]] != [REMARK_LOOP]:
        raise SystemExit(f"{REMARK_LOOP_WAIT} no longer feeds {REMARK_LOOP}; the input changed")


# Gemini free tier (gemini-3.5-flash-lite): 15 requests/min. The grouped remark loop is paced to
# ~10 calls/min (one Gemini call per loop pass + 6 s wait) and retried on a 429.
REMARK_LOOP_WAIT_SECONDS = 6
REMARK_PREVIOUS = "Get Previous Instructor Analysis1"
AI_RETRY = {"retryOnFail": True, "maxTries": 3, "waitBetweenTries": 5000}   # n8n caps the wait at 5000 ms


def _feed_wait(conns, src):
    """Output 1 (the error output) of `src` goes to Wait1, i.e. the loop skips the instructor."""
    main = conns.setdefault(src, {}).setdefault("main", [])
    while len(main) < 2:
        main.append([])
    if not any(c["node"] == REMARK_LOOP_WAIT for c in main[1]):
        main[1].append({"node": REMARK_LOOP_WAIT, "type": "main", "index": 0})


def apply_gemini_rate_limit(out) -> list[str]:
    """Keep the n8n workflow under the Gemini free-tier limit (idempotent).
      * Wait1 (the pause before the next instructor of the grouped remark loop) = 6 s, explicit unit.
      * 'Unified AI Analysis1' / 'Individual AI Analysis1': retry 3x, 5 s apart (a 429 is retried).
      * Grouped loop: a final AI failure ('Unified AI Analysis1' error output), a parse failure
        ('Parse Unified AI Result1' error output, also a missing text) or a failed 'previous analysis'
        lookup skips that instructor and goes on through Wait1, so one failure never stops the import."""
    by_name = {n["name"]: n for n in out["nodes"]}
    for name in [REMARK_LOOP_WAIT, UNIFIED_AI, INDIVIDUAL_AI, UNIFIED_PARSE, REMARK_PREVIOUS]:
        if name not in by_name:
            raise SystemExit(f"Input workflow has no node named {name!r}")
    done = []
    wait = by_name[REMARK_LOOP_WAIT]["parameters"]
    want = {"resume": "timeInterval", "amount": REMARK_LOOP_WAIT_SECONDS, "unit": "seconds"}
    if any(wait.get(k) != v for k, v in want.items()):
        wait.update(want)
        done.append(f"{REMARK_LOOP_WAIT} = {REMARK_LOOP_WAIT_SECONDS} s")
    for name in (UNIFIED_AI, INDIVIDUAL_AI):
        n = by_name[name]
        if any(n.get(k) != v for k, v in AI_RETRY.items()):
            n.update(AI_RETRY)
            done.append(f"{name} retry 3x/5 s")
    for name, mode in ((UNIFIED_AI, "continueErrorOutput"), (UNIFIED_PARSE, "continueErrorOutput"),
                       (REMARK_PREVIOUS, "continueRegularOutput")):
        if by_name[name].get("onError") != mode:
            by_name[name]["onError"] = mode
            done.append(f"{name} onError {mode}")
    for src in (UNIFIED_AI, UNIFIED_PARSE):
        before = json.dumps(out["connections"].get(src), sort_keys=True)
        _feed_wait(out["connections"], src)
        if json.dumps(out["connections"].get(src), sort_keys=True) != before:
            done.append(f"{src} error output -> {REMARK_LOOP_WAIT}")
    return done


def patch_flagged_fcm_lookup(node):
    """'save flagged to failed_scan_queue1' posts with 'Prefer: return=minimal', so its output has
    no body and '{{ $json.user_id }}' was undefined ("Query Parameters must be a string of
    comma-separated values or an array of values"). Read the staff id from the record that was
    posted, as an explicit array; NULL (no staff id) just matches no row, so no push is sent."""
    opts = node["parameters"].setdefault("options", {})
    if opts.get("queryReplacement") != "={{ $json.user_id }}" or "$1::uuid" not in node["parameters"]["query"]:
        raise SystemExit(f"{FLAGGED_FCM}: unexpected parameters; the input changed")
    opts["queryReplacement"] = FLAGGED_FCM_PARAMS


def patch_phone_subject_lookup(node):
    """Phone scans whose instructor is not matched: 'get instructor id6' (alwaysOutputData) emits an
    empty item, both '{{ }}' values were empty, n8n dropped them from the parameter list and the
    query failed ("Variable $1 out of range"). The SQL already handles a NULL instructor
    (resolves the subject, links nothing), so pass an explicit array with the subject text
    taken from If31's item when the lookup returned nothing."""
    opts = node["parameters"].setdefault("options", {})
    if opts.get("queryReplacement") != "={{ $json.instructor_id }}\n{{ $json.subject_search_term }}":
        raise SystemExit(f"{PHONE_SUBJECT_LOOKUP}: unexpected parameters; the input changed")
    opts["queryReplacement"] = PHONE_SUBJECT_PARAMS


CORRECTION_INSTRUCTOR_LOOKUPS = ["get instructor id7", "get instructor id9"]   # sast-manual-correction / import-error-correction


def patch_correction_instructor_lookup(node):
    """Placeholder instructors (user_info.is_placeholder; disabled + 'pending', maybe without a
    department/role row) are valid batch instructors, so a corrected website page of a
    placeholder batch must resolve. Everyone else keeps the existing rule (approved +
    instructor role). On equal scores a real approved account wins."""
    q = node["parameters"]["query"]
    q = replace_once(q, (
        "  JOIN public.department_table dt ON dt.user_id = ui.id\n"
        "  JOIN public.roles r             ON r.id = dt.roles\n"), (
        "  LEFT JOIN public.department_table dt ON dt.user_id = ui.id\n"
        "  LEFT JOIN public.roles r             ON r.id = dt.roles\n"), node["name"])
    q = replace_once(q, (
        "  WHERE r.\"Roles\" IN ('DEPARTMENT_HEAD','FULL-TIME','PART-TIME')\n"
        "    AND ui.account_status = 'approved'\n"), (
        "  WHERE ( (r.\"Roles\" IN ('DEPARTMENT_HEAD','FULL-TIME','PART-TIME') AND ui.account_status = 'approved')\n"
        "          OR lower(COALESCE(ui.is_placeholder::text, 'false')) IN ('true','t','1','yes') )\n"), node["name"])
    q = replace_once(q, "  ORDER BY match_score DESC\n", (
        "  ORDER BY match_score DESC, (ui.account_status = 'approved') DESC\n"), node["name"])
    node["parameters"]["query"] = q


# Correction webhooks: the original scan image must be looked up BEFORE the record is mapped.
# (webhook, image lookup, instructor lookup, image-lookup SQL, image-lookup parameters)
CORRECTION_IMAGE_LOOKUPS = [
    ("sast-manual-correction1", "get existing scan image id", "get instructor id7",
     "correction_scan_image_lookup.sql",
     "={{ [ String($json.body.failed_scan_id ?? ''), "
     "(Array.isArray($json.body.task_id) ? '' : String($json.body.task_id ?? '')) ] }}"),
    ("import-error-correction1", "get existing scan image id1", "get instructor id9",
     "import_error_scan_image_lookup.sql",
     "={{ [ String($json.body.import_error_id ?? ''), "
     "(Array.isArray($json.body.task_id) ? '' : String($json.body.task_id ?? "
     "(($json.body.raw_data && typeof $json.body.raw_data === 'object') ? ($json.body.raw_data.task_id ?? '') : ''))) ] }}"),
]
SCAN_IMAGE_SAVE_IF = "is data from google link?"      # If19 false -> save a scan image? -> import_errors
SCAN_IMAGE_SAVE_GUARD_ID = "8f0b6c1e-web0-4d01-9a01-000000000006"


def serialize_correction_image_lookups(out):
    """Corrections of a flagged scan (sast-manual-correction) and of an import error
    (import-error-correction) whose instructor/subject still do not resolve become a new
    import_errors row, which must point at the ORIGINAL scan image.

    Bug: the webhook fed 'get instructor id7/9' AND 'get existing scan image id(1)' in parallel.
    With executionOrder v1 n8n runs one branch to the end before starting the next, ordered by
    canvas position; the image lookup sat lower on the canvas, so the whole chain (Map target ->
    If19 -> 'is data from google link?' -> Save scan error image1 -> Insert import_errors1) ran
    BEFORE the lookup. 'Map target' caught "node not executed" and set existing_scan_image_id =
    null, so 'is data from google link?' (task_id set, no existing image) sent the row to 'Save
    scan error image1', which inserted a NEW scan_error_images row with image_base64 NULL ('Map
    target' has no scan_image) and the import error pointed at that empty row: no image in the app.

    Fix: webhook -> image lookup -> instructor lookup (serial). The image lookup always returns
    exactly one row (NULL when there is no image; alwaysOutputData + continueRegularOutput), so
    the chain always continues, and it only returns an image row that holds data (falls back to
    the failed_scan_queue / scan_error_images row of the same task_id). The instructor lookup
    reads the webhook body by name, since its input is now the image lookup's row."""
    conns = out["connections"]
    by_name = {n["name"]: n for n in out["nodes"]}
    for hook, lookup, instr, sql_file, params in CORRECTION_IMAGE_LOOKUPS:
        for name in (hook, lookup, instr):
            if name not in by_name:
                raise SystemExit(f"Input workflow has no node named {name!r}")
        hook_out = [c["node"] for c in conns.get(hook, {}).get("main", [[]])[0]]
        if sorted(hook_out) != sorted([instr, lookup]) or conns.get(lookup, {}).get("main"):
            raise SystemExit(f"{hook}: expected outputs {instr!r} + {lookup!r} (lookup unconnected); "
                             f"got {hook_out}; the input changed")
        ln, il = by_name[lookup], by_name[instr]
        if ln["parameters"].get("options", {}).get("queryReplacement", "").strip() not in (
                "={{ $json.body.failed_scan_id }}", "={{ $json.body.import_error_id }}"):
            raise SystemExit(f"{lookup}: unexpected parameters; the input changed")
        if il["parameters"].get("options", {}).get("queryReplacement") != "={{ $json.body.instructor || '' }}":
            raise SystemExit(f"{instr}: unexpected parameters; the input changed")
        sql = read(sql_file)
        assert_no_param_in_sql_comments(sql_file, sql)
        ln["parameters"]["query"] = sql
        ln["parameters"].setdefault("options", {})["queryReplacement"] = params
        ln["alwaysOutputData"] = True
        ln["onError"] = "continueRegularOutput"
        il["parameters"]["options"]["queryReplacement"] = \
            "={{ $('" + hook + "').first().json.body.instructor || '' }}"
        conns[hook]["main"][0] = [c for c in conns[hook]["main"][0] if c["node"] != instr]
        add_connection(conns, lookup, 0, instr, 0)


def guard_scan_image_save(node):
    """'is data from google link?' (true -> 'Save scan error image1') also requires an image to
    save. Without one (corrections and website rows carry no scan_image) it inserted an EMPTY
    scan_error_images row and the import error pointed at it; now such a row goes straight to
    'Format error for import_errors' (existing image id, or NULL)."""
    conds = node["parameters"]["conditions"]["conditions"]
    if [c.get("leftValue") for c in conds] != ["={{ $json.task_id }}", "={{ !!$json.existing_scan_image_id }}"] \
            or node["parameters"]["conditions"].get("combinator") != "and":
        raise SystemExit(f"{SCAN_IMAGE_SAVE_IF}: unexpected conditions; the input changed")
    conds.append({
        "id": SCAN_IMAGE_SAVE_GUARD_ID,
        "leftValue": "={{ !!(typeof $json.scan_image === 'string' && $json.scan_image.trim()) }}",
        "rightValue": "",
        "operator": {"type": "boolean", "operation": "true", "singleValue": True},
    })


# ---------------------------------------------------------------- corrections: id first, respond first
# sast-manual-correction (the website review page and Flutter's failed-scan screen). Flutter sends
# body.instructor_id / body.subject_id only when an autocomplete entry was picked (typing clears
# them); the website backend always sends both. Without a valid id the name/code lookup below runs
# exactly as before, so Flutter keeps working unchanged.
CORRECTION_HOOK = "sast-manual-correction1"
CORRECTION_INSTR = "get instructor id7"
CORRECTION_SUBJ = "get subject id6"
CORRECTION_MAP = "Map target"
CORRECTION_MARK = "Mark Queue Validated1"
CORRECTION_RESPOND = "Respond to Webhook5"
CORRECTION_AFTER = "After Correction Response"      # new: Respond to Webhook5 -> this -> If19
UUID_SQL_RE = "'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'"
CORRECTION_BODY = "$('" + CORRECTION_HOOK + "').first().json.body"
CORRECTION_INSTR_PARAMS = ("={{ [ String(" + CORRECTION_BODY + ".instructor ?? ''), "
                           "String(" + CORRECTION_BODY + ".instructor_id ?? '') ] }}")
CORRECTION_SUBJ_PARAMS = ("={{ [ $json.instructor_id ?? null, String(" + CORRECTION_BODY + ".subject ?? ''), "
                          "String(" + CORRECTION_BODY + ".subject_id ?? '') ] }}")
CORRECTION_AFTER_CODE = (
    "// webhook already answered, now pass Map target's items to If19\n"
    "return $('Map target').all().map((item) => ({ json: item.json }));\n")


def patch_correction_instructor_by_id(node):
    """'get instructor id7': body.instructor_id wins when it is the id of a creditable instructor
    (same rule as the name lookup: approved + DEPARTMENT_HEAD/FULL-TIME/PART-TIME, or a
    placeholder). Otherwise (no id, not a uuid, not creditable) the name lookup runs exactly as
    before. Runs after patch_correction_instructor_lookup and serialize_correction_image_lookups."""
    name = node["name"]
    opts = node["parameters"].setdefault("options", {})
    if opts.get("queryReplacement") != "={{ $('" + CORRECTION_HOOK + "').first().json.body.instructor || '' }}":
        raise SystemExit(f"{name}: unexpected parameters; the input changed")
    q = node["parameters"]["query"]
    q = replace_once(q, "WITH input AS (\n  SELECT btrim(COALESCE($1,'')) AS raw_name\n),\nb AS (\n", (
        "WITH input AS (\n"
        "  SELECT btrim(COALESCE($1,'')) AS raw_name,\n"
        "         lower(btrim(COALESCE($2,''))) AS want_id\n"
        "),\n"
        "by_id AS (\n"
        "  SELECT ui.id AS instructor_id, ui.first_name, ui.last_name, 1::real AS match_score\n"
        "  FROM public.user_info ui\n"
        "  CROSS JOIN input i\n"
        "  WHERE i.want_id ~ " + UUID_SQL_RE + "\n"
        "    AND ui.id::text = i.want_id\n"
        "    AND ( (ui.account_status = 'approved'\n"
        "           AND EXISTS (SELECT 1 FROM public.department_table dt\n"
        "                         JOIN public.roles r ON r.id = dt.roles\n"
        "                        WHERE dt.user_id = ui.id\n"
        "                          AND r.\"Roles\" IN ('DEPARTMENT_HEAD','FULL-TIME','PART-TIME')))\n"
        "          OR lower(COALESCE(ui.is_placeholder::text, 'false')) IN ('true','t','1','yes') )\n"
        "  LIMIT 1\n"
        "),\n"
        "b AS (\n"), name)
    q = replace_once(q, "    AND i.raw_name <> ''\n", "    AND i.raw_name <> ''\n    AND NOT EXISTS (SELECT 1 FROM by_id)\n", name)
    q = replace_once(q, (
        "  LIMIT 1\n)\n"
        "SELECT instructor_id, first_name, last_name, match_score FROM b\n"
        "UNION ALL\n"
        "SELECT NULL::uuid, NULL::text, NULL::text, 0::real\n"
        "WHERE NOT EXISTS (SELECT 1 FROM b);"), (
        "  LIMIT 1\n),\n"
        "hit AS (\n"
        "  SELECT instructor_id, first_name, last_name, match_score FROM by_id\n"
        "  UNION ALL\n"
        "  SELECT instructor_id, first_name, last_name, match_score FROM b\n"
        ")\n"
        "SELECT instructor_id, first_name, last_name, match_score FROM hit\n"
        "UNION ALL\n"
        "SELECT NULL::uuid, NULL::text, NULL::text, 0::real\n"
        "WHERE NOT EXISTS (SELECT 1 FROM hit);"), name)
    assert_no_param_in_sql_comments(name, q)
    node["parameters"]["query"] = q
    # explicit array: n8n drops an empty '{{ }}' value and splits a value on commas
    opts["queryReplacement"] = CORRECTION_INSTR_PARAMS


def patch_correction_subject_by_id(node):
    """'get subject id6': body.subject_id wins when it is the id of an existing subject; else the
    code/name lookup of body.subject runs exactly as before (it still links the subject to the
    resolved instructor for the current term). Parameters as an explicit array: with the old
    '{{ }}' list an empty instructor id was dropped and the subject shifted to $1."""
    name = node["name"]
    opts = node["parameters"].setdefault("options", {})
    if opts.get("queryReplacement") != ("={{ $json.instructor_id }}\n{{ $('" + CORRECTION_HOOK
                                        + "').item.json.body.subject }}"):
        raise SystemExit(f"{name}: unexpected parameters; the input changed")
    q = node["parameters"]["query"]
    q = replace_once(q, "         btrim(COALESCE($2,'')) AS raw\n),\n",
                     "         btrim(COALESCE($2,'')) AS raw,\n"
                     "         lower(btrim(COALESCE($3,''))) AS want_id\n),\n", name)
    q = replace_once(q, "by_code AS (\n", (
        "by_id AS (\n"
        "  SELECT s.id, s.subject_code, s.subject_name, s.department_id\n"
        "  FROM public.subjects s, a\n"
        "  WHERE a.want_id ~ " + UUID_SQL_RE + "\n"
        "    AND s.id::text = a.want_id\n"
        "  LIMIT 1\n"
        "),\n"
        "by_code AS (\n"), name)
    q = replace_once(q, "  FROM public.subjects s, p\n  WHERE length(p.code_key) >= 3\n",
                     "  FROM public.subjects s, p\n  WHERE NOT EXISTS (SELECT 1 FROM by_id)\n"
                     "    AND length(p.code_key) >= 3\n", name)
    q = replace_once(q, "  WHERE NOT EXISTS (SELECT 1 FROM by_code)\n",
                     "  WHERE NOT EXISTS (SELECT 1 FROM by_id)\n    AND NOT EXISTS (SELECT 1 FROM by_code)\n", name)
    q = replace_once(q, "m AS (SELECT * FROM by_code UNION ALL SELECT * FROM by_name),",
                     "m AS (SELECT * FROM by_id UNION ALL SELECT * FROM by_code UNION ALL SELECT * FROM by_name),", name)
    assert_no_param_in_sql_comments(name, q)
    node["parameters"]["query"] = q
    opts["queryReplacement"] = CORRECTION_SUBJ_PARAMS


def patch_correction_term(node):
    """'Map target': website review approvals (body.correction_source = 'website_review') are
    credited to body.term_id (the term stored on the queue row = the batch term); every other
    correction keeps the system's current term ('GET TERM BY ID6'), exactly as before."""
    name = node["name"]
    code = node["parameters"]["jsCode"]
    if code.count("data.current_term_id") != 2:
        raise SystemExit(f"{name}: expected two uses of data.current_term_id; the input changed")
    code = replace_once(code, "const data = $input.first().json;\n", (
        "const data = $input.first().json;\n\n"
        "// website_review uses the page's term, others the current term\n"
        "let correctedTermId = data.current_term_id;\n"
        "try {\n"
        "  const cb = $('" + CORRECTION_HOOK + "').first().json.body || {};\n"
        "  const ct = typeof cb.term_id === 'string' ? cb.term_id.trim().toLowerCase() : '';\n"
        "  if (cb.correction_source === 'website_review'\n"
        "      && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(ct)) {\n"
        "    correctedTermId = ct;\n"
        "  }\n"
        "} catch (e) {}\n"), name)
    code = replace_once(code, "    current_term_id:        data.current_term_id,\n",
                        "    current_term_id:        correctedTermId,\n", name)
    code = replace_once(code, "      term_id:            data.current_term_id     ?? null,\n",
                        "      term_id:            correctedTermId          ?? null,\n", name)
    node["parameters"]["jsCode"] = code


def respond_correction_before_shared_flow(out):
    """sast-manual-correction: answer BEFORE the shared flow. Before: 'Map target' fed both 'Mark
    Queue Validated1' (-> 'Respond to Webhook5') and If19; If19 sits higher on the canvas, so with
    executionOrder v1 the whole shared flow ran first and the caller waited for it (the website
    backend waits 180 s, Flutter's n8n-proxy 60 s). Now: Map target -> Mark Queue Validated1 ->
    Respond to Webhook5 -> 'After Correction Response' (re-emits Map target's items) -> If19.
    No Respond node is downstream of If19 (the Google Sheet webhook rule)."""
    conns = out["connections"]
    by_name = {n["name"]: n for n in out["nodes"]}
    for n in (CORRECTION_MAP, CORRECTION_MARK, CORRECTION_RESPOND, SHARED_ENTRY_CLEAN):
        if n not in by_name:
            raise SystemExit(f"Input workflow has no node named {n!r}")
    if CORRECTION_AFTER in by_name:
        raise SystemExit(f"Node name clash: {CORRECTION_AFTER}")
    map_out = conns.get(CORRECTION_MAP, {}).get("main", [])
    if len(map_out) != 1 or sorted(c["node"] for c in map_out[0]) != sorted([CORRECTION_MARK, SHARED_ENTRY_CLEAN]):
        raise SystemExit(f"{CORRECTION_MAP}: expected outputs {CORRECTION_MARK} + {SHARED_ENTRY_CLEAN}; the input changed")
    if [c["node"] for c in conns.get(CORRECTION_MARK, {}).get("main", [[]])[0]] != [CORRECTION_RESPOND]:
        raise SystemExit(f"{CORRECTION_MARK} no longer feeds {CORRECTION_RESPOND}; the input changed")
    if any(conns.get(CORRECTION_RESPOND, {}).get("main", [])) \
            or float(by_name[CORRECTION_RESPOND].get("typeVersion", 1)) < 1.1:
        raise SystemExit(f"{CORRECTION_RESPOND}: already connected or too old to pass its item on; the input changed")
    map_out[0] = [c for c in map_out[0] if c["node"] != SHARED_ENTRY_CLEAN]
    out["nodes"].append({
        "parameters": {"jsCode": CORRECTION_AFTER_CODE},
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
        "position": [-42192, 21712],
        "id": nid(CORRECTION_AFTER),
        "name": CORRECTION_AFTER,
    })
    add_connection(conns, CORRECTION_RESPOND, 0, CORRECTION_AFTER, 0)
    add_connection(conns, CORRECTION_AFTER, 0, SHARED_ENTRY_CLEAN, 0)


def swap_supabase_credentials(nodes):
    """Replace hardcoded apikey/Authorization headers with the supabaseApi credential."""
    swapped = []
    for n in nodes:
        if n.get("type") != "n8n-nodes-base.httpRequest":
            continue
        p = n.get("parameters", {})
        hp = p.get("headerParameters", {}).get("parameters", [])
        secret_headers = [h for h in hp if str(h.get("name", "")).lower() in ("apikey", "authorization")
                          and ("sb_secret" in str(h.get("value", "")) or "eyJ" in str(h.get("value", ""))
                               or "supabase.co" in str(p.get("url", "")))]
        if not secret_headers:
            continue
        keep = [h for h in hp if h not in secret_headers]
        if keep:
            p["headerParameters"]["parameters"] = keep
        else:
            p.pop("headerParameters", None)
            p["sendHeaders"] = False
        p["authentication"] = "predefinedCredentialType"
        p["nodeCredentialType"] = "supabaseApi"
        creds = n.setdefault("credentials", {})
        creds["supabaseApi"] = {"id": SUPABASE_CRED_ID, "name": SUPABASE_CRED_NAME}
        swapped.append(n["name"])
    return swapped


def apply_live_layout(out, layout_path):
    """Canvas positions only: every node that also exists (same name) in the live export keeps
    the position it has there. Parameters, connections, ids and credentials are NOT taken from
    it. Returns (number of nodes moved, names positioned from the live export)."""
    if not layout_path or not layout_path.exists():
        return 0, set()
    live = json.loads(layout_path.read_text(encoding="utf-8"))
    pos = {n["name"]: n.get("position") for n in live.get("nodes", [])}
    moved = 0
    placed = set()
    for n in out["nodes"]:
        p = pos.get(n["name"])
        if isinstance(p, list) and len(p) == 2:
            placed.add(n["name"])
            if p != n.get("position"):
                n["position"] = list(p)
                moved += 1
    return moved, placed


def place_new_nodes(out, placed):
    """Nodes that are not in the live export yet get a spot next to their neighbours on the live
    canvas (after the next live export, its position wins like for every other node)."""
    by_name = {n["name"]: n for n in out["nodes"]}
    if FORM_MATCH not in placed:
        a, b = by_name["Website Identity Check"]["position"], by_name["Website Decide"]["position"]
        by_name[FORM_MATCH]["position"] = [(a[0] + b[0]) // 2, max(a[1], b[1]) + 176]
    for name, before, after in ((WEB_KNOWN_NAMES, "Website Python OMR", "Website AI Validation"),
                                (PHONE_KNOWN_NAMES, PHONE_OMR_NODE, PHONE_GEMINI_NODE),
                                (PHONE_CANDIDATES, PHONE_NOT_FORM_NODE, PHONE_REMARK_CHECK)):
        if name not in placed:
            a, b = by_name[before]["position"], by_name[after]["position"]
            by_name[name]["position"] = [(a[0] + b[0]) // 2, max(a[1], b[1]) + 176]
    if SHEET_RESPONSE not in placed:
        m = by_name[SHEET_MAP]["position"]
        by_name[SHEET_RESPONSE]["position"] = [m[0] + 224, m[1] + 176]
        by_name[SHEET_AFTER]["position"] = [m[0] + 448, m[1] + 176]
        by_name[SHEET_ERROR_RESPONSE]["position"] = [m[0] - 448, m[1] + 352]
    if CORRECTION_AFTER not in placed:
        r = by_name[CORRECTION_RESPOND]["position"]
        by_name[CORRECTION_AFTER]["position"] = [r[0] + 224, r[1]]
    place_stamp_nodes(out, placed)
    place_correction_match_nodes(out, placed)


# ---------------------------------------------------------------- stamp rule (2026-10-16, migration 015)
# Every paper form carries a numbering-machine stamp (3..12 digits, as printed). Gemini returns every
# stamped number (stamp_numbers); the reconcilers keep one readable stamp (website_src/stamp.js) or
# flag the page (stamp_missing / stamp_unreadable / multiple_stamps). A stamp already stored for the
# term auto-rejects the page (duplicate_stamp): website 'Website Identity Check' ($6) and the race
# in 'Website Raw Upsert' (public.save_website_raw_row); phone 'Phone Stamp Check' -> 'Phone Stamp
# Verdict' before If31 (the phone answers success at If31). Corrections (sast-manual-correction) take
# body.stamp_no / body.stamp_source and answer HTTP 409 duplicate_stamp before anything is saved.
STAMP_PROMPT_PARAGRAPH = (
    "stamp_numbers: every number printed on the page by a numbering-machine stamp. A numbering stamp is a "
    "machine-printed number in ink (usually black) made of evenly spaced, same-size digits, often 5 to 9 digits "
    "long and sometimes starting with zeros. It can be anywhere on the page (top, bottom, a margin, inside or over "
    "the table) and in any orientation (upright, sideways, upside down or tilted); read it in its own orientation. "
    "Check marks, signatures or handwriting may cover part of it. Return each stamped number as a string of digits "
    "only, exactly as printed, keeping leading zeros; write ? for a digit you cannot read. If there are several "
    "stamped numbers, return all of them. Return [] when the page has no stamp. Do NOT include the student ID, "
    "dates, phone numbers, handwritten numbers, rating numbers, or the printed form codes and texts (for example "
    "\"SS Form2\", \"Revision 3\", \"February 4, 2009\")."
)
# authored with literal backslash-n and JSON-escaped quotes, like the rest of the prompt text
STAMP_PROMPT_ADD = r"\n\n--- STAMP ---\n" + json.dumps(STAMP_PROMPT_PARAGRAPH)[1:-1]
STAMP_SCHEMA_ADD = '"stamp_numbers": { "type": "ARRAY", "items": { "type": "STRING" } },\n        '
STAMP_REQUIRED_ANCHOR = '"student_id",\n        "document_check",'
STAMP_REQUIRED_NEW = '"student_id",\n        "stamp_numbers",\n        "document_check",'
STAMP_MARK = "// stamp rule (2026-10-16)"

# Two readers (2026-10-17): the OMR (/process, STAMP_FINDER + STAMP_READER) sends stamp_candidates with
# a tile sheet (upright close-ups of every place a stamp may be) and its own digit reads. Both Gemini
# requests attach the tile sheet as the LAST image (only when /process sent one) with the STAMP TILES
# paragraph, get the strict stamp-reading rules and two more required fields (stamp_numbers_page,
# stamp_reads_tiles); stamp.js accepts a stamp only when Gemini and the Python reader agree. The texts
# are verbatim from the tile pilot (scratchpad stampfind/gemini_tile_pilot.py, strict variant).
STAMP_TILE_TEXT = (
    "\n\n--- STAMP TILES (the LAST image) ---\n"
    "The last image is a tile sheet made by our scanner software. Each tile is labelled at its left "
    "(T1, T2, ...) and is a close-up of ONE place on this page where a numbering-machine stamp MAY be, "
    "already turned so its text runs left to right and enlarged. T<n>a / T<n>b are the same crop, the b "
    "tile turned 180 degrees: read only the one that is upright. Most tiles are NOT stamps (printed form "
    "text, logos, table lines, wood or paper texture, handwriting, check marks): ignore them.\n"
    "Fill three stamp fields:\n"
    "- stamp_numbers_page: the stamp numbers you read on the FULL PAGE image alone, as if there were no "
    "tiles.\n"
    "- stamp_reads_tiles: one entry per stamp you see in the tiles: the tile label and the number read "
    "from that tile. The same stamp can appear in two tiles: list it once. [] when no tile shows a stamp.\n"
    "- stamp_numbers: your final answer.\n"
    "Digits only, keep leading zeros, write ? for each digit you cannot read; never guess a worn, faint "
    "or missing digit. Ignore handwriting (student ID, dates) and printed form codes and texts "
    "(\"SS Form2\", \"Revision 3\", \"February 4, 2009\")."
)
STAMP_STRICT_TEXT = (
    "STAMP READING RULES (strict):\n"
    "- Read each digit of the numbering-machine stamp independently.\n"
    "- If ANY stroke of a digit is missing, broken, faint, smudged or ambiguous (e.g. 3 vs 7 vs 8, "
    "0 vs 9 vs 6, 1 vs 7), output ? for that digit. Never guess.\n"
    "- Never add or drop digits. Numbering-machine stamps have a fixed digit count, and a leading zero "
    "is printed as a real digit.\n"
    "- If more than one stamp impression is visible (in any tile or on the page), report all of them.\n"
    "- Prefer ? over a confident guess; a human will check.\n\n"
)
# the pilot put it at the end of the stamp section, i.e. just before '--- BUBBLE GRID ---'
STAMP_STRICT_ADD = json.dumps("\n\n" + STAMP_STRICT_TEXT.rstrip("\n"))[1:-1]
STAMP_TILE_SCHEMA_ADD = ('"stamp_numbers_page": { "type": "ARRAY", "items": { "type": "STRING" } },\n        '
                         '"stamp_reads_tiles": { "type": "ARRAY", "items": { "type": "OBJECT", "properties": { '
                         '"tile": { "type": "STRING" }, "number": { "type": "STRING" } }, "required": ["tile", "number"] } },\n        ')
STAMP_TILE_REQUIRED_NEW = ('"student_id",\n        "stamp_numbers",\n        "stamp_numbers_page",\n'
                           '        "stamp_reads_tiles",\n        "document_check",')
STAMP_TEXT_END = 'Return ONLY valid JSON matching the schema."'      # end of the prompt text part
PHONE_PAGE_PART_END = ('"data": "{{ $(\'SAST image from phone\').first().json.body.image }}"\n'
                       '          }\n        }\n      ]')
WEB_OMR_NODE = "Website Python OMR"
PHONE_OMR_NODE = "image to ocr and omr python"
STAMP_TILE_MAX_B64 = 4000000                      # a tile sheet is ~100-200 KB of base64


def stamp_tile_b64_js(omr_node: str) -> str:
    """JS (n8n expression body) -> the OMR's tile sheet base64, or '' (no block, /process failed, an
    old OMR, or anything that is not plain base64)."""
    return ("(() => { let t = ''; try { const sc = $('" + omr_node + "').first().json.stamp_candidates; "
            "t = (sc && typeof sc.tile_sheet_b64 === 'string') ? sc.tile_sheet_b64 : ''; } catch (e) { t = ''; } "
            "return (t.length > 100 && t.length < " + str(STAMP_TILE_MAX_B64) + " && t.indexOf('\"') === -1 "
            "&& t.indexOf(String.fromCharCode(92)) === -1) ? t : ''; })()")


def stamp_tile_part_expr(omr_node: str) -> str:
    """Last Gemini image part: the tile sheet, only when the OMR sent one (else nothing)."""
    js = ("(() => { const t = " + stamp_tile_b64_js(omr_node) + "; "
          "return t ? ',\\n        { \"inlineData\": { \"mimeType\": \"image/jpeg\", \"data\": \"' + t + '\" } }' : ''; })()")
    if "{{" in js or "}}" in js:
        raise SystemExit("tile part expression must not contain '{{' or '}}'")
    return "{{ " + js + " }}"


def stamp_tile_text_expr(omr_node: str) -> str:
    """The STAMP TILES paragraph (JSON-escaped like the static prompt text), only with a tile sheet."""
    js = ("(() => { const t = " + stamp_tile_b64_js(omr_node) + "; "
          "return t ? JSON.stringify(" + json.dumps(STAMP_TILE_TEXT) + ").slice(1, -1) : ''; })()")
    if "{{" in js or "}}" in js:
        raise SystemExit("tile text expression must not contain '{{' or '}}'")
    return "{{ " + js + " }}"


def patch_gemini_stamp_tiles(body: str, name: str, omr_node: str, page_part_end: str) -> str:
    """Tile sheet image + STAMP TILES paragraph + strict rules + the two extra required fields (once)."""
    body = replace_once(body, HEADER_BUBBLE_ANCHOR, STAMP_STRICT_ADD + HEADER_BUBBLE_ANCHOR, name)
    body = replace_once(body, STAMP_TEXT_END, STAMP_TEXT_END[:-1] + stamp_tile_text_expr(omr_node) + '"', name)
    body = replace_once(body, page_part_end, page_part_end[:-len("\n      ]")] + stamp_tile_part_expr(omr_node)
                        + "\n      ]", name)
    body = replace_once(body, STAMP_SCHEMA_ADD, STAMP_SCHEMA_ADD + STAMP_TILE_SCHEMA_ADD, name)
    return replace_once(body, STAMP_REQUIRED_NEW, STAMP_TILE_REQUIRED_NEW, name)


PHONE_ROUTE_IF = "If31"                          # needs_review false -> success answer + credit
PHONE_STAMP_CHECK = "Phone Stamp Check"          # Postgres (phone_stamp_check.sql), continue on error
PHONE_STAMP_VERDICT = "Phone Stamp Verdict"      # Code (phone_stamp_verdict.js)
CORR_STAMP_CHECK = "Correction Stamp Check"      # Postgres (phone_stamp_check.sql), continue on error
CORR_STAMP_IF = "Correction Stamp Duplicate?"
CORR_STAMP_RESPOND = "Correction Duplicate Stamp Response"   # HTTP 409 (or 503 check failed), nothing saved
_PREC = "$('" + PHONE_RECONCILE + "').first().json"
# an accepted stamp (auto-reject on a match), else the stamp_hint (a match only adds a note)
PHONE_STAMP_PARAMS = ("={{ [ '', (" + _PREC + ".stamp_status === 'ok' ? String(" + _PREC + ".stamp_no ?? '') : String("
                      + _PREC + ".stamp_hint ?? '')), String(" + _PREC + ".task_id ?? '') ] }}")
CORR_STAMP_PARAMS = ("={{ [ String($json.current_term_id ?? ''), String($json.stamp_no ?? ''), "
                     "String($json.task_id ?? '') ] }}")
# fail safe: a check that could not run (DB error, no duplicate_stamp_exists) on a correction that
# carries a stamp is answered as an error too (HTTP 503 stamp_check_failed, nothing saved); a
# correction without a stamp has nothing to check and goes on
CORR_STAMP_IF_EXPR = ("={{ $json.duplicate_stamp_exists === true || ($json.duplicate_stamp_exists !== false "
                      "&& String($('Map target').first().json.stamp_no ?? '') !== '') }}")
CORR_STAMP_IF_ID = "8f0b6c1e-web0-4d01-9a01-000000000009"
_CDUP = "$json.duplicate_stamp_of"
_CJS = ("JSON.stringify({ success: false, code: 'duplicate_stamp', "
        "message: 'This form was already scanned: stamp ' + String((" + _CDUP + " && " + _CDUP + ".stamp_no) "
        "|| $('Map target').first().json.stamp_no || '?') + ' is already stored for this term' "
        "+ ((" + _CDUP + " && " + _CDUP + ".submitted_date) ? ' (saved ' + " + _CDUP + ".submitted_date + ')' : '') "
        "+ '. Nothing was saved.', duplicate_of: (" + _CDUP + " || null) })")
_CFAIL = ("JSON.stringify({ success: false, code: 'stamp_check_failed', "
          "message: 'The stamp check could not run, so nothing was saved. Try again in a moment.', "
          "duplicate_of: null })")
CORR_STAMP_RESPONSE_BODY = "={{ $json.duplicate_stamp_exists === true ? " + _CJS + " : " + _CFAIL + " }}"
CORR_STAMP_RESPONSE_CODE = "={{ $json.duplicate_stamp_exists === true ? 409 : 503 }}"
MARK_QUERY_V1 = "UPDATE public.failed_scan_queue\nSET status = 'validated', validated_at = NOW()\nWHERE id = $1::uuid;"
MARK_PARAMS_V1 = "={{ $('sast-manual-correction1').first().json.body.failed_scan_id }}"
# the queue row also gets the stamp (only when the caller sent the stamp_no key: an older client
# that does not know the stamp leaves the stamp read on the page in place)
MARK_QUERY = ("UPDATE public.failed_scan_queue\n"
              "SET status = 'validated', validated_at = NOW(),\n"
              "    stamp_no = CASE WHEN $4 = 'true' THEN NULLIF($2, '') ELSE stamp_no END,\n"
              "    stamp_source = CASE WHEN $4 = 'true' THEN NULLIF($3, '') ELSE stamp_source END\n"
              "WHERE id = $1::uuid;")
_CB = "$('sast-manual-correction1').first().json.body"
MARK_PARAMS = ("={{ [ (" + _CB + ".failed_scan_id ?? null), String($('Map target').first().json.stamp_no ?? ''), "
               "String($('Map target').first().json.stamp_source ?? ''), "
               "String(Object.keys(" + _CB + " || {}).includes('stamp_no')) ] }}")
if any(m in x for x in (CORR_STAMP_RESPONSE_BODY[4:-3], MARK_PARAMS[4:-3], PHONE_STAMP_PARAMS[4:-3])
       for m in ("{{", "}}")):
    raise SystemExit("stamp expressions must not contain '{{' or '}}'")

STAMP_COLUMNS_HELPER = (
    "// stamp rule (2026-10-16): a valid stamp (3..12 digits, as printed) + its source, else NULL + NULL\n"
    "function stampColumns(r) {\n"
    "  const s = (typeof r['stamp_no'] === 'string' || typeof r['stamp_no'] === 'number') ? String(r['stamp_no']).trim() : '';\n"
    "  const ok = /^[0-9]{3,12}$/.test(s);\n"
    "  const src = ['gemini+python', 'gemini', 'staff', 'override'].includes(r['stamp_source']) ? r['stamp_source'] : 'staff';\n"
    "  return { stamp_no: ok ? s : null, stamp_source: ok ? src : null };\n"
    "}\n\n")
CIJ_STAMP_ANCHOR = "      ...(websiteRawStored ? { _raw_already_stored: true } : {})\n"
CIJ_STAMP_LINE = ("      // stamp only when the mapper sent one, so sheet rows never clear it\n"
                  "      ...(Object.prototype.hasOwnProperty.call(r, 'stamp_no') ? stampColumns(r) : {}),\n")
PHONE_MAP_STAMP_ANCHOR = '  result["task_id"] = separateData.task_id || null;\n'
PHONE_MAP_STAMP = (
    "  // stamp rule: the one accepted stamp of the page (a clean scan always has one)\n"
    "  if (separateData.stamp_status === 'ok' && separateData.stamp_no) {\n"
    '    result["stamp_no"] = separateData.stamp_no;\n'
    '    result["stamp_source"] = separateData.stamp_source || "gemini";\n'
    "  }\n")
MAP_TARGET_STAMP_ANCHOR = "return [{\n  json: {\n    instructor_ID:"
MAP_TARGET_STAMP = (
    STAMP_MARK + ": the stamp typed or confirmed by staff (website review or Flutter): 3..12\n"
    "// digits, else none. stamp_source 'override' (a rescued duplicate) or 'staff' (anything else).\n"
    "let correctionStamp = null;\n"
    "let correctionStampSource = null;\n"
    "try {\n"
    "  const sb = $('sast-manual-correction1').first().json.body || {};\n"
    "  const st = (typeof sb.stamp_no === 'string' || typeof sb.stamp_no === 'number') ? String(sb.stamp_no).trim() : '';\n"
    "  if (/^[0-9]{3,12}$/.test(st)) {\n"
    "    correctionStamp = st;\n"
    "    correctionStampSource = sb.stamp_source === 'override' ? 'override' : 'staff';\n"
    "  }\n"
    "} catch (e) {}\n\n")
MAP_TARGET_TOP_ANCHOR = "    task_id:                data.raw.task_id ?? null,\n"
MAP_TARGET_TOP = ("    stamp_no:               correctionStamp,         // -> raw row (NULL = no stamp)\n"
                  "    stamp_source:           correctionStampSource,\n")
MAP_TARGET_RAW_ANCHOR = "      task_id:            data.raw.task_id         ?? null,\n"
MAP_TARGET_RAW = ("      stamp_no:           correctionStamp,\n"
                  "      stamp_source:       correctionStampSource,\n")
GEMINI_FAILURE_REASONS_OLD = "    review_reasons: ['ai_validation_failed'],\n"
GEMINI_FAILURE_REASONS_NEW = "    review_reasons: ['ai_validation_failed', 'stamp_unreadable'],\n"
GEMINI_FAILURE_STAMP_ANCHOR = "    student_id: '',   // text only comes from Gemini\n"
GEMINI_FAILURE_STAMP = ("    stamp_no: null,   // stamp rule: nothing was read, so the stamp is unreadable\n"
                        "    stamp_status: 'unreadable',\n"
                        "    stamp_candidates: [],\n"
                        "    stamp_raw: null,\n")
BUILD_FLAGGED_PHONE_OLD = ("let rec;\nif (webRec) {\n  rec = webRec;\n} else try {\n"
                           "  rec = $('reconcile ocr and omr1').first().json;\n")
BUILD_FLAGGED_PHONE_NEW = (
    "// phone runs: the record after the stamp check ('Phone Stamp Verdict'), else the reconciler's\n"
    "let phoneRec = null;\n"
    "try {\n"
    "  const v = $('Phone Stamp Verdict').first();\n"
    "  phoneRec = (v && v.json && v.json._stage === 'phone_stamp_checked') ? v.json : null;\n"
    "} catch (eV) {}\n\n"
    "let rec;\nif (webRec) {\n  rec = webRec;\n} else if (phoneRec) {\n  rec = phoneRec;\n} else try {\n"
    "  rec = $('reconcile ocr and omr1').first().json;\n")
BUILD_FLAGGED_STAMP_BLOCK = (
    "\n" + STAMP_MARK + ": the stamp read on the page goes on every queue row (phone and website).\n"
    "// A stamp already stored for the term (auto_reject 'duplicate_stamp') stores the row discarded and\n"
    "// rejected; it comes after the min-answered rule and wins over it. Nobody has to review it and no\n"
    "// staff push is sent ('Get staff FCM for flagged scan' skips a discarded row).\n"
    "const STAMP_RE = /^[0-9]{3,12}$/;\n"
    "record.stamp_no = STAMP_RE.test(String(rec.stamp_no ?? '')) ? String(rec.stamp_no) : null;\n"
    "record.stamp_source = record.stamp_no ? (rec.stamp_source || 'gemini') : null;   // 'gemini+python': both readers\n"
    "record.partial_data = {\n"
    "  ...record.partial_data,\n"
    "  stamp_status: rec.stamp_status ?? record.partial_data?.stamp_status ?? null,\n"
    "  stamp_candidates: Array.isArray(rec.stamp_candidates) ? rec.stamp_candidates : (record.partial_data?.stamp_candidates ?? []),\n"
    "  stamp_raw: rec.stamp_raw ?? record.partial_data?.stamp_raw ?? null,\n"
    "  // two readers (2026-10-17): what Gemini and the OMR's stamp reader saw, and a possible duplicate\n"
    "  // of a stamp that was not accepted (a hint for staff, never an auto-reject)\n"
    "  stamp_check: rec.stamp_check ?? record.partial_data?.stamp_check ?? null,\n"
    "  stamp_hint: rec.stamp_hint ?? record.partial_data?.stamp_hint ?? null,\n"
    "  ...((rec.stamp_duplicate_hint || record.partial_data?.stamp_duplicate_hint)\n"
    "    ? { stamp_duplicate_hint: rec.stamp_duplicate_hint || record.partial_data.stamp_duplicate_hint } : {}),\n"
    "};\n"
    "if (rec.auto_reject === 'duplicate_stamp') {\n"
    "  const dup = (rec.duplicate_of && typeof rec.duplicate_of === 'object') ? rec.duplicate_of : {};\n"
    "  record.status = 'discarded';\n"
    "  record.review_action = 'rejected';\n"
    "  record.review_correction = {\n"
    "    action: 'rejected',\n"
    "    auto: true,\n"
    "    rule: 'duplicate_stamp',\n"
    "    note: 'This form was already scanned (stamp ' + (record.stamp_no || '?') + ')'\n"
    "      + (dup.submitted_date ? ', saved ' + dup.submitted_date : '')\n"
    "      + ', so this copy was discarded automatically and is not counted.',\n"
    "    reviewed_by: null,\n"
    "    reviewed_by_name: 'Automatic rule',\n"
    "    at: new Date().toISOString(),\n"
    "  };\n"
    "  record.review_reasons = ['duplicate_stamp',\n"
    "    ...record.review_reasons.filter((r) => r !== 'duplicate_stamp' && r !== 'reused_image' && r !== 'spot_check')];\n"
    "  record.partial_data = { ...record.partial_data, duplicate_of: dup, stamp_status: 'ok' };\n"
    "}\n")
RAW_SCHEMA_STAMP = [
    {"id": "stamp_no", "displayName": "stamp_no", "required": False, "defaultMatch": False, "display": True,
     "type": "string", "canBeUsedToMatch": False, "removed": False},
    {"id": "stamp_source", "displayName": "stamp_source", "required": False, "defaultMatch": False, "display": True,
     "type": "string", "canBeUsedToMatch": False, "removed": False},
]
# website nodes refreshed from website_src (a live export holds the previous build of them)
STAMP_WEB_CODE = [("Website Decide", "decide.js"), (WEBSITE_MAP, "website_map_to_target.js"),
                  ("Website Success Response", "website_success_response.js")]
STAMP_WEB_SQL = [("Website Identity Check", "identity_check.sql", "-- Batch still valid", IDENTITY_CHECK_PARAMS),
                 ("Website Raw Upsert", "website_raw_upsert.sql", "-- Same key as the shared upsert", RAW_UPSERT_PARAMS)]


def _patch_once(code: str, mark: str, anchor: str, new: str, where: str) -> tuple[str, bool]:
    """Insert/replace once (guarded): returns (code, changed). Already applied when mark is in code."""
    if mark in code:
        return code, False
    return replace_once(code, anchor, new, where), True


def _first_line(text: str) -> str:
    return text.split("\n", 1)[0]


def _stamp_positions(by_name):
    """Spots for the new nodes, next to their neighbours on the canvas."""
    r, i = by_name[PHONE_RECONCILE]["position"], by_name[PHONE_ROUTE_IF]["position"]
    m, k = by_name[CORRECTION_MAP]["position"], by_name[CORRECTION_MARK]["position"]
    a = by_name[CORRECTION_RESPOND]["position"]
    return {
        PHONE_STAMP_CHECK: [(r[0] + i[0]) // 2, max(r[1], i[1]) + 176],
        PHONE_STAMP_VERDICT: [i[0], max(r[1], i[1]) + 176],
        CORR_STAMP_CHECK: [(m[0] + k[0]) // 2, max(m[1], k[1]) + 176],
        CORR_STAMP_IF: [k[0], max(m[1], k[1]) + 176],
        CORR_STAMP_RESPOND: [a[0], max(m[1], a[1]) + 176],
    }


def place_stamp_nodes(out, placed):
    by_name = {n["name"]: n for n in out["nodes"]}
    for name, pos in _stamp_positions(by_name).items():
        if name in by_name and name not in placed:
            by_name[name]["position"] = pos


def apply_stamp_rule(out) -> list[str]:
    """Stamp rule (idempotent; full build and live export; needs migration 015). Returns what changed."""
    by_name = {n["name"]: n for n in out["nodes"]}
    conns = out["connections"]
    done = []
    for name in (PHONE_GEMINI_NODE, WEB_GEMINI_NODE, PHONE_RECONCILE, PHONE_ROUTE_IF, WEB_RECONCILE, BUILD_FLAGGED,
                 CODE_IN_JS, PHONE_MAP, CORRECTION_MAP, CORRECTION_MARK, CORRECTION_RESPOND, RAW_UPSERT,
                 PHONE_GEMINI_FAILURE, FLAGGED_FCM, *[n for n, _ in STAMP_WEB_CODE], *[n for n, *_ in STAMP_WEB_SQL]):
        if name not in by_name:
            raise SystemExit(f"apply_stamp_rule: no node named {name!r}")

    # 1. both main Gemini prompts: the stamp paragraph + required stamp_numbers (array of strings)
    for name in (PHONE_GEMINI_NODE, WEB_GEMINI_NODE):
        gem = by_name[name]["parameters"]
        body = gem["jsonBody"]
        if '"stamp_numbers": {' not in body:
            body = replace_once(body, HEADER_BUBBLE_ANCHOR, STAMP_PROMPT_ADD + HEADER_BUBBLE_ANCHOR, name)
            body = replace_once(body, KNOWN_SCHEMA_ANCHOR, STAMP_SCHEMA_ADD + KNOWN_SCHEMA_ANCHOR, name)
            body = replace_once(body, STAMP_REQUIRED_ANCHOR, STAMP_REQUIRED_NEW, name)
            gem["jsonBody"] = body
            done.append(f"{name}: stamp_numbers (prompt + required schema field)")
        if body.count(STAMP_PROMPT_ADD) != 1 or (body.count(STAMP_REQUIRED_NEW) + body.count(STAMP_TILE_REQUIRED_NEW)) != 1:
            raise SystemExit(f"{name}: stamp prompt/schema not exactly once")

    # 1b. two readers (2026-10-17): tile sheet (last image, only when /process sent one) + STAMP TILES
    #     paragraph + strict rules + stamp_numbers_page / stamp_reads_tiles (required); model unchanged
    web_page_end = header_image_part_expr("$('Website Prepare Scan').first(0).json") + "\n      ]"
    for name, omr, page_end in ((PHONE_GEMINI_NODE, PHONE_OMR_NODE, PHONE_PAGE_PART_END),
                                (WEB_GEMINI_NODE, WEB_OMR_NODE, web_page_end)):
        if omr not in by_name:
            raise SystemExit(f"apply_stamp_rule: no node named {omr!r}")
        gem = by_name[name]["parameters"]
        if '"stamp_reads_tiles": {' not in gem["jsonBody"]:
            gem["jsonBody"] = patch_gemini_stamp_tiles(gem["jsonBody"], name, omr, page_end)
            done.append(f"{name}: stamp tile sheet (last image) + STAMP TILES / strict rules + stamp_numbers_page, "
                        "stamp_reads_tiles")
        body = gem["jsonBody"]
        for part in (STAMP_STRICT_ADD, stamp_tile_text_expr(omr), stamp_tile_part_expr(omr), STAMP_TILE_SCHEMA_ADD,
                     STAMP_TILE_REQUIRED_NEW):
            if body.count(part) != 1:
                raise SystemExit(f"{name}: stamp tile patch not exactly once ({part[:40]!r})")
        if body.find(stamp_tile_part_expr(omr)) < body.find('"inlineData"'):
            raise SystemExit(f"{name}: the tile sheet must be the last image")

    # 2. both reconcilers carry stamp.js (they are refreshed from website_src by apply_check_only_rule /
    #    apply_phone_name_privacy); here only checked
    for name, src in ((WEB_RECONCILE, "reconcile.js"), (PHONE_RECONCILE, "phone_reconcile.js")):
        if by_name[name]["parameters"]["jsCode"] != read_js(src) or "stReadStamps(" not in read_js(src):
            raise SystemExit(f"{name}: not the current {src} with the stamp rule; run the reconciler refresh first")

    # 3. website nodes from website_src (a live export holds the previous build)
    for name, src in STAMP_WEB_CODE:
        p = by_name[name]["parameters"]
        new = read_js(src)
        if p.get("jsCode") != new:
            if _first_line(p.get("jsCode", "")) != _first_line(new):
                raise SystemExit(f"{name}: not a previous build of {src} (hand edit?)")
            p["jsCode"] = new
            done.append(f"{name}: {src}")
    for name, src, head, params in STAMP_WEB_SQL:
        p = by_name[name]["parameters"]
        sql = read(src)
        assert_no_param_in_sql_comments(name, sql)
        if p.get("query") != sql or p.get("options", {}).get("queryReplacement") != params:
            if not str(p.get("query", "")).startswith(head):
                raise SystemExit(f"{name}: not a previous build of {src} (hand edit?)")
            p["query"] = sql
            p.setdefault("options", {})["queryReplacement"] = params
            done.append(f"{name}: {src} + parameters")

    # 4. shared nodes
    bf = by_name[BUILD_FLAGGED]["parameters"]
    if MIN_ANSWERS_MARK not in bf["jsCode"]:
        raise SystemExit(f"{BUILD_FLAGGED}: the min-answered block must be applied first")
    code, ch1 = _patch_once(bf["jsCode"], "$('Phone Stamp Verdict')", BUILD_FLAGGED_PHONE_OLD, BUILD_FLAGGED_PHONE_NEW,
                            BUILD_FLAGGED)
    code, ch2 = _patch_once(code, STAMP_MARK, MIN_ANSWERS_ANCHOR, BUILD_FLAGGED_STAMP_BLOCK + MIN_ANSWERS_ANCHOR,
                            BUILD_FLAGGED)
    if code.find(MIN_ANSWERS_MARK) > code.find(STAMP_MARK):
        raise SystemExit(f"{BUILD_FLAGGED}: the stamp block must come after the min-answered block")
    if ch1 or ch2:
        bf["jsCode"] = code
        done.append(f"{BUILD_FLAGGED}: stamp columns, duplicate_stamp -> discarded/rejected, phone stamp verdict")

    cij = by_name[CODE_IN_JS]["parameters"]
    code, ch1 = _patch_once(cij["jsCode"], "function stampColumns(", "// Map the raw data and include the new database flags\n",
                            STAMP_COLUMNS_HELPER + "// Map the raw data and include the new database flags\n", CODE_IN_JS)
    code, ch2 = _patch_once(code, "? stampColumns(r) : {}", CIJ_STAMP_ANCHOR, CIJ_STAMP_LINE + CIJ_STAMP_ANCHOR, CODE_IN_JS)
    if ch1 or ch2:
        cij["jsCode"] = code
        done.append(f"{CODE_IN_JS}: stamp_no / stamp_source (only when the mapper sent one)")

    pm = by_name[PHONE_MAP]["parameters"]
    code, ch = _patch_once(pm["jsCode"], 'result["stamp_no"]', PHONE_MAP_STAMP_ANCHOR,
                           PHONE_MAP_STAMP_ANCHOR + PHONE_MAP_STAMP, PHONE_MAP)
    if ch:
        pm["jsCode"] = code
        done.append(f"{PHONE_MAP}: stamp_no (gemini)")

    mt = by_name[CORRECTION_MAP]["parameters"]
    code, ch1 = _patch_once(mt["jsCode"], STAMP_MARK, MAP_TARGET_STAMP_ANCHOR, MAP_TARGET_STAMP + MAP_TARGET_STAMP_ANCHOR,
                            CORRECTION_MAP)
    code, ch2 = _patch_once(code, "stamp_no:               correctionStamp", MAP_TARGET_TOP_ANCHOR,
                            MAP_TARGET_TOP_ANCHOR + MAP_TARGET_TOP, CORRECTION_MAP)
    code, ch3 = _patch_once(code, "stamp_no:           correctionStamp", MAP_TARGET_RAW_ANCHOR,
                            MAP_TARGET_RAW_ANCHOR + MAP_TARGET_RAW, CORRECTION_MAP)
    if ch1 or ch2 or ch3:
        mt["jsCode"] = code
        done.append(f"{CORRECTION_MAP}: body.stamp_no / body.stamp_source")

    gf = by_name[PHONE_GEMINI_FAILURE]["parameters"]
    code, ch1 = _patch_once(gf["jsCode"], GEMINI_FAILURE_REASONS_NEW, GEMINI_FAILURE_REASONS_OLD,
                            GEMINI_FAILURE_REASONS_NEW, PHONE_GEMINI_FAILURE)
    code, ch2 = _patch_once(code, "stamp_status: 'unreadable'", GEMINI_FAILURE_STAMP_ANCHOR,
                            GEMINI_FAILURE_STAMP_ANCHOR + GEMINI_FAILURE_STAMP, PHONE_GEMINI_FAILURE)
    if ch1 or ch2:
        gf["jsCode"] = code
        done.append(f"{PHONE_GEMINI_FAILURE}: stamp_unreadable")

    schema = by_name[RAW_UPSERT]["parameters"].get("columns", {}).get("schema")
    if not isinstance(schema, list) or not any(c.get("id") == "task_id" for c in schema):
        raise SystemExit(f"{RAW_UPSERT}: unexpected column schema; the input changed")
    added = [copy.deepcopy(c) for c in RAW_SCHEMA_STAMP if not any(x.get("id") == c["id"] for x in schema)]
    if added:
        schema.extend(added)
        done.append(f"{RAW_UPSERT}: column schema + stamp_no, stamp_source")

    if by_name[FLAGGED_FCM]["parameters"].get("options", {}).get("queryReplacement") != FLAGGED_FCM_PARAMS:
        raise SystemExit(f"{FLAGGED_FCM}: must skip a discarded row (apply_min_answers_rule first)")

    # 5. phone: reconcile ocr and omr1 -> Phone Stamp Check -> Phone Stamp Verdict -> If31
    pos = _stamp_positions(by_name)
    if PHONE_STAMP_CHECK not in by_name:
        outs = conns.get(PHONE_RECONCILE, {}).get("main", [])
        if len(outs) != 1 or [c["node"] for c in outs[0]] != [PHONE_ROUTE_IF]:
            raise SystemExit(f"{PHONE_RECONCILE} does not feed only {PHONE_ROUTE_IF}: {outs}; the input changed")
        if PHONE_STAMP_VERDICT in by_name:
            raise SystemExit(f"Node name clash: {PHONE_STAMP_VERDICT}")
        outs[0] = []
        out["nodes"].append(pg_node(PHONE_STAMP_CHECK, pos[PHONE_STAMP_CHECK], read("phone_stamp_check.sql"),
                                    PHONE_STAMP_PARAMS, "continueRegularOutput", always_output=True))
        out["nodes"].append(code_node(PHONE_STAMP_VERDICT, pos[PHONE_STAMP_VERDICT], "phone_stamp_verdict.js"))
        add_connection(conns, PHONE_RECONCILE, 0, PHONE_STAMP_CHECK, 0)
        add_connection(conns, PHONE_STAMP_CHECK, 0, PHONE_STAMP_VERDICT, 0)
        add_connection(conns, PHONE_STAMP_VERDICT, 0, PHONE_ROUTE_IF, 0)
        done.append(f"added {PHONE_RECONCILE} -> {PHONE_STAMP_CHECK} -> {PHONE_STAMP_VERDICT} -> {PHONE_ROUTE_IF}")
    else:
        by_name[PHONE_STAMP_CHECK]["parameters"]["query"] = read("phone_stamp_check.sql")
        by_name[PHONE_STAMP_CHECK]["parameters"]["options"] = {"queryReplacement": PHONE_STAMP_PARAMS}
        by_name[PHONE_STAMP_VERDICT]["parameters"]["jsCode"] = read_js("phone_stamp_verdict.js")

    # 6. corrections: Map target -> Correction Stamp Check -> Correction Stamp Duplicate? -> (true) 409
    #    answer, nothing saved; (false) Mark Queue Validated1 (+ the stamp) -> Respond to Webhook5 -> ...
    if CORR_STAMP_CHECK not in by_name:
        outs = conns.get(CORRECTION_MAP, {}).get("main", [])
        if len(outs) != 1 or [c["node"] for c in outs[0]] != [CORRECTION_MARK]:
            raise SystemExit(f"{CORRECTION_MAP} does not feed only {CORRECTION_MARK}: {outs}; the input changed")
        if any(n in by_name for n in (CORR_STAMP_IF, CORR_STAMP_RESPOND)):
            raise SystemExit("Node name clash: correction stamp nodes")
        outs[0] = []
        out["nodes"].append(pg_node(CORR_STAMP_CHECK, pos[CORR_STAMP_CHECK], read("phone_stamp_check.sql"),
                                    CORR_STAMP_PARAMS, "continueRegularOutput", always_output=True))
        out["nodes"].append(if_node(CORR_STAMP_IF, pos[CORR_STAMP_IF], CORR_STAMP_IF_EXPR, CORR_STAMP_IF_ID))
        resp = respond_node(CORR_STAMP_RESPOND, pos[CORR_STAMP_RESPOND])
        resp["parameters"] = {"respondWith": "json", "responseBody": CORR_STAMP_RESPONSE_BODY,
                              "options": {"responseCode": CORR_STAMP_RESPONSE_CODE}}
        out["nodes"].append(resp)
        add_connection(conns, CORRECTION_MAP, 0, CORR_STAMP_CHECK, 0)
        add_connection(conns, CORR_STAMP_CHECK, 0, CORR_STAMP_IF, 0)
        add_connection(conns, CORR_STAMP_IF, 0, CORR_STAMP_RESPOND, 0)
        add_connection(conns, CORR_STAMP_IF, 1, CORRECTION_MARK, 0)
        done.append(f"added {CORRECTION_MAP} -> {CORR_STAMP_CHECK} -> {CORR_STAMP_IF} (true: {CORR_STAMP_RESPOND} "
                    f"409; false: {CORRECTION_MARK})")
    else:
        by_name[CORR_STAMP_CHECK]["parameters"]["query"] = read("phone_stamp_check.sql")
        by_name[CORR_STAMP_CHECK]["parameters"]["options"] = {"queryReplacement": CORR_STAMP_PARAMS}
        by_name[CORR_STAMP_RESPOND]["parameters"]["responseBody"] = CORR_STAMP_RESPONSE_BODY
        by_name[CORR_STAMP_RESPOND]["parameters"]["options"] = {"responseCode": CORR_STAMP_RESPONSE_CODE}
        by_name[CORR_STAMP_IF]["parameters"]["conditions"]["conditions"][0]["leftValue"] = CORR_STAMP_IF_EXPR
    mk = by_name[CORRECTION_MARK]["parameters"]
    if mk.get("query") != MARK_QUERY or mk.get("options", {}).get("queryReplacement") != MARK_PARAMS:
        if mk.get("query", "").strip() != MARK_QUERY_V1 or mk.get("options", {}).get("queryReplacement") != MARK_PARAMS_V1:
            raise SystemExit(f"{CORRECTION_MARK}: unexpected query/parameters; the input changed")
        mk["query"] = MARK_QUERY
        mk.setdefault("options", {})["queryReplacement"] = MARK_PARAMS
        done.append(f"{CORRECTION_MARK}: queue row also gets stamp_no / stamp_source")

    assert_no_instructor_names_to_gemini(out)
    return done


# ---------------------------------------------------------------- corrections: never validate an unresolved scan
# 2026-10-24. Before: a correction whose instructor/subject did not resolve (Flutter sent only the typed
# text, the strict SQL name lookup missed it) still marked the failed_scan_queue row validated and then
# went If19 (false) -> import_errors, where staff had to pick the instructor and subject AGAIN. Now:
#   get existing scan image id -> Correction Candidates (Postgres, correction_candidates.sql) -> Correction
#   Name Match (correction_name_match.js: body ids, else a clear LOCAL name_match.js match) -> get
#   instructor id7 (by id, else the old strict name lookup) -> ...
#   Map target -> Correction Ids Found? (true) -> Correction Stamp Check -> ... -> Mark Queue Validated1
#                                       (false) -> Correction Not Found -> Correction Not Found Response
#   (HTTP 422 {success:false, code, message, candidates}; nothing saved, the queue row stays pending).
CORR_IMAGE_LOOKUP = "get existing scan image id"
CORR_CANDIDATES = "Correction Candidates"
CORR_NAME_MATCH = "Correction Name Match"
CORR_IDS_IF = "Correction Ids Found?"
CORR_IDS_IF_ID = "8f0b6c1e-web0-4d01-9a01-000000000010"
CORR_IDS_IF_EXPR = ("={{ String($json.instructor_ID ?? '').trim() !== '' "
                    "&& String($json.true_subject_id ?? '').trim() !== '' }}")
CORR_NOT_FOUND = "Correction Not Found"
CORR_NOT_FOUND_RESPOND = "Correction Not Found Response"
CORR_NOT_FOUND_BODY = "={{ JSON.stringify($json.response) }}"
CORR_NOT_FOUND_CODE = 422
_CM = "$('" + CORR_NAME_MATCH + "').first().json"
# the match node's values, else (it failed: onError continue) the webhook body exactly as before
CORRECTION_INSTR_PARAMS_V2 = (
    "={{ [ String(" + _CM + ".instructor_name ?? " + CORRECTION_BODY + ".instructor ?? ''), "
    "String(" + _CM + ".instructor_id ?? " + CORRECTION_BODY + ".instructor_id ?? '') ] }}")
CORRECTION_SUBJ_PARAMS_V2 = (
    "={{ [ $json.instructor_id ?? null, "
    "String(" + _CM + ".subject_text ?? " + CORRECTION_BODY + ".subject ?? ''), "
    "String(" + _CM + ".subject_id ?? " + CORRECTION_BODY + ".subject_id ?? '') ] }}")


def _correction_match_positions(by_name):
    a, b = by_name[CORR_IMAGE_LOOKUP]["position"], by_name[CORRECTION_INSTR]["position"]
    m = by_name[CORRECTION_MAP]["position"]
    y = min(a[1], b[1]) - 176
    return {
        CORR_CANDIDATES: [a[0] + (b[0] - a[0]) // 4, y],
        CORR_NAME_MATCH: [a[0] + 3 * (b[0] - a[0]) // 4, y],
        CORR_IDS_IF: [m[0] + 112, m[1] - 176],
        CORR_NOT_FOUND: [m[0] + 224, m[1] - 176],
        CORR_NOT_FOUND_RESPOND: [m[0] + 448, m[1] - 176],
    }


def place_correction_match_nodes(out, placed):
    by_name = {n["name"]: n for n in out["nodes"]}
    for name, pos in _correction_match_positions(by_name).items():
        if name in by_name and name not in placed:
            by_name[name]["position"] = pos


def apply_correction_name_match(out) -> list[str]:
    """sast-manual-correction never marks a scan validated (nor makes an import error) unless its
    instructor AND subject resolved; text-only corrections are matched locally (idempotent; full
    build and live export; needs apply_stamp_rule first). Returns what changed."""
    by_name = {n["name"]: n for n in out["nodes"]}
    conns = out["connections"]
    done = []
    for name in (CORRECTION_HOOK, CORR_IMAGE_LOOKUP, CORRECTION_INSTR, CORRECTION_SUBJ, CORRECTION_MAP,
                 CORR_STAMP_CHECK, CORRECTION_MARK):
        if name not in by_name:
            raise SystemExit(f"apply_correction_name_match: no node named {name!r}")
    pos = _correction_match_positions(by_name)
    cand_sql = read("correction_candidates.sql")
    assert_no_param_in_sql_comments(CORR_CANDIDATES, cand_sql)

    # 1. webhook -> image lookup -> Correction Candidates -> Correction Name Match -> get instructor id7
    if CORR_NAME_MATCH not in by_name:
        outs = conns.get(CORR_IMAGE_LOOKUP, {}).get("main", [])
        if len(outs) != 1 or [c["node"] for c in outs[0]] != [CORRECTION_INSTR]:
            raise SystemExit(f"{CORR_IMAGE_LOOKUP} does not feed only {CORRECTION_INSTR}: {outs}; the input changed")
        if CORR_CANDIDATES in by_name:
            raise SystemExit(f"Node name clash: {CORR_CANDIDATES}")
        outs[0] = []
        out["nodes"].append(pg_node(CORR_CANDIDATES, pos[CORR_CANDIDATES], cand_sql, None,
                                    "continueRegularOutput", always_output=True))
        out["nodes"].append(code_node(CORR_NAME_MATCH, pos[CORR_NAME_MATCH], "correction_name_match.js",
                                      on_error="continueRegularOutput"))
        add_connection(conns, CORR_IMAGE_LOOKUP, 0, CORR_CANDIDATES, 0)
        add_connection(conns, CORR_CANDIDATES, 0, CORR_NAME_MATCH, 0)
        add_connection(conns, CORR_NAME_MATCH, 0, CORRECTION_INSTR, 0)
        done.append(f"added {CORR_IMAGE_LOOKUP} -> {CORR_CANDIDATES} -> {CORR_NAME_MATCH} -> {CORRECTION_INSTR}")
    else:
        cp, mp = by_name[CORR_CANDIDATES]["parameters"], by_name[CORR_NAME_MATCH]["parameters"]
        if cp.get("query") != cand_sql:
            cp["query"] = cand_sql
            cp["options"] = {}
            done.append(f"{CORR_CANDIDATES}: correction_candidates.sql")
        if mp.get("jsCode") != read_js("correction_name_match.js"):
            mp["jsCode"] = read_js("correction_name_match.js")
            done.append(f"{CORR_NAME_MATCH}: correction_name_match.js")

    # 2. the SQL lookups read the match node (body values when it failed)
    for name, old, new in ((CORRECTION_INSTR, CORRECTION_INSTR_PARAMS, CORRECTION_INSTR_PARAMS_V2),
                           (CORRECTION_SUBJ, CORRECTION_SUBJ_PARAMS, CORRECTION_SUBJ_PARAMS_V2)):
        opts = by_name[name]["parameters"].setdefault("options", {})
        if opts.get("queryReplacement") == new:
            continue
        if opts.get("queryReplacement") != old:
            raise SystemExit(f"{name}: unexpected parameters; the input changed")
        opts["queryReplacement"] = new
        done.append(f"{name}: parameters from {CORR_NAME_MATCH}")

    # 3. Map target -> Correction Ids Found? -> (true) Correction Stamp Check; (false) 422, nothing saved
    if CORR_IDS_IF not in by_name:
        outs = conns.get(CORRECTION_MAP, {}).get("main", [])
        if len(outs) != 1 or [c["node"] for c in outs[0]] != [CORR_STAMP_CHECK]:
            raise SystemExit(f"{CORRECTION_MAP} does not feed only {CORR_STAMP_CHECK}: {outs}; the input changed")
        if any(n in by_name for n in (CORR_NOT_FOUND, CORR_NOT_FOUND_RESPOND)):
            raise SystemExit("Node name clash: correction not-found nodes")
        outs[0] = []
        out["nodes"].append(if_node(CORR_IDS_IF, pos[CORR_IDS_IF], CORR_IDS_IF_EXPR, CORR_IDS_IF_ID))
        out["nodes"].append(code_node(CORR_NOT_FOUND, pos[CORR_NOT_FOUND], "correction_not_found.js"))
        resp = respond_node(CORR_NOT_FOUND_RESPOND, pos[CORR_NOT_FOUND_RESPOND])
        resp["parameters"] = {"respondWith": "json", "responseBody": CORR_NOT_FOUND_BODY,
                              "options": {"responseCode": CORR_NOT_FOUND_CODE}}
        out["nodes"].append(resp)
        add_connection(conns, CORRECTION_MAP, 0, CORR_IDS_IF, 0)
        add_connection(conns, CORR_IDS_IF, 0, CORR_STAMP_CHECK, 0)
        add_connection(conns, CORR_IDS_IF, 1, CORR_NOT_FOUND, 0)
        add_connection(conns, CORR_NOT_FOUND, 0, CORR_NOT_FOUND_RESPOND, 0)
        done.append(f"added {CORRECTION_MAP} -> {CORR_IDS_IF} (true: {CORR_STAMP_CHECK}; false: {CORR_NOT_FOUND} -> "
                    f"{CORR_NOT_FOUND_RESPOND} 422)")
    else:
        nf = by_name[CORR_NOT_FOUND]["parameters"]
        if nf.get("jsCode") != read_js("correction_not_found.js"):
            nf["jsCode"] = read_js("correction_not_found.js")
            done.append(f"{CORR_NOT_FOUND}: correction_not_found.js")
        by_name[CORR_NOT_FOUND_RESPOND]["parameters"] = {
            "respondWith": "json", "responseBody": CORR_NOT_FOUND_BODY,
            "options": {"responseCode": CORR_NOT_FOUND_CODE}}
        by_name[CORR_IDS_IF]["parameters"]["conditions"]["conditions"][0]["leftValue"] = CORR_IDS_IF_EXPR

    assert_no_instructor_names_to_gemini(out)
    return done


# OCR cross-check (2026-10-28, panel recommendations #2, #10, #11): website_src/ocr_crosscheck.js in both
# reconcilers (the AI's student ID / remark vs the OMR's ocr_meta.ink; Gemini's corrected_rows),
# INVALID_MARK_TEXT gains 'double' (OMR mark_detect: two marks in one row), and both main Gemini
# prompts get one white-ink line plus the required corrected_rows array.
WHITE_INK_LINE = ("- A mark covered by correction fluid or tape (white-out), or heavily crossed out, is erased and is "
                  "not an answer: read only the remaining uncovered check, and list that row (m1-m10, p1-p10) in "
                  "corrected_rows ([] if none).")
CORRECTED_SCHEMA_ANCHOR = '"column_order": { "type": "ARRAY", "items": { "type": "STRING" } },'
CORRECTED_SCHEMA = '\n        "corrected_rows": { "type": "ARRAY", "items": { "type": "STRING" } },'
CORRECTED_REQUIRED_ANCHOR = '"scan_quality",\n        "ratings"\n      ]'
CORRECTED_REQUIRED = '"scan_quality",\n        "corrected_rows",\n        "ratings"\n      ]'
OCR_CROSSCHECK_CALL = "ocCrossCheck(py, gemini, geminiOk"


def apply_ocr_crosscheck(out) -> list[str]:
    """Panel recommendations #2/#10/#11 (idempotent; full build and live export; needs the stamp rule
    and the check-only line first): both reconcilers refreshed from website_src (ocr_crosscheck.js,
    INVALID_MARK_TEXT 'double'); both main Gemini prompts get WHITE_INK_LINE right after the check-only
    line and a required corrected_rows array in the schema. No instructor names. Returns what changed."""
    by_name = {n["name"]: n for n in out["nodes"]}
    done = []
    for name in (PHONE_GEMINI_NODE, WEB_GEMINI_NODE):
        gem = by_name[name]["parameters"]
        body = gem["jsonBody"]
        if WHITE_INK_LINE not in body:
            if body.count(CHECK_ONLY_LINE) != 1:
                raise SystemExit(f"{name}: check-only line missing; run apply_check_only_rule first")
            body = replace_once(body, CHECK_ONLY_ANCHOR + r"\n", CHECK_ONLY_ANCHOR + r"\n" + WHITE_INK_LINE + r"\n", name)
            done.append(f"{name}: white-ink prompt line")
        if '"corrected_rows"' not in body:
            body = replace_once(body, CORRECTED_SCHEMA_ANCHOR, CORRECTED_SCHEMA_ANCHOR + CORRECTED_SCHEMA, name)
            body = replace_once(body, CORRECTED_REQUIRED_ANCHOR, CORRECTED_REQUIRED, name)
            done.append(f"{name}: corrected_rows in the response schema (required)")
        gem["jsonBody"] = body
        if body.count(WHITE_INK_LINE) != 1 or body.count('"corrected_rows"') != 2:
            raise SystemExit(f"{name}: white-ink line / corrected_rows schema not exactly once")
    for name, src, marks in ((WEB_RECONCILE, "reconcile.js", (WEB_RECONCILE_MARK,)),
                             (PHONE_RECONCILE, "phone_reconcile.js", (PRIVACY_PHONE_RECONCILE_MARK,))):
        rec = by_name[name]["parameters"]
        new_rec = read_js(src)
        if OCR_CROSSCHECK_CALL not in new_rec:
            raise SystemExit(f"website_src/{src}: lacks the OCR cross-check")
        if rec["jsCode"] != new_rec:
            if not any(m in rec["jsCode"] for m in marks):
                raise SystemExit(f"{name}: not a previous build of {src} (hand edit?)")
            rec["jsCode"] = new_rec
            done.append(f"{name}: {src} (OCR cross-check, double mark)")
    assert_no_instructor_names_to_gemini(out)
    return done


def live_main(argv):
    """--live LIVE_EXPORT [OUTPUT]: apply only the migration-013 step (apply_sast_v2) to a live n8n
    export of this build. Everything else (node ids, positions, credentials, name, id, versionId,
    active) stays exactly as exported, so importing the result never rolls back newer live work."""
    if not argv:
        raise SystemExit("usage: build_website_branch.py --live LIVE_EXPORT_JSON [OUTPUT_JSON]")
    src = Path(argv[0])
    dst = Path(argv[1]) if len(argv) > 1 else HERE.parent / (src.stem.replace(" ", "-") + ".json")
    if dst.resolve() == src.resolve():
        raise SystemExit("output must differ from the live export (the export is never modified)")
    wf = json.loads(src.read_text(encoding="utf-8"))
    out = copy.deepcopy(wf)
    apply_sast_v2(out)
    privacy = apply_phone_name_privacy(out)
    check_only = apply_check_only_rule(out)
    min_answers = apply_min_answers_rule(out)
    stamp = apply_stamp_rule(out)
    words = apply_wordcloud_words(out)
    sentences = apply_ai_sentences(out)
    interp = apply_interpretation_summary(out)
    tones = apply_remark_tone_rules(out)
    sheet = apply_sheet_header_match(out)
    remarks = apply_remark_placeholders(out)
    pacing = apply_gemini_rate_limit(out)
    early = apply_sheet_early_response(out)
    jobs = apply_sheet_import_jobs(out)
    corr_match = apply_correction_name_match(out)
    crosscheck = apply_ocr_crosscheck(out)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"input : {src} (name {wf.get('name')!r}, id {wf.get('id')}, versionId {wf.get('versionId')})")
    print(f"output: {dst}")
    print(f"nodes : {len(wf['nodes'])} -> {len(out['nodes'])} (removed {len(SAST_DEAD_NODES)}: "
          f"{', '.join(SAST_DEAD_NODES)})")
    print(f"flow  : {RAW_UPSERT_GATE} -> {SAST_PAIRS} -> {SAST_RECOMPUTE} (recompute_sast) -> {SAST_ALERT_IF}")
    print(f"null ratings: {CODE_IN_JS}, {FORMAT_FINAL}, {PHONE_MAP}, {WEBSITE_MAP}, {CORRECTION_BUILD}, "
          f"{', '.join(CORRECTION_MAPS)}")
    print("phone name privacy: " + ("; ".join(privacy) if privacy else "already applied"))
    print("check-only rule: " + ("; ".join(check_only) if check_only else "already applied"))
    print("min-answered rule: " + ("; ".join(min_answers) if min_answers else "already applied"))
    print("stamp rule: " + ("; ".join(stamp) if stamp else "already applied"))
    print("wordcloud words: " + ("; ".join(words) if words else "already applied"))
    print("ai sentences: " + ("; ".join(sentences) if sentences else "already applied"))
    print("interpretation summary: " + ("; ".join(interp) if interp else "already applied"))
    print("remark tone rules: " + ("; ".join(tones) if tones else "already applied"))
    print("sheet header match: " + ("; ".join(sheet) if sheet else "already applied"))
    print("placeholder comments: " + ("; ".join(remarks) if remarks else "already applied"))
    print("gemini rate limit: " + ("; ".join(pacing) if pacing else "already applied"))
    print("sheet early response: " + ("; ".join(early) if early else "already applied"))
    print("sheet import jobs: " + ("; ".join(jobs) if jobs else "already applied"))
    print("correction name match: " + ("; ".join(corr_match) if corr_match else "already applied"))
    print("ocr cross-check: " + ("; ".join(crosscheck) if crosscheck else "already applied"))
    return 0


def main(argv):
    if len(argv) > 1 and argv[1] == "--live":
        return live_main(argv[2:])
    src = Path(argv[1]) if len(argv) > 1 else DEFAULT_INPUT
    dst = Path(argv[2]) if len(argv) > 2 else DEFAULT_OUTPUT
    layout = Path(argv[3]) if len(argv) > 3 else DEFAULT_LAYOUT
    wf = json.loads(src.read_text(encoding="utf-8"))
    out = copy.deepcopy(wf)
    orig_by_name = {n["name"]: n for n in wf["nodes"]}

    by_name = {n["name"]: n for n in out["nodes"]}
    for required in [WEBHOOK_NAME, PHONE_GEMINI_NODE, PHONE_NOT_FORM_NODE, "image to ocr and omr python",
                     SHARED_ENTRY_CLEAN, SHARED_ENTRY_FLAGGED, CODE_IN_JS, BUILD_FLAGGED, FLAGGED_RESPOND,
                     SHEET_MAP, FLAGGED_FCM, PHONE_SUBJECT_LOOKUP, PHONE_RECONCILE, PHONE_GEMINI_FAILURE,
                     RAW_ERROR_IF, SCAN_IMAGE_SAVE_IF, *CORRECTION_INSTRUCTOR_LOOKUPS, IMPORT_ERROR_FORMAT,
                     *CORRECTION_MAPS, UNIFIED_PARSE, INDIVIDUAL_PARSE, UNIFIED_AI, INDIVIDUAL_AI,
                     CORRECTION_HOOK, CORRECTION_INSTR, CORRECTION_SUBJ, CORRECTION_MARK, CORRECTION_RESPOND]:
        if required not in by_name:
            raise SystemExit(f"Input workflow has no node named {required!r}")

    # 1. drop the draft website nodes (+ their connections)
    missing_old = [n for n in OLD_WEBSITE_NODES if n not in by_name]
    out["nodes"] = [n for n in out["nodes"] if n["name"] not in OLD_WEBSITE_NODES]
    remove_node_connections(out["connections"], OLD_WEBSITE_NODES + [WEBHOOK_NAME])

    # 2. reconfigure the webhook: header auth, respond via Respond nodes
    hook = next(n for n in out["nodes"] if n["name"] == WEBHOOK_NAME)
    hook["parameters"] = {
        "httpMethod": "POST",
        "path": "sast-batch-scan",
        "authentication": "headerAuth",
        "responseMode": "responseNode",
        "options": {},
    }
    hook["credentials"] = {"httpHeaderAuth": {"id": WEBHOOK_CRED_ID, "name": WEBHOOK_CRED_NAME}}
    hook["position"] = [-53984, 11904]

    # 3. website front end
    new_nodes = build_website_nodes(orig_by_name)
    existing = {n["name"] for n in out["nodes"]}
    clash = [n["name"] for n in new_nodes if n["name"] in existing]
    if clash:
        raise SystemExit(f"Node name clash: {clash}")
    out["nodes"].extend(new_nodes)

    # 4. shared-node edits
    by_name = {n["name"]: n for n in out["nodes"]}
    patch_code_in_javascript(by_name[CODE_IN_JS])
    patch_build_flagged_record(by_name[BUILD_FLAGGED])
    patch_phone_reconcile(by_name[PHONE_RECONCILE])
    patch_phone_gemini_failure(by_name[PHONE_GEMINI_FAILURE])
    by_name[PHONE_GEMINI_NODE]["parameters"]["jsonBody"] = patch_gemini_prompt(
        by_name[PHONE_GEMINI_NODE]["parameters"]["jsonBody"], PHONE_GEMINI_NODE)
    for name in CORRECTION_MAPS:
        patch_correction_map(by_name[name])
    patch_import_error_format(by_name[IMPORT_ERROR_FORMAT])
    patch_unified_parse(by_name[UNIFIED_PARSE])
    patch_individual_parse(by_name[INDIVIDUAL_PARSE])
    patch_remark_ai_prompt(by_name[UNIFIED_AI], "Prefer concise English theme names.\n")
    patch_remark_ai_prompt(by_name[INDIVIDUAL_AI], "Remove filler words.\n")
    patch_flagged_respond(by_name[FLAGGED_RESPOND])
    patch_sheet_map_to_target(by_name[SHEET_MAP])
    for name in CORRECTION_INSTRUCTOR_LOOKUPS:
        patch_correction_instructor_lookup(by_name[name])
    patch_flagged_fcm_lookup(by_name[FLAGGED_FCM])
    patch_phone_subject_lookup(by_name[PHONE_SUBJECT_LOOKUP])
    guard_scan_image_save(by_name[SCAN_IMAGE_SAVE_IF])
    serialize_correction_image_lookups(out)
    by_name = {n["name"]: n for n in out["nodes"]}
    patch_correction_instructor_by_id(by_name[CORRECTION_INSTR])
    patch_correction_subject_by_id(by_name[CORRECTION_SUBJ])
    patch_correction_term(by_name[CORRECTION_MAP])
    respond_correction_before_shared_flow(out)
    apply_main_flow_aggregate_fix(out)
    fix_remark_loop_single_feed(out)
    add_phone_remark_check(out)
    add_phone_known_names(out)
    apply_phone_name_privacy(out)   # adds Phone Instructor Candidates; no Gemini call gets instructor names
    apply_check_only_rule(out)      # only check/slash marks count: prompt line (reconcilers come from website_src)
    apply_min_answers_rule(out)     # fewer than 5 of 20 answered: queue row stored discarded, no push

    # 5. connections (website front end + gate after the raw upsert)
    for s, o, t, i in WEBSITE_CONNECTIONS:
        add_connection(out["connections"], s, o, t, i)

    # 5b. migration 013: one recompute_sast path after the raw upsert; blank ratings stay NULL
    apply_sast_v2(out)

    # 5c. migration 015: the stamp rule (stamp_numbers, duplicate_stamp auto-reject, corrections 409)
    apply_stamp_rule(out)
    apply_correction_name_match(out)   # unresolved instructor/subject -> 422, queue row stays pending
    apply_ocr_crosscheck(out)          # AI student ID / remark vs OMR ink, white-ink rows, double marks

    # 5d. word cloud words (real word counts next to the AI themes)
    apply_wordcloud_words(out)

    # 5d2. AI positives/improvements as one sentence per line
    apply_ai_sentences(out)
    apply_interpretation_summary(out)   # student_interpretation: one paragraph about all students
    apply_remark_tone_rules(out)        # tone rules/examples in both remark prompts; tolerant tone parsing

    # 5e. Google Sheet import: columns matched by normalised header (old and new layout)
    apply_sheet_header_match(out)
    apply_remark_placeholders(out)
    apply_sheet_early_response(out)   # sheet webhook answers before the Gemini remark loop

    # 5e. Gemini free tier: paced remark loop, retries, no import-stopping failure
    apply_gemini_rate_limit(out)

    # 5f. sheet import job (migration 018): the app polls sheet_import_jobs until the flow really ends
    apply_sheet_import_jobs(out)

    # 6. secrets -> credentials
    swapped = swap_supabase_credentials(out["nodes"])

    # 7. canvas layout from the live export (positions only); new nodes next to their neighbours
    moved, placed = apply_live_layout(out, layout)
    place_new_nodes(out, placed)
    _sheet_job_order({n["name"]: n for n in out["nodes"]})   # the done marker below If19 (execution order)

    # Import inactive: activate after checking the credentials in the editor.
    out["active"] = False

    dst.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(out, ensure_ascii=False, indent=2) + "\n"
    dst.write_text(text, encoding="utf-8", newline="\n")

    print(f"input : {src}")
    print(f"output: {dst}")
    if missing_old:
        print(f"note  : draft nodes not found in input (already removed?): {missing_old}")
    print(f"nodes : {len(wf['nodes'])} -> {len(out['nodes'])}")
    print(f"shared edits: {CODE_IN_JS}, {BUILD_FLAGGED}, {FLAGGED_RESPOND}, {SHEET_MAP}, {PHONE_RECONCILE}, "
          f"{PHONE_GEMINI_FAILURE}; {RAW_LOOP}[done] -> {RAW_UPSERT_GATE} -> {SAST_PAIRS} -> "
          f"{SAST_RECOMPUTE} (recompute_sast) -> {SAST_ALERT_IF}")
    print(f"null ratings (rating.js): {CODE_IN_JS}, {FORMAT_FINAL}, {PHONE_MAP}, {WEBSITE_MAP}, "
          f"{CORRECTION_BUILD}, {', '.join(CORRECTION_MAPS)}; removed {len(SAST_DEAD_NODES)} aggregate nodes")
    print(f"sexual-word filter: re-check nodes {PHONE_REMARK_CHECK} / Website Remark Check "
          f"(verify timeouts {PHONE_VERIFY_TIMEOUT_MS // 1000} s / {WEB_VERIFY_TIMEOUT_MS // 1000} s); "
          f"{sum('function maskProfanity' in n['parameters'].get('jsCode', '') for n in out['nodes'])} nodes mask remarks")
    print(f"form check: {FORM_MATCH} (form_match.sql) -> Website Decide; corrections: {CORRECTION_INSTR} / "
          f"{CORRECTION_SUBJ} prefer body ids, {CORRECTION_MAP} website_review term, "
          f"{CORRECTION_RESPOND} -> {CORRECTION_AFTER} -> {SHARED_ENTRY_CLEAN}")
    print(f"supabase credential swapped on {len(swapped)} HTTP nodes: {', '.join(swapped)}")
    print(f"layout: {moved} node positions taken from {layout}" if layout.exists()
          else f"layout: {layout} not found; generated positions kept")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
