import React, { useEffect, useRef, useState } from "react";
import { API_BASE, api } from "../api.js";
import { ConfirmDialog, KeyReveal, Loading, PageHeader, downloadWatcherConfig } from "../components.jsx";
import { Icon } from "../icons.jsx";
import {
  DEFAULT_FOLDER,
  InstallSteps,
  ScannerStatus,
  hasBeenOnline,
  sameFolder,
  toWindowsPath,
  useStation,
} from "../scanner.jsx";

// "Scanner laptop" page: status light, one-click installer, scan folder, and
// the old manual setup tucked away under "Advanced (for IT)".
export default function WatcherSetup({ stationId, stationKey, onStationKey, onStationMissing }) {
  const { station, setStation, error: loadError, loading, refresh } = useStation(stationId, { onMissing: onStationMissing });

  const [folder, setFolder] = useState(DEFAULT_FOLDER);
  const folderTouched = useRef(false);
  const [savingFolder, setSavingFolder] = useState(false);
  const [folderError, setFolderError] = useState("");
  const [folderSaved, setFolderSaved] = useState(false);

  // Advanced (for IT)
  const [apiBase, setApiBase] = useState(API_BASE);
  const [advError, setAdvError] = useState("");
  const [advInfo, setAdvInfo] = useState("");
  const [advBusy, setAdvBusy] = useState(false);
  const [confirmRegen, setConfirmRegen] = useState(false);

  // Fill the folder box from the server until the user starts typing.
  useEffect(() => {
    if (station && !folderTouched.current) setFolder(toWindowsPath(station.folder_path) || DEFAULT_FOLDER);
  }, [station]);

  const savedFolder = station?.folder_path || "";
  const folderDirty = !savedFolder || !sameFolder(folder, savedFolder);
  const apiLooksLocal = /localhost|127\.0\.0\.1/.test(apiBase);

  async function saveFolder(e) {
    e.preventDefault();
    const value = folder.trim();
    setFolderSaved(false);
    if (!value) {
      setFolderError("Please type a folder, for example C:\\Scans.");
      return;
    }
    setFolderError("");
    setSavingFolder(true);
    try {
      const updated = await api.setFolder(stationId, value);
      if (updated) setStation(updated);
      folderTouched.current = false;
      setFolder(toWindowsPath(updated?.folder_path) || value);
      setFolderSaved(true);
    } catch (err) {
      setFolderError(err?.message || "The folder couldn't be saved. Please try again.");
    } finally {
      setSavingFolder(false);
    }
  }

  async function downloadZip() {
    setAdvError("");
    setAdvInfo("");
    setAdvBusy(true);
    try {
      await api.downloadWatcherZip();
      setAdvInfo("Program files downloaded.");
    } catch (err) {
      setAdvError(err.message);
    } finally {
      setAdvBusy(false);
    }
  }

  function configFolder() {
    return savedFolder || DEFAULT_FOLDER;
  }

  function downloadConfig() {
    setAdvError("");
    setAdvInfo("");
    if (!stationKey) {
      setConfirmRegen(true);
      return;
    }
    downloadWatcherConfig({ apiBase, stationKey, folder: configFolder() });
    setAdvInfo("watcher_config.json downloaded. Put it next to folder_watcher.py.");
  }

  async function regenerate() {
    setAdvBusy(true);
    setAdvError("");
    setAdvInfo("");
    try {
      const res = await api.regenerateStationKey(stationId);
      onStationKey(res.station_key);
      setStation(res);
      downloadWatcherConfig({ apiBase, stationKey: res.station_key, folder: configFolder() });
      setAdvInfo("New connection code made and watcher_config.json downloaded. Replace the old settings file on the scanner laptop and restart the program.");
    } catch (err) {
      setAdvError(err.message);
    } finally {
      setConfirmRegen(false);
      setAdvBusy(false);
    }
  }

  const installSection = (
    <section className="section" aria-labelledby="install-h">
      <h2 id="install-h">{hasBeenOnline(station) ? "Install again (only if needed)" : "Install it (once per laptop)"}</h2>
      {hasBeenOnline(station) && (
        <p>This laptop has been set up before. You only need to install again on a new or reset laptop.</p>
      )}
      {station ? (
        <InstallSteps
          station={station}
          onDownloaded={() => {
            // The installer made a new connection code; any code shown earlier is dead now.
            onStationKey(null);
            refresh();
          }}
        />
      ) : (
        !loading && <p>Can't load this station yet. Please refresh the page.</p>
      )}
    </section>
  );

  const folderSection = (
    <section className="section" aria-labelledby="folder-h">
      <h2 id="folder-h">Scan folder</h2>
      <p>This is the folder on the scanner laptop where new scans are saved.</p>
      <form onSubmit={saveFolder} className="folder-form">
        <label htmlFor="scan-folder">Folder on the scanner laptop</label>
        <div className="input-with-button">
          <input
            id="scan-folder"
            value={folder}
            onChange={(e) => {
              folderTouched.current = true;
              setFolder(e.target.value);
              setFolderSaved(false);
              setFolderError("");
            }}
            placeholder={DEFAULT_FOLDER}
            maxLength={500}
            spellCheck={false}
            autoComplete="off"
            aria-describedby="folder-tip"
            aria-invalid={folderError ? "true" : undefined}
          />
          <button type="submit" className="primary" disabled={savingFolder || !station || !folderDirty}>
            <Icon name="folder" size={18} />
            {savingFolder ? "Saving..." : "Save folder"}
          </button>
        </div>
      </form>
      <p id="folder-tip" className="tip">
        <strong>Tip:</strong> Set your scanner to save into this same folder.
      </p>
      {folderError && <div className="error" role="alert">{folderError}</div>}
      {folderSaved && (
        <div className="success-box" role="status">
          Saved. The scanner laptop will switch to this folder by itself within 30 seconds. Nothing to do on the laptop.
        </div>
      )}
    </section>
  );

  return (
    <div>
      <PageHeader
        title="Step 1: Scanner setup"
        subtitle="Install the scan program on the laptop connected to the scanner, and choose the folder it watches. You only do this once per laptop."
        back={{ to: "/", label: "Home" }}
      />

      <section className="section" aria-labelledby="status-h">
        <h2 id="status-h">Is it working?</h2>
        {loading && !station ? (
          <Loading>Checking the scanner laptop...</Loading>
        ) : (
          <ScannerStatus
            station={station}
            variant="card"
            hint={`${station?.label ? `Station: ${station.label}. ` : ""}This updates by itself every 10 seconds.`}
          />
        )}
        {loadError && <div className="error" role="alert">{loadError}</div>}
      </section>

      {/* Once installed, the folder setting matters more than reinstalling. */}
      {hasBeenOnline(station) ? (
        <>
          {folderSection}
          {installSection}
        </>
      ) : (
        <>
          {installSection}
          {folderSection}
        </>
      )}

      <details className="advanced">
        <summary>Advanced (for IT)</summary>
        <div className="advanced-body">
          <p>
            Manual setup, for IT staff only. Most people should use <strong>Download installer</strong> above instead.
          </p>

          {stationKey && station && (
            <KeyReveal station={station} stationKey={stationKey} folder={configFolder()} apiBase={apiBase} />
          )}

          <label htmlFor="api-base">Server address (goes into the settings file)</label>
          <input id="api-base" value={apiBase} onChange={(e) => setApiBase(e.target.value)} placeholder="https://api.yourdomain.com" spellCheck={false} />
          {apiLooksLocal && (
            <p className="small muted">This points at this computer (localhost). Only correct if the server runs on the same laptop.</p>
          )}
          <p className="small muted">
            The settings file uses the saved scan folder: <code>{toWindowsPath(configFolder())}</code>
          </p>

          <div className="btn-row">
            <button onClick={downloadZip} disabled={advBusy}>
              <Icon name="download" size={16} /> Program files (.zip)
            </button>
            <button onClick={downloadConfig} disabled={advBusy || !apiBase.trim() || !station}>
              <Icon name="download" size={16} />
              {stationKey ? "Settings file (watcher_config.json)" : "Settings file (makes a new connection code)"}
            </button>
            <button onClick={() => setConfirmRegen(true)} disabled={advBusy || !station}>Make a new connection code</button>
          </div>
          {!stationKey && (
            <p className="small muted">
              For safety, a connection code is only shown when it is made. Downloading the settings file makes a new
              code, and the old one stops working straight away.
            </p>
          )}
          {advInfo && <div className="success-box small" role="status">{advInfo}</div>}
          {advError && <div className="error" role="alert">{advError}</div>}

          <h3>Manual install steps</h3>
          <ol>
            <li>Install Python 3.10+ (<a href="https://www.python.org/downloads/" target="_blank" rel="noreferrer">python.org/downloads</a>), keeping the "py launcher" option on.</li>
            <li>Unzip the program files somewhere permanent, e.g. <code>C:\ievaluate-watcher\</code>.</li>
            <li>Put <code>watcher_config.json</code> in that same folder.</li>
            <li>Open a terminal in that folder and run:<pre>py -3 -m pip install -r requirements.txt</pre></li>
            <li>Double-click <code>start_watcher.bat</code> to test. Within about 30 seconds the light on this page should turn green.</li>
            <li>Double-click <code>install_task.bat</code> so it starts automatically at every log-on (and restarts if it stops).</li>
          </ol>
          <div className="warning-box small">
            Sent files move to <code>processed\&lt;date&gt;\</code> inside the scan folder. Files saved before the batch
            started (or that can't be read) move to <code>skipped\</code>. If scans stop arriving, check{" "}
            <code>watcher.log</code> next to the program.
          </div>
        </div>
      </details>

      {confirmRegen && (
        <ConfirmDialog
          title="Make a new connection code?"
          confirmLabel="Make code & download settings file"
          danger
          busy={advBusy}
          onCancel={() => setConfirmRegen(false)}
          onConfirm={regenerate}
        >
          <p>
            The current code stops working straight away. The scanner laptop needs the new
            <code> watcher_config.json</code> (downloaded automatically) and a restart of the program.
          </p>
        </ConfirmDialog>
      )}
    </div>
  );
}
