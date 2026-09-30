"""
utlis.py  —  Survey-form scanning utilities
============================================
Two-stage pipeline
  Stage 1 · isolate_paper()        → rectified full-page BGR image
  Stage 2 · findTableContour()     → 4-corner rect of the rating grid
            warpTable()            → perspective-correct that grid
Then:
  detectMarkInRow()                → OMR (checkmark → column 0-4 or -1)
  extract_fields_from_paper()      → OCR on fixed relative regions
  draw_field_overlay()             → annotated paper thumbnail

──────────────────────────────────────────────────────────────────
CHANGES IN THIS VERSION
──────────────────────────────────────────────────────────────────
3. *** NEW *** — removed the local Ollama vision-model (moondream /
   qwen2.5vl) OCR layer entirely. All handwritten-field reading is now
   done with EasyOCR directly — no more `ollama` import, no more
   per-field model overrides, no more color/enhanced/binarized retry
   ladder built for a VLM's failure modes. Concretely removed:
     - OLLAMA_VISION_MODEL / OLLAMA_VISION_MODEL_OVERRIDES / _model_for_field
     - _chat_with_fallback()
     - _image_for_attempt() and its color-ladder helpers
       (_upscale_color_for_vlm, _enhance_for_vlm)
     - _easyocr_guess() — it existed only to feed a "hint" into a VLM
       prompt; EasyOCR's own read is now just the answer directly.
     - _hint_text() / _numeric_hint() — VLM-prompt-only helpers.
     - _looks_degenerate() — was guarding against a VLM inventing a
       plausible-looking fake student ID; EasyOCR doesn't fabricate
       text the way a generative VLM can, so that guard is no longer
       needed.
   `_ocr_crop()` and `_ocr_student_id()` are now thin, EasyOCR-only
   functions: binarize the crop (_prepare_for_ocr), run
   `reader.readtext()`, optionally retry once at a larger size if the
   first pass came back empty. Function names/signatures used by
   `extract_fields_from_paper()` are kept the same so nothing else in
   main.py / api.py needs to change.
   `field_images` / `main_fields_image` (base64 PNG crops) are still
   produced in the output dict for backward compatibility with
   main.py's debug-image saving and api.py's JSON response shape, but
   they're no longer intended to be forwarded to a downstream AI —
   they're just debug/reference images now.

──────────────────────────────────────────────────────────────────
CHANGES CARRIED OVER FROM EARLIER VERSIONS
──────────────────────────────────────────────────────────────────
- OMR: detectTableGrid() column/row line threshold raised to a
  tunable `line_thresh=0.6` (was a hardcoded 0.3) — prevents a column
  of stacked diagonal checkmarks (e.g. every row checked in the same
  column) from being mistaken for a real ruling line.
- OMR: added binarize_table() — a SINGLE global Otsu threshold computed
  once for the whole warped table, instead of re-computing Otsu
  per-row inside detectMarkInRow(). detectMarkInRow() accepts an
  optional pre-binarized `table_bw`; falls back to per-row Otsu if
  not given.
- OMR: widened the per-cell border margin (mx) used inside
  detectMarkInRow so ink sitting on/near a column's vertical rule
  line no longer counts toward that cell's score.
- OMR: detectMarkInRow can return per-column debug scores via
  return_scores=True.
- OCR: extract_fields_from_paper() returns base64-encoded PNG crops
  for every field under 'field_images'.
- OCR: ay_semester and date also get a handwriting-OCR attempt
  (kept under ay_semester_text / date_text so nothing that reads the
  old boolean keys breaks).
- `instructor` field region widened on the left (0.290 → 0.260) —
  was clipping the first 1-2 letters of the handwritten name.
- remarks region: top moved down / right moved left to avoid the
  section-header row and the empty rating-column borders.
"""

import base64
import cv2
import numpy as np

# ─────────────────────────────────────────────────────────
#  RATING SYSTEM CONSTANTS
# ─────────────────────────────────────────────────────────
RATING_NAMES  = ['O',  'VS', 'S',  'F',  'US']
RATING_SCORES = [5,     4,    3,    2,    1   ]

MGMT_LABELS = [
    "Gives reasonable course / subject assignments",
    "Earns appreciation and kind attention from the students",
    "Gives orientation about the subject and how students are evaluated",
    "Gives tests and/or projects within the objectives of the course",
    "Shows deep interest and concern in assisting the students",
    "Manifests sympathetic insight into students' feelings",
    "Checks and records test papers / term papers",
    "Is on time and regular in meeting the class",
    "Apportions fair subject / course assignments",
    "Sustains the attention of the class for the whole period",
]

PERF_LABELS = [
    "Presents lesson clearly, methodically, and substantially",
    "Motivates the students to learn",
    "Facilitates learning with appropriate educational methods and techniques",
    "Shows mastery of the lesson",
    "Is ready for the class",
    "Inspires students' self-reliance in their quest for knowledge",
    "Knows when students have difficulty and finds ways to make it easy",
    "Integrates values into the lesson",
    "Speaks the language of instruction (English or Filipino) clearly and fluently",
    "Delivers thought-provoking questions",
]


# ─────────────────────────────────────────────────────────
#  INTERNAL GEOMETRY HELPERS
# ─────────────────────────────────────────────────────────

def reorder_corners(pts):
    pts = pts.reshape(4, 2).astype(np.float32)
    out = np.zeros((4, 2), np.float32)

    s      = pts.sum(axis=1)
    out[0] = pts[np.argmin(s)]
    out[3] = pts[np.argmax(s)]

    d      = np.diff(pts, axis=1).ravel()
    out[1] = pts[np.argmin(d)]
    out[2] = pts[np.argmax(d)]

    return out


def _edge_map(img, canny_lo=10, canny_hi=70, dilate_iters=2, blur_k=5):
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img.copy()
    g = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(g)
    g = cv2.GaussianBlur(g, (blur_k, blur_k), 0)
    e = cv2.Canny(g, canny_lo, canny_hi)
    e = cv2.dilate(e, np.ones((5, 5), np.uint8), iterations=dilate_iters)
    return e


def _quad_contours(edge_map, img_area, lo_ratio=0.05, hi_ratio=0.99):
    cnts, _ = cv2.findContours(edge_map, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    quads = []
    for c in cnts:
        a = cv2.contourArea(c)
        if a < img_area * lo_ratio or a > img_area * hi_ratio:
            continue
        p  = cv2.arcLength(c, True)
        ap = cv2.approxPolyDP(c, 0.02 * p, True)
        if len(ap) == 4:
            quads.append((a, ap))
    quads.sort(key=lambda x: x[0], reverse=True)
    return quads


def _best_quad(quads, frame_w, frame_h, border_margin=0.012, min_fill=0.85):
    best, best_area = None, -1
    for area, approx in quads:
        x, y, w, h = cv2.boundingRect(approx)
        if (x <= frame_w * border_margin or y <= frame_h * border_margin or
                (x + w) >= frame_w * (1 - border_margin) or
                (y + h) >= frame_h * (1 - border_margin)):
            continue

        fill = area / (w * h + 1e-6)
        if fill < min_fill:
            continue

        if area > best_area:
            best_area = area
            best = approx
    return best


# ─────────────────────────────────────────────────────────
#  STAGE 1  —  PAPER ISOLATION
# ─────────────────────────────────────────────────────────

def _snap_to_paper(raw_w, raw_h):
    long_s  = max(raw_w, raw_h)
    short_s = min(raw_w, raw_h) or 1
    ratio   = long_s / short_s

    if   ratio < 1.35:  sw, sl, name = 1700, 2200, "Short Bond (8.5×11)"
    elif ratio < 1.47:  sw, sl, name = 1654, 2339, "A4 (8.27×11.69)"
    elif ratio < 1.58:  sw, sl, name = 1700, 2600, "Long Bond (8.5×13)"
    else:               sw, sl, name = 1700, 2800, "Legal (8.5×14)"

    if raw_w >= raw_h:
        return sl, sw, name + " [landscape]"
    return sw, sl, name


def isolate_paper(img):
    H, W  = img.shape[:2]
    A     = H * W
    best  = None

    for lo, hi, dil in [(10, 70, 3), (5, 50, 4), (30, 100, 2), (50, 150, 1)]:
        e     = _edge_map(img, lo, hi, dil)
        quads = _quad_contours(e, A, lo_ratio=0.15, hi_ratio=0.98)
        best  = _best_quad(quads, W, H)
        if best is not None:
            break

    if best is None:
        print("[WARN] isolate_paper: no page boundary found — using full frame.")
        return img.copy(), 0, 0

    corners          = reorder_corners(best)
    tl, tr, bl, br   = corners

    raw_w = int(max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl)))
    raw_h = int(max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr)))

    out_w, out_h, pname = _snap_to_paper(raw_w, raw_h)
    print(f"[INFO] Paper → {pname}  ({out_w} × {out_h} px)")

    src  = corners.astype(np.float32)
    dst  = np.float32([[0, 0], [out_w, 0], [0, out_h], [out_w, out_h]])
    M    = cv2.getPerspectiveTransform(src, dst)
    paper = cv2.warpPerspective(img, M, (out_w, out_h))

    ox = int(corners[:, 0].min())
    oy = int(corners[:, 1].min())
    return paper, ox, oy


