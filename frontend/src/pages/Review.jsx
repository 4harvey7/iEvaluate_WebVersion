import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { api, REVIEW_DRAFT_PREFIX } from "../api.js";
import { ConfirmDialog, EmptyState, Loading, PageHeader, timeAgo, useNow } from "../components.jsx";
import { Icon } from "../icons.jsx";
import { useSearch } from "../useSearch.js";
import { friendlyReason, importErrorText, matchText, reviewErrorText, unsavedStateText } from "../messages.js";
import { PageViewer, ScanImage, ScoreChip, scoreFromRatings } from "../pageViewer.jsx";

// Live = polling through the backend (the browser never talks to Supabase).
const POLL_VISIBLE_MS = 3000;
const POLL_HIDDEN_MS = 10000;
const FULL_RELOAD_MS = 60000;
const CLAIM_REFRESH_MS = 60000;
const TOAST_MS = 6000;

const RATING_KEYS = [
  ...Array.from({ length: 10 }, (_, i) => `m${i + 1}`),
  ...Array.from({ length: 10 }, (_, i) => `p${i + 1}`),
];
const MISMATCH_FOUND = new Set(["instructor_mismatch_found", "subject_mismatch_found"]);
const CONFLICT_CODES = new Set(["stale_row", "already_resolved", "in_progress"]);

const QUEUE_FILTERS = [
  { key: "all", label: "All" },
  { key: "instructor", label: "Instructor" },
  { key: "subject", label: "Subject" },
  { key: "omr", label: "Answers (OMR)" },
];
const IMPORT_FILTERS = [
  { key: "all", label: "All" },
  { key: "instructor", label: "Instructor" },
  { key: "subject", label: "Subject" },
];

// ------------------------------------------------------------------ helpers
const ts = (v) => {
  const t = Date.parse(v || "");
  return Number.isNaN(t) ? 0 : t;
};
const byCreated = (a, b) => ts(a.created_at) - ts(b.created_at) || String(a.id).localeCompare(String(b.id));
const pct = (v) => (v === null || v === undefined ? "" : `${Math.round(Number(v) * 100)}%`);
const subjectLabel = (s) => (s ? [s.code, s.name].filter(Boolean).join(" - ") : "") || "Unknown subject";
const personLabel = (p) => p?.name || "Unknown instructor";
const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
const PICK_FIRST_TEXT = "Pick the instructor and the subject first.";

// A row is replaced only by a newer version of itself (edits live in drafts, never here).
function isNewer(a, b) {
  const va = Number(a.review_version) || 0;
  const vb = Number(b.review_version) || 0;
  if (va !== vb) return va > vb;
  return ts(a.updated_at) > ts(b.updated_at);
}

function mergeRows(prev, incoming, { full = false, trust = false } = {}) {
  const next = full ? new Map() : new Map(prev);
  for (const r of incoming) {
    if (!r || r.id === undefined || r.id === null) continue;
    const cur = prev.get(r.id);
    next.set(r.id, !cur || trust || isNewer(r, cur) ? r : cur);
  }
  return next;
}

// "Couldn't be saved" rows: no drafts, the server is the truth; keep the
// highest version (an action's answer can arrive before an older poll).
function mergeUnsaved(prev, incoming, full = false) {
  const next = full ? new Map() : new Map(prev);
  for (const r of incoming) {
    if (!r || !r.id) continue;
    const cur = prev.get(r.id);
    next.set(r.id, !cur || Number(r.version) >= Number(cur.version) ? r : cur);
  }
  return next;
}

function newIds(prevNew, prevMap, nextMap) {
  const out = new Set([...prevNew].filter((id) => nextMap.has(id)));
  for (const id of nextMap.keys()) if (!prevMap.has(id)) out.add(id);
  return out;
}

const reasonsText = (row) => (row.review_reasons || []).join(", ").toLowerCase();

// Same substring rules as the Flutter validation screen tabs.
const QUEUE_CATEGORY = {
  all: () => true,
  instructor: (r) => reasonsText(r).includes("instructor") || !(r.suggested_instructor?.name || "").trim(),
  subject: (r) =>
    reasonsText(r).includes("subject") || !(r.suggested_subject?.code || r.suggested_subject?.name || "").trim(),
  omr: (r) => {
    const t = reasonsText(r);
    return (
      ["omr", "bubble", "unfilled", "multiple_mark", "multiple mark", "blur"].some((k) => t.includes(k)) ||
      (r.table_found === false && (t.includes("table") || t.includes("corner")))
    );
  },
};
const IMPORT_CATEGORY = {
  all: () => true,
  instructor: (r) => String(r.error_type || "").toLowerCase().includes("instructor"),
  subject: (r) => String(r.error_type || "").toLowerCase().includes("subject"),
};

const hasInstructorMismatch = (r) =>
  (r.review_reasons || []).some((x) => String(x).startsWith("instructor_mismatch_")) ||
  ["found", "not_found", "ambiguous"].includes(r.instructor_match);
const hasSubjectMismatch = (r) =>
  (r.review_reasons || []).some((x) => String(x).startsWith("subject_mismatch_")) ||
  ["found", "not_found", "ambiguous"].includes(r.subject_match);

// Credited, but n8n couldn't store it (instructor/subject not found): it waits in Import errors.
// n8n answers before that is known, so this is re-checked on every poll.
const movedToImports = (row, importRow, pendingTasks) =>
  row.status === "validated" && ((importRow && importRow.status === "pending") || !!pendingTasks?.has(row.task_id));

function queueState(row, importRow, pendingTasks) {
  if (row.review_state === "submitting") return { label: "Sending...", tone: "proc" };
  if (row.status === "validated") {
    if (movedToImports(row, importRow, pendingTasks)) return { label: "Now in Import errors", tone: "flagged" };
    return { label: "Done", tone: "success" };
  }
  if (row.status === "discarded") return { label: "Discarded", tone: "duplicate" };
  if (row.status !== "pending") return { label: "Done", tone: "success" };
  if (row.review_state === "submit_failed") return { label: "Not sent - try again", tone: "error" };
  if (hasInstructorMismatch(row)) return { label: "Check instructor", tone: "flagged" };
  if (hasSubjectMismatch(row)) return { label: "Check subject", tone: "flagged" };
  return { label: "Needs a look", tone: "flagged" };
}

function importState(row) {
  if (row.review_state === "submitting") return { label: "Sending...", tone: "proc" };
  if (row.status === "resolved") return { label: "Fixed", tone: "success" };
  if (row.status === "discarded") return { label: "Discarded", tone: "duplicate" };
  if (row.status !== "pending") return { label: "Done", tone: "success" };
  if (row.review_state === "submit_failed") return { label: "Not sent - try again", tone: "error" };
  return { label: importErrorText(row.error_type), tone: "flagged" };
}

function queueDefaults(row) {
  // After a failed send, start from what was sent; otherwise from the suggestion.
  const c = row.status === "pending" && row.review_state === "submit_failed" ? row.correction : null;
  const pick = (p) => (p?.id ? { id: p.id, name: p.name || null } : null);
  const pickS = (s) => (s?.id ? { id: s.id, code: s.code || null, name: s.name || null } : null);
  return {
    instructor: c?.instructor?.id ? pick(c.instructor) : pick(row.suggested_instructor),
    subject: c?.subject?.id ? pickS(c.subject) : pickS(row.suggested_subject),
    student_id: (c ? c.student_id : row.student_id) || "",
    remarks: (c ? c.remarks : row.remarks) || "",
    ratings: { ...((c ? c.ratings : row.ratings) || {}) },
  };
}

function importDefaults(row) {
  return {
    instructor: null,
    subject: null,
    student_id: row.student_id || "",
    remarks: row.remarks || "",
    ratings: { ...(row.ratings || {}) },
  };
}

function cleanRatings(ratings) {
  const out = {};
  for (const k of RATING_KEYS) {
    const v = Number(ratings?.[k]);
    out[k] = Number.isInteger(v) && v >= 1 && v <= 5 ? v : null;
  }
  return out;
}

function sendBody(values, version, confirmOverwrite) {
  return {
    expected_version: version,
    instructor_id: values.instructor?.id,
    subject_id: values.subject?.id,
    student_id: values.student_id || "",
    remarks: values.remarks || "",
    ratings: cleanRatings(values.ratings),
    confirm_overwrite: !!confirmOverwrite,
  };
}

// Mismatch forms whose only problem is "the sheet names someone else, and we
// know who": these can be credited together to the same suggestion.
function bulkGroups(rows, drafts) {
  const groups = new Map();
  for (const r of rows) {
    if (r.status !== "pending" || r.review_state) continue;
    if (drafts[`q:${r.id}`]) continue; // being edited: leave it to the editor
    const reasons = r.review_reasons || [];
    if (!reasons.length || !reasons.every((x) => MISMATCH_FOUND.has(x))) continue;
    const si = r.suggested_instructor;
    const ss = r.suggested_subject;
    if (!si?.id || !ss?.id) continue;
    if (si.id === r.batch_instructor?.id && ss.id === r.batch_subject?.id) continue;
    const key = `${si.id}|${ss.id}`;
    if (!groups.has(key)) groups.set(key, { key, instructor: si, subject: ss, rows: [] });
    groups.get(key).rows.push(r);
  }
  return [...groups.values()].filter((g) => g.rows.length >= 2);
}

// ------------------------------------------------------------------ drafts (sessionStorage)
const DRAFT_PREFIX = REVIEW_DRAFT_PREFIX;

function readDrafts(batchId) {
  try {
    const raw = sessionStorage.getItem(DRAFT_PREFIX + batchId);
    const d = raw ? JSON.parse(raw) : {};
    return d && typeof d === "object" && !Array.isArray(d) ? d : {};
  } catch {
    return {};
  }
}

