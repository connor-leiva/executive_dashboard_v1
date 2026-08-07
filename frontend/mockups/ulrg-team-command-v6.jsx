import { useState, Fragment } from "react";

/* ──────────────────────────────────────────────────────────────
   ULRG · Team Command - mockup v6
   v6 reverses the week order: most recent week furthest LEFT, to
   match the spreadsheet the team already reads. Data is STORED
   oldest first (index aligned to WEEKS ascending) and reversed only
   at render, so every derivation stays in chronological order and
   nothing has to think about direction twice.
   Consequence: the counted band is now the LEFTMOST weeks and the
   "+N earlier" marker moves to the far RIGHT.

   v5 fixed the width logic. The two halves trade space:
     · panel shut -> 8 weeks (13 in production), weeks fill the row
     · panel open -> 5 weeks (6 in production), panel gets the room
   Column widths are declared in a colgroup with table-layout fixed,
   so freed space goes to the weeks instead of the name column.
   When the cumulative window reaches past the visible weeks, a
   "+N earlier" marker sits at the head of the counted band so the
   total is never made of weeks you cannot see.
   ────────────────────────────────────────────────────────────── */

const C = {
  ink: "#002E2C", body: "#3B4B44", slate: "#5C6B62", muted: "#93A099",
  hair: "#ECE6DC", hairSoft: "#F6F2EB", page: "#F2EDE6", surface: "#FFFFFF", parchment: "#F8F5F2",
  meadow: "#5F7D5A", meadowInk: "#4F6A4D", meadowBg: "#E7EFE5", sprout: "#B8CCB8",
  teal: "#1F6E72", mist: "#DCE7E9", evergreen: "#002E2C",
  amber: "#D9A227", amberInk: "#8A6414", amberBg: "#FBF0D8",
  poppy: "#E8836A", poppyInk: "#B85434", poppyBg: "#FBE7E1",
  daffodil: "#FFDD1F", daffodilBg: "#FFF8D4",
  onDark: "#F4EFE7", onDarkMute: "#9FB4AE",
};
const FD = '"Poppins","Inter",system-ui,sans-serif';
const FB = '"Inter",system-ui,sans-serif';
const FM = '"JetBrains Mono",ui-monospace,SFMono-Regular,monospace';

const WEEKS_LEFT = 5, DAY = 6, DAYS = 31;
const VIS_OPEN = 5, VIS_SHUT = 8;   // production: 6 and 13

const WEEKS = [
  { n: 23, d: "6/08" }, { n: 24, d: "6/15" }, { n: 25, d: "6/22" }, { n: 26, d: "6/29" },
  { n: 27, d: "7/06" }, { n: 28, d: "7/13" }, { n: 29, d: "7/20" }, { n: 30, d: "7/27" },
];

const GROUPS = [
  {
    key: "davis", name: "Davis", owner: "Justin",
    rows: [
      { m: "Appointments Met", goal: 30, type: "flow", stage: 1, lever: "volume", own: "JT", src: "sisu", v: [34, 20, 25, 19, 20, 18, 18, 33] },
      { m: "Clients Signed", goal: 20, type: "flow", stage: 2, lever: "volume", own: "JT", src: "sisu", v: [20, 18, 15, 13, 11, 11, 13, 20] },
      { m: "Under Contract", goal: 10, type: "flow", stage: 3, lever: "volume", own: "JT", src: "sisu", v: [9, 12, 13, 6, 6, 9, 4, 11] },
      { m: "Homes Sold", goal: 8, type: "flow", stage: 4, lever: "volume", own: "JT", src: "sisu", note: "130 Q2 pace", v: [11, 11, 6, 10, 7, 10, 7, 3] },
      { m: "Recruiting Appts Met", goal: 10, type: "flow", lever: "behavior", own: "JT", src: "manual", v: [10, 10, 11, 12, 8, 11, 0, 10] },
      { m: "Sympli Attach Rate", goal: 40, type: "rate", lever: "behavior", own: "JT", src: "sisu", v: [43.0, 27.3, 0, 28.6, 0, 10, 0, 66.7] },
      { m: "Meraki Attach Rate", goal: 60, type: "rate", lever: "behavior", own: "JT", src: "sisu", v: [54.6, 54.6, 28.6, 50, 55.6, 70, 62.5, 33.3] },
    ],
    read: "Every stage is falling, including closings. The 102 percent on Homes Sold is the last of the May pipeline arriving. Last four weeks it is already down to 84.",
  },
  {
    key: "slc", name: "SLC", owner: "unassigned",
    rows: [
      { m: "Appointments Met", goal: 24, type: "flow", stage: 1, lever: "volume", own: "??", src: "sisu", v: [28, 12, 16, 21, 24, 19, 17, 26] },
      { m: "Signed Units", goal: 15, type: "flow", stage: 2, lever: "volume", own: "??", src: "sisu", v: [24, 11, 13, 10, 15, 8, 16, 15] },
      { m: "Under Contract", goal: 6, type: "flow", stage: 3, lever: "volume", own: "??", src: "sisu", v: [9, 8, 8, 7, 8, 6, 5, 7] },
      { m: "Homes Sold", goal: 6, type: "flow", stage: 4, lever: "volume", own: "??", src: "sisu", note: "100 Q2 pace", v: [8, 4, 12, 7, 7, 3, 7, 8] },
      { m: "Recruiting Appts Met", goal: 5, type: "flow", lever: "behavior", own: "??", src: "manual", v: [4, 5, 5, 5, 7, 4, 0, 5] },
      { m: "Sympli Attach Rate", goal: 40, type: "rate", lever: "behavior", own: "??", src: "sisu", v: [0, 50, 25, 0, 50, 0, 40, 20] },
      { m: "Meraki Attach Rate", goal: 60, type: "rate", lever: "behavior", own: "??", src: "sisu", v: [67, 50, 33, 40, 71.4, 66.7, 75, 58] },
    ],
    read: "Behind on the cumulative but climbing. Appointments are up 9 points over the last four weeks. This is a hole being filled, not one being dug.",
  },
  {
    key: "utco", name: "Utah County", owner: "unassigned",
    rows: [
      { m: "Appointments Met", goal: 5, type: "flow", stage: 1, lever: "volume", own: "??", src: "sisu", v: [5, 2, 6, 2, 3, 3, 6, 7] },
      { m: "Signed Units", goal: 4, type: "flow", stage: 2, lever: "volume", own: "??", src: "sisu", v: [4, 1, 4, 2, 3, 4, 1, 2] },
      { m: "Under Contract", goal: 3, type: "flow", stage: 3, lever: "volume", own: "??", src: "sisu", v: [4, 0, 3, 2, 2, 1, 2, 2] },
      { m: "Homes Sold", goal: 2, type: "flow", stage: 4, lever: "volume", own: "??", src: "sisu", note: "20 Q2 pace", v: [0, 1, 0, 4, 2, 1, 1, 5] },
      { m: "Meraki Attach Rate", goal: 60, type: "rate", lever: "behavior", own: "??", src: "sisu", v: [0, 100, 0, 0, 0, 0, 0, 25] },
      { m: "Sympli Attach Rate", goal: 40, type: "rate", lever: "behavior", own: "??", src: "sisu", v: [0, 0, 0, 50, 0, 0, 0, 0] },
    ],
    read: "Appointments recovering fast, up 20 points. Conversion is the block. On this volume the attach goals cannot be reached by arithmetic and need resetting.",
  },
  {
    key: "overall", name: "Overall", owner: "Spring",
    rows: [
      { m: "ZHL Referral Rate", goal: 10, type: "rate", lever: "behavior", own: "SB", src: "sisu", v: [null, 12.1, 11.9, 8.7, 9.0, 11.5, null, 9.7] },
      { m: "New Recruitment Leads", goal: 9, type: "flow", lever: "volume", own: "SB", src: "manual", v: [null, 14, 24, 12, 3, 16, 4, 3] },
      { m: "Mastermind RSVPs", goal: 0, type: "flow", lever: "volume", own: "SB", src: "ghl", v: [null, null, null, null, 62, 0, 3, 0] },
      { m: "Met to Signed Ratio YTD", goal: 60, type: "snap", pct: true, own: "SB", src: "sisu", v: [null, 55.7, 55.4, 56.1, 56.3, 56.0, null, 56.6] },
      { m: "Meraki Attach, Utah Life", goal: 60, type: "rate", lever: "behavior", own: "SB", src: "sisu", v: [null, 50.0, 26.3, 40.9, 61.1, 57.1, 61.1, 37.5] },
      { m: "Sympli Attach, Utah Life", goal: 40, type: "rate", lever: "behavior", own: "SB", src: "sisu", v: [null, 26.0, 0.0, 15.4, 11.1, 9.1, 16.7, 18.8] },
      { m: "Database HealthScore", goal: 65, type: "snap", own: "SB", src: "fub", v: [null, 65, 66, 65, 67, 65, 66, 65] },
      { m: "Q2 Homes, 250 target", goal: 20, type: "flow", lever: "volume", own: "SB", src: "sisu", v: [null, 16, 19, 21, 18, 14, 14, 16] },
      { m: "QTD Agents Recruited", goal: 4, type: "snap", own: "SB", src: "manual", v: [null, 5, 5, 1, null, 13, null, 20] },
    ],
    read: "Three rows here are running totals, not weekly counts. They carry no cumulative because summing them double counts.",
  },
];

