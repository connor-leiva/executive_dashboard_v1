import { useState } from "react";

/* ──────────────────────────────────────────────────────────────
   The Forum — focused business view (mockup, v2)
   Changes from v1:
   · Color: poppy/red is gone from this view. Structure is
     evergreen, positive progress is meadow, and DAFFODIL is the
     one "needs attention today" highlight. Watch states are
     amber. Nothing reads as alarm.
   · Layout: the four deep dives (Pipeline / Renewals / Event /
     Revenue quality) collapse into an equal-sized summary deck —
     hero stat + micro-visual + one salient line. Selecting a
     card expands its full detail below (the selector → focus
     pattern from the ULRG financials mockup). Row 1 is a fixed
     5/7 grid so card sizes stay consistent.
   · Motion: CSS-only. Fade-up on expansion, a daffodil underline
     that grows under the selected card, gentle hover lifts, a
     soft pulse on the watch dot. prefers-reduced-motion honored.
   Anchored to live numbers as of Jul 2, 2026: 70 members
   (Forum 26 · IC 44) · $1.2M ARR · 47 memberships · 28 registered
   for Park City · $31K MRR. All figures representative.
   ────────────────────────────────────────────────────────────── */

const T = {
  evergreen: "#002E2C",
  meadow: "#61835E",
  meadowInk: "#4F6A4D",
  meadowBg: "#E9EFE7",
  sprout: "#B8CCB8",
  parchment: "#F8F5F2",
  line: "#E8E0D4",
  white: "#FFFFFF",
  petal: "#FFBA9F",
  mist: "#DCE7E9",
  teal: "#227175",
  daffodil: "#FFF3AD",
  daffodilBg: "#FFF9D6",
  amber: "#9C6A1E",
  amberBg: "#F5EAD3",
  ink: "#002E2C",
  slate: "#56655C",
  muted: "#8A968C",
  onDark: "#F3EEE7",
  onDarkMute: "#9CB0AB",
};

const ACCENT = T.evergreen; // structure: label ticks, bars
const GOOD = T.meadow;      // positive progress fills

/* ── data · KPI tiles (row 1, matches the live panel) ────────── */

const KPIS = [
  { label: "Active Members", value: "70", sub: "Forum 26 · Inner Circle 44", drill: "roster" },
  { label: "Forum ARR", value: "$1.2M", sub: "47 memberships", drill: "contracts" },
  { label: "New Members", value: "0", sub: "month to date" },
  { label: "Renewals Due", value: "0", sub: "July", drill: "renewals" },
  { label: "Registered", value: "28", sub: "Park City, UT", drill: "registered" },
  { label: "MRR", value: "$31K", sub: "monthly subscriptions", drill: "monthly" },
];

/* ── data · recruiting pipeline (GHL sales funnel, current) ──── */

const FUNNEL = {
  stages: [
    { label: "Applied", v: 14, value: "$392K" },
    { label: "Discovery call booked", v: 9, value: "$252K" },
    { label: "Call held", v: 6, value: "$168K" },
    { label: "Invited · agreement out", v: 3, value: "$84K" },
  ],
  footer: "Q2 cohort: 31 applications → 7 onboarded · 23% application-to-member · avg 21 days to close",
};

/* ── data · renewals, next 90 days ───────────────────────────── */

const RENEWAL_STATUS = {
  committed: { label: "Committed", bg: T.meadowBg, text: T.meadowInk },
  talking: { label: "In conversation", bg: T.mist, text: T.teal },
  risk: { label: "At risk", bg: T.amberBg, text: T.amber },
};

const RENEWALS_NEXT = [
  { name: "Marcus Tran", seg: "Forum", month: "Aug", value: "$30K", status: "committed" },
  { name: "Jordan Pierce", seg: "Forum", month: "Aug", value: "$30K", status: "risk" },
  { name: "Dana Whitfield", seg: "Inner Circle", month: "Aug", value: "$18K", status: "talking" },
  { name: "Alicia Romero", seg: "Forum", month: "Sep", value: "$30K", status: "committed" },
  { name: "Chris Boone", seg: "Inner Circle", month: "Sep", value: "$18K", status: "risk" },
  { name: "Sam Kessler", seg: "Forum", month: "Oct", value: "$30K", status: "talking" },
];

const RENEWALS_SUMMARY = {
  count: 12, value: "$300K",
  mix: { committed: 6, talking: 3, risk: 3 },
  riskValue: "$66K",
  retention: "Trailing 12 mo · 86% logo · 91% dollar retention",
};

/* ── data · next event ───────────────────────────────────────── */

const EVENT = {
  name: "The Forum · Q3 Immersion",
  where: "Park City, UT",
  when: "Sep 15–17",
  daysOut: 75,
  registered: 28,
  members: 70,
  guests: 4,
  paceNote: "34 were registered at this point before Scottsdale Q2",
  behindPace: true,
};

/* ── data · revenue quality ──────────────────────────────────── */

const REVQ = {
  pif: { value: 828000, count: 33, label: "Paid in full" },
  monthly: { value: 372000, count: 14, label: "Monthly", sub: "$31K MRR annualized" },
  pastDue: { count: 2, value: "$4.4K" },
  bridge: [
    { label: "Jan 1", value: "$1.13M" },
    { label: "New", value: "+$158K" },
    { label: "Churned", value: "−$88K", soft: true },
    { label: "Today", value: "$1.2M", tot: true },
  ],
};

/* ── data · deep-dive deck (collapsed summaries) ───────────────
   Each card: hero stat, micro-visual, one salient line. The
   salient line in amber is the watch signal — no separate flag
   row needed. Selecting a card expands its full detail below. */