function writeDrafts(batchId, drafts) {
  try {
    if (Object.keys(drafts).length) sessionStorage.setItem(DRAFT_PREFIX + batchId, JSON.stringify(drafts));
    else sessionStorage.removeItem(DRAFT_PREFIX + batchId);
  } catch {
    /* storage full or blocked: drafts live in memory only */
  }
}

function useDrafts(batchId) {
  const [drafts, setDrafts] = useState(() => readDrafts(batchId));
  const setDraft = useCallback(
    (key, draft) => {
      setDrafts((prev) => {
        const next = { ...prev };
        if (draft) next[key] = draft;
        else delete next[key];
        writeDrafts(batchId, next);
        return next;
      });
    },
    [batchId]
  );
  return [drafts, setDraft];
}

// ------------------------------------------------------------------ live data
function useReviewData(batchId) {
  const [state, setState] = useState({
    loaded: false,
    batch: null,
    counts: {},
    meta: {},
    queue: new Map(),
    imports: new Map(),
    unsaved: new Map(),
    newQueue: new Set(),
    newImports: new Set(),
    pendingTasks: new Set(),
    lastOkAt: 0,
  });
  const [error, setError] = useState("");
  const [fatal, setFatal] = useState(null); // "not_found" | "forbidden"
  const cursor = useRef(null);
  const lastFull = useRef(0);
  const inFlight = useRef(false);

  const apply = useCallback((d) => {
    setState((prev) => {
      const opts = { full: !!d.full, trust: !d.migration_ready };
      const queue = mergeRows(prev.queue, d.queue || [], opts);
      const imports = mergeRows(prev.imports, d.import_errors || [], opts);
      const unsaved = mergeUnsaved(prev.unsaved, d.unsaved || [], !!d.full);
      return {
        loaded: true,
        batch: d.batch || prev.batch,
        counts: { ...(d.counts || {}), unsaved_pending: Number(d.unsaved_pending) || 0 },
        meta: {
          migration_ready: !!d.migration_ready,
          correction_ready: d.correction_ready !== false,
          import_correction_ready: d.import_correction_ready !== false,
          unsaved_ready: d.unsaved_ready !== false,
          retry_ready: d.retry_ready !== false,
          access: d.access || "full",
        },
        queue,
        imports,
        unsaved,
        // Pages that arrive after the first load wait behind an "N new" button
        // so the list never jumps under the reviewer.
        newQueue: prev.loaded ? newIds(prev.newQueue, prev.queue, queue) : new Set(),
        newImports: prev.loaded ? newIds(prev.newImports, prev.imports, imports) : new Set(),
        pendingTasks: new Set(d.import_pending_tasks || []),
        lastOkAt: Date.now(),
      };
    });
  }, []);

  const poll = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    const full = !cursor.current || Date.now() - lastFull.current >= FULL_RELOAD_MS;
    try {
      const d = await api.getReviews(batchId, full ? null : cursor.current);
      if (d.full) lastFull.current = Date.now();
      cursor.current = d.cursor || null;
      apply(d);
      setError("");
      setFatal(null);
    } catch (err) {
      if (err?.status === 404) setFatal("not_found");
      else if (err?.status === 403) setFatal("forbidden");
      else setError(err?.message || "Can't refresh right now.");
    } finally {
      inFlight.current = false;
    }
  }, [batchId, apply]);

  useEffect(() => {
    let timer;
    let stopped = false;
    const schedule = (ms) => {
      clearTimeout(timer);
      timer = setTimeout(tick, ms);
    };
    async function tick() {
      await poll();
      if (!stopped) schedule(document.hidden ? POLL_HIDDEN_MS : POLL_VISIBLE_MS);
    }
    const onVisible = () => {
      if (!document.hidden && !stopped) tick();
    };
    tick();
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      stopped = true;
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [poll]);

  // Next poll re-reads everything (after a conflict: be sure we show the truth).
  const refresh = useCallback(() => {
    lastFull.current = 0;
    poll();
  }, [poll]);

  const mergeRow = useCallback((kind, row) => {
    if (!row) return;
    setState((prev) =>
      kind === "import"
        ? { ...prev, imports: mergeRows(prev.imports, [row]) }
        : kind === "unsaved"
          ? { ...prev, unsaved: mergeUnsaved(prev.unsaved, [row]) }
          : { ...prev, queue: mergeRows(prev.queue, [row]) }
    );
  }, []);

  const reveal = useCallback((kind) => {
    setState((prev) => (kind === "import" ? { ...prev, newImports: new Set() } : { ...prev, newQueue: new Set() }));
  }, []);

  return { ...state, error, fatal, refresh, mergeRow, reveal };
}

// ------------------------------------------------------------------ small pieces
function LiveBadge({ lastOkAt, error, now }) {
  const fresh = lastOkAt && now - lastOkAt < 20000 && !error;
  return (
    <span className={`live-badge ${fresh ? "on" : "off"}`} title={lastOkAt ? `Updated ${timeAgo(new Date(lastOkAt).toISOString(), now)}` : ""}>
      <span className="live-dot" aria-hidden="true" />
      {fresh ? "Live" : "Reconnecting..."}
    </span>
  );
}

function ChoiceList({ legend, name, options, value, onChange, onSomeoneElse, someoneElseLabel }) {
  return (
    <fieldset className="choice-list">
      <legend>{legend}</legend>
      {options.map((o) => {
        const checked = value?.id === o.value.id;
        return (
          <label key={o.key} className={`choice ${checked ? "checked" : ""}`}>
            <input type="radio" name={name} checked={checked} onChange={() => onChange(o.value)} />
            <span>
              <strong>{o.label}</strong>
              {o.hint && <span className="muted small"> {o.hint}</span>}
            </span>
          </label>
        );
      })}
      {onSomeoneElse && (
        <button type="button" className="small-btn someone-else" onClick={onSomeoneElse}>{someoneElseLabel}</button>
      )}
    </fieldset>
  );
}

function InstructorSearch({ id, initialQuery, onPick, onCancel }) {
  const [query, setQuery] = useState(initialQuery || "");
  const { results, loading, error } = useSearch(api.searchInstructors, query);
  return (
    <div className="picker">
      <label htmlFor={id}>Find the instructor</label>
      <input id={id} value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Type at least 2 letters of the name..." autoComplete="off" autoFocus />
      {loading && <p className="muted small">Searching...</p>}
      {error && <div className="error">{error}</div>}
      {results.slice(0, 8).map((r) => (
        <button type="button" key={r.id} className="result-item" onClick={() => onPick({ id: r.id, name: r.full_name })}>
          {r.full_name}
          {r.department_name && <span className="muted"> - {r.department_name}</span>}
          {r.is_placeholder && <span className="badge">login not activated yet</span>}
        </button>
      ))}
      {!loading && query.trim().length >= 2 && results.length === 0 && !error && (
        <p className="muted small">No instructor found with that name. Check the spelling.</p>
      )}
      <button type="button" className="small-btn picker-cancel" onClick={onCancel}>Cancel</button>
    </div>
  );
}

function SubjectSearch({ id, initialQuery, onPick, onCancel }) {
  const [query, setQuery] = useState(initialQuery || "");
  const { results, loading, error } = useSearch(api.searchSubjects, query);
  return (
    <div className="picker">
      <label htmlFor={id}>Find the subject</label>
      <input id={id} value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Type a subject code or name..." autoComplete="off" autoFocus />
      {loading && <p className="muted small">Searching...</p>}
      {error && <div className="error">{error}</div>}
      {results.slice(0, 8).map((r) => (
        <button type="button" key={r.id} className="result-item" onClick={() => onPick({ id: r.id, code: r.subject_code, name: r.subject_name })}>
          <strong>{r.subject_code}</strong> - {r.subject_name}
        </button>
      ))}
      {!loading && query.trim().length >= 2 && results.length === 0 && !error && (
        <p className="muted small">No subject found. Check the code or name.</p>
      )}
      <button type="button" className="small-btn picker-cancel" onClick={onCancel}>Cancel</button>
    </div>
  );
}

function RatingsEditor({ idPrefix, ratings, comparisons, onChange }) {
  const comp = useMemo(() => Object.fromEntries((comparisons || []).map((c) => [c.question, c])), [comparisons]);
  const group = (prefix, title) => (
    <fieldset className="ratings-group">
      <legend>{title}</legend>
      <div className="ratings-grid">
        {Array.from({ length: 10 }, (_, i) => {
          const k = `${prefix}${i + 1}`;
          const c = comp[k];
          const disagree = c && c.agree === false;
          return (
            <div key={k} className={`rating-cell ${disagree ? "disagree" : ""}`}>
              <label htmlFor={`${idPrefix}-${k}`} className="rating-key">{k.toUpperCase()}</label>
              <select
                id={`${idPrefix}-${k}`}
                value={ratings?.[k] ?? ""}
                onChange={(e) => onChange(k, e.target.value === "" ? null : Number(e.target.value))}
              >
                <option value="">-</option>
                {[5, 4, 3, 2, 1].map((n) => <option key={n} value={n}>{n}</option>)}
              </select>
              {disagree && (
                <span className="rating-hint" title="The answer reader and the AI read this answer differently">
                  reader {c.python ?? "-"} · AI {c.gemini ?? "-"}
                </span>
              )}
            </div>
          );
        })}
      </div>
    </fieldset>
  );
  return (
    <div className="ratings">
      <p className="muted small ratings-scale">5 Outstanding · 4 Very satisfactory · 3 Satisfactory · 2 Fair · 1 Unsatisfactory · - blank</p>
      {group("m", "Management (M1 to M10)")}
      {group("p", "Performance (P1 to P10)")}
    </div>
  );
}

