"""Orientation normalisation for scanned SS Form 2 pages (SAST).

The OMR grid detection in n8n needs every page upright and in portrait. Scanners
deliver it sideways (either way) or upside down, so every ingested page goes
through `normalize_orientation` before JPEG conversion:

  1. EXIF transpose (phone/scanner orientation tag).
  2. Landscape -> portrait with a fixed 90 degree turn.
  3. Upright vs upside-down (which also settles 90 vs 270 for sideways scans),
     decided from the form's printed structure -- ported from the Flutter
     app's form_signature.dart / the OMR pipeline's detectTableGrid():
       * the rating block is five equal columns = six vertical rulings at
         72-98% of the table width, i.e. on the RIGHT of an upright page;
       * the ruled table sits in the lower part of the page (~38-92%), below
         the letterhead, so its horizontal rulings (and the rating columns)
         centre below the middle of an upright page.
     Rotating by 180 degrees mirrors every one of these measurements, so a
     single signed score decides: > 0 upright, < 0 upside down.
  4. Low confidence (not a form, blank, heavily damaged): keep the page as it
     is after step 2 and report `uncertain`, so n8n / staff can check it.

Pure Pillow + numpy on a ~480 px wide copy; typically a few tens of ms.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageOps

ANALYSIS_WIDTH = 480
CONFIDENCE_THRESHOLD = 0.25

# Weights of the three structural cues (they sum to 1, so |score| <= 1).
W_RATING_SIDE = 0.6      # vertical rulings right vs left
W_TABLE_ROWS = 0.2       # horizontal rulings below vs above the middle
W_RATING_HEIGHT = 0.2    # vertical rulings below vs above the middle

T = Image.Transpose


@dataclass
class OrientationResult:
    image: Image.Image = field(repr=False)
    rotation_cw: int = 0            # clockwise degrees applied after the EXIF step: 0/90/180/270
    exif_transposed: bool = False
    confidence: float = 0.0         # 0..1
    uncertain: bool = False
    features: dict = field(default_factory=dict)

    @property
    def changed(self) -> bool:
        return self.exif_transposed or self.rotation_cw != 0


# Clockwise degrees -> Pillow transpose (Pillow's ROTATE_n is counter-clockwise).
_CW = {90: T.ROTATE_270, 180: T.ROTATE_180, 270: T.ROTATE_90}

# Landscape pages are first turned counter-clockwise (the orientation most
# scanners here produce: letterhead along the right edge). If the heuristic is
# unsure, this is where the page stays.
LANDSCAPE_DEFAULT_CW = 270


def rotate_cw(img: Image.Image, degrees: int) -> Image.Image:
    degrees %= 360
    return img.transpose(_CW[degrees]) if degrees else img


def _analysis_array(img: Image.Image) -> np.ndarray:
    g = img.convert("L")
    if g.width > ANALYSIS_WIDTH * 2:
        g = g.reduce(max(1, g.width // (ANALYSIS_WIDTH * 2)))
    h = max(1, round(g.height * ANALYSIS_WIDTH / g.width))
    g = g.resize((ANALYSIS_WIDTH, h), Image.BILINEAR)
    return np.asarray(g, dtype=np.int16)


DESKEW_COARSE = [-4.0, -3.0, -2.0, -1.0, 0.0, 1.0, 2.0, 3.0, 4.0]  # degrees


def _deskew(dark: np.ndarray) -> tuple[np.ndarray, float]:
    """Rotate the (small) ink mask to the angle whose row profile is sharpest:
    printed rulings line up with pixel rows only when the page is square.
    Coarse 1-degree search over +/-4, then +/-0.5 around the best."""
    img = Image.fromarray((dark * 255).astype(np.uint8))
    cache: dict[float, tuple[float, np.ndarray]] = {}

    def score(angle: float) -> float:
        if angle not in cache:
            m = dark if angle == 0 else np.asarray(img.rotate(angle, resample=Image.NEAREST, fillcolor=0)) > 127
            rows = m.sum(axis=1).astype(np.float64)
            cache[angle] = (float((rows * rows).sum()), m)
        return cache[angle][0]

    best = max(DESKEW_COARSE, key=score)
    best = max((best - 0.5, best, best + 0.5), key=score)
    return cache[best][1], best


def _dark_mask(a: np.ndarray) -> np.ndarray:
    paper = np.percentile(a, 90)
    ink = np.percentile(a, 2)
    if paper - ink < 30:  # blank / no contrast
        return np.zeros(a.shape, dtype=bool)
    return a < paper - max(35.0, 0.35 * (paper - ink))


def _run_starts(mask: np.ndarray, length: int, axis: int) -> np.ndarray:
    """Positions where a run of >= `length` dark pixels starts along `axis`."""
    m = mask.astype(np.int32)
    if axis == 1:
        m = m.T
    n = m.shape[0]
    if length >= n:
        out = np.zeros((1, m.shape[1]), dtype=bool)
    else:
        c = np.vstack([np.zeros((1, m.shape[1]), dtype=np.int32), np.cumsum(m, axis=0)])
        out = (c[length:] - c[:-length]) == length
    return out.T if axis == 1 else out


def _centroid(profile: np.ndarray) -> float | None:
    total = profile.sum()
    if total <= 0:
        return None
    return float((profile * np.arange(len(profile))).sum() / total / max(1, len(profile) - 1))


def orientation_score(img: Image.Image) -> tuple[float, dict]:
    """Signed score in [-1, 1] for a PORTRAIT page: > 0 upright, < 0 upside down."""
    a = _analysis_array(img)
    h, w = a.shape
    dark = _dark_mask(a)
    if not dark.any():
        return 0.0, {"reason": "blank"}
    dark, skew = _deskew(dark)

    # Ignore a thin margin: scanner-bed edges produce full-length dark lines.
    my, mx = max(1, h // 50), max(1, w // 50)
    dark[:my, :] = dark[-my:, :] = False
    dark[:, :mx] = dark[:, -mx:] = False

    # Vertical rulings: tolerate a slight skew by OR-ing each column with its neighbours.
    vdark = dark.copy()
    vdark[:, 1:] |= dark[:, :-1]
    vdark[:, :-1] |= dark[:, 1:]
    v_starts = _run_starts(vdark, max(8, int(0.08 * h)), axis=0)
    col = v_starts.sum(axis=0).astype(np.float64)
    col[col > 0.6 * h] = 0  # full-height lines are page borders, not the rating grid

    hdark = dark.copy()
    hdark[1:, :] |= dark[:-1, :]
    hdark[:-1, :] |= dark[1:, :]
    h_starts = _run_starts(hdark, max(8, int(0.25 * w)), axis=1)
    row_h = h_starts.sum(axis=1).astype(np.float64)

    left = col[: int(0.4 * w)].sum()
    right = col[int(0.6 * w):].sum()
    total_v = left + right
    features = {"v_mass": round(float(col.sum()), 1), "h_mass": round(float(row_h.sum()), 1), "skew": skew}
    if total_v < 0.05 * h or row_h.sum() < 0.5 * w:
        features["reason"] = "no_ruled_table"
        return 0.0, features

    side = (right - left) / (total_v + 1e-9)
    row_v = v_starts.sum(axis=1).astype(np.float64)
    cy_v = _centroid(row_v)
    cy_h = _centroid(row_h)
    table_rows = float(np.clip(((cy_h if cy_h is not None else 0.5) - 0.5) / 0.1, -1, 1))
    rating_height = float(np.clip(((cy_v if cy_v is not None else 0.5) - 0.5) / 0.1, -1, 1))

    score = W_RATING_SIDE * side + W_TABLE_ROWS * table_rows + W_RATING_HEIGHT * rating_height
    features.update(side=round(float(side), 3), table_rows=round(table_rows, 3),
                    rating_height=round(rating_height, 3), score=round(float(score), 3))
    return float(score), features


def normalize_orientation(img: Image.Image, threshold: float = CONFIDENCE_THRESHOLD) -> OrientationResult:
    exif_before = img.getexif().get(0x0112, 1) if hasattr(img, "getexif") else 1
    exif_transposed = exif_before not in (None, 1)
    if exif_transposed:
        img = ImageOps.exif_transpose(img)

    base_cw = LANDSCAPE_DEFAULT_CW if img.width > img.height else 0
    portrait = rotate_cw(img, base_cw)

    score, features = orientation_score(portrait)
    confidence = min(1.0, abs(score))
    uncertain = confidence < threshold
    extra = 180 if (not uncertain and score < 0) else 0
    total_cw = (base_cw + extra) % 360
    out = rotate_cw(portrait, extra)
    return OrientationResult(
        image=out,
        rotation_cw=total_cw,
        exif_transposed=exif_transposed,
        confidence=round(confidence, 3),
        uncertain=uncertain,
        features=features,
    )
