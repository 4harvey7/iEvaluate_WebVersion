# iEvaluate Batch Scanning

A scanning laptop connected to a physical scanner: SAO staff log in, open a
batch (instructor + subject), the watcher on the laptop sends each scanned
page to the backend, the backend forwards it to n8n for OMR/OCR scoring and
the live page shows results.

**Setup: see [`SETUP.md`](./SETUP.md).**

## Layout

```
migrations/001_batch_scanning.sql   # already applied
migrations/002_batch_hardening.sql  # station keys, scan items, atomic counters (run manually)
migrations/003_repair_import_error_scan_images.sql
migrations/004_watcher_folder_status.sql  # watcher folder status columns (run manually; optional)
migrations/005_scan_review_queue.sql      # review page: versions + live cursor on failed_scan_queue / import_errors (run manually)
migrations/010_scan_page_images.sql       # page viewer: score + picture of every page, private bucket scan-pages (run manually, after 008/009)
backend/                            # FastAPI (service-role Supabase client)
  app/main.py                       # app, CORS, error handling
  app/security.py                   # bearer-token + station-key auth, rate limiting
  app/logic.py                      # pure helpers (task ids, n8n validation, images)
  app/review_logic.py               # pure helpers for the review page (row shapes, checks, correction payloads)
  app/page_scores.py, page_images.py  # page viewer: score summary per page, viewing copy in the private bucket, retention (SCAN_IMAGE_RETENTION_DAYS, default 90)
  app/routers/                      # auth, stations, instructors, subjects, departments, batches, scans, reviews, watcher
  tests/                            # pytest (no DB writes)
  watcher/                          # runs on EACH scanning laptop (served as a zip)
frontend/                           # React 18 + Vite
```

## How it fits together

- **Staff auth.** `POST /auth/login` signs in with Supabase password auth and
  only succeeds for accounts with `user_info.account_status = 'approved'`,
  `disabled` not true, and a `Sao_users` role of `SAO_STAFF` or `SAO_ADMIN`
  (admin = `SAO_ADMIN`; the `is_admin` column is ignored). Every endpoint
  except `/health` and the watcher endpoints needs
  `Authorization: Bearer <access_token>`; staff id and role are derived on
  the server. Tokens live in `sessionStorage` (closing the browser logs out)
  and are refreshed through `POST /auth/refresh`.
- **Stations / watcher keys.** Registering a station (or "Regenerate key")
  creates a random key; only its SHA-256 is stored (`stations.key_hash`). The
  plain key is shown once and goes into the laptop's `watcher_config.json`
  ("Download installer", `POST /stations/{id}/installer`, returns the watcher
  package zip with that file inside and rotates the key). The watcher authenticates with `X-Station-Key`.
- **Ingest.** `POST /scans/ingest` (watcher) validates the image (JPEG/PNG,
  <= 12 MB, sha256 must match), requires an open batch on the station, rejects
  files older than the batch (`409 file_predates_batch`, watcher moves them to
  `skipped/`), de-duplicates on `(batch_id, file_sha256)` in
  `scan_batch_items`, and calls n8n.
- **n8n contract.** `POST N8N_BATCH_WEBHOOK_URL` with header
  `X-Webhook-Secret: N8N_WEBHOOK_SECRET` and JSON
  `{batch_id, instructor_id, subject_id, term_id, station_id, staff_id, filename, file_sha256, task_id: "web:<batch_id>:<sha256[:16]>", image (base64 JPEG), mime_type: "image/jpeg"}`.
  Expected 200 reply:
  `{status: success|flagged|rejected|duplicate, flagged, task_id, review_reasons: [], identity_check: null, message}`.
  (`identity_check` is null because the batch's instructor/subject are authoritative and the form's text is not validated;
  an object `{instructor, subject: match|unreadable|mismatch|unverified, ocr_instructor, ocr_subject}` from older n8n builds is still accepted.)
  The backend waits `N8N_TIMEOUT_SECONDS` (default 360 s) for the reply, which covers n8n's worst case (OMR + 3 Gemini tries).
  Anything else counts as an error (item marked `error`, watcher retries).
  The n8n URL never reaches the browser or the watcher.