const DECK = [
  {
    k: "pipeline", label: "Recruiting pipeline",
    hero: "14", heroSub: "in the pipeline",
    salient: "3 invited · $84K near-term", tone: "good",
  },
  {
    k: "renewals", label: "Renewals · next 90 days",
    hero: "$300K", heroSub: "12 renewals",
    salient: "3 at risk · $66K", tone: "watch",
  },
  {
    k: "event", label: "Next event · Park City",
    hero: "75", heroSub: "days out",
    salient: "42 unregistered · behind pace", tone: "watch",
  },
  {
    k: "revq", label: "Revenue quality",
    hero: "69%", heroSub: "paid in full",
    salient: "2 past due · $4.4K", tone: "watch",
  },
];

/* ── data · drill-down drawer datasets ───────────────────────────
   Row shape: { name, seg?, l2, r1, r2, tone? }. Every row
   deep-links to GHL. */

const GHL = "#"; // mockup — real rows carry the GHL contact/opportunity URL

const DRILLS = {
  roster: {
    title: "Active roster", count: 70,
    computed: "Contacts in Go High Level carrying an active membership tag, segmented Forum / Inner Circle.",
    rows: [
      { name: "Marcus Tran", seg: "F", l2: "Joined Jan 2024 · PIF", r1: "$30K", r2: "renews Aug · event ✓" },
      { name: "Dana Whitfield", seg: "IC", l2: "Joined Mar 2025 · Monthly", r1: "$18K", r2: "renews Aug · event ✗" },
      { name: "Jordan Pierce", seg: "F", l2: "Joined Aug 2023 · PIF", r1: "$30K", r2: "renews Aug · event ✗", tone: "watch" },
      { name: "Alicia Romero", seg: "F", l2: "Joined Sep 2024 · Monthly", r1: "$30K", r2: "renews Sep · event ✓" },
      { name: "Chris Boone", seg: "IC", l2: "Joined Oct 2024 · PIF", r1: "$18K", r2: "renews Sep · event ✗", tone: "watch" },
      { name: "Sam Kessler", seg: "F", l2: "Joined Nov 2023 · PIF", r1: "$30K", r2: "renews Oct · event ✓" },
      { name: "Priya Natarajan", seg: "F", l2: "Joined Feb 2025 · PIF", r1: "$30K", r2: "renews Feb · event ✓" },
      { name: "Tyler Okafor", seg: "IC", l2: "Joined Jun 2025 · Monthly", r1: "$18K", r2: "renews Jun · event ✓" },
      { name: "Renee Calloway", seg: "IC", l2: "Joined Apr 2024 · PIF", r1: "$18K", r2: "renews Apr · event ✗" },
      { name: "Miguel Santos", seg: "F", l2: "Joined Jul 2024 · PIF", r1: "$30K", r2: "renews Dec · event ✓" },
      { name: "Hannah Brooks", seg: "IC", l2: "Joined Jan 2026 · Monthly", r1: "$18K", r2: "renews Jan · event ✓" },
      { name: "Derek Vaughn", seg: "F", l2: "Joined May 2024 · PIF", r1: "$30K", r2: "renews May · event ✗" },
    ],
    more: "58 more · open the full roster in Go High Level",
  },
  contracts: {
    title: "Forum ARR · memberships", count: 47,
    computed: "Contract value across open opportunities in the Current Forum Members (renewals) pipeline.",
    rows: [
      { name: "Marcus Tran", seg: "F", l2: "Renewals pipeline", r1: "$30,000", r2: "renews Aug" },
      { name: "Jordan Pierce", seg: "F", l2: "Renewals pipeline", r1: "$30,000", r2: "renews Aug", tone: "watch" },
      { name: "Alicia Romero", seg: "F", l2: "Renewals pipeline", r1: "$30,000", r2: "renews Sep" },
      { name: "Sam Kessler", seg: "F", l2: "Renewals pipeline", r1: "$30,000", r2: "renews Oct" },
      { name: "Priya Natarajan", seg: "F", l2: "Renewals pipeline", r1: "$30,000", r2: "renews Feb" },
      { name: "Miguel Santos", seg: "F", l2: "Renewals pipeline", r1: "$30,000", r2: "renews Dec" },
      { name: "Dana Whitfield", seg: "IC", l2: "Renewals pipeline", r1: "$18,000", r2: "renews Aug" },
      { name: "Chris Boone", seg: "IC", l2: "Renewals pipeline", r1: "$18,000", r2: "renews Sep", tone: "watch" },
      { name: "Renee Calloway", seg: "IC", l2: "Renewals pipeline", r1: "$18,000", r2: "renews Apr" },
      { name: "Tyler Okafor", seg: "IC", l2: "Renewals pipeline", r1: "$18,000", r2: "renews Jun" },
    ],
    more: "37 more memberships · $1.2M total",
  },
  renewals: {
    title: "Renewal book · next 90 days", count: 12,
    computed: "Open opportunities in the renewals pipeline with a renewal month of Aug, Sep, or Oct.",
    rows: [
      { name: "Marcus Tran", seg: "F", l2: "Aug · Committed", r1: "$30K", r2: "stage: verbal yes" },
      { name: "Jordan Pierce", seg: "F", l2: "Aug · At risk", r1: "$30K", r2: "stage: no response ×3", tone: "watch" },
      { name: "Dana Whitfield", seg: "IC", l2: "Aug · In conversation", r1: "$18K", r2: "stage: call booked" },
      { name: "Alicia Romero", seg: "F", l2: "Sep · Committed", r1: "$30K", r2: "stage: agreement out" },
      { name: "Chris Boone", seg: "IC", l2: "Sep · At risk", r1: "$18K", r2: "stage: considering exit", tone: "watch" },
      { name: "Kim Delgado", seg: "F", l2: "Sep · Committed", r1: "$30K", r2: "stage: verbal yes" },
      { name: "Sam Kessler", seg: "F", l2: "Oct · In conversation", r1: "$30K", r2: "stage: call booked" },
      { name: "Owen Foster", seg: "IC", l2: "Oct · Committed", r1: "$18K", r2: "stage: verbal yes" },
      { name: "Lena Marsh", seg: "F", l2: "Oct · Committed", r1: "$30K", r2: "stage: agreement out" },
      { name: "Ray Whitman", seg: "IC", l2: "Oct · In conversation", r1: "$18K", r2: "stage: call booked" },
      { name: "Nia Coleman", seg: "F", l2: "Oct · Committed", r1: "$30K", r2: "stage: verbal yes" },
      { name: "Grant Ellis", seg: "IC", l2: "Oct · At risk", r1: "$18K", r2: "stage: no response ×2", tone: "watch" },
    ],
    more: null,
  },
  registered: {
    title: "Registered · Park City Q3", count: 28,
    computed: "Contacts tagged 'the forum q3 2026' in Go High Level.",
    rows: [
      { name: "Marcus Tran", seg: "F", l2: "Registered Jun 12", r1: "✓", r2: "" },
      { name: "Alicia Romero", seg: "F", l2: "Registered Jun 14", r1: "✓", r2: "" },
      { name: "Sam Kessler", seg: "F", l2: "Registered Jun 15", r1: "✓", r2: "" },
      { name: "Priya Natarajan", seg: "F", l2: "Registered Jun 18", r1: "✓", r2: "" },
      { name: "Tyler Okafor", seg: "IC", l2: "Registered Jun 20", r1: "✓", r2: "" },
      { name: "Hannah Brooks", seg: "IC", l2: "Registered Jun 22", r1: "✓", r2: "" },
      { name: "Miguel Santos", seg: "F", l2: "Registered Jun 25", r1: "✓", r2: "" },
      { name: "Kim Delgado", seg: "F", l2: "Registered Jun 28", r1: "✓", r2: "" },
    ],
    more: "20 more registered · 4 guests (prospects) not shown",
  },
  unregistered: {
    title: "Not yet registered · Park City Q3", count: 42,
    computed: "Active members without the 'the forum q3 2026' tag. This is the call list.",
    rows: [
      { name: "Jordan Pierce", seg: "F", l2: "Last attended: Scottsdale Q2", r1: "call", r2: "also renewal risk", tone: "watch" },
      { name: "Dana Whitfield", seg: "IC", l2: "Last attended: Dallas Q1", r1: "call", r2: "" },
      { name: "Chris Boone", seg: "IC", l2: "Last attended: Dallas Q1", r1: "call", r2: "also renewal risk", tone: "watch" },
      { name: "Renee Calloway", seg: "IC", l2: "Last attended: Scottsdale Q2", r1: "call", r2: "" },
      { name: "Derek Vaughn", seg: "F", l2: "Last attended: Scottsdale Q2", r1: "call", r2: "" },
      { name: "Owen Foster", seg: "IC", l2: "Last attended: Dallas Q1", r1: "call", r2: "" },
      { name: "Lena Marsh", seg: "F", l2: "Has never missed — likely late reg", r1: "call", r2: "" },
      { name: "Ray Whitman", seg: "IC", l2: "Last attended: Scottsdale Q2", r1: "call", r2: "" },
      { name: "Grant Ellis", seg: "IC", l2: "Missed last 2 events", r1: "call", r2: "also renewal risk", tone: "watch" },
      { name: "Nia Coleman", seg: "F", l2: "Last attended: Scottsdale Q2", r1: "call", r2: "" },
    ],
    more: "32 more unregistered members",
  },
  pastdue: {
    title: "Subscriptions past due", count: 2,
    computed: "GHL subscriptions with a failed most-recent charge.",
    rows: [
      { name: "Tyler Okafor", seg: "IC", l2: "Card declined · Jun 28", r1: "$2,200", r2: "retry scheduled Jul 3", tone: "watch" },
      { name: "Hannah Brooks", seg: "IC", l2: "Card expired · Jun 30", r1: "$2,200", r2: "update link sent", tone: "watch" },
    ],
    more: null,
  },
  monthly: {
    title: "Monthly subscriptions", count: 14,
    computed: "Active recurring subscriptions in GHL Payments. Sum = $31K MRR.",
    rows: [
      { name: "Alicia Romero", seg: "F", l2: "Forum · monthly", r1: "$2,500/mo", r2: "current" },
      { name: "Kim Delgado", seg: "F", l2: "Forum · monthly", r1: "$2,500/mo", r2: "current" },
      { name: "Nia Coleman", seg: "F", l2: "Forum · monthly", r1: "$2,500/mo", r2: "current" },
      { name: "Dana Whitfield", seg: "IC", l2: "Inner Circle · monthly", r1: "$2,200/mo", r2: "current" },
      { name: "Tyler Okafor", seg: "IC", l2: "Inner Circle · monthly", r1: "$2,200/mo", r2: "past due", tone: "watch" },
      { name: "Hannah Brooks", seg: "IC", l2: "Inner Circle · monthly", r1: "$2,200/mo", r2: "past due", tone: "watch" },
    ],
    more: "8 more monthly payers",
  },
};

