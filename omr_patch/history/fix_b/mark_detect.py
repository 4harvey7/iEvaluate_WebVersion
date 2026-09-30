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


def binarize_rating_block(gray, x_lo, x_hi):
    """
    Binarize just the rating columns, with the table rulings removed.

    Returns an ink mask (255 = ink) the same height as `gray` and
    (x_hi - x_lo) wide, so callers index it with x - x_lo.
    """
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
    rulings = cv2.dilate(rulings, np.ones((3, 3), np.uint8), iterations=1)
    ink = cv2.bitwise_and(bw, cv2.bitwise_not(rulings))

    # Drop salt noise left behind by the ruling removal.
    ink = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    return remove_specks(ink)


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


def read_rows(gray, row_y1s, row_y2s, col_xs, rating_names,
              blank_ink_ratio=BLANK_INK_RATIO, return_scores=False):
    """
    Read all rows at once. Returns a list of rating names (or "blank").
    """
    x_lo, x_hi = int(col_xs[0]), int(col_xs[-1])
    ink = binarize_rating_block(gray, x_lo, x_hi)
    if ink is None:
        res = ['blank'] * len(row_y1s)
        return (res, []) if return_scores else res

    names, all_scores = [], []
    for y1, y2 in zip(row_y1s, row_y2s):
        s = cell_scores(ink, x_lo, y1, y2, col_xs)
        all_scores.append(s)
        best = int(np.argmax(s))
        names.append(rating_names[best] if s[best] >= blank_ink_ratio else 'blank')
    return (names, all_scores) if return_scores else names
