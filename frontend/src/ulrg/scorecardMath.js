/* ULRG Scorecard — display helpers only. ALL arithmetic (attainment, gap, required, verdict)
   is server-side (SPEC Part 4); the client renders what it is given. This file is the ONE place
   band hexes may live (acceptance §Color routing) — every band decision routes through band(). */

// Scorecard palette. The three-band colors (Part 0.4) are the locked spec hexes; the neutrals
// mirror the mockup. Kept here (not theme.js) because theme.js's amber/poppy already mean other
// things (Forum tokens) and the grep requires band hexes to live only in this file.
export const C = {
  ink: "#002E2C", body: "#3B4B44", slate: "#5C6B62", muted: "#93A099",
  hair: "#ECE6DC", hairSoft: "#F6F2EB", page: "#F2EDE6", surface: "#FFFFFF", parchment: "#F8F5F2",
  meadow: "#5F7D5A", meadowInk: "#4F6A4D", meadowBg: "#E7EFE5",
  teal: "#1F6E72", mist: "#DCE7E9", evergreen: "#002E2C",
  amber: "#D9A227", amberInk: "#8A6414", amberBg: "#FBF0D8",
  poppy: "#E8836A", poppyInk: "#B85434", poppyBg: "#FBE7E1",
  daffodil: "#FFDD1F", daffodilBg: "#FFF8D4", onDark: "#F4EFE7", onDarkMute: "#9FB4AE",
};
export const FD = "var(--font-display)";
export const FB = "var(--font-text)";
// The numeric face. Archivo rather than a monospace: these are FIGURES, not code, and
// Archivo's tabular numerals line a column up without the typewriter texture.
export const FM = "var(--font-data)";

// The color law (Part 0.4): green ≥100, amber 80–99, red <80. Every band decision comes here.
export function band(pct) {
  if (pct === null || pct === undefined || isNaN(pct)) return null;
  if (pct >= 100) return { bg: C.meadowBg, ink: C.meadowInk, bar: C.meadow };
  if (pct >= 80) return { bg: C.amberBg, ink: C.amberInk, bar: C.amber };
  return { bg: C.poppyBg, ink: C.poppyInk, bar: C.poppy };
}

// Verdict label + color from the server's verdict string (server computes which; we style it).
export function verdictStyle(v) {
  switch (v) {
    case "ahead": return { t: "Ahead", ink: C.meadowInk, bg: C.meadowBg };
    case "catchable": return { t: "Catchable", ink: C.meadowInk, bg: C.meadowBg };
    case "stretch": return { t: "Stretch", ink: C.amberInk, bg: C.amberBg };
    case "reset": return { t: "Reset the goal", ink: C.poppyInk, bg: C.poppyBg };
    default: return null;
  }
}

// A weekly value formatted for a cell. Rates/snapshot-ratios come from the API already ×100.
export function fmtV(row, v) {
  if (v === null || v === undefined) return "–";
  if (row.type === "rate" || row._pct) return `${v % 1 ? v.toFixed(1) : v}%`;
  return String(v);
}

export const sgn = (n, d = 0) => (n >= 0 ? "+" : "−") + Math.abs(n).toFixed(d);

// One currency shape for the grid and the drawers. The minus sits against the dollar
// (−$8,032) so a narrow cell doesn't wrap the sign onto its own line. Cents only when
// the caller asks (cost per lead). Compact $130.9K is for the week column; the title
// and the drawer keep the full figure.
export function fmtMoney(v, { cents = false, compact = false } = {}) {
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  const sign = n < 0 ? "−" : "";
  const abs = Math.abs(n);
  if (compact && abs >= 1e6) return `${sign}$${(abs / 1e6).toFixed(1)}M`;
  if (compact && abs >= 10000) return `${sign}$${(abs / 1000).toFixed(1)}K`;
  const body = cents
    ? abs.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    : Math.round(abs).toLocaleString("en-US");
  return `${sign}$${body}`;
}
export const isPct = (row) => row.type === "rate" || row._pct;

// Title case for scorecard names and headings. Matches backend/app/services/titlecase.py.
// Notes and other sentences are not passed through here.
const SMALL = new Set(["a", "an", "the", "and", "or", "of", "in", "to", "vs.", "vs", "at", "by", "for"]);
const KEEP = {
  vip: "VIP", ghl: "GHL", cpl: "CPL", ids: "IDS", qtd: "QTD", eos: "EOS",
  clickup: "ClickUp", meta: "Meta", stripe: "Stripe", forum: "Forum",
  becollective: "beCollective", quickbooks: "QuickBooks", zoom: "Zoom",
  axcion: "Axcion", mighty: "Mighty", dallas: "Dallas", scottsdale: "Scottsdale",
  sb: "SB", ga: "GA", mrr: "MRR", ytd: "YTD", cac: "CAC", revshare: "RevShare",
};

function titleToken(raw, force) {
  if (/[a-z]/.test(raw) && /[A-Z]/.test(raw.slice(1))) return raw;
  if (/^[A-Z][A-Z0-9]{1,}$/.test(raw)) return raw;
  if (/^\$[\d,.]+[KMB]?$/i.test(raw)) return raw;
  if (/\d/.test(raw)) return raw;
  const key = raw.replace(/^[^A-Za-z]+|[^A-Za-z.]+$/g, "");
  if (!key) return raw;
  const lower = key.toLowerCase();
  if (KEEP[lower]) return raw.replace(key, KEEP[lower]);
  if (!force && SMALL.has(lower)) return raw.replace(key, lower);
  return raw.replace(key, lower.charAt(0).toUpperCase() + lower.slice(1));
}

function titleWord(raw, force) {
  if (raw.includes("-") && raw.replace(/-/g, "") !== "") {
    return raw.split("-").map((bit, i) => titleToken(bit, i ? true : force)).join("-");
  }
  return titleToken(raw, force);
}

export function titleCase(text) {
  if (text == null || String(text).trim() === "") return text;
  const parts = String(text).split(/(\s+)/);
  const words = parts.filter((p) => p && !/^\s+$/.test(p));
  const last = words.length - 1;
  let n = 0;
  return parts.map((part) => {
    if (!part || /^\s+$/.test(part)) return part;
    const out = titleWord(part, n === 0 || n === last);
    n += 1;
    return out;
  }).join("");
}
