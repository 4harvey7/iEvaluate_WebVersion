"""Page preprocessing so the Python OMR can find the paper edge.

Flatbed scans have no background around the sheet: the OMR's isolate_paper()
then takes the table border for "the page" and table detection fails. The
scanners here output Legal (8.5x14 in) or short (8.5x11) images; the real
sheet is long bond (8.5x13) or short bond (8.5x11), top-aligned, with white
below. So, for scanner/website pages only (never the phone path), after
orientation and before JPEG encoding:

  0. Detect the bond size: SHORT when the image itself is at most 1.40x as
     tall as wide, or when its lowest printed/written row sits within
     1.40 x width of the top (short sheets end at ~1.21-1.29 x width, long
     sheets at ~1.50); otherwise (or on a blank page) LONG. The result is
     sent to n8n as paper_size for the OMR.
  1. Crop the bottom to that bond's proportions (height = width * 13/8.5 or
     11/8.5, measured from the top), but never above the lowest
     printed/written row: the cut is placed at max(bond height, ink bottom +
     margin), so a slightly shifted or deskewed page keeps all its rows.
  2. Add a solid mid-grey (128) border (default 5% of the width) on every
     side, giving isolate_paper() a clear paper edge.

Configured by PAD_FOR_OMR / PAD_PERCENT / CROP_LONG_BOND (see app/db.py);
CROP_LONG_BOND keeps its name but now means "crop to the detected bond size".
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageOps

LONG_BOND_RATIO = 13 / 8.5
SHORT_BOND_RATIO = 11 / 8.5
# Height/width at or below which a page (or its content) is short bond.
SHORT_DETECT_RATIO = 1.40
GREY = 128

# Header crop (website OCR only): top slice of the already-oriented portrait page, covering the
# CTU letterhead/instructions plus the handwritten "Name of Instructor/Professor" / "Subject
# Taught" / "AY/Semester" line, just above the rating grid ("I. Management"). Picked by checking
# samples in C:\FORMS (2803x1700 landscape scans -> 1700x2803 portrait): the grid starts at
# ~41% of page height, this leaves margin for skew. NOTE: the student ID field is near the
# SIGNATURE block at the bottom of this form (~89%), not in this crop; only instructor/subject
# benefit from it.
HEADER_TOP_FRACTION = 0.44
HEADER_UPSCALE = 2.0
HEADER_JPEG_QUALITY = 88

# A row counts as content if at least this share of its pixels is ink.
ROW_INK_FRACTION = 0.003
# Ignore this bottom band when looking for content (scanner-lid edge shadows).
BOTTOM_EDGE_IGNORE = 0.015
# Keep this much (share of page height) below the lowest content row.
CONTENT_MARGIN = 0.01


@dataclass
class OmrPrepConfig:
    pad: bool = True
    pad_percent: float = 5.0
    crop_long_bond: bool = True


@dataclass
class OmrPrepResult:
    image: Image.Image
    steps: list[str]
    cropped_rows: int = 0
    pad_px: int = 0
    paper_size: str = "LONG"

    @property
    def label(self) -> str:
        return "+".join(self.steps) if self.steps else "none"


def ink_bottom(img: Image.Image) -> int | None:
    """Lowest row (in full-resolution pixels) that holds content, or None if blank."""
    g = img.convert("L")
    scale = 1
    if g.width > 600:
        scale = g.width / 600
        g = g.resize((600, max(1, round(g.height / scale))), Image.BILINEAR)
    a = np.asarray(g, dtype=np.int16)
    paper = np.percentile(a, 90)
    ink = np.percentile(a, 1)
    if paper - ink < 30:
        return None
    dark = a < paper - max(35.0, 0.35 * (paper - ink))
    h = dark.shape[0]
    dark[h - max(1, int(BOTTOM_EDGE_IGNORE * h)):, :] = False
    rows = np.nonzero(dark.mean(axis=1) >= ROW_INK_FRACTION)[0]
    if rows.size == 0:
        return None
    return min(img.height, int(round((int(rows[-1]) + 1) * scale)))


def detect_paper_size(img: Image.Image) -> str:
    """'SHORT' (8.5x11) or 'LONG' (8.5x13) bond; blank pages count as LONG."""
    if img.height / img.width <= SHORT_DETECT_RATIO:
        return "SHORT"
    b = ink_bottom(img)
    if b is not None and b / img.width <= SHORT_DETECT_RATIO:
        return "SHORT"
    return "LONG"


def crop_height(img: Image.Image, paper_size: str = "LONG") -> int:
    """Height to keep from the top: bond proportion, never cutting content."""
    ratio = SHORT_BOND_RATIO if paper_size == "SHORT" else LONG_BOND_RATIO
    target = int(round(img.width * ratio))
    if img.height <= target:
        return img.height
    bottom = ink_bottom(img)
    keep = target
    if bottom is not None:
        keep = max(target, bottom + int(round(CONTENT_MARGIN * img.height)))
    return min(img.height, keep)


def crop_header(img: Image.Image, fraction: float = HEADER_TOP_FRACTION,
                 upscale: float = HEADER_UPSCALE) -> Image.Image:
    """Cropped, upscaled, contrast-boosted top slice of an already-oriented portrait page, for a
    clearer Gemini read of the handwritten instructor/subject fields (see HEADER_TOP_FRACTION)."""
    h = max(1, min(img.height, int(round(img.height * fraction))))
    crop = img.crop((0, 0, img.width, h))
    if crop.mode not in ("L", "RGB"):
        crop = crop.convert("RGB")
    if upscale and upscale != 1.0:
        crop = crop.resize((max(1, int(round(crop.width * upscale))), max(1, int(round(crop.height * upscale)))),
                            Image.LANCZOS)
    return ImageOps.autocontrast(crop, cutoff=1)


def prepare_for_omr(img: Image.Image, cfg: OmrPrepConfig) -> OmrPrepResult:
    steps: list[str] = []
    cropped = 0
    paper_size = detect_paper_size(img)
    if cfg.crop_long_bond:
        keep = crop_height(img, paper_size)
        if keep < img.height:
            cropped = img.height - keep
            img = img.crop((0, 0, img.width, keep))
            steps.append("shortbond_crop" if paper_size == "SHORT" else "longbond_crop")
    pad_px = 0
    if cfg.pad and cfg.pad_percent > 0:
        pad_px = max(1, int(round(img.width * cfg.pad_percent / 100)))
        if img.mode not in ("L", "RGB"):
            img = img.convert("RGB")
        fill = GREY if img.mode == "L" else (GREY, GREY, GREY)
        img = ImageOps.expand(img, border=pad_px, fill=fill)
        steps.append(f"grey_pad_{cfg.pad_percent:g}")
    return OmrPrepResult(image=img, steps=steps, cropped_rows=cropped, pad_px=pad_px,
                         paper_size=paper_size)
