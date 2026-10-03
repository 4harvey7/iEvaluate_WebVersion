import base64
import io
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image

from app import logic


def _img_bytes(fmt: str, mode: str = "RGB", size=(40, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new(mode, size, color=128 if mode == "L" else (200, 10, 10)).save(buf, format=fmt)
    return buf.getvalue()


# ---------------------------------------------------------------- task id
def test_task_id_format():
    sha = "ab" * 32
    bid = "3fa1c2e0-1234-4a5b-9c1d-abcdef123456"
    assert logic.make_task_id(bid, sha) == f"web:{bid}:{'ab' * 8}"
    assert len(logic.make_task_id(bid, sha).split(":")[2]) == 16


def test_n8n_payload_exact_keys():
    batch = {"id": "b", "instructor_id": "i", "subject_id": "s", "term_id": "t", "created_by": "u"}
    p = logic.build_n8n_payload(batch, "st", "f.jpg", "c" * 64, "QUJD")
    assert set(p) == {"batch_id", "instructor_id", "subject_id", "term_id", "station_id", "staff_id",
                      "filename", "file_sha256", "task_id", "image", "mime_type"}
    assert p["staff_id"] == "u" and p["mime_type"] == "image/jpeg" and p["task_id"] == "web:b:" + "c" * 16


# ---------------------------------------------------------------- station keys
def test_station_key_hashing():
    key = logic.generate_station_key()
    assert len(key) >= 40
    h = logic.hash_station_key(key)
    assert len(h) == 64 and h != key
    assert logic.station_key_matches(key, h)
    assert not logic.station_key_matches(key + "x", h)
    assert not logic.station_key_matches("", h)
    assert not logic.station_key_matches(key, None)
    assert logic.generate_station_key() != key


# ---------------------------------------------------------------- predates
def test_predates_check():
    created = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
    assert logic.file_predates_batch(created - timedelta(minutes=5), created)
    assert not logic.file_predates_batch(created + timedelta(seconds=1), created)
    assert not logic.file_predates_batch(created - timedelta(seconds=10), created, grace_seconds=30)
    assert logic.file_predates_batch(created - timedelta(seconds=31), created, grace_seconds=30)


def test_clock_skew_correction():
    server_now = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
    client_now = server_now - timedelta(minutes=3)  # laptop clock 3 min behind
    mtime = client_now - timedelta(seconds=5)
    corrected = logic.corrected_capture_time(mtime, client_now, server_now)
    assert corrected == server_now - timedelta(seconds=5)
    # absurd skew ignored
    assert logic.corrected_capture_time(mtime, server_now - timedelta(days=10), server_now) == mtime
    assert logic.corrected_capture_time(mtime, None, server_now) == mtime


def test_parse_ts_variants():
    assert logic.parse_ts("2026-09-01T10:00:00Z") == datetime(2026, 9, 1, 10, tzinfo=timezone.utc)
    assert logic.parse_ts("2026-09-01T10:00:00.1234567+00:00").microsecond == 123456
    assert logic.parse_ts("2026-09-01T10:00:00").tzinfo is not None
    assert logic.parse_ts("garbage") is None
    assert logic.parse_ts(None) is None


def test_last_activity():
    b = {"created_at": "2026-09-01T10:00:00Z", "last_scan_at": "2026-09-01T12:00:00Z"}
    assert logic.last_activity(b).hour == 12
    assert logic.last_activity({"created_at": "2026-09-01T10:00:00Z", "last_scan_at": None}).hour == 10


# ---------------------------------------------------------------- n8n response
def test_n8n_valid_response():
    r = logic.validate_n8n_response({
        "status": "flagged", "flagged": True, "task_id": "web:x:y", "review_reasons": ["low confidence", 3, None],
        "identity_check": {"instructor": "match", "subject": "MISMATCH", "ocr_instructor": "J Doe", "ocr_subject": "CS 1"},
        "message": "ok",
    })
    assert r["status"] == "flagged" and r["flagged"] is True
    assert r["review_reasons"] == ["low confidence", "3"]
    assert r["identity_check"]["subject"] == "mismatch"
    assert r["identity_check"]["instructor"] == "match"


def test_n8n_success_marked_flagged_becomes_flagged():
    assert logic.validate_n8n_response({"status": "success", "flagged": True})["status"] == "flagged"


def test_n8n_unknown_identity_value_is_unverified():
    r = logic.validate_n8n_response({"status": "success", "identity_check": {"instructor": "maybe"}})
    assert r["identity_check"]["instructor"] == "unverified"
    assert r["identity_check"]["subject"] == "unverified"


def test_n8n_single_item_array_accepted():
    assert logic.validate_n8n_response([{"status": "duplicate"}])["status"] == "duplicate"


@pytest.mark.parametrize("bad", [
    [], [{"status": "success"}, {"status": "success"}], "text", 42, None,
    {"status": "weird"}, {}, {"status": "success", "review_reasons": {"a": 1}},
    {"status": "success", "identity_check": "match"},
])
def test_n8n_invalid_responses(bad):
    with pytest.raises(logic.PipelineResponseError):
        logic.validate_n8n_response(bad)


# ---------------------------------------------------------------- images
def test_decode_and_size_limit():
    raw = _img_bytes("JPEG")
    b64 = base64.b64encode(raw).decode()
    assert logic.decode_image_base64(b64, 1024 * 1024) == raw
    assert logic.decode_image_base64("data:image/jpeg;base64," + b64, 1024 * 1024) == raw
    with pytest.raises(logic.ImageValidationError) as e:
        logic.decode_image_base64(b64, 100)
    assert e.value.status == 413
    with pytest.raises(logic.ImageValidationError):
        logic.decode_image_base64("not base64!!", 1024)


def test_normalize_jpeg_passthrough_and_png_conversion():
    jpg = _img_bytes("JPEG", "L", size=(30, 40))  # portrait: no rotation needed
    assert logic.normalize_to_jpeg(jpg) == jpg
    landscape = _img_bytes("JPEG", "L")  # landscape is always turned to portrait
    out = Image.open(io.BytesIO(logic.normalize_to_jpeg(landscape)))
    assert out.size == (30, 40)
    png = _img_bytes("PNG", "RGBA")
    out = logic.normalize_to_jpeg(png)
    assert logic.sniff_image_type(out) == "jpeg"


def test_normalize_rejects_non_images():
    with pytest.raises(logic.ImageValidationError) as e:
        logic.normalize_to_jpeg(b"%PDF-1.4 hello")
    assert e.value.status == 415
    with pytest.raises(logic.ImageValidationError):
        logic.normalize_to_jpeg(b"\xff\xd8\xff" + b"\x00" * 50)  # truncated JPEG


def test_safe_filename():
    assert logic.safe_filename("C:\\scans\\a\\page1.jpg") == "page1.jpg"
    assert logic.safe_filename("../../etc/passwd") == "passwd"
    assert logic.safe_filename("") == "scan.jpg"


# ---------------------------------------------------------------- subjects
def test_subject_normalization_and_ranking():
    assert logic.normalize_code("Math 101") == logic.normalize_code("MATH-101") == "MATH101"
    assert logic.code_like_pattern("CS1") == "%C%S%1%"
    rows = [
        {"subject_code": "MATH 1010", "subject_name": "Advanced"},
        {"subject_code": "MATH-101", "subject_name": "Algebra"},
        {"subject_code": "ENG 1", "subject_name": "Math for poets"},
        {"subject_code": "PHY 9", "subject_name": "Physics"},
    ]
    ranked = logic.rank_subjects(rows, "math 101")
    assert ranked[0]["subject_code"] == "MATH-101"
    assert ranked[1]["subject_code"] == "MATH 1010"
    assert all(r["subject_code"] != "PHY 9" for r in ranked)


def test_clean_search_text_strips_filter_syntax():
    assert logic.clean_search_text("a,b(c)%_*") == "a b c"


def test_n8n_payload_reused_image_only_when_found():
    batch = {"id": "b", "instructor_id": "i", "subject_id": "s", "term_id": "t", "created_by": "u"}
    info = logic.reused_image_info({"batch_id": "b0", "status": "success", "filename": "x.jpg", "task_id": "web:b0:x"})
    assert info == {"batch_id": "b0", "status": "success"}  # nothing else (no personal data)
    p = logic.build_n8n_payload(batch, "st", "f.jpg", "c" * 64, "QUJD", reused_image=info)
    assert p["reused_image"] == info
    assert "reused_image" not in logic.build_n8n_payload(batch, "st", "f.jpg", "c" * 64, "QUJD", reused_image=None)
    for st in ("flagged", "duplicate"):
        assert logic.reused_image_info({"batch_id": "b0", "status": st}) == {"batch_id": "b0", "status": st}
    for row in (None, {}, {"batch_id": "b0", "status": "rejected"}, {"batch_id": "b0", "status": "error"},
                {"batch_id": "b0", "status": "processing"}, {"batch_id": None, "status": "success"}):
        assert logic.reused_image_info(row) is None
