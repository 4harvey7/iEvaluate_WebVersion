import ipaddress
import os
import uuid
from functools import lru_cache
from urllib.parse import urlsplit

import httpx
from dotenv import load_dotenv
from supabase import create_client, Client, ClientOptions

load_dotenv()


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("true", "1", "yes", "on")


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


class Settings:
    def __init__(self) -> None:
        self.supabase_url: str = os.environ["SUPABASE_URL"]
        self.supabase_service_key: str = os.environ["SUPABASE_SERVICE_KEY"]
        # Only the backend ever knows the n8n URL. Never echo it to clients.
        self.n8n_batch_webhook_url: str = os.environ.get("N8N_BATCH_WEBHOOK_URL", "").strip()
        self.n8n_webhook_secret: str = os.environ.get("N8N_WEBHOOK_SECRET", "").strip()
        # Must exceed n8n's worst case for one page: Python OMR (60 s timeout) + Gemini
        # (3 tries x 90 s + 2 x 5 s waits) + database work = ~345 s. The item stays 'processing'
        # for this long + 60 s, so a resend meanwhile gets 409 in_progress, never a second run.
        self.n8n_timeout_seconds: float = _float_env("N8N_TIMEOUT_SECONDS", 360.0)
        # Review page corrections (routers/reviews.py). Default: the batch webhook URL
        # with its last path part 'sast-batch-scan' swapped for the existing correction
        # webhooks the Flutter app uses.
        self.n8n_correction_webhook_url: str = (
            os.environ.get("N8N_CORRECTION_WEBHOOK_URL", "").strip()
            or derive_webhook_url(self.n8n_batch_webhook_url, "sast-manual-correction")
        )
        self.n8n_import_error_webhook_url: str = (
            os.environ.get("N8N_IMPORT_ERROR_WEBHOOK_URL", "").strip()
            or derive_webhook_url(self.n8n_batch_webhook_url, "import-error-correction")
        )
        # The correction flow runs the whole shared flow before it answers.
        self.n8n_correction_timeout_seconds: float = _float_env("N8N_CORRECTION_TIMEOUT_SECONDS", 180.0)
        # Pipeline failures (n8n answered but did not save / bad reply) allowed per page
        # before it is marked permanently failed and the watcher skips the file.
        self.max_pipeline_attempts: int = max(1, int(_float_env("MAX_PIPELINE_ATTEMPTS", 5)))
        # OMR preprocessing for scanner/website pages (app/omr_prep.py).
        self.pad_for_omr: bool = _bool_env("PAD_FOR_OMR", True)
        self.pad_percent: float = max(0.0, min(20.0, _float_env("PAD_PERCENT", 5.0)))
        self.crop_long_bond: bool = _bool_env("CROP_LONG_BOND", True)
        self.simulate_scans: bool = _bool_env("SIMULATE_SCANS", False)
        self.abandoned_batch_hours: float = _float_env("ABANDONED_BATCH_HOURS", 3.0)
        self.max_upload_mb: float = _float_env("MAX_UPLOAD_MB", 12.0)
        # Scan-ingest calls handled at once (app/body_limit.py); more get 503
        # server_busy + Retry-After and the watcher retries. Each call holds one
        # decoded page and waits for n8n, so keep it >= the number of scanning laptops.
        self.ingest_max_concurrency: int = max(1, int(_float_env("INGEST_MAX_CONCURRENCY", 4)))
        # Grace window (seconds) for the "file predates batch" check, on top of
        # the watcher clock-skew correction.
        self.predate_grace_seconds: float = _float_env("PREDATE_GRACE_SECONDS", 30.0)
        # A watcher (2.4.4+, heartbeat every 15 s) counts as online this long after
        # its last heartbeat. Older watchers keep 90 s (app/station_status.py).
        self.watcher_online_seconds: float = _float_env("WATCHER_ONLINE_SECONDS", 40.0)
        # Page viewer (migration 010, app/page_images.py): a viewing copy of every
        # scanned page in this PRIVATE Supabase Storage bucket. Empty = don't keep pictures.
        self.scan_image_bucket: str = os.environ.get("SCAN_IMAGE_BUCKET", "scan-pages").strip()
        # Pictures older than this many days are removed (0 = keep them).
        self.scan_image_retention_days: int = max(0, int(_float_env("SCAN_IMAGE_RETENTION_DAYS", 90)))
        self.scan_image_max_side: int = int(_float_env("SCAN_IMAGE_MAX_SIDE", 1800))
        self.scan_image_jpeg_quality: int = int(_float_env("SCAN_IMAGE_JPEG_QUALITY", 75))
        # Public backend URL written into the watcher installer's watcher_config.json
        # (same value as the frontend's VITE_API_BASE). Empty = derive from the request.
        self.watcher_api_base: str = (
            os.environ.get("WATCHER_API_BASE") or os.environ.get("PUBLIC_API_BASE") or ""
        ).strip().rstrip("/")
        self.cors_origins: list[str] = [
            o.strip()
            for o in os.environ.get(
                "CORS_ORIGINS",
                "http://localhost:5173,http://127.0.0.1:5173",
            ).split(",")
            if o.strip()
        ]
        # Opt-in: also allow any private-LAN origin (dev convenience only).
        self.cors_allow_lan: bool = _bool_env("CORS_ALLOW_LAN", False)
        # Rate limiting behind a proxy (security.client_ip): trust CF-Connecting-IP /
        # the rightmost X-Forwarded-For entry. Leave false for local runs (headers
        # would be spoofable); set true on Render (it sits behind Cloudflare).
        self.trust_proxy_headers: bool = _bool_env("TRUST_PROXY_HEADERS", False)
        # Brevo (app/mailer.py): same API key and sender as the Flutter app's
        # Supabase functions. Optional: empty = activation still works, no email.
        self.brevo_api_key: str = os.environ.get("BREVO_API_KEY", "").strip()
        self.brevo_sender_email: str = os.environ.get("BREVO_SENDER_EMAIL", "").strip()
        self.brevo_sender_name: str = os.environ.get("BREVO_SENDER_NAME", "").strip() or "SAO Management"

    @property
    def simulation_mode(self) -> bool:
        return self.simulate_scans and not self.n8n_batch_webhook_url

    @property
    def email_configured(self) -> bool:
        return bool(self.brevo_api_key and self.brevo_sender_email)


