"""
mark_detect.py - decide which rating column a row's checkmark sits in.

WHY THIS EXISTS
----------------
The original detectMarkInRow() scored a cell by its ink fraction in a
whole-table binarization (CLAHE + one global Otsu). Two things went
wrong with that:

1. The global Otsu threshold is set by the printed label text that fills
   the left ~70% of the table, not by the pencil marks in the rating
   columns, so faint marks fell below it and the row scored as "blank".
   On the 2026-09-05 dataset that produced 207 spurious "blank"
   predictions out of 312 errors - even though only 4 of 1320 labeled
   rows are genuinely blank.
2. The cell's own ruling lines count as ink, which adds a near-constant
   floor to every cell and shrinks the relative gap between the marked
   column and its neighbours.

This module binarizes the rating block ON ITS OWN (so the threshold is
set by pencil-vs-paper, not by printed text), strips the ruling lines
morphologically, and then scores cells. Because the form is a forced
single choice, the winner is the argmax rather than a cell that has to
clear an absolute ink threshold; "blank" is reserved for rows with
essentially no ink at all.
"""
import cv2
import numpy as np

# A row whose best column has less ink than this is treated as unmarked.
# Set well below the lightest real pencil mark in the labeled corpus -
# genuine blanks are ~0.1% of rows, so the cost of guessing on a truly
# empty row is far lower than the cost of dropping a real faint mark.
BLANK_INK_RATIO = 0.004

# Fraction of cell width/height trimmed before scoring, to keep the
# ruling lines and the neighbouring columns' ink out of the cell.
# Chosen from the middle of a broad plateau (a 5x4x4 sweep over the
# labeled corpus moves row accuracy only between 97.8% and 99.1%), not
# from its single best point, so the values are not fitted to individual
# sheets.
X_MARGIN_FRAC = 0.14
# The bottom trim is deliberately much smaller than the top one: raters
# routinely draw a tick low enough to sit on their row's bottom ruling,
# and trimming the old 14% from the bottom deleted those marks outright,
# scoring the row blank. The top of a cell needs the bigger trim because
# that is where the row above's low-drawn ticks land.
Y_TOP_FRAC = 0.18
Y_BOT_FRAC = 0.06

# PATCH (fix B): isolated ink specks are dropped after the rulings are
# stripped. Sizes are in warped-table pixels (TABLE_W = 2048), so they do
# not depend on the input resolution. On 22 flatbed scans dust specks scored
# 0.004-0.010 ink - above BLANK_INK_RATIO and overlapping the faintest real
# mark (0.008) - so no ink threshold could separate them, but their size does.
#
# Fragments are GROUPED before measuring: ink pieces within SPECK_GROUP_PX of
# each other count as one blob, and a blob is kept if its overall extent
# (longest side) is >= SPECK_MIN_PX. A faint pencil tick on a phone photo
# binarizes as a chain of 4-11 px dashes; measured one fragment at a time
# (the first version, 12 px, no grouping) those dashes were all deleted and
# 4 real marks on 2 of 42 labelled phone photos read as blank. A dust speck
# is isolated, so grouping does not rescue it.
# Measured plateau (both sets at their best: scanner 438/440, phone 823/840):
#   min 10 px with group 3-7 px, min 12 px with group 5-7 px.
# Min 14+ px deletes real phone marks; group 8+ px lets a scanner speck back.
# 10 / 5 is the middle of that plateau. Set SPECK_MIN_PX = 0 to disable.
SPECK_MIN_PX = 10
SPECK_GROUP_PX = 5


def remove_specks(ink, min_px=SPECK_MIN_PX, group_px=SPECK_GROUP_PX):
    """Drop ink blobs (fragments within group_px merged) shorter than min_px on their longest side."""
    if ink is None or min_px <= 0:
        return ink
    grow = max(0, int(group_px))
    grouped = cv2.dilate(ink, np.ones((2 * grow + 1, 2 * grow + 1), np.uint8)) if grow else ink
    n, lab, st, _ = cv2.connectedComponentsWithStats(grouped, connectivity=8)
    if n <= 1:
        return ink
    keep = np.zeros(n, bool)
    # the dilation added `grow` px on every side; subtract it to get the group's real extent
    extent = np.maximum(st[1:, cv2.CC_STAT_WIDTH], st[1:, cv2.CC_STAT_HEIGHT]) - 2 * grow
    keep[1:] = extent >= min_px
    return np.where((ink > 0) & keep[lab], 255, 0).astype(np.uint8)


