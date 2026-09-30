import React, { useEffect, useId, useRef, useState } from "react";
import { api } from "./api.js";
import { Loading } from "./components.jsx";
import { friendlyReason, outcomeNote } from "./messages.js";

// Page viewer: the scanned page (as it was sent for marking, after
// auto-rotation) plus its result. Used by the live batch page and the review page.

export const RATING_KEYS = [
  ...Array.from({ length: 10 }, (_, i) => `m${i + 1}`),
  ...Array.from({ length: 10 }, (_, i) => `p${i + 1}`),
];

const avg = (xs) => (xs.length ? Math.round((xs.reduce((a, b) => a + b, 0) / xs.length) * 100) / 100 : null);

// Same rule as the server (app/page_scores.py): blanks are left out of the averages.
export function scoreFromRatings(ratings) {
  const clean = {};
  for (const k of RATING_KEYS) {
    const v = Number(ratings?.[k]);
    clean[k] = Number.isInteger(v) && v >= 1 && v <= 5 ? v : null;
  }
  const m = RATING_KEYS.filter((k) => k[0] === "m" && clean[k] != null).map((k) => clean[k]);
  const p = RATING_KEYS.filter((k) => k[0] === "p" && clean[k] != null).map((k) => clean[k]);
  return {
    ratings: clean,
    answered: m.length + p.length,
    questions: RATING_KEYS.length,
    average: avg([...m, ...p]),
    management_avg: avg(m),
    performance_avg: avg(p),
  };
}

const fmt = (v) => (v == null ? "-" : Number(v).toFixed(2));

// Compact, prominent score for a row: "4.35 /5" plus how many answers were read.
export function ScoreChip({ summary }) {
  if (!summary || summary.average == null) return null;
  const tone = summary.credited ? "credited" : "partial";
  return (
    <span
      className={`score-chip ${tone}`}
      title={`Average rating ${fmt(summary.average)} out of 5 (${summary.answered} of ${summary.questions || 20} answered)`}
    >
      <strong>{fmt(summary.average)}</strong>
      <span className="score-of">/5</span>
    </span>
  );
}

// One short line under the row: answered, M/P averages and who it was credited to.
export function ScoreLine({ summary }) {
  if (!summary) return null;
  const who = [summary.instructor, summary.subject_code].filter(Boolean).join(" · ");
  const parts = [];
  if (summary.answered != null) parts.push(`${summary.answered}/${summary.questions || 20} answered`);
  if (summary.management_avg != null) parts.push(`Management ${fmt(summary.management_avg)}`);
  if (summary.performance_avg != null) parts.push(`Performance ${fmt(summary.performance_avg)}`);
  return (
    <div className="small score-line">
      {parts.join(" · ")}
      {who && (
        <span className="muted">
          {parts.length ? " · " : ""}
          {summary.credited ? "Credited to " : "Suggested: "}
          {who}
        </span>
      )}
    </div>
  );
}