BATCH_WEBHOOK_PATH = "sast-batch-scan"


def derive_webhook_url(batch_url: str, path: str) -> str:
    """'https://n8n.x/webhook/sast-batch-scan' -> 'https://n8n.x/webhook/<path>'.
    Empty when the batch URL is empty or does not end in 'sast-batch-scan'."""
    base = (batch_url or "").strip()
    head, sep, tail = base.rstrip("/").rpartition("/")
    if not sep or tail.split("?", 1)[0] != BATCH_WEBHOOK_PATH:
        return ""
    return f"{head}/{path}"


def is_valid_uuid(val) -> bool:
    if not val or not isinstance(val, str):
        return False
    try:
        uuid.UUID(val)
        return True
    except (ValueError, AttributeError, TypeError):
        return False


@lru_cache
def get_settings() -> Settings:
    return Settings()


def _http_client(timeout: float = 30.0) -> httpx.Client:
    # http2=False avoids RemoteProtocolError ("Server disconnected") on idle connections.
    return httpx.Client(
        http2=False,
        timeout=httpx.Timeout(timeout, connect=10.0),
        limits=httpx.Limits(max_keepalive_connections=10, max_connections=20, keepalive_expiry=30.0),
    )


@lru_cache
def get_supabase() -> Client:
    """Shared service-role client. Never sign a user in on this client
    (that would swap its auth header to the user's token)."""
    s = get_settings()
    options = ClientOptions(httpx_client=_http_client())
    return create_client(s.supabase_url, s.supabase_service_key, options=options)


def new_auth_client() -> Client:
    """Isolated, throwaway client for sign-in / refresh so user sessions never
    leak into the shared service-role client."""
    s = get_settings()
    options = ClientOptions(httpx_client=_http_client(), flow_type="implicit", auto_refresh_token=False, persist_session=False)
    return create_client(s.supabase_url, s.supabase_service_key, options=options)


class ConfigError(RuntimeError):
    pass


class DatabaseUnreachable(RuntimeError):
    """Supabase (database or auth) could not be reached. main.py turns it into
    503 database_unreachable."""


def validate_settings(s: Settings) -> None:
    """Refuse to start with an unsafe configuration."""
    if s.n8n_batch_webhook_url and not s.n8n_webhook_secret:
        raise ConfigError(
            "N8N_BATCH_WEBHOOK_URL is set but N8N_WEBHOOK_SECRET is empty. Set N8N_WEBHOOK_SECRET "
            "(the shared secret the n8n workflow checks in the X-Webhook-Secret header) and restart."
        )
    for name, attr in (("N8N_BATCH_WEBHOOK_URL", "n8n_batch_webhook_url"),
                       ("N8N_CORRECTION_WEBHOOK_URL", "n8n_correction_webhook_url"),
                       ("N8N_IMPORT_ERROR_WEBHOOK_URL", "n8n_import_error_webhook_url")):
        url = getattr(s, attr, "") or ""
        if url and not is_safe_webhook_url(url):
            # Never echo the URL itself (it can carry a token).
            raise ConfigError(
                f"{name} must start with https:// (plain http:// is only allowed for localhost "
                "or a private-network address such as 192.168.x.x / 10.x.x.x)."
            )


def _is_private_host(host: str) -> bool:
    """localhost, 127.0.0.0/8, ::1, or an RFC1918 / other private IP literal.
    Hostnames other than 'localhost' are NOT trusted (DNS could point anywhere)."""
    host = (host or "").strip().lower().rstrip(".")
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_loopback or ip.is_private


def is_safe_webhook_url(url: str) -> bool:
    """https:// anywhere; http:// only to a loopback / private-network host."""
    try:
        parts = urlsplit((url or "").strip())
        host = parts.hostname or ""
    except ValueError:
        return False
    scheme = parts.scheme.lower()
    if not host:
        return False
    if scheme == "https":
        return True
    return scheme == "http" and _is_private_host(host)
