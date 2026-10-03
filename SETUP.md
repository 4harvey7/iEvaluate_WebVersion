# Setup Guide

Nothing here renames or drops anything the Flutter app uses. Migrations are
additive and are run **manually** by you.

## 0. Rotate the Supabase secret key (do this first)

The Supabase secret (service-role) key was exposed in an n8n workflow
export. Treat it as compromised:

1. Supabase Dashboard -> Settings -> API Keys -> create a new **secret key**
   (`sb_secret_...`), then delete/revoke the exposed one.
2. Put the new key in `backend/.env` (`SUPABASE_SERVICE_KEY`) and on your
   backend host, and in n8n credentials if n8n uses it.
3. Don't paste keys into workflow exports, tickets or chat. Never put it in
   the frontend.

## 1. Apply migration 002

`migrations/001_batch_scanning.sql` is already applied. Open
`migrations/002_batch_hardening.sql`, read it, and run it in the Supabase SQL
Editor. It:

- adds `stations.key_hash / key_created_at / last_seen_at / watcher_version`
- adds `scan_batches.rejected_count / duplicate_count / last_scan_at`
- creates `scan_batch_items` (idempotency per `(batch_id, file_sha256)`, RLS on, backend-only)
- creates `increment_batch_counter`, `claim_batch_item`, `complete_batch_item`
  (backend-only; execute revoked from anon/authenticated)
- replaces `search_instructors` (same signature) so it returns only
  instructors (FULL-TIME / PART-TIME / DEPARTMENT_HEAD) and placeholders
- **locks down `stations` and `scan_batches`**: drops the permissive
  "any authenticated user" policies from 001 and revokes anon/authenticated
  access (signup is open, so any self-registered user could otherwise edit
  batches or station keys). Only the backend's service role uses them.

Until 002 is applied, the backend answers `503 schema_outdated`.

Other limits: request bodies are capped (17 MB on `/scans/ingest`, 1 MB elsewhere) before
parsing. Images over 40 megapixels are rejected. n8n timeouts and network errors
are retried without limit and don't count toward `MAX_PIPELINE_ATTEMPTS`. An n8n
401/403 (wrong secret) returns 503 and doesn't count either.

A commented-out, optional block at the end adds a unique index on
`sast_all_raw_data_survey(task_id)`. That table is shared with the Flutter
app, so run the duplicate check shown there before enabling it.

Existing stations have no key after 002. For each station open **Scanner
setup -> Download installer** and run INSTALL on its laptop (see step 5).

## 1b. Apply migration 004 (watcher folder status)

`migrations/004_watcher_folder_status.sql` adds `stations.watcher_folder`,
`watcher_folder_exists`, `watcher_folder_error`, `watcher_queued_files` and
`watcher_last_file_at`, which watcher 2.3+ reports in its heartbeat. Review
it and run it in the Supabase SQL Editor. Until it is applied the backend
keeps working (it logs a warning, stores only `last_seen_at` /
`watcher_version`, and the website cannot show "Folder not found" /
"That folder isn't allowed" or the last page time). It is picked up within
about 5 minutes of applying, no restart needed.

## 1c. Apply migration 005 (review page)

`migrations/005_scan_review_queue.sql` makes the website review page
(`/batch/<id>/review`) editable. Review it and run it in the Supabase SQL
Editor (it is safe to run again). It only **adds** things: on
`failed_scan_queue` and `import_errors` the columns `updated_at`,
`review_version`, `review_state`, `review_error`, `review_submitted_at`,
`review_claimed_by`, `review_claimed_at` (plus `review_action` and
`review_correction` on `failed_scan_queue`), three indexes, and a
`BEFORE UPDATE` trigger on each table that sets `updated_at` and bumps
`review_version` whenever the status or content changes. No RLS policy,
Realtime publication, rename or drop; the Flutter app and n8n keep working
unchanged (they never need the new columns).

Until it is applied the review page still lists the forms (read-only, always
reloaded in full) and every action answers `503 migration_required`. The
backend notices it within about 5 minutes, no restart needed.

Crediting forms from the review page uses the n8n correction webhooks. For
website approvals to be credited to the **batch's** term and to use the
instructor/subject **ids** the reviewer picked, re-import the updated n8n
workflow (`n8n/perfect-101-website.json`, see `n8n/WEBSITE_BRANCH.md`). With
an older workflow the correction still works, but n8n finds the instructor by
name and uses the current term.

## 1d. Apply migration 012 (same image in another batch)