const SRC = {
  sisu: { label: "Sisu", auto: true }, fub: { label: "Follow Up Boss", auto: true },
  ghl: { label: "GoHighLevel", auto: true }, manual: { label: "Entered by hand", auto: false },
};

const ROOMS = {
  davis: {
    name: "Davis", leader: "Justin", initials: "JT", committed: "Aug 4 L10",
    pace: { label: "Appointments Met", target: 125, actual: 21, cum: [4, 7, 11, 15, 18, 21], src: "sisu" },
    rocks: [
      { title: "50% of agents on weekly check-ins", due: "Aug 31", src: "manual", have: 11, need: 17, of: 34, prior: 9,
        detail: [{ n: "Checked in this week", v: 11 }, { n: "Missed 1 week", v: 6 }, { n: "Missed 2+ weeks", v: 9 }, { n: "Never started", v: 8 }] },
      { title: "8 people into the next two Hello Weeks", due: "Sep 8", src: "ghl", have: 3, need: 8, pipeline: 4,
        sessions: [{ d: "Hello Week, Aug 18", seats: 2, names: ["M. Reyes", "D. Kwan"] }, { d: "Hello Week, Sep 8", seats: 1, names: ["T. Alvarez"] }] },
    ],
  },
  slc: { name: "SLC", leader: "unassigned", initials: "??", committed: null },
  utco: { name: "Utah County", leader: "unassigned", initials: "??", committed: null },
};

const BBA = [
  { c: "Whitaker", a: "R. Nunes", d: 4, act: 2, sh: 3 }, { c: "Delgado", a: "P. Ord", d: 7, act: 1, sh: 5 },
  { c: "Chen", a: "R. Nunes", d: 9, act: 3, sh: 2 }, { c: "Barlow", a: "K. Frye", d: 12, act: 9, sh: 1 },
  { c: "Ivory", a: "P. Ord", d: 16, act: 4, sh: 6 }, { c: "Sandoval", a: "M. Teague", d: 19, act: 2, sh: 4 },
  { c: "Roos", a: "K. Frye", d: 23, act: 14, sh: 2 }, { c: "Pratt", a: "R. Nunes", d: 27, act: 6, sh: 7 },
  { c: "Okafor", a: "M. Teague", d: 31, act: 5, sh: 3 }, { c: "Lindsey", a: "P. Ord", d: 34, act: 21, sh: 1 },
  { c: "Haugen", a: "K. Frye", d: 38, act: 3, sh: 8 }, { c: "Merrill", a: "R. Nunes", d: 41, act: 17, sh: 2 },
  { c: "Bui", a: "M. Teague", d: 46, act: 8, sh: 4 }, { c: "Fontaine", a: "P. Ord", d: 49, act: 26, sh: 1 },
  { c: "Sowards", a: "K. Frye", d: 53, act: 11, sh: 5 }, { c: "Gallegos", a: "R. Nunes", d: 58, act: 4, sh: 9 },
  { c: "Ault", a: "M. Teague", d: 63, act: 33, sh: 2 }, { c: "Pham", a: "P. Ord", d: 67, act: 19, sh: 3 },
  { c: "Kirby", a: "K. Frye", d: 71, act: 41, sh: 1 }, { c: "Nakamura", a: "R. Nunes", d: 76, act: 7, sh: 6 },
  { c: "Osgood", a: "M. Teague", d: 81, act: 29, sh: 2 }, { c: "Vela", a: "P. Ord", d: 85, act: 38, sh: 1 },
  { c: "Rasmussen", a: "K. Frye", d: 88, act: 52, sh: 0 },
];

/* ── bands + math ────────────────────────────────────────────── */
function band(pct) {
  if (pct === null || pct === undefined || isNaN(pct)) return null;
  if (pct >= 100) return { bg: C.meadowBg, ink: C.meadowInk, bar: C.meadow };
  if (pct >= 80) return { bg: C.amberBg, ink: C.amberInk, bar: C.amber };
  return { bg: C.poppyBg, ink: C.poppyInk, bar: C.poppy };
}
const attainOf = (vals, r) => r.type === "rate"
  ? ((vals.reduce((a, b) => a + b, 0) / vals.length) / r.goal) * 100
  : (vals.reduce((a, b) => a + b, 0) / (r.goal * vals.length)) * 100;

function calc(r, win) {
  if (r.type === "snap" || !r.goal) return null;
  const vals = r.v.slice(-win).filter((x) => x !== null && x !== undefined);
  if (!vals.length) return null;
  const n = vals.length, best = Math.max(...vals);
  const c = { n, best, attain: attainOf(vals, r), rate: r.type === "rate" };
  if (c.rate) {
    const avg = vals.reduce((a, b) => a + b, 0) / n;
    c.avg = avg; c.gap = avg - r.goal;
    c.req = (r.goal * (n + WEEKS_LEFT) - avg * n) / WEEKS_LEFT;
  } else {
    const sum = vals.reduce((a, b) => a + b, 0), target = r.goal * n;
    c.sum = sum; c.target = target; c.gap = sum - target;
    c.req = r.goal + (c.gap < 0 ? -c.gap / WEEKS_LEFT : 0);
  }
  const all = r.v.filter((x) => x !== null && x !== undefined);
  if (all.length >= 8) c.dir = attainOf(all.slice(-4), r) - attainOf(all.slice(-8, -4), r);
  let s = 0;
  for (let i = r.v.length - 1; i >= 0; i--) {
    const x = r.v[i]; if (x === null || x === undefined) continue;
    if (x >= r.goal) break; s++;
  }
  c.streak = s;
  return c;
}
function verdict(c) {
  if (!c) return null;
  if (c.gap >= 0) return { t: "Ahead", ink: C.meadowInk, bg: C.meadowBg };
  if (c.req <= c.best) return { t: "Catchable", ink: C.meadowInk, bg: C.meadowBg };
  if (c.req <= c.best * 1.1) return { t: "Stretch", ink: C.amberInk, bg: C.amberBg };
  return { t: "Reset the goal", ink: C.poppyInk, bg: C.poppyBg };
}
const fmtV = (r, v) => v === null || v === undefined ? "-" : (r.type === "rate" || r.pct) ? `${v % 1 ? v.toFixed(1) : v}%` : String(v);
const sgn = (n, d = 0) => (n >= 0 ? "+" : "\u2212") + Math.abs(n).toFixed(d);

