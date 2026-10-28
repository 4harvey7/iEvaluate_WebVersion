import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api.js";
import { ConfirmDialog, EmptyState, Loading, PageHeader, timeAgo, useNow } from "../components.jsx";
import { Icon } from "../icons.jsx";
import {
  MARKING_OFF_TEXT,
  friendlyCode,
  friendlyMessage,
  friendlyReason,
  itemCode,
  outcomeNote,
  rotationText,
} from "../messages.js";
import { OldWatcherNote, ScannerStatus, SkippedPagesCard, getScannerState, skippedCount, useStation } from "../scanner.jsx";
import { PageViewer, ScoreChip, ScoreLine } from "../pageViewer.jsx";

const POLL_MS = 3000;

// Counters and filters share these keys (they are the keys of batch.counts),
// so a counter and its filter always mean the same pages.
const COUNTERS = [
  { key: "processed", label: "Scored", cls: "", help: "Read and scored. Nothing to do." },
  { key: "flagged", label: "Flagged", cls: "c-flag", help: "Scored, but a person has to check it. The line under the number shows how many still wait for review." },
  { key: "rejected", label: "Rejected", cls: "c-reject", help: "Couldn't be scored. Check the sheet and scan it again." },
  { key: "duplicates", label: "Already scanned", cls: "c-dup", help: "This exact page was already received, so it was ignored." },
  { key: "errors", label: "Trying again", cls: "c-err", help: "Not marked yet. It is tried again automatically." },
  {
    key: "unsaved",
    label: "Couldn't be saved",
    cls: "c-flag",
    help: "Marking couldn't save these pages. A copy is kept: open the review page to try again or type in the answers. No need to scan them again.",
  },
  { key: "failed", label: "Failed", cls: "c-reject", help: "Gave up after several tries. Scan these sheets again." },
  { key: "processing", label: "Working on it", cls: "c-proc", help: "Received and being scored right now." },
];
const FILTERS = [{ key: "all", label: "All" }, ...COUNTERS];
const LABEL = { ...Object.fromEntries(COUNTERS.map((k) => [k.key, k.label])), flagged: "Needs a look" };

const EMPTY_TEXT = {
  all: ["No pages yet", "Scan the first sheet. It will show up here within a few seconds."],
  processed: ["No pages scored yet", "Scored pages will show up here."],
  flagged: ["Nothing flagged", "Pages a person should check will show up here."],
  rejected: ["No rejected pages", "Good - every page could be read so far."],
  duplicates: ["No repeated pages", "Pages scanned twice will show up here."],
  errors: ["Nothing is being tried again", "Pages that hit a hiccup show up here while they are retried."],
  unsaved: ["Nothing waiting", "Pages marking couldn't save would show up here, ready to try again or type in on the review page."],
  failed: ["Nothing has failed", "Pages that couldn't be marked after several tries would show up here."],
  processing: ["Nothing is being worked on", "New pages show up here for a few seconds while they are scored."],
};

// Same rule as the counters on the server (batches.py): errors that can
// still be retried are "Trying again"; retryable === false is "Failed",
// except pages whose copy waits on the review page ("Couldn't be saved").
function itemBucket(item) {
  if (item?.status === "error" && itemCode(item) === "not_saved_needs_review") return "unsaved";
  switch (item?.status) {
    case "success":
      return "processed";
    case "flagged":
      return "flagged";
    case "rejected":
      return "rejected";
    case "duplicate":
      return "duplicates";
    case "error":
      return item.retryable === false ? "failed" : "errors";
    case "processing":
      return "processing";
    default:
      return "other";
  }
}

const itemTime = (it) => new Date(it.created_at || it.updated_at || 0).getTime() || 0;

// One list of pages, newest first. Uses batch.items when the server sends
// it, otherwise joins the two lists it has always sent.
function allItems(batch) {
  const source = Array.isArray(batch?.items) ? batch.items : [...(batch?.attention_items || []), ...(batch?.recent_items || [])];
  const byId = new Map();
  for (const it of source) {
    if (!it || !it.id) continue;
    const prev = byId.get(it.id);
    if (!prev || new Date(it.updated_at || 0) >= new Date(prev.updated_at || 0)) byId.set(it.id, it);
  }
  return [...byId.values()].sort((a, b) => itemTime(b) - itemTime(a));
}

const REVIEWED_LABEL = { validated: "Reviewed", discarded: "Discarded" };
const isStampDuplicate = (item) => (item.review_reasons || []).some((r) => String(r).trim().toLowerCase() === "duplicate_stamp");

