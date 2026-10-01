import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api.js";
import { EmptyState, Loading, PageHeader } from "../components.jsx";

const PAGE_SIZE = 20;
const RETRY_MS = 15000;

function dateText(iso) {
  if (!iso) return "-";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "-" : d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

function DateSpan({ iso }) {
  return <span title={iso ? new Date(iso).toLocaleString() : undefined}>{dateText(iso)}</span>;
}

// The /review page: every batch this person may review, open or closed.
export default function ReviewBatches({ profile, stationId }) {
  const [onlyPending, setOnlyPending] = useState(false);
  const [list, setList] = useState({ loaded: false, batches: [], next: null, migrationReady: true });
  const [error, setError] = useState("");
  const [reloadKey, setReloadKey] = useState(0);
  const [moreBusy, setMoreBusy] = useState(false);
  const [moreError, setMoreError] = useState("");
  const [hereId, setHereId] = useState(null);

  useEffect(() => {
    if (!stationId) return;
    api.getOpenBatch(stationId).then((b) => setHereId(b?.id || null)).catch(() => {});
  }, [stationId]);

  useEffect(() => {
    const ctrl = new AbortController();
    let timer = null;
    setList((l) => ({ ...l, loaded: false }));
    const load = () => {
      api
        .listReviewBatches({ onlyPending, limit: PAGE_SIZE }, ctrl.signal)
        .then((d) => {
          setList({ loaded: true, batches: d.batches || [], next: d.next_before || null, migrationReady: d.migration_ready !== false });
          setError("");
        })
        .catch((e) => {
          if (e?.name === "AbortError") return;
          setError(e?.message || "Something went wrong.");
          timer = setTimeout(load, RETRY_MS);
        });
    };
    load();
    return () => {
      ctrl.abort();
      clearTimeout(timer);
    };
  }, [onlyPending, reloadKey]);

  const loadMore = () => {
    setMoreBusy(true);
    setMoreError("");
    api
      .listReviewBatches({ before: list.next, onlyPending, limit: PAGE_SIZE })
      .then((d) =>
        setList((l) => {
          const seen = new Set(l.batches.map((b) => b.id));
          return { ...l, batches: [...l.batches, ...(d.batches || []).filter((b) => !seen.has(b.id))], next: d.next_before || null };
        }),
      )
      .catch((e) => setMoreError(e?.message || "Something went wrong."))
      .finally(() => setMoreBusy(false));
  };

  const batches = list.batches;
  const rows = hereId ? [...batches.filter((b) => b.id === hereId), ...batches.filter((b) => b.id !== hereId)] : batches;

  return (
    <div>
      <PageHeader title="Review forms" subtitle="Pick a batch to check its forms. Open and closed batches are both here, so you can review forms any time." />
      <p className="muted small">
        {profile?.is_admin
          ? "You see every batch because you are an SAO admin."
          : "You see the batches you started. An SAO admin can see all batches."}
      </p>
      {!list.migrationReady && (
        <div className="warning-box small">
          <strong>Read-only for now.</strong> Saving on the review pages needs a database update (migration 005). Ask IT to apply it. You can still open each batch and see its forms.
        </div>
      )}
      <label className="check-inline">
        <input type="checkbox" checked={onlyPending} onChange={(e) => setOnlyPending(e.target.checked)} /> Only show batches with forms to review
      </label>

      {error && !list.loaded && (
        <div>
          <div className="error" role="alert">Can't load the batches right now. {error}</div>
          <button type="button" onClick={() => setReloadKey((k) => k + 1)}>Try again now</button>
        </div>
      )}
      {!list.loaded && !error && <Loading>Loading your batches...</Loading>}

      {list.loaded && rows.length === 0 && !onlyPending && (
        <EmptyState icon="folder" title="No batches yet">
          {profile?.is_admin ? "Batches show up here as soon as someone starts one." : "Batches you start show up here, so you can review their forms any time."}
        </EmptyState>
      )}
      {list.loaded && rows.length === 0 && onlyPending && (
        <EmptyState icon="check" title="Nothing left to review">
          Every form in your batches is done. Untick "Only show batches with forms to review" to see all batches.
        </EmptyState>
      )}

      {list.loaded && rows.length > 0 && (
        <div className="review-table" role="list" aria-label="Batches">
          <div className="review-head" aria-hidden="true">
            <span>#</span>
            <span>Instructor and subject</span>
            <span>Term and laptop</span>
            <span>Started</span>
            <span>Status</span>
            <span />
          </div>
          {rows.map((b, i) => {
            const open = b.status === "open";
            const c = b.counts || {};
            return (
              <div role="listitem" className="review-item" key={b.id}>
                <div className="review-row">
                  <span className="rr-num">{i + 1}</span>
                  <span className="rr-form">
                    <strong>{b.instructor?.name || "Unknown instructor"}</strong>
                    <span className="muted small">{b.subject?.code} {b.subject?.name}</span>
                  </span>
                  <span className="rr-problem small">
                    <span className="rr-label">Term and laptop: </span>
                    {b.term?.label || "-"} · {b.station?.label || "-"}
                  </span>
                  <span className="rr-credit small">
                    <span className="rr-label">Started: </span>
                    <DateSpan iso={b.created_at} />
                    {b.started_by?.name ? ` by ${b.started_by.name}` : ""}
                    {b.closed_at && (
                      <span className="muted"> · Closed <DateSpan iso={b.closed_at} /></span>
                    )}
                  </span>
                  <span className="rr-status">
                    <span className={`pill ${open ? "proc" : "duplicate"}`}>{open ? "Open" : "Closed"}</span>
                    {b.id === hereId && <span className="badge">On this laptop</span>}
                    {c.queue_pending > 0 && <span className="pill flagged">Needs review: {c.queue_pending}</span>}
                    {c.import_pending > 0 && <span className="pill flagged">Import errors: {c.import_pending}</span>}
                    {c.unsaved_pending > 0 && <span className="pill error">Couldn't be saved: {c.unsaved_pending}</span>}
                    {b.to_review === 0 && <span className="pill success">Nothing left to review</span>}
                    {b.access === "own" && <span className="muted small">Only your forms</span>}
                  </span>
                  <span className="rr-action">
                    <Link className="btn small-btn" to={`/batch/${b.id}/review`}>{b.to_review > 0 ? "Review" : "View"}</Link>
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {list.loaded && list.next && (
        <button type="button" onClick={loadMore} disabled={moreBusy}>{moreBusy ? "Loading..." : "Show more batches"}</button>
      )}
      {list.loaded && !list.next && rows.length > 0 && <p className="muted small">That's all the batches.</p>}
      {moreError && <div className="error" role="alert">Can't load more right now. {moreError}</div>}
    </div>
  );
}