- **Orientation.** Every page is turned upright + portrait on the server
  (`app/orientation.py`) before n8n: EXIF transpose, landscape->portrait,
  then upright vs upside-down from the form's own structure (rating columns on
  the right, ruled table in the lower part of the page). Unsure pages are only
  made portrait and flagged `orientation_uncertain`. n8n also receives
  optional `orientation_applied` (clockwise degrees) and
  `orientation_confidence`.
- **OMR preprocessing.** Flatbed scans have no background around the paper, so
  the OMR mistook the table border for the page (0% table detection). After
  orientation, scanner pages are classified as short bond (8.5x11: image or
  content bottom within 1.40x the width) or long bond (8.5x13) and cropped to
  that proportion (measured from the top, never cutting ink). Then a
  5% mid-grey (128) border is added (`app/omr_prep.py`; `PAD_FOR_OMR`,
  `PAD_PERCENT`, `CROP_LONG_BOND`). n8n receives `preprocess`, e.g.
  `"longbond_crop+grey_pad_5"` / `"shortbond_crop+grey_pad_5"`, and
  `paper_size` (`SHORT`/`LONG`), which n8n passes to the OMR (default `LONG`). On the 22 sample scans the OMR found the table
  on 22/22 (full resolution) and 21/22 (watcher 2200 px JPEGs), versus 0/22 before.
- **Counters** are updated atomically by the `complete_batch_item` SQL
  function (migration 002).
- **Review page** (`/batch/<id>/review`, linked from "Needs a look" on the
  live page and from the menu while a batch is open). Two lists for the
  batch: **Needs review** (its `failed_scan_queue` rows, including forms whose
  sheet names another instructor/subject, with n8n's suggestion pre-filled)
  and **Import errors** (its website `import_errors` rows, `task_id` =
  `web:<batch_id>:...`; Google Sheet errors stay in the Flutter app). Staff
  open a form next to its scan, fix instructor / subject / student ID /
  remarks / ratings and credit or discard it, while pages are still arriving.
  - Live = polling the backend (`GET /reviews/batch/{id}?since=<cursor>`,
    every 3 s while visible, 10 s hidden, full reload every 60 s). New forms
    wait behind an "N new" button so the list never jumps; edits are drafts
    in `sessionStorage` that polling never touches.
  - Every edit sends the `review_version` it was made on (migration 005 bumps
    it on any content/status change, from the website, Flutter or n8n). An
    outdated edit gets `409 stale_row` and the page shows "Changed
    elsewhere"; a form already finished elsewhere gets `409 already_resolved`.
  - Crediting calls the existing n8n correction webhooks the Flutter app uses
    (`sast-manual-correction`, `import-error-correction`) with
    `correction_source: "website_review"`. Before it is sent the backend checks
    that the instructor can be credited (same rule as n8n), that the subject
    exists, and that it would not replace another stored form of the same
    student (`409 would_overwrite` unless the reviewer confirms). If n8n times
    out the form stays "Sending..." and is settled on a later poll (or marked
    "Not sent" after 10 minutes). An HTTP error marks it "Not sent" (`502`).
  - Access: SAO admins everything; SAO staff their own rows, batches they
    started, and the batch of the laptop they are logged in at.
  - Before migration 005 the lists still load (read-only) and actions answer
    `503 migration_required`.

## Rebuilding the watcher zip

After editing anything in `backend/watcher/`:

```
cd backend
venv\Scripts\python watcher\build_package.py
```

This rewrites `backend/app/static/watcher-package.zip` (no secrets inside;
never add a `watcher_config.json` to it).

## Tests

```
cd backend
venv\Scripts\python -m pip install -r requirements-dev.txt
venv\Scripts\python -m pytest -q
```

## Not built here

- The n8n batch branch itself (see the contract above).
- The correction logic itself: the review page reuses the n8n correction
  webhooks (the same ones the Flutter correction screens use).
