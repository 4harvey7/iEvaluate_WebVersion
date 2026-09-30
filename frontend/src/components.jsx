import React, { useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { API_BASE, downloadJson } from "./api.js";
import { Icon } from "./icons.jsx";

export function timeAgo(iso, now = Date.now()) {
  if (!iso) return "never";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "unknown";
  const s = Math.max(0, Math.round((now - t) / 1000));
  if (s < 45) return "just now";
  const m = Math.max(1, Math.round(s / 60));
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 48) return `${h} hour${h === 1 ? "" : "s"} ago`;
  const d = Math.round(h / 24);
  return `${d} days ago`;
}

export function useNow(intervalMs = 1000) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}

// Simple coloured dot + text. Kept for small generic uses.
export function StatusDot({ online, label }) {
  return (
    <span className={`status ${online ? "online" : "offline"}`}>
      <span className="dot" />
      {label ?? (online ? "Online" : "Offline")}
    </span>
  );
}

// Page title + one-line "what this page is for" + optional back link.
export function PageHeader({ title, subtitle, back, actions, children }) {
  useEffect(() => {
    if (typeof title === "string") document.title = `${title} - iEvaluate Batch Scanning`;
  }, [title]);
  return (
    <header className="page-header">
      {back && (
        <nav className="breadcrumbs" aria-label="Breadcrumb">
          <Link to={back.to}>
            <Icon name="chevronLeft" size={16} />
            {back.label}
          </Link>
        </nav>
      )}
      <div className="page-header-row">
        <h1>{title}</h1>
        {actions && <div className="page-header-actions">{actions}</div>}
      </div>
      {subtitle && <p className="page-subtitle">{subtitle}</p>}
      {children}
    </header>
  );
}

export function Loading({ children = "Loading..." }) {
  return (
    <div className="loading" role="status" aria-live="polite">
      <span className="spinner" aria-hidden="true" />
      <span>{children}</span>
    </div>
  );
}

export function EmptyState({ icon = "scan", title, children }) {
  return (
    <div className="empty-state">
      <Icon name={icon} size={28} />
      <div>
        <strong>{title}</strong>
        {children && <div className="muted">{children}</div>}
      </div>
    </div>
  );
}

export function ConfirmDialog({ title, children, confirmLabel = "Confirm", cancelLabel = "Cancel", danger = false, busy = false, onConfirm, onCancel }) {
  const titleId = useId();
  const confirmRef = useRef(null);
  const cancelRef = useRef(null);

  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && !busy && onCancel?.();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onCancel]);

  // Move focus into the dialog (the safe choice for risky actions) and give it back afterwards.
  useEffect(() => {
    const previous = document.activeElement;
    (danger ? cancelRef.current : confirmRef.current)?.focus();
    return () => previous?.focus?.();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby={titleId}>
      <div className="modal">
        <h2 id={titleId}>{title}</h2>
        <div className="modal-body">{children}</div>
        <div className="modal-actions">
          <button ref={confirmRef} className={danger ? "danger" : "primary"} disabled={busy} onClick={onConfirm}>
            {busy ? "Working..." : confirmLabel}
          </button>
          <button ref={cancelRef} disabled={busy} onClick={onCancel}>{cancelLabel}</button>
        </div>
      </div>
    </div>
  );
}

export function buildWatcherConfig({ apiBase, stationKey, folder }) {
  return {
    api_base: (apiBase || API_BASE).replace(/\/+$/, ""),
    station_key: stationKey,
    folder: folder || "C:/Scans",
  };
}

export function downloadWatcherConfig({ apiBase, stationKey, folder }) {
  downloadJson(buildWatcherConfig({ apiBase, stationKey, folder }), "watcher_config.json");
}

// Shows a freshly made connection code exactly once (manual / IT setup).
export function KeyReveal({ station, stationKey, folder, apiBase, onDone, doneLabel = "I've saved it - continue" }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(stationKey);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  }

  return (
    <div className="warning-box">
      <strong>Connection code for "{station?.label}"</strong>
      <p>
        This code lets the scanner laptop send scans to the website. It is shown <strong>only now</strong> and
        can't be looked up later. Keep it private - anyone with it can send scans as this laptop.
      </p>
      <div className="key-box">
        <code>{stationKey}</code>
      </div>
      <div className="btn-row">
        <button type="button" onClick={copy}>{copied ? "Copied" : "Copy code"}</button>
        <button
          type="button"
          onClick={() => downloadWatcherConfig({ apiBase, stationKey, folder: folder || station?.folder_path })}
        >
          <Icon name="download" size={16} /> Download settings file (watcher_config.json)
        </button>
        {onDone && (
          <button type="button" className="primary" onClick={onDone}>{doneLabel}</button>
        )}
      </div>
    </div>
  );
}
