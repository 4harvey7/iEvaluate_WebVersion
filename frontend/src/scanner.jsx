import React, { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "./api.js";
import { ConfirmDialog, timeAgo, useNow } from "./components.jsx";
import { Icon } from "./icons.jsx";

export const DEFAULT_FOLDER = "C:\\Scans";
export const STATUS_POLL_MS = 10000;

// Show folders the way Windows users know them: C:\Scans, not C:/Scans.
export function toWindowsPath(p) {
  return typeof p === "string" ? p.trim().replace(/\//g, "\\") : "";
}

function folderKey(p) {
  return toWindowsPath(p).replace(/\\+$/, "").toLowerCase();
}

export function sameFolder(a, b) {
  return folderKey(a) === folderKey(b);
}

export function isOnline(station) {
  if (!station) return false;
  return Boolean(station.watcher_online ?? station.online);
}

export function hasBeenOnline(station) {
  return Boolean(station?.last_seen_at);
}

// One place that turns a station into plain-English status. Every status
// badge in the app uses this so the wording is always the same.
// tone: green | red | amber | grey | unknown
export function getScannerState(station, now = Date.now()) {
  if (!station) return { tone: "unknown", key: "unknown", title: "Checking...", message: "" };
  const online = isOnline(station);
  const folder = toWindowsPath(station.watcher_folder || station.folder_path || "");
  const err = station.watcher_folder_error || null;

  if (!online) {
    if (!hasBeenOnline(station)) {
      return { tone: "grey", key: "not_installed", title: "Not installed yet", message: "" };
    }
    return {
      tone: "red",
      key: "offline",
      title: "Offline",
      message: `the scanner laptop is off or not connected (last seen ${timeAgo(station.last_seen_at, now)})`,
    };
  }
  if (err === "unsafe_path") {
    return { tone: "amber", key: "unsafe_path", title: "That folder isn't allowed", message: "pick a folder just for scans" };
  }
  if (err === "access_denied") {
    return {
      tone: "amber",
      key: "access_denied",
      title: "Can't open the folder",
      message: folder ? `the laptop isn't allowed to open that folder (${folder})` : "the laptop isn't allowed to open that folder",
    };
  }
  if (err === "not_found" || station.watcher_folder_exists === false) {
    return {
      tone: "amber",
      key: "not_found",
      title: "Folder not found",
      message: folder ? `the laptop can't find ${folder}` : "the laptop can't find the scan folder",
    };
  }
  if (err) {
    return { tone: "amber", key: "folder_problem", title: "Problem with the scan folder", message: "check the folder setting" };
  }
  return {
    tone: "green",
    key: "online",
    title: "Online",
    message: station.watcher_folder ? `watching ${toWindowsPath(station.watcher_folder)}` : "",
  };
}

// The laptop checks for a new folder setting every ~30 seconds.
export function isSwitchingFolder(station) {
  return Boolean(
    isOnline(station) &&
      station?.folder_path &&
      station?.watcher_folder &&
      !sameFolder(station.folder_path, station.watcher_folder)
  );
}

// Loads a station and refreshes it every `intervalMs` while the page is open
// (skips refreshes while the browser tab is hidden).
export function useStation(stationId, { intervalMs = STATUS_POLL_MS, onMissing } = {}) {
  const [station, setStationState] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(Boolean(stationId));
  const seq = useRef(0);
  const onMissingRef = useRef(onMissing);
  onMissingRef.current = onMissing;

  const refresh = useCallback(async () => {
    if (!stationId) return null;
    const id = ++seq.current;
    try {
      const s = await api.getStation(stationId);
      if (id !== seq.current) return null;
      setStationState(s);
      setError("");
      return s;
    } catch (err) {
      if (id !== seq.current) return null;
      if (err?.status === 404) onMissingRef.current?.();
      else setError(err?.message || "Couldn't check the scanner laptop.");
      return null;
    } finally {
      if (id === seq.current) setLoading(false);
    }
  }, [stationId]);

  // A local update (e.g. after saving) wins over any refresh still in flight.
  const setStation = useCallback((s) => {
    seq.current += 1;
    setStationState(s);
    setLoading(false);
  }, []);

  useEffect(() => {
    if (!stationId) return undefined;
    refresh();
    if (!intervalMs) return undefined;
    const timer = setInterval(() => {
      if (!document.hidden) refresh();
    }, intervalMs);
    const onVisible = () => {
      if (!document.hidden) refresh();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [stationId, intervalMs, refresh]);

  return { station, setStation, error, loading, refresh };
}

function StatusText({ state }) {
  return (
    <>
      <strong>{state.title}</strong>
      {state.message && <> &mdash; {state.message}</>}
    </>
  );
}

// variant="badge": one-line coloured light (lists, headers, sidebar).
// variant="card": big status card with extra details (setup + home pages).
export function ScannerStatus({ station, variant = "badge", showDetails = true, hint, className = "" }) {
  const now = useNow(5000);
  const state = getScannerState(station, now);

  if (variant === "badge") {
    return (
      <span className={`scanner-badge tone-${state.tone} ${className}`}>
        <span className="light" aria-hidden="true" />
        <span className="scanner-badge-text">
          <StatusText state={state} />
        </span>
      </span>
    );
  }

  const queued = Number(station?.watcher_queued_files) || 0;
  const switching = isSwitchingFolder(station);
  const advice = {
    not_installed: "Follow the install steps to set it up. It only takes a few minutes.",
    offline: "Check that the scanner laptop is turned on, logged in and connected to the internet.",
    not_found: "Check the folder name below, or create that folder on the scanner laptop.",
    access_denied: "Pick a different folder, such as C:\\Scans, in the Scan folder setting and click Save folder.",
    unsafe_path:
      "Type a folder like C:\\Scans in the Scan folder setting and click Save folder. The laptop waits until you do.",
    folder_problem: "Check the folder setting below.",
  }[state.key];

  return (
    <div className={`scanner-card tone-${state.tone} ${className}`} role="status" aria-live="polite">
      <span className="big-light" aria-hidden="true" />
      <div className="scanner-card-body">
        <div className="scanner-card-title">
          <StatusText state={state} />
        </div>
        {showDetails && station && (
          <ul className="scanner-facts">
            {hasBeenOnline(station) && (
              <li>
                Last page received:{" "}
                {station.watcher_last_file_at ? timeAgo(station.watcher_last_file_at, now) : "none yet"}
              </li>
            )}
            {queued > 0 && (
              <li>
                Waiting to send: <strong>{queued}</strong> file{queued === 1 ? "" : "s"}
              </li>
            )}
            {switching && <li className="switching">Switching to the new folder&hellip; (up to 30 seconds)</li>}
            {advice && <li className="advice">{advice}</li>}
          </ul>
        )}
        {hint && <div className="muted small">{hint}</div>}
      </div>
    </div>
  );
}

export function skippedCount(station) {
  return Math.max(0, Number(station?.watcher_skipped_files) || 0);
}

// The laptop picks up a website request (resend / discard) within ~30 seconds.
export function skippedCommandPending(station) {
  return station?.watcher_command === "resend_skipped" || station?.watcher_command === "discard_skipped";
}

const pagesWord = (n) => `${n} page${n === 1 ? " was" : "s were"}`;

// Pages the laptop held back because they were scanned before the batch
// started. variant="full" (batch page): Add to this batch / Discard buttons.
// variant="brief" (Home, Start a batch): one line + a link.
export function SkippedPagesCard({ station, onStationChange, variant = "full", link, linkLabel, hint }) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const count = skippedCount(station);
  const pending = skippedCommandPending(station);

  if (!station || (!count && !pending)) return null;

  async function send(action) {
    setBusy(true);
    setError("");
    try {
      const s = await api.skippedPages(station.id, action);
      setConfirming(false);
      if (s && typeof s === "object") onStationChange?.(s);
    } catch (err) {
      setConfirming(false);
      setError(err?.message || "That didn't work. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  const pendingText = (
    <p className="skipped-pending" role="status">
      <span className="spinner" aria-hidden="true" />
      {station.watcher_command === "discard_skipped" ? "Discarding the pages. " : station.watcher_command === "resend_skipped" ? "Adding the pages to this batch. " : ""}
      Working on it&hellip; the laptop will do this within 30 seconds.
    </p>
  );

  if (variant === "brief") {
    return (
      <div className="warning-box small skipped-card" role="status">
        {pending ? (
          pendingText
        ) : (
          <>
            <strong>{pagesWord(count)} scanned before a batch was started.</strong>{" "}
            They are held on the scanner laptop until you add them to a batch or discard them.{" "}
            {hint && <>{hint} </>}
            {link && <Link to={link}>{linkLabel || "Open the batch page"}</Link>}
          </>
        )}
      </div>
    );
  }

  return (
    <section className="warning-box skipped-card" aria-labelledby="skipped-title">
      <h2 id="skipped-title" className="skipped-title">
        <Icon name="alert" size={18} /> {pending ? "Pages scanned before this batch" : `${pagesWord(count)} scanned before this batch started.`}
      </h2>
      {pending ? (
        pendingText
      ) : (
        <>
          <p className="small">
            The laptop held them back so they don't end up in the wrong batch. If they belong to this stack, add them.
            If not, discard them.
          </p>
          <div className="btn-row">
            <button type="button" className="primary" disabled={busy} onClick={() => send("add_to_batch")}>
              {busy ? "Working..." : "Add to this batch"}
            </button>
            <button type="button" disabled={busy} onClick={() => setConfirming(true)}>
              Discard
            </button>
          </div>
        </>
      )}
      {error && <div className="error" role="alert">{error}</div>}

      {confirming && (
        <ConfirmDialog
          title={`Discard ${count} page${count === 1 ? "" : "s"}?`}
          confirmLabel="Discard pages"
          cancelLabel="Keep them"
          danger
          busy={busy}
          onCancel={() => setConfirming(false)}
          onConfirm={() => send("discard")}
        >
          <p>These pages won't be marked. They're kept on the laptop in a 'discarded' folder.</p>
        </ConfirmDialog>
      )}
    </section>
  );
}

// The six install steps. Wording matches the printed guide
// (backend/watcher/INSTALL_STEPS.md) - change both together.
// where="setup": shown on the Scanner setup page, next to the download button.
// where="help": the printed wording, used on the Help page.
export function installSteps(where = "setup") {
  return [
    where === "setup" ? (
      <>Click <strong>"Download installer"</strong> below.</>
    ) : (
      <>On the website, open <strong>"Scanner setup"</strong>. Choose your scanner station. Click <strong>"Download installer"</strong>.</>
    ),
    <>Open your <strong>Downloads</strong> folder. Right-click the <strong>ScanWatcher</strong> file. Click <strong>"Extract All"</strong>, then <strong>"Extract"</strong>.</>,
    <>
      In the new window, double-click the <strong>"ScanWatcher"</strong> folder, then double-click <strong>"INSTALL"</strong>.
      <ul className="step-notes">
        <li>If you see "Windows protected your PC": click <strong>"More info"</strong>, then <strong>"Run anyway"</strong>.</li>
        <li>If you see "Open File - Security Warning": click <strong>"Run"</strong>.</li>
        <li>If you see "Do you want to allow this app to make changes": click <strong>"Yes"</strong>.</li>
      </ul>
    </>,
    <>Wait. Do not type anything. When the window turns green and says <strong>"ALL DONE"</strong>, close the window.</>,
    <>In your scanner program, look for <strong>"Save to"</strong>, <strong>"Destination"</strong> or <strong>"Folder"</strong>. Choose <strong>C:\Scans</strong>.</>,
    where === "setup" ? (
      <>Come back here. The light should turn green (<strong>"Online"</strong>). Scan one page to test it.</>
    ) : (
      <>Look at the website. The light should be green (<strong>"Online"</strong>). Scan one page to test it.</>
    ),
  ];
}

// Big "Download installer" button + the six steps. Asks first if this would
// cut off a laptop that is already set up.
export function InstallSteps({ station, onDownloaded, where = "setup" }) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [downloaded, setDownloaded] = useState("");

  function start() {
    setError("");
    if (hasBeenOnline(station)) setConfirming(true);
    else download();
  }

  async function download() {
    setBusy(true);
    setError("");
    try {
      const name = await api.downloadInstaller(station.id);
      setDownloaded(name || "ScanWatcher.zip");
      setConfirming(false);
      onDownloaded?.();
    } catch (err) {
      setConfirming(false);
      setError(
        err?.status === 503 || err?.status === 404
          ? "The installer isn't ready on the server yet — ask IT."
          : err?.message || "The download didn't work. Please try again."
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="install-steps">
      <ol className="steps">
        {installSteps(where).map((text, i) => (
          <li key={i} className={i === 0 && downloaded ? "done" : ""}>
            <span className="step-num" aria-hidden="true">
              {i === 0 && downloaded ? <Icon name="check" size={16} /> : i + 1}
            </span>
            <div className="step-text">
              <span className="sr-only">Step {i + 1}: </span>
              {text}
              {i === 0 && where === "help" && (
                <div className="step-action">
                  <Link to="/scanner-setup" className="btn primary">
                    Open Scanner setup <Icon name="chevronRight" size={18} />
                  </Link>
                </div>
              )}
              {i === 0 && where === "setup" && (
                <div className="step-action">
                  <button
                    type="button"
                    className="primary big"
                    onClick={start}
                    disabled={busy || !station?.id}
                  >
                    <Icon name="download" />
                    {busy ? "Preparing download..." : downloaded ? "Download installer again" : "Download installer"}
                  </button>
                  {downloaded && (
                    <div className="success-box" role="status">
                      Downloaded <strong>{downloaded}</strong>. Now follow steps 2 to 6.
                    </div>
                  )}
                  {error && <div className="error" role="alert">{error}</div>}
                </div>
              )}
            </div>
          </li>
        ))}
      </ol>

      {confirming && (
        <ConfirmDialog
          title="Make a new installer?"
          confirmLabel="Continue"
          danger
          busy={busy}
          onCancel={() => setConfirming(false)}
          onConfirm={download}
        >
          <p>
            This makes a new connection code. The old laptop for this station will stop working until you
            install on it again. Continue?
          </p>
        </ConfirmDialog>
      )}
    </div>
  );
}