function RatingsView({ ratings }) {
  const group = (prefix, title) => (
    <div className="ratings-view-group">
      <div className="small rv-title">{title}</div>
      <div className="ratings-view">
        {Array.from({ length: 10 }, (_, i) => {
          const k = `${prefix}${i + 1}`;
          const v = ratings?.[k];
          return (
            <div key={k} className={`rv-cell ${v == null ? "blank" : ""}`}>
              <span className="rv-key">{k.toUpperCase()}</span>
              <span className="rv-val">{v ?? "-"}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
  return (
    <div className="ratings-readonly">
      {group("m", "Management (M1 to M10)")}
      {group("p", "Performance (P1 to P10)")}
      <p className="muted small">5 Outstanding · 4 Very satisfactory · 3 Satisfactory · 2 Fair · 1 Unsatisfactory · - blank</p>
    </div>
  );
}

function imageMissingText(err, kind) {
  if (kind === "item" && err?.status === 404) {
    const since = err?.detail?.kept_since;
    if (err?.code === "image_not_kept" && since) {
      return `Image not kept for pages scanned before ${new Date(since).toLocaleDateString()}.`;
    }
    return err?.message || "No picture was kept for this page.";
  }
  if (err?.status === 404) return "No picture was saved for this form.";
  if (err?.status === 403) return err?.message || "You can't open the pages of this batch.";
  return "The picture couldn't be loaded. Close and open it again to try again.";
}

// The scanned page as a blob URL fetched with the login token.
export function ScanImage({ kind, id, batchId }) {
  const [src, setSrc] = useState(null);
  const [state, setState] = useState("loading");
  const [problem, setProblem] = useState("");
  const [zoom, setZoom] = useState(false);

  useEffect(() => {
    let alive = true;
    let url = null;
    setState("loading");
    setSrc(null);
    api
      .reviewImage(kind, id, batchId)
      .then((blob) => {
        url = URL.createObjectURL(blob);
        if (alive) {
          setSrc(url);
          setState("ok");
        } else {
          URL.revokeObjectURL(url);
        }
      })
      .catch((err) => {
        if (!alive) return;
        setProblem(imageMissingText(err, kind));
        setState(err?.status === 404 ? "none" : "error");
      });
    return () => {
      alive = false;
      if (url) URL.revokeObjectURL(url);
    };
  }, [kind, id, batchId]);

  if (state === "loading") return <div className="scan-image placeholder"><Loading>Loading the scan...</Loading></div>;
  if (state !== "ok") return <div className="scan-image placeholder muted">{problem}</div>;
  return (
    <figure className={`scan-image ${zoom ? "zoomed" : ""}`}>
      <button type="button" className="scan-image-btn" onClick={() => setZoom((z) => !z)} aria-label={zoom ? "Show the scan smaller" : "Show the scan bigger"}>
        <img src={src} alt="The scanned form" />
      </button>
      <figcaption className="small">
        <button type="button" className="link-btn" onClick={() => setZoom((z) => !z)}>{zoom ? "Smaller" : "Bigger"}</button>
        {" · "}
        <a href={src} target="_blank" rel="noreferrer">Open full size</a>
      </figcaption>
    </figure>
  );
}

function ResultPanel({ summary, status, reasons, message, loading }) {
  const notes = [...new Set((reasons || []).map(friendlyReason).filter(Boolean))];
  const note = !notes.length && message ? outcomeNote(message) : "";
  return (
    <div className="viewer-result">
      {loading ? (
        <Loading>Loading the result...</Loading>
      ) : summary && summary.average != null ? (
        <>
          <div className="viewer-score">
            <ScoreChip summary={summary} />
            <div>
              <div><strong>Average rating {fmt(summary.average)} out of 5</strong></div>
              <ScoreLine summary={summary} />
              {summary.student_id && <div className="small muted">Student {summary.student_id}</div>}
              {summary.agreement_percent != null && (
                <div className="small muted">The answer reader and the AI agreed on {summary.agreement_percent}% of the rows.</div>
              )}
              {!summary.credited && (
                <div className="small">Not credited yet: a person has to check this form on the review page first.</div>
              )}
            </div>
          </div>
          <RatingsView ratings={summary.ratings} />
        </>
      ) : (
        <p className="small muted">
          {status === "rejected"
            ? "No score: this page was not scored."
            : status === "processing"
              ? "Still being scored."
              : status === "error"
                ? "No score yet: marking hasn't saved this page."
                : "No score was kept for this page."}
        </p>
      )}
      {notes.map((t) => <div key={t} className="small item-msg">{t}</div>)}
      {note && <div className="small item-msg">{note}</div>}
    </div>
  );
}

// Modal. For a live-batch page pass batchId + itemId (the result is loaded
// here); for a review row pass kind/id for the picture and the summary.
export function PageViewer({ title, batchId, itemId, kind = "item", id, summary: given, status, reasons, message, onClose }) {
  const titleId = useId();
  const closeRef = useRef(null);
  const [detail, setDetail] = useState(null);
  const [loadError, setLoadError] = useState("");
  const isItem = kind === "item";

  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && onClose?.();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    const previous = document.activeElement;
    closeRef.current?.focus();
    return () => previous?.focus?.();
  }, []);

  useEffect(() => {
    if (!isItem) return undefined;
    let alive = true;
    setDetail(null);
    setLoadError("");
    api
      .getBatchItem(batchId, itemId)
      .then((d) => alive && setDetail(d))
      .catch((err) => alive && setLoadError(err?.message || "The result couldn't be loaded."));
    return () => {
      alive = false;
    };
  }, [isItem, batchId, itemId]);

  const summary = isItem ? detail?.summary : given;
  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby={titleId} onClick={(e) => e.target === e.currentTarget && onClose?.()}>
      <div className="modal page-viewer">
        <div className="viewer-head">
          <h2 id={titleId}>{title || "Scanned page"}</h2>
          <button ref={closeRef} type="button" className="small-btn" onClick={onClose}>Close</button>
        </div>
        <div className="viewer-body">
          <div className="viewer-image">
            {isItem && loadError ? (
              <div className="scan-image placeholder muted">{loadError}</div>
            ) : (
              <ScanImage kind={kind} id={isItem ? itemId : id} batchId={batchId} />
            )}
          </div>
          {!(isItem && loadError) && (
            <ResultPanel
              summary={summary}
              loading={isItem && !detail}
              status={isItem ? detail?.status : status}
              reasons={isItem ? detail?.review_reasons : reasons}
              message={isItem ? detail?.message : message}
            />
          )}
        </div>
      </div>
    </div>
  );
}