def draw_page_detection(img, paper_ox, paper_oy, corners_in_orig=None):
    vis = img.copy()
    if corners_in_orig is not None:
        pts = corners_in_orig.astype(np.int32).reshape(-1, 1, 2)
        cv2.polylines(vis, [pts], True, (0, 255, 0), 3)
        for pt in corners_in_orig.astype(int):
            cv2.circle(vis, tuple(pt), 10, (0, 255, 0), -1)
    cv2.circle(vis, (paper_ox, paper_oy), 14, (0, 200, 255), -1)
    cv2.putText(vis, "TL", (paper_ox + 10, paper_oy + 8),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 200, 255), 2)
    return vis


# ─────────────────────────────────────────────────────────
#  STAGE 2  —  RATING TABLE DETECTION
# ─────────────────────────────────────────────────────────

def findTableContour(paper_img):
    H, W  = paper_img.shape[:2]
    A     = H * W
    best  = None
    best_area = 0

    # Quads touching the page edge within this fraction are the page border itself,
    # not the inner rating table — mirror the border_margin logic in _best_quad.
    BORDER_MARGIN = 0.03

    for lo, hi, dil in [(15, 75, 1), (10, 60, 2), (25, 90, 1), (8, 50, 2)]:
        e     = _edge_map(paper_img, lo, hi, dil)
        quads = _quad_contours(e, A, lo_ratio=0.08, hi_ratio=0.92)

        for r_area, approx in quads:
            corners      = reorder_corners(approx)
            cy           = corners[:, 1].mean()
            tbl_w        = max(np.linalg.norm(corners[1] - corners[0]),
                               np.linalg.norm(corners[3] - corners[2]))
            tbl_h        = max(np.linalg.norm(corners[2] - corners[0]),
                               np.linalg.norm(corners[3] - corners[1]))

            # ── existing filters ─────────────────────────────────────────
            if cy < H * 0.25 or cy > H * 0.95:   continue
            if tbl_w < W * 0.55:                   continue
            if tbl_h < H * 0.20:                   continue

            # ── Bug 1 fix: reject quads that hug the page border ─────────
            # The page-border quad typically has its bounding box touching
            # (or nearly touching) all four edges of the rectified paper.
            # The real rating table sits well inside the page.
            bx, by, bw_r, bh_r = cv2.boundingRect(approx)
            if (bx <= W * BORDER_MARGIN or
                    by <= H * BORDER_MARGIN or
                    (bx + bw_r) >= W * (1 - BORDER_MARGIN) or
                    (by + bh_r) >= H * (1 - BORDER_MARGIN)):
                continue

            # ── Bug 1 fix: reject quads taller than 75% of the page ──────
            # The rating table spans ~35-40% of the page height; a quad
            # covering >75% is almost certainly the full-page border.
            if tbl_h > H * 0.75:
                continue

            if r_area > best_area:
                best_area = r_area
                best      = approx

        if best is not None:
            break

    # PATCH (fix B): a handwritten stroke crossing the table border can
    # (a) stop the border from closing as a 4-vertex contour at all, or
    # (b) drag one corner of the quad far off the border. Both fed a skewed
    # or missing table to the grid fit. Recover (a) from the extreme points
    # of the largest table-sized contour, then square up one bad corner.
    rough = False
    if best is None:
        best = _table_quad_from_extremes(paper_img)
        rough = best is not None
        if best is not None:
            print("[INFO] findTableContour: no 4-vertex table contour — using the "
                  "extreme points of the largest table-sized contour.")
    if best is not None:
        # PATCH (fix C): put the sides back on the border rulings first
        # (see _snap_table_quad), then the fix-B one-corner repair.
        best = _snap_table_quad(paper_img, best, rough=rough)
        best = _repair_table_quad(best)

    if best is None:
        print("[WARN] findTableContour: table rectangle not detected.")
    else:
        c = reorder_corners(best)
        print(f"[INFO] Table contour found  (area ≈ {best_area:.0f} px²)  "
              f"corners TL({c[0][0]:.0f},{c[0][1]:.0f}) "
              f"TR({c[1][0]:.0f},{c[1][1]:.0f}) "
              f"BL({c[2][0]:.0f},{c[2][1]:.0f}) "
              f"BR({c[3][0]:.0f},{c[3][1]:.0f})")

    return best


# PATCH (fix C) — snap the table quad onto the table's real border rulings.
# findTableContour() takes the corners from approxPolyDP on a DILATED edge
# map, so anything printed or written close to the border merges into the
# contour and drags a corner. On short-bond flatbed scans the "Subject
# Taught" underline ends right above the table's top-left corner and pulled
# it up by ~40 px on 6 of 16 sheets (also on 2 of 42 phone photos); the
# warped table came out sheared by a whole row across its width, the row
# grid could not be fitted and those sheets read 3-8 of 20 rows correctly.
# The corner-angle repair below cannot catch this: a 40 px drag is only
# ~2 deg, the same as a phone photo's genuine keystone.
#
# What is always true is that each side of the table is a straight printed
# ruling along (almost) the whole side. For each side we sample a darkness
# profile across it at TR_SAMPLES points, keep the ruling-like peaks (darker
# than both flanks by TR_PEAK_PROM grey levels), and vote for straight lines
# (a small Hough over offset-at-start x offset-at-end).
#   * If a well-supported line lies within tolerance of the contour side,
#     the side is right and nothing moves (true for 34/38 scans and 40/42
#     phone photos, which are therefore unchanged).
#   * Otherwise the side moves onto the nearest line that is nearly as well
#     supported as the best one - but only if that line is TILTED against
#     the side (a dragged corner tilts its side; a far parallel line is an
#     inner ruling, meaning the real border was just too faint to see).
# Once a side moves, every fitted side is placed on its ruling (keeping the
# contour's usual few-px outward margin) and the corners are re-intersected.
# A rough quad from _table_quad_from_extremes() is searched wider and all
# its sides are snapped.
# Measured plateau (all three labelled sets unchanged): search 0.07-0.14 of
# the table height, support 0.5-0.75, relative support 0.75-0.95, peak
# prominence 14-30, move tolerance 0.008-0.012 of the table height.
TR_SAMPLES = 48            # sample points per side
TR_T_RANGE = (0.06, 0.94)  # skip the corners themselves (that is where a drag is)
TR_SEARCH_FRAC = 0.07      # +/- search across a side, share of table height
TR_ROUGH_SEARCH_FRAC = 0.20
TR_STRIP_HALF = 6          # average +/- this many px along the side (noise)
TR_HIT_TOL = 2.5           # px: a sample supports a line if a peak is this close
TR_MIN_SUPPORT = 0.60      # share of samples that must lie on the line
TR_SUPPORT_REL = 0.85      # ... and nearly as many as the best-supported line
TR_MOVE_TOL_FRAC = 0.012   # a side "agrees" if both ends are within this share of height
TR_PEAK_WIN = 8            # px either side used to measure a peak's prominence
TR_PEAK_PROM = 20.0        # grey levels darker than both flanks


