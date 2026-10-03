#!/usr/bin/env python3
"""Build n8n/perfect-101-website.json from the original n8n export.

Re-runnable:  python build_website_branch.py [INPUT_JSON] [OUTPUT_JSON] [LAYOUT_JSON]
Live export:  python build_website_branch.py --live LIVE_EXPORT_JSON [OUTPUT_JSON]
              applies only step 6 below (apply_sast_v2) to a live n8n export of this build
              (e.g. 'perfect 2.1.json' -> ../perfect-2.1.json); ids/positions/name/id kept.

Defaults:
  INPUT  = C:\\Users\\rodzh\\Downloads\\perfect 101 (4).json   (read only, never modified)
  OUTPUT = ../perfect-101-website.json (next to this tools/ folder)
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
     it (token fuzzy scoring in name_match.js); a form that matches nobody is not flagged. The
     phone Gemini prompt gets the known instructors/subjects ('Phone Known Names', known_names.sql)
     and returns known_instructor / known_subject; the website prompt gets the known SUBJECTS only
     ('Website Known Names', website_known_names.sql: no instructor names are sent to Gemini) and
     returns known_subject. OMR ratings come from Python, cross-checked by Gemini (row
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
DEFAULT_OUTPUT = HERE.parent / "perfect-101-website.json"
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
FLAGGED_FCM_PARAMS = "={{ [ $('build flagged record1').first().json.user_id ?? null ] }}"
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
}
# Known names for the main Gemini OCR prompt: a new Postgres node right before the Gemini call;
# Gemini returns the raw reading plus its closest known entry. Phone: instructors + subjects
# (known_names.sql). Website: subjects only (website_known_names.sql), no instructor names.
WEB_KNOWN_NAMES = "Website Known Names"
PHONE_KNOWN_NAMES = "Phone Known Names"
PHONE_OMR_NODE = "image to ocr and omr python"
SUBJECT_HINT_OLD = "2. subject: CTU course code (DDMGT, IT, CSO, HCI, ADBAS, IDA + digits)."
SUBJECT_HINT_NEW = "2. subject: the course code and/or subject title, as written."
KNOWN_ANCHOR = "6. student_id: digits only."
KNOWN_HEAD = (
    "\n7. known_instructor: the one instructor in the list below that the written name refers to, copied "
    "exactly from the list; empty if none clearly fits or no name is written.\n"
    "8. known_subject: the code of the one subject in the list below that the written subject refers to, "
    "copied exactly from the list; empty if none clearly fits or no subject is written.\n"
    "The lists only help to read messy handwriting: instructor and subject above stay exactly as written.\n"
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
KNOWN_SCHEMA_ADD ='"known_instructor": { "type": "STRING" },\n        "known_subject": { "type": "STRING" },\n        '
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
    """Prompt text with the known lists from the Known Names node (JSON-escaped for the body); empty
    when the lookup failed, so Gemini then just reads the form as before."""
    js = ("JSON.stringify((() => { try { const k = $('" + node_name + "').first().json || {}; "
          "if (!k.instructor_list && !k.subject_list) return ''; "
          "return " + json.dumps(KNOWN_HEAD) + " + 'Known instructors: ' + (k.instructor_list || 'none') "
          "+ '\\nKnown subjects (code = title): ' + (k.subject_list || 'none'); "
          "} catch (e) { return ''; } })()).slice(1, -1)")
    if "{{" in js or "}}" in js:
        raise SystemExit("known names expression must not contain '{{' or '}}'")
    return "{{ " + js + " }}"


def patch_gemini_known(body: str, node_name: str, where: str) -> str:
    """Main Gemini OCR prompt: neutral subject hint, the known lists, and two optional schema fields
    (known_instructor / known_subject); every existing field stays as it was."""
    body = replace_once(body, SUBJECT_HINT_OLD, SUBJECT_HINT_NEW, where)
    body = replace_once(body, KNOWN_ANCHOR, KNOWN_ANCHOR + known_names_expression(node_name), where)
    return replace_once(body, KNOWN_SCHEMA_ANCHOR, KNOWN_SCHEMA_ADD + KNOWN_SCHEMA_ANCHOR, where)


# --- Website AI Validation only: header crop, confidence, few-shot examples, and no instructor
# name list sent to Gemini (privacy). Local fuzzy matching against the batch/creditable
# instructors (form_match.sql, decide.js) is unaffected: only what Gemini itself is told changes.
KNOWN_HEAD_WEBSITE = (
    "\n7. known_subject: the code of the one subject in the list below that the written subject "
    "refers to, copied exactly from the list; empty if none clearly fits or no subject is written.\n"
    "The list only helps to read messy handwriting: subject above stays exactly as written.\n"
)
KNOWN_SCHEMA_ADD_WEBSITE = '"known_subject": { "type": "STRING" },\n        '
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


def known_names_expression_website(node_name: str) -> str:
    """Website only (privacy): the known SUBJECT list only -- no instructor names are sent to
    Gemini. Empty when the lookup failed or there are no subjects, same as the phone prompt."""
    js = ("JSON.stringify((() => { try { const k = $('" + node_name + "').first().json || {}; "
          "if (!k.subject_list) return ''; "
          "return " + json.dumps(KNOWN_HEAD_WEBSITE) + " + 'Known subjects (code = title): ' + (k.subject_list || 'none'); "
          "} catch (e) { return ''; } })()).slice(1, -1)")
    if "{{" in js or "}}" in js:
        raise SystemExit("known names expression (website) must not contain '{{' or '}}'")
    return "{{ " + js + " }}"


def patch_gemini_known_website(body: str, node_name: str, where: str) -> str:
    """Website only: neutral subject hint, the known SUBJECT list (no instructor names), one
    optional schema field (known_subject) plus the optional per-field confidence enums."""
    body = replace_once(body, SUBJECT_HINT_OLD, SUBJECT_HINT_NEW, where)
    body = replace_once(body, KNOWN_ANCHOR, KNOWN_ANCHOR + known_names_expression_website(node_name), where)
    return replace_once(body, KNOWN_SCHEMA_ANCHOR,
                        CONFIDENCE_SCHEMA_ADD + KNOWN_SCHEMA_ADD_WEBSITE + KNOWN_SCHEMA_ANCHOR, where)


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


def known_names_node(name, pos, params_js):
    """Phone: known instructors/subjects for the prompt. A failure goes on with an empty list."""
    return pg_node(name, pos, read("known_names.sql"), "={{ [ " + params_js + " ] }}", "continueRegularOutput",
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
        "={{ [ " + ", ".join([
            P + ".instructor_id",
            P + ".subject_id",
            P + ".term_id ?? null",
            "String($json.student_id ?? '').trim()",
            P + ".task_id",
        ]) + " ] }}",
        "continueErrorOutput"))
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
    D = "$('Website Decide').first(0).json"
    nodes.append(pg_node(
        "Website Raw Upsert", [-51296, 12000], read("website_raw_upsert.sql"),
        "={{ [ " + ", ".join([
            D + ".instructor_id",
            D + ".subject_id",
            D + ".term_id",
            "(String(" + D + ".student_id ?? '').trim() || null)",
            "(String(" + D + ".instructor_name ?? '').trim() || null)",
            "(String(" + D + ".remark ?? '').trim() || null)",
            D + ".submitted_date",
            "(" + D + ".staff_id || null)",
            D + ".task_id",
            "JSON.stringify(" + D + ".ratings ?? {})",
        ]) + " ] }}",
        "continueErrorOutput"))
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
    prompt lists the known instructors/subjects. The Gemini body reads the phone image with
    .first() (one scan per execution) instead of .item, so the extra node never breaks item pairing."""
    conns = out["connections"]
    by_name = {n["name"]: n for n in out["nodes"]}
    outs = conns[PHONE_OMR_NODE]["main"]
    if len(outs) != 1 or [c["node"] for c in outs[0]] != [PHONE_GEMINI_NODE]:
        raise SystemExit(f"{PHONE_OMR_NODE} does not feed only {PHONE_GEMINI_NODE}: {outs}; the input changed")
    outs[0] = []
    a, b = by_name[PHONE_OMR_NODE]["position"], by_name[PHONE_GEMINI_NODE]["position"]
    out["nodes"].append(known_names_node(PHONE_KNOWN_NAMES, [(a[0] + b[0]) // 2, max(a[1], b[1]) + 176], "null"))
    add_connection(conns, PHONE_OMR_NODE, 0, PHONE_KNOWN_NAMES, 0)
    add_connection(conns, PHONE_KNOWN_NAMES, 0, PHONE_GEMINI_NODE, 0)
    gem = by_name[PHONE_GEMINI_NODE]["parameters"]
    body = replace_once(gem["jsonBody"], PHONE_IMAGE_ITEM, PHONE_IMAGE_FIRST, PHONE_GEMINI_NODE)
    gem["jsonBody"] = patch_gemini_known(body, PHONE_KNOWN_NAMES, PHONE_GEMINI_NODE)


def flagged_respond_expression() -> str:
    """Body of the shared 'Respond to Webhook'. Website executions: the backend
    contract object. Phone executions: exactly the text the node always sent
    (its body was the fixed string '{{ JSON.stringify($json) }}', not an expression)."""
    phone_literal = "'{' + '{ JSON.stringify($json) }' + '}'"
    js = (
        "(() => { "
        "let w = null; "
        "try { const it = $('Website Flagged Record').first(); "
        "w = (it && it.json && it.json._source === 'website') ? it.json : null; } catch (e) { w = null; } "
        f"if (!w) {{ return {phone_literal}; }} "
        "const failed = !!($json && $json.error !== undefined); "
        "const reasons = Array.isArray(w.review_reasons) ? w.review_reasons.slice() : []; "
        "if (failed) { reasons.push('internal_error_not_saved'); } "
        "const note = String(w.review_note || '').trim(); "
        "const message = failed "
        "? 'NOT SAVED: inserting the page into the review queue failed in n8n. Send this page again.' "
        ": ('Flagged for manual review (' + (reasons.join(', ') || 'unspecified') + ').' + (note ? ' ' + note : '')).slice(0, 1000); "
        "return JSON.stringify({ status: 'flagged', flagged: true, task_id: String(w.task_id || ''), "
        "review_reasons: reasons, identity_check: (w.identity_check || null), "
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
      'send to ...' nodes (recompute_aggregates.sql) are removed there.
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
    want = read_js("website_map_to_target.js")
    code = code_of(WEBSITE_MAP)
    if code != want:
        code = replace_once(code, "  // blank row -> 0, stored as NULL later\n",
                            "  // blank row stays null (stored as NULL, never 0)\n", WEBSITE_MAP)
        code = replace_once(code, "d.ratings?.[`m${i}`] ?? 0;", "d.ratings?.[`m${i}`] ?? null;", WEBSITE_MAP)
        code = replace_once(code, "d.ratings?.[`p${i}`] ?? 0;", "d.ratings?.[`p${i}`] ?? null;", WEBSITE_MAP)
        if code != want:
            raise SystemExit(f"{WEBSITE_MAP}: code differs from website_src/website_map_to_target.js")
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
                                (PHONE_KNOWN_NAMES, PHONE_OMR_NODE, PHONE_GEMINI_NODE)):
        if name not in placed:
            a, b = by_name[before]["position"], by_name[after]["position"]
            by_name[name]["position"] = [(a[0] + b[0]) // 2, max(a[1], b[1]) + 176]
    if CORRECTION_AFTER not in placed:
        r = by_name[CORRECTION_RESPOND]["position"]
        by_name[CORRECTION_AFTER]["position"] = [r[0] + 224, r[1]]


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
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"input : {src} (name {wf.get('name')!r}, id {wf.get('id')}, versionId {wf.get('versionId')})")
    print(f"output: {dst}")
    print(f"nodes : {len(wf['nodes'])} -> {len(out['nodes'])} (removed {len(SAST_DEAD_NODES)}: "
          f"{', '.join(SAST_DEAD_NODES)})")
    print(f"flow  : {RAW_UPSERT_GATE} -> {SAST_PAIRS} -> {SAST_RECOMPUTE} (recompute_sast) -> {SAST_ALERT_IF}")
    print(f"null ratings: {CODE_IN_JS}, {FORMAT_FINAL}, {PHONE_MAP}, {WEBSITE_MAP}, {CORRECTION_BUILD}, "
          f"{', '.join(CORRECTION_MAPS)}")
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

    # 5. connections (website front end + gate after the raw upsert)
    for s, o, t, i in WEBSITE_CONNECTIONS:
        add_connection(out["connections"], s, o, t, i)

    # 5b. migration 013: one recompute_sast path after the raw upsert; blank ratings stay NULL
    apply_sast_v2(out)

    # 6. secrets -> credentials
    swapped = swap_supabase_credentials(out["nodes"])

    # 7. canvas layout from the live export (positions only); new nodes next to their neighbours
    moved, placed = apply_live_layout(out, layout)
    place_new_nodes(out, placed)

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
