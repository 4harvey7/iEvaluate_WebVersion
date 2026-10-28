import React, { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api.js";
import { useSearch } from "../useSearch.js";
import { Loading, PageHeader } from "../components.jsx";
import { Icon } from "../icons.jsx";
import { ScannerStatus, SkippedPagesCard, WatcherOffHint, getScannerState, useStation } from "../scanner.jsx";

function InstructorPicker({ departments, selected, onSelect, inputId }) {
  const [query, setQuery] = useState("");
  const { results, loading, error } = useSearch(api.searchInstructors, selected ? "" : query);
  const [showCreate, setShowCreate] = useState(false);
  const [first, setFirst] = useState("");
  const [last, setLast] = useState("");
  const [dept, setDept] = useState("");
  const [employment, setEmployment] = useState("");
  const [duplicates, setDuplicates] = useState([]);
  const [createError, setCreateError] = useState("");
  const [creating, setCreating] = useState(false);

  function openCreate() {
    const parts = query.trim().split(/\s+/);
    setFirst(parts.length > 1 ? parts.slice(0, -1).join(" ") : parts[0] || "");
    setLast(parts.length > 1 ? parts[parts.length - 1] : "");
    setDuplicates([]);
    setCreateError("");
    setShowCreate(true);
  }

  async function submitCreate(confirmNew = false) {
    setDuplicates([]);
    setCreateError("");
    setCreating(true);
    try {
      const created = await api.createInstructor({
        first_name: first.trim(),
        last_name: last.trim(),
        department_id: Number(dept),
        employment_type: employment,
        confirm_new_person: confirmNew === true,
      });
      onSelect(created);
      setShowCreate(false);
    } catch (err) {
      if (err.status === 409 && err.code === "duplicate_instructor") {
        setDuplicates(err.detail?.existing_instructors || (err.detail?.existing_instructor ? [err.detail.existing_instructor] : []));
      } else {
        setCreateError(err.message);
      }
    } finally {
      setCreating(false);
    }
  }

  if (selected) {
    return (
      <div className="selected-box">
        <div>
          <strong>{selected.full_name || `${selected.first_name} ${selected.last_name}`}</strong>
          {selected.department_name && <span className="muted"> - {selected.department_name}</span>}
          {selected.is_placeholder && <span className="badge">login not activated yet</span>}
        </div>
        <button type="button" className="small-btn" onClick={() => onSelect(null)}>Change</button>
      </div>
    );
  }

  return (
    <>
      <input id={inputId} value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Start typing a name (at least 2 letters)..." autoComplete="off" />
      {loading && <p className="muted small">Searching...</p>}
      {error && <div className="error">{error}</div>}
      {results.map((r) => (
        <button type="button" key={r.id} className="result-item" onClick={() => onSelect(r)}>
          {r.full_name}
          {r.department_name && <span className="muted"> - {r.department_name}</span>}
          {r.is_placeholder && <span className="badge">login not activated yet</span>}
        </button>
      ))}
      {!loading && query.trim().length >= 2 && results.length === 0 && !error && (
        <p className="muted small">No instructor found with that name. Check the spelling, or add them below.</p>
      )}
      {query.trim().length >= 2 && !showCreate && (
        <button type="button" onClick={openCreate}>+ Add a new instructor</button>
      )}
      {showCreate && (
        <div className="warning-box">
          <strong>Add a new instructor</strong>
          <p>This adds the instructor now so you can scan their sheets. Their login stays switched off until an SAO admin activates it with their real email.</p>
          <div className="grid-2">
            <div>
              <label htmlFor="new-inst-first">First name</label>
              <input id="new-inst-first" value={first} onChange={(e) => setFirst(e.target.value)} maxLength={80} />
            </div>
            <div>
              <label htmlFor="new-inst-last">Last name</label>
              <input id="new-inst-last" value={last} onChange={(e) => setLast(e.target.value)} maxLength={80} />
            </div>
          </div>
          <label htmlFor="new-inst-dept">Department</label>
          <select id="new-inst-dept" value={dept} onChange={(e) => setDept(e.target.value)}>
            <option value="">Choose a department...</option>
            {departments.map((d) => (
              <option key={d.id} value={d.id}>{d.d_name}{d.d_code ? ` (${d.d_code})` : ""}</option>
            ))}
          </select>
          <label htmlFor="new-inst-emp">Employment type</label>
          <select id="new-inst-emp" value={employment} onChange={(e) => setEmployment(e.target.value)}>
            <option value="">Choose...</option>
            <option value="FULL-TIME">Full-time</option>
            <option value="PART-TIME">Part-time</option>
          </select>
          {duplicates.length === 1 && (
            <div className="error">
              {duplicates[0].exact ? "An instructor with this exact name already exists" : "An instructor with a very similar name already exists"}: <strong>{duplicates[0].full_name || `${duplicates[0].first_name} ${duplicates[0].last_name}`}</strong>
              {duplicates[0].department_name ? ` (${duplicates[0].department_name})` : ""}.
              {duplicates[0].eligible ? (
                <div>
                  <button type="button" className="primary" onClick={() => { onSelect(duplicates[0]); setShowCreate(false); }}>
                    Use existing instructor
                  </button>
                </div>
              ) : (
                <div>That person isn't set up as an instructor. Ask an SAO admin to check before adding a new one.</div>
              )}
            </div>
          )}
          {duplicates.length > 1 && (
            <div className="error">
              Instructors with very similar names already exist. Is it one of these?
              <ul>
                {duplicates.map((m) => (
                  <li key={m.id}>
                    <strong>{m.full_name || `${m.first_name} ${m.last_name}`}</strong>
                    {m.department_name && <span className="muted"> - {m.department_name}</span>}
                    {m.is_placeholder && <span className="badge">login not activated yet</span>}
                    {m.eligible ? (
                      <div>
                        <button type="button" className="primary" onClick={() => { onSelect(m); setShowCreate(false); }}>
                          Use existing instructor
                        </button>
                      </div>
                    ) : (
                      <div>That person isn't set up as an instructor. Ask an SAO admin to check before adding a new one.</div>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {/* Exactly the same name can't be added again (the database allows one person per full name). */}
          {duplicates.length > 0 && !duplicates.some((d) => d.exact) && (
            <div>
              <button type="button" onClick={() => submitCreate(true)} disabled={creating}>
                This is a different person - add "{first.trim()} {last.trim()}"
              </button>
            </div>
          )}
          {createError && <div className="error">{createError}</div>}
          <button
            type="button"
            className="primary"
            disabled={!first.trim() || !last.trim() || !dept || !employment || creating}
            onClick={() => submitCreate(false)}
          >
            {creating ? "Adding..." : "Add instructor"}
          </button>
          <button type="button" onClick={() => setShowCreate(false)} disabled={creating}>Cancel</button>
        </div>
      )}
    </>
  );
}

function SubjectPicker({ departments, selected, onSelect, inputId }) {
  const [query, setQuery] = useState("");
  const { results, loading, error } = useSearch(api.searchSubjects, selected ? "" : query);
  const [showCreate, setShowCreate] = useState(false);
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [dept, setDept] = useState("");
  const [existing, setExisting] = useState(null);
  const [createError, setCreateError] = useState("");
  const [creating, setCreating] = useState(false);

  function openCreate() {
    setCode(query.trim().toUpperCase());
    setName("");
    setExisting(null);
    setCreateError("");
    setShowCreate(true);
  }

  async function submitCreate() {
    setExisting(null);
    setCreateError("");
    setCreating(true);
    try {
      const created = await api.createSubject({ subject_code: code.trim(), subject_name: name.trim(), department_id: Number(dept) });
      onSelect(created);
      setShowCreate(false);
    } catch (err) {
      if (err.status === 409 && err.detail?.existing_subject) setExisting(err.detail.existing_subject);
      else setCreateError(err.message);
    } finally {
      setCreating(false);
    }
  }

  if (selected) {
    return (
      <div className="selected-box">
        <div>
          <strong>{selected.subject_code}</strong> - {selected.subject_name}
          {selected.department_name && <span className="muted"> ({selected.department_name})</span>}
        </div>
        <button type="button" className="small-btn" onClick={() => onSelect(null)}>Change</button>
      </div>
    );
  }

  return (
    <>
      <input id={inputId} value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Start typing a subject code or name..." autoComplete="off" />
      {loading && <p className="muted small">Searching...</p>}
      {error && <div className="error">{error}</div>}
      {results.map((r) => (
        <button type="button" key={r.id} className="result-item" onClick={() => onSelect(r)}>
          <strong>{r.subject_code}</strong> - {r.subject_name}
          {r.department_name && <span className="muted"> ({r.department_name})</span>}
        </button>
      ))}
      {!loading && query.trim().length >= 2 && results.length === 0 && !error && (
        <p className="muted small">No subject found. Check the spelling, or add it below.</p>
      )}
      {query.trim().length >= 2 && !showCreate && (
        <button type="button" onClick={openCreate}>+ Add a new subject</button>
      )}
      {showCreate && (
        <div className="warning-box">
          <strong>Add a new subject</strong>
          <div className="grid-2">
            <div>
              <label htmlFor="new-subj-code">Subject code</label>
              <input id="new-subj-code" value={code} onChange={(e) => setCode(e.target.value)} maxLength={30} />
            </div>
            <div>
              <label htmlFor="new-subj-name">Subject name</label>
              <input id="new-subj-name" value={name} onChange={(e) => setName(e.target.value)} maxLength={200} />
            </div>
          </div>
          <label htmlFor="new-subj-dept">Department</label>
          <select id="new-subj-dept" value={dept} onChange={(e) => setDept(e.target.value)}>
            <option value="">Choose a department...</option>
            {departments.map((d) => (
              <option key={d.id} value={d.id}>{d.d_name}{d.d_code ? ` (${d.d_code})` : ""}</option>
            ))}
          </select>
          {existing && (
            <div className="error">
              That subject already exists: <strong>{existing.subject_code}</strong> - {existing.subject_name}.
              <div>
                <button type="button" className="primary" onClick={() => { onSelect(existing); setShowCreate(false); }}>
                  Use existing subject
                </button>
              </div>
            </div>
          )}
          {createError && <div className="error">{createError}</div>}
          <button type="button" className="primary" disabled={!code.trim() || !name.trim() || !dept || creating} onClick={submitCreate}>
            {creating ? "Adding..." : "Add subject"}
          </button>
          <button type="button" onClick={() => setShowCreate(false)} disabled={creating}>Cancel</button>
        </div>
      )}
    </>
  );
}

export default function BatchSetup({ stationId, onStationMissing }) {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(true);
  const { station } = useStation(stationId, { onMissing: onStationMissing });
  const [departments, setDepartments] = useState([]);
  const [simulation, setSimulation] = useState(false);
  const [error, setError] = useState("");
  const [instructor, setInstructor] = useState(null);
  const [subject, setSubject] = useState(null);
  const [starting, setStarting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [openBatch, depts, cfg] = await Promise.all([
          api.getOpenBatch(stationId),
          api.listDepartments(),
          api.config().catch(() => null),
        ]);
        if (cancelled) return;
        if (openBatch) {
          navigate(`/batch/${openBatch.id}`, { replace: true });
          return;
        }
        setDepartments(depts || []);
        setSimulation(!!cfg?.simulation_mode);
      } catch (err) {
        if (cancelled) return;
        if (err.status === 404) {
          onStationMissing?.();
          return;
        }
        setError(err.message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [stationId, navigate, onStationMissing]);

  async function startBatch() {
    setError("");
    setStarting(true);
    try {
      const batch = await api.startBatch({ station_id: stationId, instructor_id: instructor.id, subject_id: subject.id });
      navigate(`/batch/${batch.id}`);
    } catch (err) {
      if (err.code === "batch_already_open") {
        const open = await api.getOpenBatch(stationId).catch(() => null);
        if (open) return navigate(`/batch/${open.id}`);
      }
      setError(err.message);
    } finally {
      setStarting(false);
    }
  }

  const header = (
    <PageHeader
      title="Step 2: Start a batch"
      subtitle="A batch is one stack of sheets for one instructor and one subject. Choose them below, then press Start scanning."
      back={{ to: "/", label: "Home" }}
      actions={simulation && <span className="badge sim" title="Practice mode: results are made up, nothing is really scored">PRACTICE MODE</span>}
    />
  );

  if (loading) {
    return (
      <div>
        {header}
        <Loading>Checking whether a batch is already open on this laptop...</Loading>
      </div>
    );
  }

  const scannerReady = getScannerState(station).tone === "green";

  return (
    <div>
      {header}

      <div className="status-strip">
        <span className="muted">Scanner laptop{station?.label ? ` (${station.label})` : ""}:</span>
        <ScannerStatus station={station} />
      </div>
      {station && !scannerReady && (
        <div className="warning-box small">
          {getScannerState(station).key === "offline" ? (
            <WatcherOffHint />
          ) : (
            <>
              The scanner laptop isn't ready, so scans won't arrive yet. You can still start the batch, but it's best to fix
              this first: <Link to="/scanner-setup">go to Step 1: Scanner setup</Link>.
            </>
          )}
        </div>
      )}

      <SkippedPagesCard
        station={station}
        variant="brief"
        hint="After you press Start scanning, the batch page lets you add them to the new batch or discard them."
      />

      <ol className="form-steps">
        <li>
          <label htmlFor="pick-instructor" className="form-step-label">
            <span className={`step-num ${instructor ? "is-done" : ""}`} aria-hidden="true">
              {instructor ? <Icon name="check" size={16} /> : "A"}
            </span>
            Choose the instructor
          </label>
          <InstructorPicker inputId="pick-instructor" departments={departments} selected={instructor} onSelect={setInstructor} />
        </li>
        <li>
          <label htmlFor="pick-subject" className="form-step-label">
            <span className={`step-num ${subject ? "is-done" : ""}`} aria-hidden="true">
              {subject ? <Icon name="check" size={16} /> : "B"}
            </span>
            Choose the subject
          </label>
          <SubjectPicker inputId="pick-subject" departments={departments} selected={subject} onSelect={setSubject} />
        </li>
        <li>
          <div className="form-step-label">
            <span className="step-num" aria-hidden="true">C</span>
            Start scanning
          </div>
          {error && <div className="error" role="alert">{error}</div>}
          <button className="primary big" disabled={!instructor || !subject || starting} onClick={startBatch}>
            <Icon name="play" />
            {starting ? "Starting..." : "Start scanning"}
          </button>
          {(!instructor || !subject) && <p className="muted small">Choose an instructor and a subject first.</p>}
          <p className="muted small">
            Only sheets scanned after you press Start scanning go into this batch. Older files in the scan folder are held back, and you can add them from the batch page.
          </p>
        </li>
      </ol>
    </div>
  );
}
