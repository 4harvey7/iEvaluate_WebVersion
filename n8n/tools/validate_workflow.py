#!/usr/bin/env python3
"""Static checks for n8n/perfect-101-website.json.

Usage:  python validate_workflow.py [OUTPUT_JSON] [ORIGINAL_JSON]

Checks
  * valid JSON, unique node names/ids/webhook paths, connections reference
    existing nodes (and valid output indexes)
  * website front end = everything reachable from 'SAST from Website' up to the
    shared entries (If19 for clean pages, 'Save scan error image' for flagged)
  * clean pages: Website Raw Upsert -> Website Respond Saved -> Website Map to Target
    Format -> If19 (answered before entering the shared flow); flagged pages reach the
    phone flagged path
  * n8n "Unused Respond to Webhook node" rule: no Respond node reachable from a v2
    webhook whose responseMode is not responseNode (e.g. 'Google LINK from phone1')
  * removed website nodes are gone and nothing references them; every $('...')
    in the whole workflow points to an existing node
  * execution simulation: every website outcome fires exactly ONE respond node;
    no other webhook can reach any website Respond node
  * no "sb_secret" / "eyJ"; Supabase HTTP nodes use the credential; webhook
    header auth; lists the nodes that show credential "issues" until set up
  * nodes outside the website front end are unchanged except the documented
    shared edits; connections unchanged except the documented re-route
  * JS syntax (needs `node`) and SQL syntax (needs `pip install pglast`);
    no $n placeholders inside SQL comments
  * website: the form's instructor/subject are compared with the database by 'Website Form
    Match' (Identity Check outputs 0+1 -> Form Match; Form Match outputs 0+1 -> Decide) and a
    mismatch is flagged for review (instructor_/subject_mismatch_found|not_found|ambiguous,
    form_match_failed; the suggestion pre-filled in partial_data, never auto-credited);
    identity_check non-null only with a *_mismatch_* reason; the old removed identity reasons
    stay forbidden; Website Identity Check stays batch-only (5 parameters, no similarity);
    both reconcilers use the row-agreement rule (one AGREEMENT_MIN constant, same value) and
    take text only from Gemini; Gemini/OMR timeouts and retries fit inside the backend's
    N8N_TIMEOUT_SECONDS
  * corrections (sast-manual-correction): get instructor id7 / get subject id6 prefer the body
    ids (else the old name/code lookup), array parameters; Map target credits website_review
    approvals to body.term_id; Map target -> Mark Queue Validated1 -> Respond to Webhook5 ->
    After Correction Response -> If19 (answered before the shared flow, one respond per path)
  * sexual-word filter: one identical copy of website_src/profanity.js in every node that stores
    or analyses a remark; the phone and website remark re-check (Remark Check -> IF -> Verify
    Remark Words -> reconciler, no retry, fail safe) and its time budget; the remark-AI parsers
    drop profane/masked keywords; both main Gemini prompts transcribe without censoring
  * known names: 'Website Known Names' / 'Phone Known Names' (known_names.sql, continue on error)
    right before the main Gemini calls, whose prompts list the known instructors/subjects and
    ask for known_instructor / known_subject -- except the website: 'Website Known Names' runs
    website_known_names.sql (subjects only, no parameters) and its prompt asks only for
    known_subject, so no instructor name ever reaches Gemini; Decide (subject pick) and the phone
    reconciler use them through one identical copy of website_src/name_match.js; not_found never flags
Exit code 0 = all checks passed.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND_DB_PY = HERE.parent.parent / "backend" / "app" / "db.py"   # N8N_TIMEOUT_SECONDS default
DEFAULT_OUTPUT = HERE.parent / "perfect-101-website.json"
DEFAULT_INPUT = Path(r"C:\Users\rodzh\Downloads\perfect 101 (4).json")
WEBHOOK = "SAST from Website"
SHARED_ENTRIES = {"If19", "Save scan error image"}
WEB_RESPOND = "Website Respond"
WEB_RESPOND_SAVED = "Website Respond Saved"
RAW_UPSERT = "Insert or update rows in a table1"
RAW_SKIP_IF = "Raw Row Already Stored?"
FLAGGED_RESPOND = "Respond to Webhook"
MAIN_RECOMPUTE_NODES = ["send to managment table1", "send to performance node1"]
RAW_LOOP = "Loop Over Items3"
RAW_UPSERT_GATE = "Raw Upsert Done"
AGG_CHAIN_HEADS = ["Management Node1", "Performance1"]
EDITED_SHARED = {  # node -> parameter paths allowed to change
    "Code in JavaScript": [("jsCode",)],
    "build flagged record1": [("jsCode",)],
    "Respond to Webhook": [("responseBody",)],
    "send to managment table1": [("query",), ("options", "queryReplacement"), ("options", "queryBatching")],
    "send to performance node1": [("query",), ("options", "queryReplacement"), ("options", "queryBatching")],
    "Insert or update rows in a table1": [("options", "queryBatching"), ("@", "alwaysOutputData")],
    "get instructor id7": [("query",), ("options", "queryReplacement")],   # + body read by webhook name, id first
    "get subject id6": [("query",), ("options", "queryReplacement")],      # body.subject_id first, array params
    "get instructor id9": [("query",), ("options", "queryReplacement")],
    # corrections: image lookup runs first, always returns one row (only an image with data)
    "get existing scan image id": [("query",), ("options", "queryReplacement"),
                                   ("@", "alwaysOutputData"), ("@", "onError")],
    "get existing scan image id1": [("query",), ("options", "queryReplacement"),
                                    ("@", "alwaysOutputData"), ("@", "onError")],
    "is data from google link?": [("conditions",)],   # + only save an image when the row carries one
    "Map to Target Format": [("jsCode",)],   # Google Sheet rows: + import_type (If21 true branch)
    "Get staff FCM for flagged scan": [("options", "queryReplacement")],   # user_id from build flagged record1
    "get subject id4": [("options", "queryReplacement")],                  # explicit null-safe array
    # phone: text only from Gemini, row-agreement rule, omr_unavailable (website_src/phone_reconcile.js)
    "reconcile ocr and omr1": [("jsCode",)],
    "Handle Gemini failure": [("jsCode",)],                                # student id no longer from Python OCR
    # sexual-word filter (website_src/profanity.js) + prompts
    "ai validation ocr and omr": [("jsonBody",)],        # remarks: transcribe without censoring
    "Map target": [("jsCode",)],                         # + website_review approvals: term = body.term_id
    "Map target1": [("jsCode",)],
    "Format error for import_errors": [("jsCode",)],
    "Parse Unified AI Result1": [("jsCode",)],           # + drop profane/masked wordcloud keywords
    "Add Parse Code node1": [("jsCode",)],
    "Unified AI Analysis1": [("text",)],                 # + keyword rule
    "Individual AI Analysis1": [("text",)],
}
PHONE_REMARK_NODES = ["Phone Remark Check", "Phone Remark Needs Check?", "Phone Verify Remark Words"]
# nodes that must carry the sexual-word filter (identical copies of website_src/profanity.js)
FILTER_NODES = {"reconcile ocr and omr1", "Website Reconcile OCR OMR", "Phone Remark Check", "Website Remark Check",
                "Website Decide", "Code in JavaScript", "build flagged record1", "Map to Target Format", "Map target",
                "Map target1", "Format error for import_errors", "Parse Unified AI Result1", "Add Parse Code node1"}
FILTER_START = "// sexual-word filter, from website_src/profanity.js\n"
FILTER_END = "// end of sexual-word filter\n"
# (branch, not-a-form IF, remark check, IF, verify node, reconciler, main Gemini node)
REMARK_CHAINS = [("phone", "Is not a SAST form?", "Phone Remark Check", "Phone Remark Needs Check?",
                  "Phone Verify Remark Words", "reconcile ocr and omr1", "ai validation ocr and omr"),
                 ("website", "Website Is Not A Form?", "Website Remark Check", "Website Remark Needs Check?",
                  "Website Verify Remark Words", "Website Reconcile OCR OMR", "Website AI Validation")]
# (node before, known-names node, main Gemini node)
KNOWN_CHAINS = [("Website Python OMR", "Website Known Names", "Website AI Validation"),
                ("image to ocr and omr python", "Phone Known Names", "ai validation ocr and omr")]
SUBJECT_HINT_OLD = "2. subject: CTU course code (DDMGT, IT, CSO, HCI, ADBAS, IDA + digits)."
SUBJECT_HINT_NEW = "2. subject: the course code and/or subject title, as written."
NM_START = "// name matcher, from website_src/name_match.js\n"
NM_END = "// end of name matcher\n"
LC_START = "// layout check, from website_src/layout_check.js\n"
LC_END = "// end of layout check\n"
NOT_FOUND_RULE = "const flags = (m) => m.match === 'found' || (m.match === 'ambiguous' && !m.contenders.some((c) => c.is_batch));"
NO_CENSOR = "Never censor or mask a word, even a vulgar one: it is filtered later."
# removed identity reasons (earlier designs): must never come back
IDENTITY_REASONS = ["batch_instructor_mismatch", "instructor_not_verified", "batch_subject_mismatch",
                    "subject_not_verified", "identity_check_failed"]
# the form's instructor/subject vs the database (Website Form Match + Website Decide)
FORM_MATCH = "Website Form Match"
MISMATCH_REASONS = ["instructor_mismatch_found", "instructor_mismatch_not_found", "instructor_mismatch_ambiguous",
                    "subject_mismatch_found", "subject_mismatch_not_found", "subject_mismatch_ambiguous"]
FORM_MATCH_FAILED = "form_match_failed"
MATCH_VALUES = ["same", "found", "not_found", "ambiguous", "unreadable", "unchecked"]
DECIDE_CONSTANTS = {"NAME_MATCH_MIN": "0.70", "MATCH_MARGIN": "0.10", "MIN_NAME_LETTERS": "3", "SUBJECT_MATCH_MIN": "0.60"}
FORM_PD_KEYS = ["form_check_version", "instructor_match", "subject_match", "suggested_instructor_id",
                "suggested_instructor_name", "suggested_subject_id", "suggested_subject_code", "suggested_subject_name",
                "instructor_match_confidence", "subject_match_confidence", "instructor_candidates", "subject_candidates",
                "batch_instructor_name", "batch_subject_code", "batch_subject_name"]
# corrections (sast-manual-correction): id first, answered before the shared flow
CORRECTION_HOOK = "sast-manual-correction1"
CORRECTION_MAP = "Map target"
CORRECTION_MARK = "Mark Queue Validated1"
CORRECTION_RESPOND = "Respond to Webhook5"
CORRECTION_AFTER = "After Correction Response"
RECONCILERS = ["Website Reconcile OCR OMR", "reconcile ocr and omr1"]
OLD_OMR_RULES = ["omr_disagreement_", "severe_omr_disagreement_", "low_confidence_omr_", "FLAG_DIFF_COUNT",
                 "SEVERE_CONFLICT_LIMIT", "WEAK_CONFLICT_LIMIT"]
EXPECTED_CREDENTIAL_IDS = {"supabaseApi": "t8WKxORo7BHCziqr", "httpHeaderAuth": "cGPn1nyAiZqSUx40"}
# ("@", key) above = a top-level node field (not under parameters).
REMARK_LOOP_WAIT = "Wait1"
REMARK_LOOP_FEED = "insert data to student remarks2"
FLAGGED_FCM = "Get staff FCM for flagged scan"
PG_QUERY_BATCHING = {"single", "transaction", "independently"}   # n8n Postgres v2 option values
EXPECTED_REMOVED_EDGES = {("If1", "main", 0, "Management Node1", 0), ("If1", "main", 0, "Performance1", 0),
                          ("If14", "main", 1, RAW_LOOP, 0), (RAW_LOOP, "main", 1, RAW_UPSERT, 0),
                          # Loop Over Items8 is fed back once per group (alert branch no longer feeds Wait1)
                          ("If25", "main", 0, REMARK_LOOP_WAIT, 0),
                          ("PushNotificaton for sentiment analysisi5", "main", 0, REMARK_LOOP_WAIT, 0),
                          # corrections: the scan image lookup now runs before the instructor lookup
                          ("sast-manual-correction1", "main", 0, "get instructor id7", 0),
                          ("import-error-correction1", "main", 0, "get instructor id9", 0),
                          # phone: the remark re-check sits between the not-a-form check and the reconciler
                          ("Is not a SAST form?", "main", 1, "reconcile ocr and omr1", 0),
                          # sast-manual-correction: answered before the shared flow
                          ("Map target", "main", 0, "If19", 0),
                          # phone: the known-names lookup sits between the OMR call and Gemini
                          ("image to ocr and omr python", "main", 0, "ai validation ocr and omr", 0)}
EXPECTED_ADDED_EDGES = {(RAW_LOOP, "main", 0, RAW_UPSERT_GATE, 0),
                        (RAW_UPSERT, "main", 0, RAW_LOOP, 0),
                        (RAW_LOOP, "main", 1, RAW_SKIP_IF, 0),
                        (RAW_SKIP_IF, "main", 0, RAW_LOOP, 0),
                        (RAW_SKIP_IF, "main", 1, RAW_UPSERT, 0),
                        (RAW_UPSERT_GATE, "main", 0, "Management Node1", 0),
                        (RAW_UPSERT_GATE, "main", 0, "Performance1", 0),
                        ("get existing scan image id", "main", 0, "get instructor id7", 0),
                        ("get existing scan image id1", "main", 0, "get instructor id9", 0),
                        ("Is not a SAST form?", "main", 1, "Phone Remark Check", 0),
                        ("Phone Remark Check", "main", 0, "Phone Remark Needs Check?", 0),
                        ("Phone Remark Needs Check?", "main", 0, "Phone Verify Remark Words", 0),
                        ("Phone Remark Needs Check?", "main", 1, "reconcile ocr and omr1", 0),
                        ("Phone Verify Remark Words", "main", 0, "reconcile ocr and omr1", 0),
                        ("Respond to Webhook5", "main", 0, "After Correction Response", 0),
                        ("After Correction Response", "main", 0, "If19", 0),
                        ("image to ocr and omr python", "main", 0, "Phone Known Names", 0),
                        ("Phone Known Names", "main", 0, "ai validation ocr and omr", 0)}
# (webhook, scan image lookup, mapper that reads it) -- corrections that can end in import_errors
CORRECTION_IMAGE_CHAINS = [("sast-manual-correction1", "get existing scan image id", "Map target"),
                           ("import-error-correction1", "get existing scan image id1", "Map target1")]
SCAN_IMAGE_SAVE_IF = "is data from google link?"
REMOVED_NODES = [
    # earlier website design, replaced by the shared main flow
    "Website Insert Raw Row", "Website Raw Saved?", "Website Recompute Aggregates", "Website Overall Total",
    "Website Question Means", "Website Respond Success", "Website Low Score Gate",
    "Website AI Remark Input", "Website Has Remark?", "Website Save Scan Image", "Website Queue Flagged",
    "Website Flagged Response", "Website Save Gate",
    # draft branch in the original export
    "Prepare Website Scan", "Website If Flagged?", "Website Respond Flagged", "Ready for Main Flow (Website)",
]
REF_RE = re.compile(r"""\$\(\s*(['"])(.+?)\1\s*\)|\$node\[\s*(['"])(.+?)\3\s*\]""")

results: list[tuple[bool, str]] = []


def check(ok: bool, msg: str) -> bool:
    results.append((bool(ok), msg))
    return bool(ok)


def strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from strings(v)


def targets(conns, node, out_idx):
    lists = conns.get(node, {}).get("main", [])
    return [c["node"] for c in (lists[out_idx] if out_idx < len(lists) else []) or []]


def main(argv):
    out_path = Path(argv[1]) if len(argv) > 1 else DEFAULT_OUTPUT
    in_path = Path(argv[2]) if len(argv) > 2 else DEFAULT_INPUT
    text = out_path.read_text(encoding="utf-8")
    try:
        wf = json.loads(text)
        check(True, "output is valid JSON")
    except json.JSONDecodeError as e:
        check(False, f"output is valid JSON ({e})")
        return report()

    nodes = wf["nodes"]
    by_name = {n["name"]: n for n in nodes}
    conns = wf["connections"]
    check(len(by_name) == len(nodes), f"node names unique ({len(nodes)} nodes)")
    check(len({n['id'] for n in nodes}) == len(nodes), "node ids unique")
    paths = [n["parameters"].get("path") for n in nodes if n["type"] == "n8n-nodes-base.webhook"]
    check(len(paths) == len(set(paths)), f"webhook paths unique ({len(paths)} webhooks)")

    bad = []
    for src, outs in conns.items():
        if src not in by_name:
            bad.append(f"source {src!r} missing")
            continue
        for ctype, lists in outs.items():
            for idx, lst in enumerate(lists):
                for c in lst or []:
                    if c["node"] not in by_name:
                        bad.append(f"{src}[{idx}] -> {c['node']!r} missing")
                if ctype == "main" and idx == 1 and lst:
                    n = by_name[src]
                    if n["type"] not in ("n8n-nodes-base.if", "n8n-nodes-base.splitInBatches",
                                         "n8n-nodes-base.switch") and n.get("onError") != "continueErrorOutput":
                        bad.append(f"{src} uses output 1 but has no error output / second branch")
    check(not bad, "every connection references an existing node" + (f": {bad}" if bad else ""))

    # ---- website front end membership and hand-offs
    front = set()
    stack = [WEBHOOK]
    while stack:
        cur = stack.pop()
        if cur in front or cur in SHARED_ENTRIES:
            continue
        front.add(cur)
        for lists in conns.get(cur, {}).values():
            for lst in lists:
                for c in lst or []:
                    stack.append(c["node"])
    website = set(front)
    website_named = {n for n in by_name if n.startswith("Website ")} | {WEBHOOK}
    website_named.discard("Website Branch Note")
    check(website == website_named, f"website nodes = front end reachable from the webhook "
          f"({len(website)} nodes: {sorted(website - {WEBHOOK})})"
          + ("" if website == website_named else f"; diff {sorted(website ^ website_named)}"))

    def inbound(target):
        return sorted((s, i) for s, outs in conns.items() for lists in outs.values()
                      for i, lst in enumerate(lists) for c in (lst or []) if c["node"] == target)
    handoff_ok = (targets(conns, "Website Is Flagged?", 1) == ["Website Raw Upsert"]
                  and targets(conns, "Website Raw Upsert", 0) == ["Website Raw Stored?"]
                  and targets(conns, "Website Raw Stored?", 0) == ["Website Success Response"]
                  and targets(conns, "Website Success Response", 0) == [WEB_RESPOND_SAVED]
                  and targets(conns, WEB_RESPOND_SAVED, 0) == ["Website Map to Target Format"]
                  and inbound("Website Map to Target Format") == [(WEB_RESPOND_SAVED, 0)]
                  and targets(conns, "Website Map to Target Format", 0) == ["If19"]
                  and targets(conns, "Website Flagged Record", 0) == ["Save scan error image"])
    check(handoff_ok, "clean pages: Website Raw Upsert -> Raw Stored? -> Success Response -> Website Respond Saved "
                      "-> Website Map to Target Format -> If19; flagged: Website Flagged Record -> Save scan error image")
    check(targets(conns, "Map to Target Format3", 0) == ["If19"]
          and targets(conns, "If19", 0) == ["If1"], "website and phone share the same If19 -> If1 entry")

    # ---- removed nodes and references
    still = [r for r in REMOVED_NODES if r in by_name or r in text]
    check(not still, "removed website nodes are gone and not referenced anywhere" + (f": {still}" if still else ""))
    ref_missing = []
    for n in nodes:
        for s in strings(n.get("parameters", {})):
            for m in REF_RE.finditer(s):
                ref = m.group(2) or m.group(4)
                if ref not in by_name:
                    ref_missing.append(f"{n['name']} -> $('{ref}')")
    check(not ref_missing, "every $('...') reference in the workflow points to an existing node"
          + (f": {ref_missing}" if ref_missing else ""))
    outside = []
    for name in sorted(website):
        for s in strings(by_name[name].get("parameters", {})):
            for m in REF_RE.finditer(s):
                ref = m.group(2) or m.group(4)
                if ref not in website:
                    outside.append(f"{name} -> $('{ref}')")
    check(not outside, "website nodes only reference website nodes" + (f": {outside}" if outside else ""))

    # ---- execution simulation (respond nodes per outcome)
    responders = {n["name"] for n in nodes if n["type"] == "n8n-nodes-base.respondToWebhook"}
    rev = {}
    for s, outs in conns.items():
        for lists in outs.values():
            for lst in lists:
                for c in lst or []:
                    rev.setdefault(c["node"], set()).add(s)
    relevant = set(responders)
    stack = list(responders)
    while stack:
        cur = stack.pop()
        for p in rev.get(cur, ()):
            if p not in relevant:
                relevant.add(p)
                stack.append(p)

    def alternatives(name, visit, forced):
        n = by_name[name]
        lists = conns.get(name, {}).get("main", [])
        if name in forced:
            return [forced[name]]
        if n["type"] == "n8n-nodes-base.splitInBatches":
            return [[1]] if visit == 1 else [[0]]
        if n["type"] == "n8n-nodes-base.if" or n.get("onError") == "continueErrorOutput":
            return [[0], [1]]
        return [[0]] if lists else [[]]

    def simulate(start, forced, count_only=None):
        outcomes = []

        def explore(pending, visits, fired):
            if len(outcomes) > 20000:
                raise RuntimeError("too many scenarios")
            if not pending:
                outcomes.append(fired)
                return
            node, rest = pending[0], pending[1:]
            if node not in relevant:
                explore(rest, visits, fired)
                return
            v = visits.get(node, 0) + 1
            if v > 3:
                raise RuntimeError(f"loop through {node}")
            vis = {**visits, node: v}
            f2 = fired + ([node] if node in responders and (count_only is None or node in count_only) else [])
            for outs in alternatives(node, v, forced):
                children = [t for o in outs for t in targets(conns, node, o)]
                explore(children + rest, vis, f2)
        explore([start], {}, [])
        return outcomes

    web_outcomes = simulate(WEBHOOK, {"If19": [0], "If1": [0]})
    bad_outcomes = sorted({tuple(o) for o in web_outcomes if len(o) != 1})
    fired_by = sorted({o[0] for o in web_outcomes if len(o) == 1})
    check(not bad_outcomes, f"website: exactly one respond fires in each of {len(web_outcomes)} simulated "
          f"outcomes (responders used: {fired_by})" + (f"; bad: {bad_outcomes[:5]}" if bad_outcomes else ""))

    def reach(start, skip=()):
        seen, st = set(), [start]
        while st:
            cur = st.pop()
            if cur in seen or cur in skip:
                continue
            seen.add(cur)
            for lists in conns.get(cur, {}).values():
                for lst in lists:
                    for c in lst or []:
                        st.append(c["node"])
        return seen

    # n8n Webhook v2 checkResponseModeConfiguration: a webhook whose responseMode is not
    # 'responseNode' throws "Unused Respond to Webhook node found in the workflow" when any
    # Respond node is downstream -> every call of that webhook fails.
    hooks_all = [n for n in nodes if n["type"] == "n8n-nodes-base.webhook"]
    unused = []
    info_v1 = []
    for h in hooks_all:
        mode = h["parameters"].get("responseMode", "onReceived")
        if mode in ("responseNode", "streaming"):
            continue
        down = sorted(x for x in reach(h["name"]) if x in responders)
        if not down:
            continue
        if float(h.get("typeVersion", 1)) >= 2:
            unused.append(f"{h['name']} ({mode}) -> {down}")
        else:
            info_v1.append(f"{h['name']} (v{h.get('typeVersion')}, {mode}, unchanged from the original)")
    check(not unused, "no Respond node is reachable from a v2 webhook whose responseMode is not responseNode "
          "(n8n 'Unused Respond to Webhook node' check)" + (f": {unused}" if unused else "")
          + (f"; note, not checked by n8n for v1: {info_v1}" if info_v1 else ""))

    other_hooks = [n["name"] for n in nodes if n["type"] == "n8n-nodes-base.webhook" and n["name"] != WEBHOOK]
    web_responders = {WEB_RESPOND, WEB_RESPOND_SAVED}
    leaks = [f"{h} -> {sorted(reach(h) & web_responders)}" for h in other_hooks if reach(h) & web_responders]
    flag_leak = [h for h in other_hooks if h.startswith("Google LINK") and FLAGGED_RESPOND in reach(h)]
    check(not leaks and not flag_leak, f"phone/sheet/correction/health/bulk webhooks ({len(other_hooks)}) never reach "
          f"a website Respond node, and the Google Sheet webhook never reaches {FLAGGED_RESPOND}"
          + (f": {leaks} {flag_leak}" if leaks or flag_leak else ""))
    # the webhook is answered before the page enters the shared flow
    check("If19" not in reach(WEBHOOK, skip={WEB_RESPOND_SAVED}) and "If19" in reach(WEBHOOK),
          "website: If19 is reachable only through Website Respond Saved (answer after the raw row is stored, "
          "before the shared flow)")
    check(all(by_name[r]["parameters"].get("respondWith") == "firstIncomingItem" for r in web_responders)
          and "Website Flagged Record" in by_name[FLAGGED_RESPOND]["parameters"].get("responseBody", ""),
          "website answers are one JSON object (Website Respond / Respond Saved = first item; shared Respond to Webhook "
          "builds the contract object for website executions)")

    # clean website path and its alternatives
    base = {"Website Prepare Scan": [0], "Website Duplicate Check": [0], "Website Is Duplicate?": [1],
            "Website Attach Image": [0], "Website Is Not A Form?": [1], "Website Reconcile OCR OMR": [0],
            "Website Decide": [0]}
    clean = simulate(WEBHOOK, {**base, "Website Is Flagged?": [1], "Website Raw Upsert": [0],
                               "Website Raw Stored?": [0], "If19": [0], "If1": [0]})
    check(clean and all(o == [WEB_RESPOND_SAVED] for o in clean)
          and {"If19", "If1", RAW_UPSERT} <= reach("Website Map to Target Format"),
          "website clean path: Website Respond Saved answers, then If19 -> If1 -> shared raw upsert "
          f"({len(clean)} simulated outcomes)")
    conflict = simulate(WEBHOOK, {**base, "Website Is Flagged?": [1], "Website Raw Upsert": [0],
                                  "Website Raw Stored?": [1]})
    upsert_err = simulate(WEBHOOK, {**base, "Website Is Flagged?": [1], "Website Raw Upsert": [1]})
    flagged = simulate(WEBHOOK, {**base, "Website Is Flagged?": [0]})
    check(flagged and conflict and all(o == [FLAGGED_RESPOND] for o in flagged + conflict)
          and upsert_err and all(o == [WEB_RESPOND] for o in upsert_err),
          f"website flagged / raw-conflict (duplicate_student_id) pages answer only through the shared "
          f"{FLAGGED_RESPOND}; a failed raw upsert answers through {WEB_RESPOND} (not saved)")

    # placeholder instructors: valid batch instructors (the SQL + Decide guard)
    idsql = by_name["Website Identity Check"]["parameters"]["query"]
    dec = by_name["Website Decide"]["parameters"]["jsCode"]
    bi_part = idsql.split("bs AS (")[0]
    check("is_placeholder" in bi_part and "NOT IN ('true', 't', '1', 'yes')" in bi_part
          and "batch_instructor_is_placeholder !== true" in dec,
          "placeholder batch instructors are not 'disabled' (batch check SQL + Decide guard)")
    corr = [n for n in ("get instructor id7", "get instructor id9")
            if "is_placeholder" not in by_name[n]["parameters"]["query"]
            or "LEFT JOIN public.department_table" not in by_name[n]["parameters"]["query"]]
    check(not corr, "correction instructor lookups (get instructor id7 / id9) accept placeholder instructors"
          + (f": {corr}" if corr else ""))
    check("duplicate_student_exists" in idsql and "r.task_id IS DISTINCT FROM $5" in idsql
          and "reasons.push('duplicate_student_id')" in dec
          and "WHERE s.task_id = EXCLUDED.task_id" in by_name["Website Raw Upsert"]["parameters"]["query"],
          "same student + instructor + subject + term with another task_id -> duplicate_student_id "
          "(Identity Check/Decide), and Website Raw Upsert never overwrites another page's row")
    inv = by_name["Website Invalid Request Response"]["parameters"]["jsCode"]
    check("review_reasons: ['invalid_request', 'not_saved_permanent']" in inv and "'internal_error_not_saved'" not in inv,
          "invalid request -> review_reasons ['invalid_request', 'not_saved_permanent'] (not retryable)")

    # ---- secrets and credentials
    check("sb_secret" not in text, "no 'sb_secret' anywhere in the output")
    check("eyJ" not in text, "no 'eyJ' (JWT) anywhere in the output")
    sb_bad = []
    for n in nodes:
        if n["type"] != "n8n-nodes-base.httpRequest":
            continue
        p = n["parameters"]
        if "supabase.co" in str(p.get("url", "")):
            hdrs = [h.get("name", "").lower() for h in p.get("headerParameters", {}).get("parameters", [])]
            if ("apikey" in hdrs or "authorization" in hdrs or p.get("nodeCredentialType") != "supabaseApi"
                    or "supabaseApi" not in n.get("credentials", {})):
                sb_bad.append(n["name"])
    check(not sb_bad, "Supabase HTTP nodes use the supabaseApi credential" + (f": {sb_bad}" if sb_bad else ""))
    hook = by_name[WEBHOOK]
    hp = hook["parameters"]
    check(hp.get("path") == "sast-batch-scan" and hp.get("authentication") == "headerAuth"
          and hp.get("responseMode") == "responseNode"
          and hook.get("credentials", {}).get("httpHeaderAuth", {}).get("name") == "iEvaluate website webhook secret",
          "website webhook: POST sast-batch-scan, headerAuth credential, responseMode=responseNode")
    pending_creds = sorted(f"{n['name']} [{c['name']}]" for n in nodes
                           for c in n.get("credentials", {}).values() if c.get("id") is None)
    wrong_ids = sorted(f"{n['name']} [{t}={c.get('id')}]" for n in nodes for t, c in n.get("credentials", {}).items()
                       if t in EXPECTED_CREDENTIAL_IDS and c.get("id") != EXPECTED_CREDENTIAL_IDS[t])
    check(not pending_creds and not wrong_ids,
          "every credential is referenced by name AND the live credential id (Supabase / webhook secret ids from the "
          "live export), so a re-import into the same n8n keeps them linked"
          + (f": no id {pending_creds}" if pending_creds else "") + (f": wrong id {wrong_ids}" if wrong_ids else ""))

    # ---- website: the form's instructor/subject vs the database (flagged for review, never auto-credited)
    web_code = {n["name"]: n["parameters"].get("jsCode", "") for n in nodes
                if n["name"] in website and n["type"] == "n8n-nodes-base.code"}
    respond_body = by_name[FLAGGED_RESPOND]["parameters"].get("responseBody", "")
    # the removed identity reasons (earlier designs) never come back
    ident_bad = sorted(f"{name}: {r}" for name, c in web_code.items() for r in IDENTITY_REASONS if f"'{r}'" in c)
    ident_bad += [r for r in IDENTITY_REASONS if f"'{r}'" in respond_body]
    # answers that can never carry a *_mismatch_* reason keep identity_check null; no 'unverified' verdict
    null_responders = ["Website Invalid Request Response", "Website Duplicate Response", "Website Not Saved Response",
                       "Website Rejected Response", "Website Success Response"]
    not_null = [n for n in null_responders if "identity_check: null" not in web_code.get(n, "")]
    not_null += [f"{n} ('unverified')" for n, c in web_code.items() if "'unverified'" in c]
    params = sorted(set(re.findall(r"\$(\d+)", idsql)), key=int)
    qr_id = by_name["Website Identity Check"]["parameters"]["options"]["queryReplacement"]
    sql_ok = (params == ["1", "2", "3", "4", "5"] and "similarity" not in idsql
              and "ocr_instructor" not in qr_id and "subject_code_candidates" not in qr_id)
    check(not ident_bad and not not_null and sql_ok,
          "website: none of the removed identity reasons (" + ", ".join(IDENTITY_REASONS) + "); identity_check null "
          "in every answer that cannot carry a mismatch; Website Identity Check = batch integrity only (5 parameters, "
          "no similarity)" + (f": reasons {ident_bad}" if ident_bad else "")
          + (f": identity_check not null in {not_null}" if not_null else "")
          + ("" if sql_ok else f": identity SQL params {params}"))

    fm_bad = []
    fmn = by_name.get(FORM_MATCH)
    if not fmn or fmn["type"] != "n8n-nodes-base.postgres" or fmn.get("onError") != "continueErrorOutput":
        fm_bad.append(f"{FORM_MATCH}: must be a Postgres node with an error output")
    else:
        fsql = fmn["parameters"].get("query", "")
        fqr = fmn["parameters"].get("options", {}).get("queryReplacement", "")
        src_sql = (HERE / "website_src" / "form_match.sql").read_text(encoding="utf-8").replace("\r\n", "\n").rstrip() + "\n"
        if fsql != src_sql:
            fm_bad.append(f"{FORM_MATCH}: query differs from website_src/form_match.sql")
        if sorted(set(re.findall(r"\$(\d+)", fsql)), key=int) != ["1", "2", "3", "4", "5"]:
            fm_bad.append(f"{FORM_MATCH}: SQL must use exactly $1..$5")
        if not (fqr.startswith("={{ [") and fqr.endswith("] }}") and fqr.count("{{") == 1
                and ".instructor_id" in fqr and ".subject_id" in fqr and "json.ocr_instructor" in fqr
                and "json.ocr_subject" in fqr and "term_id" in fqr):
            fm_bad.append(f"{FORM_MATCH}: parameters must be one array [batch instructor, batch subject, OCR instructor, "
                          f"OCR subject, term] (n8n drops empty '{{{{ }}}}' values)")
        for needle in ("word_similarity(", "similarity(", "translate(", "regexp_matches(", "'DEPARTMENT_HEAD'",
                       "is_placeholder", "instructor_subjects", "form_match_version"):
            if needle not in fsql:
                fm_bad.append(f"{FORM_MATCH}: SQL lacks {needle}")
    wiring = (targets(conns, "Website Identity Check", 0) == [FORM_MATCH]
              and targets(conns, "Website Identity Check", 1) == [FORM_MATCH]
              and targets(conns, FORM_MATCH, 0) == ["Website Decide"]
              and targets(conns, FORM_MATCH, 1) == ["Website Decide"]
              and inbound("Website Decide") == [(FORM_MATCH, 0), (FORM_MATCH, 1)]
              and inbound(FORM_MATCH) == [("Website Identity Check", 0), ("Website Identity Check", 1)])
    if not wiring:
        fm_bad.append("wiring must be Identity Check [0,1] -> Form Match; Form Match [0,1] -> Decide")
    dec_bad = [r for r in MISMATCH_REASONS + [FORM_MATCH_FAILED] if f"'{r}'" not in dec]
    dec_bad += [f"match value {v}" for v in MATCH_VALUES if f"'{v}'" not in dec]
    for const, val in DECIDE_CONSTANTS.items():
        if len(re.findall(rf"^const {const}\s*=\s*{re.escape(val)};", dec, re.M)) != 1:
            dec_bad.append(f"const {const} = {val}")
    for needle in ("nodeItem('Website Identity Check')", "$input.first()", "const MISMATCH_RE = /^(instructor|subject)_mismatch_/;",
                   "const identityCheck = reasons.some((r) => MISMATCH_RE.test(r))", "identity_check: identityCheck,",
                   "if (targetDiffers) {"):
        if needle not in dec:
            dec_bad.append(f"Decide lacks {needle!r}")
    dec_bad += [f"partial_data.{k}" for k in FORM_PD_KEYS if f"      {k}: " not in dec]
    frec = web_code.get("Website Flagged Record", "")
    frec_bad = [k for k in FORM_PD_KEYS if f"    {k}: " not in frec]
    if "identity_check: d.identity_check ?? null" not in frec or "instructor_match: 'unchecked'" not in frec:
        frec_bad.append("identity_check pass-through / unchecked form keys")
    resp_ok = "identity_check: (w.identity_check || null)" in respond_body and "identity_check: null" not in respond_body
    check(not fm_bad and not dec_bad and not frec_bad and resp_ok,
          f"website form check: {FORM_MATCH} (form_match.sql, 5 array parameters, error output) between Identity Check "
          "(both outputs) and Decide (both outputs); Decide flags " + ", ".join(MISMATCH_REASONS) + f", {FORM_MATCH_FAILED} "
          "with the suggestion pre-filled, drops a batch duplicate_student_id when the suggestion differs, sets "
          "identity_check only with a *_mismatch_* reason; Flagged Record error paths 'unchecked' + batch suggestion; "
          "Respond to Webhook passes identity_check through"
          + (f": {fm_bad}" if fm_bad else "") + (f": Decide {dec_bad}" if dec_bad else "")
          + (f": Flagged Record {frec_bad}" if frec_bad else "") + ("" if resp_ok else ": Respond to Webhook identity_check"))

    # ---- OMR: Python ratings cross-checked by Gemini with ONE agreement threshold; text only from Gemini
    def no_comments(code):
        return "\n".join(re.sub(r"^\s*//.*$", "", ln) for ln in code.split("\n"))
    agree_bad = []
    mins = set()
    for name in RECONCILERS:
        code = by_name[name]["parameters"]["jsCode"]
        m = re.findall(r"^const AGREEMENT_MIN = ([0-9.]+);", code, re.M)
        if len(m) != 1:
            agree_bad.append(f"{name}: AGREEMENT_MIN defined {len(m)} times")
            continue
        mins.add(m[0])
        body = no_comments(code)
        agree_bad += [f"{name}: still uses {o}" for o in OLD_OMR_RULES + ["ocr_fields", "pythonOcr"] if o in body]
        if "omr_agreement_below_${AGREEMENT_MIN_PCT}" not in body or "agreement_percent" not in body:
            agree_bad.append(f"{name}: no omr_agreement_below_<pct> reason / agreement_percent output")
    if len(mins) > 1:
        agree_bad.append(f"AGREEMENT_MIN differs between the reconcilers: {sorted(mins)}")
    if "ocr_fields" in by_name["Handle Gemini failure"]["parameters"]["jsCode"]:
        agree_bad.append("Handle Gemini failure still reads Python ocr_fields")
    check(not agree_bad, f"both reconcilers: Python ratings cross-checked by Gemini with one AGREEMENT_MIN "
          f"({', '.join(sorted(mins))}), old count-based disagreement rules removed, text only from Gemini "
          "(no Python ocr_fields fallback, also in Handle Gemini failure)" + (f": {agree_bad}" if agree_bad else ""))

    # ---- timeouts: n8n's worst case for one website page must fit in the backend's wait
    omr = by_name["Website Python OMR"]
    gem = by_name["Website AI Validation"]
    omr_ms = omr["parameters"].get("options", {}).get("timeout") or 0
    gem_ms = gem["parameters"].get("options", {}).get("timeout") or 0
    tries = gem.get("maxTries", 1) if gem.get("retryOnFail") else 1
    wait_ms = gem.get("waitBetweenTries", 0) if gem.get("retryOnFail") else 0
    worst_s = (omr_ms + tries * gem_ms + (tries - 1) * wait_ms) / 1000 + 15   # + database work / margin
    be_default = None
    if BACKEND_DB_PY.exists():
        mm = re.search(r'_float_env\("N8N_TIMEOUT_SECONDS",\s*([0-9.]+)\)', BACKEND_DB_PY.read_text(encoding="utf-8"))
        be_default = float(mm.group(1)) if mm else None
    phone_gem = by_name["ai validation ocr and omr"]
    check(gem_ms >= 90_000 and tries >= 3 and wait_ms >= 5_000 and omr_ms >= 60_000
          and gem.get("onError") == "continueErrorOutput"
          and (be_default is None or be_default >= worst_s)
          and not phone_gem["parameters"].get("options", {}).get("timeout") and phone_gem.get("maxTries") == 2,
          f"website Gemini: timeout {gem_ms // 1000} s x {tries} tries, {wait_ms // 1000} s between (was 30 s x 2: "
          f"aborted as ECONNABORTED); OMR {omr_ms // 1000} s; worst case ~{worst_s:.0f} s <= backend "
          f"N8N_TIMEOUT_SECONDS default {be_default}; phone Gemini unchanged (Flutter waits 60 s)")

    # ---- sexual-word filter + remark re-check
    src = (HERE / "website_src" / "profanity.js").read_text(encoding="utf-8").replace("\r\n", "\n").rstrip() + "\n"
    body = "\n".join(ln for ln in src.split("\n") if ln.strip() and not ln.lstrip().startswith("//"))
    copies = {}
    for n in nodes:
        code = n["parameters"].get("jsCode", "")
        if "function maskProfanity" in code:
            i = code.find(FILTER_START)
            j = code.find(FILTER_END, i)
            copies[n["name"]] = code[code.index("\n", i) + 1:j].rstrip("\n") if i >= 0 and j > i else None
    filter_bad = [f"{k}: copy differs from website_src/profanity.js" for k, v in copies.items() if v != body]
    if set(copies) != FILTER_NODES:
        filter_bad.append(f"filter nodes {sorted(set(copies) ^ FILTER_NODES)}")
    code_of = lambda name: by_name[name]["parameters"].get("jsCode", "")   # noqa: E731
    uses = {
        "reconcile ocr and omr1": "pfResolveRemark(gText('remarks'), 'Phone Verify Remark Words')",
        "Website Reconcile OCR OMR": "'Website Verify Remark Words')",
        "Website Decide": "const remark = maskProfanity(remarkIn);",
        "Code in JavaScript": "Remarks_and_Suggestions: maskProfanity(",
        "build flagged record1": "const maskedRemarks = maskProfanity(record.partial_data.remarks);",
        "Map to Target Format": 'result["Remarks & Suggestions"] = maskProfanity(',
        "Map target": "Remarks_and_Suggestions: maskProfanity(",
        "Map target1": "'Remarks & Suggestions': maskProfanity(",
        "Format error for import_errors": "'Remarks & Suggestions': maskProfanity(",
        "Parse Unified AI Result1": "if (profaneKeyword(word)) continue;",
        "Add Parse Code node1": "keywords.filter((k) => !(k && profaneKeyword(",
    }
    filter_bad += [f"{k}: does not use the filter ({v})" for k, v in uses.items() if v not in code_of(k)]
    if code_of("Map target").count("maskProfanity(data.raw.Remarks_and_Suggestions") != 2:
        filter_bad.append("Map target: both remark fields must be masked")
    check(not filter_bad, f"sexual-word filter: one identical copy of website_src/profanity.js in {len(copies)} nodes "
          "(reconcilers, remark checks, Decide, Code in JavaScript, build flagged record1, Format error for "
          "import_errors, the three mappers, both remark-AI parsers), used on every stored remark"
          + (f": {filter_bad}" if filter_bad else ""))

    chain_bad = []
    for branch, notform, rcheck, rif, verify, reconciler, gemini in REMARK_CHAINS:
        vn, gn = by_name[verify], by_name[gemini]
        vp = vn["parameters"]
        cond = by_name[rif]["parameters"]["conditions"]["conditions"]
        if (targets(conns, notform, 1) != [rcheck] or targets(conns, rcheck, 0) != [rif]
                or targets(conns, rif, 0) != [verify] or targets(conns, rif, 1) != [reconciler]
                or targets(conns, verify, 0) != [reconciler] or len(conns.get(verify, {}).get("main", [])) != 1):
            chain_bad.append(f"{branch}: wiring {notform}[1] -> {rcheck} -> {rif} -> {verify} | {reconciler}")
        if len(cond) != 1 or cond[0]["leftValue"] != "={{ $json.remark_check_needed === true }}":
            chain_bad.append(f"{rif}: condition")
        if vn.get("onError") != "continueRegularOutput" or vn.get("retryOnFail"):
            chain_bad.append(f"{verify}: must continue on error with no retry (the reconciler masks on failure)")
        if not (0 < (vp.get("options", {}).get("timeout") or 0) <= 15_000):
            chain_bad.append(f"{verify}: timeout must be set and <= 15 s")
        if vp.get("url") != gn["parameters"].get("url") or vn.get("credentials") != gn.get("credentials"):
            chain_bad.append(f"{verify}: not the same Gemini model/credential as {gemini}")
        if "$json.verify_prompt" not in vp.get("jsonBody", "") or "$json.verify_generation_config" not in vp.get("jsonBody", ""):
            chain_bad.append(f"{verify}: body does not use the Remark Check prompt/config")
        if by_name[rcheck].get("onError") != "continueRegularOutput" or "pfRemarkCheckItem(remark)" not in code_of(rcheck):
            chain_bad.append(f"{rcheck}: must build the check item and continue on error")
        if NO_CENSOR not in gn["parameters"].get("jsonBody", ""):
            chain_bad.append(f"{gemini}: prompt does not ask for an uncensored transcription")
    wcheck = code_of("Website Remark Check")
    wv_ms = by_name["Website Verify Remark Words"]["parameters"]["options"]["timeout"]
    deadline = omr_ms + tries * gem_ms + (tries - 1) * wait_ms   # the OMR + Gemini worst case (see above)
    if (f"const VERIFY_TIMEOUT_MS = {wv_ms};" not in wcheck or f"const VERIFY_DEADLINE_MS = {deadline};" not in wcheck
            or "received_at_ms: Date.now()" not in code_of("Website Prepare Scan")
            or "item.verify_skipped = 'time_budget'" not in wcheck):
        chain_bad.append(f"Website Remark Check: time budget constants must be {wv_ms} / {deadline} ms "
                         "(verify timeout / OMR + Gemini worst case) with received_at_ms from Website Prepare Scan")
    check(not chain_bad, "remark re-check on scans: <not-a-form false> -> Remark Check -> IF (a word flagged) -> Verify "
          "Remark Words (one Gemini call, same image, <= 15 s, no retry, continue on error) -> reconciler, else straight "
          "to the reconciler; the website re-check only starts when it ends inside the OMR + Gemini worst case "
          f"({deadline // 1000} s), so the backend budget is unchanged; main Gemini prompts transcribe uncensored"
          + (f": {chain_bad}" if chain_bad else ""))

    # ---- known instructors/subjects in the main Gemini prompts (phone + website)
    known_bad = []
    def src_sql(fname):
        return (HERE / "website_src" / fname).read_text(encoding="utf-8").replace("\r\n", "\n").rstrip() + "\n"
    for before, known, gemini in KNOWN_CHAINS:
        kn = by_name.get(known)
        if not kn:
            known_bad.append(f"{known} missing")
            continue
        kp = kn["parameters"]
        is_web = gemini == "Website AI Validation"
        # website (privacy): subjects only, no parameters; phone: instructors + subjects, unchanged
        sql_file = "website_known_names.sql" if is_web else "known_names.sql"
        repl = kp.get("options", {}).get("queryReplacement")
        params_ok = repl is None if is_web else str(repl or "").startswith("={{ [")
        if (kn["type"] != "n8n-nodes-base.postgres" or kp.get("query") != src_sql(sql_file)
                or kn.get("onError") != "continueRegularOutput" or kn.get("alwaysOutputData") is not True
                or not params_ok):
            known_bad.append(f"{known}: Postgres node with {sql_file}, "
                             + ("no parameters" if is_web else "array parameters")
                             + ", continue on error, always output")
        if is_web and re.search(r"user_info|instructor", re.sub(r"--[^\n]*", "", kp.get("query", "")), re.I):
            known_bad.append(f"{known}: must not look up or return instructor names (privacy)")
        if targets(conns, before, 0) != [known] or targets(conns, known, 0) != [gemini] or inbound(gemini) != [(known, 0)]:
            known_bad.append(f"wiring {before} -> {known} -> {gemini}")
        gbody = by_name[gemini]["parameters"].get("jsonBody", "")
        # website (privacy): no instructor names sent to Gemini, so no known_instructor field;
        # phone: unchanged, both known_instructor and known_subject.
        needles = [f"$('{known}').first()", '"known_subject": { "type": "STRING" }', SUBJECT_HINT_NEW]
        if gemini == "Website AI Validation":
            needles += ['"instructor_confidence": { "type": "STRING", "enum": ["high", "medium", "low"] }',
                        "header_image_base64"]
            if any(w in gbody for w in ("known_instructor", "Known instructors", "instructor_list", "instructor_names")):
                known_bad.append(f"{gemini}: must not send an instructor name list to Gemini (privacy)")
        else:
            needles.append('"known_instructor": { "type": "STRING" }')
        for needle in needles:
            if needle not in gbody:
                known_bad.append(f"{gemini}: body lacks {needle!r}")
        if SUBJECT_HINT_OLD in gbody or ".item.json" in gbody:
            known_bad.append(f"{gemini}: old subject hint or .item reference left")
    nm_src = (HERE / "website_src" / "name_match.js").read_text(encoding="utf-8").replace("\r\n", "\n").rstrip() + "\n"
    nm_body = "\n".join(ln for ln in nm_src.split("\n") if ln.strip() and not ln.lstrip().startswith("//"))
    for name, needle in (("Website Decide", "String(rec.known_subject || '')"),
                         ("reconcile ocr and omr1", "nmKnownInstructor(gText('known_instructor')")):
        code = by_name[name]["parameters"].get("jsCode", "")
        i = code.find(NM_START)
        j = code.find(NM_END, i)
        if i < 0 or j < 0 or code[code.index("\n", i) + 1:j].rstrip("\n") != nm_body:
            known_bad.append(f"{name}: copy of website_src/name_match.js missing or different")
        if needle not in code:
            known_bad.append(f"{name}: does not use the known pick ({needle})")
    for name in ("Website Reconcile OCR OMR", "Website Decide"):
        if "known_instructor" in code_of(name):
            known_bad.append(f"{name}: the website has no known_instructor (no instructor list is sent to Gemini)")
    if "known_subject" not in code_of("Website Reconcile OCR OMR"):
        known_bad.append("Website Reconcile OCR OMR does not pass known_subject on")
    if NOT_FOUND_RULE not in dec:
        known_bad.append("Decide: only found / ambiguous without the batch may flag")
    check(not known_bad, "known names: Known Names lookup (continue on error) right before both main Gemini calls; "
          "phone prompt lists known instructors/subjects and asks for known_instructor / known_subject (optional); "
          "website lookup returns subjects only and the website prompt sends no instructor name (privacy) and only "
          "asks for known_subject, plus optional "
          "per-field confidence and the optional header_image third part; Decide + phone reconciler use the known "
          "picks via one copy of website_src/name_match.js; not_found never flags"
          + (f": {known_bad}" if known_bad else ""))

    # ---- layout safety net: one identical copy of website_src/layout_check.js in both reconcilers,
    # used before the rows are compared; both main Gemini prompts ask for the OPTIONAL column_order
    lay_bad = []
    lc_src = (HERE / "website_src" / "layout_check.js").read_text(encoding="utf-8").replace("\r\n", "\n").rstrip() + "\n"
    lc_body = "\n".join(ln for ln in lc_src.split("\n") if ln.strip() and not ln.lstrip().startswith("//"))
    for name in ("Website Reconcile OCR OMR", "reconcile ocr and omr1"):
        code = code_of(name)
        i = code.find(LC_START)
        j = code.find(LC_END, i)
        if i < 0 or j < 0 or code[code.index("\n", i) + 1:j].rstrip("\n") != lc_body:
            lay_bad.append(f"{name}: copy of website_src/layout_check.js missing or different")
        for needle in ("const layoutSignals = lcLayoutSignals(py, gemini, geminiOk);",
                       "const pythonConfident = !layoutMismatch && ",
                       "if (layoutMismatch) reasons.push('layout_mismatch');"):
            if needle not in code:
                lay_bad.append(f"{name}: lacks {needle!r}")
        if code.find("const layoutMismatch") > code.find("const pythonConfident"):
            lay_bad.append(f"{name}: layout check must run before the rows are compared")
    for name in ("ai validation ocr and omr", "Website AI Validation"):
        gbody = by_name[name]["parameters"].get("jsonBody", "")
        if '"column_order": { "type": "ARRAY", "items": { "type": "STRING" } }' not in gbody or "- column_order: " not in gbody:
            lay_bad.append(f"{name}: does not ask for column_order")
        if "column_order" in gbody[gbody.rfind('"required"'):]:
            lay_bad.append(f"{name}: column_order must stay optional")
    check(not lay_bad, "layout safety net: both reconcilers run one identical copy of website_src/layout_check.js "
          "before comparing rows (layout_mismatch, no python_confident win); both Gemini prompts ask for the "
          "optional column_order" + (f": {lay_bad}" if lay_bad else ""))

    # ---- unchanged outside the website front end (vs original)
    if in_path.exists():
        orig = json.loads(in_path.read_text(encoding="utf-8"))
        onodes = {n["name"]: n for n in orig["nodes"]}
        changed = []
        for name, on in onodes.items():
            if name in website or name not in by_name:
                continue
            nn = json.loads(json.dumps(by_name[name]))
            oo = json.loads(json.dumps(on))
            if nn == oo:
                continue
            for pth in EDITED_SHARED.get(name, []):
                if pth[0] == "@":
                    nn.pop(pth[1], None)
                    oo.pop(pth[1], None)
                    continue
                for d in (nn["parameters"], oo["parameters"]):
                    tgt = d
                    for k in pth[:-1]:
                        tgt = tgt.get(k, {})
                    tgt.pop(pth[-1], None)
            for d in (nn, oo):  # credential swap; canvas positions come from the live export (layout only)
                d.pop("credentials", None)
                d.pop("position", None)
                for k in ("headerParameters", "authentication", "nodeCredentialType", "sendHeaders"):
                    d["parameters"].pop(k, None)
            if nn != oo:
                changed.append(name)
        check(not changed, f"nodes outside the website front end unchanged (canvas positions aside) except the "
                           f"credential swap and the shared edits {sorted(EDITED_SHARED)}" + (f": {changed}" if changed else ""))
        removed = sorted(set(onodes) - set(by_name))
        check(all(r in REMOVED_NODES or r.startswith("Website ") for r in removed),
              f"only draft website nodes removed from the original: {removed}")

        def edge_set(c, exclude):
            es = set()
            for s, outs in c.items():
                if s in exclude:
                    continue
                for ct, lists in outs.items():
                    for i, lst in enumerate(lists):
                        for e in lst or []:
                            if e["node"] not in exclude:
                                es.add((s, ct, i, e["node"], e["index"]))
            return es
        old_web = {n for n in onodes if n.startswith("Website ") or n in
                   ("SAST from Website", "Prepare Website Scan", "Ready for Main Flow (Website)")}
        e_old = edge_set(orig["connections"], old_web)
        e_new = edge_set(conns, website)
        added, removed_e = e_new - e_old, e_old - e_new
        ok = added == EXPECTED_ADDED_EDGES and removed_e == EXPECTED_REMOVED_EDGES
        check(ok, "shared-flow connections unchanged except the aggregate re-route "
                  "(If1 -x-> Management Node1/Performance1; Loop Over Items3[done] -> Raw Upsert Done -> both)"
              + ("" if ok else f"; +{sorted(added)} -{sorted(removed_e)}"))

        cij = by_name["Code in JavaScript"]["parameters"]["jsCode"]
        bfr = by_name["build flagged record1"]["parameters"]["jsCode"]
        check("$('SAST from Website')" in cij and "task_id: String(r['task_id'])" in cij
              and "r['Student ID'].trim()" in cij and "if (rec.batch_id) record.batch_id" in bfr
              and "$('Website Flagged Record')" in bfr,
              "shared edits present: Code in JavaScript (website staff id, task_id, blank student -> NULL); "
              "build flagged record1 (website record, batch_id, partial_data)")
    else:
        check(True, f"(original not found at {in_path}; skipped unchanged-flow comparison)")

    # ---- If21: Google Sheet rows take the grouped remark AI path, scans the per-remark path.
    # 'clean data1' sets import_type, but 'Map to Target Format' builds new objects, so it has to
    # set it itself or every sheet row falls to If21's false branch (If22, one AI call per remark).
    sheet_map = by_name.get("Map to Target Format", {}).get("parameters", {}).get("jsCode", "")
    if21 = by_name.get("If21", {}).get("parameters", {}).get("conditions", {}).get("conditions", [])
    other_maps = ["Map to Target Format3", "Website Map to Target Format", "Map target", "Map target1"]
    leak = [m for m in other_maps if "import_type" in json.dumps(by_name.get(m, {}).get("parameters", {}))]
    check(re.search(r"""result\[["']import_type["']\]\s*=\s*webhookData\.type\s*\|\|\s*["']\w+["']""", sheet_map)
          and "newItems.push({ json: result })" in sheet_map
          and len(if21) == 1 and if21[0].get("leftValue") == "={{ $json.import_type }}"
          and if21[0].get("operator", {}).get("operation") == "notEmpty"
          and targets(conns, "If21", 0) == ["Instructor message2"] and targets(conns, "If21", 1) == ["If22"]
          and targets(conns, "Map to Target Format", 0) == ["If19"]
          and not leak,
          "Map to Target Format sets import_type on every sheet row, so sheet rows hit If21's true branch "
          "(Instructor message2, grouped remark AI); phone/website/correction mappers never set it (If22)"
          + (f": import_type in {leak}" if leak else ""))

    # ---- aggregate fix: same SQL + ordering guaranteed by structure
    rec_sql = (HERE / "website_src" / "recompute_aggregates.sql").read_text(encoding="utf-8").replace("\r\n", "\n").rstrip() + "\n"
    check(all(by_name[n]["parameters"]["query"] == rec_sql for n in MAIN_RECOMPUTE_NODES)
          and all(by_name[n]["parameters"]["options"]["queryReplacement"]
                  == "={{ [ $json.instructor_id, $json.term_id ?? null, $json.subject_id ] }}"
                  for n in MAIN_RECOMPUTE_NODES),
          "send to managment table1 / send to performance node1 run recompute_aggregates.sql with "
          "[instructor_id, term_id, subject_id]")
    order_ok = (all(inbound(h) == [(RAW_UPSERT_GATE, 0)] for h in AGG_CHAIN_HEADS)
                and inbound(RAW_UPSERT_GATE) == [(RAW_LOOP, 0)]
                and targets(conns, RAW_LOOP, 1) == [RAW_SKIP_IF]
                and targets(conns, RAW_SKIP_IF, 1) == [RAW_UPSERT]
                and inbound("If14") == [(RAW_UPSERT, 0)])
    cij_code = by_name["Code in JavaScript"]["parameters"]["jsCode"]
    loop_ok = (inbound(RAW_LOOP) == sorted([("Code in JavaScript", 0), (RAW_UPSERT, 0), (RAW_SKIP_IF, 0)])
               and targets(conns, RAW_SKIP_IF, 0) == [RAW_LOOP]
               and "$('Website Raw Upsert')" in cij_code and "_raw_already_stored: true" in cij_code
               and RAW_LOOP in targets(conns, RAW_UPSERT, 0) and targets(conns, "If14", 1) == []
               and by_name[RAW_UPSERT]["parameters"].get("options", {}).get("queryBatching") == "independently")
    check(loop_ok, "raw upsert loop always reaches 'done' (every upsert result, row or error, feeds Loop Over "
          "Items3 once per iteration; If14 keeps only its error-flag branch; rows upserted independently), so "
          "Raw Upsert Done fires exactly once even after a failed row; website rows (already stored, all-or-none "
          "per execution) skip the shared upsert via Raw Row Already Stored?")
    # ---- the raw-upsert loop always feeds back (bug: 'independent' was not a valid option value, the
    # upsert ran no query, returned 0 items, and splitInBatches -- which only runs again when an input
    # arrives -- never reached 'done'). Every branch of the loop body must lead back into
    # Loop Over Items3, and every node on the way must emit at least one item per run.
    body, st = set(), [c for c in targets(conns, RAW_LOOP, 1)]
    while st:
        cur = st.pop()
        if cur in body or cur == RAW_LOOP:
            continue
        body.add(cur)
        st.extend(c["node"] for lst in conns.get(cur, {}).get("main", []) for c in (lst or []))
    feeders = {s for s, _ in inbound(RAW_LOOP)} - {"Code in JavaScript"}
    feedback_bad = []
    for name in sorted(body):
        n = by_name[name]
        back = name in feeders
        if n["type"] == "n8n-nodes-base.if":
            outs = conns.get(name, {}).get("main", [])
            for i in (0, 1):
                tg = [c["node"] for c in (outs[i] if i < len(outs) else []) or []]
                if name == RAW_SKIP_IF and not tg:
                    feedback_bad.append(f"{name}[{i}] leads nowhere")
        elif back and n["type"] == "n8n-nodes-base.postgres":
            if n.get("alwaysOutputData") is not True:
                feedback_bad.append(f"{name}: alwaysOutputData is not true (0 items would stop the loop)")
    if not (feeders == {RAW_UPSERT, RAW_SKIP_IF} and targets(conns, RAW_SKIP_IF, 0) == [RAW_LOOP]
            and targets(conns, RAW_SKIP_IF, 1) == [RAW_UPSERT] and RAW_LOOP in targets(conns, RAW_UPSERT, 0)):
        feedback_bad.append(f"loop feeders {sorted(feeders)}")
    check(not feedback_bad, "raw-upsert loop always feeds back: Raw Row Already Stored? true -> Loop Over Items3, "
          "false -> upsert -> Loop Over Items3; the upsert has alwaysOutputData, so even a 0-item run "
          "re-feeds the loop and 'done' (Raw Upsert Done) always fires"
          + (f": {feedback_bad}" if feedback_bad else ""))
    bad_batching = sorted(n["name"] for n in nodes if n["type"] == "n8n-nodes-base.postgres"
                          and "queryBatching" in n["parameters"].get("options", {})
                          and n["parameters"]["options"]["queryBatching"] not in PG_QUERY_BATCHING)
    check(not bad_batching, "every Postgres queryBatching is a real n8n value (single/transaction/independently; "
          "any other value runs NO query and returns 0 items)" + (f": {bad_batching}" if bad_batching else ""))

    # ---- grouped remark AI loop: exactly one feed back per group
    wait_in = inbound(REMARK_LOOP_WAIT)
    check(wait_in == [(REMARK_LOOP_FEED, 0)] and targets(conns, REMARK_LOOP_WAIT, 0) == ["Loop Over Items8"]
          and targets(conns, "Branch 3 — Student Remarks2", 0) == [REMARK_LOOP_FEED, "If24"],
          "Loop Over Items8 is fed back once per group (Wait1 <- only insert data to student remarks2; "
          "the critical-remark alert branch no longer advances the loop)" + ("" if wait_in == [(REMARK_LOOP_FEED, 0)]
                                                                          else f": Wait1 <- {wait_in}"))

    # ---- flagged-scan staff push: the queue POST (Prefer: return=minimal) returns no body
    fcm_qr = by_name[FLAGGED_FCM]["parameters"].get("options", {}).get("queryReplacement", "")
    check("$json.user_id" not in fcm_qr and "$('build flagged record1')" in fcm_qr
          and fcm_qr.startswith("={{ [") and "?? null" in fcm_qr,
          "Get staff FCM for flagged scan does not read $json.user_id (its input has no body); it reads "
          "build flagged record1 as an explicit, null-safe array")
    minimal_bad = []
    for n in nodes:
        qr = n.get("parameters", {}).get("options", {}).get("queryReplacement")
        if n["type"] != "n8n-nodes-base.postgres" or not isinstance(qr, str) or "$json" not in qr:
            continue
        for src, _ in inbound(n["name"]):
            sp = by_name[src]["parameters"]
            prefer = [h.get("value", "") for h in sp.get("headerParameters", {}).get("parameters", [])
                      if str(h.get("name", "")).lower() == "prefer"]
            if by_name[src]["type"] == "n8n-nodes-base.httpRequest" and any("return=minimal" in v for v in prefer):
                minimal_bad.append(f"{n['name']} <- {src}")
    check(not minimal_bad, "no Postgres node reads $json from an HTTP node that returns no body (Prefer: return=minimal)"
          + (f": {minimal_bad}" if minimal_bad else ""))

    lock_ok = all(by_name[n]["parameters"]["options"].get("queryBatching") == "transaction"
                  and "pg_advisory_xact_lock(hashtext('ievaluate:recompute_aggregates'))" in by_name[n]["parameters"]["query"]
                  for n in MAIN_RECOMPUTE_NODES)
    check(lock_ok, "recompute nodes take a transaction-level advisory lock first (queryBatching = transaction)")
    # Everything reachable from If1 WITHOUT passing through the raw upsert or the loop's
    # "done" output could run before the raw row is stored. Nodes downstream of the
    # upsert (e.g. the If14 error branch -> Website Save Gate) run after it by definition.
    before, st = set(), ["If1"]
    while st:
        cur = st.pop()
        if cur in before:
            continue
        before.add(cur)
        if cur == RAW_UPSERT:
            continue
        for lists in conns.get(cur, {}).values():
            for i, lst in enumerate(lists):
                if cur == RAW_LOOP and i == 0:  # "done"
                    continue
                for c in lst or []:
                    st.append(c["node"])
    early = [n for n in MAIN_RECOMPUTE_NODES + AGG_CHAIN_HEADS if n in before]
    check(order_ok and not early, "recompute runs only after the raw upsert "
          "(reachable from If1 only through Loop Over Items3[done])"
          + (f": reachable early: {early}" if early else ""))

    # ---- corrections: the scan image lookup must have run before the mapper reads it.
    # executionOrder v1 runs one branch to the end before the next (by canvas position), so a
    # lookup on a PARALLEL branch of the webhook ran after Map target -> ... -> Insert
    # import_errors1: existing_scan_image_id was null and the import error pointed at a new,
    # EMPTY scan_error_images row (image not shown in the app).
    def ancestors(target):
        seen, st = set(), [target]
        while st:
            cur = st.pop()
            for src, _ in inbound(cur):
                if src not in seen:
                    seen.add(src)
                    st.append(src)
        return seen

    def reach_without(start, blocked):
        seen, st = set(), [start]
        while st:
            cur = st.pop()
            if cur in seen or cur == blocked:
                continue
            seen.add(cur)
            for lists in conns.get(cur, {}).values():
                for lst in lists:
                    for c in lst or []:
                        st.append(c["node"])
        return seen

    chain_bad = []
    for hook, lookup, mapper in CORRECTION_IMAGE_CHAINS:
        ln = by_name.get(lookup, {})
        mcode = by_name.get(mapper, {}).get("parameters", {}).get("jsCode", "")
        if f"$('{lookup}')" not in mcode:
            chain_bad.append(f"{mapper} does not read {lookup}")
        if lookup not in ancestors(mapper):
            chain_bad.append(f"{lookup} is not upstream of {mapper}")
        if mapper in reach_without(hook, lookup):
            chain_bad.append(f"{mapper} reachable from {hook} without passing {lookup}")
        if ln.get("alwaysOutputData") is not True or ln.get("onError") != "continueRegularOutput":
            chain_bad.append(f"{lookup}: needs alwaysOutputData + onError continueRegularOutput (0 rows / error "
                             f"would stop the correction)")
        q = ln.get("parameters", {}).get("query", "")
        if "SELECT NULL::uuid WHERE NOT EXISTS" not in q or "COALESCE(s.image_base64, '') <> ''" not in q:
            chain_bad.append(f"{lookup}: must return exactly one row and only an image row with data")
        for n in nodes:   # every node on the chain reads the webhook body by name, not via $json
            if n["name"] in reach_without(hook, None) and n["name"] in ancestors(mapper)                     and n["type"] == "n8n-nodes-base.postgres" and n["name"] != lookup                     and "$json.body" in n["parameters"].get("options", {}).get("queryReplacement", ""):
                chain_bad.append(f"{n['name']} reads $json.body but its input is no longer the webhook")
    check(not chain_bad, "corrections: the scan image lookup runs before Map target / Map target1 "
          "(serial, always one row, only an image with data), so a new import error keeps the "
          "original scan image" + (f": {chain_bad}" if chain_bad else ""))
    corr_bad = []
    q7 = by_name["get instructor id7"]["parameters"]["query"]
    qr7 = by_name["get instructor id7"]["parameters"]["options"].get("queryReplacement", "")
    for needle in ("by_id AS (", "ui.id::text = i.want_id", "AND NOT EXISTS (SELECT 1 FROM by_id)",
                   "hit AS (", "WHERE NOT EXISTS (SELECT 1 FROM hit);", "is_placeholder"):
        if needle not in q7:
            corr_bad.append(f"get instructor id7 lacks {needle!r}")
    if not (qr7.startswith("={{ [") and f"$('{CORRECTION_HOOK}').first().json.body.instructor " in qr7
            and ".body.instructor_id" in qr7 and "$json" not in qr7):
        corr_bad.append("get instructor id7: parameters must be [body.instructor, body.instructor_id] read by webhook name")
    q6 = by_name["get subject id6"]["parameters"]["query"]
    qr6 = by_name["get subject id6"]["parameters"]["options"].get("queryReplacement", "")
    for needle in ("by_id AS (", "s.id::text = a.want_id", "m AS (SELECT * FROM by_id UNION ALL SELECT * FROM by_code",
                   "WHERE NOT EXISTS (SELECT 1 FROM by_id)"):
        if needle not in q6:
            corr_bad.append(f"get subject id6 lacks {needle!r}")
    if not (qr6.startswith("={{ [") and "$json.instructor_id ?? null" in qr6 and ".body.subject_id" in qr6
            and ".body.subject " in qr6):
        corr_bad.append("get subject id6: parameters must be [instructor_id, body.subject, body.subject_id] as an array")
    mt = by_name[CORRECTION_MAP]["parameters"]["jsCode"]
    if ("cb.correction_source === 'website_review'" not in mt or "current_term_id:        correctedTermId," not in mt
            or "term_id:            correctedTermId" not in mt or mt.count("data.current_term_id") != 1):
        corr_bad.append("Map target: website_review approvals must use body.term_id (else the current term)")
    after = by_name.get(CORRECTION_AFTER, {})
    if after.get("type") != "n8n-nodes-base.code" or "return $('Map target').all()" not in after.get("parameters", {}).get("jsCode", ""):
        corr_bad.append(f"{CORRECTION_AFTER}: must re-emit $('Map target').all()")
    if not (targets(conns, CORRECTION_MAP, 0) == [CORRECTION_MARK] and targets(conns, CORRECTION_MARK, 0) == [CORRECTION_RESPOND]
            and targets(conns, CORRECTION_RESPOND, 0) == [CORRECTION_AFTER] and targets(conns, CORRECTION_AFTER, 0) == ["If19"]
            and inbound(CORRECTION_AFTER) == [(CORRECTION_RESPOND, 0)]):
        corr_bad.append("wiring must be Map target -> Mark Queue Validated1 -> Respond to Webhook5 -> After Correction Response -> If19")
    if "If19" in reach_without(CORRECTION_HOOK, CORRECTION_RESPOND):
        corr_bad.append("If19 reachable from sast-manual-correction1 without passing Respond to Webhook5")
    if responders & reach("If19"):
        corr_bad.append(f"a Respond node is downstream of If19: {sorted(responders & reach('If19'))}")
    # 'Merge' (combine by position) fires once: the simulation follows only its 'build the record1' input
    corr_out = simulate(CORRECTION_HOOK, {"GET TERM BY ID6": [], "If19": [0], "If1": [0]})
    if not corr_out or any(o != [CORRECTION_RESPOND] for o in corr_out):
        corr_bad.append(f"sast-manual-correction responds {sorted({tuple(o) for o in corr_out})}")
    check(not corr_bad, "corrections (sast-manual-correction): get instructor id7 / get subject id6 use body.instructor_id / "
          "body.subject_id when valid (else the old name/code lookup; array parameters); Map target credits website_review "
          "approvals to body.term_id; answered by Respond to Webhook5 before the shared flow (-> After Correction Response "
          f"-> If19), exactly one respond per path ({len(corr_out)} simulated)" + (f": {corr_bad}" if corr_bad else ""))

    sif = by_name.get(SCAN_IMAGE_SAVE_IF, {})
    sconds = sif.get("parameters", {}).get("conditions", {})
    guard = [c for c in sconds.get("conditions", []) if "$json.scan_image" in str(c.get("leftValue", ""))
             and c.get("operator", {}).get("operation") == "true"]
    check(sconds.get("combinator") == "and" and len(guard) == 1
          and targets(conns, SCAN_IMAGE_SAVE_IF, 0) == ["Save scan error image1"]
          and "$json.scan_image" in by_name["Save scan error image1"]["parameters"].get("jsonBody", ""),
          f"{SCAN_IMAGE_SAVE_IF} only routes to Save scan error image1 when the row carries a scan_image "
          "(never inserts an empty scan_error_images row)")

    # ---- SQL
    sql_nodes = [n for n in nodes if (n["name"] in website or n["name"] in EDITED_SHARED)
                 and n["type"] == "n8n-nodes-base.postgres" and n["parameters"].get("operation") == "executeQuery"]
    comment_bad = []
    for n in sql_nodes:
        q = n["parameters"]["query"]
        for line in q.split("\n"):
            if "--" in line and re.search(r"\$\d", line.split("--", 1)[1]):
                comment_bad.append(n["name"])
        if "/*" in q:
            comment_bad.append(n["name"] + " (block comment)")
    check(not comment_bad, "no $n placeholders inside SQL comments" + (f": {comment_bad}" if comment_bad else ""))
    try:
        import pglast  # type: ignore
        sql_bad = []
        for n in sql_nodes:
            try:
                pglast.parse_sql(n["parameters"]["query"])
            except Exception as e:  # noqa: BLE001
                sql_bad.append(f"{n['name']}: {e}")
        check(not sql_bad, f"SQL of {len(sql_nodes)} website/edited Postgres nodes parses (PostgreSQL parser via pglast)"
              + (f": {sql_bad}" if sql_bad else ""))
    except ImportError:
        check(True, "(pglast not installed; SQL syntax check skipped -- pip install pglast)")

    # ---- JS syntax
    node_bin = shutil.which("node")
    if node_bin:
        snippets = []
        for name in sorted(website | {RAW_UPSERT_GATE, CORRECTION_AFTER, *EDITED_SHARED, *PHONE_REMARK_NODES}):
            n = by_name[name]
            if n["type"] == "n8n-nodes-base.code":
                snippets.append({"where": f"{name} (jsCode)", "kind": "code", "src": n["parameters"]["jsCode"]})
            for s in strings(n.get("parameters", {})):
                if s.startswith("=") and "{{" in s:
                    for m in re.finditer(r"\{\{([\s\S]*?)\}\}", s):
                        snippets.append({"where": f"{name} (expression)", "kind": "expr", "src": m.group(1)})
        checker = r"""
const vm = require('vm');
const items = JSON.parse(require('fs').readFileSync(process.argv[2], 'utf8'));
const bad = [];
for (const it of items) {
  const wrapped = it.kind === 'code'
    ? '(async function(){\n' + it.src + '\n})'
    : '(function(){ return (' + it.src + '\n); })';
  try { new vm.Script(wrapped); } catch (e) { bad.push(it.where + ': ' + e.message); }
}
console.log(JSON.stringify({ total: items.length, bad }));
"""
        with tempfile.TemporaryDirectory() as td:
            sp = Path(td) / "snippets.json"
            cp = Path(td) / "check.js"
            sp.write_text(json.dumps(snippets), encoding="utf-8")
            cp.write_text(checker, encoding="utf-8")
            res = subprocess.run([node_bin, str(cp), str(sp)], capture_output=True, text=True)
            try:
                r = json.loads(res.stdout.strip().splitlines()[-1])
                check(not r["bad"], f"JS syntax OK for {r['total']} website/shared code nodes and expressions"
                      + (f": {r['bad']}" if r["bad"] else ""))
            except Exception:  # noqa: BLE001
                check(False, f"JS syntax check failed to run: {res.stderr[:300]}")
    else:
        check(True, "(node not found; JS syntax check skipped)")

    return report()


def report():
    for ok, msg in results:
        print(("PASS  " if ok else "FAIL  ") + msg)
    failed = sum(1 for ok, _ in results if not ok)
    print(f"\n{len(results) - failed}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
