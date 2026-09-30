import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app.security import CurrentUser, require_staff

router = APIRouter()

# Built from backend/watcher/ by `python watcher/build_package.py`. Contains no
# secrets: the station key lives only in the per-laptop watcher_config.json,
# which POST /stations/{id}/installer adds to its copy of this zip (or IT
# downloads it from the website's "Advanced (for IT)" section).
STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
ZIP_PATH = os.path.join(STATIC_DIR, "watcher-package.zip")


@router.get("/download")
def download_watcher(user: CurrentUser = Depends(require_staff)):
    if not os.path.isfile(ZIP_PATH):
        raise HTTPException(status_code=503, detail="The watcher package has not been built on the server yet. Ask the administrator to run watcher/build_package.py.")
    return FileResponse(
        ZIP_PATH,
        media_type="application/zip",
        filename="ievaluate-scan-watcher.zip",
    )
