"""Orientation normalisation (app/orientation.py).

* Synthetic SS Form 2 look-alike: runs everywhere (CI).
* Real scans from C:\\FORMS: used only if that folder exists on this machine.
  They are read in place at test time - never copied, printed or committed.
"""
import glob
import io
import os
import time

import pytest
from PIL import Image, ImageDraw

from app import logic
from app.orientation import normalize_orientation, rotate_cw

FORMS_DIR = os.environ.get("IEVALUATE_FORMS_DIR", r"C:\FORMS")


# ---------------------------------------------------------------- synthetic form
def synthetic_form(w: int = 1700, h: int = 2200) -> Image.Image:
    """Upright portrait page with the structure of SS Form 2: letterhead with
    two seals on top, a ruled table in the lower-middle, the 5-column rating
    block (6 vertical rulings at 72-98% of table width) on the right, footer
    logos at the bottom, and some text-like blocks."""
    im = Image.new("L", (w, h), 250)
    d = ImageDraw.Draw(im)
    # letterhead
    d.ellipse((int(0.10 * w), int(0.02 * h), int(0.20 * w), int(0.10 * h)), fill=40)
    d.ellipse((int(0.80 * w), int(0.02 * h), int(0.90 * w), int(0.10 * h)), fill=40)
    for i, y in enumerate(range(int(0.03 * h), int(0.10 * h), int(0.015 * h))):
        d.rectangle((int(0.30 * w), y, int(0.70 * w) - i * 30, y + 12), fill=30)
    # instruction text blocks
    for y in range(int(0.16 * h), int(0.38 * h), 28):
        x = int(0.08 * w)
        while x < int(0.88 * w):
            d.rectangle((x, y, x + 70, y + 10), fill=60)
            x += 95
    # table
    tx0, tx1 = int(0.08 * w), int(0.92 * w)
    ty0, ty1 = int(0.42 * h), int(0.80 * h)
    tw = tx1 - tx0
    for i in range(22):
        y = ty0 + i * (ty1 - ty0) // 21
        d.line((tx0, y, tx1, y), fill=20, width=3)
    for frac in (0.72, 0.772, 0.824, 0.876, 0.928, 0.98):
        x = tx0 + int(frac * tw)
        d.line((x, ty0, x, ty1), fill=20, width=3)
    d.line((tx0, ty0, tx0, ty1), fill=20, width=3)
    for i in range(21):  # item text + some check marks
        y = ty0 + i * (ty1 - ty0) // 21 + 12
        d.rectangle((tx0 + 20, y, tx0 + int(0.6 * tw), y + 10), fill=70)
        cx = tx0 + int((0.745 + 0.052 * (i % 5)) * tw)
        d.line((cx - 10, y, cx, y + 14, cx + 16, y - 8), fill=30, width=4)
    # signature lines + footer logos
    for y in (int(0.86 * h), int(0.89 * h)):
        d.line((int(0.08 * w), y, int(0.35 * w), y), fill=30, width=2)
    for i in range(6):
        x = int(0.30 * w) + i * 120
        d.rectangle((x, int(0.95 * h), x + 80, int(0.98 * h)), fill=50)
    return im


def _jpeg(im: Image.Image, **kw) -> bytes:
    b = io.BytesIO()
    im.save(b, format="JPEG", quality=85, **kw)
    return b.getvalue()


@pytest.mark.parametrize("skew", [-2, 0, 2])
@pytest.mark.parametrize("k", [0, 90, 180, 270])
def test_synthetic_form_every_rotation_comes_out_upright(k, skew):
    up = synthetic_form()
    if skew:
        up = up.rotate(skew, expand=True, fillcolor=250)
    r = normalize_orientation(rotate_cw(up, k))
    assert not r.uncertain, r.features
    assert (r.rotation_cw + k) % 360 == 0, (k, r.rotation_cw, r.features)
    assert r.image.height > r.image.width  # portrait


def test_blank_page_is_uncertain_and_only_made_portrait():
    r = normalize_orientation(Image.new("L", (2200, 1700), 250))
    assert r.uncertain and r.rotation_cw == 270  # landscape->portrait only
    assert r.image.height > r.image.width
    r2 = normalize_orientation(Image.new("L", (1700, 2200), 250))
    assert r2.uncertain and r2.rotation_cw == 0