const IDENTITY_WORDS = { match: "matches", mismatch: "doesn't match", unreadable: "can't read it" };

// The instructor/subject written on the sheet is compared with the batch. Only a page
// flagged for a mismatch carries an identity result (the review page shows the
// suggested instructor/subject and lets staff credit it). "unverified" is not shown.
function IdentityCheck({ identity }) {
  if (!identity) return null;
  const cell = (label, verdict, ocr) =>
    IDENTITY_WORDS[verdict] ? (
      <span className={`identity ${verdict}`}>
        {label}: {IDENTITY_WORDS[verdict]}
        {ocr && verdict !== "match" ? ` (sheet says "${ocr}")` : ""}
      </span>
    ) : null;
  const instructor = cell("Instructor", identity.instructor, identity.ocr_instructor);
  const subject = cell("Subject", identity.subject, identity.ocr_subject);
  if (!instructor && !subject) return null;
  return (
    <div className="identity-row">
      {instructor}
      {subject}
    </div>
  );
}

function ItemRow({ item, now, markingOff, reviewTo, canView, onView }) {
  const bucket = itemBucket(item);
  const code = itemCode(item);
  // While marking is off, a page that is "trying again" is simply waiting.
  const waiting = bucket === "errors" && (code === "pipeline_not_configured" || markingOff);
  const unreadable = bucket === "failed" && ["not_saved_permanent", "pipeline_rejected_request", "invalid_request"].includes(code);
  const mismatch = item.identity && (item.identity.instructor === "mismatch" || item.identity.subject === "mismatch");
  // Flagged pages already fixed on the review page or in the app (queue row no longer pending).
  const reviewed = bucket === "flagged" && item.review_status && item.review_status !== "pending";
  // Its stamp number was already saved this term: marking discarded it by itself (counted under Flagged).
  // Taken back on the review page (Override), it is an ordinary flagged form again.
  const alreadyScanned = bucket === "flagged" && isStampDuplicate(item) && !["pending", "validated"].includes(item.review_status);
  const label = waiting
    ? "Waiting for marking"
    : unreadable
      ? "Failed – couldn't be read"
      : alreadyScanned
        ? "Already scanned"
        : reviewed
          ? REVIEWED_LABEL[item.review_status] || "Reviewed"
          : LABEL[bucket] || "Received";
  const rotation = rotationText(item.orientation_applied);

  const lines = [];
  if (waiting) lines.push(MARKING_OFF_TEXT);
  else if (bucket === "duplicates") lines.push(`${friendlyCode("duplicate")}. Nothing to do.`);
  else if (bucket === "failed") lines.push("Not marked. The scanner laptop put this file aside. Please scan the sheet again.");
  if (!waiting && item.message && (bucket === "flagged" || bucket === "rejected")) {
    // The reasons below say why; n8n's note is only shown when there are none.
    const note = (item.review_reasons || []).length ? "" : outcomeNote(item.message);
    if (note) lines.push(note);
  } else if (!waiting && item.message && bucket !== "processed" && bucket !== "duplicates") {
    const m = friendlyMessage(item.message);
    if (!(bucket === "failed" && /scan (this|the) sheet again/i.test(m))) lines.push(m);
  }
  if (!waiting) {
    for (const r of item.review_reasons || []) {
      // "Couldn't save - trying again" is no longer true once the page waits for review.
      if (bucket === "unsaved" && /^(internal_error_not_saved|not_saved_permanent)$/i.test(String(r).trim())) continue;
      const t = friendlyReason(r);
      if (t && !(bucket === "failed" && /scan (this|the) sheet again/i.test(t))) lines.push(t);
    }
  }
  const MISMATCH_TEXT = "This sheet may be for a different instructor or subject.";
  const uniqueLines = [...new Set(lines.filter((t) => t && !(mismatch && t === MISMATCH_TEXT)))];

  const pillCls = waiting
    ? "waiting"
    : bucket === "unsaved"
      ? "flagged"
      : bucket === "failed" || bucket === "errors"
        ? "error"
        : alreadyScanned
          ? "duplicate"
          : reviewed
            ? "reviewed"
            : item.status;
  const rowCls = waiting ? "waiting" : bucket === "unsaved" ? "flagged" : bucket === "failed" || bucket === "errors" ? "error" : item.status;
  const at = item.created_at || item.updated_at;
  return (
    <li className={`item-row ${rowCls} ${mismatch ? "mismatch" : ""}`}>
      <div className="item-head">
        <span className={`pill ${pillCls}`}>{label}</span>
        <ScoreChip summary={item.summary} />
        <span className="item-file">{item.filename}</span>
        <span className="muted small" title={at ? new Date(at).toLocaleString() : undefined}>{timeAgo(at, now)}</span>
      </div>
      <ScoreLine summary={item.summary} />
      {bucket === "duplicates" && item.summary && (
        <div className="muted small">This is the result saved the first time this page was scanned.</div>
      )}
      {rotation && <div className="muted small">{rotation}</div>}
      {item.orientation_uncertain && (
        <div className="small orientation-note">
          This page may be sideways or upside down. Please check it on the correction screen.
        </div>
      )}
      {mismatch && <div className="mismatch-note">{MISMATCH_TEXT}</div>}
      {uniqueLines.map((t) => (
        <div key={t} className="small item-msg">{t}</div>
      ))}
      <IdentityCheck identity={item.identity} />
      {bucket === "flagged" && !reviewed && !alreadyScanned && reviewTo && (
        <Link className="small-link" to={reviewTo}>Review this form</Link>
      )}
      {alreadyScanned && reviewTo && (
        <Link className="small-link" to={reviewTo}>See it on the review page (Override if it is a different sheet)</Link>
      )}
      {bucket === "unsaved" && reviewTo && (
        <Link className="small-link" to={`${reviewTo}#couldnt-save`}>Try again or type in the answers</Link>
      )}
      {canView && (
        <div className="item-actions">
          <button type="button" className="small-btn" onClick={() => onView(item)}>View page</button>
        </div>
      )}
    </li>
  );
}

