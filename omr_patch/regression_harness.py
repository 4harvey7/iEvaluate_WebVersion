"""
regression_harness.py - compare OMR code versions side by side on labelled images.

Each image is sent through the real Flask /process endpoint (api.py, via
Flask's test client) of every code version, exactly like n8n calls it, and
the ratings are scored against your labels.

Usage (run with the Python env that runs the OMR service - opencv, numpy,
flask, Pillow; EasyOCR is optional and only slows things down):

    python regression_harness.py --images "C:\\path\\to\\photos" --labels labels.csv
        [--original "C:\\Projects\\OMR PYTHON DEPLOYMENT"]   (default)
        [--patched  <folder with patched files>]           (default: ./patched)
        [--version NAME=FOLDER ...]  extra versions; FOLDER's *.py files are laid
                                over --original (use FOLDER=ORIGINAL for none).
                                Default versions: ORIGINAL and PATCHED.
        [--paper-size SHORT]    default when a label row has no paper_size
        [--use-label-paper-size / --no-use-label-paper-size]
        [--only REGEX]          score only filenames matching REGEX
        [--scanner-prep "C:\\...\\app\\backend"]  apply the website backend's
                                prepare_page (orientation + page-size crop +
                                grey pad) first (scanner/website path only -
                                NOT for phone photos)
        [--website-paper-size LONG|backend]  with --scanner-prep: the paper_size
                                the n8n website branch sends. LONG = the old
                                hard-coded value; backend = what prepare_page
                                detected (PreparedPage.paper_size), else LONG.
        [--legal-bed]           simulate a Legal-size (8.5x14) flatbed: pad the
                                bottom of each image with white paper to 14/8.5
                                aspect before anything else
        [--downscale 2200]      cap the long edge first, like the folder watcher
        [--details]             print every wrong row (question, truth, read)
        [--save FILE.json]      save every version's raw per-row results, plus each
                                full /process response with the image dropped and
                                OCR text masked (input for n8n_compat_check.js)

Labels: CSV or JSON.
  CSV header: filename,m1,...,m10,p1,...,p10[,paper_size]
  JSON: a list of objects with those keys, or {"filename": {...}, ...}, or the
  labelling-tool export (a list of {"filename", "paper_type", "rows": [{"answer"}]}).
  A cell is O / VS / S / F / US; blank = empty, "-", "blank" or null.
  "?" (or "X" for double-marked) excludes that row from scoring.

Nothing is written to any code or image folder: each version is assembled in
a temp folder (every *.py of --original, overwritten by the version's files).
"""
import argparse, csv, glob, io, json, os, re, shutil, subprocess, sys, tempfile, contextlib
from concurrent.futures import ThreadPoolExecutor

QUESTIONS = [f"m{i}" for i in range(1, 11)] + [f"p{i}" for i in range(1, 11)]
LETTERS = {"O", "VS", "S", "F", "US"}
IMG_EXT = (".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp")


# ---------------------------------------------------------------- worker
def _redact(j):
    """/process response without personal data: images dropped, OCR text masked."""
    out = {k: v for k, v in j.items() if k not in ("n8n_ocr_image", "image")}
    ocr = j.get("ocr_fields")
    if isinstance(ocr, dict):
        out["ocr_fields"] = {k: ("" if v in (None, "") else "<redacted>") for k, v in ocr.items()}
    return out


def _watcher_like(raw, long_edge):
    """Greyscale JPEG (q85) with the long edge capped, like backend/watcher/folder_watcher.py."""
    from PIL import Image, ImageOps
    img = Image.open(io.BytesIO(raw))
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
        img = Image.alpha_composite(bg, img)
    img = img.convert("L")
    s = long_edge / max(img.size)
    if s < 0.999:
        img = img.resize((max(1, round(img.width * s)), max(1, round(img.height * s))), Image.LANCZOS)
    b = io.BytesIO()
    img.save(b, format="JPEG", quality=85, optimize=True)
    return b.getvalue()