# PATCH (fix C): show-through / faint-ink filter.
# Short-bond flatbed scans show the printed text of the sheet's back side
# through the paper. The adaptive threshold turned that mirror-image text
# into "ink" in every cell it crossed (up to 0.25 of a cell), which made the
# argmax pick a random column: 0/16 short-bond sheets were read fully
# correctly. Show-through is faint: measured against the local paper level
# its darkest pixels reach at most ~1/3 of the contrast of the printed
# rulings on the same sheet, while a pen or pencil mark reaches about as
# dark as the rulings. The test is relative to the sheet's own ruling
# contrast (so exposure and phone shading cancel out): a blob (fragments
# within FAINT_GROUP_PX merged) is kept only if its darkest decile reaches
# FAINT_REL x ruling contrast. Kept blobs keep ALL their pixels, so real
# marks score exactly as before and the omr_debug scores keep the scale the
# n8n reconcilers' absolute weak-row threshold (top score < 0.05) expects.
# FAINT_PX_REL > 0 additionally drops single pixels fainter than that share
# of the ruling contrast; it is OFF because it trims the anti-aliased edges
# of every stroke (median tick score 0.077 -> 0.063 on long-bond scans, which
# made the reconcilers flag correct forms as low-confidence) for no
# accuracy gain. Measured plateau (FAINT_PX_REL 0): FAINT_REL 0.35-0.5,
# FAINT_GROUP_PX 0-2. Below 0.35 show-through comes back. Set FAINT_REL = 0
# to disable.
FAINT_REL = 0.4
FAINT_PX_REL = 0.0
FAINT_GROUP_PX = 2
PAPER_WIN_PX = 25       # local paper level = grey dilation over this window


def remove_faint(ink, block, ruling_px, rel=FAINT_REL, px_rel=FAINT_PX_REL,
                 group_px=FAINT_GROUP_PX):
    """Drop ink much fainter than the printed rulings (show-through, shadows)."""
    if ink is None or rel <= 0 or not ruling_px.any():
        return ink
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (PAPER_WIN_PX, PAPER_WIN_PX))
    paper = cv2.blur(cv2.dilate(block, k), (PAPER_WIN_PX, PAPER_WIN_PX))
    contrast = np.clip(paper.astype(np.int16) - block.astype(np.int16), 0, 255).astype(np.float32)
    rul_c = float(np.median(contrast[ruling_px]))
    if rul_c < 10:          # no usable ruling contrast: leave the ink alone
        return ink
    if px_rel > 0:
        ink = np.where((ink > 0) & (contrast >= px_rel * rul_c), 255, 0).astype(np.uint8)
    grow = max(0, int(group_px))
    grouped = cv2.dilate(ink, np.ones((2 * grow + 1, 2 * grow + 1), np.uint8)) if grow else ink
    n, lab, _, _ = cv2.connectedComponentsWithStats(grouped, connectivity=8)
    if n <= 1:
        return ink
    on = ink > 0
    labs, vals = lab[on], contrast[on]
    order = np.argsort(labs, kind="stable")
    labs_s, vals_s = labs[order], vals[order]
    bounds = np.searchsorted(labs_s, np.arange(n + 1))
    keep = np.zeros(n, bool)
    for i in range(1, n):
        v = vals_s[bounds[i]:bounds[i + 1]]
        if v.size:
            keep[i] = np.percentile(v, 90) >= rel * rul_c
    return np.where(on & keep[lab], 255, 0).astype(np.uint8)


def binarize_rating_block(gray, x_lo, x_hi):
    """
    Binarize just the rating columns, with the table rulings removed.

    Returns an ink mask (255 = ink) the same height as `gray` and
    (x_hi - x_lo) wide, so callers index it with x - x_lo.
    """
    out = _binarize(gray, x_lo, x_hi)
    return None if out is None else out[0]