/* ── nav (beCollective split out into its own space) ─────────── */

const NAV = [
  { k: "overview", label: "Portfolio", dot: T.parchment },
  { k: "ulrg", label: "ULRG + Team", dot: T.meadow },
  { k: "forum", label: "The Forum", dot: T.daffodil },
  { k: "becollective", label: "beCollective", dot: T.petal },
  { k: "sympli", label: "Sympli Mortgage", dot: T.teal },
  { k: "flywheel", label: "Referral Flywheel", dot: T.sprout, divide: true },
];

/* ── small pieces ──────────────────────────────────────────── */

function Source({ name }) {
  return (
    <span style={{
      fontFamily: "Inter,sans-serif", fontSize: 10.5, fontWeight: 600, color: T.slate,
      background: T.parchment, border: `1px solid ${T.line}`, borderRadius: 5, padding: "2px 7px",
    }}>{name}</span>
  );
}

function PanelLabel({ children, accent }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 14 }}>
      <span style={{ width: 3, height: 14, borderRadius: 2, background: accent || ACCENT }} />
      <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 11, fontWeight: 700, letterSpacing: "0.1em", textTransform: "uppercase", color: T.slate }}>
        {children}
      </span>
    </div>
  );
}

function Card({ children, style }) {
  return <div style={{ background: T.white, border: `1px solid ${T.line}`, borderRadius: 14, padding: 22, ...style }}>{children}</div>;
}

