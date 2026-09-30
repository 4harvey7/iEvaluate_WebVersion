import logging

import httpx
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from postgrest.exceptions import APIError

from app.body_limit import BodySizeLimitMiddleware
from app.db import DatabaseUnreachable, get_settings, validate_settings
from app.routers import auth, batch_pages, batches, departments, instructors, reviews, scans, stations, subjects, watcher_download
from app.security import CurrentUser, require_staff

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("ievaluate")

app = FastAPI(title="iEvaluate Batch Scanning API", docs_url=None, redoc_url=None, openapi_url=None)

# Postgres / PostgREST codes meaning "migration 002 has not been applied".
SCHEMA_MISSING_CODES = {"42703", "42P01", "42883", "PGRST202", "PGRST204", "PGRST205"}

# Network trouble between this server and Supabase (DNS, refused, timeout,
# dropped connection) -> 503 the watcher / website retry, never a generic 500.
# httpx.TimeoutException and httpx.ConnectError are TransportError subclasses.
TRANSPORT_ERRORS = (httpx.TransportError, DatabaseUnreachable)
# PostgREST APIError built from a non-JSON gateway reply (Cloudflare / proxy):
# its code is the HTTP status.
GATEWAY_STATUS_CODES = {"502", "503", "504", "520", "521", "522", "523", "524", "525", "530"}
DATABASE_UNREACHABLE = {"code": "database_unreachable", "detail": "The server can't reach the database right now. It will try again."}


def _unreachable_response(request: Request, exc: Exception) -> JSONResponse:
    log.warning("database unreachable on %s %s: %s", request.method, request.url.path, type(exc).__name__)
    return JSONResponse(status_code=503, content=dict(DATABASE_UNREACHABLE), headers={"Retry-After": "15"})


def _is_gateway_error(exc: APIError) -> bool:
    return str(getattr(exc, "code", "") or "") in GATEWAY_STATUS_CODES


def _api_error_response(exc: APIError, request: Request | None = None) -> JSONResponse:
    code = getattr(exc, "code", None)
    if _is_gateway_error(exc) and request is not None:
        return _unreachable_response(request, exc)
    if code == "22P02":
        return JSONResponse(status_code=400, content={"detail": "Invalid identifier."})
    if code == "PGRST116":
        return JSONResponse(status_code=404, content={"detail": "Resource not found."})
    if code == "23505":
        return JSONResponse(status_code=409, content={"detail": "That record already exists."})
    if code == "23503":
        return JSONResponse(status_code=400, content={"detail": "A referenced record does not exist."})
    log.error("database error code=%s message=%s", code, getattr(exc, "message", ""))
    if code in SCHEMA_MISSING_CODES:
        return JSONResponse(status_code=503, content={
            "detail": {"code": "schema_outdated", "message": "Database schema is out of date. Apply migrations/002_batch_hardening.sql."}
        })
    return JSONResponse(status_code=500, content={"detail": "Database error. Please try again."})


@app.exception_handler(APIError)
async def postgrest_exception_handler(request: Request, exc: APIError):
    return _api_error_response(exc, request)


@app.exception_handler(httpx.TransportError)
@app.exception_handler(DatabaseUnreachable)
async def transport_exception_handler(request: Request, exc: Exception):
    return _unreachable_response(request, exc)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    log.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error."})


# Innermost catch-all so even unexpected errors pass back through the CORS
# middleware (otherwise the browser only sees an opaque network failure).
@app.middleware("http")
async def catch_all(request: Request, call_next):
    try:
        return await call_next(request)
    except APIError as exc:
        return _api_error_response(exc, request)
    except TRANSPORT_ERRORS as exc:
        return _unreachable_response(request, exc)
    except Exception:
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": "Internal server error."})


settings = get_settings()
validate_settings(settings)  # refuse to start with an unsafe config

# Body cap sits inside CORS (so 413s carry CORS headers) and outside routing.
app.add_middleware(BodySizeLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_origin_regex=(
        r"^https?://(localhost|127\.0\.0\.1|192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+)(:\d+)?$"
        if settings.cors_allow_lan else None
    ),
    allow_credentials=False,  # bearer tokens, no cookies
    allow_methods=["GET", "POST", "PUT", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
    expose_headers=["Content-Disposition"],  # installer zip filename
)

app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(stations.router, prefix="/stations", tags=["stations"])
app.include_router(instructors.router, prefix="/instructors", tags=["instructors"])
app.include_router(subjects.router, prefix="/subjects", tags=["subjects"])
app.include_router(batches.router, prefix="/batches", tags=["batches"])
# Page viewer: /batches/{batch_id}/items/{item_id}[/image]
app.include_router(batch_pages.router, prefix="/batches", tags=["batches"])
app.include_router(scans.router, prefix="/scans", tags=["scans"])
app.include_router(watcher_download.router, prefix="/watcher", tags=["watcher"])
app.include_router(departments.router, prefix="/departments", tags=["departments"])
app.include_router(reviews.router, prefix="/reviews", tags=["reviews"])


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/config")
def client_config(user: CurrentUser = Depends(require_staff)):
    s = get_settings()
    return {
        "simulation_mode": s.simulation_mode,
        "pipeline_configured": bool(s.n8n_batch_webhook_url),
        "abandoned_batch_hours": s.abandoned_batch_hours,
        "watcher_online_seconds": s.watcher_online_seconds,
        # Review page: corrections can be sent to the n8n correction webhooks.
        "review_corrections_configured": bool(s.n8n_correction_webhook_url),
    }