def _binarize(gray, x_lo, x_hi):
    """binarize_rating_block() plus the (dilated) ruling mask it removed."""
    block = gray[:, x_lo:x_hi]
    if block.size == 0:
        return None

    # Local contrast boost, then a threshold calibrated on THIS block
    # only. Adaptive thresholding handles the uneven lighting typical of
    # phone photos far better than one global cut.
    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
    enh = clahe.apply(block)
    bw = cv2.adaptiveThreshold(enh, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                               cv2.THRESH_BINARY_INV, 31, 12)

    # Strip the printed grid: anything that runs as a long horizontal or
    # vertical bar is a ruling, not a hand-drawn mark.
    bh, bwid = bw.shape[:2]
    hk = cv2.getStructuringElement(cv2.MORPH_RECT, (max(15, bwid // 3), 1))
    vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(15, bh // 12)))
    rulings = cv2.bitwise_or(cv2.morphologyEx(bw, cv2.MORPH_OPEN, hk),
                             cv2.morphologyEx(bw, cv2.MORPH_OPEN, vk))
    ruling_px = rulings > 0
    rulings = cv2.dilate(rulings, np.ones((3, 3), np.uint8), iterations=1)
    ink = cv2.bitwise_and(bw, cv2.bitwise_not(rulings))

    # Drop salt noise left behind by the ruling removal.
    ink = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    ink = remove_specks(ink)
    return remove_faint(ink, block, ruling_px), rulings


# PATCH (fix C): spill-over of a mark into the neighbouring row.
# A tick drawn across a row ruling leaves a short tail in the next row
# (one labelled scanner sheet read a blank row as "S" from such a tail).
# Ruling removal cuts the stroke at the ruling, so the pieces are re-joined
# first - growing the ink by BRIDGE_PX, but only inside a band around the
# removed rulings, so separate marks elsewhere never merge. Each joined
# stroke then keeps only the rows holding at least LEAK_FRAC of its largest
# per-row share. Deliberately conservative: two marks that touch across a
# ruling are usually of similar size and both stay.
# Measured plateau: LEAK_FRAC 0.25-0.35, BRIDGE_PX 4-8 (0.15 lets one spill
# tail through again; 0.4+ starts eating
# small real marks that touch a neighbour's mark on phone photos).
LEAK_FRAC = 0.25
BRIDGE_PX = 6


def attribute_to_rows(ink, rulings, row_y1s, row_y2s, frac=LEAK_FRAC, bridge=BRIDGE_PX):
    """Remove each stroke's small spill-over into neighbouring rows."""
    if frac <= 0 or ink is None:
        return ink
    h = ink.shape[0]
    grown = cv2.dilate(ink, cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (2 * bridge + 1, 2 * bridge + 1)))
    band = cv2.dilate(rulings, np.ones((7, 7), np.uint8)) > 0
    joined = np.where((ink > 0) | ((grown > 0) & band), 255, 0).astype(np.uint8)
    n, lab, _, _ = cv2.connectedComponentsWithStats(joined, connectivity=8)
    if n <= 1:
        return ink
    row_of = np.full(h, -1, int)
    for r, (a, b) in enumerate(zip(row_y1s, row_y2s)):
        row_of[max(0, int(a)):min(h, int(b))] = r
    ys, xs = np.nonzero(ink > 0)
    rr = row_of[ys]
    ll = lab[ys, xs]
    sel = rr >= 0
    mass = np.zeros((n, len(row_y1s)), np.int64)
    np.add.at(mass, (ll[sel], rr[sel]), 1)
    keep = mass >= frac * np.maximum(mass.max(axis=1, keepdims=True), 1)
    drop = sel.copy()
    drop[sel] = ~keep[ll[sel], rr[sel]]
    out = ink.copy()
    out[ys[drop], xs[drop]] = 0
    return out


def cell_scores(ink, x_lo, y1, y2, col_xs):
    """Ink fraction of each rating cell in the row band [y1, y2)."""
    h = ink.shape[0]
    y1, y2 = max(0, int(y1)), min(h, int(y2))
    cell_h = y2 - y1
    if cell_h < 4:
        return [0.0] * (len(col_xs) - 1)
    yy1 = y1 + max(1, int(cell_h * Y_TOP_FRAC))
    yy2 = max(yy1 + 1, y2 - max(1, int(cell_h * Y_BOT_FRAC)))

    out = []
    for c in range(len(col_xs) - 1):
        a, b = int(col_xs[c]) - x_lo, int(col_xs[c + 1]) - x_lo
        mx = max(2, int((b - a) * X_MARGIN_FRAC))
        cell = ink[yy1:yy2, a + mx:b - mx]
        out.append(float(np.mean(cell > 0)) if cell.size else 0.0)
    return out


# PATCH (fix C): second look at rows that read blank.
# Raters often draw the tick ON a row ruling, so it lands in the trimmed
# top/bottom strip of its own cell or just across the ruling, in the strip
# of the neighbouring row that Y_TOP_FRAC trims away. For a row that read
# blank only, count its whole band plus RESCUE_Z row-heights beyond each
# ruling - in a neighbour's strip only ink OUTSIDE the column that
# neighbour chose (that ink is the neighbour's own mark) - and accept the
# best column if it reaches RESCUE_MIN. Never looks across a section edge
# (the column headers and section titles live there). Rows that already
# have a mark are never changed. Measured plateau: RESCUE_Z 0.25-0.5,
# RESCUE_MIN 0.004-0.006; no labelled blank row was turned into a mark.
# Rescued rows report the rescue scores, which are small, so the n8n
# reconcilers see them as weak rows. Set RESCUE_Z = 0 to disable.
RESCUE_Z = 0.35
RESCUE_MIN = 0.006
ROWS_PER_SECTION = 10


def rescue_scores(ink, x_lo, y1s, y2s, col_xs, i, best_c, z=RESCUE_Z):
    """Scores for blank row i from its whole band plus the neighbours' near strips."""
    h = ink.shape[0]
    y1, y2 = int(y1s[i]), int(y2s[i])
    rh = y2 - y1
    if rh < 4:
        return None
    first = i % ROWS_PER_SECTION == 0
    last = i % ROWS_PER_SECTION == ROWS_PER_SECTION - 1
    ext = int(round(z * rh))
    parts = [(y1, y2, None)]
    if not first and i - 1 >= 0:
        parts.append((max(0, y1 - ext), y1, best_c[i - 1]))
    if not last and i + 1 < len(y1s):
        parts.append((y2, min(h, y2 + ext), best_c[i + 1]))
    out = []
    for c in range(len(col_xs) - 1):
        a, b = int(col_xs[c]) - x_lo, int(col_xs[c + 1]) - x_lo
        mx = max(2, int((b - a) * X_MARGIN_FRAC))
        cnt = 0
        for ya, yb, nb in parts:
            if nb is not None and nb == c:
                continue
            cnt += int((ink[ya:yb, a + mx:b - mx] > 0).sum())
        out.append(cnt / float(max(1, rh * (b - a - 2 * mx))))
    return out


def read_rows(gray, row_y1s, row_y2s, col_xs, rating_names,
              blank_ink_ratio=BLANK_INK_RATIO, return_scores=False):
    """
    Read all rows at once. Returns a list of rating names (or "blank").
    """
    x_lo, x_hi = int(col_xs[0]), int(col_xs[-1])
    b = _binarize(gray, x_lo, x_hi)
    if b is None:
        res = ['blank'] * len(row_y1s)
        return (res, []) if return_scores else res
    ink = attribute_to_rows(b[0], b[1], row_y1s, row_y2s)

    names, all_scores, best_c = [], [], []
    for y1, y2 in zip(row_y1s, row_y2s):
        s = cell_scores(ink, x_lo, y1, y2, col_xs)
        all_scores.append(s)
        best = int(np.argmax(s))
        ok = s[best] >= blank_ink_ratio
        best_c.append(best if ok else -1)
        names.append(rating_names[best] if ok else 'blank')
    if RESCUE_Z > 0:
        for i in range(len(row_y1s)):
            if best_c[i] >= 0:
                continue
            s = rescue_scores(ink, x_lo, row_y1s, row_y2s, col_xs, i, best_c)
            if s is not None and max(s) >= RESCUE_MIN:
                all_scores[i] = s
                names[i] = rating_names[int(np.argmax(s))]
    return (names, all_scores) if return_scores else names