`migrations/012_scan_items_sha_index.sql` adds one index,
`scan_batch_items (file_sha256)`. Before calling n8n the backend now looks for
the same image file in another batch (success / flagged / duplicate), and n8n
flags such a page `reused_image` for review instead of saving it again. Review
it and run it in the Supabase SQL Editor after 011 (safe to run again). Until
it is applied the lookup still works, only slower; a failed lookup never fails
a scan. The flag itself needs the re-imported n8n workflow
(`n8n/perfect-101-website.json`).

## 2. Backend environment

Copy `backend/.env.example` to `backend/.env` (or set these on your host):

| Variable | Required | Meaning |
|---|---|---|
| `SUPABASE_URL` | yes | `https://<project-ref>.supabase.co` |
| `SUPABASE_SERVICE_KEY` | yes | Supabase **secret** key (rotated, see step 0). Backend only. |
| `N8N_BATCH_WEBHOOK_URL` | for real scoring | n8n batch webhook. Only the backend knows it. |
| `N8N_WEBHOOK_SECRET` | **yes if a webhook URL is set** (the backend refuses to start without it) | Sent as `X-Webhook-Secret`; check it in the n8n workflow (e.g. an IF node on the header) and reject mismatches. |
| `SIMULATE_SCANS` | no (default `false`) | `true` + no webhook URL = simulated results with a SIMULATION badge. Never in production. |
| `ABANDONED_BATCH_HOURS` | no (default `3`) | Hours without activity before any staff member can force-close a batch. |
| `CORS_ORIGINS` | yes in prod | Comma-separated exact frontend origins, e.g. `https://your-app.vercel.app`. |
| `CORS_ALLOW_LAN` | no (default `false`) | Dev only: also allow private-LAN origins. |
| `MAX_PIPELINE_ATTEMPTS` | no (default `5`) | Failed n8n tries per page (reply `internal_error_not_saved`, HTTP error or unusable reply) before the page is marked "failed after retries" and the watcher moves it to `skipped/`. Timeouts and unreachable n8n are retried without limit. |
| `FORWARDED_ALLOW_IPS` | behind a proxy | Used by the Dockerfile/Procfile (`uvicorn --proxy-headers --forwarded-allow-ips`). Set it to your platform's proxy IPs so login rate limiting sees real client IPs. Use `*` only if the app is reachable exclusively through that proxy. Default `127.0.0.1`. |
| `PAD_FOR_OMR`, `PAD_PERCENT`, `CROP_LONG_BOND` | no (defaults `true`, `5`, `true`) | OMR preprocessing for scanner pages. Detects short (8.5x11) or long (8.5x13) bond and crops the scan to that sheet (`CROP_LONG_BOND` keeps its old name), measured from the top and never above the lowest ink; the size is sent to n8n as `paper_size`. Then adds a mid-grey border of `PAD_PERCENT`% of the width so the OMR's paper detection finds the sheet edge. Scanner/website pages only. |
| `WATCHER_API_BASE` | recommended in prod | Public URL of this backend (same as the frontend's `VITE_API_BASE`), written into the installer's `watcher_config.json`. Empty = the URL the request came in on (can be wrong behind a proxy). |
| `N8N_CORRECTION_WEBHOOK_URL` | no | Webhook the review page uses to credit a flagged form (the Flutter app's `sast-manual-correction`). Default: `N8N_BATCH_WEBHOOK_URL` with its last part `sast-batch-scan` replaced by `sast-manual-correction`. Empty (and no default) = the review page can't credit forms (`503 correction_not_configured`). Sent with `X-Webhook-Secret`. |
| `N8N_IMPORT_ERROR_WEBHOOK_URL` | no | Webhook for fixing a website import error (the Flutter app's `import-error-correction`). Default: same derivation, `import-error-correction`. |
| `N8N_CORRECTION_TIMEOUT_SECONDS` | no (default `180`) | How long the backend waits for a correction webhook. After a timeout the form shows "Sending..." and is settled when n8n's own update is seen, or marked "Not sent" after 10 minutes. |
| `SCAN_IMAGE_BUCKET` | no (default `scan-pages`) | Page viewer (migration 010): PRIVATE Supabase Storage bucket where the backend keeps a viewing copy of every scanned page (long side `SCAN_IMAGE_MAX_SIDE`=1800 px, JPEG quality `SCAN_IMAGE_JPEG_QUALITY`=75, about 250 KB per page, 250 MB per 1,000 pages). Uploaded after marking answers, in the background, with the service key; staff see it only through the backend (login + batch access). Empty = keep no pictures. |
| `SCAN_IMAGE_RETENTION_DAYS` | no (default `90`) | Pictures older than this are removed from the bucket (checked at most once an hour, started by the batch page's poll). `0` = keep them. The score stays on the page's row. |
| `BREVO_API_KEY`, `BREVO_SENDER_EMAIL` | recommended | Brevo API key and sender for the instructor activation email (the same values the Flutter app's Supabase functions use). Empty = activation still works, but no email is sent and the Activate page shows the sign-in steps for the admin to pass on. A warning is logged at start-up. |
| `BREVO_SENDER_NAME` | no (default `SAO Management`) | Sender name on that email. |
| `N8N_TIMEOUT_SECONDS`, `MAX_UPLOAD_MB`, `PREDATE_GRACE_SECONDS`, `WATCHER_ONLINE_SECONDS` | no | Tuning, see `.env.example`. |

## 3. Run locally

```
# backend
cd backend
python -m venv venv
venv\Scripts\python -m pip install -r requirements.txt
venv\Scripts\python -m uvicorn app.main:app --reload --port 8000

# frontend (second terminal)
cd frontend
npm install
npm run dev
```

`VITE_API_BASE` defaults to `http://localhost:8000`.

## 4. Deploy

### Deploy to Render + Vercel

1. **Backend (Render):** New -> Blueprint -> pick this repo. `render.yaml` creates the `ievaluate-api` Docker service (root `backend/`, health check `/health`). Fill in the prompted values (SUPABASE_URL, SUPABASE_SERVICE_KEY, N8N_BATCH_WEBHOOK_URL, N8N_WEBHOOK_SECRET, CORS_ORIGINS, WATCHER_API_BASE, BREVO_API_KEY, BREVO_SENDER_EMAIL). Use the service's https onrender.com URL for WATCHER_API_BASE.
2. **Frontend (Vercel):** import the repo, Root Directory `frontend`, env var `VITE_API_BASE=https://<render-url>`. The build fails unless it is https, and the Content-Security-Policy is generated from it.
3. Set CORS_ORIGINS on Render to the Vercel URL (no trailing slash) and redeploy.
4. Check `https://<render-url>/health` returns `{"status":"ok"}`.

### Other hosts

- **Backend:** any Docker host (Railway, Render, Fly). Root directory
  `backend/`, which has the Dockerfile. Set the env vars from step 2. Check
  `<backend-url>/health` returns `{"status":"ok"}`.
- **Frontend (Vercel):** Root Directory `frontend`, env var
  `VITE_API_BASE=<backend-url>`. `vercel.json` handles SPA routes.
- Then set `CORS_ORIGINS` on the backend to the Vercel URL and redeploy.

## 5. Install the watcher on each scanning laptop

1. On the laptop, log in -> pick or register the station -> **Scanner setup
   -> Download installer**. The zip (`ScanWatcher-<station>.zip`) is the
   watcher package plus a ready `watcher_config.json`. Each download makes a
   **new station key**; the previous laptop for that station stops working.
2. Extract All, open the `ScanWatcher` folder, double-click `INSTALL`. It
   finds or installs Python, installs the requirements, copies everything to
   `%LOCALAPPDATA%\ScanWatcher`, creates `C:\Scans`, sets up auto-start and
   starts the watcher. The window turns green and says "ALL DONE".
3. The Scanner setup page light turns green ("Online"). Point the scanner
   software at `C:\Scans` (or change the folder on the website; the laptop
   switches within ~30 seconds).

`CHECK.bat` shows a plain-words status report; `UNINSTALL.bat` removes it
(scans are kept). End-user steps: `backend/watcher/HOW TO INSTALL.txt`; IT
details and the manual install: `backend/watcher/TECHNICAL NOTES.txt` (both
also inside the zip). The package must be rebuilt after editing anything in
`backend/watcher/` (see README, "Rebuilding the watcher zip").

## Accounts

- Only approved `SAO_STAFF` / `SAO_ADMIN` accounts (via `Sao_users`) can log
  in. The `Activate instructor` page is limited to `SAO_ADMIN`.
- Placeholder instructors created during batch setup are disabled, banned
  auth users (`account_status = 'pending'`, `is_placeholder = true`) with a
  `department_table` row (FULL-TIME or PART-TIME). An admin activates them
  with the instructor's real email. That unbans the account, sets it
  approved. The admin types the email twice and confirms it before
  activating. The backend then emails the instructor (Brevo, see
  `BREVO_API_KEY`) how to set a password with "Forgot Password?" in the
  iEvaluate phone app, using that email. Supabase's own password-reset mail
  is never sent. If the email can't be sent (Brevo not configured or an
  error), the activation still stands and the page shows the steps for the
  admin to pass on in person.