function SegChip({ seg }) {
  const f = seg === "F" || seg === "Forum";
  return (
    <span style={{
      fontFamily: "Poppins,sans-serif", fontSize: 9, fontWeight: 700, letterSpacing: "0.06em",
      color: f ? T.evergreen : T.teal, background: f ? T.daffodil : T.mist,
      borderRadius: 4, padding: "2px 6px", textTransform: "uppercase", flexShrink: 0,
    }}>{f ? "Forum" : "IC"}</span>
  );
}

function StatusChip({ status }) {
  const s = RENEWAL_STATUS[status];
  return (
    <span style={{
      fontFamily: "Inter,sans-serif", fontSize: 10.5, fontWeight: 600,
      color: s.text, background: s.bg, borderRadius: 5, padding: "3px 8px", whiteSpace: "nowrap",
    }}>{s.label}</span>
  );
}

/* daffodil = the one "do this today" highlight */
function ActionRow({ label, cta, onClick }) {
  return (
    <button className="fv-link" onClick={onClick} style={{
      display: "flex", justifyContent: "space-between", alignItems: "center", width: "100%",
      background: T.daffodilBg, border: `1px solid ${T.daffodil}`, borderRadius: 9,
      padding: "10px 13px", marginTop: 14, cursor: "pointer", gap: 10,
    }}>
      <span style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, fontWeight: 600, color: T.ink, textAlign: "left" }}>{label}</span>
      <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, fontWeight: 600, color: T.amber, whiteSpace: "nowrap" }}>{cta} →</span>
    </button>
  );
}

/* KPI tile — clickable when it has a drill target */
function KpiTile({ d, onOpen }) {
  const clickable = !!d.drill;
  return (
    <button className={clickable ? "fv-tile" : ""} disabled={!clickable}
      onClick={clickable ? () => onOpen(d.drill) : undefined} style={{
        background: T.parchment, borderRadius: 10, padding: "12px 13px", border: "none",
        textAlign: "left", cursor: clickable ? "pointer" : "default", position: "relative", width: "100%",
      }}>
      <div style={{ fontFamily: "Inter,sans-serif", fontSize: 11, color: T.slate, marginBottom: 6, fontWeight: 500 }}>{d.label}</div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 6 }}>
        <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 22, fontWeight: 600, color: T.ink, fontVariantNumeric: "tabular-nums" }}>{d.value}</span>
        {d.sub && <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11, color: T.muted }}>{d.sub}</span>}
      </div>
      {clickable && <span aria-hidden style={{ position: "absolute", top: 10, right: 11, fontSize: 10, color: T.muted }}>↗</span>}
    </button>
  );
}

/* ── deck micro-visuals (fixed 12px lane so cards stay equal) ── */

function MicroBars() {
  const max = Math.max(...FUNNEL.stages.map((s) => s.v));
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 3, height: 21 }}>
      {FUNNEL.stages.map((s, i) => (
        <div key={i} style={{ height: 3, width: `${(s.v / max) * 100}%`, background: ACCENT, opacity: 0.35 + 0.65 * (s.v / max), borderRadius: 2 }} />
      ))}
    </div>
  );
}

function MicroSplit({ parts }) {
  const total = parts.reduce((a, p) => a + p.v, 0);
  return (
    <div style={{ display: "flex", height: 8, borderRadius: 4, overflow: "hidden", gap: 2, margin: "6px 0 7px" }}>
      {parts.map((p, i) => (
        <div key={i} style={{ width: `${(p.v / total) * 100}%`, background: p.c, opacity: p.o ?? 1 }} />
      ))}
    </div>
  );
}

function MicroProgress({ pct, fill }) {
  return (
    <div style={{ height: 8, borderRadius: 4, background: T.parchment, overflow: "hidden", margin: "6px 0 7px" }}>
      <div style={{ width: `${pct}%`, height: "100%", background: fill, borderRadius: 4 }} />
    </div>
  );
}

const MICROS = {
  pipeline: <MicroBars />,
  renewals: <MicroSplit parts={[
    { v: RENEWALS_SUMMARY.mix.committed, c: GOOD },
    { v: RENEWALS_SUMMARY.mix.talking, c: T.teal, o: 0.35 },
    { v: RENEWALS_SUMMARY.mix.risk, c: T.amber, o: 0.55 },
  ]} />,
  event: <MicroProgress pct={Math.round((EVENT.registered / EVENT.members) * 100)} fill={GOOD} />,
  revq: <MicroSplit parts={[
    { v: REVQ.pif.value, c: ACCENT },
    { v: REVQ.monthly.value, c: ACCENT, o: 0.28 },
  ]} />,
};

/* ── deck card (collapsed summary — the salient highlight) ───── */