def _legal_bed(raw):
    """Pad the page's bottom with scanner-white to Legal (14/8.5) aspect, as a Legal flatbed
    would return a shorter sheet placed at the top of the glass. Upright portrait assumed
    after rotating landscape inputs clockwise (the backend re-orients anyway)."""
    from PIL import Image, ImageOps
    import numpy as np
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(raw)))
    if img.mode not in ("L", "RGB"):
        img = img.convert("RGB")
    if img.width > img.height:
        img = img.rotate(-90, expand=True)
    target = int(round(img.width * 14 / 8.5))
    if img.height >= target:
        return raw
    a = np.asarray(img.convert("L"))
    paper = int(np.percentile(a[-20:, :], 60))  # the sheet's own bottom-margin white
    fill = paper if img.mode == "L" else (paper, paper, paper)
    out = Image.new(img.mode, (img.width, target), fill)
    out.paste(img, (0, 0))
    b = io.BytesIO()
    out.save(b, format="PNG")
    return b.getvalue()


def worker(code_dir, jobs_path, out_path, scanner_prep, downscale=0, website_ps="LONG", legal_bed=False):
    """Runs inside a subprocess so the code versions never share modules."""
    sys.path.insert(0, code_dir)
    with contextlib.redirect_stdout(io.StringIO()):
        import api  # noqa: E402  (loads utlis / grid_config / mark_detect from code_dir)
    prep = None
    if scanner_prep:
        sys.path.insert(0, scanner_prep)
        from app.logic import prepare_page
        from app.omr_prep import OmrPrepConfig
        prep = lambda raw: prepare_page(raw, omr_prep=OmrPrepConfig())
    client = api.app.test_client()
    jobs = json.load(open(jobs_path, encoding="utf-8"))
    out = {}
    for job in jobs:
        raw = open(job["path"], "rb").read()
        if legal_bed:
            raw = _legal_bed(raw)
        if downscale:
            raw = _watcher_like(raw, downscale)
        paper_size = job["paper_size"]
        prep_info = None
        if prep:
            page = prep(raw)
            raw = page.jpeg
            detected = getattr(page, "paper_size", None)
            prep_info = {"preprocess": page.preprocess, "paper_size": detected}
            # what the n8n website branch sends to the OMR
            paper_size = (detected if (website_ps == "backend" and detected in ("SHORT", "LONG"))
                          else "LONG")
        data = {"image": (io.BytesIO(raw), "scan.jpg"), "paper_size": paper_size,
                "task_id": "regression", "user_id": "regression"}
        with contextlib.redirect_stdout(io.StringIO()):
            r = client.post("/process", data=data, content_type="multipart/form-data")
        j = r.get_json() or {}
        if r.status_code != 200:
            out[job["filename"]] = {"error": j.get("error", f"HTTP {r.status_code}")}
            continue
        rows = j["ratings"]["management"] + j["ratings"]["performance"]
        dbg = j.get("omr_debug", [])
        out[job["filename"]] = {
            "ratings": [c["rating_name"] if c["detected"] else None for c in rows],
            "table_found": j.get("table_found"), "grid_source": j.get("grid_source"),
            "paper_type": j.get("paper_type"), "sent_paper_size": paper_size, "prep": prep_info,
            "scores": [[d["scores"].get(n, 0) for n in ("O", "VS", "S", "F", "US")] if d.get("scores")
                       else [0] * 5 for d in dbg],
            "top_scores": [max(d["scores"].values()) if d.get("scores") else 0 for d in dbg],
            "keys": sorted(j.keys()),
            # Full /process response for n8n_compat_check.js, with personal data removed:
            # no page image, and every OCR text replaced by a placeholder (keys kept).
            "response": _redact(j),
        }
    json.dump(out, open(out_path, "w", encoding="utf-8"))


# ---------------------------------------------------------------- labels
def norm_cell(v):
    s = "" if v is None else str(v).strip().upper()
    if s in ("", "-", "BLANK", "NONE", "NULL"):
        return None
    if s in ("?", "X"):
        return "?"
    if s in LETTERS:
        return s
    raise ValueError(f"unknown label value {v!r}")


