# OMR patch "fix C" for the Python OMR service (omr-api)

This folder holds a candidate update for the live Python OMR,
`C:\Projects\OMR PYTHON DEPLOYMENT`, which runs as container `omr-api` and is
called by n8n at `http://omr-api:5000/process`. The live folder is never edited
from here.

| file | what it is |
|---|---|
| `patched/utlis.py`, `patched/mark_detect.py` | the two changed service files; they replace the deployed files of the same name |
| `omr_fix_c.diff` | the same change as a unified diff against the deployed original (CRLF; apply with `patch -p1 --binary`) |
| `regression_harness.py` | runs code versions side by side through the real Flask `/process` and scores them against labels |
| `n8n_compat_check.js` | feeds real `/process` responses into the current n8n reconciler Code nodes (see "n8n compatibility") |
| `history/` | fix B (the previous candidate, never deployed), kept for reference |

`api.py`, `grid_config.py`, `grid_detect.py` and `requirements.txt` do not change in fix C.

**Layout evidence (2026-10-01, separate from fix C, edited directly in `C:\Projects\OMR PYTHON DEPLOYMENT`).**
`api.py`, `grid_config.py` and `grid_detect.py` now add one response key, `layout`
(`grid_detect.layout_evidence()`): `columns_detected` (vertical rulings counted independently of the
grid), `column_boundaries_matched/_total`, `rows_detected` (20 + unexplained row lines inside a
section), `boundaries_supported/_total`, `grid_source` and `structure_fit` (`row_spacing`,
`units_per_section`, `body_units`, `units_pinned`, `score`, `score_per_boundary`). Nothing else in
the response changes and the rows read are identical (`resolveGrid()` / `readOmrRows()` keep their
old return values; `resolveGridWithInfo()` and `readOmrRows(..., return_layout=True)` are new).
Fix C's two files work unchanged on top. The n8n reconcilers use it for `layout_mismatch`
(`app/n8n/WEBSITE_BRANCH.md`, "Layout safety net"). Deploying it means copying those three files
too; an older service without `layout` is handled (no flag).
There are no new dependencies (only cv2/numpy, which are already used). The Flask API
contract is unchanged: same request fields, and the same response keys (`ratings`,
`omr_debug`, `table_found`, `grid_source`, `paper_type`, `ocr_fields`, ...).

## What fix C changes (all generic, no per-image rules)

Fix C contains fix B, plus:

1. **Speck filter with grouping** (`mark_detect.remove_specks`, from fix B). Ink pieces within
   5 px are grouped, and groups under 10 px are dropped. This removes scanner dust without
   removing faint dashed ticks.
2. **Table-corner repair** (`utlis._repair_table_quad`, from fix B). Squares up one corner
   that the contour dragged away from a rectangular table.
3. **Show-through / faint-ink filter** (`mark_detect.remove_faint`, new). Short-bond scans
   show the back side's printed text through the paper, and the adaptive threshold turned it
   into "ink" in every cell (0/16 short sheets were read fully correctly). An ink blob now
   survives only if its darkest decile reaches 0.4x the contrast of the sheet's own printed
   rulings. Because the threshold is relative to the rulings, exposure and phone shading
   cancel out. Kept blobs keep all their pixels, so real ticks score exactly as before. An
   earlier draft also dropped single faint pixels; that thinned every stroke and pushed the
   `omr_debug` scores of correct long-bond rows under the reconcilers' weak threshold (7
   correct long forms flagged `low_confidence_ocr`), so that step is off.
4. **Table-side snap** (`utlis._snap_table_quad`, new). Text written or printed close to the
   table border (the "Subject Taught" underline on short bond) merged into the dilated
   contour and dragged a corner by about 40 px, which sheared the warped table by one row. The
   fix fits each side to its straight printed ruling. A side moves only if it misses its
   ruling and the ruling is tilted against it. It is unchanged on 34/38 scans and 40/42 phone
   photos.
