"""OMR preprocessing (app/omr_prep.py): long-bond crop + grey border.

Synthetic fixtures run everywhere; real scans in C:\\FORMS are used only when
present (read in place, never copied or printed)."""
import base64
import glob
import io
import os

import numpy as np
import pytest
from PIL import Image

from app import logic
from app.omr_prep import (GREY, HEADER_TOP_FRACTION, HEADER_UPSCALE, LONG_BOND_RATIO,
                          SHORT_BOND_RATIO, OmrPrepConfig, crop_header, crop_height,
                          detect_paper_size, prepare_for_omr)
from tests.test_orientation import FORMS_DIR, synthetic_form

CFG = OmrPrepConfig()


def legal_page(w=1700, content_offset=0):
    """Legal-size scan (8.5x14) holding a long-bond (8.5x13) form, top-aligned
    (optionally shifted down), white below."""
    h_legal = int(round(w * 14 / 8.5))
    h_bond = int(round(w * LONG_BOND_RATIO))
    page = Image.new("L", (w, h_legal), 250)
    page.paste(synthetic_form(w, h_bond), (0, content_offset))
    return page


def dark_count(a: np.ndarray) -> int:
    return int((a < 140).sum())


def border_is_grey(img: Image.Image, pad: int, tol: int = 0) -> bool:
    a = np.asarray(img.convert("L"), dtype=np.int16)
    strips = [a[:pad, :], a[-pad:, :], a[:, :pad], a[:, -pad:]]
    return all(np.abs(s - GREY).max() <= tol for s in strips)


def test_legal_scan_cropped_to_long_bond_and_padded():
    page = legal_page()
    r = prepare_for_omr(page, CFG)
    pad = int(round(1700 * 0.05))
    keep = int(round(1700 * LONG_BOND_RATIO))
    assert r.label == "longbond_crop+grey_pad_5"
    assert r.image.size == (1700 + 2 * pad, keep + 2 * pad)
    assert r.pad_px == pad and r.cropped_rows == page.height - keep
    assert border_is_grey(r.image, pad)
    interior = np.asarray(r.image)[pad:-pad, pad:-pad]
    assert dark_count(interior) == dark_count(np.asarray(page))  # no ink lost


def test_shifted_page_keeps_every_row():
    page = legal_page(content_offset=150)  # content now runs past the long-bond line
    keep = crop_height(page)
    assert keep > int(round(1700 * LONG_BOND_RATIO))
    r = prepare_for_omr(page, CFG)
    pad = r.pad_px
    interior = np.asarray(r.image)[pad:-pad, pad:-pad]
    assert dark_count(interior) == dark_count(np.asarray(page))


def test_short_page_not_cropped_only_padded():
    page = synthetic_form(1700, 2200)  # letter-ish, shorter than long bond
    r = prepare_for_omr(page, CFG)
    assert r.label == "grey_pad_5" and r.cropped_rows == 0
    assert r.paper_size == "SHORT"
    assert r.image.size == (1700 + 2 * r.pad_px, 2200 + 2 * r.pad_px)


def test_config_switches():
    page = legal_page()
    assert prepare_for_omr(page, OmrPrepConfig(pad=False)).label == "longbond_crop"
    assert prepare_for_omr(page, OmrPrepConfig(crop_long_bond=False)).label == "grey_pad_5"
    r = prepare_for_omr(page, OmrPrepConfig(pad_percent=8))
    assert r.pad_px == int(round(1700 * 0.08)) and r.label.endswith("grey_pad_8")


def test_prepare_page_applies_prep_and_reports_it():
    b = io.BytesIO()
    legal_page(850).save(b, format="JPEG", quality=90)
    page = logic.prepare_page(b.getvalue(), omr_prep=CFG)
    assert page.preprocess == "longbond_crop+grey_pad_5"
    assert page.paper_size == "LONG"
    out = Image.open(io.BytesIO(page.jpeg))
    pad = int(round(850 * 0.05))
    assert out.size == (850 + 2 * pad, int(round(850 * LONG_BOND_RATIO)) + 2 * pad)
    assert border_is_grey(out, pad - 2, tol=6)  # JPEG ringing tolerance
    # without prep (phone path / disabled): untouched
    plain = logic.prepare_page(b.getvalue())
    assert plain.preprocess == "none" and plain.paper_size is None