def load_labels(path):
    if path.lower().endswith(".json"):
        d = json.load(open(path, encoding="utf-8"))
        rows = [dict(v, filename=k) for k, v in d.items()] if isinstance(d, dict) else d
    else:
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
    labels = {}
    for r in rows:
        if isinstance(r.get("rows"), list):          # labelling-tool export
            ans = sorted(r["rows"], key=lambda x: x["index"])
            labels[r["filename"].strip()] = {
                "truth": [norm_cell(x.get("answer")) for x in ans],
                "paper_size": (r.get("paper_type") or "").strip().upper() or None,
            }
            continue
        r = {k.strip().lower(): v for k, v in r.items()}
        labels[r["filename"].strip()] = {
            "truth": [norm_cell(r.get(q)) for q in QUESTIONS],
            "paper_size": (r.get("paper_size") or "").strip() or None,
        }
    return labels


# ---------------------------------------------------------------- scoring
def score(labels, results):
    s = dict(forms=0, errors=0, rows=0, ok=0, filled=0, filled_ok=0, blanks=0, blanks_ok=0,
             fp=0, fn=0, wrong=0, forms_ok=0, table=0, grid={})
    per_form = {}
    for fn_, lab in labels.items():
        r = results.get(fn_)
        s["forms"] += 1
        if r is None or "error" in r:
            s["errors"] += 1
            per_form[fn_] = None
            continue
        s["table"] += bool(r["table_found"])
        s["grid"][r["grid_source"]] = s["grid"].get(r["grid_source"], 0) + 1
        good = 0
        all_ok = True
        wrong_rows = []
        for q, t, p in zip(QUESTIONS, lab["truth"], r["ratings"]):
            if t == "?":
                continue
            s["rows"] += 1
            hit = t == p
            s["ok"] += hit
            good += hit
            if t is None:
                s["blanks"] += 1
                s["blanks_ok"] += p is None
                if p is not None:
                    s["fp"] += 1
            else:
                s["filled"] += 1
                s["filled_ok"] += hit
                if p is None:
                    s["fn"] += 1
                elif not hit:
                    s["wrong"] += 1
            if not hit:
                all_ok = False
                wrong_rows.append((q, t or "-", p or "-"))
        s["forms_ok"] += all_ok
        per_form[fn_] = (good, wrong_rows)
    return s, per_form


