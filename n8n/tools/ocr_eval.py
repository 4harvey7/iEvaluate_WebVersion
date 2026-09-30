#!/usr/bin/env python3
"""Read-only report on Gemini's header OCR accuracy (website scans).

Reads the `ocr_correction_log` view (migrations/006_ocr_correction_log.sql) over the Supabase
REST API and prints per-field accuracy, plus the top 10 most common misreads. Never writes
anything; if the view does not exist yet (migration not applied), it says so and exits.

Usage:
  SUPABASE_URL=https://xxx.supabase.co SUPABASE_SERVICE_KEY=... python ocr_eval.py [--limit N]

  --limit N   only look at the first N rows (most recently updated first); default: all rows.
"""
from __future__ import annotations

import collections
import json
import os
import sys
import urllib.error
import urllib.request

VIEW = "ocr_correction_log"
PAGE_SIZE = 1000
# (label, *_correct column, ocr reading column, staff-approved value column)
FIELDS = [
    ("Instructor", "instructor_correct", "ocr_instructor", "final_instructor_name"),
    ("Subject", "subject_correct", "ocr_subject", "final_subject_code"),
    ("Student ID", "student_id_correct", "ocr_student_id", "final_student_id"),
]


def env(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if not v:
        print(f"Missing required env var {name}.", file=sys.stderr)
        sys.exit(2)
    return v


def fetch_all(base_url: str, key: str, limit: int | None) -> list[dict]:
    rows: list[dict] = []
    offset = 0
    while True:
        page_size = min(PAGE_SIZE, limit - len(rows)) if limit else PAGE_SIZE
        if limit and page_size <= 0:
            break
        url = (f"{base_url.rstrip('/')}/rest/v1/{VIEW}?select=*&order=updated_at.desc"
              f"&limit={page_size}&offset={offset}")
        req = urllib.request.Request(url, headers={"apikey": key, "Authorization": f"Bearer {key}"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                page = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            if e.code == 404 or "does not exist" in body.lower() or "not find" in body.lower():
                print(f"The '{VIEW}' view was not found. Apply migrations/006_ocr_correction_log.sql first.",
                      file=sys.stderr)
                sys.exit(1)
            print(f"HTTP {e.code} fetching {VIEW}: {body[:500]}", file=sys.stderr)
            sys.exit(1)
        except urllib.error.URLError as e:
            print(f"Could not reach {base_url}: {e}", file=sys.stderr)
            sys.exit(1)
        if not page:
            break
        rows.extend(page)
        if len(page) < page_size:
            break
        offset += page_size
    return rows


def field_accuracy(rows: list[dict], correct_field: str) -> tuple[int, int]:
    correct = sum(1 for r in rows if r.get(correct_field) is True)
    total = sum(1 for r in rows if r.get(correct_field) is not None)
    return correct, total


def top_misreads(rows: list[dict], n: int = 10) -> list[tuple[str, str, str, int]]:
    counter: "collections.Counter[tuple[str, str, str]]" = collections.Counter()
    for label, correct_field, ocr_field, final_field in FIELDS:
        for r in rows:
            if r.get(correct_field) is False:
                ocr = str(r.get(ocr_field) or "").strip()
                final = str(r.get(final_field) or "").strip()
                if ocr or final:
                    counter[(label, ocr, final)] += 1
    return [(label, ocr, final, n) for (label, ocr, final), n in counter.most_common(n)]


def main() -> None:
    limit = None
    args = sys.argv[1:]
    if "--limit" in args:
        i = args.index("--limit")
        try:
            limit = int(args[i + 1])
        except (IndexError, ValueError):
            print("--limit needs an integer.", file=sys.stderr)
            sys.exit(2)

    base_url = env("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    if not key:
        # accept the more common Supabase env var name too
        key = env("SUPABASE_SERVICE_ROLE_KEY")

    rows = fetch_all(base_url, key, limit)
    print(f"{len(rows)} resolved website scan(s) in {VIEW}.\n")
    if not rows:
        return

    print("Per-field accuracy (Gemini reading vs the staff-approved value; blank/unreadable pairs are not counted):")
    for label, correct_field, _, _ in FIELDS:
        correct, total = field_accuracy(rows, correct_field)
        pct = f"{100 * correct / total:.1f}%" if total else "n/a (no comparable rows)"
        print(f"  {label:12s} {correct:5d}/{total:<5d} correct  ({pct})")

    top = top_misreads(rows)
    print("\nTop 10 most common misreads (Gemini reading -> staff-approved value):")
    if not top:
        print("  none")
    for label, ocr, final, count in top:
        print(f"  {count:4d}x  [{label}]  {ocr!r} -> {final!r}")


if __name__ == "__main__":
    main()
