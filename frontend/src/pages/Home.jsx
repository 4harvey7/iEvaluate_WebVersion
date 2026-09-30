import React, { useEffect, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { api } from "../api.js";
import { Loading, PageHeader } from "../components.jsx";
import { Icon } from "../icons.jsx";
import { ScannerStatus, SkippedPagesCard, getScannerState, hasBeenOnline, useStation } from "../scanner.jsx";

// "Getting started" checklist: shows what's done and the one next thing to do.
export default function Home({ stationId, profile, onSwitchStation, onStationMissing }) {
  const location = useLocation();
  const justClosed = Boolean(location.state?.closedBatch);
  const { station, loading, error: stationError } = useStation(stationId, { onMissing: onStationMissing });
  const [openBatch, setOpenBatch] = useState(undefined); // undefined = still checking
  const [batchError, setBatchError] = useState("");

  useEffect(() => {
    let cancelled = false;
    api
      .getOpenBatch(stationId)
      .then((b) => !cancelled && setOpenBatch(b || null))
      .catch((e) => {
        if (cancelled) return;
        setOpenBatch(null);
        setBatchError(e.message);
      });
    return () => {
      cancelled = true;
    };
  }, [stationId]);

  const scanner = getScannerState(station);
  const scannerReady = scanner.tone === "green";
  const scannerInstalled = hasBeenOnline(station);
  const batchOpen = Boolean(openBatch);

  const steps = [
    {
      id: "laptop",
      title: "Choose this laptop",
      done: true,
      body: (
        <>
          This computer is <strong>{station?.label || "set up"}</strong>.{" "}
          <button type="button" className="link-btn" onClick={onSwitchStation}>Not right? Change laptop</button>
        </>
      ),
    },
    {
      id: "scanner",
      title: "Scanner setup",
      done: scannerReady,
      warn: scannerInstalled && !scannerReady,
      body: scannerReady ? (
        <ScannerStatus station={station} />
      ) : scannerInstalled ? (
        <>
          <ScannerStatus station={station} />
          <div className="muted small">Open Scanner setup to see how to fix this.</div>
        </>
      ) : (
        "Install the scan program on the laptop connected to the scanner. It takes about 5 minutes, once."
      ),
      to: "/scanner-setup",
      cta: "Open Scanner setup",
    },
    {
      id: "start",
      title: "Start a batch",
      done: batchOpen,
      body: batchOpen
        ? "A batch is open on this laptop."
        : "Choose the instructor and subject for the stack of sheets you're about to scan.",
      to: "/start",
      cta: "Start a batch",
    },
    {
      id: "scan",
      title: "Scan and check the results",
      done: false,
      body: batchOpen
        ? "Scan the sheets. Pages appear on the batch page within seconds. Check anything marked \"Needs a look\", then finish and close the batch."
        : "After you start a batch, scan the sheets and watch them arrive. Then finish and close the batch.",
      to: batchOpen ? `/batch/${openBatch.id}` : null,
      cta: "Open the current batch",
    },
  ];

  // The next thing to do: an open batch always wins, otherwise the first step not done.
  const nextId = batchOpen ? "scan" : (steps.find((s) => !s.done) || {}).id;

  return (
    <div>
      <PageHeader
        title={`Hello${profile?.first_name ? `, ${profile.first_name}` : ""}`}
        subtitle="This checklist shows what's ready and what to do next to scan a stack of test sheets."
      />

      {justClosed && (
        <div className="success-box" role="status">
          Batch closed. Its results are saved. Start a new batch when you're ready for the next stack.
        </div>
      )}

      {openBatch !== undefined && (
        <SkippedPagesCard
          station={station}
          variant="brief"
          link={batchOpen ? `/batch/${openBatch.id}` : "/start"}
          linkLabel={batchOpen ? "Open the current batch to add or discard them" : "Start a batch, then add or discard them"}
        />
      )}

      {loading && openBatch === undefined ? (
        <Loading>Checking this laptop...</Loading>
      ) : (
        <ol className="checklist">
          {steps.map((s, i) => {
            const isNext = s.id === nextId;
            const state = s.done ? "done" : s.warn ? "warn" : isNext ? "next" : "todo";
            return (
              <li key={s.id} className={`check-item ${state}`}>
                <span className="check-mark" aria-hidden="true">
                  {s.done ? <Icon name="check" size={18} /> : s.warn ? "!" : i}
                </span>
                <div className="check-body">
                  <div className="check-title">
                    {s.title}
                    {s.done && <span className="sr-only"> (done)</span>}
                    {isNext && <span className="badge next-badge">Next</span>}
                  </div>
                  <div className="check-text">{s.body}</div>
                  {s.to && (isNext || s.warn) && (
                    <Link to={s.to} className={`btn ${isNext ? "primary big" : ""}`}>
                      {s.cta}
                      <Icon name="chevronRight" size={18} />
                    </Link>
                  )}
                  {s.to && !isNext && !s.warn && !s.done && (
                    <Link to={s.to} className="small-link">{s.cta}</Link>
                  )}
                </div>
              </li>
            );
          })}
        </ol>
      )}

      {stationError && <div className="error" role="alert">{stationError}</div>}
      {batchError && <div className="error" role="alert">{batchError}</div>}

      <p className="muted small">
        Stuck? The <Link to="/help">Help page</Link> has the install steps and answers to common questions.
      </p>
    </div>
  );
}