function DeckCard({ d, active, onSelect }) {
  const watch = d.tone === "watch";
  return (
    <button className={`fd-card ${active ? "on" : ""}`} onClick={onSelect}
      aria-expanded={active} style={{
        position: "relative", textAlign: "left", background: T.white, cursor: "pointer",
        border: `1px solid ${active ? T.evergreen : T.line}`, borderRadius: 14, padding: "16px 16px 14px",
        display: "flex", flexDirection: "column", gap: 7, minHeight: 148,
      }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
        <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 10.5, fontWeight: 700, letterSpacing: "0.08em", textTransform: "uppercase", color: T.slate }}>{d.label}</span>
        <span aria-hidden className="fd-chev" style={{ fontSize: 10, color: T.muted }}>{active ? "▴" : "▾"}</span>
      </div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 7 }}>
        <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 27, fontWeight: 700, color: T.ink, lineHeight: 1, fontVariantNumeric: "tabular-nums" }}>{d.hero}</span>
        <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11, color: T.muted }}>{d.heroSub}</span>
      </div>
      <div style={{ marginTop: "auto" }}>
        {MICROS[d.k]}
        <span style={{
          display: "inline-flex", alignItems: "center", gap: 6,
          fontFamily: "Inter,sans-serif", fontSize: 11.5, fontWeight: 600,
          color: watch ? T.amber : T.meadowInk,
        }}>
          {watch && <span style={{ width: 6, height: 6, borderRadius: 99, background: T.daffodil, border: `1.5px solid ${T.amber}`, flexShrink: 0 }} />}
          {d.salient}
        </span>
      </div>
      <span aria-hidden className="fd-underline" />
    </button>
  );
}

/* ── expanded details (the full views from v1, recolored) ────── */

function PipelineDetail() {
  const max = Math.max(...FUNNEL.stages.map((s) => s.v));
  return (
    <div>
      <div style={{ display: "flex", flexDirection: "column", gap: 10, maxWidth: 640 }}>
        {FUNNEL.stages.map((s, i) => (
          <div key={i} style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <span style={{ width: 150, fontFamily: "Inter,sans-serif", fontSize: 12, color: T.slate, textAlign: "right" }}>{s.label}</span>
            <div style={{ flex: 1, height: 22, background: T.parchment, borderRadius: 5, overflow: "hidden" }}>
              <div style={{ width: `${(s.v / max) * 100}%`, height: "100%", background: ACCENT, opacity: 0.35 + 0.65 * (s.v / max), borderRadius: 5 }} />
            </div>
            <span style={{ width: 26, fontFamily: "Poppins,sans-serif", fontSize: 13, fontWeight: 600, color: T.ink, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>{s.v}</span>
            <span style={{ width: 52, fontFamily: "Inter,sans-serif", fontSize: 11, color: T.muted, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>{s.value}</span>
          </div>
        ))}
      </div>
      <div style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.slate, marginTop: 16, paddingTop: 13, borderTop: `1px solid ${T.line}`, lineHeight: 1.5 }}>
        {FUNNEL.footer}
      </div>
    </div>
  );
}

function RenewalsDetail({ onOpen }) {
  return (
    <div>
      <div style={{ maxWidth: 640 }}>
        {RENEWALS_NEXT.map((r, i) => (
          <div key={i} style={{
            display: "flex", alignItems: "center", gap: 10, padding: "8px 0",
            borderTop: i === 0 ? "none" : `1px solid ${T.parchment}`,
          }}>
            <span style={{ width: 28, fontFamily: "Poppins,sans-serif", fontSize: 10.5, fontWeight: 700, color: T.muted, textTransform: "uppercase", letterSpacing: "0.04em" }}>{r.month}</span>
            <span style={{ flex: 1, fontFamily: "Inter,sans-serif", fontSize: 12.5, fontWeight: 600, color: T.ink, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{r.name}</span>
            <SegChip seg={r.seg} />
            <span style={{ width: 40, fontFamily: "Poppins,sans-serif", fontSize: 12.5, fontWeight: 600, color: T.ink, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>{r.value}</span>
            <span style={{ width: 104, textAlign: "right" }}><StatusChip status={r.status} /></span>
          </div>
        ))}
      </div>
      <button className="fv-link" onClick={() => onOpen("renewals")} style={{
        display: "flex", justifyContent: "space-between", alignItems: "baseline", width: "100%", maxWidth: 640,
        border: "none", background: "transparent", cursor: "pointer", padding: "13px 0 0",
        borderTop: `1px solid ${T.line}`, marginTop: 12, gap: 10,
      }}>
        <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.slate, textAlign: "left" }}>{RENEWALS_SUMMARY.retention}</span>
        <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, fontWeight: 600, color: T.meadowInk, whiteSpace: "nowrap" }}>
          all {RENEWALS_SUMMARY.count} · {RENEWALS_SUMMARY.value} →
        </span>
      </button>
    </div>
  );
}

function EventDetail({ onOpen }) {
  const pct = Math.round((EVENT.registered / EVENT.members) * 100);
  const unregistered = EVENT.members - EVENT.registered;
  return (
    <div style={{ maxWidth: 640 }}>
      <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
        <div>
          <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 17, fontWeight: 600, color: T.ink }}>{EVENT.where}</div>
          <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12, color: T.muted, marginTop: 2 }}>{EVENT.name} · {EVENT.when}</div>
        </div>
        <div style={{ textAlign: "right" }}>
          <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 26, fontWeight: 700, color: T.ink, fontVariantNumeric: "tabular-nums" }}>{EVENT.daysOut}</span>
          <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.muted, marginLeft: 5 }}>days out</span>
        </div>
      </div>
      <div style={{ marginTop: 16 }}>
        <div style={{ display: "flex", height: 12, borderRadius: 6, overflow: "hidden", background: T.parchment }}>
          <div style={{ width: `${pct}%`, background: GOOD, borderRadius: 6 }} />
        </div>
        <div style={{ display: "flex", justifyContent: "space-between", marginTop: 8, flexWrap: "wrap", gap: 6 }}>
          <span style={{ fontFamily: "Inter,sans-serif", fontSize: 12, color: T.ink, fontWeight: 600 }}>
            {EVENT.registered} of {EVENT.members} members · {pct}%
          </span>
          <span style={{ fontFamily: "Inter,sans-serif", fontSize: 12, color: T.muted }}>+ {EVENT.guests} guests <span style={{ color: T.teal, fontWeight: 600 }}>(prospect seats)</span></span>
        </div>
      </div>
      <ActionRow label={`${unregistered} members not yet registered`} cta="the call list" onClick={() => onOpen("unregistered")} />
      {EVENT.behindPace && (
        <div style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.amber, marginTop: 12, lineHeight: 1.5 }}>
          Behind pace — {EVENT.paceNote}.
        </div>
      )}
    </div>
  );
}

