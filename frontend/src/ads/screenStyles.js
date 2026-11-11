/* The Ads screens' one stylesheet: Today, Performance, Budget, Activity and Settings.
 *
 * It replaces the ported mockup sheet (adsStyles.js) as the thing AdsView mounts. That sheet
 * styled the old single-page layout, and its bare `table`, `th`, `td` and `a` rules reached the
 * new screens: Activity's cells sat right-aligned under left-aligned heads, Settings' brand names
 * were pushed right, table heads rendered at 9px, and under 780px every table turned into clipped
 * cards. Nothing here styles a bare element except inside an `ax-` class.
 *
 * One system, written down once:
 *   spacing   4 8 12 16 24 32                 (--ax-1 .. --ax-6)
 *   type      13 label/meta · 14 body · 16 card title · 20 drawer title · 24 the page title and
 *             the headline figures (22 on a phone). A figure never outgrows the title, and
 *             nothing is smaller than 13.
 *   radius    14 cards · 10 segmented track · 8 controls
 *   controls  36px high (44 on a touch screen)
 *   motion    150ms in, 120ms out, 90ms press, 200ms panel, one ease-out curve; transform and
 *             opacity only, nothing on data, and nothing moves under reduced motion.
 *
 * Colours come from adsTokens, which is still the only file under ads/ permitted a hex.
 */
import { alpha } from "../theme.js";
import { C } from "./adsTokens.js";