5. **Spill-over attribution** (`mark_detect.attribute_to_rows`, new). A tick drawn across a
   row ruling leaves a tail in the next row. After re-joining a stroke cut by ruling removal,
   rows holding under 25% of that stroke's largest per-row share are dropped. This removed a
   false mark on a blank row.
6. **Blank-row rescue** (`mark_detect.rescue_scores`, new). Only for rows that read blank:
   the whole band is counted, plus 0.35 row-heights into the neighbours, excluding the
   neighbour's own chosen column. The rescue never crosses a section edge. Rescued rows report
   their small rescue scores, so the n8n reconcilers still treat them as weak (`omr_debug`
   stays meaningful).

## Paper size

- **What the OMR does with it.** `paper_size` only selects the *fixed fallback grid*
  (`grid_config.FALLBACK_GRIDS`), which is used only when neither the structure fit nor the
  grid detector finds the rows. OCR regions do not use it.
- **Effect with fix C.** Every labelled scan and photo is read with `structure-fit`, so fix C
  gives identical results whether n8n sends LONG or SHORT. With the old code, short sheets
  sometimes hit the fallback grid, and a wrong paper size cost accuracy.
- **Detection (backend).** `app/backend/app/omr_prep.py` now detects the bond size of each
  scanner page. SHORT applies when the image, or its lowest ink, is at most 1.40x the width;
  otherwise LONG. Measured cut-offs: short sheets end at about 1.21-1.29x the width, long
  sheets at about 1.50x. Detection was right on 54/54 pages (22 long, 16 short, 16
  short-on-Legal).
- **Crop and send (backend).** The page is cropped to 11/8.5 or 13/8.5, never above the lowest
  ink, and `paper_size` is sent to n8n.
- **n8n.** The n8n "Website Prepare Scan" node uses a valid `body.paper_size` (SHORT/LONG) and
  falls back to LONG.
- **Phone path.** Unchanged: the Flutter gatherer sends its paper tag (default short bond).

## Results (through the real `/process`; scanner sets through the real backend `prepare_page`)

Labels:
- scanner: `omr_ground_truth_2026-09-27.json` (22 long + 16 short forms);
- phone: `omr_ground_truth_2026-09-05.json` (42 short photos).

Settings: scanner pages capped to 2200 px like the folder watcher; website `paper_size` from
the backend. REF is the "last man standing" project; its OMR files are byte-identical to the
deployed original, and only `api.py` adds a `document_check`.

| set (rows) | metric | ORIGINAL (deployed) | fix B | REF (last man standing) | **fix C (this)** |
|---|---|---|---|---|---|
| scanner LONG, 22 forms (440) | rows correct | 405 (92.0%) | 426 (96.8%) | 405 (92.0%) | **427 (97.0%)** |
| | tables found / false marks on blank rows / misses | 21 / 10 / 8 | 22 / 2 / 1 | 21 / 10 / 8 | **22 / 1 / 1** |
| | forms fully correct | 15 | 18 | 15 | **19** |
| scanner SHORT, 16 forms (320) | rows correct | 216 (67.5%) | 232 (72.5%) | 216 (67.5%) | **317 (99.1%)** |
| | tables found / false marks / misses | 13 / 1 / 33 | 16 / 1 / 3 | 13 / 1 / 33 | **16 / 1 / 1** |
| | forms fully correct | 0 | 0 | 0 | **13** |
| short sheets on a Legal flatbed (320) | rows correct | 218 (68.1%) | 240 (75.0%) | 218 (68.1%) | **318 (99.4%)** |
| | forms fully correct | 1 | 1 | 1 | **14** |
| phone, 42 photos (840) | rows correct | 823 (98.0%) | 823 (98.0%) | 823 (98.0%) | **825 (98.2%)** |
| | false marks / misses / forms fully correct | 0 / 2 / 35 | 0 / 2 / 35 | 0 / 2 / 35 | **0 / 0 / 36** |