def pct(a, b):
    return f"{a}/{b} ({a / b * 100:.1f}%)" if b else "-"


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--images", required=True)
    ap.add_argument("--labels", required=True)
    ap.add_argument("--original", default=r"C:\Projects\OMR PYTHON DEPLOYMENT")
    ap.add_argument("--patched", default=os.path.join(here, "patched"))
    ap.add_argument("--version", action="append", default=[], metavar="NAME=FOLDER")
    ap.add_argument("--paper-size", default="SHORT")
    ap.add_argument("--only", default=None)
    ap.add_argument("--scanner-prep", default=None)
    ap.add_argument("--website-paper-size", default="LONG", choices=["LONG", "backend"])
    ap.add_argument("--legal-bed", action="store_true")
    ap.add_argument("--downscale", type=int, default=0,
                    help="cap the long edge like the folder watcher (e.g. 2200) before anything else")
    ap.add_argument("--details", action="store_true")
    ap.add_argument("--save", default=None)
    ap.add_argument("--_worker", nargs=3, help=argparse.SUPPRESS)
    a = ap.parse_args()
    if a._worker:
        return worker(a._worker[0], a._worker[1], a._worker[2], a.scanner_prep, a.downscale,
                      a.website_paper_size, a.legal_bed)

    labels = load_labels(a.labels)
    if a.only:
        labels = {f: v for f, v in labels.items() if re.search(a.only, f)}
    files = {os.path.basename(p): p for p in glob.glob(os.path.join(a.images, "*"))
             if p.lower().endswith(IMG_EXT)}
    missing = [f for f in labels if f not in files]
    if missing:
        print(f"[WARN] {len(missing)} labelled file(s) not found in --images")
    labels = {f: v for f, v in labels.items() if f in files}
    if not labels:
        sys.exit("no labelled images found")

    versions = [v.split("=", 1) for v in a.version] or [["ORIGINAL", "ORIGINAL"], ["PATCHED", a.patched]]
    tmp = tempfile.mkdtemp(prefix="omr_regression_")
    try:
        code_dirs = {}
        for name, folder in versions:
            if folder.upper() == "ORIGINAL":
                code_dirs[name] = a.original
                continue
            d = os.path.join(tmp, "code_" + re.sub(r"\W", "_", name))
            os.makedirs(d)
            for p in glob.glob(os.path.join(a.original, "*.py")):
                shutil.copy2(p, d)
            for p in glob.glob(os.path.join(folder, "*.py")):
                shutil.copy2(p, d)
            code_dirs[name] = d
        jobs = [{"filename": f, "path": files[f], "paper_size": v["paper_size"] or a.paper_size}
                for f, v in sorted(labels.items())]
        jobs_path = os.path.join(tmp, "jobs.json")
        json.dump(jobs, open(jobs_path, "w", encoding="utf-8"))

        def run(name):
            outp = os.path.join(tmp, re.sub(r"\W", "_", name) + ".json")
            cmd = [sys.executable, os.path.abspath(__file__), "--images", a.images, "--labels", a.labels,
                   "--_worker", code_dirs[name], jobs_path, outp,
                   "--website-paper-size", a.website_paper_size]
            if a.scanner_prep:
                cmd += ["--scanner-prep", a.scanner_prep]
            if a.downscale:
                cmd += ["--downscale", str(a.downscale)]
            if a.legal_bed:
                cmd += ["--legal-bed"]
            subprocess.run(cmd, check=True)
            return name, json.load(open(outp, encoding="utf-8"))

        print(f"running {', '.join(code_dirs)} on {len(jobs)} image(s) ...", flush=True)
        with ThreadPoolExecutor(max_workers=len(code_dirs)) as ex:
            res = dict(ex.map(run, list(code_dirs)))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if a.save:
        json.dump(res, open(a.save, "w", encoding="utf-8"))
    names = list(code_dirs)
    print(f"\nversions: {', '.join(f'{n}={f}' for n, f in versions)}\ndefault paper_size: {a.paper_size}"
          f"{'   scanner-prep ON (website paper_size: ' + a.website_paper_size + ')' if a.scanner_prep else ''}"
          f"{'   legal-bed' if a.legal_bed else ''}"
          f"{f'   downscale {a.downscale}px' if a.downscale else ''}\n")
    stats = {n: score(labels, r) for n, r in res.items()}
    rows = [
        ("forms (errors)", lambda s: f"{s['forms']} ({s['errors']})"),
        ("table_found", lambda s: pct(s["table"], s["forms"] - s["errors"])),
        ("grid_source", lambda s: ", ".join(f"{k}:{v}" for k, v in sorted(s["grid"].items(), key=str))),
        ("row accuracy", lambda s: pct(s["ok"], s["rows"])),
        ("filled rows correct", lambda s: pct(s["filled_ok"], s["filled"])),
        ("blank rows read blank", lambda s: pct(s["blanks_ok"], s["blanks"])),
        ("false marks on blanks", lambda s: str(s["fp"])),
        ("misses (read blank)", lambda s: str(s["fn"])),
        ("wrong column", lambda s: str(s["wrong"])),
        ("forms fully correct", lambda s: pct(s["forms_ok"], s["forms"])),
    ]
    w = 26
    print(f"{'metric':24}" + "".join(f"{n:>{w}}" for n in names))
    for label, f in rows:
        print(f"{label:24}" + "".join(f"{f(stats[n][0]):>{w}}" for n in names))

    base = names[0]
    print(f"\nper form (rows correct, {' -> '.join(names)}):")
    worse = {n: [] for n in names[1:]}
    for fn_ in sorted(labels):
        vals = [stats[n][1][fn_] for n in names]
        cells = [str(v[0]) if v else "ERR" for v in vals]
        tags = []
        for n, v in zip(names[1:], vals[1:]):
            b = stats[base][1][fn_]
            if b and v and v[0] < b[0]:
                tags.append(f"{n} WORSE")
                worse[n].append(fn_)
        print(f"  {fn_:40} " + " -> ".join(f"{c:>3}" for c in cells) + ("   <-- " + ", ".join(tags) if tags else ""))
        if a.details:
            for n, x in zip(names, vals):
                if x and x[1]:
                    print(f"      {n}: " + ", ".join(f"{q} truth {t} read {r}" for q, t, r in x[1]))
    for n in names[1:]:
        print(f"forms made WORSE than {base} by {n}: {len(worse[n])}")


if __name__ == "__main__":
    main()