function RevQDetail({ onOpen }) {
  const total = REVQ.pif.value + REVQ.monthly.value;
  const pifPct = (REVQ.pif.value / total) * 100;
  const k = (n) => "$" + Math.round(n / 1000) + "K";
  return (
    <div style={{ maxWidth: 640 }}>
      {/* payment mix — solid is collected, lighter is still collecting */}
      <div style={{ display: "flex", height: 12, borderRadius: 6, overflow: "hidden", gap: 2 }}>
        <div style={{ width: `${pifPct}%`, background: ACCENT, borderRadius: "6px 0 0 6px" }} />
        <div style={{ width: `${100 - pifPct}%`, background: ACCENT, opacity: 0.28, borderRadius: "0 6px 6px 0" }} />
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", marginTop: 8, flexWrap: "wrap", gap: 6 }}>
        <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.slate }}>
          <b style={{ color: T.ink, fontFamily: "Poppins,sans-serif" }}>{k(REVQ.pif.value)}</b> paid in full · {REVQ.pif.count}
        </span>
        <button className="fv-link" onClick={() => onOpen("monthly")} style={{ border: "none", background: "transparent", cursor: "pointer", padding: 0, fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.slate }}>
          <b style={{ color: T.ink, fontFamily: "Poppins,sans-serif" }}>{k(REVQ.monthly.value)}</b> monthly · {REVQ.monthly.count} · {REVQ.monthly.sub} ↗
        </button>
      </div>
      <ActionRow label={`${REVQ.pastDue.count} subscriptions past due · ${REVQ.pastDue.value}`} cta="recover" onClick={() => onOpen("pastdue")} />
      {/* ARR bridge */}
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginTop: 14, paddingTop: 13, borderTop: `1px solid ${T.line}`, flexWrap: "wrap" }}>
        {REVQ.bridge.map((b, i) => (
          <span key={i} style={{ display: "inline-flex", alignItems: "baseline", gap: 8 }}>
            {i > 0 && <span style={{ fontSize: 11, color: T.muted }}>→</span>}
            <span>
              <span style={{ fontFamily: "Inter,sans-serif", fontSize: 10, color: T.muted, display: "block" }}>{b.label}</span>
              <span style={{
                fontFamily: "Poppins,sans-serif", fontSize: b.tot ? 15 : 12.5, fontWeight: b.tot ? 700 : 600,
                color: b.soft ? T.slate : b.tot ? T.meadowInk : T.ink, fontVariantNumeric: "tabular-nums",
              }}>{b.value}</span>
            </span>
          </span>
        ))}
        <span style={{ fontFamily: "Inter,sans-serif", fontSize: 10.5, color: T.muted, marginLeft: "auto" }}>ARR bridge · YTD</span>
      </div>
    </div>
  );
}

const DETAIL_META = {
  pipeline: { title: "Recruiting pipeline · sales funnel", C: PipelineDetail },
  renewals: { title: "Renewals · next 90 days", C: RenewalsDetail },
  event: { title: "Next event · readiness", C: EventDetail },
  revq: { title: "Revenue quality", C: RevQDetail },
};

/* ── drill-down drawer ─────────────────────────────────────── */

function Drawer({ drill, onClose }) {
  if (!drill) return null;
  const d = DRILLS[drill];
  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "rgba(0,46,44,0.38)", zIndex: 50, display: "flex", justifyContent: "flex-end" }}>
      <div className="fv-drawer" onClick={(e) => e.stopPropagation()} style={{
        width: 430, maxWidth: "92vw", background: T.white, height: "100%",
        display: "flex", flexDirection: "column", boxShadow: "-18px 0 44px rgba(0,46,44,0.22)",
      }}>
        <div style={{ background: T.evergreen, padding: "20px 22px 16px" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12 }}>
            <div>
              <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 17, fontWeight: 600, color: T.onDark }}>
                {d.title} <span style={{ color: T.daffodil }}>· {d.count}</span>
              </div>
              <div style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.onDarkMute, marginTop: 6, lineHeight: 1.5 }}>{d.computed}</div>
            </div>
            <button onClick={onClose} aria-label="Close" style={{
              border: "none", background: "rgba(248,245,242,0.10)", color: T.onDark, borderRadius: 8,
              width: 28, height: 28, cursor: "pointer", fontSize: 14, flexShrink: 0, lineHeight: 1,
            }}>×</button>
          </div>
          <div style={{ marginTop: 12 }}><Source name="Go High Level" /></div>
        </div>
        <div style={{ flex: 1, overflowY: "auto", padding: "8px 22px 18px" }}>
          {d.rows.map((r, i) => (
            <div key={i} style={{ display: "flex", alignItems: "center", gap: 10, padding: "11px 0", borderBottom: `1px solid ${T.parchment}` }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span style={{ fontFamily: "Inter,sans-serif", fontSize: 13, fontWeight: 600, color: T.ink, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{r.name}</span>
                  {r.seg && <SegChip seg={r.seg} />}
                  {r.tone === "watch" && <span aria-hidden style={{ width: 6, height: 6, borderRadius: 99, background: T.daffodil, border: `1.5px solid ${T.amber}`, flexShrink: 0 }} />}
                </div>
                {r.l2 && <div style={{ fontFamily: "Inter,sans-serif", fontSize: 11, color: T.muted, marginTop: 3 }}>{r.l2}</div>}
              </div>
              <div style={{ textAlign: "right", flexShrink: 0 }}>
                <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 13, fontWeight: 600, color: r.tone === "watch" ? T.amber : T.ink, fontVariantNumeric: "tabular-nums" }}>{r.r1}</div>
                {r.r2 && <div style={{ fontFamily: "Inter,sans-serif", fontSize: 10.5, color: T.muted, marginTop: 2 }}>{r.r2}</div>}
              </div>
              <a href={GHL} onClick={(e) => e.preventDefault()} title="Open in Go High Level" style={{ fontFamily: "Inter,sans-serif", fontSize: 11, color: T.slate, textDecoration: "none", flexShrink: 0 }}>↗</a>
            </div>
          ))}
          {d.more && (
            <div style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.muted, padding: "14px 0 4px", textAlign: "center" }}>{d.more}</div>
          )}
        </div>
      </div>
    </div>
  );
}

