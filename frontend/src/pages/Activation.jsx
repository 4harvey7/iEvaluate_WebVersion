import React, { useState } from "react";
import { api } from "../api.js";
import { useSearch } from "../useSearch.js";
import { ConfirmDialog, PageHeader } from "../components.jsx";

const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const sameEmail = (a, b) => a.trim().toLowerCase() === b.trim().toLowerCase();

export default function Activation() {
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(null);
  const { results, loading, error: searchError } = useSearch(api.searchPlaceholders, selected ? "" : query);
  const [realEmail, setRealEmail] = useState("");
  const [confirmEmail, setConfirmEmail] = useState("");
  const [confirming, setConfirming] = useState(false);
  // { email, emailSent, name } after a successful activation.
  const [done, setDone] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const emailValid = EMAIL_RE.test(realEmail.trim());
  const emailsMatch = emailValid && sameEmail(realEmail, confirmEmail);
  const showMismatch = confirmEmail.trim() !== "" && emailValid && !emailsMatch;

  function pick(r) {
    setSelected(r);
    setRealEmail("");
    setConfirmEmail("");
    setDone(null);
    setError("");
  }

  function cancel() {
    setSelected(null);
    setRealEmail("");
    setConfirmEmail("");
    setConfirming(false);
  }

  async function activate() {
    setError("");
    setDone(null);
    setBusy(true);
    try {
      const email = realEmail.trim().toLowerCase();
      const res = await api.activateInstructor(selected.id, email);
      setDone({ email: res.email || email, emailSent: res.email_sent === true, name: selected.full_name });
      setSelected(null);
      setQuery("");
      setRealEmail("");
      setConfirmEmail("");
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
      setConfirming(false);
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
          <input id="real-email" type="email" value={realEmail} onChange={(e) => setRealEmail(e.target.value)} maxLength={254} autoComplete="off" />
          <label htmlFor="confirm-email">Confirm email (type it again)</label>
          <input
            id="confirm-email"
            type="email"
            value={confirmEmail}
            onChange={(e) => setConfirmEmail(e.target.value)}
            maxLength={254}
            autoComplete="off"
            aria-invalid={showMismatch}
            aria-describedby={showMismatch ? "confirm-email-hint" : undefined}
          />
          {showMismatch && <p id="confirm-email-hint" className="error">The two emails don't match. Check the spelling.</p>}
          <p className="muted small">The sign-in steps are emailed to this address, so check it carefully.</p>
          <button className="primary" onClick={() => setConfirming(true)} disabled={busy || !emailsMatch}>
            {busy ? "Activating..." : "Activate"}
          </button>
          <button onClick={cancel} disabled={busy}>Cancel</button>
        </div>
      ) : (
        <>
          <label htmlFor="find-placeholder">Find the instructor</label>
          <input id="find-placeholder" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Start typing their name..." autoComplete="off" />
          {loading && <p className="muted small">Searching...</p>}
          {searchError && <div className="error">{searchError}</div>}
          {results.map((r) => (
            <button type="button" key={r.id} className="result-item" onClick={() => pick(r)}>
              {r.full_name}
              {r.department_name && <span className="muted"> - {r.department_name}</span>}
            </button>
          ))}
          {!loading && query.trim().length >= 2 && results.length === 0 && !searchError && (
            <p className="muted small">No instructors waiting for a login match that name.</p>
          )}
        </>
      )}

      {confirming && selected && (
        <ConfirmDialog
          title="Is this email correct?"
          confirmLabel="Yes, activate"
          cancelLabel="Go back"
          busy={busy}
          onConfirm={activate}
          onCancel={() => setConfirming(false)}
        >
          <p>
            An email with login instructions will be sent to <strong>{realEmail.trim().toLowerCase()}</strong> for{" "}
            <strong>{selected.full_name}</strong>. Is this correct?
          </p>
        </ConfirmDialog>
      )}

      {done && done.emailSent && (
        <div className="success-box" role="status">
          Activated. An email with sign-in steps was sent to {done.email}.
        </div>
      )}
      {done && !done.emailSent && (
        <>
          <div className="success-box" role="status">Activated.</div>
          <div className="warning-box" role="alert">
            <strong>The sign-in email could not be sent.</strong>
            <p>Give {done.name || "the instructor"} these steps in person:</p>
            <ol>
              <li>Open the iEvaluate app.</li>
              <li>Tap <strong>Forgot Password?</strong> on the login screen.</li>
              <li>Enter <strong>{done.email}</strong> and tap <strong>Send Code</strong>.</li>
              <li>Type the code we email them, then choose a new password.</li>
            </ol>
          </div>
        </>
      )}
      {error && <div className="error" role="alert">{error}</div>}
    </div>
  );
}
