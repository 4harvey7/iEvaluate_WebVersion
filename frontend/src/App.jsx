import React, { useCallback, useEffect, useState } from "react";
import { BrowserRouter, Routes, Route, NavLink, Navigate, useLocation, useNavigate } from "react-router-dom";
import Login from "./pages/Login.jsx";
import Home from "./pages/Home.jsx";
import BatchSetup from "./pages/BatchSetup.jsx";
import ScanningLive from "./pages/ScanningLive.jsx";
import Review from "./pages/Review.jsx";
import ReviewBatches from "./pages/ReviewBatches.jsx";
import Activation from "./pages/Activation.jsx";
import WatcherSetup from "./pages/WatcherSetup.jsx";
import Help from "./pages/Help.jsx";
import SastGuide, { GuideModal } from "./pages/SastGuide.jsx";
import { api, loadSession, onUnauthorized } from "./api.js";
import { ConfirmDialog, KeyReveal, Loading, PageHeader } from "./components.jsx";
import { Icon } from "./icons.jsx";
import { ScannerStatus, useStation } from "./scanner.jsx";

const UUID_REGEX = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const STATION_STORAGE = "station_id";

function readStationId() {
  try {
    const saved = localStorage.getItem(STATION_STORAGE) || "";
    if (UUID_REGEX.test(saved)) return saved;
    localStorage.removeItem(STATION_STORAGE);
  } catch {
    /* storage blocked */
  }
  return "";
}

function writeStationId(id) {
  try {
    if (id && UUID_REGEX.test(id)) localStorage.setItem(STATION_STORAGE, id);
    else localStorage.removeItem(STATION_STORAGE);
  } catch {
    /* ignore */
  }
}

const GUIDE_ROLES = ["SAO_STAFF", "SAO_ADMIN"];
const guideKey = (userId) => `sastGuideAck:${userId}`;