/* ── The Forum view ────────────────────────────────────────── */

function ForumView({ onOpen }) {
  const [sel, setSel] = useState(null);
  const meta = sel ? DETAIL_META[sel] : null;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
      {/* header */}
      <div style={{ display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap" }}>
        <span style={{ width: 5, height: 30, borderRadius: 3, background: ACCENT }} />
        <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 24, fontWeight: 600, color: T.ink }}>The Forum</span>
        <span style={{ fontFamily: "Inter,sans-serif", fontSize: 13, color: T.muted }}>Mastermind · 70 members</span>
        <span style={{ flex: 1 }} />
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          <span className="fv-pulse" style={{ width: 7, height: 7, borderRadius: 99, background: T.amber, color: T.amber }} />
          <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, fontWeight: 600, color: T.amber }}>3 items to watch</span>
        </span>
      </div>

      {/* row 1 — P&L (QuickBooks pending, as in prod) + KPI tiles */}
      <div className="fv-row1">
        <Card style={{ display: "flex", flexDirection: "column" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <PanelLabel>Financial · P&amp;L</PanelLabel>
            <Source name="QuickBooks" />
          </div>
          <div style={{ flex: 1, display: "flex", flexDirection: "column", justifyContent: "center", gap: 14 }}>
            <div style={{ fontFamily: "Inter,sans-serif", fontSize: 13, color: T.slate }}>
              Financials light up when QuickBooks is connected
            </div>
            <button style={{
              alignSelf: "flex-start", fontFamily: "Inter,sans-serif", fontSize: 12.5, fontWeight: 600,
              color: T.onDark, background: T.evergreen, border: "none", borderRadius: 9,
              padding: "10px 16px", cursor: "pointer",
            }}>Connect QuickBooks</button>
            <div style={{ fontFamily: "Inter,sans-serif", fontSize: 11, color: T.muted, lineHeight: 1.5 }}>
              Spring B entity · Forum revenue splits from beCollective by QBO class.
            </div>
          </div>
        </Card>
        <Card>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <PanelLabel>Operational · leading indicators</PanelLabel>
            <Source name="Go High Level" />
          </div>
          <div className="fv-ops">
            {KPIS.map((d, i) => <KpiTile key={i} d={d} onOpen={onOpen} />)}
          </div>
        </Card>
      </div>

      {/* deep-dive deck — salient highlights, select to expand */}
      <div>
        <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", margin: "2px 2px 12px" }}>
          <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 11, fontWeight: 700, letterSpacing: "0.14em", textTransform: "uppercase", color: T.slate }}>Deep dives</span>
          <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.muted }}>select a card to expand</span>
        </div>
        <div className="fd-deck">
          {DECK.map((d) => (
            <DeckCard key={d.k} d={d} active={sel === d.k}
              onSelect={() => setSel(sel === d.k ? null : d.k)} />
          ))}
        </div>
        {meta && (
          <Card style={{ marginTop: 14 }}>
            <div className="fd-body" key={sel}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <PanelLabel>{meta.title}</PanelLabel>
                <span style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  <Source name="Go High Level" />
                  <button className="fv-link" onClick={() => setSel(null)} aria-label="Collapse" style={{
                    border: `1px solid ${T.line}`, background: T.parchment, color: T.slate, borderRadius: 7,
                    fontFamily: "Inter,sans-serif", fontSize: 11, fontWeight: 600, padding: "4px 10px", cursor: "pointer",
                  }}>Collapse ▴</button>
                </span>
              </div>
              <meta.C onOpen={onOpen} />
            </div>
          </Card>
        )}
      </div>
    </div>
  );
}

/* ── other views (placeholders — unchanged / future builds) ── */

function Placeholder({ title, note }) {
  return (
    <Card style={{ maxWidth: 560 }}>
      <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 17, fontWeight: 600, color: T.ink }}>{title}</div>
      <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, color: T.slate, marginTop: 8, lineHeight: 1.6 }}>{note}</div>
    </Card>
  );
}

/* ── shell ─────────────────────────────────────────────────── */

