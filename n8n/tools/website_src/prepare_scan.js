// Throwing here sends the page to 'Website Invalid Request Response'.
const body = $input.first().json.body ?? {};

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const str = (v) => (v === null || v === undefined) ? '' : String(v).trim();
const uuidOrNull = (v) => {
  const s = str(v).toLowerCase();
  return UUID_RE.test(s) ? s : null;
};

const problems = [];
const batchId      = uuidOrNull(body.batch_id);
const instructorId = uuidOrNull(body.instructor_id);
const subjectId    = uuidOrNull(body.subject_id);
const termId       = uuidOrNull(body.term_id);   // null means current term
const stationId    = uuidOrNull(body.station_id);
const staffId      = uuidOrNull(body.staff_id);

if (!batchId) problems.push('batch_id');
if (!instructorId) problems.push('instructor_id');
if (!subjectId) problems.push('subject_id');
if (str(body.term_id) && !termId) problems.push('term_id');
if (str(body.staff_id) && !staffId) problems.push('staff_id');

let image = str(body.image);
if (image.startsWith('data:') && image.includes(',')) image = image.slice(image.indexOf(',') + 1);
image = image.replace(/\s+/g, '');
if (!image || image.length < 100 || !/^[A-Za-z0-9+/]+={0,2}$/.test(image)) problems.push('image');

// optional: a zoomed header crop for a clearer OCR read; invalid/missing -> just dropped,
// never blocks the request (backward compatible with backends that don't send it)
let headerImage = str(body.header_image);
if (headerImage.startsWith('data:') && headerImage.includes(',')) headerImage = headerImage.slice(headerImage.indexOf(',') + 1);
headerImage = headerImage.replace(/\s+/g, '');
if (!headerImage || headerImage.length < 100 || !/^[A-Za-z0-9+/]+={0,2}$/.test(headerImage)) headerImage = '';

const sha = str(body.file_sha256).toLowerCase();
let taskId = str(body.task_id);
if (!taskId && batchId && /^[0-9a-f]{16,64}$/.test(sha)) taskId = `web:${batchId}:${sha.slice(0, 16)}`;
if (!taskId || taskId.length > 200 || /[\r\n]/.test(taskId)) problems.push('task_id');

if (problems.length) {
  throw new Error(`invalid_request: missing or invalid ${problems.join(', ')}`);
}

const mimeRaw = str(body.mime_type).toLowerCase();
const mime = mimeRaw === 'image/png' ? 'image/png' : 'image/jpeg';
const filename = (str(body.filename).replace(/[\\/\r\n]/g, '_') || 'scan.jpg').slice(0, 200);
const paperRaw = str(body.paper_size).toUpperCase();
const paperSize = (paperRaw === 'SHORT' || paperRaw === 'LONG') ? paperRaw : 'LONG';
// optional: the backend found this image file already marked in another batch -> held for review
// (decide.js). Only a batch id and a final status; anything else -> dropped, never blocks the request
const REUSED_STATUSES = ['success', 'flagged', 'rejected', 'duplicate'];
const reusedIn = (body.reused_image && typeof body.reused_image === 'object') ? body.reused_image : {};
const reusedBatchId = uuidOrNull(reusedIn.batch_id);
const reusedStatus = str(reusedIn.status).toLowerCase();
const reusedImage = (reusedBatchId && reusedBatchId !== batchId && REUSED_STATUSES.includes(reusedStatus))
  ? { batch_id: reusedBatchId, status: reusedStatus } : null;

return [{
  json: {
    batch_id: batchId,
    instructor_id: instructorId,
    subject_id: subjectId,
    term_id: termId,
    station_id: stationId,
    staff_id: staffId,
    filename,
    file_sha256: sha,
    task_id: taskId,
    mime_type: mime,
    // unknown sizes fall back to LONG, the scanner default
    paper_size: paperSize,
    image_base64: image,
    // zoomed header crop (base64 JPEG), '' when not sent or invalid
    header_image_base64: headerImage,
    // { batch_id, status } of an earlier batch with the same image file, null when none
    reused_image: reusedImage,
    // used by the remark re-check time budget
    received_at_ms: Date.now(),
  },
}];
