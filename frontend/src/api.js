export const API_BASE = (import.meta.env.VITE_API_BASE || "http://localhost:8000").replace(/\/+$/, "");

// Station laptops are shared: tokens live in sessionStorage so closing the
// browser logs the user out.
const SESSION_KEY = "ievaluate_session";

export class ApiError extends Error {
  constructor(message, { status = 0, code = null, detail = null } = {}) {
    super(message);
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
}

function safeParse(raw) {
  try {
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export function loadSession() {
  let s = null;
  try {
    s = safeParse(sessionStorage.getItem(SESSION_KEY));
  } catch {
    s = null;
  }
  if (!s || typeof s !== "object" || typeof s.access_token !== "string" || !s.profile || typeof s.profile !== "object") {
    clearSession();
    return null;
  }
  return s;
}

function saveSession(s) {
  try {
    sessionStorage.setItem(SESSION_KEY, JSON.stringify(s));
  } catch {
    /* storage unavailable: session lives in memory only */
  }
  memorySession = s;
}

export function clearSession() {
  memorySession = null;
  try {
    sessionStorage.removeItem(SESSION_KEY);
  } catch {
    /* ignore */
  }
}

let memorySession = null;
function currentSession() {
  return memorySession || loadSession();
}

// App registers a callback so any 401 anywhere logs the user out.
let unauthorizedHandler = null;
export function onUnauthorized(fn) {
  unauthorizedHandler = fn;
}

// Plain wording for errors that come back without a readable message.
function friendlyStatus(status) {
  if (status === 401) return "Your login has expired. Please log in again.";
  if (status === 403) return "Your account isn't allowed to do that. Ask an SAO admin for help.";
  if (status === 404) return "We couldn't find that. It may have been removed.";
  if (status === 413) return "That file is too big.";
  if (status === 429) return "Too many tries. Please wait a moment and try again.";
  if (status >= 500) return "Something went wrong on the server. Please try again in a minute.";
  return "Something went wrong. Please try again.";
}

function messageFrom(data, res) {
  const d = data?.detail;
  if (typeof d === "string") {
    if (res.status >= 500 && /internal server error|traceback/i.test(d)) return friendlyStatus(res.status);
    return d;
  }
  if (d && typeof d === "object" && !Array.isArray(d)) return d.message || friendlyStatus(res.status);
  if (Array.isArray(d)) {
    // FastAPI validation errors
    const parts = d.slice(0, 3).map((e) => e?.msg).filter(Boolean);
    return `Please check what you typed${parts.length ? ` (${parts.join("; ")})` : ""}.`;
  }
  return friendlyStatus(res.status);
}

async function rawFetch(path, { method = "GET", body, token, signal, raw = false } = {}) {
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (token) headers["Authorization"] = `Bearer ${token}`;
  let res;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal,
    });
  } catch (err) {
    if (err?.name === "AbortError") throw err;
    throw new ApiError("Can't reach the server. Check the internet connection and try again.", { status: 0, code: "network" });
  }
  if (raw && res.ok) return res;
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const d = data?.detail;
    throw new ApiError(messageFrom(data, res), {
      status: res.status,
      code: d && typeof d === "object" && !Array.isArray(d) ? d.code || null : null,
      detail: d ?? null,
    });
  }
  return data;
}

// Single-flight refresh so parallel requests share one refresh call.
let refreshing = null;
async function refreshSession() {
  const s = currentSession();
  if (!s?.refresh_token) throw new ApiError("Session expired", { status: 401 });
  if (!refreshing) {
    refreshing = rawFetch("/auth/refresh", { method: "POST", body: { refresh_token: s.refresh_token } })
      .then((data) => {
        const next = { ...data };
        saveSession(next);
        return next;
      })
      .finally(() => {
        refreshing = null;
      });
  }
  return refreshing;
}

function expiresSoon(s) {
  if (!s?.expires_at) return false;
  return s.expires_at * 1000 - Date.now() < 60_000;
}

function handleUnauthorized() {
  clearSession();
  unauthorizedHandler?.();
}

async function request(path, opts = {}) {
  let s = currentSession();
  if (!s) {
    handleUnauthorized();
    throw new ApiError("Please log in.", { status: 401, code: "not_authenticated" });
  }
  if (expiresSoon(s)) {
    try {
      s = await refreshSession();
    } catch (err) {
      if (err?.status === 401) {
        handleUnauthorized();
        throw new ApiError("Session expired. Please log in again.", { status: 401 });
      }
      // network blip: try with the current token anyway
    }
  }
  try {
    return await rawFetch(path, { ...opts, token: s.access_token });
  } catch (err) {
    if (err?.status !== 401) throw err;
    try {
      s = await refreshSession();
    } catch {
      handleUnauthorized();
      throw new ApiError("Session expired. Please log in again.", { status: 401 });
    }
    try {
      return await rawFetch(path, { ...opts, token: s.access_token });
    } catch (err2) {
      if (err2?.status === 401) handleUnauthorized();
      throw err2;
    }
  }
}

function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function downloadJson(obj, filename) {
  saveBlob(new Blob([JSON.stringify(obj, null, 2) + "\n"], { type: "application/json" }), filename);
}

