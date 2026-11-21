import os

import pytest

# Tests never call n8n; satisfy the startup check (N8N secret of 32+ chars
# required when a webhook URL is configured) without touching the real .env.
os.environ.setdefault("N8N_WEBHOOK_SECRET", "test-secret-not-real-0123456789abcdef")


@pytest.fixture(autouse=True)
def _station_keys_known(monkeypatch):
    """body_limit looks the X-Station-Key up before buffering an ingest body;
    tests have no database, so every key passes that early check (require_station,
    usually overridden, still decides). Tests of the early check patch it back."""
    from app import security
    monkeypatch.setattr(security, "station_key_known", lambda key: True)