export const screenCss = () => `
.adsx {
  --ax-1:4px; --ax-2:8px; --ax-3:12px; --ax-4:16px; --ax-5:24px; --ax-6:32px;
  --ax-meta:13px; --ax-body:14px; --ax-title:16px; --ax-fig-sm:20px; --ax-fig:24px;
  --ax-r:14px; --ax-r-sm:8px; --ax-ctl:36px; --ax-pad:var(--ax-5);
  --ax-in:150ms; --ax-out:120ms; --ax-press:90ms; --ax-panel:200ms;
  --ax-ease:cubic-bezier(.2,.8,.2,1);
  display:grid; gap:var(--ax-4); padding:var(--ax-2) 0 var(--ax-6); min-width:0;
  font-family:var(--font-text); font-size:var(--ax-body); line-height:1.5; color:${C.ink};
  text-wrap:pretty;
}
.adsx *, .adsx *::before, .adsx *::after { box-sizing:border-box; }
@media (max-width:640px) { .adsx { gap:var(--ax-3); --ax-fig:22px; --ax-pad:var(--ax-4); } }

.adsx .ax-sr { position:absolute; width:1px; height:1px; overflow:hidden; clip:rect(0 0 0 0);
  white-space:nowrap; margin:-1px; padding:0; border:0; }
.adsx .ax-stack { display:grid; gap:var(--ax-4); min-width:0; }
@media (max-width:640px) { .adsx .ax-stack { gap:var(--ax-3); } }
.adsx .ax-row { display:flex; align-items:center; gap:var(--ax-2); flex-wrap:wrap; }
.adsx .ax-spread { display:flex; align-items:center; justify-content:space-between; gap:var(--ax-3);
  flex-wrap:wrap; }

/* ── Type ─────────────────────────────────────────────────────────────── */
.adsx .ax-label { font-size:var(--ax-meta); font-weight:500; line-height:1.35; color:${C.quiet}; }
.adsx .ax-meta { font-size:var(--ax-meta); line-height:1.5; color:${C.quiet}; margin:0; }
.adsx .ax-text { font-size:var(--ax-body); line-height:1.55; color:${C.slate}; margin:0; max-width:72ch; }
.adsx .ax-fig { font-family:var(--font-data); font-size:var(--ax-fig); font-weight:500; line-height:1.15;
  letter-spacing:-.01em; color:${C.ink}; font-variant-numeric:tabular-nums; }
.adsx .ax-num { font-family:var(--font-data); font-variant-numeric:tabular-nums; }
.adsx .ax-good { color:${C.goodInk}; }
.adsx .ax-bad { color:${C.badInk}; }

/* ── Header and screen tabs ───────────────────────────────────────────── */
.adsx .ax-top { display:flex; justify-content:space-between; align-items:center; gap:var(--ax-4);
  flex-wrap:wrap; }
.adsx .ax-brand { display:flex; align-items:center; gap:var(--ax-3); min-width:0; }
.adsx .ax-h1 { margin:0; font-family:var(--font-display); font-size:var(--ax-fig); font-weight:600;
  line-height:1.2; letter-spacing:-.01em; color:${C.ink}; }
.adsx .ax-tabs { display:flex; gap:var(--ax-5); border-bottom:1px solid ${C.line}; overflow-x:auto;
  scrollbar-width:none; }
.adsx .ax-tabs::-webkit-scrollbar { display:none; }
@media (max-width:640px) { .adsx .ax-tabs { gap:var(--ax-4); } }
.adsx .ax-tab { position:relative; flex:none; height:40px; padding:0; border:0; background:none;
  cursor:pointer; font:600 var(--ax-body)/1 var(--font-text); color:${C.quiet}; white-space:nowrap;
  transition:color var(--ax-out) var(--ax-ease); }
.adsx .ax-tab[aria-current="page"] { color:${C.ink}; }
.adsx .ax-tab[aria-current="page"]::after { content:""; position:absolute; left:0; right:0; bottom:0;
  height:2px; border-radius:2px 2px 0 0; background:${C.accent}; }

/* ── Buttons ──────────────────────────────────────────────────────────────
   One secondary look and one filled primary. The filled one marks the main action of a view. */
.adsx .ax-btn { display:inline-flex; align-items:center; justify-content:center; gap:6px;
  height:var(--ax-ctl); padding:0 14px; border-radius:var(--ax-r-sm); border:1px solid ${C.line};
  background:${C.surface}; color:${C.ink}; font:600 var(--ax-body)/1 var(--font-text);
  white-space:nowrap; cursor:pointer;
  transition:background-color var(--ax-out) var(--ax-ease), border-color var(--ax-out) var(--ax-ease),
    color var(--ax-out) var(--ax-ease), transform var(--ax-out) var(--ax-ease); }
.adsx .ax-btn--primary { background:${C.evergreen}; border-color:${C.evergreen}; color:${C.onDark}; }
.adsx .ax-btn:disabled { cursor:not-allowed; background:${C.parchment}; border-color:${C.line};
  color:${C.muted}; }
/* A disabled primary still reads as the main action, only inactive, so Approve keeps its place
   among Preview and Skip while nothing is selected. */
.adsx .ax-btn--primary:disabled { background:color-mix(in srgb, ${C.evergreen} 16%, ${C.surface});
  border-color:transparent; color:color-mix(in srgb, ${C.evergreen} 50%, ${C.surface}); }
.adsx .ax-btn--icon { width:var(--ax-ctl); padding:0; color:${C.slate}; }
.adsx .ax-link { display:inline; padding:0; border:0; background:none; cursor:pointer; font:inherit;
  font-weight:600; color:${C.accent}; text-decoration:underline; text-decoration-thickness:1px;
  text-underline-offset:3px; text-decoration-color:${alpha(C.accent, 0.35)};
  transition:text-decoration-color var(--ax-out) var(--ax-ease); }

/* ── Segmented control: the app shell's period switch, on a card ground ── */
.adsx .ax-seg { display:inline-flex; flex-wrap:wrap; gap:2px; padding:3px; max-width:100%;
  background:${C.surface}; border:1px solid ${C.line}; border-radius:10px; }
.adsx .ax-seg button { height:28px; padding:0 12px; border:0; border-radius:7px; background:transparent;
  cursor:pointer; font:500 var(--ax-meta)/1 var(--font-text); color:${C.slate}; white-space:nowrap;
  transition:background-color var(--ax-out) var(--ax-ease), color var(--ax-out) var(--ax-ease); }
.adsx .ax-seg button[aria-checked="true"] { background:${C.evergreen}; color:${C.onDark}; }

/* ── Cards ────────────────────────────────────────────────────────────── */
.adsx .ax-card { min-width:0; background:${C.surface}; border:1px solid ${C.line};
  border-radius:var(--ax-r); padding:var(--ax-pad); }
.adsx .ax-card--flush { padding:0; overflow:hidden; }
.adsx .ax-card__head { display:flex; justify-content:space-between; align-items:baseline;
  gap:var(--ax-2) var(--ax-3); flex-wrap:wrap; margin-bottom:var(--ax-4); }
.adsx .ax-card--flush > .ax-card__head { padding:var(--ax-pad) var(--ax-pad) 0; }
.adsx .ax-card__title { margin:0; font-family:var(--font-display); font-size:var(--ax-title);
  font-weight:600; line-height:1.3; color:${C.ink}; }
.adsx .ax-card__foot { display:flex; align-items:center; gap:var(--ax-3); flex-wrap:wrap;
  margin-top:var(--ax-5); }
.adsx .ax-card--flush > .ax-card__foot { margin:0; padding:var(--ax-4) var(--ax-pad);
  border-top:1px solid ${C.line}; }
.adsx .ax-section { display:grid; gap:var(--ax-3); }

/* Headline figures: four across, two on a narrow screen. */
.adsx .ax-stats { display:grid; grid-template-columns:repeat(4, minmax(0, 1fr)); gap:var(--ax-4); }
@media (max-width:900px) { .adsx .ax-stats { grid-template-columns:repeat(2, minmax(0, 1fr)); } }
@media (max-width:640px) { .adsx .ax-stats { gap:var(--ax-3); } }
.adsx .ax-stat { position:relative; display:flex; flex-direction:column; gap:var(--ax-2);
  text-align:left; font:inherit; color:inherit; }
.adsx .ax-stat__note { font-size:var(--ax-meta); line-height:1.4; color:${C.slate}; }
.adsx .ax-stat__go { position:absolute; top:var(--ax-pad); right:var(--ax-pad); color:${C.quiet};
  transition:transform var(--ax-out) var(--ax-ease), color var(--ax-out) var(--ax-ease); }
/* A card that opens something lifts 2px; one that does not stays put. */
.adsx .ax-lift { cursor:pointer;
  transition:transform 120ms var(--ax-ease), border-color 120ms var(--ax-ease),
    box-shadow 120ms var(--ax-ease); }

/* Notices: tinted, never loud. */
.adsx .ax-note { display:grid; gap:2px; border-radius:12px; padding:var(--ax-3) var(--ax-4);
  background:${alpha(C.warnInk, 0.06)}; border:1px solid ${alpha(C.warnInk, 0.22)}; }
.adsx .ax-note[data-tone="bad"] { background:${C.badBg}; border-color:${alpha(C.badInk, 0.24)}; }
.adsx .ax-note__title { font-weight:600; color:${C.warnInk}; }
.adsx .ax-note[data-tone="bad"] .ax-note__title { color:${C.badInk}; }
.adsx .ax-note__body { color:${C.slate}; }

/* ── Fields ───────────────────────────────────────────────────────────── */
.adsx .ax-input { height:var(--ax-ctl); width:100%; min-width:0; padding:0 10px;
  border:1px solid ${C.line}; border-radius:var(--ax-r-sm); background:${C.surface}; color:${C.ink};
  font:400 var(--ax-body)/1 var(--font-text);
  transition:border-color 140ms var(--ax-ease), box-shadow 140ms var(--ax-ease); }
.adsx .ax-input::placeholder { color:${C.muted}; }
.adsx .ax-input:focus-visible { outline:none; border-color:${C.accent};
  box-shadow:0 0 0 3px ${alpha(C.accent, 0.18)}; }
.adsx .ax-money { position:relative; display:inline-block; width:132px; flex:none; }
.adsx .ax-money > span { position:absolute; left:11px; top:50%; transform:translateY(-50%);
  font-family:var(--font-data); color:${C.quiet}; pointer-events:none; }
.adsx .ax-money .ax-input { padding-left:24px; font-family:var(--font-data);
  font-variant-numeric:tabular-nums; }
.adsx .ax-search { width:220px; }
@media (max-width:640px) { .adsx .ax-search { width:100%; } }
.adsx input[type="checkbox"] { width:16px; height:16px; margin:0; accent-color:${C.evergreen};
  cursor:pointer; }
.adsx input[type="checkbox"]:disabled { cursor:not-allowed; }
/* A 44px hit area round a 16px box, without moving the box. */
.adsx .ax-check { display:inline-grid; place-items:center; width:40px; height:40px; margin:-12px;
  cursor:pointer; }
.adsx .ax-switch { display:inline-flex; align-items:center; gap:10px; min-height:var(--ax-ctl);
  padding:0; border:0; background:none; cursor:pointer; font:500 var(--ax-body)/1.3 var(--font-text);
  color:${C.ink}; }
.adsx .ax-switch__track { position:relative; flex:none; width:36px; height:20px; border-radius:999px;
  background:${C.line}; transition:background-color 180ms var(--ax-ease); }
.adsx .ax-switch__track::after { content:""; position:absolute; top:2px; left:2px; width:16px;
  height:16px; border-radius:50%; background:${C.surface};
  box-shadow:0 1px 2px ${alpha(C.ink, 0.2)}; transition:transform 180ms var(--ax-ease); }
.adsx .ax-switch[aria-checked="true"] .ax-switch__track { background:${C.badInk}; }
.adsx .ax-switch[aria-checked="true"] .ax-switch__track::after { transform:translateX(16px); }

/* ── Tables ───────────────────────────────────────────────────────────── */
.adsx .ax-scroll { overflow-x:auto; }
.adsx .ax-table { width:100%; border-collapse:collapse; font-size:var(--ax-body); }
.adsx .ax-table th { padding:var(--ax-3) var(--ax-4); text-align:left; vertical-align:bottom;
  font-size:var(--ax-meta); font-weight:500; line-height:1.35; color:${C.quiet}; white-space:nowrap;
  border-bottom:1px solid ${C.line}; }
.adsx .ax-table td { padding:var(--ax-3) var(--ax-4); vertical-align:top; color:${C.ink};
  border-top:1px solid ${C.hair}; }
.adsx .ax-table tbody tr:first-child td { border-top:0; }
.adsx .ax-table th:first-child, .adsx .ax-table td:first-child { padding-left:var(--ax-pad); }
.adsx .ax-table th:last-child, .adsx .ax-table td:last-child { padding-right:var(--ax-pad); }
.adsx .ax-table .num { text-align:right; white-space:nowrap; }
.adsx .ax-table td.num { font-family:var(--font-data); font-variant-numeric:tabular-nums; }
.adsx .ax-table td.ax-strong { font-weight:600; }
.adsx .ax-table td.ax-soft { color:${C.slate}; }
.adsx .ax-table td.ax-nowrap { white-space:nowrap; }
.adsx .ax-table tr.is-link { cursor:pointer; }
.adsx .ax-table tr.is-link td { transition:background-color var(--ax-out) var(--ax-ease); }
.adsx .ax-table tr.is-link:focus-visible { outline:2px solid ${C.accent}; outline-offset:-2px; }
.adsx .ax-table tr.is-on td { background:${C.accentBg}; }
.adsx .ax-table tr.is-on td:first-child { box-shadow:inset 2px 0 0 ${C.accent}; }
.adsx .ax-table--plain th:first-child, .adsx .ax-table--plain td:first-child { padding-left:0; }
.adsx .ax-table--plain th:last-child, .adsx .ax-table--plain td:last-child { padding-right:0; }
@media (max-width:640px) {
  /* A plain table (the guardrails, a drill list) fits a phone instead of scrolling: tighter
     cells, heads that may wrap, a narrower money field. */
  .adsx .ax-table--plain th, .adsx .ax-table--plain td { padding-left:var(--ax-2); padding-right:var(--ax-2); }
  .adsx .ax-table--plain th { white-space:normal; }
  .adsx .ax-table--plain .ax-money { width:100px; }
}
.adsx .ax-sort { display:inline-flex; align-items:center; gap:4px; padding:0; border:0; background:none;
  cursor:pointer; font:inherit; color:inherit; }
.adsx .ax-sort svg { opacity:0; transition:opacity var(--ax-out) var(--ax-ease); }
.adsx .ax-sort[data-on] { color:${C.ink}; }
/* On a number column the arrow goes in front, so the label's right edge sits on the figures'. */
.adsx .ax-table th.num .ax-sort { flex-direction:row-reverse; }
.adsx .ax-sort[data-on] svg { opacity:1; }
.adsx .ax-tag { display:inline-block; margin-left:var(--ax-2); padding:1px 7px; border-radius:6px;
  font-size:var(--ax-meta); font-weight:600; line-height:1.5; vertical-align:1px;
  color:${C.warnInk}; background:${C.warnBg}; }

/* ── Today: the decision list ─────────────────────────────────────────── */
.adsx .ax-actionbar { position:sticky; top:0; z-index:5; display:flex; align-items:center;
  gap:var(--ax-2); flex-wrap:wrap; padding:var(--ax-3) 0; background:${C.page}; }
.adsx .ax-actionbar .ax-meta { margin-left:var(--ax-2); }
@media (max-width:640px) { .adsx .ax-actionbar .ax-meta { flex-basis:100%; margin-left:0; } }
.adsx .ax-dec { display:grid; grid-template-columns:20px minmax(0, 1fr); column-gap:var(--ax-3);
  align-items:start; padding:var(--ax-3) var(--ax-pad); border-top:1px solid ${C.hair};
  transition:background-color var(--ax-out) var(--ax-ease); }
.adsx .ax-dec:first-child { border-top:0; }
.adsx .ax-dec.is-on { background:${C.accentBg}; box-shadow:inset 2px 0 0 ${C.accent}; }
.adsx .ax-dec > .ax-check { margin-top:-10px; }
.adsx .ax-dec--nested { padding-top:var(--ax-2); padding-bottom:var(--ax-2); border-top-color:transparent; }
.adsx .ax-dec__open { display:grid; grid-template-columns:72px minmax(0, 1fr); column-gap:var(--ax-3);
  width:100%; padding:0; border:0; background:none; text-align:left; cursor:pointer; font:inherit;
  color:inherit; }
.adsx .ax-dec__head { grid-column:1 / -1; display:grid;
  grid-template-columns:20px 72px minmax(0, 1fr) auto; column-gap:var(--ax-3); align-items:start;
  width:100%; padding:0; border:0; background:none; text-align:left; cursor:pointer; font:inherit;
  color:inherit; }
.adsx .ax-dec__head > svg { margin-top:2px; color:${C.quiet};
  transition:transform var(--ax-panel) var(--ax-ease); }
.adsx .ax-dec__head[aria-expanded="true"] > svg { transform:rotate(180deg); }
.adsx .ax-verdict { font-size:var(--ax-meta); font-weight:600; line-height:20px; white-space:nowrap; }
.adsx .ax-verdict[data-v="kill"] { color:${C.badInk}; }
.adsx .ax-verdict[data-v="scale"] { color:${C.goodInk}; }
.adsx .ax-verdict[data-v="keep"] { color:${C.quiet}; }
.adsx .ax-dec__name { font-weight:600; line-height:20px; color:${C.ink}; overflow-wrap:anywhere; }
.adsx .ax-dec__group { margin-left:var(--ax-2); font-size:var(--ax-meta); font-weight:400; color:${C.quiet}; }
.adsx .ax-dec__line { margin-top:2px; font-size:var(--ax-meta); line-height:1.5; color:${C.slate}; }
.adsx .ax-dec__more { font-size:var(--ax-meta); font-weight:600; line-height:20px; color:${C.accent};
  white-space:nowrap; }
.adsx .ax-empty { padding:var(--ax-pad); }
@media (max-width:640px) {
  /* The verdict moves above the name, so a long ad name gets the row's whole width. */
  .adsx .ax-dec__open { grid-template-columns:minmax(0, 1fr); }
  .adsx .ax-dec__open > .ax-verdict:empty { display:none; }
  .adsx .ax-dec__head { grid-template-columns:20px minmax(0, 1fr) auto; }
  .adsx .ax-dec__head > .ax-verdict { grid-column:2; grid-row:1; }
  .adsx .ax-dec__head > :nth-child(3) { grid-column:2; grid-row:2; }
  .adsx .ax-dec__head > .ax-dec__more { grid-column:3; grid-row:1; }
}

/* ── Pace bars ────────────────────────────────────────────────────────── */
.adsx .ax-pace { display:grid; gap:var(--ax-4); }
.adsx .ax-pace__head { display:flex; justify-content:space-between; align-items:baseline;
  gap:var(--ax-3); flex-wrap:wrap; }
.adsx .ax-bar { height:8px; margin-top:6px; border-radius:999px; background:${alpha(C.ink, 0.07)};
  overflow:hidden; }
.adsx .ax-bar > i { display:block; height:100%; border-radius:inherit; }

/* Budget: the week chart. */
.adsx .ax-week { display:grid; grid-template-columns:repeat(7, minmax(0, 1fr)); gap:var(--ax-2); }
.adsx .ax-week__plot { position:relative; height:120px; }
.adsx .ax-week__col { position:absolute; inset:0 15%; }
.adsx .ax-week__plan { position:absolute; left:0; right:0; border-top:2px solid ${C.slate}; }
.adsx .ax-week__spend { position:absolute; left:0; right:0; bottom:0; border-radius:4px 4px 0 0; }
.adsx .ax-week__day { margin-top:6px; text-align:center; font-size:var(--ax-meta); color:${C.quiet}; }
.adsx .ax-week__day.is-open { color:${C.slate}; }
.adsx .ax-key { display:flex; gap:var(--ax-4); flex-wrap:wrap; font-size:var(--ax-meta); color:${C.quiet}; }
.adsx .ax-key span { display:inline-flex; align-items:center; gap:6px; }
.adsx .ax-key i { display:inline-block; width:10px; height:10px; border-radius:3px; background:${C.accent}; }
.adsx .ax-key i.is-line { height:0; width:14px; border-radius:0; border-top:2px solid ${C.slate};
  background:none; }

/* Lists of label-and-value rows: connections, the weekly plan, the brand plans. */
.adsx .ax-list > * + * { border-top:1px solid ${C.hair}; }
.adsx .ax-kv { display:flex; justify-content:space-between; align-items:center; gap:var(--ax-4);
  padding:var(--ax-3) 0; }
.adsx .ax-kv:first-child { padding-top:0; }
.adsx .ax-kv__name { font-weight:600; color:${C.ink}; }
.adsx .ax-kv__value { color:${C.slate}; text-align:right; overflow-wrap:anywhere; }
.adsx .ax-listhead { padding-bottom:var(--ax-2); border-bottom:1px solid ${C.line}; }
.adsx .ax-planrow { display:grid; grid-template-columns:minmax(0, 1fr) 132px; gap:var(--ax-4);
  align-items:center; }
.adsx .ax-planrow .ax-money { width:100%; }
@media (max-width:640px) { .adsx .ax-planrow { grid-template-columns:minmax(0, 1fr) 100px; gap:var(--ax-3); } }
@media (max-width:640px) {
  .adsx .ax-kv { flex-direction:column; align-items:flex-start; gap:var(--ax-1); }
  .adsx .ax-kv__value { text-align:left; }
}

/* ── Side panel ───────────────────────────────────────────────────────── */
.adsx .ax-drawer { position:fixed; top:0; right:0; bottom:0; z-index:40; width:min(440px, 100%);
  display:flex; flex-direction:column; gap:var(--ax-4); padding:var(--ax-5); overflow:auto;
  background:${C.surface}; border-left:1px solid ${C.line};
  box-shadow:-24px 0 48px ${alpha(C.ink, 0.08)};
  animation:ax-drawer-in var(--ax-panel) var(--ax-ease) both; }
@keyframes ax-drawer-in { from { opacity:0; transform:translateX(16px); } to { opacity:1; transform:none; } }
@media (max-width:640px) { .adsx .ax-drawer { padding:var(--ax-4); } }
.adsx .ax-drawer__head { display:flex; justify-content:space-between; align-items:flex-start;
  gap:var(--ax-3); }
.adsx .ax-drawer__title { margin:6px 0 0; font-family:var(--font-display); font-size:var(--ax-fig-sm);
  font-weight:600; line-height:1.25; color:${C.ink}; overflow-wrap:anywhere; }
.adsx .ax-dl { display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:var(--ax-4) var(--ax-5);
  margin:0; padding:var(--ax-4) 0; border-top:1px solid ${C.hair}; border-bottom:1px solid ${C.hair}; }
.adsx .ax-dl dt { font-size:var(--ax-meta); color:${C.quiet}; }
.adsx .ax-dl dd { margin:2px 0 0; font-family:var(--font-data); font-size:var(--ax-title);
  font-variant-numeric:tabular-nums; color:${C.ink}; }
.adsx .ax-thumb { display:block; width:100%; max-height:180px; object-fit:cover;
  border-radius:var(--ax-r-sm); border:1px solid ${C.hair}; }

/* ── Focus, hover, press, reduced motion ──────────────────────────────── */
.adsx button:focus-visible, .adsx [role="button"]:focus-visible, .adsx a:focus-visible,
.adsx input[type="checkbox"]:focus-visible, .adsx select:focus-visible, .adsx [tabindex]:focus-visible {
  outline:2px solid ${C.accent}; outline-offset:2px; }
.adsx .ax-tab:focus-visible { outline-offset:-2px; border-radius:6px; }

@media (hover:hover) and (pointer:fine) {
  .adsx .ax-tab:hover { color:${C.ink}; transition-duration:var(--ax-in); }
  .adsx .ax-btn:not(:disabled):hover { transform:translateY(-1px); border-color:${C.hairDeep};
    background:color-mix(in srgb, ${C.surface} 94%, ${C.ink}); transition-duration:var(--ax-in); }
  .adsx .ax-btn--primary:not(:disabled):hover { border-color:${C.evergreen};
    background:color-mix(in srgb, ${C.evergreen} 86%, ${C.surface}); }
  .adsx .ax-seg button:not([aria-checked="true"]):hover { background:${C.parchment}; color:${C.ink};
    transition-duration:var(--ax-in); }
  .adsx .ax-link:hover { text-decoration-color:currentColor; transition-duration:var(--ax-in); }
  .adsx .ax-lift:hover { transform:translateY(-2px); border-color:${C.hairDeep};
    box-shadow:0 8px 24px ${alpha(C.ink, 0.07)}; transition-duration:180ms; }
  .adsx .ax-lift:hover .ax-stat__go { transform:translateX(3px); color:${C.ink}; }
  .adsx .ax-table tr.is-link:hover td { background:${C.parchment}; transition-duration:var(--ax-in); }
  .adsx .ax-table tr.is-on:hover td { background:${C.accentBg}; }
  .adsx .ax-dec:hover { background:${C.parchment}; transition-duration:var(--ax-in); }
  .adsx .ax-dec.is-on:hover { background:${C.accentBg}; }
  .adsx .ax-sort:hover svg { opacity:.5; }
  .adsx .ax-sort[data-on]:hover svg { opacity:1; }
}
.adsx .ax-btn:not(:disabled):active { transform:translateY(1px) scale(.985);
  transition-duration:var(--ax-press); }
.adsx .ax-lift:active { transform:translateY(0); transition-duration:var(--ax-press); }

/* Touch screens, and any phone-width window: every control 44px tall. */
@media (pointer:coarse), (max-width:640px) {
  .adsx .ax-btn, .adsx .ax-input { min-height:44px; }
  .adsx .ax-btn--icon { width:44px; }
  .adsx .ax-seg button { height:44px; }
  .adsx .ax-tab { height:44px; }
  .adsx .ax-switch { min-height:44px; }
  .adsx .ax-table th .ax-sort { min-height:44px; margin:-12px 0; }
  .adsx .ax-check { width:44px; height:44px; margin:-14px; }
  .adsx .ax-dec > .ax-check { margin-top:-12px; }
  .adsx .ax-dec__head, .adsx .ax-dec__open { min-height:44px; }
}

@media (prefers-reduced-motion:reduce) {
  .adsx *, .adsx *::before, .adsx *::after {
    transition-property:color, background-color, border-color, text-decoration-color, opacity !important;
    animation:none !important; }
  .adsx .ax-btn:active, .adsx .ax-btn:hover, .adsx .ax-lift:hover, .adsx .ax-lift:active,
  .adsx .ax-lift:hover .ax-stat__go { transform:none !important; }
}
`;