function focus(g, win) {
  const flows = g.rows.filter((r) => r.stage).map((r) => ({ r, c: calc(r, win) })).sort((a, b) => a.r.stage - b.r.stage);
  const constraint = flows.find((x) => x.c && x.c.attain < 100) || null;
  const behav = g.rows.filter((r) => r.lever === "behavior").map((r) => ({ r, c: calc(r, win) }))
    .filter((x) => x.c && x.c.attain < 100).sort((a, b) => a.c.attain - b.c.attain);
  return { constraint, freeWin: behav[0] || null };
}

/* ── parts ───────────────────────────────────────────────────── */
function Eyebrow({ children, color = C.muted }) {
  return <div style={{ fontFamily: FM, fontSize: 9.5, letterSpacing: ".15em", textTransform: "uppercase", color }}>{children}</div>;
}
function Card({ children, style, pad = 20 }) {
  return <div style={{ background: C.surface, border: `1px solid ${C.hair}`, borderRadius: 14, padding: pad,
    boxShadow: "0 1px 2px rgba(0,46,44,.04), 0 8px 24px -18px rgba(0,46,44,.35)", ...style }}>{children}</div>;
}
function Chip({ children, ink, bg, dash }) {
  return <span style={{ fontFamily: FM, fontSize: 8.5, letterSpacing: ".08em", padding: "3px 6px", borderRadius: 4,
    background: bg || C.hairSoft, color: ink || C.muted, border: dash ? `1px dashed ${C.hair}` : "none", whiteSpace: "nowrap" }}>{children}</span>;
}
function Avatar({ t }) {
  const unset = t === "??";
  return <span style={{ display: "inline-flex", alignItems: "center", justifyContent: "center", width: 22, height: 22,
    borderRadius: 99, fontFamily: FM, fontSize: 9, background: unset ? C.daffodilBg : C.mist,
    color: unset ? C.amberInk : C.teal, border: unset ? `1px dashed ${C.daffodil}` : "none" }}>{t}</span>;
}
function Bar({ pct, w }) {
  const b = band(pct), scale = Math.min(125, Math.max(0, pct));
  return (
    <div style={{ position: "relative", width: w || "100%", height: 9, background: C.hairSoft, borderRadius: 99, overflow: "hidden", flex: w ? "0 0 auto" : "1 1 auto", minWidth: 44 }}>
      <div style={{ width: `${(scale / 125) * 100}%`, height: "100%", background: b.bar, borderRadius: 99 }} />
      <div style={{ position: "absolute", left: "80%", top: 0, width: 1, height: "100%", background: C.slate, opacity: .4 }} />
    </div>
  );
}
function Dir({ d, showLabel }) {
  if (d === undefined || d === null) return <span style={{ fontFamily: FM, fontSize: 11, color: C.muted }}>-</span>;
  const flat = Math.abs(d) < 3, col = flat ? C.muted : d > 0 ? C.meadow : C.poppyInk;
  return (
    <span title={`Last 4 weeks vs the 4 before, ${sgn(d, 1)} points`}
      style={{ display: "inline-flex", alignItems: "center", gap: 3, fontFamily: FM, fontSize: 11, color: col, whiteSpace: "nowrap" }}>
      <span style={{ fontSize: 12, lineHeight: 1 }}>{flat ? "\u2192" : d > 0 ? "\u2191" : "\u2193"}</span>
      {!flat && <span>{Math.abs(d).toFixed(0)}{showLabel ? "pt" : ""}</span>}
    </span>
  );
}

/* ── this week's move ────────────────────────────────────────── */
function MoveCard({ g, win }) {
  const f = focus(g, win), cr = f.constraint, v = cr && verdict(cr.c);
  return (
    <Card style={{ flex: "1 1 300px", minWidth: 280, borderTop: `3px solid ${cr ? band(cr.c.attain).bar : C.meadow}` }} pad={18}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <div style={{ fontFamily: FD, fontSize: 13, fontWeight: 600, letterSpacing: ".07em", textTransform: "uppercase", color: C.ink }}>{g.name}</div>
        <span style={{ fontFamily: FB, fontSize: 11.5, color: g.owner === "unassigned" ? C.amberInk : C.slate }}>{g.owner}</span>
      </div>
      {cr ? (
        <>
          <div style={{ marginTop: 14 }}>
            <Eyebrow>The constraint</Eyebrow>
            <div style={{ fontFamily: FD, fontSize: 17, fontWeight: 600, color: C.ink, marginTop: 6 }}>{cr.r.m}</div>
            <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 7, marginBottom: 9 }}>
              <span style={{ fontFamily: FD, fontSize: 26, fontWeight: 600, color: band(cr.c.attain).ink, fontVariantNumeric: "tabular-nums" }}>{cr.c.attain.toFixed(0)}%</span>
              <Dir d={cr.c.dir} showLabel />
              <span style={{ fontFamily: FB, fontSize: 12, color: C.slate }}>{Math.abs(cr.c.gap)} behind over {cr.c.n} weeks</span>
            </div>
            <Bar pct={cr.c.attain} />
          </div>
          <div style={{ marginTop: 14, background: v.bg, borderRadius: 9, padding: "11px 13px" }}>
            <div style={{ fontFamily: FB, fontSize: 12.5, color: C.body, lineHeight: 1.55 }}>
              Closing this needs <strong style={{ fontWeight: 600, color: v.ink }}>{cr.c.req.toFixed(0)} a week</strong> for {WEEKS_LEFT} weeks. Best week in the window was {cr.c.best}.
            </div>
            <div style={{ marginTop: 8 }}><Chip ink={v.ink} bg={C.surface}>{v.t.toUpperCase()}</Chip></div>
          </div>
        </>
      ) : <div style={{ marginTop: 16, fontFamily: FB, fontSize: 13, color: C.meadowInk }}>Every funnel stage at or above goal.</div>}
      {f.freeWin && (
        <div style={{ marginTop: 14, paddingTop: 13, borderTop: `1px solid ${C.hairSoft}` }}>
          <Eyebrow>Winnable without more volume</Eyebrow>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 7, gap: 8 }}>
            <span style={{ fontFamily: FB, fontSize: 13, color: C.ink }}>{f.freeWin.r.m}</span>
            <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <Dir d={f.freeWin.c.dir} />
              <span style={{ fontFamily: FM, fontSize: 12, color: band(f.freeWin.c.attain).ink }}>{f.freeWin.c.attain.toFixed(0)}%</span>
            </span>
          </div>
          <div style={{ fontFamily: FB, fontSize: 11.5, color: C.slate, marginTop: 5, lineHeight: 1.5 }}>
            A conversation on deals already in hand. No extra appointments required.
          </div>
        </div>
      )}
    </Card>
  );
}

/* ── layout budget ───────────────────────────────────────────── */
/* percentages sum to 100 in every state, so freed width lands on
   the weeks rather than stretching the name column. */
function widths(cum, nWeeks, earlier) {
  if (!cum) {
    const fixed = [30, 3.5, 4.5, 2];
    return [...fixed, ...Array(nWeeks).fill((100 - 40) / nWeeks)];
  }
  const fixed = earlier ? [18, 3, 4, 8, 12.5, 4.5, 5.5, 14.5] : [18, 3, 4, 8, 13, 4.5, 5.5, 15];
  const tail = earlier ? [2.5] : [];                       // "+N earlier" sits at the far right
  const used = fixed.reduce((a, b) => a + b, 0) + (earlier ? 2.5 : 0);
  return [...fixed, ...Array(nWeeks).fill((100 - used) / nWeeks), ...tail];
}