def _tr_profiles(gray, p0, p1, D):
    """Darkness profiles across side p0->p1: (TR_SAMPLES, 2D+1), plus sample ts."""
    p0, p1 = np.float32(p0), np.float32(p1)
    u = (p1 - p0) / (float(np.linalg.norm(p1 - p0)) + 1e-9)
    n = np.float32([-u[1], u[0]])
    ts = np.linspace(TR_T_RANGE[0], TR_T_RANGE[1], TR_SAMPLES).astype(np.float32)
    offs = np.arange(-D, D + 1, dtype=np.float32)
    along = np.arange(-TR_STRIP_HALF, TR_STRIP_HALF + 1, dtype=np.float32)
    base = p0[None, :] + ts[:, None] * (p1 - p0)[None, :]
    pts = (base[:, None, None, :] + offs[None, :, None, None] * n
           + along[None, None, :, None] * u)
    mx = pts[..., 0].reshape(TR_SAMPLES, -1).astype(np.float32)
    my = pts[..., 1].reshape(TR_SAMPLES, -1).astype(np.float32)
    vals = cv2.remap(gray, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    vals = vals.reshape(TR_SAMPLES, len(offs), len(along)).astype(np.float32)
    return 255.0 - vals.mean(axis=2), ts


def _tr_candidates(prof):
    """Local darkness maxima darker than BOTH flanks by TR_PEAK_PROM."""
    O = prof.shape[1]
    w = TR_PEAK_WIN
    pad = np.pad(prof, ((0, 0), (w, w)), mode="edge")
    left = np.min(np.stack([pad[:, w - k:w - k + O] for k in range(1, w + 1)]), axis=0)
    right = np.min(np.stack([pad[:, w + k:w + k + O] for k in range(1, w + 1)]), axis=0)
    prom = prof - np.maximum(left, right)
    c = np.zeros_like(prof, bool)
    c[:, 1:-1] = ((prof[:, 1:-1] >= prof[:, :-2]) & (prof[:, 1:-1] > prof[:, 2:])
                  & (prom[:, 1:-1] >= TR_PEAK_PROM))
    return c


def _tr_fit_side(gray, p0, p1, D, near_tol):
    """
    Straight ruling along side p0->p1. Returns (o0, o1, agrees) - the line's
    offset along the side normal at t=0 and t=1, and whether the contour
    side already sits on it - or None when no line is supported.
    """
    prof, ts = _tr_profiles(gray, p0, p1, D)
    cand = _tr_candidates(prof)
    tol = int(np.ceil(TR_HIT_TOL))
    hit = cand.copy()
    for k in range(1, tol + 1):
        hit[:, k:] |= cand[:, :-k]
        hit[:, :-k] |= cand[:, k:]
    O = prof.shape[1]
    grid = np.arange(-D, D + 1, dtype=np.float32)
    A, B = np.meshgrid(grid, grid, indexing="ij")
    pred = A[..., None] + (B - A)[..., None] * ts[None, None, :]
    idx = np.rint(pred).astype(np.int32) + D
    ok = (idx >= 0) & (idx < O)
    np.clip(idx, 0, O - 1, out=idx)
    support = (hit[np.arange(len(ts))[None, None, :], idx] & ok).sum(axis=2) / float(len(ts))
    near = (np.abs(A) <= near_tol) & (np.abs(B) <= near_tol)
    near_sup = np.where(near, support, 0.0)
    agree = bool(near_sup.max() >= TR_MIN_SUPPORT)
    if agree:
        i, j = np.unravel_index(np.argmax(near_sup), near_sup.shape)
    else:
        good = support >= max(TR_MIN_SUPPORT, TR_SUPPORT_REL * support.max())
        if not good.any():
            return None
        dist = np.abs(pred).mean(axis=2)
        dist[~good] = np.inf
        i, j = np.unravel_index(np.argmin(dist), dist.shape)
    # best-supported line in that neighbourhood, then least squares on its peaks
    i0, i1 = max(0, i - 4), min(len(grid), i + 5)
    j0, j1 = max(0, j - 4), min(len(grid), j + 5)
    sub = support[i0:i1, j0:j1]
    di, dj = np.unravel_index(np.argmax(sub), sub.shape)
    a, b = float(grid[i0 + di]), float(grid[j0 + dj])
    tt, oo = [], []
    for s, t in enumerate(ts):
        o_pred = a + (b - a) * t
        cs = np.where(cand[s])[0] - D
        if cs.size:
            k = int(np.argmin(np.abs(cs - o_pred)))
            if abs(cs[k] - o_pred) <= TR_HIT_TOL:
                tt.append(float(t))
                oo.append(float(cs[k]))
    if len(tt) >= 5:
        m, c = np.polyfit(np.array(tt), np.array(oo), 1)
        a, b = float(c), float(c + m)
    return a, b, agree


def _tr_line(p0, p1):
    return np.cross([p0[0], p0[1], 1.0], [p1[0], p1[1], 1.0])


def _tr_meet(l1, l2):
    x = np.cross(l1, l2)
    if abs(x[2]) < 1e-9:
        return None
    return np.float32([x[0] / x[2], x[1] / x[2]])


def _snap_table_quad(paper_img, approx, rough=False):
    """
    Returns `approx` unchanged when every side sits on its ruling (or the
    rulings cannot be found reliably); otherwise a (4,1,2) float32 quad with
    the sides moved onto the table's border rulings.
    """
    gray = cv2.cvtColor(paper_img, cv2.COLOR_BGR2GRAY) if paper_img.ndim == 3 else paper_img
    tl, tr, bl, br = [np.float32(p) for p in reorder_corners(approx)]
    th = 0.5 * (np.linalg.norm(bl - tl) + np.linalg.norm(br - tr))
    D = max(12, int(round((TR_ROUGH_SEARCH_FRAC if rough else TR_SEARCH_FRAC) * th)))
    near_tol = max(4.0, TR_MOVE_TOL_FRAC * th)
    centre = (tl + tr + bl + br) / 4.0
    sides = {"top": (tl, tr), "bottom": (bl, br), "left": (tl, bl), "right": (tr, br)}
    fits = {}
    for name, (p0, p1) in sides.items():
        f = _tr_fit_side(gray, p0, p1, D, near_tol)
        if f is None:
            continue
        d = p1 - p0
        nrm = np.float32([-d[1], d[0]]) / (np.linalg.norm(d) + 1e-9)
        sgn = 1.0 if np.dot((p0 + p1) / 2.0 - centre, nrm) > 0 else -1.0   # + = outward
        fits[name] = (f[0] * sgn, f[1] * sgn, nrm * sgn, f[2])
    if len(fits) < 3:
        return approx
    agreeing = [k for k in fits if fits[k][3]]
    # the contour runs a few px outside the ruling (dilated edges); keep that margin
    margin = float(np.median([-v for k in agreeing for v in fits[k][:2]])) if agreeing else 0.0
    if rough:
        moved_sides = list(fits)
    else:
        moved_sides = [k for k in fits
                       if not fits[k][3] and abs(fits[k][0] - fits[k][1]) > near_tol]
    if not moved_sides:
        return approx
    lines = {}
    for name, (p0, p1) in sides.items():
        if name in fits:
            a, b, nrm, _ = fits[name]
            lines[name] = _tr_line(p0 + nrm * (a + margin), p1 + nrm * (b + margin))
        else:
            lines[name] = _tr_line(p0, p1)
    corners = [_tr_meet(lines["top"], lines["left"]), _tr_meet(lines["top"], lines["right"]),
               _tr_meet(lines["bottom"], lines["left"]), _tr_meet(lines["bottom"], lines["right"])]
    if any(c is None for c in corners):
        return approx
    out = np.float32(corners)
    moved = float(np.max(np.linalg.norm(out - np.float32([tl, tr, bl, br]), axis=1)))
    if moved > 2.5 * D:
        return approx
    print(f"[INFO] findTableContour: snapped table side(s) {', '.join(moved_sides)} onto the "
          f"border ruling (corner moved up to {moved:.0f} px).")
    return out.reshape(4, 1, 2)


# PATCH (fix B) — table-quad repair helpers used by findTableContour().
# The printed table is a rectangle, but a phone photo whose page edge was
# only roughly found leaves it as a genuine trapezoid (keystone), which the
# perspective warp handles correctly and must NOT be "repaired". A corner
# dragged off the border by ink is a much bigger distortion. Measured:
#   phone photos (47)    : worst corner <= 2.8 deg, parallelogram residual <= 5.8% of table height
#   crossed-border scan  : worst corner    8.1 deg, residual 21% of table height
# Repair only when BOTH limits are exceeded, well clear of the phone range.
TABLE_CORNER_TOL_DEG = 4.0
TABLE_QUAD_MIN_RESIDUAL = 0.12   # |tl + br - tr - bl| / shorter side edge length


def _quad_corner_errors(q):
    """|angle - 90| in degrees at each corner of a reorder_corners() quad (tl, tr, bl, br)."""
    tl, tr, bl, br = q
    ring = [tl, tr, br, bl]
    errs = []
    for i in range(4):
        p, a, b = ring[i], ring[i - 1], ring[(i + 1) % 4]
        v1, v2 = a - p, b - p
        c = float(np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2) + 1e-9))
        errs.append(abs(np.degrees(np.arccos(np.clip(c, -1.0, 1.0))) - 90.0))
    return errs


