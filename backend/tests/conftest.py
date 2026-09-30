import os

# Tests never call n8n; satisfy the startup check (N8N secret required when a
# webhook URL is configured) without touching the real .env.
os.environ.setdefault("N8N_WEBHOOK_SECRET", "test-secret")
