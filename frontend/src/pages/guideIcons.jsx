import React from "react";

// Tiny local stroke-icon map (24x24). Names follow Material icon names.
const P = {
  approval: "M5 20h14M7 16h10l-2-4V9a3 3 0 1 0-6 0v3z",
  edit_note: "M4 7h10M4 12h7M4 17h5M14 17l6-6 2 2-6 6h-2z",
  photo_camera: "M4 8h4l2-3h4l2 3h4v11H4zM12 17a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7z",
  fact_check: "M4 6h9M4 11h6M4 16h6M13 15l2 2 5-5",
  table_chart: "M4 5h16v14H4zM4 10h16M4 15h16M10 5v14",
  vertical_align_top: "M5 4h14M12 20V9M7 13l5-5 5 5",
  water_drop: "M12 3c4 5 6 8 6 11a6 6 0 0 1-12 0c0-3 2-6 6-11z",
  visibility: "M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z",
  content_copy: "M8 8h11v12H8zM5 16V4h11",
  block: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM5.6 5.6l12.8 12.8",
  invert_colors_off: "M12 3c4 5 6 8 6 11a6 6 0 0 1-12 0c0-3 2-6 6-11zM4 4l16 16",
  edit: "M4 20l1-5L16 4l4 4L9 19zM14 6l4 4",
  badge: "M4 6h16v13H4zM9 6V3h6v3M8 13h8M8 16h5",
  check: "M5 12l5 5L20 7",
  crop_square: "M5 5h14v14H5z",
  format_list_numbered: "M10 6h10M10 12h10M10 18h10M4 5l1-1v4M4 14h2l-2 3h2",
  comment: "M4 4h16v12H8l-4 4z",
  close: "M6 6l12 12M18 6L6 18",
  looks_two: "M4 4h16v16H4zM9 9a3 3 0 0 1 6 0c0 3-6 4-6 7h6",
  backspace: "M9 5h12v14H9L2 12zM12 9l6 6M18 9l-6 6",
  edit_off: "M4 20l1-5L16 4l4 4L9 19zM3 3l18 18",
  description: "M6 3h8l4 4v14H6zM14 3v4h4M9 12h6M9 16h6",
  layers: "M12 3l9 5-9 5-9-5zM3 13l9 5 9-5",
  wb_sunny: "M12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M19 5l-2 2M7 17l-2 2",
  crop_free: "M4 9V4h5M15 4h5v5M20 15v5h-5M9 20H4v-5",
  straighten: "M3 8h18v8H3zM7 8v4M11 8v3M15 8v4M19 8v3",
  preview: "M3 5h18v14H3zM3 9h18M12 17a3 3 0 1 0 0-5 3 3 0 0 0 0 5z",
  rotate_right: "M20 12a8 8 0 1 1-3-6.2M20 4v5h-5",
  crop: "M6 2v16h16M2 6h16v16",
  compare: "M9 4H4v16h5M15 4h5v16h-5M12 3v18",
  pin: "M4 9h16M4 15h16M9 4L7 20M17 4l-2 16",
  edit_document: "M6 3h8l4 4v6M6 3v18h6M16 21l1-4 5-5 2 2-5 5z",
  do_not_disturb_on: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM8 12h8",
  help_outline: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.7.4-1 .9-1 1.7M12 17v.5",
  tab: "M3 5h7l2 3h9v11H3z",
  view_column: "M4 5h16v14H4zM9.5 5v14M14.5 5v14",
  looks_5: "M4 4h16v16H4zM15 8H9v4h4a2 2 0 0 1 0 4H9",
  person: "M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4 21c0-4 4-6 8-6s8 2 8 6",
  menu_book: "M12 6c-2-2-5-2-8-2v14c3 0 6 0 8 2 2-2 5-2 8-2V4c-3 0-6 0-8 2zM12 6v14",
  table_rows: "M4 5h16v14H4zM4 10h16M4 14.5h16",
  share: "M18 8a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM6 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM18 22a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM8.6 10.5l6.8-3.9M8.6 13.5l6.8 3.9",
  link: "M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3A4 4 0 0 0 11 18.7l1-1",
  grid_off: "M4 5h16v14H4zM4 12h16M12 5v14M3 3l18 18",
  lock: "M6 11h12v10H6zM8 11V7a4 4 0 0 1 8 0v4",
};

export function Icon({ name, size = 20 }) {
  return (
    <svg className="gi" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      <path d={P[name] || P.help_outline} />
    </svg>
  );
}

export const SECTION_ICONS = ["approval", "edit_note", "photo_camera", "fact_check", "table_chart"];

// [label, icon] per item, same order as SAST_GUIDE sections[].dos / donts.
export const ITEM_META = [
  {
    dos: [["Stamp before handing out", "approval"], ["Upright, same spot", "vertical_align_top"], ["Dark, fresh ink", "water_drop"], ["All digits visible", "visibility"]],
    donts: [["No double stamp", "content_copy"], ["No photocopies / reused numbers", "block"], ["No faint stamp", "invert_colors_off"]],
  },
  {
    dos: [["Black or blue ballpen", "edit"], ["Clear header", "badge"], ["Check ✓ or slash /", "check"], ["Box-sized mark", "crop_square"], ["One mark per row, all 20", "format_list_numbered"], ["Remarks box only", "comment"]],
    donts: [["No X, shading, circle, dot", "close"], ["No two marks in a row", "looks_two"], ["No crossing out", "backspace"], ["No pencil, no writing on stamp", "edit_off"], ["No folding or stapling", "description"]],
  },
  {
    dos: [["Flat, darker surface", "layers"], ["Good light, no glare", "wb_sunny"], ["Whole page, phone straight", "crop_free"], ["Right paper size", "straighten"], ["Check the preview", "preview"]],
    donts: [["No tilted or folded pages", "rotate_right"], ["No double scans", "content_copy"], ["Don't crop stamp or table", "crop"]],
  },
  {
    dos: [["Compare with the paper", "compare"], ["Type stamp exactly", "pin"], ["Fill missing, discard bad", "edit_document"]],
    donts: [["Don't approve unchecked", "do_not_disturb_on"], ["Don't guess a stamp", "help_outline"]],
  },
  {
    dos: [["Tab: SAST Survey Data", "tab"], ["Headers in row 1", "view_column"], ["Items 1–10 / 11–20", "format_list_numbered"], ["Ratings 1 to 5", "looks_5"], ["Instructor name format", "person"], ["Course \"CODE TITLE\"", "menu_book"], ["One row per student", "table_rows"], ["Share as Viewer", "share"], ["Paste link and wait", "link"]],
    donts: [["No renamed tab or merged/extra rows", "grid_off"], ["Numbers, not words", "pin"], ["Truly blank comments", "comment"], ["No double import", "content_copy"], ["\"Access denied\" = not shared", "lock"]],
  },
];
