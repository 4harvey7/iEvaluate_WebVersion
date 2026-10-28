# SAST Mark Labeler

A single static page (`index.html`: inline CSS and JS, no libraries, no backend, no network calls) for
labeling photos and scans of REAL paper SAST evaluation forms. The labels are used to build and test a
rule-based OpenCV shape classifier that must accept only check marks (any style) and "/" slashes, and
reject X, shading, circles and dots. Images are stored as the original file bytes (no re-encoding).
Work is kept in the browser's IndexedDB, so a refresh does not lose it.

## Deploy
Upload the single `index.html` to any static host: Netlify Drop, GitHub Pages, Vercel. Nothing else needed.

## Member workflow
1. Fill real paper forms by hand with a pen (vary pens, vary mark styles, include wrong marks like X, shade, circle, dot).
2. Capture each sheet with BOTH the scanner and a phone photo when possible (whole form, flat, good light, no fingers).
3. Open the page, enter your name, load the images, then for each sheet choose paper size, capture, pen,
   and every row's column and mark. Use "Set ALL rows' mark to" when a sheet uses one mark everywhere.
4. For the second capture of the same paper, press "Copy labels from previous entry" (capture is not copied).
5. Press "Download dataset (.zip)" and send the zip to the owner. Keep labeling; zips can be re-downloaded any time.

## Owner
Put the received zips in `C:\Projects\OMR PYTHON DEPLOYMENT\dataset\`.
Each zip holds `images/`, `labels.json` and `labels.csv` (`id,image,labeler,paper_type,capture,pen,key,section,row_no,column,mark,notes`, then appended `width,height,page_cut_off` and `page_|table_` `tl|tr|br|bl` `_x|_y` corner columns; a corners-only image gets one row with empty key/mark).

## Warning
NEVER upload these test sheets through the real app or website. They would be saved as real evaluations.