def _repair_table_quad(approx, tol_deg=TABLE_CORNER_TOL_DEG,
                       min_residual=TABLE_QUAD_MIN_RESIDUAL):
    """
    If one corner is far off (worst corner angle >= tol_deg AND the
    parallelogram residual >= min_residual of the table's side height),
    rebuild each corner in turn as the parallelogram completion of the
    other three and keep the most rectangular result — only if it is
    better than the original. Anything milder is returned unchanged.
    """
    q = reorder_corners(approx)
    worst = max(_quad_corner_errors(q))
    tl, tr, bl, br = q
    side_h = min(np.linalg.norm(bl - tl), np.linalg.norm(br - tr)) + 1e-9
    residual = float(np.linalg.norm(tl + br - tr - bl)) / side_h
    if worst < tol_deg or residual < min_residual:
        return approx
    cands = [
        (tr + bl - br, tr, bl, br),
        (tl, tl + br - bl, bl, br),
        (tl, tr, tl + br - tr, br),
        (tl, tr, bl, tr + bl - tl),
    ]
    best = min(cands, key=lambda c: max(_quad_corner_errors(np.array(c))))
    best_err = max(_quad_corner_errors(np.array(best)))
    if best_err >= worst:
        return approx
    print(f"[INFO] findTableContour: repaired a skewed table corner "
          f"({worst:.1f} -> {best_err:.1f} deg off square).")
    return np.array(best, np.float32).reshape(4, 1, 2)


