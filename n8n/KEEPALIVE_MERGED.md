The backend keep-alive (Every 10 minutes -> Ping backend -> Status not 200? -> Record failure) now lives inside perfect-2.1.json.
Do NOT import keepalive-backend.json (kept for reference only); importing it causes double pings.