def test_crop_header_size_and_upscale():
    page = synthetic_form(1700, 2803)
    header = crop_header(page)
    expect_h = int(round(2803 * HEADER_TOP_FRACTION))
    assert header.size == (int(round(1700 * HEADER_UPSCALE)), int(round(expect_h * HEADER_UPSCALE)))


def test_prepare_page_attaches_header_jpeg_for_website_only():
    b = io.BytesIO()
    legal_page(850).save(b, format="JPEG", quality=90)
    page = logic.prepare_page(b.getvalue(), omr_prep=CFG)
    assert page.header_jpeg
    header_img = Image.open(io.BytesIO(page.header_jpeg))
    assert header_img.format == "JPEG"
    # header crop comes from the oriented image BEFORE the bond crop/grey border, so its
    # width matches the raw page width (not the padded OMR output).
    assert header_img.width == int(round(850 * HEADER_UPSCALE))
    # phone path (no omr_prep): no header crop is produced
    plain = logic.prepare_page(b.getvalue())
    assert plain.header_jpeg is None


def test_payload_carries_header_image_only_when_present():
    batch = {"id": "b", "instructor_id": "i", "subject_id": "s", "term_id": "t", "created_by": "u"}
    with_header = logic.PreparedPage(jpeg=b"", header_jpeg=b"\xff\xd8\xff\xd9")
    p = logic.build_n8n_payload(batch, "st", "f.jpg", "c" * 64, "QUJD", orientation=with_header)
    assert p["header_image"] == base64.b64encode(b"\xff\xd8\xff\xd9").decode("ascii")
    no_header = logic.PreparedPage(jpeg=b"")
    p2 = logic.build_n8n_payload(batch, "st", "f.jpg", "c" * 64, "QUJD", orientation=no_header)
    assert "header_image" not in p2
    no_page = logic.build_n8n_payload(batch, "st", "f.jpg", "c" * 64, "QUJD")
    assert "header_image" not in no_page


def test_rgb_page_gets_rgb_grey_border():
    page = legal_page(600).convert("RGB")
    r = prepare_for_omr(page, CFG)
    assert r.image.mode == "RGB" and r.image.getpixel((0, 0)) == (GREY, GREY, GREY)


def test_payload_carries_preprocess():
    batch = {"id": "b", "instructor_id": "i", "subject_id": "s", "term_id": "t", "created_by": "u"}
    page = logic.PreparedPage(jpeg=b"", preprocess="longbond_crop+grey_pad_5")
    p = logic.build_n8n_payload(batch, "st", "f.jpg", "c" * 64, "QUJD", orientation=page)
    assert p["preprocess"] == "longbond_crop+grey_pad_5"
    assert "paper_size" not in p  # not detected -> n8n falls back to LONG


# ---------------------------------------------------------------- short vs long bond
def inked_page(w=1700, aspect=14 / 8.5, content_bottom=1.25):
    """White page of height w*aspect: a black band near the top ("letterhead")
    and a thin black rule whose last row sits at content_bottom * w."""
    h = int(round(w * aspect))
    page = Image.new("L", (w, h), 250)
    a = np.asarray(page).copy()
    a[int(0.05 * w):int(0.10 * w), int(0.1 * w):int(0.9 * w)] = 0
    bottom = int(round(content_bottom * w))
    a[bottom - 10:bottom, int(0.1 * w):int(0.9 * w)] = 0
    return Image.fromarray(a), bottom


def test_short_aspect_image_is_short_and_not_cropped():
    page, _ = inked_page(aspect=11 / 8.5, content_bottom=1.2)
    assert detect_paper_size(page) == "SHORT"
    r = prepare_for_omr(page, CFG)
    assert r.paper_size == "SHORT" and r.cropped_rows == 0 and r.label == "grey_pad_5"
    assert r.image.size == (1700 + 2 * r.pad_px, page.height + 2 * r.pad_px)


def test_legal_image_with_short_content_cropped_to_short_bond():
    page, _ = inked_page(aspect=14 / 8.5, content_bottom=1.25)
    assert detect_paper_size(page) == "SHORT"
    r = prepare_for_omr(page, CFG)
    pad = int(round(1700 * 0.05))
    keep = int(round(1700 * SHORT_BOND_RATIO))
    assert r.paper_size == "SHORT" and r.label == "shortbond_crop+grey_pad_5"
    assert r.image.size == (1700 + 2 * pad, keep + 2 * pad)
    assert r.cropped_rows == page.height - keep
    assert border_is_grey(r.image, pad)
    interior = np.asarray(r.image)[pad:-pad, pad:-pad]
    assert dark_count(interior) == dark_count(np.asarray(page))


