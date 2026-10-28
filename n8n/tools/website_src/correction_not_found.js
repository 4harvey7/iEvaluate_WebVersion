// sast-manual-correction: the instructor or subject did not resolve to one database entry. Answered
// HTTP 422 by 'Correction Not Found Response'; nothing is saved, the failed_scan_queue row is NOT
// marked validated and no import_errors row is made, so the scan stays pending in the app and the
// staff member picks the instructor / subject from the list. Input: the 'Map target' item.
const m = $input.first()?.json ?? {};
let body = {};
try { body = $('sast-manual-correction1').first().json.body || {}; } catch (e) {}
let match = {};
try { match = $('Correction Name Match').first().json || {}; } catch (e) {}

const has = (v) => typeof v === 'string' ? v.trim() !== '' : (v !== null && v !== undefined && v !== '');
const noInstructor = !has(m.instructor_ID);
const noSubject = !has(m.true_subject_id);
const text = (v) => (typeof v === 'string' || typeof v === 'number') ? String(v).trim() : '';
const shown = (v) => (v.length > 80 ? v.slice(0, 77) + '...' : v);

function list(v) {
  return Array.isArray(v) ? v.filter((x) => x && typeof x === 'object' && x.id).slice(0, 5) : [];
}
function part(kind, raw, info) {
  const what = kind === 'instructor' ? 'instructor' : 'subject';
  const m2 = info && typeof info === 'object' ? info.match : null;
  if (!raw) return `No ${what} was given.`;
  if (m2 === 'ambiguous') return `Several ${what === 'instructor' ? 'instructors' : 'subjects'} fit "${shown(raw)}".`;
  if (m2 === 'id') return `The selected ${what} can no longer be credited.`;
  return `The ${what} "${shown(raw)}" was not found.`;
}

const iInfo = match.instructor_match || {};
const sInfo = match.subject_match || {};
const code = noInstructor && noSubject ? 'instructor_and_subject_not_found'
  : (noInstructor ? 'instructor_not_found' : 'subject_not_found');
const parts = [];
if (noInstructor) parts.push(part('instructor', text(body.instructor), iInfo));
if (noSubject) parts.push(part('subject', text(body.subject), sInfo));
const pick = noInstructor && noSubject ? 'the instructor and the subject' : (noInstructor ? 'the instructor' : 'the subject');
const message = `Not saved: ${parts.join(' ')} Pick ${pick} from the list and submit again.`;

const candidates = {};
if (noInstructor) {
  candidates.instructors = list(iInfo.candidates).map((c) => ({ id: String(c.id), name: String(c.name || '') }));
}
if (noSubject) {
  candidates.subjects = list(sInfo.candidates).map((c) => ({ id: String(c.id), code: String(c.code || ''), name: String(c.name || '') }));
}

return [{
  json: {
    response_code: 422,
    response: {
      success: false,
      code,
      message,
      instructor_match: noInstructor ? (iInfo.match || 'none') : 'found',
      subject_match: noSubject ? (sInfo.match || 'none') : 'found',
      candidates,
      failed_scan_id: body.failed_scan_id ?? null,
    },
  },
}];