export default function ForumFocus() {
  const [view, setView] = useState("forum");
  const [drill, setDrill] = useState(null);
  return (
    <div style={{ background: T.parchment, minHeight: "100%", fontFamily: "Inter,sans-serif" }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700;800&family=Inter:wght@400;500;600&family=Sacramento&display=swap');
        .fv-nav { transition: background .12s ease; }
        .fv-tile { transition: transform .15s ease, box-shadow .15s ease; }
        .fv-tile:hover { transform: translateY(-1px); box-shadow: 0 8px 20px rgba(0,46,44,.10); }
        .fv-link:hover { filter: brightness(0.97); }
        .fv-row1 { display: grid; grid-template-columns: minmax(0,5fr) minmax(0,7fr); gap: 18px; align-items: stretch; }
        .fv-ops { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }

        /* deep-dive deck */
        .fd-deck { display: grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap: 14px; }
        .fd-card { transition: transform .15s ease, box-shadow .15s ease, border-color .15s ease; }
        .fd-card:hover { transform: translateY(-2px); box-shadow: 0 10px 24px rgba(0,46,44,.10); }
        .fd-card.on { box-shadow: 0 10px 24px rgba(0,46,44,.08); }
        .fd-underline { position: absolute; left: 14px; right: 14px; bottom: -1px; height: 3px; border-radius: 3px;
          background: ${T.daffodil}; transform: scaleX(0); transform-origin: left; transition: transform .22s ease; }
        .fd-card.on .fd-underline { transform: scaleX(1); }
        .fd-body { animation: fdFade .28s ease; }
        @keyframes fdFade { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }

        .fv-drawer { animation: fvSlide .22s ease; }
        @keyframes fvSlide { from { transform: translateX(24px); opacity: 0; } to { transform: none; opacity: 1; } }
        @media (prefers-reduced-motion: no-preference) {
          .fv-pulse { animation: fvPulse 2.4s ease-out infinite; }
        }
        @keyframes fvPulse { 0% { box-shadow: 0 0 0 0 currentColor; } 70% { box-shadow: 0 0 0 5px rgba(0,0,0,0); } 100% { box-shadow: 0 0 0 0 rgba(0,0,0,0); } }

        .fv-nav:focus-visible, .fv-tile:focus-visible, .fv-link:focus-visible, .fd-card:focus-visible { outline: 2px solid ${T.teal}; outline-offset: 2px; }
        @media (max-width: 1000px) { .fd-deck { grid-template-columns: repeat(2, minmax(0,1fr)); } .fv-row1 { grid-template-columns: 1fr; } }
        @media (max-width: 560px) { .fd-deck { grid-template-columns: 1fr; } .fv-ops { grid-template-columns: 1fr; } }
        @media (prefers-reduced-motion: reduce) {
          .fv-tile, .fv-nav, .fd-card, .fd-underline { transition: none; }
          .fv-drawer, .fd-body { animation: none; }
          .fv-tile:hover, .fd-card:hover { transform: none; }
        }
      `}</style>

      <div style={{ display: "flex", minHeight: "100vh" }}>
        {/* Rail */}
        <aside style={{ width: 224, background: T.evergreen, padding: "24px 16px", display: "flex", flexDirection: "column", flexShrink: 0 }}>
          <div style={{ padding: "0 8px 22px" }}>
            <div style={{ fontFamily: "Sacramento,cursive", fontSize: 34, color: T.onDark, lineHeight: 1 }}>Spring</div>
            <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 10, fontWeight: 700, letterSpacing: "0.18em", color: T.sprout, marginTop: 6, textTransform: "uppercase" }}>Command Center</div>
          </div>
          {NAV.map((n) => {
            const active = view === n.k;
            return (
              <button key={n.k} className="fv-nav" onClick={() => setView(n.k)} style={{
                display: "flex", alignItems: "center", gap: 10, width: "100%", textAlign: "left",
                background: active ? "rgba(248,245,242,0.10)" : "transparent", border: "none",
                borderLeft: active ? `3px solid ${T.daffodil}` : "3px solid transparent", borderRadius: 8,
                padding: "10px 10px", cursor: "pointer", marginTop: n.divide ? 14 : 2,
                borderTop: n.divide ? "1px solid rgba(248,245,242,0.10)" : "none", paddingTop: n.divide ? 16 : 10,
              }}>
                <span style={{ width: 8, height: 8, borderRadius: 2, background: n.dot, flexShrink: 0 }} />
                <span style={{ fontFamily: "Inter,sans-serif", fontSize: 13.5, fontWeight: active ? 600 : 500, color: active ? T.onDark : T.onDarkMute }}>{n.label}</span>
              </button>
            );
          })}
          <div style={{ flex: 1 }} />
          <div style={{ padding: "0 8px", fontFamily: "Inter,sans-serif", fontSize: 10.5, color: "#5C6F6A", lineHeight: 1.6 }}>Mockup · representative data</div>
        </aside>

        {/* Main */}
        <main style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "14px 26px", borderBottom: `1px solid ${T.line}`, background: T.white, flexWrap: "wrap", gap: 10 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <span style={{ fontFamily: "Inter,sans-serif", fontSize: 13, color: T.slate }}>As of</span>
              <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 13.5, fontWeight: 600, color: T.ink }}>Jul 2, 2026</span>
              <span style={{ display: "inline-flex", background: T.parchment, borderRadius: 7, border: `1px solid ${T.line}`, padding: 2, gap: 2 }}>
                {["Month", "Quarter", "Year", "Last month"].map((p, i) => (
                  <span key={p} style={{
                    fontFamily: "Inter,sans-serif", fontSize: 11, fontWeight: 600, borderRadius: 5, padding: "3px 9px",
                    background: i === 0 ? T.evergreen : "transparent", color: i === 0 ? T.onDark : T.slate,
                  }}>{p}</span>
                ))}
              </span>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11, color: T.muted, marginRight: 2 }}>Live from</span>
              {["QuickBooks", "Sisu", "Follow Up Boss", "Go High Level"].map((s) => (
                <span key={s} style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
                  <span style={{ width: 6, height: 6, borderRadius: 99, background: T.meadow }} /><Source name={s} />
                </span>
              ))}
            </div>
          </div>

          <div style={{ padding: 26, maxWidth: 1100 }}>
            {view === "forum" && <ForumView onOpen={setDrill} />}
            {view === "becollective" && <Placeholder title="beCollective"
              note="beCollective gets its own space — same shell, its own accent, its own GHL segment (membership tiers, community engagement, its own funnel). Mocked separately." />}
            {view === "overview" && <Placeholder title="Portfolio" note="Unchanged from the current build — see spring-command-center.jsx. The Spring B card splits into The Forum + beCollective cards." />}
            {view === "ulrg" && <Placeholder title="ULRG + Team" note="Unchanged from the current build." />}
            {view === "sympli" && <Placeholder title="Sympli Mortgage" note="Unchanged from the current build." />}
            {view === "flywheel" && <Placeholder title="Referral Flywheel" note="Unchanged from the current build." />}
          </div>
        </main>
      </div>

      <Drawer drill={drill} onClose={() => setDrill(null)} />
    </div>
  );
}
