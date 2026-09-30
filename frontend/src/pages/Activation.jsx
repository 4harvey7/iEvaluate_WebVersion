import React, { useState } from "react";
import { api } from "../api.js";
import { useSearch } from "../useSearch.js";
import { PageHeader } from "../components.jsx";

export default function Activation() {
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(null);
  const { results, loading, error: searchError } = useSearch(api.searchPlaceholders, selected ? "" : query);
  const [realEmail, setRealEmail] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function activate() {
    setError("");
    setMessage("");
    setBusy(true);
    try {
      const res = await api.activateInstructor(selected.id, realEmail.trim());
      setMessage(
        res.email_sent
          ? `Activated. A password-setup email was sent to ${res.email}.`
          : `Activated, but the password email could not be sent. Ask ${selected.full_name} to use "Forgot password" with ${res.email}.`
      );
      setSelected(null);
      setQuery("");
      setRealEmail("");
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <PageHeader
        title="Activate instructor logins"
        subtitle="Give an instructor who was added during scanning their own login. Check who they are in person first. Their batches and results stay with them."
        back={{ to: "/", label: "Home" }}
      />

      {selected ? (
        <div>
          <div className="selected-box">
            <div>
              Activating: <strong>{selected.full_name}</strong>
              {selected.department_name && <span className="muted"> - {selected.department_name}</span>}
            </div>
          </div>
          <label htmlFor="real-email">Instructor's real email</label>
          <input id="real-email" type="email" value={realEmail} onChange={(e) => setRealEmail(e.target.value)} maxLength={254} />
          <button className="primary" onClick={activate} disabled={busy || !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(realEmail.trim())}>
            {busy ? "Activating..." : "Activate & send password email"}
          </button>
          <button onClick={() => setSelected(null)} disabled={busy}>Cancel</button>
        </div>
      ) : (
        <>
          <label htmlFor="find-placeholder">Find the instructor</label>
          <input id="find-placeholder" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Start typing their name..." autoComplete="off" />
          {loading && <p className="muted small">Searching...</p>}
          {searchError && <div className="error">{searchError}</div>}
          {results.map((r) => (
            <button type="button" key={r.id} className="result-item" onClick={() => { setSelected(r); setMessage(""); setError(""); }}>
              {r.full_name}
              {r.department_name && <span className="muted"> - {r.department_name}</span>}
            </button>
          ))}
          {!loading && query.trim().length >= 2 && results.length === 0 && !searchError && (
            <p className="muted small">No instructors waiting for a login match that name.</p>
          )}
        </>
      )}

      {message && <div className="success-box" role="status">{message}</div>}
      {error && <div className="error" role="alert">{error}</div>}
    </div>
  );
}