def test_non_form_text_page_is_uncertain():
    im = Image.new("L", (1700, 2200), 255)
    d = ImageDraw.Draw(im)
    for y in range(100, 2100, 40):
        d.text((100, y), "lorem ipsum dolor sit amet " * 8, fill=0)
    assert normalize_orientation(im).uncertain


def test_exif_orientation_is_applied_first():
    up = synthetic_form(850, 1100)
    # EXIF 6 = "display rotated 90 deg clockwise": store the page turned CCW.
    stored = up.transpose(Image.Transpose.ROTATE_90)
    exif = Image.Exif()
    exif[0x0112] = 6
    page = logic.prepare_page(_jpeg(stored, exif=exif.tobytes()))
    assert page.exif_transposed and page.rotation_cw == 0 and not page.orientation_uncertain
    out = Image.open(io.BytesIO(page.jpeg))
    assert out.height > out.width
    assert out.getexif().get(0x0112) in (None, 1)


def test_upright_jpeg_passes_through_byte_for_byte():
    raw = _jpeg(synthetic_form(850, 1100))
    page = logic.prepare_page(raw)
    assert page.jpeg == raw and page.rotation_cw == 0 and not page.orientation_uncertain


def test_sideways_png_is_rotated_and_reencoded():
    b = io.BytesIO()
    rotate_cw(synthetic_form(850, 1100), 90).save(b, format="PNG")
    page = logic.prepare_page(b.getvalue())
    assert page.rotation_cw == 270 and logic.sniff_image_type(page.jpeg) == "jpeg"
    out = Image.open(io.BytesIO(page.jpeg))
    assert out.height > out.width


def test_n8n_payload_carries_orientation():
    batch = {"id": "b", "instructor_id": "i", "subject_id": "s", "term_id": "t", "created_by": "u"}
    page = logic.PreparedPage(jpeg=b"", rotation_cw=270, orientation_confidence=0.71)
    p = logic.build_n8n_payload(batch, "st", "f.jpg", "c" * 64, "QUJD", orientation=page)
    assert p["orientation_applied"] == 270 and p["orientation_confidence"] == 0.71


def test_analysis_is_fast():
    im = rotate_cw(synthetic_form(2200, 2850), 90)
    im.load()
    t = time.perf_counter()
    normalize_orientation(im)
    assert (time.perf_counter() - t) < 0.3


# ---------------------------------------------------------------- real samples (local only)
# Clockwise degrees that make each known sample upright, from visual inspection.
# The scanner's usual output is landscape with the letterhead on the right (270).
KNOWN_UPRIGHT_CW = {"FORMS-03a": 180, "FORMS-05b": 90}
REAL_SAMPLES = sorted(glob.glob(os.path.join(FORMS_DIR, "*.png")) + glob.glob(os.path.join(FORMS_DIR, "*.jpg")))


def _upright_cw(path: str, im: Image.Image) -> int:
    name = os.path.splitext(os.path.basename(path))[0]
    if name in KNOWN_UPRIGHT_CW:
        return KNOWN_UPRIGHT_CW[name]
    return 270 if im.width > im.height else 0


@pytest.mark.skipif(not REAL_SAMPLES, reason=f"no sample scans in {FORMS_DIR}")
def test_real_samples_every_rotation_comes_out_upright(capsys):
    results = []
    for path in REAL_SAMPLES:
        im = Image.open(path)
        im.load()
        up = rotate_cw(im, _upright_cw(path, im))
        ok, worst_ms, min_conf = 0, 0.0, 1.0
        for k in (0, 90, 180, 270):
            v = rotate_cw(up, k)
            t = time.perf_counter()
            r = normalize_orientation(v)
            worst_ms = max(worst_ms, (time.perf_counter() - t) * 1000)
            min_conf = min(min_conf, r.confidence)
            ok += (r.rotation_cw + k) % 360 == 0 and not r.uncertain
        results.append((os.path.basename(path), ok, min_conf, worst_ms))
    with capsys.disabled():
        print("\norientation accuracy on real samples (4 rotations each):")
        for name, ok, conf, ms in results:
            print(f"  {name}: {ok}/4  min confidence {conf:.2f}  worst {ms:.0f} ms")
    assert all(ok == 4 for _, ok, _, _ in results), results
