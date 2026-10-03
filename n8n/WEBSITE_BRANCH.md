# Website batch branch (`sast-batch-scan`)

`perfect-101-website.json` is the full "perfect 101" workflow with:

1. **The draft "SAST from Website" branch replaced** by a website front end of 26 nodes (all named `Website …`). The front end only does what is website specific: payload validation, duplicate check, OCR/OMR (same Python + Gemini calls as the phone), not-a-form reject, reconcile, batch integrity check, the check of the form's instructor/subject against the database, the clean/flagged decision, and storing the clean page's raw row so the backend can be answered. A page is credited automatically to the instructor and subject the staff member picked for the batch **only when the form agrees with them**. A blank/unreadable instructor, subject or student id is held for review (`instructor_missing`, `subject_missing`, `student_id_missing`), and so is an image file already marked in another batch (`reused_image`). When the form names another instructor/subject that is in the database, the page is flagged with that instructor/subject **pre-filled as the suggestion**; nothing is credited until staff approve it (see "Batch check and the form's instructor/subject").
2. **No duplicated calculation nodes.** After the raw row is stored and the webhook answered, a clean page goes through `Website Map to Target Format` into the **existing `If19`**, the same entry `Map to Target Format3` uses for phone scans. The management/performance recompute, overall totals, question means, `If28` alerts and the remark AI path exist once and are shared by phone, website, Google Sheet and correction executions. A flagged page reuses the **phone flagged path** (`Save scan error image` → `build flagged record1` → `save flagged to failed_scan_queue1` → `Respond to Webhook`).
3. **Every hardcoded Supabase key removed.** 8 HTTP Request nodes now use the n8n credential **Supabase iEvaluate (secret key)** (type *Supabase API*), referenced by name and by the live credential id (taken from the live export), so a re-import into the same n8n keeps it linked. That credential sends both the `apikey` and `Authorization: Bearer` headers. The `Content-Type`/`Prefer` headers stay as they were.
4. **Aggregate fix in the shared main flow.** See the last section.
5. **Sexual words in remarks are masked** (`iyot` → `****`) before anything is stored, with a second Gemini look on scans to rule out handwriting misreads. Insults and curses are not touched. See "Sexual-word filter (remarks)".
6. **The workflow imports as inactive** (`active: false`). Create and select the credentials first, then activate it.
7. **Corrections of a flagged scan** (`sast-manual-correction`, used by the website review page and by Flutter) take the instructor/subject **ids** when they are sent, credit website approvals to the page's term, and answer before the shared flow runs. See "Corrections (sast-manual-correction)".

Rebuild or verify at any time. The input files are never modified:

- `C:\Users\rodzh\Downloads\perfect 101 (4).json`: the original export, the **patch base**.
- `C:\Users\rodzh\Downloads\perfect 101 (5).json` (optional): the **live** export. Only the canvas positions of same-named nodes are taken from it, so a re-import keeps the layout arranged by hand in n8n. It cannot be the patch base: it already contains this build (the draft nodes and every patch anchor are gone).

```
python tools/build_website_branch.py      # regenerates perfect-101-website.json (byte-identical on every run)
python tools/validate_workflow.py         # static + path-simulation checks (optional: pip install pglast for SQL parsing)
node   tools/test_website_code.js         # runs the website Code nodes and the edited shared nodes with mocked inputs
node   tools/test_website_sql.js          # runs the SQL (and migrations/004) on a THROWAWAY local Postgres (needs pg-promise; see file header)
node   ../omr_patch/n8n_compat_check.js …  # real /process outputs through both reconcilers (see omr_patch/README.md)
node   tools/test_website_code.js --print-profanity-patterns   # SQL pattern lines for migrations/004 after a word-list change
```

The Code and SQL sources are kept in `tools/website_src/`. Edit them there, then re-run the build. A `// @@include profanity.js` / `remark_verify.js` line in a source is replaced by that shared helper (full-line comments dropped); base-workflow nodes that mask remarks get the same `profanity.js` block in front of their code.

## Flow

```
SAST from Website (POST sast-batch-scan, Header Auth X-Webhook-Secret, respond via node)
 └ Website Prepare Scan ── error ─► Website Invalid Request Response ─► Website Respond   (flagged, NOT saved)
    └ Website Duplicate Check ── error ─► Website Not Saved Response ─► Website Respond   (flagged, NOT saved)
       └ Website Is Duplicate? ── yes ─► Website Duplicate Response ─► Website Respond     (duplicate)
          └ Website Attach Image ─► Website Python OMR (omr-api:5000, paper_size SHORT/LONG from the backend, default LONG; 60 s)
             └ Website Known Names (known subjects for the prompt, no instructor names; continue on error)
             └ Website AI Validation (Gemini, same prompt/schema as phone; 90 s x 3 tries) ── error ─┐
                └ Website Is Not A Form? ── yes ─► Website Rejected Response ─► Website Respond (rejected)
                   └ Website Remark Check (sexual-word filter on Gemini's remark)                        │
                      └ Website Remark Needs Check? ── yes ─► Website Verify Remark Words (Gemini, 15 s, 1 try) ─┐
                         └ no ─────────────────────────────────────────────────────────────────────┐    │     │
                   └ Website Reconcile OCR OMR  ◄──────────────────────────────────────────────────┴────┴─────┘ (Python ratings, Gemini cross-check, text from Gemini, remark masked)
                      └ Website Identity Check (batch integrity, 1 Postgres query) ── error ─┐
                         └ Website Form Match (form's instructor/subject vs the DB) ◄───────┘ ── error ─┐
                            └ Website Decide ◄──────────────────────────────────────────────────────────┘
                               └ Website Is Flagged?
                                  ├ no ─► Website Raw Upsert ── error ─► Website Not Saved Response ─► Website Respond (flagged, NOT saved)
                                  │        └ Website Raw Stored? ── no (another page of this student) ─► Website Flagged Record
                                  │           └ Website Success Response ─► Website Respond Saved   (success, row already stored)
                                  │              └ Website Map to Target Format ─► If19  ═══ SHARED MAIN FLOW ═══
                                  │                 If19 → If1 → Code in JavaScript (marks the row as stored) → Loop Over Items3
                                  │                   → Raw Row Already Stored? ── yes ─► Loop Over Items3 (shared upsert skipped)
                                  │                   Loop Over Items3 [done] ─► Raw Upsert Done ─► Management Node1 / Performance1
                                  │                   → recompute → overall totals → question means → If28 alerts; If1 → If21 remark AI
                                  └ yes ─► Website Flagged Record ◄── error outputs of Reconcile / Decide
                                            └ Save scan error image → build flagged record1 → save flagged to failed_scan_queue1
                                               └ Respond to Webhook   (website contract object: flagged; or flagged + internal_error_not_saved)
```

Every website path ends at exactly one Respond node that returns **HTTP 200 with one JSON object**. `validate_workflow.py` simulates every branch combination and checks that exactly one respond fires per website outcome, and that no other webhook can reach a website Respond node.

**No Respond node downstream of the shared flow.** `Google LINK from phone1` (Google Sheet import, `sast-link-upload`) is a v2 webhook with `responseMode = lastNode`. n8n's Webhook node (`checkResponseModeConfiguration`) walks every downstream node and throws "Unused Respond to Webhook node found in the workflow" if any Respond node is reachable, which would fail every sheet import. The Google webhook enters the shared flow at `If19`, so nothing downstream of `If19` may be a Respond node. The website therefore answers **before** entering the shared flow, and `validate_workflow.py` enforces the rule for every v2 webhook whose mode is not `responseNode`. Webhook modes: `SAST image from phone`, `sast-manual-correction1`, `import-error-correction1`, `subject-bulk-import1` and `SAST from Website` are `responseNode` (fine); `Google LINK from phone1` is `lastNode` and reaches no Respond node. `Health Check Webhook1` is a **v1** webhook (`lastNode`) wired to `Respond Online1`, unchanged from the original export; n8n only runs this check for v2 webhooks.