function MatchSummary({ row }) {
  if (!hasInstructorMismatch(row) && !hasSubjectMismatch(row)) return null;
  const bi = row.batch_instructor || {};
  const bs = row.batch_subject || {};
  const si = row.suggested_instructor || {};
  const ss = row.suggested_subject || {};
  const conf = (v) => (v === null || v === undefined ? "" : ` (${pct(v)} sure)`);
  return (
    <div className="match-summary">
      <table className="match-table">
        <thead>
          <tr>
            <th scope="col"><span className="sr-only">Source</span></th>
            <th scope="col">Instructor</th>
            <th scope="col">Subject</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <th scope="row">On the sheet</th>
            <td>{row.ocr_instructor ? `"${row.ocr_instructor}"` : "-"}</td>
            <td>{row.ocr_subject ? `"${row.ocr_subject}"` : "-"}</td>
          </tr>
          <tr>
            <th scope="row">This batch</th>
            <td>{personLabel(bi)}</td>
            <td>{subjectLabel(bs)}</td>
          </tr>
          <tr className="suggested">
            <th scope="row">Suggested</th>
            <td>{personLabel(si)}{conf(si.confidence)}</td>
            <td>{subjectLabel(ss)}{conf(ss.confidence)}</td>
          </tr>
          <tr>
            <th scope="row">Check</th>
            <td className={`match-${row.instructor_match}`}>{matchText("instructor", row.instructor_match)}</td>
            <td className={`match-${row.subject_match}`}>{matchText("subject", row.subject_match)}</td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

function ChangedBanner({ finished, onUseNew, onKeep }) {
  return (
    <div className="warning-box small changed-banner" role="status">
      <strong>Changed elsewhere.</strong>{" "}
      {finished
        ? "This form was finished somewhere else while you were editing, so your edits can't be sent."
        : "Someone changed this form while you were editing it (for example on the phone app). Your edits are kept."}
      <div className="btn-row">
        <button type="button" className="small-btn" onClick={onUseNew}>{finished ? "Drop my edits" : "Use the new version"}</button>
        {!finished && <button type="button" className="small-btn" onClick={onKeep}>Keep my edits</button>}
      </div>
    </div>
  );
}

function OverwriteDialog({ detail, busy, onConfirm, onCancel }) {
  return (
    <ConfirmDialog
      title="Replace the form that is already saved?"
      confirmLabel="Yes, replace it"
      cancelLabel="Cancel"
      danger
      busy={busy}
      onConfirm={onConfirm}
      onCancel={onCancel}
    >
      <p>{detail?.message || "A form from this student is already saved for this instructor and subject."}</p>
      <p>Only replace it if this sheet is the right one (for example the student ID was misread on the other one).</p>
    </ConfirmDialog>
  );
}

function ReasonList({ reasons }) {
  const lines = [...new Set((reasons || []).map(friendlyReason).filter(Boolean))];
  if (!lines.length) return null;
  return (
    <ul className="reason-list">
      {lines.map((t) => <li key={t}>{t}</li>)}
    </ul>
  );
}

// ------------------------------------------------------------------ queue editor
function QueueEditor({ row, importRow, pendingTasks, draft, setDraft, meta, me, onRowUpdate, onClose, onRefresh, showToast }) {
  const key = `q:${row.id}`;
  const defaults = useMemo(() => queueDefaults(row), [row]);
  const values = draft || defaults;
  const baseVersion = draft ? draft.base_version : row.review_version;
  const finished = row.status !== "pending";
  const sending = row.review_state === "submitting";
  const changedElsewhere = !!draft && Number(row.review_version) > Number(draft.base_version);
  const canAct = meta.migration_ready && meta.correction_ready && !finished && !sending;
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  const [overwrite, setOverwrite] = useState(null); // {detail, values}
  const [discarding, setDiscarding] = useState(false);
  const [note, setNote] = useState("");
  const [claim, setClaim] = useState(null);
  const [findInstructor, setFindInstructor] = useState(false);
  const [findSubject, setFindSubject] = useState(false);
  const label = row.filename || "This form";

  const edit = (patch) => setDraft(key, { ...values, ...patch, base_version: baseVersion });

  // Advisory "I'm looking at this" marker, refreshed while the editor is open.
  useEffect(() => {
    if (!meta.migration_ready || row.status !== "pending") return undefined;
    let stopped = false;
    const doClaim = () =>
      api.claimReview(row.id).then((r) => { if (!stopped) setClaim(r); }).catch(() => {});
    doClaim();
    const t = setInterval(doClaim, CLAIM_REFRESH_MS);
    return () => {
      stopped = true;
      clearInterval(t);
      api.claimReview(row.id, true).catch(() => {});
    };
  }, [row.id, meta.migration_ready]); // eslint-disable-line react-hooks/exhaustive-deps

  const viewer = (claim && !claim.claimed && claim.claimed_by) || row.claimed_by;
  const otherViewer = viewer && viewer.id !== me ? viewer : null;

  async function send(vals, confirmOverwrite = false) {
    if (!vals.instructor?.id || !vals.subject?.id) {
      setProblem(PICK_FIRST_TEXT);
      return;
    }
    setBusy(true);
    setProblem("");
    try {
      const res = await api.approveReview(row.id, sendBody(vals, baseVersion, confirmOverwrite));
      onRowUpdate(res.row);
      setDraft(key, null);
      setOverwrite(null);
      const who = `${personLabel(vals.instructor)}${vals.subject?.code ? ` · ${vals.subject.code}` : ""}`;
      if (res.outcome === "submitted") showToast(`${label}: sent. Waiting for marking to confirm it...`, "info");
      else if (res.outcome === "import_error") showToast(`${label}: sent, but it couldn't be saved yet. It is now under Import errors.`, "warn");
      else showToast(`${label}: sent and credited to ${who}.`);
      onClose();
    } catch (err) {
      if (err?.detail?.row) onRowUpdate(err.detail.row);
      if (err?.code === "would_overwrite") {
        setOverwrite({ detail: err.detail, values: vals });
      } else if (err?.code === "correction_failed") {
        // Our own failed send bumped the version: keep the edits on top of it.
        const v = err.detail?.row?.review_version;
        setDraft(key, { ...vals, base_version: v ?? baseVersion });
        setOverwrite(null);
        setProblem(err.message);
      } else {
        setOverwrite(null);
        if (err?.code === "stale_row") setDraft(key, { ...vals, base_version: baseVersion });
        setProblem(reviewErrorText(err));
        if (CONFLICT_CODES.has(err?.code)) onRefresh();
      }
    } finally {
      setBusy(false);
    }
  }

  async function discard() {
    setBusy(true);
    setProblem("");
    try {
      const res = await api.rejectReview(row.id, { expected_version: baseVersion, note: note.trim() || null });
      onRowUpdate(res.row);
      setDraft(key, null);
      setDiscarding(false);
      showToast(`${label}: discarded. It will not be counted.`);
      onClose();
    } catch (err) {
      setDiscarding(false);
      setProblem(reviewErrorText(err));
      if (CONFLICT_CODES.has(err?.code)) onRefresh();
    } finally {
      setBusy(false);
    }
  }

  const bi = row.batch_instructor || {};
  const bs = row.batch_subject || {};
  const si = row.suggested_instructor || {};
  const ss = row.suggested_subject || {};

  const instructorOptions = useMemo(() => {
    const opts = [];
    const seen = new Set();
    const add = (p, hint) => {
      if (!p?.id || seen.has(p.id)) return;
      seen.add(p.id);
      opts.push({ key: p.id, value: { id: p.id, name: p.name || null }, label: personLabel(p), hint });
    };
    if (si.id && si.id !== bi.id) add(si, `suggested${si.confidence != null ? `, ${pct(si.confidence)} match` : ""}`);
    add(bi, "this batch's instructor");
    for (const c of row.instructor_candidates || []) add(c, c.is_batch ? "this batch's instructor" : `possible match${c.score != null ? `, ${pct(c.score)}` : ""}`);
    if (values.instructor?.id) add(values.instructor, "picked by you");
    return opts;
  }, [row, values.instructor]); // eslint-disable-line react-hooks/exhaustive-deps

  const subjectOptions = useMemo(() => {
    const opts = [];
    const seen = new Set();
    const add = (s, hint) => {
      if (!s?.id || seen.has(s.id)) return;
      seen.add(s.id);
      opts.push({ key: s.id, value: { id: s.id, code: s.code || null, name: s.name || null }, label: subjectLabel(s), hint });
    };
    if (ss.id && ss.id !== bs.id) add(ss, `suggested${ss.confidence != null ? `, ${pct(ss.confidence)} match` : ""}`);
    add(bs, "this batch's subject");
    for (const c of row.subject_candidates || []) add(c, c.is_batch ? "this batch's subject" : `possible match${c.score != null ? `, ${pct(c.score)}` : ""}`);
    if (values.subject?.id) add(values.subject, "picked by you");
    return opts;
  }, [row, values.subject]); // eslint-disable-line react-hooks/exhaustive-deps

  const diffI = values.instructor?.id && bi.id && values.instructor.id !== bi.id;
  const diffS = values.subject?.id && bs.id && values.subject.id !== bs.id;
  let batchLabel = null;
  if (diffI && diffS) batchLabel = `Credit to this batch (${personLabel(bi)} · ${bs.code || subjectLabel(bs)})`;
  else if (diffI) batchLabel = `Credit to batch instructor (${personLabel(bi)})`;
  else if (diffS) batchLabel = `Credit to batch subject (${bs.code || subjectLabel(bs)})`;
  const toBatch = () =>
    send({
      ...values,
      instructor: diffI ? { id: bi.id, name: bi.name } : values.instructor,
      subject: diffS ? { id: bs.id, code: bs.code, name: bs.name } : values.subject,
    });

  const finishedText = (() => {
    if (row.status === "discarded") {
      return `Discarded${row.validated_by_name ? ` by ${row.validated_by_name}` : ""}${row.review_note_by_reviewer ? `: "${row.review_note_by_reviewer}"` : "."}`;
    }
    if (movedToImports(row, importRow, pendingTasks)) return "Sent, but it couldn't be saved yet: see Import errors below.";
    const c = row.correction;
    const by = row.validated_by_name ? ` by ${row.validated_by_name}` : "";
    if (c?.instructor?.name && row.review_action) {
      return `Credited to ${c.instructor.name}${c.subject?.code ? ` · ${c.subject.code}` : ""}${by}.`;
    }
    return `Already fixed${by || " elsewhere (for example on the phone app)"}.`;
  })();

  return (
    <div className="review-editor" id={`editor-q-${row.id}`}>
      {changedElsewhere && (
        <ChangedBanner
          finished={finished}
          onUseNew={() => setDraft(key, null)}
          onKeep={() => setDraft(key, { ...draft, base_version: row.review_version })}
        />
      )}
      {otherViewer && !finished && (
        <div className="warning-box small">{otherViewer.name || "Another staff member"} is also looking at this form right now.</div>
      )}
      {sending && <div className="info-box">Sent to marking. Waiting for it to confirm... This updates by itself.</div>}
      {!finished && !sending && row.review_state === "submit_failed" && (
        <div className="error">Not sent: {row.review_error || "the last try failed."}</div>
      )}
      {finished && <div className="success-box small">{finishedText}</div>}

      <div className="editor-grid">
        <div className="editor-image">
          <ScanImage kind="queue" id={row.id} />
        </div>
        <div className="editor-fields">
          <ReasonList reasons={row.review_reasons} />
          {row.review_note && (
            <details className="review-details">
              <summary>Details from marking</summary>
              <p>{row.review_note}</p>
              {row.agreement_percent != null && <p className="small">Answer reader and AI agree on {row.agreement_percent}% of the answers.</p>}
            </details>
          )}
          <MatchSummary row={row} />

          <fieldset className="editor-form" disabled={!canAct || busy}>
            {findInstructor ? (
              <InstructorSearch
                id={`find-i-${row.id}`}
                initialQuery={row.ocr_instructor || ""}
                onPick={(p) => { edit({ instructor: p }); setFindInstructor(false); }}
                onCancel={() => setFindInstructor(false)}
              />
            ) : (
              <ChoiceList
                legend="Credit this form to"
                name={`inst-${row.id}`}
                options={instructorOptions}
                value={values.instructor}
                onChange={(p) => edit({ instructor: p })}
                onSomeoneElse={() => setFindInstructor(true)}
                someoneElseLabel="Someone else..."
              />
            )}
            {findSubject ? (
              <SubjectSearch
                id={`find-s-${row.id}`}
                initialQuery={row.ocr_subject || ""}
                onPick={(s) => { edit({ subject: s }); setFindSubject(false); }}
                onCancel={() => setFindSubject(false)}
              />
            ) : (
              <ChoiceList
                legend="Subject"
                name={`subj-${row.id}`}
                options={subjectOptions}
                value={values.subject}
                onChange={(s) => edit({ subject: s })}
                onSomeoneElse={() => setFindSubject(true)}
                someoneElseLabel="Another subject..."
              />
            )}

            <div className="grid-2">
              <div>
                <label htmlFor={`sid-${row.id}`}>Student ID</label>
                <input id={`sid-${row.id}`} value={values.student_id} maxLength={50} autoComplete="off"
                  onChange={(e) => edit({ student_id: e.target.value })} />
              </div>
            </div>
            <label htmlFor={`rem-${row.id}`}>Remarks</label>
            <textarea id={`rem-${row.id}`} rows={3} maxLength={4000} value={values.remarks}
              onChange={(e) => edit({ remarks: e.target.value })} />

            <RatingsEditor
              idPrefix={`r-${row.id}`}
              ratings={values.ratings}
              comparisons={row.comparisons}
              onChange={(k, v) => edit({ ratings: { ...values.ratings, [k]: v } })}
            />
          </fieldset>

          {problem && <div className="error" role="alert">{problem}</div>}
          {!meta.migration_ready && <p className="muted small">Read-only until the database update (migration 005) is applied.</p>}
          {meta.migration_ready && !meta.correction_ready && <p className="muted small">Sending corrections isn't set up on this server yet.</p>}
          {!finished && (!values.instructor?.id || !values.subject?.id) && problem !== PICK_FIRST_TEXT && (
            <p className="small">{PICK_FIRST_TEXT}</p>
          )}

          <div className="editor-actions">
            {!finished && (
              <>
                <button type="button" className="primary" disabled={!canAct || busy || !values.instructor?.id || !values.subject?.id} onClick={() => send(values)}>
                  <Icon name="check" size={18} />
                  {busy
                    ? "Sending..."
                    : values.instructor?.id
                      ? `Credit to ${personLabel(values.instructor)}${values.subject?.code ? ` · ${values.subject.code}` : ""}`
                      : "Credit"}
                </button>
                {batchLabel && (
                  <button type="button" disabled={!canAct || busy} onClick={toBatch}>{batchLabel}</button>
                )}
                <button type="button" className="danger-outline" disabled={!meta.migration_ready || finished || sending || busy} onClick={() => setDiscarding(true)}>
                  Discard
                </button>
              </>
            )}
            {draft && !finished && (
              <button type="button" className="link-btn" disabled={busy} onClick={() => setDraft(key, null)}>Undo my edits</button>
            )}
            <button type="button" onClick={onClose}>Close</button>
          </div>
        </div>
      </div>

      {overwrite && (
        <OverwriteDialog
          detail={overwrite.detail}
          busy={busy}
          onConfirm={() => send(overwrite.values, true)}
          onCancel={() => setOverwrite(null)}
        />
      )}
      {discarding && (
        <ConfirmDialog
          title="Discard this form?"
          confirmLabel="Discard"
          danger
          busy={busy}
          onConfirm={discard}
          onCancel={() => setDiscarding(false)}
        >
          <p>It will not be counted for any instructor. The scan stays saved.</p>
          <label htmlFor={`note-${row.id}`}>Why? (optional)</label>
          <input id={`note-${row.id}`} value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} placeholder="For example: not an evaluation form" />
        </ConfirmDialog>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ import error editor
function ImportEditor({ row, batch, draft, setDraft, meta, onRowUpdate, onClose, onRefresh, showToast }) {
  const key = `ie:${row.id}`;
  const defaults = useMemo(() => importDefaults(row), [row]);
  const values = draft || defaults;
  const baseVersion = draft ? draft.base_version : row.review_version;
  const finished = row.status !== "pending";
  const sending = row.review_state === "submitting";
  const changedElsewhere = !!draft && Number(row.review_version) > Number(draft.base_version);
  const canAct = meta.migration_ready && meta.import_correction_ready && !finished && !sending;
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  const [overwrite, setOverwrite] = useState(null);
  const [discarding, setDiscarding] = useState(false);
  const [findInstructor, setFindInstructor] = useState(false);
  const [findSubject, setFindSubject] = useState(false);
  const label = row.student_id ? `Form of student ${row.student_id}` : "This form";

  const edit = (patch) => setDraft(key, { ...values, ...patch, base_version: baseVersion });

  async function send(confirmOverwrite = false) {
    if (!values.instructor?.id || !values.subject?.id) {
      setProblem(PICK_FIRST_TEXT);
      return;
    }
    setBusy(true);
    setProblem("");
    try {
      const res = await api.resolveImportError(row.id, sendBody(values, baseVersion, confirmOverwrite));
      onRowUpdate(res.row);
      setDraft(key, null);
      setOverwrite(null);
      if (res.outcome === "submitted") showToast(`${label}: sent. Waiting for marking to confirm it...`, "info");
      else showToast(`${label}: credited to ${personLabel(values.instructor)}.`);
      onClose();
    } catch (err) {
      if (err?.detail?.row) onRowUpdate(err.detail.row);
      if (err?.code === "would_overwrite") {
        setOverwrite(err.detail);
      } else if (err?.code === "correction_failed") {
        setDraft(key, { ...values, base_version: err.detail?.row?.review_version ?? baseVersion });
        setOverwrite(null);
        setProblem(err.message);
      } else {
        setOverwrite(null);
        if (err?.code === "stale_row") setDraft(key, { ...values, base_version: baseVersion });
        setProblem(reviewErrorText(err));
        if (CONFLICT_CODES.has(err?.code)) onRefresh();
      }
    } finally {
      setBusy(false);
    }
  }

  async function discard() {
    setBusy(true);
    setProblem("");
    try {
      const res = await api.discardImportError(row.id, { expected_version: baseVersion });
      onRowUpdate(res.row);
      setDraft(key, null);
      setDiscarding(false);
      showToast(`${label}: discarded.`);
      onClose();
    } catch (err) {
      setDiscarding(false);
      setProblem(reviewErrorText(err));
      if (CONFLICT_CODES.has(err?.code)) onRefresh();
    } finally {
      setBusy(false);
    }
  }

  const bi = batch?.instructor || {};
  const bs = batch?.subject || {};
  const instructorOptions = [];
  if (bi.id) instructorOptions.push({ key: bi.id, value: { id: bi.id, name: bi.name }, label: personLabel(bi), hint: "this batch's instructor" });
  if (values.instructor?.id && values.instructor.id !== bi.id) {
    instructorOptions.push({ key: values.instructor.id, value: values.instructor, label: personLabel(values.instructor), hint: "picked by you" });
  }
  const subjectOptions = [];
  if (bs.id) subjectOptions.push({ key: bs.id, value: { id: bs.id, code: bs.code, name: bs.name }, label: subjectLabel(bs), hint: "this batch's subject" });
  if (values.subject?.id && values.subject.id !== bs.id) {
    subjectOptions.push({ key: values.subject.id, value: values.subject, label: subjectLabel(values.subject), hint: "picked by you" });
  }

  return (
    <div className="review-editor" id={`editor-ie-${row.id}`}>
      {changedElsewhere && (
        <ChangedBanner
          finished={finished}
          onUseNew={() => setDraft(key, null)}
          onKeep={() => setDraft(key, { ...draft, base_version: row.review_version })}
        />
      )}
      {sending && <div className="info-box">Sent to marking. Waiting for it to confirm... This updates by itself.</div>}
      {!finished && !sending && row.review_state === "submit_failed" && (
        <div className="error">Not sent: {row.review_error || "the last try failed."}</div>
      )}
      {finished && (
        <div className="success-box small">
          {row.status === "discarded" ? "Discarded." : `Fixed${row.resolved_by_name ? ` by ${row.resolved_by_name}` : ""}.`}
        </div>
      )}

      <div className="editor-grid">
        <div className="editor-image">
          <ScanImage kind="import" id={row.id} />
        </div>
        <div className="editor-fields">
          <p className="import-why">
            <strong>{importErrorText(row.error_type)}.</strong> The form was scored, but it couldn't be saved because the{" "}
            {String(row.error_type || "").includes("subject") && String(row.error_type || "").includes("instructor")
              ? "instructor and subject"
              : String(row.error_type || "").includes("subject") ? "subject" : "instructor"}{" "}
            didn't match anyone in the system. Pick the right ones to save it.
          </p>
          <dl className="import-facts">
            <div><dt>Instructor on the form</dt><dd>{row.raw_instructor_name || "-"}</dd></div>
            <div><dt>Subject on the form</dt><dd>{row.raw_subject_name || "-"}</dd></div>
          </dl>
          <ReasonList reasons={row.review_reasons} />
          {row.review_note && (
            <details className="review-details">
              <summary>Details from marking</summary>
              <p>{row.review_note}</p>
            </details>
          )}

          <fieldset className="editor-form" disabled={!canAct || busy}>
            {findInstructor ? (
              <InstructorSearch
                id={`find-ie-i-${row.id}`}
                initialQuery={row.raw_instructor_name || ""}
                onPick={(p) => { edit({ instructor: p }); setFindInstructor(false); }}
                onCancel={() => setFindInstructor(false)}
              />
            ) : (
              <ChoiceList
                legend="Credit this form to"
                name={`ie-inst-${row.id}`}
                options={instructorOptions}
                value={values.instructor}
                onChange={(p) => edit({ instructor: p })}
                onSomeoneElse={() => setFindInstructor(true)}
                someoneElseLabel={instructorOptions.length ? "Someone else..." : "Find the instructor..."}
              />
            )}
            {findSubject ? (
              <SubjectSearch
                id={`find-ie-s-${row.id}`}
                initialQuery={row.raw_subject_name || ""}
                onPick={(s) => { edit({ subject: s }); setFindSubject(false); }}
                onCancel={() => setFindSubject(false)}
              />
            ) : (
              <ChoiceList
                legend="Subject"
                name={`ie-subj-${row.id}`}
                options={subjectOptions}
                value={values.subject}
                onChange={(s) => edit({ subject: s })}
                onSomeoneElse={() => setFindSubject(true)}
                someoneElseLabel={subjectOptions.length ? "Another subject..." : "Find the subject..."}
              />
            )}
            <div className="grid-2">
              <div>
                <label htmlFor={`ie-sid-${row.id}`}>Student ID</label>
                <input id={`ie-sid-${row.id}`} value={values.student_id} maxLength={50} autoComplete="off"
                  onChange={(e) => edit({ student_id: e.target.value })} />
              </div>
            </div>
            <label htmlFor={`ie-rem-${row.id}`}>Remarks</label>
            <textarea id={`ie-rem-${row.id}`} rows={3} maxLength={4000} value={values.remarks}
              onChange={(e) => edit({ remarks: e.target.value })} />
            <RatingsEditor
              idPrefix={`ie-r-${row.id}`}
              ratings={values.ratings}
              comparisons={row.comparisons}
              onChange={(k, v) => edit({ ratings: { ...values.ratings, [k]: v } })}
            />
          </fieldset>

          {problem && <div className="error" role="alert">{problem}</div>}
          {!meta.migration_ready && <p className="muted small">Read-only until the database update (migration 005) is applied.</p>}
          {!finished && (!values.instructor?.id || !values.subject?.id) && problem !== PICK_FIRST_TEXT && (
            <p className="small">{PICK_FIRST_TEXT}</p>
          )}

          <div className="editor-actions">
            {!finished && (
              <>
                <button type="button" className="primary" disabled={!canAct || busy || !values.instructor?.id || !values.subject?.id} onClick={() => send(false)}>
                  <Icon name="check" size={18} />
                  {busy ? "Sending..." : values.instructor?.id ? `Save and credit to ${personLabel(values.instructor)}` : "Save and credit"}
                </button>
                <button type="button" className="danger-outline" disabled={!meta.migration_ready || sending || busy} onClick={() => setDiscarding(true)}>
                  Discard
                </button>
              </>
            )}
            {draft && !finished && (
              <button type="button" className="link-btn" disabled={busy} onClick={() => setDraft(key, null)}>Undo my edits</button>
            )}
            <button type="button" onClick={onClose}>Close</button>
          </div>
        </div>
      </div>

      {overwrite && (
        <OverwriteDialog detail={overwrite} busy={busy} onConfirm={() => send(true)} onCancel={() => setOverwrite(null)} />
      )}
      {discarding && (
        <ConfirmDialog title="Discard this import error?" confirmLabel="Discard" danger busy={busy} onConfirm={discard} onCancel={() => setDiscarding(false)}>
          <p>The form will not be counted for any instructor. The scan stays saved.</p>
        </ConfirmDialog>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ rows
// "View page" + score on a review row (the picture opens in the page viewer).
function RowViewer({ label, kind, id, summary, reasons, message }) {
  const [viewing, setViewing] = useState(false);
  return (
    <>
      <ScoreChip summary={summary} />
      <button type="button" className="small-btn" onClick={() => setViewing(true)}>View page</button>
      {viewing && (
        <PageViewer title={label} kind={kind} id={id} summary={summary} reasons={reasons} message={message} onClose={() => setViewing(false)} />
      )}
    </>
  );
}

function queueSummary(row) {
  const c = row.status === "validated" && row.correction?.ratings ? row.correction : null;
  const s = scoreFromRatings(c ? c.ratings : row.ratings);
  return {
    ...s,
    credited: !!c,
    instructor: c ? c.instructor?.name : row.suggested_instructor?.name,
    subject_code: c ? c.subject?.code : row.suggested_subject?.code,
    student_id: (c ? c.student_id : row.student_id) || null,
    agreement_percent: c ? null : row.agreement_percent,
  };
}

function QueueRow({ row, index, importRow, pendingTasks, open, isNew, hasDraft, me, now, onToggle }) {
  const st = queueState(row, importRow, pendingTasks);
  const finished = row.status !== "pending";
  const problems = [...new Set((row.review_reasons || []).map(friendlyReason).filter(Boolean))];
  const mismatch = hasInstructorMismatch(row) || hasSubjectMismatch(row);
  const viewer = row.claimed_by && row.claimed_by.id !== me ? row.claimed_by : null;
  let credit;
  if (finished && row.correction?.instructor?.name && row.review_action && row.review_action !== "rejected") {
    credit = <span>Credited to <strong>{row.correction.instructor.name}</strong>{row.correction.subject?.code ? ` · ${row.correction.subject.code}` : ""}</span>;
  } else if (mismatch && !finished) {
    credit = (
      <span>
        {row.ocr_instructor && <span className="muted">Sheet: "{row.ocr_instructor}"{row.ocr_subject ? ` · "${row.ocr_subject}"` : ""} </span>}
        <span className="rr-suggest">
          Suggested: <strong>{personLabel(row.suggested_instructor)}</strong>
          {row.suggested_subject?.code ? ` · ${row.suggested_subject.code}` : ""}
          {row.suggested_instructor?.confidence != null && ` (${pct(row.suggested_instructor.confidence)})`}
        </span>
      </span>
    );
  } else {
    credit = <span className="muted">{personLabel(row.batch_instructor)} (this batch)</span>;
  }
  return (
    <div className={`review-row ${finished ? "finished" : ""} ${open ? "open" : ""} ${isNew ? "is-new" : ""}`} id={`review-q-${row.id}`}>
      <span className="rr-num" aria-hidden="true">{index}</span>
      <span className="rr-form">
        <span className="item-file">{row.filename || "Scanned page"}</span>
        <span className="muted small">
          {timeAgo(row.created_at, now)}
          {row.student_id ? ` · Student ${row.student_id}` : ""}
        </span>
      </span>
      <span className="rr-problem small">
        <span className="rr-label">What to check: </span>
        {problems.length ? problems.slice(0, 2).join(" ") : "Check this form."}
        {problems.length > 2 && <span className="muted"> (+{problems.length - 2} more)</span>}
      </span>
      <span className="rr-credit small"><span className="rr-label">Credit to: </span>{credit}</span>
      <span className="rr-status">
        <span className={`pill ${st.tone}`}>{st.label}</span>
        {isNew && <span className="pill new">New</span>}
        {hasDraft && !finished && <span className="badge">your edits</span>}
        {viewer && !finished && <span className="muted small">{viewer.name || "Someone"} is looking</span>}
      </span>
      <span className="rr-action">
        <RowViewer label={row.filename || "Scanned page"} kind="queue" id={row.id} summary={queueSummary(row)} reasons={row.review_reasons} />
        <button type="button" className="small-btn" aria-expanded={open} aria-controls={`editor-q-${row.id}`} onClick={onToggle}>
          {open ? "Close" : finished ? "View" : "Open"}
        </button>
      </span>
    </div>
  );
}

function ImportRow({ row, index, open, isNew, hasDraft, now, onToggle }) {
  const st = importState(row);
  const finished = row.status !== "pending";
  return (
    <div className={`review-row ${finished ? "finished" : ""} ${open ? "open" : ""} ${isNew ? "is-new" : ""}`} id={`review-ie-${row.id}`}>
      <span className="rr-num" aria-hidden="true">{index}</span>
      <span className="rr-form">
        <span>{row.student_id ? `Student ${row.student_id}` : "Scanned form"}</span>
        <span className="muted small">{timeAgo(row.created_at, now)}</span>
      </span>
      <span className="rr-problem small">
        <span className="rr-label">Problem: </span>
        {importErrorText(row.error_type)}
      </span>
      <span className="rr-credit small">
        <span className="rr-label">On the form: </span>
        {row.raw_instructor_name || "-"}{row.raw_subject_name ? ` · ${row.raw_subject_name}` : ""}
      </span>
      <span className="rr-status">
        <span className={`pill ${st.tone}`}>{st.label}</span>
        {isNew && <span className="pill new">New</span>}
        {hasDraft && !finished && <span className="badge">your edits</span>}
      </span>
      <span className="rr-action">
        <RowViewer
          label={row.student_id ? `Student ${row.student_id}` : "Scanned form"}
          kind="import"
          id={row.id}
          summary={{ ...scoreFromRatings(row.ratings), credited: row.status === "resolved", instructor: row.raw_instructor_name, subject_code: row.raw_subject_name, student_id: row.student_id || null }}
          reasons={row.review_reasons}
        />
        <button type="button" className="small-btn" aria-expanded={open} aria-controls={`editor-ie-${row.id}`} onClick={onToggle}>
          {open ? "Close" : finished ? "View" : "Open"}
        </button>
      </span>
    </div>
  );
}

// ------------------------------------------------------------------ couldn't be saved
const UNSAVED_CONFLICTS = new Set(["stale_row", "already_resolved", "in_progress", "item_changed"]);
const unsavedErrorText = (err) => (err?.code === "migration_required" ? err.message : reviewErrorText(err));

function unsavedTone(row) {
  if (row.status === "sending" || row.status === "retrying") return "proc";
  if (row.status === "saved" || row.status === "manual") return "success";
  return row.last_error ? "error" : "flagged";
}

function triesText(row) {
  const auto = Number(row.watcher_failures) || 0;
  const here = Number(row.website_attempts) || 0;
  const parts = [];
  if (auto) parts.push(`${auto} ${auto === 1 ? "try" : "tries"} by the laptop`);
  if (here) parts.push(`${here} from this page`);
  return parts.join(", ") || "-";
}

function UnsavedRow({ row, index, open, now, onToggle }) {
  const finished = row.status === "saved" || row.status === "manual";
  const notes = [...new Set((row.review_reasons || []).map(friendlyReason).filter(Boolean))];
  return (
    <div className={`review-row ${finished ? "finished" : ""} ${open ? "open" : ""}`} id={`review-u-${row.id}`}>
      <span className="rr-num" aria-hidden="true">{index}</span>
      <span className="rr-form">
        <span className="item-file">{row.filename || "Scanned page"}</span>
        <span className="muted small">{timeAgo(row.created_at, now)}</span>
      </span>
      <span className="rr-problem small">
        <span className="rr-label">Problem: </span>
        Marking couldn't save this page.
        {notes.length > 0 && <span className="muted"> {notes[0]}</span>}
      </span>
      <span className="rr-credit small"><span className="rr-label">Tries: </span>{triesText(row)}</span>
      <span className="rr-status">
        <span className={`pill ${unsavedTone(row)}`}>{row.last_error && row.status === "needs_review" ? "Last try failed" : unsavedStateText(row)}</span>
      </span>
      <span className="rr-action">
        {row.has_image !== false && (
          <RowViewer label={row.filename || "Scanned page"} kind="unsaved" id={row.id} summary={null} reasons={row.review_reasons} message={row.message} />
        )}
        <button type="button" className="small-btn" aria-expanded={open} aria-controls={`editor-u-${row.id}`} onClick={onToggle}>
          {open ? "Close" : finished ? "View" : "Open"}
        </button>
      </span>
    </div>
  );
}

function UnsavedEditor({ row, meta, onRowUpdate, onQueueRow, onOpenQueue, onClose, onRefresh, showToast }) {
  const [busy, setBusy] = useState(null); // "retry" | "manual"
  const [problem, setProblem] = useState("");
  const [askManual, setAskManual] = useState(false);
  const label = row.filename || "This page";
  const waiting = row.status === "needs_review";
  const max = Number(row.max_website_attempts) || 5;
  const usedUp = (Number(row.website_attempts) || 0) >= max;

  async function run(kind) {
    setBusy(kind);
    setProblem("");
    try {
      if (kind === "retry") {
        const res = await api.retryUnsaved(row.id, { expected_version: row.version });
        onRowUpdate(res.row);
        showToast(`${label}: sent to marking again. This list updates by itself when it answers.`, "info");
      } else {
        const res = await api.unsavedToReview(row.id, { expected_version: row.version });
        onRowUpdate(res.row);
        setAskManual(false);
        if (res.outcome === "already_saved") {
          showToast(`${label}: marking had already saved and scored this page. Nothing to type in.`);
        } else {
          showToast(`${label}: moved to Needs review. Type in the answers there.`);
          onQueueRow(res.queue_row);
        }
        onClose();
      }
    } catch (err) {
      setAskManual(false);
      setProblem(unsavedErrorText(err));
      if (UNSAVED_CONFLICTS.has(err?.code)) onRefresh();
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="review-editor" id={`editor-u-${row.id}`}>
      {row.status === "sending" && (
        <div className="info-box">Sent to marking again. Waiting for its answer... This updates by itself and can take a few minutes.</div>
      )}
      {row.status === "retrying" && (
        <div className="info-box">The scanner laptop is still sending this page by itself. You can act on it here once it gives up.</div>
      )}
      {row.status === "saved" && <div className="success-box small">{unsavedStateText(row)}. Nothing else to do here.</div>}
      {row.status === "manual" && (
        <div className="success-box small">
          Moved to Needs review{row.acted_by_name ? ` by ${row.acted_by_name}` : ""}. Type in the answers there.{" "}
          {row.fsq_id && <button type="button" className="link-btn" onClick={() => onOpenQueue(row.fsq_id)}>Open it</button>}
        </div>
      )}
      {waiting && row.last_error && <div className="error">Last try: {row.last_error}</div>}

      <div className="editor-grid">
        <div className="editor-image">
          {row.has_image !== false ? (
            <ScanImage kind="unsaved" id={row.id} />
          ) : (
            <div className="scan-image placeholder muted">
              The scan is no longer kept here: it is with the {row.status === "manual" ? "Needs review form" : "saved form"}.
            </div>
          )}
        </div>
        <div className="editor-fields">
          <p className="import-why">
            <strong>Marking read this page but couldn't save it.</strong> Nothing from it is stored yet, so it isn't counted
            for anyone. Send it to marking again, or type in the answers yourself. There is no need to scan the sheet again.
          </p>
          {(row.review_reasons || []).length > 0 && (
            <>
              <p className="small muted">Marking also noted:</p>
              <ReasonList reasons={row.review_reasons} />
            </>
          )}
          <p className="small muted">Tries so far: {triesText(row)}.</p>
          {row.message && (
            <details className="review-details">
              <summary>Details from marking (for IT)</summary>
              <p className="small">{row.message}</p>
            </details>
          )}

          {problem && <div className="error" role="alert">{problem}</div>}
          {waiting && !meta.retry_ready && (
            <p className="muted small">Sending again isn't set up on this server. You can still type in the answers.</p>
          )}
          {waiting && meta.retry_ready && usedUp && (
            <p className="muted small">It was sent again {max} times from here. Type in the answers instead.</p>
          )}

          <div className="editor-actions">
            {waiting && (
              <>
                <button type="button" className="primary" disabled={!row.can_retry || !!busy} onClick={() => run("retry")}>
                  <Icon name="scan" size={18} />
                  {busy === "retry" ? "Sending..." : "Try again"}
                </button>
                <button type="button" disabled={!row.can_enter_by_hand || !!busy} onClick={() => setAskManual(true)}>
                  Type in the answers
                </button>
              </>
            )}
            <button type="button" onClick={onClose}>Close</button>
          </div>
        </div>
      </div>

      {askManual && (
        <ConfirmDialog
          title="Type in the answers yourself?"
          confirmLabel="Yes, move it to Needs review"
          cancelLabel="Cancel"
          busy={busy === "manual"}
          onConfirm={() => run("manual")}
          onCancel={() => setAskManual(false)}
        >
          <p>
            The page moves to <strong>Needs review</strong> with the scan next to an empty form. Type the student ID,
            remarks and every rating from the scan, then credit it as usual.
          </p>
          <p className="small">It can't be sent to marking again after that.</p>
        </ConfirmDialog>
      )}
    </div>
  );
}

function NewBadge({ count, onShow, what }) {
  if (!count) return null;
  return (
    <button type="button" className="new-badge" onClick={onShow}>
      {count} new {count === 1 ? what : `${what}s`} - show
    </button>
  );
}

function BulkBox({ groups, meta, onRun, bulk }) {
  if (!meta.migration_ready || !meta.correction_ready) return null;
  if (!groups.length && !bulk) return null;
  return (
    <div className="bulk-box">
      {groups.map((g) => (
        <div key={g.key} className="bulk-group">
          <span>
            <strong>{plural(g.rows.length, "form looks", "forms look")}</strong> like {personLabel(g.instructor)}
            {g.subject?.code ? ` · ${g.subject.code}` : ""}. Nothing else needs checking on them.
          </span>
          <button type="button" className="small-btn" disabled={!!bulk?.running} onClick={() => onRun(g)}>
            Credit all {g.rows.length} to {personLabel(g.instructor)}
          </button>
        </div>
      ))}
      {bulk && (
        <div className="bulk-progress" role="status">
          {bulk.running
            ? `Crediting ${Math.min(bulk.done + 1, bulk.total)} of ${bulk.total}...`
            : `${plural(bulk.ok, "form", "forms")} credited${bulk.skipped.length ? `, ${bulk.skipped.length} skipped` : ""}.`}
          {!bulk.running && bulk.skipped.length > 0 && (
            <ul className="small">
              {bulk.skipped.map((s) => <li key={s.id}>{s.name}: {s.reason}</li>)}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ page
function ReviewPage({ batchId, profile }) {
  const data = useReviewData(batchId);
  const [drafts, setDraft] = useDrafts(batchId);
  const [openQ, setOpenQ] = useState(null);
  const [openIe, setOpenIe] = useState(null);
  const [openU, setOpenU] = useState(null);
  const [qFilter, setQFilter] = useState("all");
  const [ieFilter, setIeFilter] = useState("all");
  const [showFinished, setShowFinished] = useState(true);
  const [flash, setFlash] = useState(() => new Set()); // just-revealed rows, highlighted for a moment
  const [toast, setToast] = useState(null);
  const [bulk, setBulk] = useState(null);
  const [bulkAsk, setBulkAsk] = useState(null);
  const now = useNow(5000);
  const me = profile?.id;
  const queueRef = useRef(data.queue);
  queueRef.current = data.queue;

  const showToast = useCallback((text, tone = "success") => setToast({ text, tone, at: Date.now() }), []);
  useEffect(() => {
    if (!toast) return undefined;
    const t = setTimeout(() => setToast(null), TOAST_MS);
    return () => clearTimeout(t);
  }, [toast]);
  useEffect(() => {
    if (!flash.size) return undefined;
    const t = setTimeout(() => setFlash(new Set()), 8000);
    return () => clearTimeout(t);
  }, [flash]);

  const queueRows = useMemo(() => [...data.queue.values()].sort(byCreated), [data.queue]);
  const importRows = useMemo(() => [...data.imports.values()].sort(byCreated), [data.imports]);
  const unsavedRows = useMemo(() => [...data.unsaved.values()].sort(byCreated), [data.unsaved]);
  const unsavedIndex = useMemo(() => new Map(unsavedRows.map((r, i) => [r.id, i + 1])), [unsavedRows]);
  const queueIndex = useMemo(() => new Map(queueRows.map((r, i) => [r.id, i + 1])), [queueRows]);
  const importIndex = useMemo(() => new Map(importRows.map((r, i) => [r.id, i + 1])), [importRows]);
  const importByTask = useMemo(() => {
    const m = new Map();
    for (const r of importRows) if (r.task_id) m.set(r.task_id, r);
    return m;
  }, [importRows]);

  const shownQueue = queueRows.filter((r) => !data.newQueue.has(r.id));
  const shownImports = importRows.filter((r) => !data.newImports.has(r.id));
  const qTodo = (key) => shownQueue.filter((r) => r.status === "pending" && QUEUE_CATEGORY[key](r)).length;
  const ieTodo = (key) => shownImports.filter((r) => r.status === "pending" && IMPORT_CATEGORY[key](r)).length;
  const visibleQueue = shownQueue.filter((r) => QUEUE_CATEGORY[qFilter](r) && (showFinished || r.status === "pending" || r.id === openQ));
  const visibleImports = shownImports.filter((r) => IMPORT_CATEGORY[ieFilter](r) && (showFinished || r.status === "pending" || r.id === openIe));
  const unsavedOpen = (r) => r.status !== "saved" && r.status !== "manual";
  const visibleUnsaved = unsavedRows.filter((r) => showFinished || unsavedOpen(r) || r.id === openU);
  const groups = bulkGroups(shownQueue, drafts);

  // Coming from the batch page's "Try again or type in the answers" link.
  const jumped = useRef(false);
  useEffect(() => {
    if (jumped.current || !data.loaded || window.location.hash !== "#couldnt-save") return;
    jumped.current = true;
    requestAnimationFrame(() => document.getElementById("couldnt-save")?.scrollIntoView?.({ block: "start" }));
  }, [data.loaded]);

  // "Type in the answers": the new Needs review form opens right away.
  function openQueueRow(id) {
    setOpenU(null);
    setOpenQ(id);
    requestAnimationFrame(() => document.getElementById(`review-q-${id}`)?.scrollIntoView?.({ block: "center", behavior: "smooth" }));
  }
  function toggleU(id) {
    setOpenU((cur) => (cur === id ? null : id));
    requestAnimationFrame(() => document.getElementById(`review-u-${id}`)?.scrollIntoView?.({ block: "nearest" }));
  }

  function reveal(kind) {
    const pending = kind === "import" ? data.newImports : data.newQueue;
    const rows = kind === "import" ? importRows : queueRows;
    const first = rows.find((r) => pending.has(r.id));
    setFlash(new Set(pending));
    data.reveal(kind);
    if (first) {
      requestAnimationFrame(() => {
        document.getElementById(`${kind === "import" ? "review-ie" : "review-q"}-${first.id}`)?.scrollIntoView?.({ block: "center", behavior: "smooth" });
      });
    }
  }

  function toggleQ(id) {
    setOpenQ((cur) => (cur === id ? null : id));
    requestAnimationFrame(() => document.getElementById(`review-q-${id}`)?.scrollIntoView?.({ block: "nearest" }));
  }
  function toggleIe(id) {
    setOpenIe((cur) => (cur === id ? null : id));
    requestAnimationFrame(() => document.getElementById(`review-ie-${id}`)?.scrollIntoView?.({ block: "nearest" }));
  }

  // Bulk: one approve call per form, one after the other; each is checked
  // again on the server (version, overwrite, instructor) before it is sent.
  async function runBulk(group) {
    setBulkAsk(null);
    const progress = { key: group.key, total: group.rows.length, done: 0, ok: 0, skipped: [], running: true };
    setBulk({ ...progress });
    for (const r of group.rows) {
      const cur = queueRef.current.get(r.id) || r;
      const name = cur.filename || `Form ${cur.student_id || ""}`.trim();
      if (cur.status !== "pending" || cur.review_state === "submitting") {
        progress.skipped.push({ id: cur.id, name, reason: "already dealt with" });
      } else {
        try {
          const res = await api.approveReview(cur.id, sendBody(queueDefaults(cur), cur.review_version, false));
          data.mergeRow("queue", res.row);
          progress.ok += 1;
        } catch (err) {
          if (err?.detail?.row) data.mergeRow("queue", err.detail.row);
          const reason =
            err?.code === "would_overwrite"
              ? "another form from this student is already saved - open it to decide"
              : reviewErrorText(err);
          progress.skipped.push({ id: cur.id, name, reason });
        }
      }
      progress.done += 1;
      setBulk({ ...progress });
    }
    progress.running = false;
    setBulk({ ...progress });
    data.refresh();
  }

  if (data.fatal === "not_found") {
    return (
      <div>
        <PageHeader title="Batch not found" subtitle="This batch doesn't exist any more, or the link is wrong." back={{ to: "/review", label: "All batches" }} />
      </div>
    );
  }
  if (data.fatal === "forbidden") {
    return (
      <div>
        <PageHeader
          title="Review forms"
          subtitle="Only the staff member who started this batch, or an SAO admin, can review these forms."
          back={{ to: "/review", label: "All batches" }}
        />
      </div>
    );
  }
  if (!data.loaded) {
    return (
      <div>
        <PageHeader title="Review forms" back={{ to: `/batch/${batchId}`, label: "Scan & check" }} />
        {data.error ? <div className="error" role="alert">{data.error}</div> : <Loading>Loading the forms to review...</Loading>}
      </div>
    );
  }

  const b = data.batch || {};
  const meta = data.meta;
  const counts = data.counts || {};
  const back = b.status === "open" ? { to: `/batch/${batchId}`, label: "Scan & check" } : { to: "/review", label: "All batches" };
  const scrollTo = (id) => document.getElementById(id)?.scrollIntoView?.({ block: "start", behavior: "smooth" });

  return (
    <div className="review-page">
      <PageHeader
        title="Review forms"
        subtitle="Forms from this batch that need a person, and forms that couldn't be saved. The lists update by themselves while pages are still arriving, so you can fix forms now."
        back={back}
        actions={<LiveBadge lastOkAt={data.lastOkAt} error={data.error} now={now} />}
      />

      <div className="batch-meta">
        <div><span className="muted">Instructor</span><strong>{personLabel(b.instructor)}</strong></div>
        <div><span className="muted">Subject</span><span><strong>{b.subject?.code}</strong> {b.subject?.name}</span></div>
        <div><span className="muted">Term</span>{b.term?.label || "-"}</div>
        <div><span className="muted">Laptop</span>{b.station?.label || "-"}</div>
        <div><span className="muted">Batch</span>{b.status === "open" ? "Open - still scanning" : "Closed"}</div>
      </div>

      {data.error && <div className="error" role="alert">Can't refresh right now: {data.error} Trying again...</div>}
      {!meta.migration_ready && (
        <div className="warning-box small">
          <strong>Read-only for now.</strong> Saving from this page needs a database update (migration 005). Ask IT to apply it.
          The lists still update.
        </div>
      )}
      {meta.migration_ready && !meta.correction_ready && (
        <div className="warning-box small">
          Sending corrections isn't set up on this server yet, so forms can't be credited from here. Ask IT to set it up.
        </div>
      )}
      {meta.access === "own" && <div className="info-box">You can see only the forms you scanned in this batch.</div>}

      <nav className="review-jump" aria-label="Jump to a list">
        <button type="button" className="chip" onClick={() => scrollTo("needs-review")}>
          Needs review <span className="chip-count">{counts.queue_pending ?? qTodo("all")}</span>
          {data.newQueue.size > 0 && <span className="pill new">+{data.newQueue.size} new</span>}
        </button>
        {meta.access !== "own" && meta.unsaved_ready && unsavedRows.length > 0 && (
          <button type="button" className="chip" onClick={() => scrollTo("couldnt-save")}>
            Couldn't be saved <span className="chip-count">{counts.unsaved_pending || 0}</span>
          </button>
        )}
        <button type="button" className="chip" onClick={() => scrollTo("import-errors")}>
          Import errors <span className="chip-count">{counts.import_pending ?? ieTodo("all")}</span>
          {data.newImports.size > 0 && <span className="pill new">+{data.newImports.size} new</span>}
        </button>
        <label className="check-inline">
          <input type="checkbox" checked={showFinished} onChange={(e) => setShowFinished(e.target.checked)} />
          Show finished forms
        </label>
      </nav>

      {/* ---------------- Needs review ---------------- */}
      <section className="section review-panel" id="needs-review" aria-labelledby="needs-review-title">
        <div className="panel-head">
          <h2 id="needs-review-title">Needs review</h2>
          <NewBadge count={data.newQueue.size} what="form" onShow={() => reveal("queue")} />
        </div>
        <div className="filter-chips" role="group" aria-label="Show forms with">
          {QUEUE_FILTERS.map((f) => (
            <button type="button" key={f.key} className={`chip ${qFilter === f.key ? "selected" : ""}`} aria-pressed={qFilter === f.key} onClick={() => setQFilter(f.key)}>
              {f.label} <span className="chip-count">{qTodo(f.key)}</span>
            </button>
          ))}
        </div>

        <BulkBox groups={groups} meta={meta} bulk={bulk} onRun={(g) => setBulkAsk(g)} />

        {visibleQueue.length === 0 ? (
          <EmptyState icon="check" title={shownQueue.length ? "Nothing here" : "Nothing needs review yet"}>
            {shownQueue.length
              ? "No forms match this filter."
              : "Forms that need a person show up here within a few seconds of being scanned."}
            {data.newQueue.size > 0 && (
              <div><button type="button" className="link-btn" onClick={() => reveal("queue")}>Show the {plural(data.newQueue.size, "new form", "new forms")}</button></div>
            )}
          </EmptyState>
        ) : (
          <div className="review-table" role="list" aria-label="Forms that need review">
            <div className="review-head" aria-hidden="true">
              <span>#</span><span>Form</span><span>What to check</span><span>Credit to</span><span>Status</span><span />
            </div>
            {visibleQueue.map((r) => (
              <div role="listitem" className={`review-item ${openQ === r.id ? "open" : ""}`} key={r.id}>
                <QueueRow
                  row={r}
                  index={queueIndex.get(r.id)}
                  importRow={importByTask.get(r.task_id)}
                  pendingTasks={data.pendingTasks}
                  open={openQ === r.id}
                  isNew={flash.has(r.id)}
                  hasDraft={!!drafts[`q:${r.id}`]}
                  me={me}
                  now={now}
                  onToggle={() => toggleQ(r.id)}
                />
                {openQ === r.id && (
                  <QueueEditor
                    key={`editor-${r.id}`}
                    row={r}
                    importRow={importByTask.get(r.task_id)}
                    pendingTasks={data.pendingTasks}
                    draft={drafts[`q:${r.id}`]}
                    setDraft={setDraft}
                    meta={meta}
                    me={me}
                    onRowUpdate={(row) => data.mergeRow("queue", row)}
                    onClose={() => setOpenQ(null)}
                    onRefresh={data.refresh}
                    showToast={showToast}
                  />
                )}
              </div>
            ))}
          </div>
        )}
      </section>

      {/* ---------------- Couldn't be saved ---------------- */}
      {meta.access !== "own" && meta.unsaved_ready && unsavedRows.length > 0 && (
        <section className="section review-panel" id="couldnt-save" aria-labelledby="couldnt-save-title">
          <div className="panel-head">
            <h2 id="couldnt-save-title">Couldn't be saved</h2>
          </div>
          <p className="small panel-help">
            Pages that marking read but couldn't save, even after the laptop tried several times. A copy of each scan is
            kept here: send it to marking again, or type in the answers yourself. No need to scan the sheets again.
          </p>
          {visibleUnsaved.length === 0 ? (
            <EmptyState icon="check" title={unsavedRows.length ? "Nothing left here" : "Nothing here"}>
              {unsavedRows.length
                ? "Every page that couldn't be saved has been dealt with."
                : "Good - marking saved every page so far."}
            </EmptyState>
          ) : (
            <div className="review-table" role="list" aria-label="Pages that couldn't be saved">
              <div className="review-head" aria-hidden="true">
                <span>#</span><span>Page</span><span>Problem</span><span>Tries</span><span>Status</span><span />
              </div>
              {visibleUnsaved.map((r) => (
                <div role="listitem" className={`review-item ${openU === r.id ? "open" : ""}`} key={r.id}>
                  <UnsavedRow row={r} index={unsavedIndex.get(r.id)} open={openU === r.id} now={now} onToggle={() => toggleU(r.id)} />
                  {openU === r.id && (
                    <UnsavedEditor
                      key={`u-editor-${r.id}`}
                      row={r}
                      meta={meta}
                      onRowUpdate={(row) => data.mergeRow("unsaved", row)}
                      onQueueRow={(row) => {
                        data.mergeRow("queue", row);
                        if (row?.id) openQueueRow(row.id);
                      }}
                      onOpenQueue={openQueueRow}
                      onClose={() => setOpenU(null)}
                      onRefresh={data.refresh}
                      showToast={showToast}
                    />
                  )}
                </div>
              ))}
            </div>
          )}
        </section>
      )}

      {/* ---------------- Import errors ---------------- */}
      <section className="section review-panel" id="import-errors" aria-labelledby="import-errors-title">
        <div className="panel-head">
          <h2 id="import-errors-title">Import errors</h2>
          <NewBadge count={data.newImports.size} what="error" onShow={() => reveal("import")} />
        </div>
        <p className="small panel-help">
          Forms that were scored but couldn't be saved because the instructor or subject wasn't found. Only this batch's
          website scans are listed here; Google Sheet errors stay in the iEvaluate app.
        </p>
        {meta.access === "own" ? (
          <p className="muted small">Import errors are shown to the person who started the batch and to SAO admins.</p>
        ) : (
          <>
            <div className="filter-chips" role="group" aria-label="Show import errors about">
              {IMPORT_FILTERS.map((f) => (
                <button type="button" key={f.key} className={`chip ${ieFilter === f.key ? "selected" : ""}`} aria-pressed={ieFilter === f.key} onClick={() => setIeFilter(f.key)}>
                  {f.label} <span className="chip-count">{ieTodo(f.key)}</span>
                </button>
              ))}
            </div>
            {visibleImports.length === 0 ? (
              <EmptyState icon="check" title={shownImports.length ? "Nothing here" : "No import errors"}>
                {shownImports.length ? "No import errors match this filter." : "Good - every corrected form could be saved so far."}
                {data.newImports.size > 0 && (
                  <div><button type="button" className="link-btn" onClick={() => reveal("import")}>Show the {plural(data.newImports.size, "new error", "new errors")}</button></div>
                )}
              </EmptyState>
            ) : (
              <div className="review-table" role="list" aria-label="Import errors">
                <div className="review-head" aria-hidden="true">
                  <span>#</span><span>Form</span><span>Problem</span><span>On the form</span><span>Status</span><span />
                </div>
                {visibleImports.map((r) => (
                  <div role="listitem" className={`review-item ${openIe === r.id ? "open" : ""}`} key={r.id}>
                    <ImportRow
                      row={r}
                      index={importIndex.get(r.id)}
                      open={openIe === r.id}
                      isNew={flash.has(r.id)}
                      hasDraft={!!drafts[`ie:${r.id}`]}
                      now={now}
                      onToggle={() => toggleIe(r.id)}
                    />
                    {openIe === r.id && (
                      <ImportEditor
                        key={`ie-editor-${r.id}`}
                        row={r}
                        batch={b}
                        draft={drafts[`ie:${r.id}`]}
                        setDraft={setDraft}
                        meta={meta}
                        onRowUpdate={(row) => data.mergeRow("import", row)}
                        onClose={() => setOpenIe(null)}
                        onRefresh={data.refresh}
                        showToast={showToast}
                      />
                    )}
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </section>

      {bulkAsk && (
        <ConfirmDialog
          title={`Credit ${plural(bulkAsk.rows.length, "form", "forms")} to ${personLabel(bulkAsk.instructor)}?`}
          confirmLabel={`Credit all ${bulkAsk.rows.length}`}
          onConfirm={() => runBulk(bulkAsk)}
          onCancel={() => setBulkAsk(null)}
        >
          <p>
            These sheets name {personLabel(bulkAsk.instructor)}
            {bulkAsk.subject?.code ? ` (${bulkAsk.subject.code})` : ""} instead of this batch's instructor. Their answers
            were read without problems, so they are credited as read.
          </p>
          <ul className="small bulk-list">
            {bulkAsk.rows.slice(0, 10).map((r) => <li key={r.id}>{r.filename || "Scanned page"}{r.student_id ? ` - student ${r.student_id}` : ""}</li>)}
            {bulkAsk.rows.length > 10 && <li>and {bulkAsk.rows.length - 10} more</li>}
          </ul>
          <p className="small">Each form is checked again before it is sent. Forms that changed meanwhile are skipped.</p>
        </ConfirmDialog>
      )}

      <div className="toast-region" role="status" aria-live="polite">
        {toast && <div className={`toast ${toast.tone}`}>{toast.text}</div>}
      </div>
    </div>
  );
}

export default function Review({ profile }) {
  const { batchId } = useParams();
  return <ReviewPage key={batchId} batchId={batchId} profile={profile} />;
}