- **Paper size.** With the old hard-coded `paper_size: LONG`, fix C scores the same (427 / 317 /
  318). ORIGINAL and fix B score 202 and 200 on SHORT with LONG, because their fallback grid is
  wrong.
- **Old backend.** Behind the old backend (long crop, no `paper_size`), fix C scores 317 on
  short-on-Legal.
- **Regressions.** No form is worse than ORIGINAL on any set.
- **Old labels.** On the earlier scanner labels (`scanner_labels.csv`), fix B was 438/440 and
  fix C is 439/440. The new ground truth changes 14 long-form rows, 11 of them on FORMS-07 (see
  below).
- **Weak-row signal (`omr_debug`).** Correct rows with top score < 0.05 or margin < 0.02:
  - long: 12 (ORIGINAL 12);
  - short: 20 (ORIGINAL 49);
  - phone: 89 (ORIGINAL 89).

  Median tick score: 0.077-0.080, the same as ORIGINAL.
- **Stability.** Every parameter was swept (search 0.05-0.14, support 0.5-0.75, relative
  support 0.75-0.95, ruling prominence 14-30, move tolerance 0.008-0.016, FAINT_REL 0.35-0.5,
  FAINT_GROUP 0-2, LEAK 0.25-0.35, BRIDGE 4-8, RESCUE_Z 0.25-0.5, RESCUE_MIN 0.004-0.008).
  - Inside those ranges: long 426-427, short 316-317, phone 825-826.
  - Split-half hold-out (even/odd forms of every set): choosing each parameter on one half
    picks the shipped default, which scores the same on the other half.
  - The one cliff is move tolerance 0.02 (short 300). That tolerance is as large as the
    ~40 px corner drag it must detect.

Known remaining errors, with the same reads in ORIGINAL and fix B unless noted:

- **Probable label errors in the ground truth** (please re-check):
  - FORMS-07: 11 rows. Every version reads the visible ticks, and the older labels agree
    with the OMR. The new labels are shifted by +2, -1 and +1 columns.
  - Phone SCAN-1786427944885, p1-p10: the labels are shifted one column against the visible
    ticks, while m1-m10 on the same sheet read correctly.

  Without these two sheets: long 418/420, phone 815/820.
- **FORMS-09 m6:** a tick straddles the m6/m7 ruling almost 50/50. It reads S on the blank
  m6. Weak score (0.020), so the website flagged it `python_weak_gemini_blank` (limit 1 row at
  the time; since 2026-10-01 the limit is 3 rows, so a single speck row alone no longer flags).
- **FORMS-10 m5:** a tick on the F/US column boundary reads F; the label is US.
- **img…11052100 p6:** a tick in VS plus a scribble in S; S is darker and wins. This is the
  one scanner error the website reconciler would store unflagged: Gemini disagrees by only 1
  and the row is not weak.