function Row({ r, win, cum, weeks, offset, counted, earlier, open, toggle }) {
  const c = calc(r, win), b = c && band(c.attain), v = c && verdict(c);
  const na = <span style={{ fontFamily: FB, fontSize: 11.5, color: C.muted }}>{"\u2013"}</span>;
  const vals = r.v.slice(offset).reverse();   // render newest first; storage stays ascending
  const wk = weeks.slice().reverse();         // index aligned to vals

  return (
    <Fragment>
      <tr style={{ background: open ? C.hairSoft : "transparent" }}>
        <td style={{ ...tdL, paddingLeft: 12 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <button onClick={toggle} aria-label="Show detail" style={{ background: "none", border: "none", cursor: "pointer", padding: 0, width: 12,
              fontFamily: FM, fontSize: 12, color: C.muted, transform: open ? "rotate(90deg)" : "none", transition: "transform .15s" }}>{"\u203A"}</button>
            {r.stage && <span style={{ fontFamily: FM, fontSize: 9, color: C.muted }}>{r.stage}</span>}
            <span style={{ fontFamily: FB, fontSize: 13, color: C.ink }}>{r.m}</span>
            {r.type === "snap" && <Chip dash>SNAPSHOT</Chip>}
            {!SRC[r.src].auto && <Chip dash>HAND</Chip>}
            {c && c.streak >= 3 && <Chip ink={C.poppyInk} bg={C.poppyBg}>IDS {c.streak}</Chip>}
          </div>
          {r.note && <div style={{ fontFamily: FB, fontSize: 10.5, color: C.muted, marginTop: 2, paddingLeft: 20 }}>{r.note}</div>}
        </td>
        <td style={tdC}><Avatar t={r.own} /></td>
        <td style={{ ...tdC, fontFamily: FM, fontSize: 11.5, color: C.slate }}>{"\u2265"}{r.goal}{(r.type === "rate" || r.pct) ? "%" : ""}</td>

        {cum ? (
          <>
            <td style={{ ...cumCell, borderLeft: `1px solid ${C.hair}`, paddingLeft: 14, fontFamily: FM, fontSize: 11.5, color: C.body }}>
              {c ? (c.rate ? `${c.avg.toFixed(1)}% avg` : `${c.sum} of ${c.target}`) : na}
            </td>
            <td style={cumCell}>
              {c ? (
                <div style={{ display: "flex", alignItems: "center", gap: 11 }}>
                  <Bar pct={c.attain} />
                  <span style={{ fontFamily: FD, fontSize: 15, fontWeight: 600, color: b.ink, fontVariantNumeric: "tabular-nums", flex: "0 0 auto" }}>{c.attain.toFixed(0)}%</span>
                </div>
              ) : na}
            </td>
            <td style={{ ...cumCell, textAlign: "center" }}>{c ? <Dir d={c.dir} /> : na}</td>
            <td style={{ ...cumCell, textAlign: "right", fontFamily: FM, fontSize: 12, color: !c ? C.muted : c.gap >= 0 ? C.meadowInk : C.poppyInk }}>
              {c ? (c.rate ? `${sgn(c.gap, 1)} pt` : sgn(c.gap)) : na}
            </td>
            <td style={{ ...cumCell, paddingRight: 12 }}>
              {c ? (
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  {v.t !== "Ahead" && (
                    <span style={{ fontFamily: FM, fontSize: 11.5, color: C.slate, flex: "0 0 auto" }}>
                      {c.rate ? `${c.req.toFixed(0)}% avg` : `${c.req.toFixed(0)}/wk`}
                    </span>
                  )}
                  <Chip ink={v.ink} bg={v.bg}>{v.t.toUpperCase()}</Chip>
                </div>
              ) : <span style={{ fontFamily: FB, fontSize: 11.5, color: C.muted }}>not cumulative</span>}
            </td>
          </>
        ) : (
          <td style={{ padding: 0, borderBottom: `1px solid ${C.hairSoft}`, borderLeft: `1px solid ${C.hair}`, borderRight: `1px solid ${C.hair}`, textAlign: "center" }}>
            {c && <span title={`${c.attain.toFixed(0)}% cumulative`} style={{ display: "inline-block", width: 3, height: 16, borderRadius: 99, background: b.bar }} />}
          </td>
        )}

        {vals.map((val, i) => {
          const bb = val === null || val === undefined || !r.goal ? null : band((val / r.goal) * 100);
          const inWin = counted.has(wk[i].n);
          return (
            <td key={i} style={{ ...tdC, fontFamily: FD, fontSize: 13, fontWeight: 600, fontVariantNumeric: "tabular-nums",
              color: bb ? bb.ink : C.muted, background: bb ? bb.bg : "transparent", opacity: cum && !inWin ? .4 : 1 }}>
              {fmtV(r, val)}
            </td>
          );
        })}
        {earlier > 0 && <td style={{ padding: 0, borderBottom: `1px solid ${C.hairSoft}`, background: C.parchment }} />}
      </tr>

      {open && (
        <tr>
          <td colSpan={3 + (cum ? 5 + (earlier > 0 ? 1 : 0) : 1) + weeks.length} style={{ padding: 0, borderBottom: `1px solid ${C.hair}` }}>
            <div style={{ background: C.hairSoft, padding: "14px 22px 14px 34px", display: "flex", gap: 30, flexWrap: "wrap" }}>
              <div style={{ flex: "1 1 320px", minWidth: 260 }}>
                <Eyebrow>Direction</Eyebrow>
                <div style={{ fontFamily: FB, fontSize: 12.5, color: C.body, lineHeight: 1.6, marginTop: 7 }}>
                  {!c ? "Snapshot metric. It is a level, not a weekly count, so there is nothing to accumulate."
                    : c.dir === undefined ? "Not enough history to compare four week blocks."
                    : Math.abs(c.dir) < 3 ? "Flat. The last four weeks match the four before."
                    : c.dir > 0 ? `Improving. The last four weeks ran ${c.dir.toFixed(0)} points better than the four before.`
                    : `Still falling. The last four weeks ran ${Math.abs(c.dir).toFixed(0)} points worse than the four before, so the gap is widening.`}
                </div>
                {c && c.streak > 0 && (
                  <div style={{ fontFamily: FB, fontSize: 12.5, color: c.streak >= 3 ? C.poppyInk : C.slate, marginTop: 7 }}>
                    Off goal {c.streak} {c.streak === 1 ? "week" : "weeks"} running.{c.streak >= 3 ? " EOS says take it to IDS." : ""}
                  </div>
                )}
              </div>
              {c && (
                <div style={{ flex: "1 1 300px", minWidth: 260 }}>
                  <Eyebrow>The recovery maths</Eyebrow>
                  <div style={{ fontFamily: FB, fontSize: 12.5, color: C.body, lineHeight: 1.6, marginTop: 7 }}>
                    {c.gap >= 0
                      ? `Running ${sgn(c.gap, c.rate ? 1 : 0)}${c.rate ? " points" : ""} above goal across ${c.n} weeks. Hold ${r.goal}${c.rate ? "%" : ""} a week and it stays there.`
                      : `${c.req.toFixed(0)}${c.rate ? "%" : ""} a week for ${WEEKS_LEFT} weeks closes the ${Math.abs(c.gap).toFixed(c.rate ? 1 : 0)}${c.rate ? " point" : ""} gap. The best single week in this window was ${c.best}${c.rate ? "%" : ""}.`}
                  </div>
                </div>
              )}
              <div style={{ flex: "0 1 170px" }}>
                <Eyebrow>Source</Eyebrow>
                <div style={{ fontFamily: FB, fontSize: 12.5, color: C.body, marginTop: 7 }}>{SRC[r.src].label}</div>
                <div style={{ fontFamily: FB, fontSize: 11.5, color: C.muted, marginTop: 3 }}>
                  {SRC[r.src].auto ? "Synced 6:04am" : "No feed, typed each Monday"}
                </div>
              </div>
            </div>
          </td>
        </tr>
      )}
    </Fragment>
  );
}

function Scorecard() {
  const [win, setWin] = useState(8);
  const [cum, setCum] = useState(true);
  const [hideOk, setHideOk] = useState(false);
  const [open, setOpen] = useState({});

  const vis = cum ? VIS_OPEN : VIS_SHUT;
  const weeks = WEEKS.slice(-vis);            // ascending, for the math
  const weeksDesc = weeks.slice().reverse();  // descending, for the header
  const offset = WEEKS.length - vis;
  const counted = new Set(WEEKS.slice(-win).map((w) => w.n));
  const earlier = cum ? Math.max(0, win - vis) : 0;
  const qtd = win === 1;
  const cols = widths(cum, weeks.length, earlier);
  const NCOL = 3 + (cum ? 5 + (earlier > 0 ? 1 : 0) : 1) + weeks.length;
  const shown = (g) => hideOk ? g.rows.filter((r) => { const c = calc(r, win); return !c || c.attain < 100; }) : g.rows;

  return (
    <>
      <div style={{ display: "flex", gap: 16, flexWrap: "wrap", marginBottom: 18 }}>
        {GROUPS.filter((g) => g.key !== "overall").map((g) => <MoveCard key={g.key} g={g} win={win} />)}
      </div>

      <Card pad={0}>
        <div style={{ padding: "15px 20px", borderBottom: `1px solid ${C.hair}`, display: "flex", justifyContent: "space-between", alignItems: "center", gap: 14, flexWrap: "wrap" }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
            <span style={{ fontFamily: FD, fontSize: 15, fontWeight: 600, color: C.ink }}>L10 Scorecard</span>
            <span style={{ fontFamily: FB, fontSize: 11.5, color: C.slate }}>Week 30 closed Aug 3. Click any measurable for the detail.</span>
          </div>
          <label style={{ display: "flex", alignItems: "center", gap: 7, fontFamily: FB, fontSize: 12, color: C.slate, cursor: "pointer" }}>
            <input type="checkbox" checked={hideOk} onChange={(e) => setHideOk(e.target.checked)} />
            Only what is off
          </label>
        </div>

        {qtd && cum && (
          <div style={{ background: C.daffodilBg, borderBottom: `1px solid ${C.hair}`, padding: "13px 20px" }}>
            <Eyebrow color={C.amberInk}>Why this panel just went quiet</Eyebrow>
            <div style={{ fontFamily: FB, fontSize: 12.5, color: C.body, lineHeight: 1.55, marginTop: 6 }}>
              The quarter turned over Aug 3, so quarter to date is one week deep. It resets the cumulative read to zero at the exact moment
              an eight week hole matters most. Keep quarter to date for the rock review. Use the rolling window for the L10.
            </div>
          </div>
        )}

        <div style={{ overflowX: "auto" }}>
          <table style={{ borderCollapse: "collapse", width: "100%", tableLayout: "fixed", minWidth: cum ? 1120 : 860 }}>
            <colgroup>{cols.map((w, i) => <col key={i} style={{ width: `${w}%` }} />)}</colgroup>
            <thead>
              <tr>
                <th colSpan={3} style={{ ...groupTh, textAlign: "left", paddingLeft: 12 }}>Measurable</th>
                {cum ? (
                  <th colSpan={5 + (earlier > 0 ? 1 : 0)} style={{ ...groupTh, background: C.mist, borderLeft: `1px solid ${C.hair}`, borderRight: `1px solid ${C.hair}`, paddingLeft: 14, textAlign: "left" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 11, flexWrap: "wrap" }}>
                      <button onClick={() => setCum(false)} style={panelBtn} aria-expanded="true" title="Shut the panel and show more weeks">
                        <span style={{ fontSize: 12, lineHeight: 1 }}>{"\u2039"}</span> Cumulative
                      </button>
                      <span style={{ display: "flex", gap: 5 }}>
                        {[[4, "4 wks"], [8, "8 wks"], [1, "QTD"]].map(([k, l]) => (
                          <button key={k} onClick={() => setWin(k)} style={segBtn(win === k)}>{l}</button>
                        ))}
                      </span>
                      <span style={{ fontFamily: FB, fontSize: 11, textTransform: "none", letterSpacing: 0, color: C.slate, fontWeight: 400 }}>
                        {earlier > 0 ? `${win} weeks counted, ${vis} shown` : "totals across the weeks marked below"}
                      </span>
                    </div>
                  </th>
                ) : (
                  <th style={{ ...groupTh, background: C.mist, borderLeft: `1px solid ${C.hair}`, borderRight: `1px solid ${C.hair}`, padding: 0 }}>
                    <button onClick={() => setCum(true)} title="Open the cumulative panel" style={{ ...panelBtn, padding: "6px 2px", width: "100%", justifyContent: "center" }} aria-expanded="false">
                      <span style={{ fontSize: 12, lineHeight: 1 }}>{"\u203A"}</span>
                    </button>
                  </th>
                )}
                <th colSpan={weeks.length} style={groupTh}>
                  Week by week {"\u00b7"} newest first {"\u00b7"} last {vis}
                </th>
              </tr>
              <tr>
                <th style={{ ...thL, paddingLeft: 32 }} />
                <th style={thC}>Own</th>
                <th style={thC}>Goal</th>
                {cum ? (
                  <>
                    <th style={{ ...thCum, borderLeft: `1px solid ${C.hair}`, paddingLeft: 14 }}>Actual</th>
                    <th style={thCum}>Pace to goal</th>
                    <th style={{ ...thCum, textAlign: "center" }}>4 wk</th>
                    <th style={{ ...thCum, textAlign: "right" }}>Gap</th>
                    <th style={{ ...thCum, paddingRight: 12 }}>To recover</th>
                  </>
                ) : (
                  <th style={{ ...thCum, padding: 0, borderLeft: `1px solid ${C.hair}`, borderRight: `1px solid ${C.hair}` }} />
                )}
                {weeksDesc.map((w) => {
                  const inWin = cum && counted.has(w.n);
                  return (
                    <th key={w.n} style={{ ...thC, background: inWin ? C.parchment : "transparent",
                      borderBottom: inWin ? `2px solid ${C.evergreen}` : `1px solid ${C.hair}` }}>
                      <div style={{ fontFamily: FM, fontSize: 9.5, color: inWin || !cum ? C.ink : C.muted }}>W{w.n}</div>
                      <div style={{ fontFamily: FB, fontSize: 10, color: C.muted, fontWeight: 400 }}>{w.d}</div>
                    </th>
                  );
                })}
                {earlier > 0 && (
                  <th title={`${earlier} counted weeks older than W${weeks[0].n}`}
                    style={{ ...thC, background: C.parchment, borderBottom: `2px solid ${C.evergreen}`, padding: "8px 2px" }}>
                    <div style={{ fontFamily: FM, fontSize: 9, color: C.ink }}>+{earlier}</div>
                    <div style={{ fontFamily: FB, fontSize: 9.5, color: C.muted, fontWeight: 400 }}>older</div>
                  </th>
                )}
              </tr>
            </thead>
            <tbody>
              {GROUPS.map((g) => {
                const rows = shown(g);
                return (
                  <Fragment key={g.key}>
                    <tr>
                      <td colSpan={NCOL} style={{ padding: 0 }}>
                        <div style={{ background: C.evergreen, padding: "9px 14px" }}>
                          <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
                            <span style={{ fontFamily: FD, fontSize: 12, fontWeight: 600, letterSpacing: ".07em", textTransform: "uppercase", color: C.onDark }}>{g.name}</span>
                            <span style={{ fontFamily: FB, fontSize: 11, color: g.owner === "unassigned" ? C.daffodil : C.onDarkMute }}>{g.owner}</span>
                            {g.read && <span style={{ fontFamily: FB, fontSize: 11.5, color: C.onDarkMute, lineHeight: 1.45, flex: "1 1 320px", minWidth: 240 }}>{g.read}</span>}
                          </div>
                        </div>
                      </td>
                    </tr>
                    {rows.length === 0 && (
                      <tr><td colSpan={NCOL} style={{ ...tdC, fontFamily: FB, fontSize: 12, color: C.meadowInk, padding: "14px 8px" }}>Everything at or above goal.</td></tr>
                    )}
                    {rows.map((r) => {
                      const id = g.key + ":" + r.m;
                      return <Row key={id} r={r} win={win} cum={cum} weeks={weeks} offset={offset} counted={counted}
                        earlier={earlier} open={!!open[id]} toggle={() => setOpen((o) => ({ ...o, [id]: !o[id] }))} />;
                    })}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>

        <div style={{ padding: "13px 20px 16px", borderTop: `1px solid ${C.hair}`, display: "flex", gap: 18, flexWrap: "wrap", alignItems: "center", fontFamily: FB, fontSize: 11.5, color: C.slate }}>
          <Legend b={band(105)} label="At or above" />
          <Legend b={band(90)} label="80 to 99" />
          <Legend b={band(60)} label="Under 80" />
          <span style={{ color: C.muted }}>
            {cum
              ? `Weeks run newest first. Panel open, so the grid holds the last ${vis} weeks and the panel carries the longer history. Weeks under the dark rule feed the totals.`
              : `Weeks run newest first. Panel shut, so all ${vis} weeks are on screen. The coloured tick still carries each row's pace to goal.`}
          </span>
        </div>
      </Card>
    </>
  );
}
function Legend({ b, label }) {
  return <span style={{ display: "inline-flex", alignItems: "center", gap: 7 }}>
    <span style={{ width: 15, height: 15, borderRadius: 4, background: b.bg, border: `1px solid ${b.bar}` }} />{label}</span>;
}
const segBtn = (on) => ({ fontFamily: FB, fontSize: 11.5, padding: "4px 10px", borderRadius: 6, cursor: "pointer", letterSpacing: 0, textTransform: "none",
  border: `1px solid ${on ? C.evergreen : C.hair}`, background: on ? C.evergreen : C.surface, color: on ? C.onDark : C.slate, fontWeight: 400 });
const panelBtn = { display: "inline-flex", alignItems: "center", gap: 5, fontFamily: FM, fontSize: 9.5, letterSpacing: ".13em", textTransform: "uppercase",
  color: C.ink, background: "none", border: "none", cursor: "pointer", padding: 0 };

/* ── team room ───────────────────────────────────────────────── */
function Pace({ p }) {
  const required = Math.round((p.target / DAYS) * DAY);
  const delta = p.actual - required, projected = Math.round((p.actual / DAY) * DAYS);
  const perDay = (p.target - p.actual) / (DAYS - DAY), b = band((p.actual / required) * 100);
  const W = 300, H = 96, x = (i) => (i / (DAYS - 1)) * W, y = (v) => H - (v / p.target) * H;
  let act = ""; p.cum.forEach((v, i) => { act += `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(v).toFixed(1)} `; });
  return (
    <Card style={{ background: C.evergreen, border: "none", flex: "1 1 380px", minWidth: 300 }} pad={22}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12 }}>
        <div><Eyebrow color={C.onDarkMute}>Daily pace metric</Eyebrow>
          <div style={{ fontFamily: FD, fontSize: 17, fontWeight: 500, color: C.onDark, marginTop: 7 }}>{p.label}</div></div>
        <Chip ink={C.onDarkMute} bg="rgba(255,255,255,.07)">{SRC[p.src].label.toUpperCase()}</Chip>
      </div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, margin: "16px 0 2px" }}>
        <span style={{ fontFamily: FD, fontSize: 52, fontWeight: 600, color: b.bar, lineHeight: 1, fontVariantNumeric: "tabular-nums" }}>{p.actual}</span>
        <span style={{ fontFamily: FD, fontSize: 20, color: C.onDarkMute }}>/ {p.target}</span>
        <span style={{ marginLeft: "auto", fontFamily: FD, fontSize: 18, fontWeight: 600, color: b.bar }}>{Math.round((p.actual / required) * 100)}%</span>
      </div>
      <div style={{ fontFamily: FB, fontSize: 12.5, color: C.onDarkMute, marginBottom: 16 }}>
        {delta >= 0 ? `${delta} ahead of` : `${Math.abs(delta)} behind`} the {required} needed by day {DAY}
      </div>
      <svg width="100%" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" style={{ display: "block", height: 96 }} aria-hidden="true">
        <path d={`M0 ${H} L${W} 0`} fill="none" stroke="rgba(159,180,174,.4)" strokeWidth="1.2" strokeDasharray="3 3" />
        <path d={`M${x(DAY - 1)} ${y(p.cum[p.cum.length - 1])} L${W} ${y(projected)}`} fill="none" stroke={b.bar} strokeWidth="1.4" strokeDasharray="3 3" opacity=".65" />
        <path d={act} fill="none" stroke={b.bar} strokeWidth="2.4" strokeLinejoin="round" strokeLinecap="round" />
        <circle cx={x(DAY - 1)} cy={y(p.cum[p.cum.length - 1])} r="3.4" fill={b.bar} />
      </svg>
      <div style={{ display: "flex", justifyContent: "space-between", fontFamily: FM, fontSize: 9, color: C.onDarkMute, marginTop: 5 }}>
        <span>AUG 1</span><span>DAY {DAY} OF {DAYS}</span><span>AUG 31</span>
      </div>
      <div style={{ display: "flex", gap: 22, marginTop: 18, paddingTop: 16, borderTop: "1px solid rgba(159,180,174,.18)", flexWrap: "wrap" }}>
        <Stat dark l="Lands at this rate" v={projected} sub={projected >= p.target ? "over" : `short by ${p.target - projected}`} />
        <Stat dark l="Needed per day now" v={perDay.toFixed(1)} sub={`over ${DAYS - DAY} days left`} />
      </div>
    </Card>
  );
}
function Stat({ l, v, sub, dark }) {
  return (<div>
    <div style={{ fontFamily: FM, fontSize: 9, letterSpacing: ".12em", textTransform: "uppercase", color: dark ? C.onDarkMute : C.muted }}>{l}</div>
    <div style={{ fontFamily: FD, fontSize: 22, fontWeight: 600, color: dark ? C.onDark : C.ink, marginTop: 5, fontVariantNumeric: "tabular-nums" }}>{v}</div>
    {sub && <div style={{ fontFamily: FB, fontSize: 11, color: dark ? C.onDarkMute : C.slate, marginTop: 2 }}>{sub}</div>}
  </div>);
}
function Rock({ r }) {
  const pct = (r.have / r.need) * 100, b = band(pct);
  return (
    <Card style={{ flex: "1 1 250px", minWidth: 240 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 10 }}>
        <Eyebrow>Rock, due {r.due}</Eyebrow>{!SRC[r.src].auto && <Chip dash>HAND</Chip>}
      </div>
      <div style={{ fontFamily: FD, fontSize: 15, fontWeight: 500, color: C.ink, margin: "9px 0 14px", lineHeight: 1.35 }}>{r.title}</div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 7 }}>
        <span style={{ fontFamily: FD, fontSize: 32, fontWeight: 600, color: b.ink, lineHeight: 1, fontVariantNumeric: "tabular-nums" }}>{r.have}</span>
        <span style={{ fontFamily: FD, fontSize: 15, color: C.slate }}>of {r.need}</span>
        <span style={{ marginLeft: "auto", fontFamily: FD, fontSize: 14, fontWeight: 600, color: b.ink }}>{Math.round(pct)}%</span>
      </div>
      <div style={{ marginTop: 11, display: "flex" }}><Bar pct={pct} /></div>
      {r.prior !== undefined && <div style={{ fontFamily: FB, fontSize: 11.5, color: C.slate, marginTop: 8 }}>
        Up from {r.prior} last week. {r.need - r.have} more to clear it, {r.of} agents on the team.</div>}
      {r.detail && <div style={{ marginTop: 14, paddingTop: 12, borderTop: `1px solid ${C.hairSoft}` }}>
        {r.detail.map((d, i) => (
          <div key={i} style={{ display: "flex", justifyContent: "space-between", padding: "5px 0", fontFamily: FB, fontSize: 12 }}>
            <span style={{ color: i === 0 ? C.meadowInk : C.slate }}>{d.n}</span>
            <span style={{ fontFamily: FM, fontSize: 11.5, color: C.ink }}>{d.v}</span></div>))}
      </div>}
      {r.sessions && <div style={{ marginTop: 14, paddingTop: 12, borderTop: `1px solid ${C.hairSoft}` }}>
        {r.sessions.map((s, i) => (
          <div key={i} style={{ marginBottom: 10 }}>
            <div style={{ display: "flex", justifyContent: "space-between", fontFamily: FB, fontSize: 12, color: C.slate }}>
              <span>{s.d}</span><span style={{ fontFamily: FM, fontSize: 11, color: C.ink }}>{s.seats}</span></div>
            <div style={{ display: "flex", gap: 5, flexWrap: "wrap", marginTop: 6 }}>
              {s.names.map((n) => <span key={n} style={{ fontFamily: FB, fontSize: 11, padding: "3px 8px", borderRadius: 99, background: C.meadowBg, color: C.meadowInk }}>{n}</span>)}
            </div></div>))}
        <div style={{ fontFamily: FB, fontSize: 11.5, color: C.amberInk, background: C.amberBg, padding: "7px 9px", borderRadius: 7 }}>
          {r.pipeline} in conversation, none registered. Registering all four still leaves one seat short.
        </div></div>}
    </Card>
  );
}
function BbaTrack() {
  const [hover, setHover] = useState(null);
  const W = 700, ROW = 13, PAD = 8, cols = 46, stacks = {};
  const placed = BBA.map((b) => { const col = Math.min(cols - 1, Math.floor((b.d / 90) * cols)); stacks[col] = (stacks[col] || 0) + 1; return { ...b, col, lvl: stacks[col] - 1 }; });
  const H = (Math.max(...placed.map((p) => p.lvl)) + 1) * ROW + PAD * 2;
  const buckets = [
    { l: "0 - 30 days", n: BBA.filter((b) => b.d <= 30).length, note: "still warm" },
    { l: "31 - 60 days", n: BBA.filter((b) => b.d > 30 && b.d <= 60).length, note: "needs a touch" },
    { l: "61 - 90 days", n: BBA.filter((b) => b.d > 60).length, note: "aging out" },
  ];
  const dot = (b) => (b.act <= 7 ? C.meadow : b.act <= 21 ? C.amber : C.poppy);
  const stale = BBA.filter((b) => b.act > 21).length;
  return (
    <Card pad={0}>
      <div style={{ padding: "18px 20px 0", display: "flex", justifyContent: "space-between", alignItems: "flex-end", gap: 14, flexWrap: "wrap" }}>
        <div>
          <Eyebrow>Worklist, not a metric</Eyebrow>
          <div style={{ fontFamily: FD, fontSize: 19, fontWeight: 600, color: C.ink, marginTop: 6 }}>Signed a BBA, no contract yet</div>
          <div style={{ fontFamily: FB, fontSize: 12.5, color: C.slate, marginTop: 4, maxWidth: 560, lineHeight: 1.5 }}>
            {BBA.length} clients. Position is days since signing, colour is days since last activity.
            {" "}<strong style={{ color: C.poppyInk, fontWeight: 600 }}>{stale} have gone quiet for three weeks or more</strong> - appointments already earned and not being kept.
          </div>
        </div>
        <div style={{ display: "flex", gap: 20 }}>
          {buckets.map((b, i) => (
            <div key={i} style={{ textAlign: "right" }}>
              <div style={{ fontFamily: FD, fontSize: 22, fontWeight: 600, color: i === 2 ? C.poppyInk : C.ink, fontVariantNumeric: "tabular-nums" }}>{b.n}</div>
              <div style={{ fontFamily: FM, fontSize: 9, letterSpacing: ".08em", color: C.muted, marginTop: 2 }}>{b.l.toUpperCase()}</div>
              <div style={{ fontFamily: FB, fontSize: 11, color: C.slate }}>{b.note}</div></div>))}
        </div>
      </div>
      <div style={{ padding: "20px 20px 6px" }}>
        <svg width="100%" viewBox={`0 0 ${W} ${H}`} style={{ display: "block" }}>
          <rect x="0" y="0" width={W} height={H} rx="8" fill={C.parchment} />
          <rect x={(60 / 90) * W} y="0" width={(30 / 90) * W} height={H} fill={C.poppyBg} opacity=".6" />
          {[30, 60].map((d) => <line key={d} x1={(d / 90) * W} y1="0" x2={(d / 90) * W} y2={H} stroke={C.hair} strokeWidth="1" />)}
          {placed.map((b, i) => {
            const cw = W / cols, cx = b.col * cw + cw / 2, cy = H - PAD - b.lvl * ROW - ROW / 2, on = hover === b.c;
            return (<g key={i} onMouseEnter={() => setHover(b.c)} onMouseLeave={() => setHover(null)} style={{ cursor: "pointer" }}>
              <rect x={cx - cw / 2} y={cy - ROW / 2} width={cw} height={ROW} fill="transparent" />
              <circle cx={cx} cy={cy} r={on ? 6 : 4.4} fill={dot(b)} stroke={on ? C.ink : "none"} strokeWidth="1.2" /></g>);
          })}
        </svg>
        <div style={{ display: "flex", justifyContent: "space-between", fontFamily: FM, fontSize: 9, color: C.muted, marginTop: 6 }}>
          <span>SIGNED TODAY</span><span>30 DAYS</span><span>60 DAYS</span><span>90 DAYS</span></div>
        {hover && (() => { const b = BBA.find((x) => x.c === hover); return (
          <div style={{ fontFamily: FB, fontSize: 12, color: C.ink, marginTop: 10, background: C.hairSoft, padding: "8px 11px", borderRadius: 7 }}>
            <strong style={{ fontWeight: 600 }}>{b.c}</strong> with {b.a} - signed {b.d} days ago, {b.sh} showings, last activity {b.act} days ago
          </div>); })()}
      </div>
      <div style={{ overflowX: "auto", marginTop: 8 }}>
        <table style={{ borderCollapse: "collapse", width: "100%", minWidth: 560 }}>
          <thead><tr>
            <th style={thL}>Client</th><th style={thL}>Agent</th>
            <th style={{ ...thC, textAlign: "right" }}>Days signed</th><th style={{ ...thC, textAlign: "right" }}>Showings</th>
            <th style={{ ...thC, textAlign: "right" }}>Last activity</th><th style={{ ...thC, textAlign: "right", paddingRight: 20 }}>Days left</th>
          </tr></thead>
          <tbody>
            {[...BBA].sort((a, b) => b.d - a.d).slice(0, 8).map((b, i) => (
              <tr key={i} onMouseEnter={() => setHover(b.c)} onMouseLeave={() => setHover(null)} style={{ background: hover === b.c ? C.hairSoft : "transparent" }}>
                <td style={{ ...tdL, fontWeight: 500 }}>{b.c}</td><td style={{ ...tdL, color: C.slate }}>{b.a}</td>
                <td style={{ ...tdC, textAlign: "right", fontFamily: FM, fontSize: 11.5 }}>{b.d}</td>
                <td style={{ ...tdC, textAlign: "right", fontFamily: FM, fontSize: 11.5, color: b.sh === 0 ? C.poppyInk : C.body }}>{b.sh}</td>
                <td style={{ ...tdC, textAlign: "right", fontFamily: FM, fontSize: 11.5, color: b.act > 21 ? C.poppyInk : C.body }}>{b.act}d</td>
                <td style={{ ...tdC, textAlign: "right", paddingRight: 20 }}>
                  <span style={{ fontFamily: FM, fontSize: 11, padding: "3px 7px", borderRadius: 5,
                    background: 90 - b.d <= 14 ? C.poppyBg : "transparent", color: 90 - b.d <= 14 ? C.poppyInk : C.slate }}>{90 - b.d}</span>
                </td></tr>))}
          </tbody>
        </table>
      </div>
      <div style={{ padding: "12px 20px 16px", borderTop: `1px solid ${C.hair}`, fontFamily: FB, fontSize: 11.5, color: C.slate }}>
        Showing the 8 oldest. Sisu buyer agreements with no matching contract record, refreshed each morning.
      </div>
    </Card>
  );
}
function TeamRoom({ k }) {
  const room = ROOMS[k], g = GROUPS.find((x) => x.key === k);
  if (!room.committed) return (
    <Card style={{ borderLeft: `3px solid ${C.daffodil}`, maxWidth: 620 }}>
      <Eyebrow color={C.amberInk}>Waiting on the leader</Eyebrow>
      <div style={{ fontFamily: FD, fontSize: 19, fontWeight: 600, color: C.ink, margin: "9px 0 8px" }}>{room.name} has no August commitments yet</div>
      <div style={{ fontFamily: FB, fontSize: 13, color: C.slate, lineHeight: 1.6, marginBottom: 16 }}>
        The room is built. It fills in the moment the {room.name} leader answers the same four questions Justin did.
      </div>
      <div style={{ background: C.parchment, borderRadius: 10, padding: "14px 16px" }}>
        {["One number you will read every day to know if you are on pace",
          "One rock with a percentage or a count and a date",
          "One rock that is a list of names",
          "One list of people who are stuck and need to move"].map((q, i) => (
          <div key={i} style={{ display: "flex", gap: 10, padding: "7px 0", fontFamily: FB, fontSize: 12.5, color: C.body }}>
            <span style={{ fontFamily: FM, fontSize: 10, color: C.muted, paddingTop: 2 }}>{String(i + 1).padStart(2, "0")}</span><span>{q}</span>
          </div>))}
      </div>
    </Card>
  );
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {g && <MoveCard g={g} win={8} />}
      <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
        <Pace p={room.pace} />
        <div style={{ flex: "1 1 300px", minWidth: 260, display: "flex", flexDirection: "column", gap: 16 }}>
          {room.rocks.map((r, i) => <Rock key={i} r={r} />)}
        </div>
      </div>
      <BbaTrack />
    </div>
  );
}