function plural(n, one, many) {
  return `${n} ${n === 1 ? one : many}`;
}

export default function ScanningLive({ stationId }) {
  const { batchId } = useParams();
  const navigate = useNavigate();
  const [batch, setBatch] = useState(null);
  const [error, setError] = useState("");
  const [dialog, setDialog] = useState(null); // "close" | "force"
  const [forceProcessing, setForceProcessing] = useState(false);
  const [acting, setActing] = useState(false);
  const [notFound, setNotFound] = useState(false);
  const [filter, setFilter] = useState("all");
  const [viewing, setViewing] = useState(null); // the page open in the page viewer
  const inFlight = useRef(false);
  const listRef = useRef(null);
  const now = useNow(1000);

  // Full scanner status (folder, queue...) for the laptop this batch belongs to.
  const { station: liveStation, setStation } = useStation(batch?.station?.id || stationId);

  const goHomeClosed = useCallback(() => navigate("/", { replace: true, state: { closedBatch: true } }), [navigate]);

  const refresh = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    try {
      const b = await api.getBatch(batchId);
      setBatch(b);
      setError(""); // errors clear on the next successful poll
      if (b.status === "closed") goHomeClosed();
    } catch (err) {
      if (err.status === 404) setNotFound(true);
      else setError(err.message);
    } finally {
      inFlight.current = false;
    }
  }, [batchId, goHomeClosed]);

  useEffect(() => {
    let timer;
    let stopped = false;
    const loop = async () => {
      await refresh();
      if (!stopped) timer = setTimeout(loop, POLL_MS);
    };
    loop();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [refresh]);

  const items = useMemo(() => allItems(batch), [batch]);

  async function doClose(force) {
    setActing(true);
    setError("");
    try {
      await api.closeBatch(batchId, force);
      goHomeClosed();
    } catch (err) {
      if (err.code === "items_processing") {
        setForceProcessing(true);
        setDialog("close");
        await refresh();
      } else {
        setError(err.message);
        setDialog(null);
      }
    } finally {
      setActing(false);
    }
  }

  async function doForceClose() {
    setActing(true);
    setError("");
    try {
      await api.forceCloseBatch(batchId);
      goHomeClosed();
    } catch (err) {
      setError(err.message);
      setDialog(null);
    } finally {
      setActing(false);
    }
  }

  function pickCounter(key) {
    setFilter((f) => (f === key ? "all" : key));
    listRef.current?.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
  }

  const back = { to: "/", label: "Home" };

  if (notFound) {
    return (
      <div>
        <PageHeader title="Batch not found" subtitle="This batch doesn't exist any more, or the link is wrong." back={back} />
        <button className="primary" onClick={() => navigate("/")}>Go to Home</button>
      </div>
    );
  }
  if (!batch) {
    return (
      <div>
        <PageHeader title="Step 3: Scan and check" back={back} />
        {error ? <div className="error" role="alert">{error}</div> : <Loading>Loading the batch...</Loading>}
      </div>
    );
  }

  const c = batch.counts || {};
  const n = (key) => Number(c[key]) || 0;
  const total = Number(c.total) || COUNTERS.reduce((sum, k) => sum + n(k.key), 0);
  const countFor = (key) => (key === "all" ? total : n(key));
  const markingOff = batch.marking_ready === false;
  const lastActivity = batch.last_activity_at ? new Date(batch.last_activity_at).getTime() : 0;
  const abandonedMs = (batch.abandoned_after_hours || 3) * 3600 * 1000;
  const canForce = lastActivity && now - lastActivity > abandonedMs;
  const abandonedHours = Number(batch.abandoned_after_hours) || 3;
  const forgottenHelp = `Only needed if a batch was left open. Available after ${abandonedHours} ${abandonedHours === 1 ? "hour" : "hours"} with no new scans.`;
  // Prefer the full station record; fall back to what the batch tells us.
  const station = liveStation && liveStation.id === batch.station?.id ? { ...batch.station, ...liveStation } : batch.station;
  const scannerState = getScannerState(station, now);
  const scannerReady = scannerState.tone === "green";
  const scannerKey = scannerState.key;
  const lastPageAt = batch.last_item_at || station?.watcher_last_file_at || batch.last_scan_at || (items[0] && (items[0].created_at || items[0].updated_at)) || null;
  const queued = Math.max(0, Number(station?.watcher_queued_files) || 0);
  const skipped = skippedCount(station);

  const shown = filter === "all" ? items : items.filter((it) => itemBucket(it) === filter);
  const expected = countFor(filter);
  const filterLabel = filter === "all" ? "All pages" : COUNTERS.find((k) => k.key === filter)?.label || LABEL[filter];
  const [emptyTitle, emptyText] = EMPTY_TEXT[filter] || EMPTY_TEXT.all;

  const riskyClose = forceProcessing || queued > 0 || n("processing") > 0 || n("errors") > 0;
  const reviewTo = `/batch/${batchId}/review`;
  // Flagged counts every flagged page; toReview only those not yet fixed (queue row still pending).
  const toReview = Number(batch.queue_pending) || 0;
  const importPending = Number(batch.import_pending) || 0;
  const flaggedDone = Math.max(0, n("flagged") - toReview);
  const unsaved = n("unsaved");
  const needsAttention = toReview + n("rejected") + unsaved;

  return (
    <div>
      <PageHeader
        title="Step 3: Scan and check"
        subtitle="Scan the sheets now. Each page shows up here within a few seconds. When the stack is done, press Finish and close batch."
        back={back}
        actions={batch.simulated && <span className="badge sim" title="Practice mode: results are made up, nothing is really scored">PRACTICE MODE</span>}
      />

      {markingOff && (
        <div className="warning-box marking-banner" role="status">
          <Icon name="alert" size={20} />
          <span>
            <strong>Marking is switched off on this server, so pages are saved but not marked yet.</strong>{" "}
            They show as "Waiting for marking" below.
          </span>
        </div>
      )}

      <ol className="mini-steps" aria-label="What to do">
        <li>Put the sheets in the scanner and scan</li>
        <li>Watch the pages appear below</li>
        <li>Check anything marked "Needs a look"</li>
        <li>Press Finish and close batch</li>
      </ol>

      <div className="batch-meta">
        <div><span className="muted">Instructor</span><strong>{batch.instructor.name}</strong>{batch.instructor.is_placeholder && <span className="badge">login not activated yet</span>}</div>
        <div><span className="muted">Subject</span><span><strong>{batch.subject.code}</strong> {batch.subject.name}</span></div>
        <div><span className="muted">Term</span>{batch.term.label || "-"}</div>
        <div><span className="muted">Laptop</span>{batch.station.label}</div>
        <div><span className="muted">Started</span>{new Date(batch.created_at).toLocaleString()}{batch.created_by_name ? ` by ${batch.created_by_name}` : ""}</div>
      </div>

      <div className="status-strip">
        <ScannerStatus station={station} />
        <span className="muted">Last page received: {lastPageAt ? timeAgo(lastPageAt, now) : "none yet"}</span>
        {queued > 0 && <span className="muted">Waiting to send: <strong>{queued}</strong></span>}
      </div>

      {!scannerReady && (
        <div className="warning-box small">
          {scannerKey === "offline" ? (
            <>
              The Scan Watcher is off. Open <strong>Start Scan Watcher</strong> on the scanner laptop. Pages you scan
              meanwhile wait in the scan folder and are sent once it starts (if this batch is open).
            </>
          ) : (
            <>
              New scans won't arrive until the scanner laptop is working. Open{" "}
              <Link to="/scanner-setup">Step 1: Scanner setup</Link>{" "}
              to see what's wrong. This batch stays open meanwhile.
            </>
          )}
        </div>
      )}
      <OldWatcherNote station={station} />

      <SkippedPagesCard
        station={station}
        onStationChange={(s) => {
          setStation(s);
          refresh();
        }}
      />

      <div className="counters" role="group" aria-label="Page totals. Choose one to show only those pages.">
        {COUNTERS.map((k) => {
          const selected = filter === k.key;
          const button = (
            <button
              type="button"
              key={k.key}
              className={`counter ${k.cls} ${selected ? "selected" : ""}`}
              title={k.help}
              aria-pressed={selected}
              onClick={() => pickCounter(k.key)}
            >
              <span className="num">{n(k.key)}</span>
              <span className="counter-label">{k.label}</span>
              {k.key === "errors" && markingOff && n("errors") > 0 && <span className="counter-sub">waiting for marking</span>}
              {k.key === "flagged" && n("flagged") > 0 && (
                <span className="counter-sub">{toReview > 0 ? `${toReview} still to review` : "all reviewed"}</span>
              )}
              <span className="sr-only">. {k.help}</span>
            </button>
          );
          if (k.key !== "flagged") return button;
          // "Flagged" also leads to the review page, where these forms are fixed.
          return (
            <div className="counter-wrap" key={k.key}>
              {button}
              <Link className="counter-link" to={reviewTo}>
                {toReview > 0 ? "Review now" : "Review page"}
              </Link>
            </div>
          );
        })}
      </div>

      {(toReview > 0 || importPending > 0 || unsaved > 0) && (
        <div className="review-callout" role="status">
          <Icon name="alert" size={20} />
          <span>
            <strong>
              {[
                toReview > 0 &&
                  (flaggedDone > 0
                    ? `${toReview} of ${n("flagged")} flagged forms still ${toReview === 1 ? "needs" : "need"} review`
                    : `${toReview === 1 ? "1 form needs" : `${toReview} forms need`} review`),
                importPending > 0 && `${importPending === 1 ? "1 form" : `${importPending} forms`} couldn't be saved`,
                unsaved > 0 && `${unsaved === 1 ? "1 page" : `${unsaved} pages`} couldn't be saved by marking`,
              ]
                .filter(Boolean)
                .join(" and ")}
              .
            </strong>{" "}
            You can fix {toReview + importPending + unsaved === 1 ? "it" : "them"} now, even while scanning continues.
          </span>
          <Link className="btn primary" to={reviewTo}>Open review page</Link>
        </div>
      )}

      <section className="section" aria-labelledby="pages-title" ref={listRef}>
        <h2 id="pages-title">Pages</h2>
        <div className="filter-chips" role="group" aria-label="Show pages">
          {FILTERS.map((f) => {
            const selected = filter === f.key;
            return (
              <button
                type="button"
                key={f.key}
                className={`chip ${selected ? "selected" : ""}`}
                aria-pressed={selected}
                onClick={() => setFilter(f.key)}
              >
                {f.label} <span className="chip-count">{countFor(f.key)}</span>
              </button>
            );
          })}
        </div>

        {filter === "flagged" && shown.length > 0 && (
          <p className="small">
            These were scored, but a person should check them. Fix them on the <Link to={reviewTo}>review page</Link> (or
            the usual correction screen in the app).
          </p>
        )}

        {shown.length === 0 ? (
          expected > 0 ? (
            <EmptyState icon="scan" title={`${plural(expected, "page", "pages")} - older than the latest list`}>
              This screen shows the most recent pages. Older ones are still counted and saved.
            </EmptyState>
          ) : (
            <EmptyState icon="scan" title={emptyTitle}>{emptyText}</EmptyState>
          )
        ) : (
          <>
            <ul className="item-list" aria-label={filterLabel}>
              {shown.map((it) => (
                <ItemRow
                  key={it.id}
                  item={it}
                  now={now}
                  markingOff={markingOff}
                  reviewTo={reviewTo}
                  canView={batch.can_view_pages === true}
                  onView={setViewing}
                />
              ))}
            </ul>
            {shown.length < expected && (
              <p className="muted small">Showing the {shown.length} most recent of {expected}. Older pages are still counted and saved.</p>
            )}
          </>
        )}
      </section>

      {batch.can_view_pages === true && batch.page_images_enabled === false && (
        <p className="muted small">Pictures of scanned pages aren't kept on this server yet, so "View page" shows the result only.</p>
      )}

      {viewing && (
        <PageViewer title={viewing.filename || "Scanned page"} batchId={batchId} itemId={viewing.id} onClose={() => setViewing(null)} />
      )}

      {error && <div className="error" role="alert">{error}</div>}

      <div className="actions">
        <button className="primary big" disabled={acting} onClick={() => { setForceProcessing(false); setDialog("close"); }}>
          <Icon name="check" /> Finish and close batch
        </button>
        <button
          disabled={acting || !canForce}
          onClick={() => setDialog("force")}
          title={canForce ? "" : forgottenHelp}
        >
          Close a forgotten batch
        </button>
      </div>
      <p className="muted small">
        {canForce ? "Only needed if a batch was left open." : forgottenHelp}
      </p>

      {dialog === "close" && (
        <ConfirmDialog
          title="Finish and close this batch?"
          confirmLabel={riskyClose ? "Close anyway" : "Close batch"}
          cancelLabel="Keep scanning"
          danger={needsAttention > 0 || riskyClose}
          busy={acting}
          onCancel={() => setDialog(null)}
          onConfirm={() => doClose(forceProcessing || n("processing") > 0)}
        >
          {queued > 0 && (
            <p className="dialog-warn">
              <strong>{queued === 1 ? "1 page is" : `${queued} pages are`} still waiting to be sent from the laptop.</strong>{" "}
              If you close now {queued === 1 ? "it won't" : "they won't"} be in this batch. Wait a moment, or close anyway.
            </p>
          )}
          {n("processing") > 0 && (
            <p className="dialog-warn">
              <strong>{n("processing") === 1 ? "1 page is" : `${n("processing")} pages are`} still being worked on.</strong>{" "}
              {n("processing") === 1 ? "It" : "They"} will finish, but you won't see the result here if you close now.
            </p>
          )}
          {n("errors") > 0 && (
            <p className="dialog-warn">
              {markingOff ? (
                <>
                  <strong>{n("errors") === 1 ? "1 page is" : `${n("errors")} pages are`} waiting for marking.</strong>{" "}
                  Keep the batch open if you want {n("errors") === 1 ? "it" : "them"} marked in this batch once marking is turned on.
                </>
              ) : (
                <>
                  <strong>{n("errors") === 1 ? "1 page is" : `${n("errors")} pages are`} being tried again.</strong>{" "}
                  Once the batch is closed {n("errors") === 1 ? "it won't" : "they won't"} be tried again.
                </>
              )}
            </p>
          )}
          {skipped > 0 && (
            <p>
              {skipped === 1 ? "1 page" : `${skipped} pages`} scanned before this batch started {skipped === 1 ? "is" : "are"} still on the
              laptop. Add {skipped === 1 ? "it" : "them"} first if {skipped === 1 ? "it belongs" : "they belong"} to this batch.
            </p>
          )}
          <p>
            <strong>{n("processed")}</strong> scored, <strong>{n("flagged")}</strong> flagged ({toReview} still to review),{" "}
            <strong>{n("rejected")}</strong> rejected,{" "}
            {unsaved > 0 && <><strong>{unsaved}</strong> couldn't be saved, </>}
            <strong>{n("failed")}</strong> failed.
          </p>
          {needsAttention > 0 && (
            <p>
              Flagged forms still to review, rejected sheets and pages that couldn't be saved stay with this batch, so you
              can check them later on the review page.
            </p>
          )}
          <p>Anything scanned after closing waits until the next batch is started.</p>
        </ConfirmDialog>
      )}

      {dialog === "force" && (
        <ConfirmDialog
          title="Close this forgotten batch?"
          confirmLabel="Yes, close it"
          danger
          busy={acting}
          onCancel={() => setDialog(null)}
          onConfirm={doForceClose}
        >
          <p>The last scan was {timeAgo(batch.last_activity_at, now)}. The batch will be marked as closed because it was left open.</p>
        </ConfirmDialog>
      )}
    </div>
  );
}