- **img…11073659 m3:** the tick over the m3/m4 ruling (the user's note) also reads VS on the
  blank m3. Weak (0.023), so it is flagged.
- **img…11094318 p4:** a faint dash at the p4/p5 ruling reads blank (0.002). Gemini fills
  it. Only on the non-Legal copy.
- **Phone:** 4 single-row wrong-column reads (the same in ORIGINAL). The website/phone
  reconcilers catch 3 of the 5 wrong phone forms; the others differ by one column.

## n8n compatibility (current `app/n8n/perfect-2.1.json`)

Re-run on 2026-09-27 after the reconciler redesign. Both reconcilers now take ratings from Python, use Gemini only as a cross-check (`omr_agreement_below_30` when fewer than 6 of 20 rows agree, `AGREEMENT_MIN = 0.30`), and take text only from Gemini. `n8n_compat_check.js` ran every saved `/process` response of fix C (`NEW`, deployed) and ORIGINAL through the real jsCode of both reconcilers:
- phone: `reconcile ocr and omr1`;
- scanner: `Website Reconcile OCR OMR`.

Gemini was mocked with the ground-truth letters. Results:

> **2026-10-01 rule change, not re-measured.** The reconcilers now trust a confident (not weak) Python row over Gemini (`resolved_by: 'python_confident'`), ignore differences of one level (`adjacent_level`), and the website `python_weak_*` limits went from 1 to 3 rows. See `app/n8n/WEBSITE_BRANCH.md` "OMR and OCR rules". The numbers below were taken before that change; with a ground-truth Gemini the agreement flag can only get rarer, and single weak/speck rows on the website no longer flag on their own.

- **Parsing.** `ratings`, `omr_debug` (weak-row counts), `table_found`, `grid_source` and `python_raw_ratings` were parsed correctly for all 96 forms. `agreement_rows` equals an independent count for every form. With Gemini's text blank, the instructor stays blank: there is no `ocr_fields` fallback any more.
- **Fully correct forms**, by set:
  - long: 19, 0 spurious flags;
  - short: 13, 0 spurious flags;
  - short-on-Legal: 14, 1 flag (`low_confidence_ocr_4_rows`), which ORIGINAL raises too;
  - phone: 36, 6 flags (`low_confidence_ocr_N_rows`), all also raised by ORIGINAL.
- **Forms with OMR errors that were flagged:**
  - long: 3/3;
  - short: 1/3;
  - short-on-Legal: 1/2;
  - phone: 4/6, up from 3/6. Wrong rows stored unflagged on phone dropped from 12 to 2: the agreement rule flags SCAN-1786427944885, whose 10 p-rows differ from the label by one column; the old count-based rule (difference ≥ 2) missed it. That form's p-row labels are a suspected label error, so with a real Gemini this may not change anything.
- Single wrong rows on a strong (not weak) Python row still pass at 80%: 19/20 agree. This affects short img20260927_11052100 (p6) and phone SCAN-1782996373456 (m7) and SCAN-1787269944723 (p10).

**Noisy Gemini** (`--noise 0.02,0.05,0.10 --trials 200`). Gemini is mocked as the ground truth plus random misreads per row: 70% a neighbouring column, 15% blank, 15% another column. The table shows the share of fully correct forms that the agreement rule alone would flag:

| per-row Gemini error | AGREEMENT_MIN 0.80 | 0.90 | 0.96 (all 20 must match) |
|---|---|---|---|
| 2% | 0.0% | 0.5–1.2% | 33–41% |
| 5% | 0.1–0.5% | 7–10% | 62–67% |
| 10% | 2–6% | 32–36% | 88–90% |

The share of forms with OMR errors that are flagged for ANY reason stays high at 0.80: long 99%, phone 67–70%, short 35–49%. That is because of the kept Python-confidence rules (`python_weak_*`, `low_confidence_ocr`).

## Deploy

Order: OMR first, then backend, then n8n. Each step is backward compatible, so a
partial deploy never breaks anything. Fix C does not need the backend or n8n steps to
reach its accuracy; they only make `paper_size` correct.

### 1. OMR service (container `omr-api`): copy exactly these two files, nothing else

| copy this file | to (in the service's code folder, next to `api.py`) |
|---|---|
| `app\omr_patch\patched\utlis.py` | `utlis.py` (replace) |
| `app\omr_patch\patched\mark_detect.py` | `mark_detect.py` (replace) |

```sh
# find the code folder inside the container (the folder that holds api.py)
docker exec omr-api sh -c 'ls "$(pwd)"; find / -name api.py -path "*" -not -path "*/site-packages/*" 2>/dev/null | head'
# back up the live files (rollback copies), then copy the new ones in
docker cp omr-api:/APP_DIR/utlis.py ./utlis.py.bak
docker cp omr-api:/APP_DIR/mark_detect.py ./mark_detect.py.bak
docker cp "app/omr_patch/patched/utlis.py"       omr-api:/APP_DIR/utlis.py
docker cp "app/omr_patch/patched/mark_detect.py" omr-api:/APP_DIR/mark_detect.py
docker restart omr-api
```

Replace `/APP_DIR` with the folder found in the first command. Files copied with
`docker cp` are lost when the container is re-created from its image. If the image is
built from `C:\Projects\OMR PYTHON DEPLOYMENT`, copy the same two files there too and
rebuild the image, keeping a backup of the originals.

**Smoke test after the restart:**

```sh
docker logs --tail 50 omr-api          # no traceback; the Flask server is listening on 5000
# from the host (or any container on the n8n network), with any real form photo/scan:
curl -s -F "image=@form.jpg" -F "paper_size=SHORT" -F "task_id=smoke" http://localhost:5000/process \
  | python -c "import sys,json; j=json.load(sys.stdin); print(j['table_found'], j['grid_source'], len(j['omr_debug']), [c['rating_name'] for c in j['ratings']['management']])"
```

Expected: `True structure-fit 20 [...]` with the ticks you see on the form. Then submit
one phone scan and one website batch page, and check that the n8n executions reach
"reconcile ocr and omr1" / "Website Reconcile OCR OMR" without new `review_reasons`.

### 2. Backend (only for paper-size detection)

- **Changed files:** `app/backend/app/omr_prep.py` and `app/backend/app/logic.py`.
  `tests/test_omr_prep.py` gains tests.
- **Redeploy:** redeploy the backend the usual way (rebuild the Docker image, or push to the
  platform), then restart it.
- **Config:** no new environment variables.
- **Check:** after the restart, the batch item's `preprocess` shows `shortbond_crop+...` or
  `grey_pad_5` for short sheets.

### 3. n8n (only for paper-size pass-through)

Re-import `app/n8n/perfect-2.1.json` over the live "perfect 2.1" workflow
(id `vOMBjDtz15QB4k4u`), or paste `app/n8n/tools/website_src/prepare_scan.js` into
"Website Prepare Scan". See `app/n8n/WEBSITE_BRANCH.md`. Earlier fixes in that file
also still wait for a re-import.

## Rollback

- **OMR:** copy the two `.bak` files back (or the originals from
  `C:\Projects\OMR PYTHON DEPLOYMENT`) with `docker cp`, then run `docker restart omr-api`.
  Nothing else depends on fix C: the response shape is the same.
- **Backend:** redeploy the previous build. The OMR then receives the long crop and LONG
  again, and fix C reads those exactly as well (see the table).
- **n8n:** a missing `paper_size` already means LONG.

## Re-running the checks

```sh
# Python env with opencv, numpy, flask, Pillow (EasyOCR optional)
GT=C:/Users/rodzh/Downloads/omr_ground_truth_2026-09-27.json
PGT="C:/Projects/last-man staning original omer and ocr/omr_ground_truth_2026-09-05.json"
BE="C:/Projects/shisss complee new website/app/backend"
python regression_harness.py --images C:/FORMS --labels $GT --only "^FORMS" --scanner-prep "$BE" \
    --website-paper-size backend --downscale 2200 --details --save long.json
python regression_harness.py --images C:/FORMS/short --labels $GT --only "^img" --scanner-prep "$BE" \
    --website-paper-size backend --downscale 2200 --details --save short.json
python regression_harness.py --images "C:/Projects/Forms via phone" --labels "$PGT" --details --save phone.json
#   add --legal-bed to simulate short sheets on a Legal flatbed;
#   add --version FIXB=history/fix_b --version "REF=C:/Projects/last-man staning original omer and ocr"
node n8n_compat_check.js --results long=long.json --results short=short.json --results phone=phone.json \
    --labels $GT --labels "$PGT" --version PATCHED --compare ORIGINAL
#   add --noise 0.02,0.05,0.10 --trials 200 to simulate an imperfect Gemini (agreement flag rate at 0.80/0.90/0.96)
```

The default harness versions are ORIGINAL (the deployed folder) and PATCHED (`./patched`).
Keep label files and saved results outside the project: they refer to real student forms.
The saved JSON holds no images or OCR text.