**Respond after save.** `Website Raw Upsert` writes the page's row into `sast_all_raw_data_survey` itself (parameterized SQL, same columns, values and conflict key as the shared `Code in JavaScript` → `Insert or update rows in a table1`). It updates an existing row only when that row is this page (same `task_id`). It never overwrites another page's row: it returns `saved = false`, and the page is flagged `duplicate_student_id`. Only when `saved = true` does `Website Success Response` → `Website Respond Saved` answer `success`. If the query fails, the answer is `flagged` + `internal_error_not_saved` (retryable) and the page does not enter the shared flow. After `Website Respond Saved` (a Respond node passes its item on), `Website Map to Target Format` feeds `If19`. `Code in JavaScript` sees that `Website Raw Upsert` stored the row and marks it `_raw_already_stored`. `Raw Row Already Stored?` then sends it straight back into `Loop Over Items3` without the shared upsert, because a NULL student id would otherwise insert a second copy. A website execution carries exactly one row, so a loop batch is never a mix of marked and unmarked rows. The aggregates, overall totals, `If28` pushes and the remark AI run after the response.

**Flagged responses.** The shared `Respond to Webhook` body checks whether `Website Flagged Record` ran in this execution. Website: it returns the contract object (`flagged`; plus `internal_error_not_saved` when `save flagged to failed_scan_queue1` failed). Phone: exactly the same text as before, so a website execution is never answered in phone format and phone answers are unchanged.

## Shared nodes that were edited