def _table_quad_from_extremes(paper_img):
    """
    Fallback when no 4-vertex table contour exists: take the largest contour
    whose bounding box passes the same size/position filters as
    findTableContour() and use its extreme points (min/max of x+y and y-x)
    as a rough quad; _repair_table_quad() then squares it up.
    """
    H, W = paper_img.shape[:2]
    BORDER_MARGIN = 0.03
    for lo, hi, dil in [(15, 75, 1), (10, 60, 2)]:
        e = _edge_map(paper_img, lo, hi, dil)
        cnts, _ = cv2.findContours(e, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        best, best_area = None, 0
        for c in cnts:
            x, y, w, h = cv2.boundingRect(c)
            if w < W * 0.55 or h < H * 0.20 or h > H * 0.75:
                continue
            cy = y + h / 2.0
            if cy < H * 0.25 or cy > H * 0.95:
                continue
            if (x <= W * BORDER_MARGIN or y <= H * BORDER_MARGIN or
                    (x + w) >= W * (1 - BORDER_MARGIN) or
                    (y + h) >= H * (1 - BORDER_MARGIN)):
                continue
            if w * h > best_area:
                best_area, best = w * h, c
        if best is not None:
            pts = best.reshape(-1, 2).astype(np.float32)
            s, d = pts.sum(axis=1), pts[:, 1] - pts[:, 0]
            quad = np.array([pts[np.argmin(s)], pts[np.argmin(d)],
                             pts[np.argmax(d)], pts[np.argmax(s)]], np.float32)
            return quad.reshape(4, 1, 2)
    return None


def warpTable(img, corners, target_w, target_h):
    pts    = reorder_corners(corners).astype(np.float32)
    dst    = np.float32([[0, 0], [target_w, 0], [0, target_h], [target_w, target_h]])
    M      = cv2.getPerspectiveTransform(pts, dst)
    warped = cv2.warpPerspective(img, M, (target_w, target_h))
    return warped, M


def extract_table_region(paper_img, target_w, target_h,
                          top_frac=0.38, bot_frac=0.92):
    H, W  = paper_img.shape[:2]
    strip = paper_img[int(H * top_frac):int(H * bot_frac), :]
    return cv2.resize(strip, (target_w, target_h))


def draw_table_detection(paper_img, corners):
    vis = paper_img.copy()
    if corners is not None:
        ordered = reorder_corners(corners).astype(np.int32)
        draw_order = ordered[[0, 1, 3, 2]]
        cv2.polylines(vis, [draw_order.reshape(-1, 1, 2)], True, (0, 0, 255), 3)
        for i, pt in enumerate(ordered):
            cv2.circle(vis, tuple(pt), 10, (0, 0, 255), -1)
            cv2.putText(vis, ['TL','TR','BL','BR'][i],
                        (pt[0]+8, pt[1]+8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 0, 255), 2)
    else:
        cv2.putText(vis, "TABLE: using fallback estimate",
                    (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 80, 255), 2)
    return vis


def _group_line_positions(idxs, gap_tol=3):
    groups = []
    cur = [int(idxs[0])]
    for v in idxs[1:]:
        if v - cur[-1] <= gap_tol:
            cur.append(int(v))
        else:
            groups.append(int(np.mean(cur)))
            cur = [int(v)]
    groups.append(int(np.mean(cur)))
    return groups


def detectTableGrid(gray, expected_rows=20, expected_cols=5, line_thresh=0.6):
    """
    Detects horizontal and vertical ruling lines from the morphologically-
    processed warped table image.

    line_thresh (default 0.6): fraction of the strongest column line's
    pixel-coverage a candidate column line must reach. Kept high to
    prevent stacked diagonal checkmarks from being mistaken for a real
    ruling line. Row lines use a lower adaptive threshold since they are
    thinner and may be faint in phone photos.

    Detection cascades through multiple row-line threshold levels so that
    faint or lightly-printed rulings are recovered before giving up.
    Column count is required to be exactly expected_cols+1.

    Row detection is STRUCTURE-aware rather than pure gap-uniformity: this
    form is always "10 Management rows, then a 'II. Performance' section
    header, then 10 Performance rows". The header row creates a big gap
    in the ruling-line spacing — but so does any row whose label text
    happens to wrap to two lines (this form has at least two of those in
    circulation). Both look like "a bigger-than-usual gap" to a pure
    threshold, so gap size alone can't tell "skip this, it's a header"
    from "keep this, it's just a tall row". Instead we split the
    candidate lines into two fixed-size chunks (11 lines -> 10 rows each)
    and only pair lines for a row WITHIN a chunk — the gap between chunks
    is never turned into a row no matter how big or small it is, and a
    wrapped-text row's oversized gap is trusted as long as it falls
    inside a chunk.
    """
    h, w = gray.shape[:2]

    # CLAHE contrast boost before thresholding — same reasoning as
    # binarize_table_with_baseline() for OMR marks: phone photos vary a
    # lot in lighting/exposure, and a plain global Otsu on the raw image
    # can lose faint printed ruling lines entirely on darker/lower-
    # contrast photos even though the lines are clearly visible to the
    # eye. Boosting local contrast first recovers them.
    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    _, bw = cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    # Horizontal kernel: must span at least 40% of width (table rulings run
    # nearly edge-to-edge; a shorter kernel picks up checkmark strokes too).
    # Vertical kernel: must span at least 12% of height.
    horiz_k = cv2.getStructuringElement(cv2.MORPH_RECT, (max(30, w * 2 // 5), 1))
    vert_k  = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(20, h // 12)))
    horiz   = cv2.morphologyEx(bw, cv2.MORPH_OPEN, horiz_k)
    vert    = cv2.morphologyEx(bw, cv2.MORPH_OPEN, vert_k)

    row_proj = horiz.sum(axis=1)
    col_proj = vert.sum(axis=0)

    if row_proj.max() == 0 or col_proj.max() == 0:
        return None

    # ── Column lines (strict threshold — keeps stacked checkmarks out) ─────
    cols_present = np.where(col_proj > col_proj.max() * line_thresh)[0]
    if len(cols_present) == 0:
        return None
    col_lines_all = _group_line_positions(cols_present)
    col_lines = [c for c in col_lines_all if c > w * 0.45]
    if len(col_lines) != expected_cols + 1:
        return None

    # Section hypotheses to try, in order. (10, 10) matches this form's
    # real layout (Management header splits it into two 10-row halves);
    # (expected_rows,) is kept as a fallback for a hypothetical form
    # printed with no internal section header at all.
    section_hypotheses = [(10, 10), (expected_rows,)]
    section_hypotheses = [s for s in section_hypotheses if sum(s) == expected_rows]

    MIN_ROW_H, MAX_ROW_H = 15, 150  # sanity bounds in warped-table pixels

    def try_section_sizes(row_lines, section_sizes):
        needed = sum(section_sizes) + len(section_sizes)
        if len(row_lines) < needed:
            return None
        extra = len(row_lines) - needed
        # Try offsets that trim the most-symmetric amount off each end
        # first (leading/trailing junk lines — table borders, stray marks
        # below the table — are the usual reason there are more candidate
        # lines than needed).
        offsets = sorted(range(extra + 1), key=lambda o: abs(o - extra / 2))
        for offset in offsets:
            window = row_lines[offset: offset + needed]
            y1s, y2s = [], []
            ok = True
            pos = 0
            for size in section_sizes:
                chunk = window[pos: pos + size + 1]
                pos += size + 1
                for i in range(size):
                    height = chunk[i + 1] - chunk[i]
                    if height < MIN_ROW_H or height > MAX_ROW_H:
                        ok = False
                        break
                    y1s.append(chunk[i])
                    y2s.append(chunk[i + 1])
                if not ok:
                    break
            if ok and len(y1s) == expected_rows:
                return y1s, y2s
        return None

    # ── Row lines: cascade through thresholds, widened for faint rulings ──
    result = None
    for row_thresh in (0.30, 0.20, 0.12, 0.08, 0.05):
        rows_present = np.where(row_proj > row_proj.max() * row_thresh)[0]
        if len(rows_present) == 0:
            continue
        row_lines = _group_line_positions(rows_present)

        for section_sizes in section_hypotheses:
            found = try_section_sizes(row_lines, section_sizes)
            if found:
                good_y1, good_y2 = found
                result = (good_y1, good_y2, col_lines)
                break
        if result:
            break

    return result


# ─────────────────────────────────────────────────────────
#  OMR  —  CHECKMARK DETECTION
# ─────────────────────────────────────────────────────────

def binarize_table(gray):
    """
    Simple global Otsu binarization — kept for backward compatibility.
    Prefer binarize_table_with_baseline() for better accuracy.
    """
    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    return bw


def binarize_table_with_baseline(gray, col_strip_start_frac=0.68):
    """
    Improved binarization: CLAHE + rating-columns-only Otsu.

    The key insight: global Otsu is contaminated by the printed form text
    (row labels, header rows) that occupies the LEFT 65 % of the warped
    table.  By computing the Otsu threshold ONLY from the right 32 % of
    the image (the actual rating columns: O/VS/S/F/US), we get a threshold
    calibrated to the ink level of the rating area only.  This prevents
    printed header text from driving the threshold up and causing the
    O-column cells to score high when they contain only form text.

    Steps
    -----
    1. CLAHE (clipLimit=3, tile 4×4) — boost local contrast
    2. Compute Otsu threshold from the rating-column strip only
       (right 32 % of the warped table by default)
    3. Apply that threshold to the WHOLE image (needed for row detection)
    4. OR with a mild adaptive threshold to rescue faint marks that the
       column-calibrated Otsu might still miss

    Parameters
    ----------
    gray               : warped table grayscale image
    col_strip_start_frac: x-fraction where rating columns begin
                          (default 0.68 ≈ where O column starts)

    Returns
    -------
    clean_bw : ndarray uint8, same shape as gray
        Binarized image: student checkmarks = 255, background/text = 0.
        Pass as `table_bw` to detectMarkInRow().
    """
    h, w = gray.shape[:2]

    # ── Step 1: CLAHE contrast boost ──────────────────────────────────
    # CLAHE equalizes local contrast, making faint pencil/ink checkmarks
    # stand out relative to their immediate background.  clipLimit=2.5
    # prevents over-amplification of noise.
    clahe    = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)

    # ── Step 2: Global Otsu binarize on CLAHE-enhanced image ──────────
    # Otsu finds the optimal threshold to separate ink from background.
    # Applied AFTER CLAHE so faint marks are already boosted.
    _, bw = cv2.threshold(enhanced, 0, 255,
                           cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    return bw


def detectMarkInRow(gray, y1, y2, col_xs,
                    min_ink_ratio=0.015, min_separation=1.4,
                    table_bw=None, return_scores=False,
                    top_margin_frac=0.18):
    """
    Detect which rating column (0-4) has a checkmark in the given row band.

    Parameters
    ----------
    gray          : full warped-table grayscale image
    y1, y2        : row band top / bottom pixel coordinates
    col_xs        : list of 6 x-coordinates bounding the 5 rating columns
    min_ink_ratio : minimum ink density a cell must have to be considered
    min_separation: best column score must be >= this multiple of 2nd-best
                    to avoid reporting an ambiguous result as answered
    table_bw      : pre-binarized whole-table image (ink=255) from
                    binarize_table_with_baseline() or binarize_table().
                    Falls back to per-row Otsu if None.
    return_scores : if True returns (best_col, [score_per_col])
    top_margin_frac: fraction of cell height to trim from the TOP of each
                    cell before scoring.  Removes ink from printed form
                    header text that sits at the top of a cell band.
                    Default 0.18 (18 %) is conservative — just the border.
    """
    n_cols = len(col_xs) - 1
    H      = gray.shape[0]
    y1, y2 = max(0, y1), min(H, y2)
    cell_h = y2 - y1
    if cell_h < 3:
        return (-1, []) if return_scores else -1

    # Vertical margins: top_margin_frac from top, 1/6 from bottom
    my_top = max(1, int(cell_h * top_margin_frac))
    my_bot = max(1, cell_h // 6)
    x_lo, x_hi = int(col_xs[0]), int(col_xs[-1])
    yy1 = y1 + my_top
    yy2 = max(yy1 + 1, y2 - my_bot)

    if table_bw is not None:
        ink = table_bw[yy1:yy2, x_lo:x_hi]
    else:
        strip = gray[yy1:yy2, x_lo:x_hi]
        if strip.size == 0:
            return (-1, []) if return_scores else -1
        _, ink = cv2.threshold(strip, 0, 255,
                                cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    if ink.size == 0:
        return (-1, []) if return_scores else -1

    scores = []
    for c in range(n_cols):
        x1c = int(col_xs[c])     - x_lo
        x2c = int(col_xs[c + 1]) - x_lo
        mx  = max(3, (x2c - x1c) // 4)   # horizontal border margin
        cell = ink[:, x1c + mx: x2c - mx]
        scores.append(float(np.mean(cell > 0)) if cell.size else 0.0)

    if not scores:
        return (-1, []) if return_scores else -1

    order  = np.argsort(scores)[::-1]
    best_c = int(order[0])
    best_v = scores[best_c]

    if best_v < min_ink_ratio:
        best_c = -1
    else:
        second_v = scores[int(order[1])] if n_cols > 1 else 0.0
        # Adaptive separation: use a tighter threshold for faint marks
        # (best score < 0.15) to avoid rejecting valid-but-weak marks.
        # For strong clear marks (best > 0.15) keep the strict 1.4 separation.
        effective_sep = min_separation if best_v >= 0.15 else max(1.15, min_separation * 0.85)
        if second_v > 0 and best_v < second_v * effective_sep:
            best_c = -1

    return (best_c, scores) if return_scores else best_c


# ─────────────────────────────────────────────────────────
#  OCR  —  FIELD EXTRACTION  (EasyOCR only — no VLM)
# ─────────────────────────────────────────────────────────
_FIELD_REGIONS = {
    # instructor: left boundary moved back LEFT (0.290 → 0.260) — the
    # 0.290 crop was clipping the first 1-2 letters of the handwritten
    # name itself (e.g. "Rodz" was coming through as "...ndz"). If the
    # printed "Professor:" label starts creeping back into the crop at
    # 0.260, nudge this a touch right rather than all the way back.
    'instructor':  (0.260, 0.415, 0.635, 0.440),
    'subject':     (0.206, 0.441, 0.412, 0.461),
    'ay_semester': (0.515, 0.441, 0.676, 0.461),
    # remarks: top moved DOWN (0.778→0.782) to skip the section-header row;
    # right boundary moved LEFT (0.912→0.700) to exclude the empty rating-column
    # cells whose vertical borders were creating false text detections
    'remarks':     (0.088, 0.782, 0.700, 0.825),
    'date':        (0.126, 0.839, 0.353, 0.855),
    'student_id':  (0.165, 0.855, 0.353, 0.874),
    'signature':   (0.159, 0.872, 0.382, 0.895),
}


def _crop_rel(img, x1f, y1f, x2f, y2f):
    H, W = img.shape[:2]
    c = img[int(y1f * H): int(y2f * H),
            int(x1f * W): int(x2f * W)]
    return c if c.size > 0 else None


def _prepare_for_ocr(crop, target_h=200, max_w=900):
    """
    Upscale → Otsu binarize → remove horizontal rule lines.
    Returns black ink on white background — the standard input shape
    EasyOCR does best with.

    max_w caps the output width so a wide field crop (e.g. instructor,
    which spans a big chunk of the page horizontally) scaled up to a
    tall target_h doesn't produce an oversized image.
    """
    if crop is None or crop.size == 0:
        return None
    oh, ow = crop.shape[:2]
    if oh == 0 or ow == 0:
        return None

    scale = target_h / oh
    new_w = min(max_w, max(1, int(ow * scale)))
    # Recompute height from the (possibly capped) width so we don't
    # distort the aspect ratio when max_w kicks in.
    new_h = max(1, int(new_w * oh / ow)) if new_w < ow * scale else target_h
    big   = cv2.resize(crop, (new_w, new_h), interpolation=cv2.INTER_CUBIC)
    h, w  = big.shape[:2]

    gray = cv2.cvtColor(big, cv2.COLOR_BGR2GRAY) if len(big.shape) == 3 else big.copy()

    # Otsu: ink → white (255), paper/bleed-through → black (0)
    _, binary = cv2.threshold(gray, 0, 255,
                               cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    # Isolate long horizontal rule lines (must span ≥ 25 % of image width)
    horiz_len = max(w // 4, 40)
    horiz_k   = cv2.getStructuringElement(cv2.MORPH_RECT, (horiz_len, 1))
    h_lines   = cv2.morphologyEx(binary, cv2.MORPH_OPEN, horiz_k)
    # Dilate 2 px vertically so pixels that sit right on the line are also removed
    h_lines   = cv2.dilate(h_lines, np.ones((3, 1), np.uint8), iterations=1)

    # Subtract rule lines — handwriting survives, printed underlines disappear
    clean = cv2.subtract(binary, h_lines)

    # Invert: black ink on white background (standard for EasyOCR)
    return cv2.bitwise_not(clean)


def _ocr_crop(crop, reader, target_h=200, field_key=None, _attempt=1):
    """
    Reads a field crop with EasyOCR directly (no VLM / Ollama layer).
    Binarizes the crop (_prepare_for_ocr), runs reader.readtext(), and
    — if the result comes back empty on the first try — retries once
    at a larger target height, since EasyOCR's accuracy on thin
    handwriting strips is sensitive to how tall the resized crop is.
    """
    if crop is None or crop.size == 0:
        return ""

    MAX_ATTEMPTS = 2
    prepped = _prepare_for_ocr(crop, target_h=target_h)
    if prepped is None:
        return ""

    try:
        result = reader.readtext(prepped, detail=0, paragraph=True)
        text = " ".join(result).strip()

        if not text and _attempt < MAX_ATTEMPTS:
            print(f"    [{field_key}] attempt {_attempt} empty, retrying (attempt {_attempt + 1})")
            return _ocr_crop(crop, reader, target_h=target_h + 120,
                              field_key=field_key, _attempt=_attempt + 1)
        return text
    except Exception as exc:
        print(f"    [EasyOCR ERR - {field_key}] {exc}")
        return ""


def _ocr_student_id(crop, reader, target_h=200, field_key='student_id', _attempt=1):
    """
    Same as _ocr_crop, but restricted to a digit allowlist since the
    field is a numeric student ID. EasyOCR supports allowlist directly
    (readtext(..., allowlist=...)), so this is simpler and more
    reliable than asking a VLM to "only reply with digits."
    """
    if crop is None or crop.size == 0:
        return ""

    MAX_ATTEMPTS = 2
    prepped = _prepare_for_ocr(crop, target_h=target_h)
    if prepped is None:
        return ""

    try:
        result = reader.readtext(prepped, detail=0, paragraph=True,
                                  allowlist="0123456789")
        text = " ".join(result).strip()

        if not text and _attempt < MAX_ATTEMPTS:
            print(f"    [student_id] attempt {_attempt} empty, retrying (attempt {_attempt + 1})")
            return _ocr_student_id(crop, reader, target_h=target_h + 120,
                                    field_key=field_key, _attempt=_attempt + 1)
        return text
    except Exception as exc:
        print(f"    [EasyOCR ERR - student_id] {exc}")
        return ""


def _digits_only(text):
    if not text:
        return ""

    stripped = text.replace(" ", "").replace("-", "").replace(":", "")
    if not stripped:
        return ""

    digit_lookalikes = set("OoQDIlLi|ZzEASsGbTBgq0123456789")
    lookalike_count   = sum(1 for ch in stripped if ch in digit_lookalikes)
    if lookalike_count / len(stripped) < 0.7:
        return ""

    swap = {
        'O': '0', 'o': '0', 'Q': '0', 'D': '0',
        'I': '1', 'l': '1', 'i': '1', '|': '1', 'L': '1',
        'Z': '2', 'z': '2',
        'E': '3',
        'A': '4',
        'S': '5', 's': '5',
        'G': '6', 'b': '6',
        'T': '7',
        'B': '8',
        'g': '9', 'q': '9',
    }
    corrected = ''.join(swap.get(ch, ch) for ch in text)
    digits    = ''.join(ch for ch in corrected if ch.isdigit())
    return digits


def _ink_present(crop, ink_threshold=0.008):
    if crop is None or crop.size == 0:
        return False
    g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if len(crop.shape) == 3 else crop
    _, bw = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    return np.sum(bw > 0) / bw.size > ink_threshold


_signature_present = _ink_present


def _crop_to_base64_png(crop):
    """
    Encodes a crop (color or grayscale numpy array) as a base64 PNG
    string. Kept for backward compatibility with main.py's debug-image
    handling and api.py's JSON response shape — these are now just
    reference/debug images, not something forwarded to any AI.
    """
    if crop is None or crop.size == 0:
        return None
    ok, buf = cv2.imencode(".png", crop)
    if not ok:
        return None
    return base64.b64encode(buf.tobytes()).decode("ascii")


# Labels + order used when stacking the "main fields" composite.
_MAIN_FIELD_ORDER = [
    ('instructor',  'Instructor / Professor'),
    ('subject',     'Subject Taught'),
    ('ay_semester', 'AY / Semester'),
    ('remarks',     'Remarks'),
    ('date',        'Date'),
    ('student_id',  'Student ID'),
]


def _build_labeled_composite(field_crops, composite_width=900, label_h=34,
                              pad=6, bg=(255, 255, 255)):
    """
    Stacks several field crops into ONE image, each with a small
    printed label above it. Kept as a convenience/debug view — no
    longer intended to be fed to a downstream AI.
    """
    rows = []
    for key, label in _MAIN_FIELD_ORDER:
        crop = field_crops.get(key)
        if crop is None or crop.size == 0:
            continue

        label_bar = np.full((label_h, composite_width, 3), bg, dtype=np.uint8)
        cv2.putText(label_bar, label, (pad, label_h - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (20, 20, 20), 1, cv2.LINE_AA)

        ch, cw = crop.shape[:2]
        scale = composite_width / cw
        resized = cv2.resize(crop, (composite_width, max(1, int(ch * scale))),
                              interpolation=cv2.INTER_CUBIC)
        if len(resized.shape) == 2:
            resized = cv2.cvtColor(resized, cv2.COLOR_GRAY2BGR)

        divider = np.full((2, composite_width, 3), (200, 200, 200), dtype=np.uint8)

        rows.append(label_bar)
        rows.append(resized)
        rows.append(divider)

    if not rows:
        return None
    return np.vstack(rows)


def build_main_fields_crop_b64(paper_bgr):
    """
    Crops instructor/subject/ay_semester/remarks/date/student_id
    straight from the rectified paper and stacks them into one labeled
    composite image, returned as a base64 PNG. Purely a crop+compose
    debug helper now — not fed to any AI.
    """
    field_crops = {
        key: _crop_rel(paper_bgr, *_FIELD_REGIONS[key])
        for key, _ in _MAIN_FIELD_ORDER
    }
    composite = _build_labeled_composite(field_crops)
    return _crop_to_base64_png(composite)


# ─────────────────────────────────────────────────────────
#  n8n AI OCR CROP  —  high-quality composite for AI OCR
# ─────────────────────────────────────────────────────────

# Fields included in the n8n OCR image and their display labels.
# Signature is excluded (it's a presence check, not OCR text).
_N8N_FIELD_ORDER = [
    ('instructor',  'Instructor / Professor'),
    ('subject',     'Subject Taught'),
    ('ay_semester', 'AY / Semester'),
    ('date',        'Date'),
    ('student_id',  'Student ID'),
    ('remarks',     'Remarks & Suggestions'),
]


def build_n8n_ocr_crop(paper_bgr, out_path=None, target_field_h=120,
                        pad=10, label_h=28, composite_w=1000):
    """
    Build a single high-quality JPEG composite image of all handwritten
    field crops for n8n AI OCR processing.

    Layout (top to bottom):
      ┌─────────────────────────────────────────┐
      │  [HEADER BANNER]  "FIELDS FOR AI OCR"  │
      ├─────────────────────────────────────────┤
      │  label: "Instructor / Professor"        │
      │  [ raw colour crop, scaled ]            │
      │  [ binarized / cleaned crop ]           │
      ├─────────────────────────────────────────┤
      │  label: "Subject Taught"                │
      │  [ raw colour crop ]                    │
      │  [ binarized ]                          │
      ├── ... repeat for each field ────────────┤
      └─────────────────────────────────────────┘

    Parameters
    ----------
    paper_bgr     : rectified paper image (output of isolate_paper)
    out_path      : if given, saves the composite as a JPEG to this path
    target_field_h: pixel height each field crop is scaled to
    pad           : vertical padding (px) between fields
    label_h       : height of the label bar above each field
    composite_w   : total width of the output image

    Returns
    -------
    composite : ndarray (BGR), the assembled image
    """
    BG        = (245, 245, 245)   # light gray background
    LABEL_CLR = (30,  30,  30)    # near-black text
    HDR_BG    = (30,  60, 120)    # dark blue header
    HDR_CLR   = (255, 255, 255)   # white header text
    SEP_CLR   = (180, 180, 180)   # separator line color

    rows = []

    # ── Header banner ──────────────────────────────────────────────────
    hdr = np.full((50, composite_w, 3), HDR_BG, dtype=np.uint8)
    cv2.putText(hdr, "HANDWRITTEN FIELDS -- FOR AI OCR",
                (pad, 33), cv2.FONT_HERSHEY_DUPLEX, 0.75, HDR_CLR, 1,
                cv2.LINE_AA)
    rows.append(hdr)
    rows.append(np.full((2, composite_w, 3), SEP_CLR, dtype=np.uint8))

    for key, label in _N8N_FIELD_ORDER:
        region = _FIELD_REGIONS.get(key)
        if region is None:
            continue
        raw_crop = _crop_rel(paper_bgr, *region)
        if raw_crop is None or raw_crop.size == 0:
            continue

        # ── Label bar ──────────────────────────────────────────────────
        lbar = np.full((label_h, composite_w, 3), BG, dtype=np.uint8)
        cv2.putText(lbar, label, (pad, label_h - 7),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.52, (0, 80, 180), 1,
                    cv2.LINE_AA)
        rows.append(lbar)

        # ── Raw colour crop (scaled) ───────────────────────────────────
        oh, ow = raw_crop.shape[:2]
        scale  = target_field_h / max(oh, 1)
        rw     = min(composite_w - 2 * pad, max(1, int(ow * scale)))
        rh     = max(1, int(oh * scale))
        resized = cv2.resize(raw_crop, (rw, rh), interpolation=cv2.INTER_CUBIC)

        raw_row = np.full((rh, composite_w, 3), BG, dtype=np.uint8)
        raw_row[:rh, pad:pad + rw] = resized
        # small label tag
        cv2.putText(raw_row, "RAW", (composite_w - 55, rh - 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, (120, 120, 120), 1)
        rows.append(raw_row)

        # ── Binarized / cleaned crop (for OCR) ────────────────────────
        prepped = _prepare_for_ocr(raw_crop, target_h=target_field_h,
                                    max_w=composite_w - 2 * pad)
        if prepped is not None:
            if len(prepped.shape) == 2:
                prepped = cv2.cvtColor(prepped, cv2.COLOR_GRAY2BGR)
            bh, bw2 = prepped.shape[:2]
            bin_row = np.full((bh, composite_w, 3), BG, dtype=np.uint8)
            bin_row[:bh, pad:pad + bw2] = prepped
            cv2.putText(bin_row, "CLEAN (OCR)", (composite_w - 110, bh - 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.38, (0, 130, 60), 1)
            rows.append(bin_row)

        # ── Separator ──────────────────────────────────────────────────
        rows.append(np.full((pad, composite_w, 3), BG, dtype=np.uint8))
        rows.append(np.full((1, composite_w, 3), SEP_CLR, dtype=np.uint8))

    if not rows:
        return None

    composite = np.vstack(rows)

    if out_path:
        cv2.imwrite(out_path, composite,
                    [cv2.IMWRITE_JPEG_QUALITY, 92])

    return composite


def extract_fields_from_paper(paper_bgr, ocr_reader):
    out    = {}
    crops  = {}
    images = {}  # base64 PNGs per field, kept for debug/reference

    TEXT_KEYS      = ('instructor', 'subject', 'remarks', 'student_id')
    PRESENCE_KEYS  = ('ay_semester', 'date', 'signature')
    ALSO_TRY_TEXT  = ('ay_semester', 'date')

    for key in TEXT_KEYS:
        crop       = _crop_rel(paper_bgr, *_FIELD_REGIONS[key])
        crops[key] = crop
        images[key] = _crop_to_base64_png(crop)
        print(f"    [{key:<15}] ", end='', flush=True)

        if key == 'student_id':
            text    = _ocr_student_id(crop, ocr_reader, field_key=key)
            cleaned = _digits_only(text)
            print(f"→  raw '{text}'  →  digits '{cleaned}'")
            out[key] = cleaned
        else:
            text     = _ocr_crop(crop, ocr_reader, field_key=key)
            out[key] = text
            print(f"→  '{text}'")

    for key in PRESENCE_KEYS:
        crop        = _crop_rel(paper_bgr, *_FIELD_REGIONS[key])
        crops[key]  = crop
        images[key] = _crop_to_base64_png(crop)
        present     = _ink_present(crop)
        out[key]    = present
        print(f"    [{key:<15}] →  {'PRESENT ✓' if present else 'none'}")

        if key in ALSO_TRY_TEXT:
            text = _ocr_crop(crop, ocr_reader, field_key=key)
            out[f"{key}_text"] = text
            print(f"    [{key + '_text':<15}] →  '{text}'")

    out['crops']  = crops   # numpy arrays — for local debug image saving only
    out['field_images'] = images  # base64 PNGs — safe to put in JSON/API responses
    out['main_fields_image'] = build_main_fields_crop_b64(paper_bgr)

    return out


# ─────────────────────────────────────────────────────────
#  VISUALISATION
# ─────────────────────────────────────────────────────────

def draw_field_overlay(paper_bgr, ocr_fields):
    vis = paper_bgr.copy()
    H, W = vis.shape[:2]

    meta = {
        'instructor':  ('Instructor',  (0,   200, 255)),
        'subject':     ('Subject',     (0,   255, 100)),
        'ay_semester': ('AY / Sem',    (255, 200,   0)),
        'remarks':     ('Remarks',     (180,  80, 255)),
        'date':        ('Date',        (50,  255, 200)),
        'student_id':  ('Student ID',  (255, 140,  30)),
        'signature':   ('Signature',   (40,  130, 255)),
    }

    for key, (label, clr) in meta.items():
        if key not in _FIELD_REGIONS:
            continue
        x1f, y1f, x2f, y2f = _FIELD_REGIONS[key]
        x1, y1 = int(x1f * W), int(y1f * H)
        x2, y2 = int(x2f * W), int(y2f * H)

        cv2.rectangle(vis, (x1, y1), (x2, y2), clr, 2)

        val = ocr_fields.get(key, '')
        val = ('SIGNED' if val else '—') if isinstance(val, bool) else str(val)
        disp = f"{label}: {val[:30]}"

        (tw, th), _ = cv2.getTextSize(disp, cv2.FONT_HERSHEY_SIMPLEX, 0.44, 1)
        cv2.rectangle(vis, (x1, y1 - th - 6), (x1 + tw + 4, y1), clr, cv2.FILLED)
        cv2.putText(vis, disp, (x1 + 2, y1 - 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.44, (0, 0, 0), 1)

    scale = min(1.0, 900 / max(H, W))
    if scale < 1.0:
        vis = cv2.resize(vis, (int(W * scale), int(H * scale)))
    return vis


# ─────────────────────────────────────────────────────────
#  LEGACY HELPERS  (kept for backward-compatibility)
# ─────────────────────────────────────────────────────────

def stackImages(imgArray, scale, lables=[]):
    rows   = len(imgArray)
    cols   = len(imgArray[0])
    rowsAv = isinstance(imgArray[0], list)
    width  = imgArray[0][0].shape[1]
    height = imgArray[0][0].shape[0]

    if rowsAv:
        for x in range(rows):
            for y in range(cols):
                imgArray[x][y] = cv2.resize(imgArray[x][y], (0, 0), None, scale, scale)
                if len(imgArray[x][y].shape) == 2:
                    imgArray[x][y] = cv2.cvtColor(imgArray[x][y], cv2.COLOR_GRAY2BGR)
        ver = np.vstack([np.hstack(imgArray[x]) for x in range(rows)])
    else:
        for x in range(rows):
            imgArray[x] = cv2.resize(imgArray[x], (0, 0), None, scale, scale)
            if len(imgArray[x].shape) == 2:
                imgArray[x] = cv2.cvtColor(imgArray[x], cv2.COLOR_GRAY2BGR)
        ver = np.hstack(imgArray)

    if lables:
        eW = int(ver.shape[1] / cols)
        eH = int(ver.shape[0] / rows)
        for d in range(rows):
            for c in range(cols):
                txt = lables[d][c]
                cv2.rectangle(ver,
                              (c * eW, eH * d),
                              (c * eW + len(txt) * 13 + 27, 30 + eH * d),
                              (255, 255, 255), cv2.FILLED)
                cv2.putText(ver, txt, (eW * c + 10, eH * d + 20),
                            cv2.FONT_HERSHEY_COMPLEX, 0.7, (255, 0, 255), 2)
    return ver


def rectContour(contours):
    rectCon = []
    for c in contours:
        a = cv2.contourArea(c)
        if a > 50:
            p  = cv2.arcLength(c, True)
            ap = cv2.approxPolyDP(c, 0.02 * p, True)
            if len(ap) == 4:
                rectCon.append(c)
    return sorted(rectCon, key=cv2.contourArea, reverse=True)


def getCornerPoints(cont):
    p  = cv2.arcLength(cont, True)
    return cv2.approxPolyDP(cont, 0.02 * p, True)


reorder = reorder_corners