// Shows the SAST guide once per semester (current term id vs the last one acknowledged).
function useGuideGate(profile) {
  const [show, setShow] = useState(false);
  const [termId, setTermId] = useState(null);
  const allowed = !!profile && (GUIDE_ROLES.includes(profile.role) || !!profile.is_admin);
  useEffect(() => {
    if (!allowed) return undefined;
    let cancelled = false;
    api
      .config()
      .then((c) => {
        if (cancelled || !c?.current_term_id) return;
        setTermId(c.current_term_id);
        let acked = null;
        try {
          acked = localStorage.getItem(guideKey(profile.id));
        } catch {
          /* storage blocked */
        }
        if (acked !== String(c.current_term_id)) setShow(true);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [allowed, profile?.id]);
  const ack = useCallback(() => {
    try {
      if (termId) localStorage.setItem(guideKey(profile.id), String(termId));
    } catch {
      /* ignore */
    }
    setShow(false);
  }, [termId, profile?.id]);
  return { show: show && allowed, ack };
}

function Brand() {
  return (
    <div className="brand">
      <span className="brand-mark" aria-hidden="true"><Icon name="scan" size={18} /></span>
      <span className="brand-text">
        <strong>iEvaluate</strong>
        <span>Batch scanning</span>
      </span>
    </div>
  );
}

// Centered layout for the screens before the main app (log in, pick laptop).
function AuthLayout({ children }) {
  return (
    <main className="auth-page" id="main">
      <Brand />
      <div className="card auth-card">{children}</div>
    </main>
  );
}

// Each laptop is one station. Picked or registered after login, remembered per machine.
function ChooseStation({ profile, onDone, onLogout, onStationKey }) {
  const navigate = useNavigate();
  const [stations, setStations] = useState([]);
  const [label, setLabel] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [revealed, setRevealed] = useState(null); // {station, key}

  useEffect(() => {
    api.listStations().then(setStations).catch((e) => setError(e.message)).finally(() => setLoading(false));
  }, []);

  async function bindAndFinish(stationId) {
    setError("");
    setBusy(true);
    try {
      await api.bindStation(stationId);
      onDone(stationId);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function registerNew(e) {
    e?.preventDefault();
    if (!label.trim() || busy) return;
    setError("");
    setBusy(true);
    try {
      const station = await api.registerStation(label.trim());
      onStationKey({ stationId: station.id, key: station.station_key });
      await api.bindStation(station.id);
      setRevealed({ station, key: station.station_key });
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  if (revealed) {
    return (
      <div>
        <PageHeader title="Laptop added" subtitle={`This computer is now "${revealed.station.label}".`} />
        <p>Next, install the scan program on the laptop connected to the scanner. It takes about 5 minutes.</p>
        <div className="btn-row">
          <button
            type="button"
            className="primary big"
            onClick={() => {
              navigate("/scanner-setup");
              onDone(revealed.station.id);
            }}
          >
            Next: Scanner setup <Icon name="chevronRight" size={18} />
          </button>
          <button type="button" onClick={() => { navigate("/"); onDone(revealed.station.id); }}>Go to Home</button>
        </div>
        <details className="advanced">
          <summary>Advanced (for IT): connection code</summary>
          <div className="advanced-body">
            <KeyReveal station={revealed.station} stationKey={revealed.key} />
          </div>
        </details>
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        title="Which laptop is this?"
        subtitle={`Hi ${profile.first_name}. Tell us which laptop you're on. This only needs to happen once per computer.`}
        actions={<button type="button" className="small-btn" onClick={onLogout}>Log out</button>}
      />

      {loading && <Loading>Loading the list of laptops...</Loading>}
      {!loading && stations.length > 0 && (
        <section className="section">
          <h2>Pick this laptop from the list</h2>
          {stations.map((s) => (
            <button
              type="button"
              key={s.id}
              className="result-item station-item"
              disabled={busy}
              onClick={() => bindAndFinish(s.id)}
            >
              <span className="station-name">{s.label}</span>
              <span className="station-meta">
                <ScannerStatus station={s} />
                {s.current_staff_name && <span className="muted">Last used by {s.current_staff_name}</span>}
                {s.open_batch_id && <span className="badge">a batch is open</span>}
              </span>
            </button>
          ))}
          <p className="small">
            Not sure which one this is? Check the sticker on the laptop or ask your SAO admin rather than guessing.
          </p>
        </section>
      )}

      <section className="section">
        <h2>{stations.length > 0 ? "Or add this laptop as a new one" : "Add this laptop"}</h2>
        <form onSubmit={registerNew}>
          <label htmlFor="new-laptop">Give this laptop a name (for example "Laptop 1")</label>
          <input id="new-laptop" value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Laptop 1" maxLength={60} />
          {error && <div className="error" role="alert">{error}</div>}
          <button className="primary" type="submit" disabled={!label.trim() || busy}>
            {busy ? "Working..." : "Add this laptop"}
          </button>
        </form>
      </section>
    </div>
  );
}

// Sidebar on desktop, slide-out menu on phones.
function Shell({ profile, stationId, busy, onRequest, onStationMissing, children }) {
  const location = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);
  const [openBatchId, setOpenBatchId] = useState(null);
  // 15 s: the sidebar light shows "Offline" soon after the scan program stops.
  const { station, refresh } = useStation(stationId, { intervalMs: 15000, onMissing: onStationMissing });

  // On every page change: close the phone menu, scroll up, refresh the batch + status.
  useEffect(() => {
    setMenuOpen(false);
    window.scrollTo(0, 0);
    let cancelled = false;
    api
      .getOpenBatch(stationId)
      .then((b) => !cancelled && setOpenBatchId(b?.id || null))
      .catch(() => {});
    refresh();
    return () => {
      cancelled = true;
    };
  }, [location.pathname, stationId, refresh]);

  useEffect(() => {
    if (!menuOpen) return undefined;
    const onKey = (e) => e.key === "Escape" && setMenuOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [menuOpen]);

  const onBatchPage = location.pathname.startsWith("/batch/");
  // The review page (/batch/:id/review) has its own menu entry and a wider layout.
  const onReviewPage = /^\/batch\/[^/]+\/review\/?$/.test(location.pathname);
  const onReviewList = /^\/review\/?$/.test(location.pathname);

  return (
    <div className={`app-shell ${menuOpen ? "menu-open" : ""}`}>
      <a href="#main" className="skip-link">Skip to main content</a>

      <header className="topbar">
        <button
          type="button"
          className="icon-btn"
          aria-label={menuOpen ? "Close menu" : "Open menu"}
          aria-expanded={menuOpen}
          aria-controls="sidebar"
          onClick={() => setMenuOpen((o) => !o)}
        >
          <Icon name={menuOpen ? "close" : "menu"} size={24} />
        </button>
        <Brand />
        <ScannerStatus station={station} className="dot-only" />
      </header>

      <aside className="sidebar" id="sidebar" aria-label="Main menu">
        <div className="sidebar-top">
          <Brand />
          <button type="button" className="icon-btn sidebar-close" aria-label="Close menu" onClick={() => setMenuOpen(false)}>
            <Icon name="close" size={22} />
          </button>
        </div>

        <nav className="side-nav">
          <NavLink to="/" end>
            <Icon name="home" /> Home
          </NavLink>

          <div className="nav-group">Scan test sheets</div>
          <NavLink to="/scanner-setup">
            <span className="nav-step">1</span> Scanner setup
          </NavLink>
          <NavLink to="/start">
            <span className="nav-step">2</span> Start a batch
          </NavLink>
          {openBatchId ? (
            <>
              <NavLink
                to={`/batch/${openBatchId}`}
                end
                className={({ isActive }) => (isActive || (onBatchPage && !onReviewPage) ? "active" : "")}
              >
                <span className="nav-step">3</span> Scan &amp; check
                <span className="nav-live">open</span>
              </NavLink>
            </>
          ) : (
            <>
              <span className={`nav-disabled ${onBatchPage && !onReviewPage ? "active" : ""}`} aria-disabled="true">
                <span className="nav-step">3</span>
                <span>
                  Scan &amp; check
                  <small>Start a batch first</small>
                </span>
              </span>
            </>
          )}
          {/* Always open: every batch this person may review, open or closed. */}
          <NavLink to="/review" className={({ isActive }) => (isActive || onReviewPage ? "active" : "")}>
            <span className="nav-step">4</span> Review forms
          </NavLink>

          {profile.is_admin && (
            <>
              <div className="nav-group">Admin</div>
              <NavLink to="/activate">
                <Icon name="userCheck" /> Activate instructor logins
              </NavLink>
            </>
          )}

          <div className="nav-group">Support</div>
          <NavLink to="/sast-guidelines">
            <Icon name="help" /> SAST Guidelines
          </NavLink>
          <NavLink to="/help">
            <Icon name="help" /> Help
          </NavLink>
        </nav>

        <div className="sidebar-foot">
          <div className="who">
            <strong>{profile.first_name} {profile.last_name}</strong>
            {profile.is_admin ? <span className="muted"> (admin)</span> : ""}
          </div>
          <div className="this-laptop">
            <span className="muted">This laptop: </span>
            <strong>{station?.label || "..."}</strong>
            <ScannerStatus station={station} />
          </div>
          <button type="button" className="nav-btn" onClick={() => onRequest("switch")}>
            <Icon name="swap" size={18} /> Change laptop
          </button>
          <button type="button" className="nav-btn logout-btn" onClick={() => onRequest("logout")} disabled={busy}>
            <Icon name="logout" size={18} /> Log out
          </button>
        </div>
      </aside>

      {menuOpen && <div className="sidebar-backdrop" onClick={() => setMenuOpen(false)} aria-hidden="true" />}

      <main className="main" id="main" tabIndex={-1}>
        <div className={`card ${onReviewPage || onReviewList ? "card-wide" : ""}`}>{children}</div>
      </main>
    </div>
  );
}

export default function App() {
  const [session, setSession] = useState(() => loadSession());
  const [stationId, setStationIdState] = useState(readStationId);
  // Plain connection code, kept in memory only (never in storage), right after it's made.
  const [stationKey, setStationKey] = useState(null);
  const [pendingAction, setPendingAction] = useState(null); // {type, openBatch}
  const [busy, setBusy] = useState(false);
  // Set when this laptop is still taken by someone with an open batch (409 station_in_use).
  const [bindProblem, setBindProblem] = useState("");

  const profile = session?.profile;

  const setStationId = useCallback((id) => {
    writeStationId(id);
    setStationIdState(id || "");
  }, []);

  useEffect(() => {
    onUnauthorized(() => setSession(null));
    return () => onUnauthorized(null);
  }, []);

  // Re-bind the logged-in user to this laptop's station on each login.
  useEffect(() => {
    if (session && stationId) {
      setBindProblem("");
      api.bindStation(stationId).catch((err) => {
        if (err?.status === 404) setStationId("");
        else if (err?.status === 409) setBindProblem(err.message || "Another staff member is still using this laptop.");
      });
    }
  }, [session?.profile?.id, stationId, setStationId]); // eslint-disable-line react-hooks/exhaustive-deps

  const doLogout = useCallback(async () => {
    setBusy(true);
    await api.logout(stationId);
    setBusy(false);
    setPendingAction(null);
    setStationKey(null);
    setSession(null);
  }, [stationId]);

  const handleStationMissing = useCallback(() => setStationId(""), [setStationId]);

  const doSwitchStation = useCallback(() => {
    setPendingAction(null);
    setStationKey(null);
    setStationId("");
  }, [setStationId]);

  // Warn before logging out / switching station while a batch is open.
  async function requestAction(type) {
    let openBatch = null;
    if (stationId) {
      try {
        openBatch = await api.getOpenBatch(stationId);
      } catch {
        openBatch = null;
      }
    }
    if (!openBatch && type === "logout") return doLogout();
    setPendingAction({ type, openBatch });
  }

  if (!session) {
    return (
      <BrowserRouter>
        <AuthLayout>
          <Login onLogin={(s) => setSession(s)} />
        </AuthLayout>
      </BrowserRouter>
    );
  }

  if (!stationId) {
    return (
      <BrowserRouter>
        <AuthLayout>
          <ChooseStation profile={profile} onDone={setStationId} onLogout={doLogout} onStationKey={setStationKey} />
        </AuthLayout>
      </BrowserRouter>
    );
  }

  const guide = useGuideGate(profile);
  const keyForStation = stationKey?.stationId === stationId ? stationKey.key : null;

  return (
    <BrowserRouter>
      <Shell profile={profile} stationId={stationId} busy={busy} onRequest={requestAction} onStationMissing={handleStationMissing}>
        {bindProblem && (
          <div className="warning-box small" role="alert">
            <p>{bindProblem}</p>
          </div>
        )}
        <Routes>
          <Route
            path="/"
            element={
              <Home
                stationId={stationId}
                profile={profile}
                onSwitchStation={() => requestAction("switch")}
                onStationMissing={handleStationMissing}
              />
            }
          />
          <Route path="/start" element={<BatchSetup stationId={stationId} onStationMissing={handleStationMissing} />} />
          <Route
            path="/scanner-setup"
            element={
              <WatcherSetup
                stationId={stationId}
                stationKey={keyForStation}
                onStationKey={(key) => setStationKey({ stationId, key })}
                onStationMissing={handleStationMissing}
              />
            }
          />
          {/* Old address, kept so bookmarks still work. */}
          <Route path="/watcher-setup" element={<Navigate to="/scanner-setup" replace />} />
          <Route path="/batch/:batchId" element={<ScanningLive stationId={stationId} />} />
          <Route path="/batch/:batchId/review" element={<Review profile={profile} />} />
          <Route path="/review" element={<ReviewBatches profile={profile} stationId={stationId} />} />
          <Route
            path="/activate"
            element={
              profile.is_admin ? (
                <Activation />
              ) : (
                <PageHeader
                  title="Admins only"
                  subtitle="Only an SAO admin can activate instructor logins."
                  back={{ to: "/", label: "Home" }}
                />
              )
            }
          />
          <Route path="/sast-guidelines" element={<SastGuide />} />
          <Route path="/help" element={<Help stationId={stationId} onStationMissing={handleStationMissing} />} />
          <Route path="*" element={<Navigate to="/" />} />
        </Routes>
      </Shell>

      {guide.show && <GuideModal onAck={guide.ack} />}

      {pendingAction && (
        <ConfirmDialog
          title={pendingAction.type === "logout" ? "Log out while a batch is open?" : "Change which laptop this is?"}
          confirmLabel={pendingAction.type === "logout" ? "Log out" : "Change laptop"}
          danger={!!pendingAction.openBatch}
          busy={busy}
          onCancel={() => setPendingAction(null)}
          onConfirm={pendingAction.type === "logout" ? doLogout : doSwitchStation}
        >
          {pendingAction.openBatch ? (
            <p>
              A batch is still <strong>open</strong> on this laptop. It stays open and new scans keep going into it.
              Finish and close it first, unless someone else will carry on with it.
            </p>
          ) : (
            <p>
              You'll pick a different laptop from the list, or add this one as new. Only do this if the wrong laptop
              was chosen. The scanner laptop must be set up for the one you pick.
            </p>
          )}
        </ConfirmDialog>
      )}
    </BrowserRouter>
  );
}