/* ── shell ───────────────────────────────────────────────────── */
const thBase = { fontFamily: FM, fontSize: 9, letterSpacing: ".1em", textTransform: "uppercase", color: C.muted, fontWeight: 400, padding: "8px 6px", borderBottom: `1px solid ${C.hair}` };
const thL = { ...thBase, textAlign: "left", paddingLeft: 14 };
const thC = { ...thBase, textAlign: "center" };
const thCum = { ...thBase, textAlign: "left", background: C.parchment, color: C.slate };
const groupTh = { fontFamily: FM, fontSize: 9, letterSpacing: ".14em", textTransform: "uppercase", color: C.muted, fontWeight: 400,
  padding: "9px 8px", textAlign: "center", borderBottom: `1px solid ${C.hair}`, background: C.hairSoft };
const tdBase = { padding: "10px 6px", borderBottom: `1px solid ${C.hairSoft}`, fontFamily: FB, fontSize: 12.5, color: C.body };
const tdL = { ...tdBase, textAlign: "left", paddingLeft: 14 };
const tdC = { ...tdBase, textAlign: "center" };
const cumCell = { padding: "10px 8px", borderBottom: `1px solid ${C.hairSoft}`, background: C.parchment, textAlign: "left", whiteSpace: "nowrap" };

const TABS = [
  { k: "scorecard", label: "Scorecard", sub: "All teams" },
  { k: "davis", label: "Davis", sub: "Justin" },
  { k: "slc", label: "SLC", sub: "Unassigned" },
  { k: "utco", label: "Utah County", sub: "Unassigned" },
];