| Node | Change |
|---|---|
| `Code in JavaScript` | Staff id also read from `$('SAST from Website')` `body.staff_id`. `task_id` mapped into the raw row (only when present, so sheet rows never clear it). A blank/whitespace student id becomes NULL, and NULL-student rows are never collapsed by the in-execution dedupe. In website executions the row is marked `_raw_already_stored` (it's already stored by `Website Raw Upsert`). Phone/sheet/correction otherwise unchanged. |
| `build flagged record1` | Uses the `Website Flagged Record` item in website executions (suggested instructor name + `subject_code` and the form-check keys in `partial_data`, `batch_id`). Phone: `partial_data.agreement_percent` added (the Flutter screen ignores keys it doesn't know); otherwise unchanged. |
| `reconcile ocr and omr1` (phone) | Replaced by `tools/website_src/phone_reconcile.js` (the build refuses to run if the original code changed). Same output fields plus `agreement_percent`, `agreement_rows`, `text_source`, `remarks_masked`, `remark_check`; `remarks` has sexual words masked after the re-check. Text only from Gemini; the row-agreement rule replaces the count-based disagreement rules; `omr_unavailable` when Python returned no ratings. See "OMR and OCR rules". |
| `Handle Gemini failure` (phone) | `student_id` is `''` instead of the Python OCR value (text comes only from Gemini). |
| `Respond to Webhook` | Website contract object for website executions; phone text unchanged. |
| `send to managment table1`, `send to performance node1` | Recompute from all raw rows under a transaction advisory lock; `queryBatching = transaction` (see the last section). |
| `Insert or update rows in a table1` | `queryBatching = independently`: one bad row no longer fails the other rows of its batch. **Fix (2026-09-27):** an earlier build wrote `independent`, which is not an n8n option value. The Postgres node then ran **no query**, stored nothing and returned 0 items ("Success", Output: 0 items), so `Loop Over Items3` was never fed again, `done` never fired, and `Raw Upsert Done` → Management/Performance never ran (execution #258, Google Sheet import). This affected every phone, sheet and correction execution; website rows skip this node, so they were fine. The node also has `alwaysOutputData = true`: a run that returns 0 items still sends one empty item back into the loop. `If14` (`$json.error !== undefined`) sends that empty item to its unconnected false output, so `Loop1 Error Flag1` does not fire. |
| `Get staff FCM for flagged scan` | Parameters `[ $('build flagged record1').first().json.user_id ?? null ]`. Its input (`save flagged to failed_scan_queue1`, `Prefer: return=minimal`) has no body, so `$json.user_id` was undefined ("Query Parameters must be a string of comma-separated values or an array of values"). A NULL user id matches no row: no push, no error. |
| `get subject id4` (phone) | Parameters `[ $json.instructor_id ?? null, $json.subject_search_term ?? $('If31').first().json.subject ?? null ]`. When the instructor was not matched, `get instructor id6` (alwaysOutputData) emits an empty item. n8n dropped both empty `{{ }}` values and the query failed ("Variable $1 out of range"). The SQL already handles a NULL instructor: it resolves the subject and links nothing. A comma in the subject no longer splits the value. |
| `Wait1` inputs (grouped remark loop `Loop Over Items8`) | Only `insert data to student remarks2` feeds `Wait1` now. `If25` (true) and `PushNotificaton for sentiment analysisi5` no longer feed it, so the critical-remark push is fire-and-forget, like its sibling `If27`. Before, a group with a Critical remark fed the loop twice. The alert branch sits higher on the canvas, so it started the next group before this group's remarks were inserted: groups ran nested and out of order, and the extra feeds re-emitted `done`. `Branch 3 — Student Remarks2` is never empty (every group has at least one remark, and the Parse node throws or emits one row per remark), so the single feed always fires. |
| `get instructor id7` (sast-manual-correction), `get instructor id9` (import-error-correction) | Also accept placeholder instructors (`is_placeholder = true`, even without a department/role row). Everyone else keeps the old rule (approved + DEPARTMENT_HEAD/FULL-TIME/PART-TIME). On equal scores a real approved account wins. |
| `get instructor id7`, `get subject id6`, `Map target`, `Respond to Webhook5` wiring (sast-manual-correction) | `body.instructor_id` / `body.subject_id` are used when they are the id of a creditable instructor / an existing subject; otherwise the old name/code lookup runs unchanged. Parameters are explicit arrays. `Map target` credits `correction_source = 'website_review'` approvals to `body.term_id`. The webhook is answered before the shared flow: `Map target` → `Mark Queue Validated1` → `Respond to Webhook5` → **`After Correction Response`** (new Code node) → `If19`. See "Corrections (sast-manual-correction)". |
| `Map to Target Format` (Google Sheet import only) | Sets `import_type` (`webhookData.type \|\| "google_sheet"`) on every row. `clean data1` sets it, but this node builds new objects and used to drop it, so `If21` (`import_type` is not empty) sent every sheet row to the per-remark AI path (`If22`, one AI call per remark; 86 rows took 1m48s) instead of the grouped one (`Instructor message2` → `Group feedback by instructor1`). Phone (`Map to Target Format3`), website and correction (`Map target`, `Map target1`) rows still have no `import_type` and keep going to `If22`. The field never reaches the DB: `Code in JavaScript` maps explicit columns, and the Set nodes downstream keep only their own fields. |
| Sexual-word filter (see "Sexual-word filter (remarks)") | `ai validation ocr and omr` (and its website copy): one sentence added to the remarks instruction ("Never censor or mask a word, even a vulgar one: it is filtered later."); schema unchanged. `Code in JavaScript` (`Remarks_and_Suggestions`), `Map to Target Format`, `Map target` (both remark fields), `Map target1`, `Format error for import_errors` (`raw_data`): `maskProfanity`. `build flagged record1`: masks `partial_data.remarks` of every queue row (last safety net) and adds `partial_data.remarks_masked: true` when masked. `Parse Unified AI Result1` / `Add Parse Code node1`: a wordcloud keyword that contains a sexual word or an asterisk is dropped (the individual parser drops them before its "at least one keyword" fallback); the stored remark and the AI texts are masked. `Unified AI Analysis1` / `Individual AI Analysis1`: one keyword rule added to the prompt. Every patch uses a guarded anchor (the build stops if the base code changed). |
| `ai validation ocr and omr` (phone) and its website copy: known names | The phone prompt lists the known instructors and subjects from `Phone Known Names` and its schema has two optional fields `known_instructor` / `known_subject`; the website copy lists the known subjects only (from `Website Known Names`) and has only the optional `known_subject` (no instructor name is sent to Gemini; see "Known instructors and subjects in the OCR prompt"). In both, the subject hint no longer names a fixed code list. Every existing field and the `required` list are unchanged. The phone body reads the image with `$('SAST image from phone').first()` instead of `.item` (one scan per execution), so the extra node before it cannot break item pairing. |
| new shared nodes | `Raw Upsert Done` (Code) and `Raw Row Already Stored?` (If). Phone remark re-check: `Phone Remark Check` (Code), `Phone Remark Needs Check?` (If), `Phone Verify Remark Words` (HTTP, Gemini). Corrections: `After Correction Response` (Code). Phone OCR: `Phone Known Names` (Postgres). |
| connections | `If25`/`PushNotificaton for sentiment analysisi5` → `Wait1` removed (see above). `If1` no longer feeds `Management Node1`/`Performance1`; `Loop Over Items3` [done] → `Raw Upsert Done` → both. `Loop Over Items3` [loop] → `Raw Row Already Stored?` (true → back to the loop, false → upsert). Every upsert result goes back into the loop (previously only `If14`'s success branch did). New inputs: `If19` ← `Website Map to Target Format`; `Save scan error image` ← `Website Flagged Record`. Phone: `Is not a SAST form?` (false) → `Phone Remark Check` → `Phone Remark Needs Check?` (true → `Phone Verify Remark Words` → `reconcile ocr and omr1`; false → `reconcile ocr and omr1`) instead of straight to the reconciler. Corrections: `Map target` → `If19` removed; `Respond to Webhook5` → `After Correction Response` → `If19` added. Phone OCR: `image to ocr and omr python` → `ai validation ocr and omr` replaced by `image to ocr and omr python` → `Phone Known Names` → `ai validation ocr and omr`. |

## OMR and OCR rules (both reconcilers)

`Website Reconcile OCR OMR` and the phone `reconcile ocr and omr1` follow the same rules (user decision, 2026-09-27):

- **Ratings come from the Python OMR.** Gemini is a second reader that only cross-checks them. The stored rating per row is Python's value, or Gemini's value when Python read that row blank.
- **All text comes only from Gemini**: instructor, subject, AY/semester, remarks, date, student id. The Python OCR text (`ocr_fields`) is never used. It is unreliable: on a real page it read the instructor as "AY/Semester: L U" and the subject as "Mà essor". When Gemini fails, the text fields are empty and the page is flagged by the Gemini-failure reason anyway.
- **Row agreement (reported).** `agreement_percent` / `agreement_rows` = rows where Python and Gemini give the same answer / 20. Both blank counts as the same answer; one blank and the other not counts as different. It goes into the reconciler output, `failed_scan_queue.omr_comparison`, `partial_data.agreement_percent` and the review note ("Python and Gemini agree on 18 of 20 rows (90%).").
- **Which differences count (user decision, 2026-10-01).** Gemini (flash-lite) is the weaker bubble reader, so a difference only counts against the page when it is a real conflict:
  - **Confident scanner wins.** A row Python read (non-blank) that is NOT weak keeps Python's answer when Gemini differs (another rating or blank). The row stays `agree: false` in `comparisons` with `resolved_by: 'python_confident'` and is not counted. A row with no `omr_debug` entry is not treated as confident.
  - **One level apart is not a conflict.** When both engines read a rating and they are fewer than `FLAG_DIFF_MARGIN = 2` levels apart (S vs VS), the row gets `resolved_by: 'adjacent_level'` and is not counted, by the agreement rule nor by `python_weak_disagreement`.
  - Everything else counts (`resolved_by: null`): weak or no-confidence Python vs a Gemini rating 2+ levels away, weak Python vs Gemini blank, and Python blank vs a Gemini rating (Gemini's value is used).
- **Agreement rule.** `flag_agreement_rows = 20 − counted conflicts` (in `omr_agreement`, next to `resolved_differences`). The page is flagged `omr_agreement_below_30` when that is below `AGREEMENT_MIN = 0.30` (6/20 passes, 5/20 is flagged), a single named constant at the top of each reconciler. The review note lists only the counted rows and says how many other differing rows were kept as the scanner read them.
- This replaces the old count-based rules `omr_disagreement_N_of_20`, `severe_omr_disagreement_N_rows` and `low_confidence_omr_N_rows`.

- **Layout safety net (2026-10-01, `tools/website_src/layout_check.js`, one copy in each reconciler).** The OMR reads ratings by position, so a form printed with another layout would be read confidently into the wrong rows/columns. `layout_mismatch` is raised, and the confident-scanner rule is switched off for the whole page (it is decided before the rows are compared), when any of these signals is present (listed in the output's `layout_check.signals`, with Gemini's `column_order` and the OMR's `layout`):
  - from the OMR's optional `layout` object (`/process`, added 2026-10-01): `columns_detected_N` (vertical rulings count N != 5 columns), `columns_misaligned` (more than 1 of the 6 column boundaries used has no ruling under it), `rows_detected_N` (N != 20, extra row lines inside a section), `row_rulings_missing` (under 85% of the row boundaries used sit on a ruling), `structure_fit_poor` (structure-fit `score_per_boundary` < 0.50);
  - `column_order_...`: Gemini's optional `column_order` (both main prompts now ask for the headers left to right) is not O, VS, S, F, US (full words and the printed numbers 5..1 are accepted as the same order);
  - `fallback_grid_low_agreement`: `grid_source` is `fallback` and fewer than 80% of the rows agree exactly with Gemini.
  Every field is optional: an older OMR without `layout`, or a Gemini answer without `column_order`, never raises it. Thresholds were measured on 89 correctly read forms (all signals clean) and 17 scans of a form whose rating block is printed wider (2026-10-01 set: every one raises `columns_misaligned`, `rows_detected_N`, `row_rulings_missing` and `structure_fit_poor`, or the fallback grid).

A row is **weak** when its `omr_debug` top score is < `WEAK_TOP_SCORE` (0.05) or the margin to the second-best column is < `WEAK_MARGIN` (0.02).

| Reason | Website | Phone | When |
|---|---|---|---|
| `ai_validation_failed` | yes | yes (`Handle Gemini failure`) | the Gemini call failed |
| `gemini_unavailable` | yes | yes | Gemini answered but returned no usable JSON |
| `omr_unavailable` | yes | yes | new: the Python OMR call failed or returned no ratings. There is nothing for Gemini to validate, so Gemini's reading alone never passes |
| `omr_agreement_below_30` | yes | yes | fewer than 30% of rows free of a counted conflict (confident-scanner and one-level differences do not count) |
| `not_a_sast_form: …` | yes | yes | normally rejected earlier by the `Is Not A Form?` node |
| `grid_not_detected` | yes, never dropped | yes, dropped when Gemini found answers (phone rule, unchanged) | the OMR did not find the table |
| `omr_fallback_grid` | yes | no | the OMR used its fixed fallback grid |
| `layout_mismatch` | yes | yes, never dropped | the printed layout is not the expected 5 columns O, VS, S, F, US x 20 rows (10 + 10); see "Layout safety net" below. While it is raised no row is resolved as `python_confident` |
| `poor_scan_quality` | yes | yes | Gemini says poor quality AND (a critical issue, or a real difference ≥ 2 columns / too many blanks); unchanged |
| `low_confidence_ocr_N_rows` | yes | yes | Python itself was unsure on ≥ 4 rows, even where Gemini agrees; kept |
| `python_weak_gemini_blank_N_rows` | yes | no | Python read a mark on a weak row that Gemini reads as blank (a speck), on ≥ `WEAK_BLANK_LIMIT` (3) rows |
| `python_weak_disagreement_N_rows` | yes | no | both read a rating on a weak Python row and they are ≥ 2 levels apart, on ≥ `WEAK_DISAGREE_LIMIT` (3) rows |
| `too_many_blanks_N_of_20` | yes | yes | more than 10 rows blank for both engines |
| `missing_instructor`, `missing_subject` | no | yes | the phone has no batch selection, so a blank name (from Gemini) still needs review |

**Why the Python-confidence rules stay.** Weak or speck rows rarely reach the agreement rule (it needs 15 counted conflicts), so `python_weak_gemini_blank` and `python_weak_disagreement` (3+ rows each, since 2026-10-01; 1 before) and `low_confidence_ocr` (4+ weak rows) are what catch those. The measurements below were taken with the old limits of 1 row and before the confident-scanner / one-level rules; they have not been re-run. On the 22 + 16 + 16 real scanner forms and 42 phone forms (Gemini mocked with the ground truth), they raise no flag on a fully correct scanner form. The only flags on fully correct forms are the 6 phone forms and 1 short-on-Legal form flagged `low_confidence_ocr`, which the original code flags too.

## Sexual-word filter (remarks)

User decision, 2026-09-27: **only sexual words are masked** (sex acts, genitals, sexual slang) in English, Tagalog and Bisaya/Cebuano. Each word becomes asterisks of the same length (`iyot` → `****`, `pak yu` → `*** **`). **Insults and curses are allowed and never masked**: idiot, kupal, gago, tanga, bogo, buang, yawa, piste, bwisit, animal ka, atay, lubot, shit, bitch, … Masking is never a flag reason.

**Word list** (`tools/website_src/profanity.js`, the single source; the build copies it into every node listed below):

| Group (toggle) | Default | Words |
|---|---|---|
| `sexual` | on | Bisaya: iyot/iyut, bilat, oten/otin/uten/utin, salsal, puday, tamod/tamud, bayag. Tagalog: kantot/kantut, hindot/hindut, jakol/jakul/dyakol, tsupa/chupa, burat, titi, tite, etits, puke, puki, pekpek, kepyas, kipay, betlog. English: pussy, cunt, boobs, boobies, tits, titties, porn, porno, blowjob, handjob, horny, dildo, jizz (+ plurals) |
| `fuck` | on | fuck, fuk, fck, phuck (+ -ing/-in/-er/-ed, motherfucker), fak, pakyu, pakshet, "pak yu" / "pak you". Masked in every use, also as a plain curse: the word *is* the sex act and a filter cannot tell the uses apart. |
| `sexual_curses` | **off** | puta, pota, putangina, potangina, tangina(mo), "putang ina", "tang ina", whore, slut. These only literally reference sex work; they are everyday curses like yawa or gago, so they are allowed. Set to `true` to mask them. |

Matching is whole-word and case-insensitive and also catches repeated letters (`iyooot`), separators (`i.y.o.t`, `i-y-o-t`, `nag-iyot`), spaced letters (`i y o t`), symbol swaps (0→o, 1→i/l, 3→e, 4→a, @→a, $/5→s, 7→t, 8→b, !/|→i, v→u, `*` for a vowel), Tagalog/Bisaya affixes for verbs and `bilat`/`oten` (`giiyot`, `kaiyot`, `maniyot`, `iyotan`, `kinantot`, `kumantot`, `nagkakantutan`, `kantotero`) and English endings. Short nouns (titi, tite, puki, …) match only as themselves, so `katiting` and `titig` stay.

**Left out on purpose**: `libog`/`malibog` (Tagalog "lust", but Bisaya "confusing": "libog kaayo ang lesson"); words with an ordinary meaning (buto, bate/bati, supot, utong, suso, itlog, pepe, kiki, lagay, cock/cockfighting, cum laude, balls, sexy); `dick` (the nickname "Dick"; as slang mostly an insult); clinical or complaint words that can be course content or a misconduct report (sex, sexual, penis, vagina, semen, masturbation, rape, harass, manyak, bastos); acronyms (wtf).

**Where it applies**

- **Scans (phone and website): "check again".** The main Gemini call transcribes the remark uncensored. `<branch> Remark Check` runs the filter on Gemini's remark. No flagged word → nothing changes, no extra call. A flagged word → `<branch> Verify Remark Words` sends the same image, the transcribed remark with the flagged words numbered in brackets, and asks for JSON per word `{index, word, really_written, correct_reading}` (one call, 15 s, no retry, same model and credential). The reconciler then stores: confirmed → masked; misread → the correct reading (masked again if that is also a sexual word); call failed, timed out, skipped or unparsable → masked (fail safe). Unmasked text exists only inside the n8n execution.
- **Website time budget.** The re-check only starts when it can end within 340 s of the page arriving (`received_at_ms` from `Website Prepare Scan`; 340 s = the OMR + Gemini worst case the backend's 360 s already covers). Otherwise it is skipped and the word masked, so the backend budget is unchanged.
- **Typed text (Google Sheet imports, `sast-manual-correction`, `import-error-correction`)**: no image to re-check, deterministic filter only (`Map to Target Format`, `Map target`, `Map target1`).
- **Before every write**: the reconcilers and `Website Decide` (raw row, `partial_data`), `Code in JavaScript` (`Remarks_and_Suggestions`, all sources), `build flagged record1` (every `failed_scan_queue.partial_data`), `Format error for import_errors` (every `import_errors.raw_data`), and both remark-AI parsers (`student_remarks`, wordcloud, AI texts). The remark AI (If21 → `Instructor message2/3`) reads the If1 items, which come from the masking mappers, so it only sees masked text; a keyword with a sexual word or an asterisk is dropped.
- **Recorded as** `remarks_masked` + `remark_check` (counts only, never the words) in the reconciler output, and `partial_data.remarks_masked: true` on queued pages. Clean rows just carry the asterisks.

**Existing data** (optional): `migrations/004_mask_existing_profanity.sql` masks the same list in `sast_all_raw_data_survey."Remarks_and_Suggestions"`, `student_remarks.remark`, `failed_scan_queue.partial_data->'remarks'` (+ `validated_data`), `import_errors.raw_data` (`Remarks & Suggestions` / `remarks`), and deletes `instructor_wordcloud` rows whose word is sexual or masked. PART 1 is a read-only preview; PART 2 runs in one transaction and can be run again safely. A masked `student_remarks` row that would duplicate another row of the same instructor/subject/term (unique key) is deleted instead (a clean identical row is kept first, else the oldest). Its patterns are generated from `profanity.js`; `test_website_sql.js` checks that the SQL and the n8n filter mask identically on a synthetic corpus.

**Changing the list**: edit `profanity.js` (word rows or a group toggle), run the build and the tests; for the migration, paste the output of `node tools/test_website_code.js --print-profanity-patterns` into its two pattern blocks.

## Known instructors and subjects in the OCR prompt

`Phone Known Names` (`tools/website_src/known_names.sql`) and `Website Known Names` (`tools/website_src/website_known_names.sql`) are one Postgres query each (continue on error, always one item) right before the main Gemini call.

- **Phone** (unchanged): lists the creditable instructors (approved + DEPARTMENT_HEAD/FULL-TIME/PART-TIME, or a placeholder) and every subject as `CODE = title`, sorted alphabetically. The prompt asks Gemini to keep `instructor` / `subject` exactly as written and to return `known_instructor` (a name copied from the list) and `known_subject` (a code from the list), or empty when none clearly fits.
- **Website** (privacy): **no instructor names are looked up or sent to Gemini.** The query returns only `subjects` / `subject_list` (the same subject list as the phone, no query parameters), the prompt lists only the subjects, and the schema has only the optional `known_subject`. Gemini's raw `instructor` reading is matched against the database by `Website Form Match` / `Website Decide` alone (DB candidates, token fuzzy scoring). `Website Verify Remark Words` only sends the flagged remark words and the scan image.

When the lookup fails the prompt reads exactly as before.

Gemini's pick is only a hint (`tools/website_src/name_match.js`, one copy in `Website Decide` and the phone reconciler):

- **Website** (subject only): the pick counts (score 0.95) for the batch subject; for any other subject only when it is on the list and the raw reading supports it (token score ≥ 0.30). A pick that is not on the list is ignored. It is stored in `partial_data.known_subject`. There is no instructor pick (`known_instructor` is neither requested nor stored; Decide ignores it if present).
- **Phone**: `instructor` / `subject` become the listed name / code (so `get instructor id6` / `get subject id4` find them exactly) under the same two conditions; otherwise the raw reading is kept. Nothing written on the form → nothing picked.

## Batch check and the form's instructor/subject

User decision, 2026-09-28: when the instructor or subject **written on the form** (read by Gemini) differs from the batch selection and can be found in the database, the page must go to **that** instructor/subject, but only after a person reviews it. Update the same day (a real batch of 8 pages of the same instructor was flagged completely because Gemini misread the name, e.g. "Kodi Harvey D. Licayan" for "Rodz Harvey D Licayan"): the staff picked the batch, so **the batch wins unless another real instructor/subject clearly beats it**; a form that matches nobody is no longer flagged. So the page is flagged into `failed_scan_queue` with the matched instructor/subject pre-filled as the suggestion (partial_data), and **nothing is written to `sast_all_raw_data_survey` until staff approve it** (the website review page or Flutter → `sast-manual-correction`). This replaces the earlier rule "the form's instructor/subject never cause a flag"; the removed reasons below stay removed.

### Website Identity Check (batch integrity, unchanged)

`Website Identity Check` is one Postgres query with 5 parameters: `[instructor_id, subject_id, term_id, student_id, task_id]`. It never looks at the form's text. It returns:

- **Batch references.** Whether the batch instructor exists / is disabled / is a placeholder, and the batch subject exists. It returns the batch instructor's DB name and the subject code/name. It also returns the resolved term: the batch term, else `system_settings.current_term_id`. A missing or disabled instructor, a missing subject or no term flags `batch_reference_invalid`.
- **Placeholder instructors** (`user_info.is_placeholder = true`; created with `disabled = true` and `account_status = 'pending'`) are valid batch instructors. The query never reports them as disabled, and `Website Decide` has the same guard. `get instructor id7` / `id9` resolve them too.
- **Duplicate student.** A row of **another** page (different or no `task_id`) already exists for the batch instructor, subject, resolved term and this non-blank student id → `duplicate_student_id` instead of overwriting that row. The raw upsert's `WHERE s.task_id = EXCLUDED.task_id` guard covers two requests racing.

If the query fails, the page is flagged `batch_check_failed`.

### Website Form Match (the form vs the database)

`Website Form Match` (`tools/website_src/form_match.sql`) runs after `Website Identity Check` (both of its outputs) with 5 array parameters `[batch instructor_id, batch subject_id, OCR instructor, OCR subject, term]`. It only **scores**; `Website Decide` applies the thresholds. Always exactly one row.

**Normalisation** (form text and DB names alike): accents folded with `translate()` (Supabase has no `unaccent`), lower case, `ma'am`/`ma’am`/`maam` → `maam` before punctuation is stripped, every non-letter → space, titles/degrees/suffixes removed (`prof professor dr doc mr mrs ms miss madam maam sir engr eng atty arch rev fr hon instr instructor teacher phd edd dba mba mit mat maed lpt rn cpa jr sr ii iii iv`; not `ma`, which is also short for Maria), spaces collapsed. Word order does not matter ("Cruz, Juan Dela" = "Juan Dela Cruz").

**Instructor score** per candidate (o = the normalised form text):

| Condition | Score |
|---|---|
| surname hit (every surname word is a word of o, or the surname written as one word is, or `word_similarity(surname, o) ≥ 0.8`) and first-name hit (`word_similarity` of the first name or its first word ≥ 0.7) | max(0.95, similarity) |
| surname hit and a one-letter word of o = the first letter of the first name ("J. Dela Cruz") | 0.85 |
| o is exactly the surname ("Dela Cruz") | 0.70 |
| surname hit only | max(0.60, similarity) |
| otherwise | `similarity(full name, o)` |

Candidates are the creditable instructors (approved + DEPARTMENT_HEAD/FULL-TIME/PART-TIME, or a placeholder: the same rule as `get instructor id7`); the batch instructor is always scored.

**Subject score**: a **code hit** (1.0) when a key of the form text equals the subject code with only its letters/digits (keys: every `LETTERS[ .-]DIGITS[LETTER]` group, the whole text and its last `(...)` part, at least 3 characters, as `get subject id6` does); otherwise `max(similarity, word_similarity)` of the subject name. `Website Decide` adds 0.05 when the subject is linked (`instructor_subjects`, same term) to the instructor the page would go to.

The query returns every candidate (not only the top 10). **`Website Decide` adds a token score** (`name_match.js`) and uses the higher of the two: each database name word is paired with its best form word (edit-distance ratio ≥ 0.55, an initial matches the first letter, 4+ letters that start a word count as an abbreviation, e.g. "Prog"), and the score is 0.75 × the matched share of the database name + 0.25 × the matched share of the form text (words weighted by length). "Kodi Harvey D. Licayan" scores 0.78 for Rodz Harvey d Licayan, "Harry D. Viiayan" 0.60, "Proh Harvey D. Llegan" 0.39 (still the best match). Subjects: a code one letter/digit off the batch code ("PO121", "PC127" for PC121) scores 0.90 for the batch only; title words ignore codes and small words (and, of, the, to, …).

### Website Decide: the verdict

Thresholds (constants at the top of `decide.js`): `NAME_MATCH_MIN = 0.70`, `MATCH_MARGIN = 0.10`, `BATCH_MARGIN = 0.20`, `BATCH_SAME_MIN = 0.40`, `SUBJECT_SAME_MIN = 0.35`, `BATCH_BEST_MIN = 0.25`, `MIN_NAME_LETTERS = 3`, `SUBJECT_MATCH_MIN = 0.60`, `SUBJECT_NAME_COVER = 0.75`.

An other candidate is **strong** when it scores ≥ `NAME_MATCH_MIN` (instructor) or ≥ `SUBJECT_MATCH_MIN` with at least 75% of its title on the form and no unknown course code next to it (subject). It **beats the batch** when it also leads the batch by ≥ `BATCH_MARGIN`.

| `instructor_match` / `subject_match` | When | Flag | Suggestion |
|---|---|---|---|
| `found` | exactly one other candidate beats the batch, or the best of them leads the second by ≥ 0.10; subject: exactly one other subject code on the form (and not the batch code) | `instructor_mismatch_found` / `subject_mismatch_found` | **the matched one** |
| `ambiguous` (flagged) | two or more others beat the batch within 0.10 of each other (e.g. "King" with Joseph and Robert King); two other subject codes | `instructor_mismatch_ambiguous` / `subject_mismatch_ambiguous` | batch |
| `ambiguous` (not flagged) | a strong other candidate that does not beat a weak batch (batch < the minimum): the batch is among the contenders | none | batch |
| `same` | nobody beats the batch and the batch scores ≥ 0.40 (subject ≥ 0.35), or it is the best match with ≥ 0.25; the batch subject code (or one off it) on the form | none | batch |
| `not_found` | nobody beats the batch and the batch scores too little | none (noted in `review_note` / `partial_data` only) | batch |
| `unreadable` | blank, a placeholder word (`N/A`, `unknown`, …), fewer than 3 letters (subject: fewer than 2 letters/digits) | `instructor_missing` / `subject_missing` (held for review, never saved silently) | batch (pre-selected) |
| `unchecked` | `Website Form Match` failed (its error output) | `form_match_failed` (held for review) | batch |

A course code on the form that belongs to no subject ("IT1021") never lets a title make another subject `found`, and a code one off another subject's code only ever supports the batch. The reason codes are unchanged; `instructor_mismatch_not_found` / `subject_mismatch_not_found` are simply no longer raised. `identity_check` shows `mismatch` only for a flagged side (`match` for same / not-flagged ambiguous, `unreadable` otherwise).

When the suggestion differs from the batch (`found`), a `duplicate_student_id` computed against the batch instructor/subject is dropped (the review note says so); the backend re-checks duplicates against the real target when the page is approved. The `*_missing` reasons are never dropped.

**Student id.** Blank, no letter/digit, or a placeholder word (`N/A`, `none`, `illegible`, …) → `student_id_missing`, stored as NULL (never a `duplicate_student_id`). The reviewer types it from the sheet, or approves without one.

**Same image in another batch.** Dedupe (watcher state, `claim_batch_item`, `task_id`) is per batch, so a copy of an already-scanned picture pasted into a new batch gets a new `task_id`. The backend looks up `scan_batch_items` with the same `file_sha256` in **another** batch with status `success`, `flagged` or `duplicate` (`rejected` = not a form, nothing stored) and sends `reused_image: {batch_id, status}`; `Website Decide` flags `reused_image` (not rejected, not saved) and staff decide. A failed lookup is logged and the page is marked without it (migration 012 indexes `file_sha256`).

A page is credited automatically (to the batch) only when all of these hold: the reconciliation raised no reason; the batch references are valid; the instructor, subject and student id on the form are readable; the form names no other instructor/subject that clearly beats the batch; no other stored form of the same student exists; the image file was not already marked in another batch. Only such a page can be picked for the random `spot_check` (about 1 in 20, held for a quick staff check).

Removed reasons (earlier designs; the validator keeps them forbidden): `batch_instructor_mismatch`, `instructor_not_verified`, `batch_subject_mismatch`, `subject_not_verified`, `identity_check_failed`.

## What gets written

**Clean page**

- `sast_all_raw_data_survey` via `Website Raw Upsert` (before the answer; the shared upsert is skipped for this row). Key `(subject_id, term_id, "instructor_ID", student_id)`:
  - `instructor_ID`, `subject_id`, `term_id` from the batch; `instructor` = the batch instructor's DB name
  - `task_id`, `sao_staff_id` = `staff_id`, `validation_status` = `'auto'`, `manually_corrected` = false
  - `student_id` (NULL when blank, so it never matches another row), remarks, `submitted_date` = the scan date in Asia/Manila
  - ratings: 1–5, or NULL for a blank row
- A resent page (same `task_id`) is stopped earlier by `Website Duplicate Check`. Another page of the same student → `duplicate_student_id` (flagged, nothing overwritten).
- Then the shared flow runs exactly as for a phone scan: `management_results` / `performance_results` recomputed from all raw rows, then `overall_total_survey`, `overall_question_means`, `If28` alerts and the remark AI path.
- n8n never touches `scan_batches`. The backend owns its counters.

**Flagged page** (shared phone flagged path)

- `scan_error_images (task_id, image_base64)`, returning `id`.
- `failed_scan_queue`, using only real columns: `task_id`, `user_id` = `staff_id`, `term_id`, `table_found`, `grid_source`, `partial_data` (jsonb), `status` = `'pending'`, `review_reasons` (`text[]`), `omr_comparison`, `validation_status` = `'flagged'`, `scan_image_id`, `batch_id`.
- `partial_data.remarks` is always masked (sexual words → asterisks); `partial_data.remarks_masked: true` is added only when something was masked (the Flutter screen ignores keys it does not know). It is never a review reason.
- `partial_data.instructor` holds the **suggested instructor's DB full name** and `partial_data.subject` the **suggested `subject_code`**: the batch's, unless the form named another instructor/subject that was found. So the Flutter correction screen (`sast-manual-correction`) defaults to the suggestion. What Gemini read on the form is in `partial_data.ocr_instructor` / `ocr_subject`. The explanation is in `partial_data.review_note` (plain English, e.g. "The form names the instructor "Prof. Pedro Reyes", which matches Pedro Reyes in the database, not the batch instructor Juan Dela Cruz. Suggested: credit Pedro Reyes."), and the row agreement is in `partial_data.agreement_percent`.
- Form-check keys in `partial_data` (all pages from this build; `form_check_version: 1`): `instructor_match`, `subject_match` (`same|found|not_found|ambiguous|unreadable|unchecked`); `suggested_instructor_id`, `suggested_instructor_name`, `suggested_subject_id`, `suggested_subject_code`, `suggested_subject_name` (the found candidate, else the batch); `instructor_match_confidence`, `subject_match_confidence` (score of the suggestion, 0..1, 2 decimals, or null when unreadable/unchecked); `instructor_candidates` (≤ 5, `{id, name, score, is_placeholder, is_batch}`) and `subject_candidates` (≤ 5, `{id, code, name, score, is_batch}`), score ≥ 0.30; `batch_instructor_name`, `batch_subject_code`, `batch_subject_name`; plus the existing `batch_id`, `batch_instructor_id`, `batch_subject_id`, `filename`. Pages queued by an internal error carry `unchecked` and the batch values. Older rows lack these keys (the backend treats them as `unchecked` with the batch as the suggestion).
- `omr_comparison` = the reconciler's `omr_agreement` object: `agreement_rows`, `agreement_percent`, `agreement_min_percent`, `flag_agreement_rows`, `resolved_differences`, `comparisons` (per row: python, gemini, used, source, agree, python_weak, resolved_by). The Flutter review screen already lists the disputed rows from `omr_comparison.comparisons`.
- The existing `Get staff FCM for flagged scan` branch runs after the queue insert, as for phone flags.
- If the queue insert fails (e.g. HTTP 409 because a parallel request queued the same `task_id`), the answer is `flagged` + `internal_error_not_saved`.

**Rejected** (Gemini says the image is not a SAST form) and **duplicate** pages write nothing.

## Corrections (sast-manual-correction)

Used by the website review page (backend `POST /reviews/queue/{id}/approve`, `correction_source: 'website_review'`) and by Flutter's failed-scan screen (`correction_source: 'manual_text'`). Flutter keeps working unchanged:

- **`get instructor id7`**: parameters `[body.instructor, body.instructor_id]` (an explicit array; the old `{{ }}` string split a name on commas and dropped an empty value). `body.instructor_id` wins when it is the id of a **creditable** instructor (approved + DEPARTMENT_HEAD/FULL-TIME/PART-TIME, or a placeholder). No id, not a uuid, an SAO/pending/unknown account → the old name lookup runs exactly as before. Flutter only sends an id after an autocomplete pick (typing clears it), so the id and the name agree.
- **`get subject id6`**: parameters `[instructor_id, body.subject, body.subject_id]`. `body.subject_id` wins when it is an existing subject; otherwise the old code/name lookup of `body.subject`. (Flutter's autocomplete fills the text field with "CODE — name", which the code/name lookup never resolved; with the id it now does.) The `instructor_subjects` link for the current term is unchanged.
- **`Map target`**: `correction_source = 'website_review'` with a uuid `body.term_id` → credited to that term (the term stored on the queue row = the batch term). Every other correction keeps the system's current term (`GET TERM BY ID6`), as before.
- **Answer first**: `Map target` → `Mark Queue Validated1` → `Respond to Webhook5` (`{ "success": true }`) → `After Correction Response` (re-emits `Map target`'s items) → `If19`. Before, `Map target` also fed `If19` directly; `If19` sits higher on the canvas, so under `executionOrder: v1` the whole shared flow (raw upsert, aggregates, remark AI, or the `import_errors` path) ran before the answer, against Flutter's 60 s proxy timeout and the backend's 180 s. Side effect: when a node of the shared flow fails after the answer, the queue row is already `validated` (before, the execution stopped before the answer and the row stayed `pending`). No Respond node is downstream of `If19`, so the Google Sheet webhook rule still holds.
- `import-error-correction` is unchanged (it already takes the ids; its answer still waits for the shared flow).

## Updating the LIVE n8n

The live n8n runs the workflow **"perfect 101"** (id `vOMBjDtz15QB4k4u`, active). The live export `perfect 101 (5).json` (2026-09-27) matches the previous build of this file except for:

- `Website Prepare Scan` still hardcodes `paper_size: 'LONG'` (it predates the paper-size change);
- the credential ids are filled in;
- the node ids and canvas positions changed (pasted/arranged in the editor);
- the sticky note "Website Branch Note" was removed.

There were no other hand edits. The build now copies the live positions and credential ids and drops the sticky note.

**Re-import (the only supported way for this release; many nodes changed):**

Update (2026-10-01, no instructor names to Gemini on the website): `Website Known Names` now runs `website_known_names.sql` (subjects only, no query parameters); `Website AI Validation` sends no instructor list and asks only for `known_subject`; `Website Reconcile OCR OMR` and `Website Decide` no longer use `known_instructor` (`partial_data.known_instructor` is no longer written). The phone branch is unchanged. Re-import the same way as below.

Update (2026-09-28, matching fix): adds `Website Known Names` and `Phone Known Names` (Postgres, credential `Postgres account`) before the two main Gemini calls, and changes `Website AI Validation`, `ai validation ocr and omr` (prompt + two optional schema fields), `Website Reconcile OCR OMR`, `reconcile ocr and omr1`, `Website Form Match` and `Website Decide`. The Gemini model is unchanged. Re-import the same way as below.

This release (2026-09-28) adds two nodes, `Website Form Match` (Postgres, credential `Postgres account`) and `After Correction Response` (Code), and changes `Website Decide`, `Website Flagged Record`, `Website Reconcile OCR OMR` (comments), `Respond to Webhook`, `get instructor id7`, `get subject id6` and `Map target`, plus the connections Identity Check → Form Match → Decide and Map target → Mark Queue Validated1 → Respond to Webhook5 → After Correction Response → If19. The new nodes are placed next to their neighbours on the live canvas. No database migration is needed on the n8n side (pages use only existing `failed_scan_queue` columns; the new data is in `partial_data`).

1. Open **perfect 101** → **⋯** menu (top right) → **Import from File…** → choose `n8n/perfect-101-website.json`. This replaces the canvas of the existing workflow, keeping its id and webhook URLs.
2. The credentials are referenced by their live ids **and** names (`Supabase iEvaluate (secret key)` = `t8WKxORo7BHCziqr`, `iEvaluate website webhook secret` = `cGPn1nyAiZqSUx40`, `Postgres account`, `Google Gemini(PaLM) Api account`, …), so they stay linked. If any node shows a red "issues" marker, open it and pick the credential from the dropdown.
3. Click **Save**. Check that the **Active** toggle is on. The file is saved with `active: false`, so switch it on if the import turned it off.
4. Optional smoke test: scan one page whose form names the batch instructor (expect `success`) and one naming another instructor of the database (expect `flagged` with `instructor_mismatch_found`, the other instructor pre-filled in the queue row). Then approve the flagged page from the website review page or correct it in Flutter.

## Credentials to create in n8n

| Name (exact) | Type | Values |
|---|---|---|
| `iEvaluate website webhook secret` | Header Auth | Name `X-Webhook-Secret`; Value = a long random string, the same as the backend's `N8N_WEBHOOK_SECRET` |
| `Supabase iEvaluate (secret key)` | Supabase API | Host `https://lyorziqedepfsgblqewv.supabase.co`; Service Role Secret = the Supabase **secret** API key (new-style secret key, not the anon/publishable key) |

**Credential links.** The Supabase and webhook-secret credentials are referenced by their live ids (`t8WKxORo7BHCziqr`, `cGPn1nyAiZqSUx40`) and names, so on the live instance no node should show a credential issue. On a different n8n instance, pick them in these 9 nodes: `SAST from Website` (webhook secret); `GET TERM BY ID1`, `GET TERM BY ID3`, `GET TERM BY ID6`, `GET TERM BY ID7`, `Insert import_errors1`, `Save scan error image`, `Save scan error image1` and `save flagged to failed_scan_queue1` (Supabase). `Postgres account`, `Google Gemini(PaLM) Api account`, `firebase pushnotification` and `Google Service Account account 2` are referenced by their existing ids.

## Manual steps after import

1. **Rotate the Supabase secret key first.** The old key was hardcoded in the previous export, so treat it as leaked. Supabase dashboard → Project Settings → API Keys → create a new secret key, then revoke the old one. Put the new key only in the n8n credential and the backend `.env`.
2. n8n → **Credentials** → **Add credential** → *Header Auth*. Name it exactly `iEvaluate website webhook secret`; Name = `X-Webhook-Secret`, Value = the backend's `N8N_WEBHOOK_SECRET`. Save.
3. n8n → **Credentials** → **Add credential** → *Supabase API* (skip if it already exists; the live instance already has it). Name it exactly `Supabase iEvaluate (secret key)`; Host = `https://lyorziqedepfsgblqewv.supabase.co`, Service Role Secret = the new secret key. Save.
4. Import `perfect-101-website.json` (Workflows → Import from File). If the old "perfect 101" is active in the same instance, import over it or deactivate it first; both use the same webhook paths.
5. Open each of the 9 nodes listed above. If the credential field is empty or red, pick the credential from the dropdown. Save the workflow. The "issues" markers disappear.
6. Activate the workflow. Production URL: `https://<n8n-host>/webhook/sast-batch-scan`.
7. Scanned JPEGs must stay under n8n's body limit (`N8N_PAYLOAD_SIZE_MAX`, 16 MB by default). The backend sends about 0.6–0.9 MB of base64: 1468×2174 px for long bond, 1870×2370 px for short, greyscale JPEG.
8. Optional, once: clean remarks stored before the sexual-word filter with `migrations/004_mask_existing_profanity.sql` (Supabase SQL editor: run PART 1 alone and review the preview, then run the whole file; take a backup first, masking cannot be undone).

### Timeouts (website)

On a real page Gemini took longer than the old 30 s timeout on both tries. axios aborted the call (code `ECONNABORTED`, which n8n shows as "The connection was aborted, perhaps the server is offline"). The page was then flagged `ai_validation_failed`, and the old code fell back to the Python OCR text. The image size was not the cause (see step 7).

| Step | Timeout | Retries |
|---|---|---|
| `Website Python OMR` | 60 s (was 30) | none; a failure is flagged `omr_unavailable` |
| `Website AI Validation` | 90 s (was 30) | 3 tries (was 2), 5 s apart (was 3) |
| `Website Verify Remark Words` | 15 s | none; only when a sexual word was flagged, and only when it can end within 340 s of the page arriving, else skipped (the words are masked) |
| n8n worst case per page | OMR 60 + Gemini 3×90 + 2×5 + database ≈ 345 s (the remark re-check never adds to it, see above) | |
| backend `N8N_TIMEOUT_SECONDS` | default **360 s** (was 120); the item stays `processing` for 360 + 60 s, so a resend meanwhile gets `409 in_progress`, never a second n8n run | |
| watcher read timeout | 150 s (unchanged). If it gives up first, it retries later and gets `409 in_progress` (re-polls every 15 s) until the stored result is returned. It never causes a duplicate run | |

The phone node `ai validation ocr and omr` keeps its timing: no explicit timeout (n8n's default applies), 2 tries, 3 s apart. `Phone Verify Remark Words` (15 s, 1 try) only runs for a remark with a flagged word. The Flutter app waits 60 s for the whole `sast-scan-upload` call (`automation_service.dart`), so longer timeouts or more retries there would only make Flutter give up first; the 30 s abort was specific to the website node.

## Backend ↔ n8n contract (fixed)

**Request**: `POST /webhook/sast-batch-scan` with header `X-Webhook-Secret: <secret>` and this JSON body:

```json
{ "batch_id": "uuid", "instructor_id": "uuid", "subject_id": "uuid", "term_id": "uuid",
  "station_id": "uuid", "staff_id": "uuid", "filename": "str", "file_sha256": "hex",
  "task_id": "web:<batch_id>:<first 16 hex of sha256>", "image": "<base64 JPEG, no data: prefix>",
  "mime_type": "image/jpeg", "paper_size": "SHORT|LONG",
  "reused_image": { "batch_id": "uuid", "status": "success|flagged|duplicate" } }
```

`paper_size` is optional (the backend sends it for scanner pages after detecting the bond size); anything missing or unknown means `LONG`. `reused_image` is optional (only when the same image file was already marked in another batch); anything invalid is dropped. Other optional extras (`orientation_applied`, `orientation_confidence`, `preprocess`) are ignored.

A wrong or missing secret gets n8n's own 403 before the workflow runs.

**Response**: always HTTP 200 with one object:

```json
{ "status": "success|flagged|rejected|duplicate", "flagged": true, "task_id": "…",
  "review_reasons": ["…"],
  "identity_check": null,
  "message": "…" }
```

`identity_check` is `null`, or, only when a `*_mismatch_*` reason is present, `{ "instructor": "match|mismatch|unreadable", "subject": "match|mismatch|unreadable", "ocr_instructor": "…", "ocr_subject": "…" }`.

- `success`:
  - the page was credited;
  - `review_reasons` is `[]`;
  - `message` names the batch instructor and subject and the Python/Gemini agreement ("Saved and credited to Juan Dela Cruz (IT 101). Python and Gemini agreed on 100% of the rows.").
- `flagged`: the page is in `failed_scan_queue` for review. Possible reasons:
  - OMR and quality: `ai_validation_failed`, `gemini_unavailable`, `omr_unavailable`, `omr_agreement_below_30`, `grid_not_detected`, `omr_fallback_grid`, `layout_mismatch`, `python_weak_gemini_blank_N_rows`, `python_weak_disagreement_N_rows`, `poor_scan_quality`, `low_confidence_ocr_N_rows`, `too_many_blanks_N_of_20`
  - batch: `batch_reference_invalid` (instructor/subject deleted or disabled, no term), `batch_check_failed` (the query failed)
  - the form's instructor/subject: `instructor_mismatch_found`, `instructor_mismatch_not_found`, `instructor_mismatch_ambiguous`, `subject_mismatch_found`, `subject_mismatch_not_found`, `subject_mismatch_ambiguous`, `form_match_failed` (the two `*_not_found` codes are no longer raised; see "Batch check and the form's instructor/subject"; nothing is credited, the suggestion is in `partial_data`)
  - blank/unreadable on the form: `instructor_missing`, `subject_missing` (the batch value is pre-selected), `student_id_missing` (can be approved without one)
  - duplicates: `duplicate_student_id` (another stored form of the same student for this instructor, subject and term), `reused_image` (the same image file was already marked in another batch)
  - internal errors, still queued: `internal_error_reconcile`, `internal_error_decide`
- Two reasons mean the page was **NOT stored anywhere**:
  - **`internal_error_not_saved`**: retryable. Covers database errors (duplicate check, raw upsert) and a failed queue insert. The backend should send the page again later, not treat it as a final result.
  - **`not_saved_permanent`**: permanent. Always with `invalid_request` (missing or invalid ids/image/task_id). Resending the same payload fails again, so the backend should mark the page failed and not retry.
- `rejected`: not a SAST form (`not_a_sast_form`). Nothing is saved.
- `duplicate`: the `task_id` already exists in `sast_all_raw_data_survey` or `failed_scan_queue`. Nothing changed.
- `identity_check` is `null` unless a `*_mismatch_*` reason is present; then it is the object above (the backend stores it in `scan_batch_items.identity`, ScanningLive shows it). `success` answers always have `null`.

## Aggregate fix in the shared main flow (phone / website / sheet / correction)

**The bug.** `calculate mangement1` and `calculate performance1` computed totals only from the items of the current execution. `send to managment table1` and `send to performance node1` then ran `ON CONFLICT … DO UPDATE SET total_responses = EXCLUDED.total_responses, …`. A phone scan or a Flutter correction is one form per execution, so each one overwrote the (instructor, term, subject) aggregate with a single response. That includes corrections of website-flagged pages. A Google Sheet import replaced it with only the sheet's rows.

**What changed.** Existing node names are unchanged.

- `send to managment table1` and `send to performance node1` now both run `tools/website_src/recompute_aggregates.sql`. The parameters are `[instructor_id, term_id, subject_id]`, taken from each item that `calculate …` emits. The per-execution totals in those items are ignored. The statement recomputes both `management_results` and `performance_results` from **all** rows in `sast_all_raw_data_survey`.
- Running it from both nodes is idempotent. Both tables are fresh before `update to overall total2` or `update to overall total3` reads them.
- When `term_id` is NULL, nothing is written, and no NULL-term `instructor_subjects` link is created.
- **Concurrency.** The SQL starts with `DO $lock$ BEGIN PERFORM pg_advisory_xact_lock(hashtext('ievaluate:recompute_aggregates')); END $lock$;`. The n8n Postgres node formats the values client side (pg-promise) and sends the text as one simple query. Both nodes are also set to `queryBatching = transaction` (`db.tx`), so the lock is held until the recompute commits, and the recompute's snapshot is taken after the lock. Two executions can't compute from each other's stale view. One global key is used, so the items of one batch can never deadlock each other. The DO statement returns no rows, so it adds no output item. `test_website_sql.js` checks that a concurrent recompute waits.

**Ordering.** n8n `executionOrder: v1` runs If1's branches one at a time, in canvas order from top to bottom. Before this change the raw upsert (`Code in JavaScript` → `Loop Over Items3` → `Insert or update rows in a table1`) ran before the Management/Performance branches only because it sat highest on the canvas. That depends on layout. The order is now enforced by the connections:

- `If1` no longer feeds `Management Node1` / `Performance1`.
- A new Code node, **`Raw Upsert Done`**, hangs off the **"done" output of `Loop Over Items3`**. That output fires only after every row went through `Insert or update rows in a table1`.
- `Raw Upsert Done` re-emits `$('If1').all(0)` into `Management Node1` and `Performance1`, which receive exactly the items they used to get from If1.
- All connections downstream of those two nodes are unchanged. `validate_workflow.py` checks that no path from If1 reaches the recompute nodes except through that "done" output.

Side effects:

- **Failed rows no longer stop the recompute.** Before, only `If14`'s success branch fed `Loop Over Items3`, so a failed batch stopped the loop and "done" (and the recompute) never ran. Now every upsert result, row or error item, goes back into the loop, once per iteration. "done" therefore fires exactly once, after every batch was attempted, and `Raw Upsert Done` always runs. `If14` keeps only its error-flag branch (`Loop1 Error Flag1`). With `queryBatching = independently`, only the bad row itself fails. With `alwaysOutputData`, even a run with 0 items feeds the loop.
- `If28` fires from both overall-total nodes on every execution whose mean is below 3.0 (unchanged). A website batch is one execution per page, so a low-scoring instructor gets one push per page, the same as phone scans.

## Unique key of `sast_all_raw_data_survey` (checked read-only)

Both the shared n8n upsert and `Website Raw Upsert` use `ON CONFLICT (subject_id, term_id, "instructor_ID", student_id)`, which needs a unique constraint or index on exactly these columns. PostgREST can't show indexes, so on 2026-09-26 I read the live table read-only (backend service client, `select` only):

- 2 367 rows, **0** duplicate (subject, term, instructor, student) groups. That is consistent with a unique key, and phone upserts already work in production, which would be impossible without one.
- **0** rows with a NULL or empty `student_id`, and no row has a `task_id` yet. So the data can't show whether the index is `NULLS NOT DISTINCT`.

Assumed: a plain unique index (NULLs distinct, the PostgreSQL default). Confirm in the Supabase SQL editor:

```sql
SELECT indexdef FROM pg_indexes WHERE schemaname = 'public' AND tablename = 'sast_all_raw_data_survey';
```

If it says `NULLS NOT DISTINCT`, the first blank-student page of an instructor/subject/term is stored and every later one is flagged `duplicate_student_id` (never overwritten). The shared n8n upsert would instead overwrite the blank row.

## Blank student IDs and sheet re-imports

A blank student id is stored as NULL, and NULLs never match in the upsert. So a blank row can't overwrite another student's row, and the in-execution dedupe no longer collapses blank rows. Side effect: **re-importing the same Google Sheet adds its blank-student rows again** (rows with a student id are still updated in place). Options:

1. Do nothing and tell SAO staff not to re-import a sheet. Simplest, but the risk remains.
2. Give sheet rows a stable key: map the Google Sheet row number (or a hash of sheet id + row) into `task_id` in `Map target`, and skip rows whose `task_id` already exists. This is the cleanest fix and matches how website/phone scans dedupe.
3. For blank-student sheet rows only, generate a synthetic `student_id` such as `blank:<sheet row hash>`, so the upsert matches on re-import. This keeps the current upsert but puts synthetic values in `student_id`.
4. Before a re-import, delete that sheet's previous rows (needs the sheet identity stored per row, as in option 2).

Recommended: option 2.
