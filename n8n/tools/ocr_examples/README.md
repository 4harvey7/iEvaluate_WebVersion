# OCR few-shot examples (website header prompt)

Empty by default. When this folder holds example pairs, `build_website_branch.py` embeds them
as few-shot turns in the "Website AI Validation" Gemini prompt (static, base64-embedded at
build time -- not read at runtime, so they cost nothing per scan).

## How to add examples

Add 3-5 pairs, each two files sharing a name:

- `<name>.jpg` or `<name>.png` -- a real (or representative) scan of the header area, ideally
  the same crop the backend sends (cropped/upscaled top slice of the page; see
  `HEADER_TOP_FRACTION` in `backend/app/omr_prep.py`), showing clearly handwritten fields.
- `<name>.json` -- the CORRECT values for that image, e.g.:

  ```json
  {
    "instructor": "Rodz Harvey D. Vicoyan",
    "subject": "IT 205",
    "student_id": "72-0415"
  }
  ```

Pick examples that cover different handwriting styles/difficulties (e.g. one very messy, one
clean, one with a crossed-out/corrected field). At most 5 are used (by filename order); a name
missing either file, or a JSON that fails to parse or is empty, is skipped.

Never commit a real student's data here unless it is already public/de-identified; use a
made-up name/subject/ID that looks realistic instead.

After adding files, rebuild the workflow (`python build_website_branch.py`) and re-run
`node test_website_code.js`.
