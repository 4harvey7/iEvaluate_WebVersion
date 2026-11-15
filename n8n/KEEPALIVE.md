# Backend keep-alive (Render free plan)

Workflow `keepalive-backend.json` pings `GET {BACKEND_URL}/health` every 10 minutes
so the free Render service does not sleep (it sleeps after ~15 min idle).

1. n8n: Workflows > Import from file > `n8n/keepalive-backend.json`.
2. On the n8n server set the environment variable `BACKEND_URL` to the Render
   HTTPS URL (no trailing slash), then restart n8n.
3. Open the workflow and toggle it Active.
4. Verify: Executions list shows a run every 10 min; "Ping backend" output has
   `statusCode: 200`. A run that reaches "Record failure" means no 200.

Notes
- Render free gives ~750 instance-hours/month: one always-on service fits.
- 10 min is below the 15 min sleep limit.
- The free plan has no uptime guarantee.
- A cold start after a deploy/restart can still take ~1 min.
- If the n8n VPS is down, the backend will sleep again.

Render env vars (names only, from `backend/.env.example` and the code):
SUPABASE_URL, SUPABASE_SERVICE_KEY, SIMULATE_SCANS, ABANDONED_BATCH_HOURS,
CORS_ORIGINS, CORS_ALLOW_LAN, PUBLIC_API_BASE, SCAN_IMAGE_BUCKET, WATCHER_API_BASE.

The watcher must point to the Render HTTPS URL.