export default function TeamCommand() {
  const [tab, setTab] = useState("scorecard");
  const [embed, setEmbed] = useState(false);
  return (
    <div style={{ background: C.page, minHeight: "100vh", fontFamily: FB, color: C.body }}>
      <div style={{ maxWidth: 1320, margin: "0 auto", padding: "26px 22px 60px" }}>
        <header style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 18, flexWrap: "wrap", marginBottom: 22 }}>
          <div>
            <Eyebrow>Utah Life Real Estate Group</Eyebrow>
            <h1 style={{ fontFamily: FD, fontSize: 30, fontWeight: 600, color: C.ink, margin: "9px 0 5px", letterSpacing: "-.01em" }}>Team Command</h1>
            <div style={{ fontFamily: FB, fontSize: 13, color: C.slate }}>
              Week 30 closed Aug 3 - the week that just happened, and the hole it sits in
            </div>
          </div>
          <button onClick={() => setEmbed(!embed)} style={segBtn(embed)}>Embed in ClickUp</button>
        </header>

        {embed && (
          <Card style={{ marginBottom: 18, borderLeft: `3px solid ${C.teal}` }}>
            <Eyebrow color={C.teal}>Read only share</Eyebrow>
            <div style={{ fontFamily: FB, fontSize: 13, color: C.body, lineHeight: 1.6, marginTop: 9 }}>
              A share link renders this view with no nav and no auth prompt, scoped to one team or the whole scorecard. Paste it into a ClickUp embed view.
            </div>
            <div style={{ fontFamily: FM, fontSize: 11.5, background: C.parchment, padding: "10px 12px", borderRadius: 7, marginTop: 12, color: C.ink, wordBreak: "break-all" }}>
              https://ulrg.acumyn.io/share/{tab === "scorecard" ? "scorecard" : tab}/k_7f2a91c4
            </div>
          </Card>
        )}

        <nav style={{ display: "flex", gap: 4, marginBottom: 22, borderBottom: `1px solid ${C.hair}`, overflowX: "auto" }}>
          {TABS.map((t) => {
            const on = tab === t.k;
            return (
              <button key={t.k} onClick={() => setTab(t.k)} style={{ background: "none", border: "none",
                borderBottom: `2px solid ${on ? C.evergreen : "transparent"}`, padding: "9px 15px 12px", cursor: "pointer",
                textAlign: "left", whiteSpace: "nowrap", marginBottom: -1 }}>
                <div style={{ fontFamily: FD, fontSize: 14, fontWeight: on ? 600 : 500, color: on ? C.ink : C.slate }}>{t.label}</div>
                <div style={{ fontFamily: FB, fontSize: 11, color: t.sub === "Unassigned" ? C.amberInk : C.muted, marginTop: 2 }}>{t.sub}</div>
              </button>);
          })}
        </nav>

        {tab === "scorecard" ? <Scorecard /> : (
          <>
            <div style={{ display: "flex", alignItems: "center", gap: 11, marginBottom: 16, flexWrap: "wrap" }}>
              <Avatar t={ROOMS[tab].initials} />
              <span style={{ fontFamily: FD, fontSize: 15, fontWeight: 500, color: C.ink }}>
                {ROOMS[tab].leader === "unassigned" ? `${ROOMS[tab].name}, no leader set` : ROOMS[tab].leader}
              </span>
              {ROOMS[tab].committed && <Chip ink={C.teal} bg={C.mist}>COMMITTED {ROOMS[tab].committed.toUpperCase()}</Chip>}
            </div>
            <TeamRoom k={tab} />
          </>
        )}

        <div style={{ marginTop: 34, paddingTop: 18, borderTop: `1px solid ${C.hair}`, fontFamily: FB, fontSize: 11.5, color: C.muted, lineHeight: 1.6 }}>
          Mockup, sample data. Scorecard values mirror the live sheet through Week 30. Recovery math assumes {WEEKS_LEFT} weeks left in the quarter.
        </div>
      </div>
    </div>
  );
}