// Reads the file name from a Content-Disposition header (only visible
// cross-origin if the server exposes that header); falls back otherwise.
export function filenameFromDisposition(header, fallback) {
  if (!header) return fallback;
  let name = null;
  const star = /filename\*\s*=\s*(?:utf-8)?''([^;]+)/i.exec(header);
  if (star) {
    try {
      name = decodeURIComponent(star[1].trim().replace(/^"|"$/g, ""));
    } catch {
      name = null;
    }
  }
  if (!name) {
    const plain = /filename\s*=\s*(?:"([^"]*)"|([^;]+))/i.exec(header);
    if (plain) name = (plain[1] ?? plain[2] ?? "").trim();
  }
  // Keep only a plain file name, never a path.
  name = (name || "").split(/[\\/]/).pop().replace(/[<>:"|?*]/g, "").trim();
  return name || fallback;
}

const q = encodeURIComponent;

export const api = {
  login: async (email, password) => {
    const data = await rawFetch("/auth/login", { method: "POST", body: { email, password } });
    saveSession(data);
    return data;
  },
  logout: async (station_id) => {
    try {
      const s = currentSession();
      if (s) await rawFetch("/auth/logout", { method: "POST", body: { station_id: station_id || null }, token: s.access_token });
    } catch {
      /* logging out locally regardless */
    }
    clearSession();
  },
  me: () => request("/auth/me"),
  config: () => request("/config"),

  listStations: () => request("/stations"),
  registerStation: (label) => request("/stations", { method: "POST", body: { label } }),
  regenerateStationKey: (id) => request(`/stations/${q(id)}/regenerate-key`, { method: "POST" }),
  bindStation: (id) => request(`/stations/${q(id)}/bind`, { method: "POST" }),
  getStation: (id) => request(`/stations/${q(id)}`),
  setFolder: (id, folder_path) => request(`/stations/${q(id)}/folder`, { method: "PUT", body: { folder_path } }),
  // Pages the laptop held back because they were scanned before the batch
  // started. action: "add_to_batch" | "discard". Returns the station.
  skippedPages: (id, action) => request(`/stations/${q(id)}/skipped-pages`, { method: "POST", body: { action } }),

  searchInstructors: (term, signal) => request(`/instructors/search?q=${q(term)}`, { signal }),
  searchPlaceholders: (term, signal) => request(`/instructors/placeholders?q=${q(term)}`, { signal }),
  createInstructor: (body) => request("/instructors", { method: "POST", body }),
  activateInstructor: (id, real_email) => request(`/instructors/${q(id)}/activate`, { method: "POST", body: { real_email } }),

  searchSubjects: (term, signal) => request(`/subjects/search?q=${q(term)}`, { signal }),
  createSubject: (body) => request("/subjects", { method: "POST", body }),

  listDepartments: () => request("/departments"),

  startBatch: (body) => request("/batches/start", { method: "POST", body }),
  getOpenBatch: (stationId) => request(`/batches/station/${q(stationId)}/open`),
  getBatch: (id) => request(`/batches/${q(id)}`),
  closeBatch: (id, force = false) => request(`/batches/${q(id)}/close`, { method: "POST", body: { force } }),
  forceCloseBatch: (id) => request(`/batches/${q(id)}/force-close`, { method: "POST" }),

  // Review page. getReviews returns only rows changed since `since` (the
  // previous answer's cursor) unless the answer says full: true.
  getReviews: (batchId, since, signal) =>
    request(`/reviews/batch/${q(batchId)}${since ? `?since=${q(since)}` : ""}`, { signal }),
  claimReview: (id, release = false) => request(`/reviews/queue/${q(id)}/claim`, { method: "POST", body: { release } }),
  approveReview: (id, body) => request(`/reviews/queue/${q(id)}/approve`, { method: "POST", body }),
  rejectReview: (id, body) => request(`/reviews/queue/${q(id)}/reject`, { method: "POST", body }),
  resolveImportError: (id, body) => request(`/reviews/import-errors/${q(id)}/resolve`, { method: "POST", body }),
  discardImportError: (id, body) => request(`/reviews/import-errors/${q(id)}/discard`, { method: "POST", body }),
  // The scanned page as a Blob (the image needs the login token, so it can't be a plain <img src>).
  reviewImage: async (kind, id) => {
    const path = kind === "import" ? `/reviews/import-errors/${q(id)}/image` : `/reviews/queue/${q(id)}/image`;
    const res = await request(path, { raw: true });
    return res.blob();
  },

  // Ready-to-run installer for this station. The server puts a fresh
  // connection code inside, so the old one stops working.
  downloadInstaller: async (id) => {
    const res = await request(`/stations/${q(id)}/installer`, { method: "POST", raw: true });
    const filename = filenameFromDisposition(res.headers.get("Content-Disposition"), "ScanWatcher.zip");
    saveBlob(await res.blob(), filename);
    return filename;
  },

  downloadWatcherZip: async () => {
    const res = await request("/watcher/download", { raw: true });
    saveBlob(await res.blob(), "ievaluate-scan-watcher.zip");
  },
};
