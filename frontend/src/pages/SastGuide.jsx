import React from "react";
import { PageHeader } from "../components.jsx";
import { SAST_GUIDE } from "../messages.js";
import { Icon, ITEM_META, SECTION_ICONS } from "./guideIcons.jsx";

const SERVICE_EMAIL = import.meta.env.VITE_SHEET_SERVICE_ACCOUNT_EMAIL || SAST_GUIDE.servicePlaceholder;

export function GuideBody() {
  return (
    <div className="sast-guide">
      {SAST_GUIDE.sections.map((sec, si) => (
        <section className="section guide-section" key={sec.title}>
          <h2 className="guide-h2"><Icon name={SECTION_ICONS[si]} size={24} /><span>{sec.title}</span></h2>
          {[["dos", "DO", "guide-do", true], ["donts", "DON'T", "guide-dont", false]].map(([k, head, cls, ok]) => (
            <div className={"guide-box " + cls} key={k}>
              <h3>{head}</h3>
              <ul className="guide-items">
                {sec[k].map((t, i) => {
                  const [label, icon] = ITEM_META[si][k][i];
                  return (
                    <li key={t}>
                      <span className={"gbadge " + (ok ? "ok" : "no")} aria-hidden="true">{ok ? "\u2713" : "\u2717"}</span>
                      <Icon name={icon} />
                      <span className="gtext">
                        <strong>{label}</strong>
                        <span className="gfull">{t.replace("{SERVICE_ACCOUNT_EMAIL}", SERVICE_EMAIL)}</span>
                      </span>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </section>
      ))}
      <p className="small">{SAST_GUIDE.footer}</p>
    </div>
  );
}

export default function SastGuide() {
  return (
    <div>
      <PageHeader title={SAST_GUIDE.title} subtitle={SAST_GUIDE.subtitle} back={{ to: "/", label: "Home" }} />
      <GuideBody />
    </div>
  );
}

// Auto-shown once per semester until the user taps "I have read this".
export function GuideModal({ onAck }) {
  return (
    <div className="guide-overlay" role="dialog" aria-modal="true" aria-label={SAST_GUIDE.title}>
      <div className="guide-modal">
        <div className="guide-modal-body">
          <PageHeader title={SAST_GUIDE.title} subtitle={SAST_GUIDE.subtitle} />
          <GuideBody />
        </div>
        <div className="guide-modal-foot">
          <button type="button" className="primary big" onClick={onAck}>
            I have read this
          </button>
        </div>
      </div>
    </div>
  );
}
