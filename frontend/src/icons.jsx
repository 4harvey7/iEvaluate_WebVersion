import React from "react";

// Small inline icon set (Feather-style strokes) so we don't need an icon library.
const PATHS = {
  home: <><path d="M3 10.5 12 3l9 7.5" /><path d="M5 9.5V21h14V9.5" /><path d="M9.5 21v-6h5v6" /></>,
  laptop: <><rect x="4" y="4" width="16" height="11" rx="1.5" /><path d="M2 19h20" /></>,
  play: <><circle cx="12" cy="12" r="9" /><path d="m10 8.5 5.5 3.5-5.5 3.5z" /></>,
  scan: <><path d="M4 8V5a1 1 0 0 1 1-1h3" /><path d="M16 4h3a1 1 0 0 1 1 1v3" /><path d="M20 16v3a1 1 0 0 1-1 1h-3" /><path d="M8 20H5a1 1 0 0 1-1-1v-3" /><path d="M4 12h16" /></>,
  userCheck: <><circle cx="9" cy="8" r="4" /><path d="M2 21a7 7 0 0 1 14 0" /><path d="m16 11 2 2 4-4" /></>,
  help: <><circle cx="12" cy="12" r="9" /><path d="M9.5 9.2a2.6 2.6 0 0 1 5 .9c0 1.8-2.5 2.3-2.5 3.9" /><path d="M12 17.2h.01" /></>,
  logout: <><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" /><path d="m16 17 5-5-5-5" /><path d="M21 12H9" /></>,
  swap: <><path d="M4 7h13l-3-3" /><path d="M20 17H7l3 3" /></>,
  menu: <><path d="M3 6h18" /><path d="M3 12h18" /><path d="M3 18h18" /></>,
  close: <><path d="M6 6l12 12" /><path d="M18 6 6 18" /></>,
  check: <path d="m5 12.5 4.5 4.5L19 7.5" />,
  download: <><path d="M12 3v12" /><path d="m7 10 5 5 5-5" /><path d="M5 21h14" /></>,
  folder: <path d="M3 6.5A1.5 1.5 0 0 1 4.5 5H9l2 2.5h8.5A1.5 1.5 0 0 1 21 9v9.5a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 18.5z" />,
  chevronLeft: <path d="m15 5-7 7 7 7" />,
  chevronRight: <path d="m9 5 7 7-7 7" />,
  alert: <><path d="M12 3 2 20h20z" /><path d="M12 10v4" /><path d="M12 17h.01" /></>,
};

export function Icon({ name, size = 20, className = "", title }) {
  const path = PATHS[name];
  if (!path) return null;
  return (
    <svg
      className={`icon ${className}`}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden={title ? undefined : "true"}
      role={title ? "img" : undefined}
      focusable="false"
    >
      {title && <title>{title}</title>}
      {path}
    </svg>
  );
}