def test_legal_image_with_long_content_cropped_to_long_bond():
    page, _ = inked_page(aspect=14 / 8.5, content_bottom=1.50)
    assert detect_paper_size(page) == "LONG"
    r = prepare_for_omr(page, CFG)
    keep = int(round(1700 * LONG_BOND_RATIO))
    assert r.paper_size == "LONG" and r.label == "longbond_crop+grey_pad_5"
    assert r.image.size == (1700 + 2 * r.pad_px, keep + 2 * r.pad_px)


def test_blank_page_is_long():
    assert detect_paper_size(Image.new("L", (1700, 2800), 250)) == "LONG"


@pytest.mark.parametrize("content_bottom", [1.36, 1.39])
def test_short_crop_never_cuts_content_below_target(content_bottom):
    # Content ends below the 11/8.5 line (but still reads as short bond): the
    # cut moves down to keep it.
    page, bottom = inked_page(aspect=14 / 8.5, content_bottom=content_bottom)
    assert detect_paper_size(page) == "SHORT"
    keep = crop_height(page, "SHORT")
    assert keep > int(round(1700 * SHORT_BOND_RATIO)) and keep >= bottom
    r = prepare_for_omr(page, CFG)
    pad = r.pad_px
    interior = np.asarray(r.image)[pad:-pad, pad:-pad]
    assert dark_count(interior) == dark_count(np.asarray(page))


def test_crop_height_never_taller_than_image():
    page, _ = inked_page(aspect=1.35, content_bottom=1.34)
    assert crop_height(page, "SHORT") <= page.height
    assert crop_height(page, "LONG") == page.height


def test_prepare_page_reports_short_paper_size():
    page, _ = inked_page(850, aspect=14 / 8.5, content_bottom=1.25)
    b = io.BytesIO()
    page.save(b, format="JPEG", quality=90)
    out = logic.prepare_page(b.getvalue(), omr_prep=CFG)
    assert out.paper_size == "SHORT"
    assert out.preprocess == "shortbond_crop+grey_pad_5"
    img = Image.open(io.BytesIO(out.jpeg))
    pad = int(round(850 * 0.05))
    assert img.size == (850 + 2 * pad, int(round(850 * SHORT_BOND_RATIO)) + 2 * pad)


@pytest.mark.parametrize("value,expected", [("SHORT", "SHORT"), ("LONG", "LONG"),
                                            (None, None), ("short", None), ("A4", None)])
def test_payload_paper_size_only_when_valid(value, expected):
    batch = {"id": "b", "instructor_id": "i", "subject_id": "s", "term_id": "t", "created_by": "u"}
    page = logic.PreparedPage(jpeg=b"", paper_size=value)
    p = logic.build_n8n_payload(batch, "st", "f.jpg", "c" * 64, "QUJD", orientation=page)
    assert p.get("paper_size") == expected
    assert ("paper_size" in p) == (expected is not None)
    no_page = logic.build_n8n_payload(batch, "st", "f.jpg", "c" * 64, "QUJD")
    assert "paper_size" not in no_page


# ---------------------------------------------------------------- real samples (local only)
REAL = sorted(glob.glob(os.path.join(FORMS_DIR, "*.png")))


@pytest.mark.skipif(not REAL, reason=f"no sample scans in {FORMS_DIR}")
@pytest.mark.parametrize("path", REAL, ids=[os.path.basename(p) for p in REAL])
def test_real_sample_geometry_border_and_no_ink_cropped(path):
    raw = open(path, "rb").read()
    upright = Image.open(io.BytesIO(logic.prepare_page(raw).jpeg)).convert("L")
    r = prepare_for_omr(upright, CFG)
    w, h = upright.size
    keep = h - r.cropped_rows
    pad = r.pad_px
    assert pad == int(round(w * 0.05))
    assert r.image.size == (w + 2 * pad, keep + 2 * pad)
    ratio = SHORT_BOND_RATIO if r.paper_size == "SHORT" else LONG_BOND_RATIO
    assert keep >= int(round(w * ratio)) or keep == h
    assert border_is_grey(r.image, pad)
    # Nothing printed or written in the removed strip (ignoring the scanner-edge band).
    removed = np.asarray(upright, dtype=np.int16)[keep: h - int(0.015 * h), :]
    if removed.size:
        row_ink = (removed < 140).mean(axis=1)
        assert row_ink.max() < 0.003
