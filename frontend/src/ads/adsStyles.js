/* The approved mockup's stylesheet, ported verbatim and SCOPED.
 *
 * SPEC-ads-module.md Part 13: where the mockup and the spec disagree, the spec wins on data
 * and behaviour and the MOCKUP WINS ON LAYOUT. So this is a mechanical port rather than a
 * re-interpretation - retyping 442 lines of design by hand is how a port stops being one.
 *
 * Every selector is prefixed with `.adsx`. The mockup is a standalone page, so its class
 * names are generic - `.card`, `.head`, `.band`, `.wrap`, `.table` - and dropping those into
 * an app that already has a Portfolio, a Launch tab and a Settings page would restyle all of
 * them. LaunchSection scopes under `.bcl` for the same reason. `.root` becomes the scope
 * itself, so the tab renders one element carrying className="adsx".
 *
 * Colours interpolate from adsTokens, which is still the only file here permitted a hex.
 */
import { ASSET, C } from "./adsTokens.js";

export const adsCss = () => `
@import url('https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700&family=Inter:wght@400;500;600&display=swap');

/* ── One type scale. Nine steps, and nothing between them. ───────────── */
.adsx {
  --a-hero:52px; --a-xl:28px; --a-title:20px; --a-lg:16px; --a-fig:13px;
  --a-body:12px; --a-small:11px; --a-cap:10px; --a-micro:9px;
  --a-gut:26px; --a-pad:22px;
  background:${C.page}; min-height:100%; font-family:Inter,sans-serif; color:${C.ink};
  text-wrap:pretty;
}
@media (max-width:640px){.adsx{ --a-hero:38px; --a-xl:24px; --a-title:17px; --a-gut:16px; --a-pad:16px; } }
.adsx .wrap { max-width:1060px; margin:0 auto; }

/* Figures: digits lead, units follow. Applied to headline figures only — never
   inside a column of table numbers, where every glyph must hold its cell. */
.adsx .unit { font-style:normal; font-size:.56em; font-weight:700; opacity:.6; letter-spacing:0;
  margin:0 .05em; }

/* ── Product chrome ──────────────────────────────────────────────────── */
.adsx .head { padding:22px var(--a-gut) 18px; }
.adsx .phead { display:flex; align-items:center; gap:12px; flex-wrap:wrap; }
.adsx .ptitle { font-family:Poppins,sans-serif; font-size:var(--a-title); font-weight:700; letter-spacing:-.015em; }
.adsx .psub { font-size:var(--a-body); color:${C.muted}; }
.adsx .spacer { flex:1; }
.adsx .srcs { display:flex; gap:6px; flex-wrap:wrap; }
.adsx .src { font-size:var(--a-cap); font-weight:600; color:${C.slate}; background:${C.parchment};
  border:1px solid ${C.hair}; border-radius:5px; padding:3px 8px; white-space:nowrap; }

.adsx .ctrl { display:flex; align-items:center; gap:10px; flex-wrap:wrap; margin-top:14px; }
.adsx .periods, .adsx .basis { display:flex; gap:5px; }
.adsx .per { font-family:Inter,sans-serif; font-size:var(--a-small); font-weight:600; color:${C.slate};
  background:${C.surface}; border:1px solid ${C.hair}; border-radius:7px; padding:6px 12px; cursor:pointer; }
.adsx .per:hover { border-color:${C.teal}; color:${C.teal}; }
.adsx .per.on { background:${C.evergreen}; border-color:${C.evergreen}; color:${C.onDark}; }
.adsx .basis { margin-left:6px; padding-left:12px; border-left:1px solid ${C.hair}; }
.adsx .range { font-size:var(--a-small); color:${C.muted}; display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
.adsx .range::before { content:''; width:20px; height:1px; background:${C.hairDeep}; }

/* ── The dark band. Full width, one edge into the light page. ────────── */
.adsx .band { background-color:${C.evergreen}; background-image:url(${ASSET.ever});
  background-size:cover; background-position:center; background-blend-mode:multiply;
  padding:26px var(--a-gut) 22px; border-bottom:1px solid rgba(184,204,184,.28);
  box-shadow:0 18px 34px -26px rgba(0,46,44,.55); }
.adsx .hero { position:relative; }
.adsx .hgrid { display:grid; grid-template-columns:minmax(0,1fr) minmax(290px,380px); gap:30px;
  align-items:stretch; }
.adsx .eyebrow { font-family:Poppins,sans-serif; font-size:var(--a-cap); font-weight:700; letter-spacing:.13em;
  text-transform:uppercase; color:${C.sprout}; }
.adsx .heronum { font-family:Poppins,sans-serif; font-size:var(--a-hero); font-weight:700; color:${C.onDark};
  line-height:.94; letter-spacing:-.03em; margin-top:14px; font-variant-numeric:tabular-nums; }
.adsx .hroas { display:flex; align-items:baseline; gap:9px; margin-top:12px; }
.adsx .hroas b { font-family:Poppins,sans-serif; font-size:var(--a-xl); font-weight:700; color:${C.sprout};
  letter-spacing:-.02em; font-variant-numeric:tabular-nums; }
.adsx .hroas em { font-style:normal; font-size:var(--a-small); font-weight:500; color:${C.onDarkMute}; }
.adsx .hline { font-size:var(--a-body); color:${C.onDarkMute}; margin-top:12px; }

/* TWO TOTALS, NOT ONE. A financed enrollment contracts the full plan price and puts a deposit
   down; a hero that headlines either one alone is wrong in a direction. They sit side by side
   with a rule between them, sized so the hierarchy still reads - contracted is the headline,
   cash received is the check on it - and they stack rather than shrink below 640px, where two
   52px figures on a 343px content box would collide. */
.adsx .hduo { display:flex; align-items:flex-start; gap:22px; margin-top:14px; flex-wrap:wrap; }
.adsx .hduo .heronum { margin-top:4px; }
.adsx .hduo .hsplit { align-self:stretch; width:1px; background:rgba(248,245,242,.2); }
.adsx .hfig { min-width:0; }
.adsx .hfig.alt .heronum { font-size:var(--a-xl); color:${C.onDarkMute}; }
.adsx .hflab { display:block; font-family:Poppins,sans-serif; font-size:var(--a-micro);
  font-weight:700; letter-spacing:.13em; text-transform:uppercase; color:${C.onDarkMute}; }
.adsx .hfsub { font-size:var(--a-small); color:${C.onDarkMute}; margin-top:6px; display:block; }
@media (max-width:640px) {
  .adsx .hduo { gap:14px; }
  .adsx .hduo .hsplit { display:none; }
  .adsx .hfig { flex:1 1 100%; }
}

.adsx .lenses { display:flex; flex-direction:column; border:1px solid rgba(248,245,242,.16);
  border-radius:12px; overflow:hidden; background:rgba(248,245,242,.04); }
.adsx .lhead { padding:8px 14px; border-bottom:1px solid rgba(248,245,242,.14);
  font-family:Poppins,sans-serif; font-size:var(--a-micro); font-weight:700; letter-spacing:.14em;
  text-transform:uppercase; color:${C.onDarkMute}; }
.adsx .lens { flex:1; display:grid; grid-template-columns:1fr auto; align-content:center;
  gap:3px 12px; padding:11px 14px; border-top:1px solid rgba(248,245,242,.1); }
.adsx .lens:nth-of-type(2) { border-top:none; }
.adsx .lens.on { background:rgba(248,245,242,.12); box-shadow:inset 3px 0 0 ${C.sprout}; }
.adsx .lens.on + .lens.on { border-top-color:rgba(248,245,242,.3); }
.adsx .llab { display:inline-flex; align-items:center; gap:7px; font-family:Poppins,sans-serif;
  font-size:var(--a-micro); font-weight:700; letter-spacing:.12em; text-transform:uppercase;
  color:${C.onDarkMute}; }
.adsx .lens.on .llab { color:${C.onDark}; }
.adsx .llab em { font-style:normal; font-size:var(--a-micro); font-weight:600; letter-spacing:.06em;
  color:${C.evergreen}; background:${C.sprout}; border-radius:4px; padding:1px 5px; }
.adsx .lnum { display:inline-flex; align-items:baseline; gap:8px; justify-self:end;
  font-family:Poppins,sans-serif; font-size:var(--a-fig); font-weight:700; color:${C.onDarkMute};
  font-variant-numeric:tabular-nums; }
.adsx .lnum i { font-style:normal; font-size:var(--a-small); font-weight:700; color:${C.onDarkMute}; }
.adsx .lens.on .lnum { font-size:var(--a-lg); color:${C.onDark}; }
.adsx .lens.on .lnum i { color:${C.sprout}; }
.adsx .lsub { grid-column:1/-1; font-size:var(--a-cap); color:${C.onDarkMute}; }
@media (max-width:980px){
  .adsx .hgrid { grid-template-columns:1fr; gap:22px; }
  .adsx .lenses { flex-direction:row; }
  .adsx .lhead { display:none; }
  .adsx .lens { border-top:none; border-left:1px solid rgba(248,245,242,.12); }
  .adsx .lens.on { box-shadow:inset 0 3px 0 ${C.sprout}; }
  .adsx .lens:nth-of-type(2) { border-left:none; }
}
@media (max-width:640px){
  .adsx .lenses { flex-direction:column; }
  .adsx .lens { border-left:none; border-top:1px solid rgba(248,245,242,.1); }
  .adsx .lens.on { box-shadow:inset 3px 0 0 ${C.sprout}; }
}
.adsx .herofoot { display:flex; justify-content:space-between; align-items:flex-start; gap:16px;
  flex-wrap:wrap; margin-top:20px; padding-top:14px; border-top:1px solid rgba(248,245,242,.18);
  font-size:var(--a-small); color:${C.onDarkMute}; line-height:1.6; }
.adsx .hfoot-l { display:inline-flex; align-items:flex-start; gap:9px; max-width:74ch; }
.adsx .hfoot-r { color:${C.sprout}; font-weight:600; white-space:nowrap; }
.adsx .hdot { width:7px; height:7px; border-radius:2px; background:${C.flagDot}; flex-shrink:0; margin-top:6px; }

/* ── Sticky jump rail — the joint between hero and page ──────────────── */
.adsx .rail { position:sticky; top:0; z-index:20; background:${C.page};
  border-bottom:1px solid ${C.hair}; padding:0 var(--a-gut); }
.adsx .railin { display:flex; align-items:center; gap:4px; max-width:1060px; margin:0 auto;
  overflow-x:auto; scrollbar-width:none; }
.adsx .railin::-webkit-scrollbar { display:none; }
.adsx .rlink { display:inline-flex; align-items:baseline; gap:7px; padding:12px 11px;
  font-size:var(--a-small); font-weight:600; color:${C.muted}; text-decoration:none;
  white-space:nowrap; border-bottom:2px solid transparent; margin-bottom:-1px; }
.adsx .rlink b { font-family:Poppins,sans-serif; font-size:var(--a-micro); font-weight:700;
  letter-spacing:.08em; color:${C.hairDeep}; font-variant-numeric:tabular-nums; }
.adsx .rlink:hover { color:${C.teal}; }
.adsx .rlink.on { color:${C.ink}; border-bottom-color:${C.teal}; }
.adsx .rlink.on b { color:${C.teal}; }
.adsx .railsum { margin-left:auto; padding-left:20px; font-size:var(--a-small); color:${C.muted};
  white-space:nowrap; font-variant-numeric:tabular-nums; }
.adsx .railsum b { font-family:Poppins,sans-serif; color:${C.ink}; font-weight:700; }
@media (max-width:1000px){.adsx .railsum{ display:none; } }

/* ── Sections ────────────────────────────────────────────────────────── */
.adsx .page { padding:0 var(--a-gut) 44px; }
.adsx .sect { scroll-margin-top:56px; padding-top:34px; }
.adsx .sect:first-child { padding-top:26px; }
.adsx .shead { display:flex; align-items:center; gap:12px; flex-wrap:wrap; margin-bottom:15px; }
.adsx .sn { font-family:Poppins,sans-serif; font-size:var(--a-cap); font-weight:700; letter-spacing:.14em;
  color:${C.teal}; font-variant-numeric:tabular-nums; }
.adsx .st { margin:0; font-family:Poppins,sans-serif; font-size:var(--a-title); font-weight:700;
  letter-spacing:-.015em; color:${C.ink}; }
.adsx .srule { flex:1 1 24px; min-width:16px; height:1px; background:${C.hairDeep}; }
.adsx .slede { font-size:var(--a-small); color:${C.muted}; max-width:52ch; text-align:right; }
@media (max-width:760px){.adsx .slede{ text-align:left; flex-basis:100%; }.adsx .srule{ display:none; } }
.adsx .sbody { display:flex; flex-direction:column; gap:16px; }

/* ── Card shell ──────────────────────────────────────────────────────── */
.adsx .card { background:${C.surface}; border:1px solid ${C.hair}; border-radius:14px;
  padding:var(--a-pad) calc(var(--a-pad) + 2px); box-shadow:0 1px 2px rgba(0,46,44,.04); }
.adsx .row2 { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
@media (max-width:900px){.adsx .row2{ grid-template-columns:1fr; } }
.adsx .kick { display:flex; align-items:center; justify-content:space-between; gap:12px;
  flex-wrap:wrap; margin-bottom:16px; }
.adsx .klead { display:inline-flex; align-items:center; gap:9px; font-family:Poppins,sans-serif;
  font-size:var(--a-cap); font-weight:700; letter-spacing:.1em; text-transform:uppercase;
  color:${C.slate}; }
.adsx .klead i { width:3px; height:13px; border-radius:2px; background:${C.evergreen}; }
.adsx .kmeta { font-size:var(--a-small); color:${C.muted}; }
.adsx .note { font-size:var(--a-small); color:${C.muted}; line-height:1.6; margin-top:14px;
  max-width:88ch; }
.adsx .note code, .adsx .blocked code { font-family:ui-monospace,monospace; font-size:var(--a-cap);
  background:${C.parchment}; border:1px solid ${C.hair}; border-radius:4px; padding:1px 5px; }
.adsx .blocked { display:flex; align-items:flex-start; gap:9px; background:${C.flagBg};
  border-radius:10px; padding:12px 14px; margin-bottom:16px; font-size:var(--a-small);
  color:${C.flagText}; line-height:1.6; }
.adsx .blocked b { color:${C.ink}; }
.adsx .tag { font-size:var(--a-cap); font-weight:700; border-radius:5px; padding:2px 7px; }

/* ── 02 · The funnel, as a descent ───────────────────────────────────── */
.adsx .ladder { display:flex; flex-direction:column; }
.adsx .sp { position:relative; align-self:stretch; }
.adsx .sp::before { content:''; position:absolute; left:10px; top:0; bottom:0; width:1px; background:${C.hairDeep}; }
.adsx .sp > i { position:absolute; left:6px; top:50%; margin-top:-4.5px; width:9px; height:9px;
  border-radius:3px; box-shadow:0 0 0 4px ${C.surface}; }
.adsx .rung { display:grid; grid-template-columns:26px minmax(0,1fr) 84px minmax(90px,1.2fr) 58px 74px;
  gap:0 12px; align-items:center; padding:9px 0; }
.adsx .rlab { font-size:var(--a-body); color:${C.body}; display:inline-flex; align-items:center;
  gap:8px; flex-wrap:wrap; }
.adsx .rung.close .rlab { font-weight:700; color:${C.ink}; }
.adsx .rung.diag .rlab { color:${C.muted}; }
.adsx .rlab em { font-style:normal; font-size:var(--a-micro); font-weight:600; letter-spacing:.05em;
  text-transform:uppercase; color:${C.muted}; background:${C.parchment};
  border:1px solid ${C.hair}; border-radius:4px; padding:1px 6px; }
.adsx .rn { font-family:Poppins,sans-serif; font-size:var(--a-fig); font-weight:700; color:${C.ink};
  text-align:right; font-variant-numeric:tabular-nums; }
.adsx .rung.close .rn { font-size:var(--a-lg); color:${C.meadow}; }
.adsx .rtrack { display:block; height:9px; background:${C.parchment}; border-radius:5px; overflow:hidden; }
.adsx .rtrack i { display:block; height:100%; border-radius:5px; transition:width .2s ease; }
.adsx .rconv { font-size:var(--a-small); color:${C.slate}; text-align:right; font-variant-numeric:tabular-nums; }
.adsx .rcost { font-family:Poppins,sans-serif; font-size:var(--a-small); font-weight:600; color:${C.muted};
  text-align:right; font-variant-numeric:tabular-nums; }
.adsx .rmeta { display:none; }
.adsx .rung.leak { background:${C.flagBg}; border-radius:8px; }
.adsx .rung.leak .rlab { font-weight:600; color:${C.ink}; }
.adsx .rung.leak .sp > i { box-shadow:0 0 0 4px ${C.flagBg}; }
.adsx .rung.leak .rtrack { background:rgba(109,83,54,.1); }

.adsx .rdrop { display:grid; grid-template-columns:26px 1fr; align-items:center; height:19px; }
.adsx .dropn { font-size:var(--a-micro); font-weight:600; letter-spacing:.05em; color:${C.muted};
  font-variant-numeric:tabular-nums; }
.adsx .finding { display:grid; grid-template-columns:26px 1fr; align-items:stretch;
  padding:4px 0 10px; }
.adsx .fbody { display:block; font-size:var(--a-small); color:${C.flagText}; line-height:1.6;
  max-width:76ch; }
.adsx .fbody b { display:inline-block; font-family:Poppins,sans-serif; font-size:var(--a-micro);
  font-weight:700; letter-spacing:.11em; text-transform:uppercase; color:${C.flagText};
  margin-right:8px; }

.adsx .zmeta { display:flex; align-items:center; gap:11px; margin:4px 0 6px; }
.adsx .zmeta > span:first-child { font-family:Poppins,sans-serif; font-size:var(--a-cap); font-weight:700;
  letter-spacing:.14em; text-transform:uppercase; color:${C.slate}; }
.adsx .zmeta i { flex:1; height:1px; background:${C.hair}; }
.adsx .zmeta > span:last-child { font-size:var(--a-cap); color:${C.muted}; }
.adsx .zcross { display:flex; align-items:baseline; justify-content:space-between; gap:14px;
  flex-wrap:wrap; background:${C.evergreen}; border-radius:9px; padding:11px 16px;
  margin:18px 0 8px; }
.adsx .zbig { font-family:Poppins,sans-serif; font-size:var(--a-cap); font-weight:700;
  letter-spacing:.16em; text-transform:uppercase; color:${C.onDark}; }
.adsx .zsub { font-size:var(--a-small); color:${C.sprout}; }
.adsx .legend { display:flex; justify-content:space-between; gap:16px; flex-wrap:wrap; margin-top:16px;
  padding-top:13px; border-top:1px solid ${C.parchment}; font-size:var(--a-small);
  color:${C.muted}; line-height:1.6; }
.adsx .legend > span:first-child { max-width:74ch; }
.adsx .lcost { white-space:nowrap; }

@media (max-width:780px){
  .adsx .rung { grid-template-columns:22px minmax(0,1fr) auto; gap:0 10px; align-items:baseline;
    padding:10px 8px 10px 0; }
  .adsx .rung .sp { grid-row:1/4; align-self:stretch; }
  .adsx .rung .rlab { grid-column:2; }
  .adsx .rung .rn { grid-column:3; }
  .adsx .rung .rtrack { grid-column:2/-1; margin:7px 0 6px; }
  .adsx .rung .rconv, .adsx .rung .rcost { display:none; }
  .adsx .rung .rmeta { display:block; grid-column:2/-1; font-size:var(--a-cap); color:${C.muted};
    font-variant-numeric:tabular-nums; }
  .adsx .rung.close .rn { font-size:var(--a-title); }
  .adsx .rdrop, .adsx .finding { grid-template-columns:22px 1fr; }
  .adsx .sp::before { left:8px; }
  .adsx .sp > i { left:4px; }
  .adsx .zcross { margin:14px 0 6px; }
}

/* ── 03 · Cohort + findings ──────────────────────────────────────────── */
.adsx .stack { display:flex; height:13px; border-radius:7px; overflow:hidden; gap:2px; }
.adsx .skey { display:flex; gap:18px; flex-wrap:wrap; margin-top:12px; font-size:var(--a-small);
  color:${C.slate}; }
.adsx .skey span { display:inline-flex; align-items:center; gap:7px; }
.adsx .skey i { width:8px; height:8px; border-radius:2px; }
.adsx .skey b { font-family:Poppins,sans-serif; color:${C.ink}; font-variant-numeric:tabular-nums; }
.adsx .mgrid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px 22px; margin-top:18px;
  padding-top:15px; border-top:1px solid ${C.parchment}; }
.adsx .mgrid > div { display:flex; justify-content:space-between; align-items:baseline; gap:10px;
  font-size:var(--a-body); color:${C.body}; }
.adsx .mgrid b { font-family:Poppins,sans-serif; font-size:var(--a-fig); font-weight:700; color:${C.ink};
  font-variant-numeric:tabular-nums; }
@media (max-width:560px){.adsx .mgrid{ grid-template-columns:1fr; } }

.adsx .finds { display:grid; grid-template-columns:1fr 1fr; column-gap:26px; }
@media (max-width:820px){.adsx .finds{ grid-template-columns:1fr; } }
.adsx .find { display:grid; grid-template-columns:9px 1fr; gap:2px 10px; align-items:baseline;
  padding:11px 0; border-top:1px solid ${C.parchment}; }
.adsx .finds > .find:nth-child(-n+2) { border-top:none; padding-top:2px; }
@media (max-width:820px){.adsx .finds > .find:nth-child(2){ border-top:1px solid ${C.parchment}; padding-top:11px; } }
.adsx .fdot { width:7px; height:7px; border-radius:2px; transform:translateY(-1px); }
.adsx .fhead { font-family:Poppins,sans-serif; font-size:var(--a-fig); font-weight:700;
  letter-spacing:-.005em; line-height:1.35; overflow-wrap:anywhere; }
.adsx .ftext { grid-column:2; font-size:var(--a-small); color:${C.muted}; line-height:1.55; }

/* ── The click layer, as a strip ─────────────────────────────────────── */
.adsx .strip { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); background:${C.surface};
  border:1px solid ${C.hair}; border-radius:14px; overflow:hidden;
  box-shadow:0 1px 2px rgba(0,46,44,.04); }
.adsx .kpi { padding:14px 18px 15px; border-top:1px solid ${C.parchment}; border-left:1px solid ${C.parchment}; }
.adsx .kpi:nth-child(-n+4) { border-top:none; }
.adsx .kpi:nth-child(4n+1) { border-left:none; }
@media (max-width:900px){
  .adsx .strip { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .adsx .kpi:nth-child(-n+4) { border-top:1px solid ${C.parchment}; }
  .adsx .kpi:nth-child(4n+1) { border-left:1px solid ${C.parchment}; }
  .adsx .kpi:nth-child(-n+2) { border-top:none; }
  .adsx .kpi:nth-child(2n+1) { border-left:none; }
}
.adsx .klab { font-family:Poppins,sans-serif; font-size:var(--a-micro); font-weight:700; letter-spacing:.1em;
  text-transform:uppercase; color:${C.slate}; }
.adsx .kval { display:flex; align-items:baseline; gap:8px; flex-wrap:wrap;
  font-family:Poppins,sans-serif; font-size:var(--a-title); font-weight:700; color:${C.ink};
  line-height:1.1; letter-spacing:-.02em; margin-top:9px; font-variant-numeric:tabular-nums; }
.adsx .ksub { font-size:var(--a-cap); color:${C.muted}; margin-top:6px; }

/* ── 04 · Groups ─────────────────────────────────────────────────────── */
.adsx .grp { border-top:1px solid ${C.parchment}; }
.adsx .grp:first-of-type { border-top:none; }
.adsx .ghead { display:flex; align-items:center; justify-content:space-between; gap:12px; width:100%;
  background:none; border:none; padding:13px 6px; cursor:pointer; text-align:left; border-radius:9px; }
.adsx .ghead:hover { background:${C.parchment}; }
.adsx .gleft { display:inline-flex; align-items:center; gap:10px; min-width:0; }
.adsx .gcount { font-family:Poppins,sans-serif; font-size:var(--a-cap); font-weight:700; color:${C.teal};
  background:${C.mist}; border-radius:20px; padding:2px 9px; font-variant-numeric:tabular-nums; }
.adsx .gname { font-family:Poppins,sans-serif; font-size:var(--a-fig); font-weight:600; color:${C.ink}; }
.adsx .gright { display:inline-flex; align-items:center; gap:18px; }
@media (max-width:860px){.adsx .gright .gstat:nth-child(-n+2){ display:none; }.adsx .gright{ gap:13px; } }
.adsx .gstat { display:flex; flex-direction:column; align-items:flex-end; }
.adsx .gstat b { font-family:Poppins,sans-serif; font-size:var(--a-fig); font-weight:700; color:${C.ink};
  font-variant-numeric:tabular-nums; }
.adsx .gstat em { font-style:normal; font-size:var(--a-micro); font-weight:600; letter-spacing:.07em;
  text-transform:uppercase; color:${C.muted}; margin-top:3px; }
.adsx .gbody { padding:2px 0 10px; }
.adsx .grow { display:grid; grid-template-columns:1fr 84px 62px 92px 14px; gap:10px; align-items:center;
  width:100%; background:none; border:none; border-top:1px solid ${C.parchment}; cursor:pointer;
  padding:9px 6px; text-align:left; font-family:Inter,sans-serif; font-size:var(--a-body); }
.adsx .grow:hover { background:${C.parchment}; }
.adsx .grow:hover .gcname { color:${C.teal}; }
.adsx .gcname { color:${C.body}; font-weight:500; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.adsx .gnum { text-align:right; font-family:Poppins,sans-serif; font-weight:600; color:${C.ink};
  font-variant-numeric:tabular-nums; }
@media (max-width:640px){.adsx .grow{ grid-template-columns:1fr 74px 84px; }.adsx .grow .gnum:nth-of-type(2){ display:none; } }

/* ── 05 · Creative wall — the hook leads, the missing image is a chip ── */
.adsx .pills { display:flex; gap:5px; flex-wrap:wrap; }
.adsx .pill { font-family:Inter,sans-serif; font-size:var(--a-small); font-weight:600; color:${C.slate};
  background:${C.surface}; border:1px solid ${C.hair}; border-radius:20px; padding:5px 12px; cursor:pointer; }
.adsx .pill:hover { border-color:${C.teal}; color:${C.teal}; }
.adsx .pill.on { background:${C.evergreen}; border-color:${C.evergreen}; color:${C.onDark}; }
.adsx .filt { display:inline-flex; align-items:center; gap:8px; margin-left:10px; background:${C.mist};
  border-radius:20px; padding:3px 11px; font-family:Inter,sans-serif; font-size:var(--a-small);
  font-weight:600; color:${C.teal}; text-transform:none; letter-spacing:0; }
.adsx .filt span { cursor:pointer; opacity:.65; }

.adsx .wall { display:grid; grid-template-columns:repeat(auto-fill,minmax(232px,1fr)); gap:12px; }
@media (max-width:560px){.adsx .wall{ grid-template-columns:1fr; } }
.adsx .ad { border:1px solid ${C.hair}; border-radius:11px; padding:11px 12px 12px; display:flex;
  flex-direction:column; gap:6px; background:${C.surface}; transition:border-color .15s ease; }
.adsx .ad:hover { border-color:${C.teal}; }
.adsx .adtop { display:flex; align-items:center; gap:9px; }
.adsx .thumb { width:40px; height:40px; border-radius:7px; flex-shrink:0; display:flex;
  align-items:center; justify-content:center; }
.adsx .adtags { display:flex; align-items:center; gap:6px; flex-wrap:wrap; min-width:0; }
.adsx .adrank { font-family:Poppins,sans-serif; font-size:var(--a-micro); font-weight:700; color:${C.slate};
  background:${C.parchment}; border:1px solid ${C.hair}; border-radius:20px; padding:2px 7px;
  font-variant-numeric:tabular-nums; }
.adsx .adfmt { font-family:ui-monospace,monospace; font-size:var(--a-micro); color:${C.slate};
  letter-spacing:.03em; }
.adsx .notag { font-size:var(--a-micro); font-weight:700; color:${C.flagText}; background:${C.flagBg};
  border-radius:4px; padding:2px 6px; }
.adsx .view { margin-left:auto; display:inline-flex; align-items:center; gap:3px; font-size:var(--a-micro);
  font-weight:600; color:${C.teal}; opacity:0; transition:opacity .15s ease; white-space:nowrap; }
.adsx .ad:hover .view { opacity:1; }
.adsx .adhead { font-family:Poppins,sans-serif; font-size:var(--a-body); font-weight:600; color:${C.ink};
  line-height:1.4; margin-top:2px; display:-webkit-box; -webkit-line-clamp:3;
  -webkit-box-orient:vertical; overflow:hidden; }
.adsx .adhead.none { font-family:Inter,sans-serif; font-weight:500; color:${C.muted}; font-style:italic; }
.adsx .adname { font-size:var(--a-cap); color:${C.slate}; white-space:nowrap; overflow:hidden;
  text-overflow:ellipsis; }
.adsx .adcamp { font-size:var(--a-micro); color:${C.muted}; white-space:nowrap; overflow:hidden;
  text-overflow:ellipsis; }
.adsx .admetrics { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:6px; margin-top:auto;
  padding-top:9px; border-top:1px solid ${C.parchment}; }
.adsx .m { display:flex; flex-direction:column; gap:2px; }
.adsx .m b { font-family:Poppins,sans-serif; font-size:var(--a-body); font-weight:700; color:${C.ink};
  font-variant-numeric:tabular-nums; }
.adsx .m em { font-style:normal; font-size:var(--a-micro); font-weight:600; letter-spacing:.04em;
  text-transform:uppercase; color:${C.muted}; }
.adsx .m.hl b { color:${C.teal}; }

/* ── 06 · Bars ───────────────────────────────────────────────────────── */
.adsx .bars { display:flex; flex-direction:column; gap:9px; }
.adsx .bar { display:grid; grid-template-columns:210px minmax(0,1fr) 78px; gap:14px; align-items:center; }
.adsx .pbar { display:grid; grid-template-columns:210px minmax(0,1fr) 56px; gap:14px; align-items:center; }
@media (max-width:640px){
  .adsx .bar, .adsx .pbar { grid-template-columns:1fr 70px; gap:8px 12px; }
  .adsx .bar .btrack, .adsx .pbar .ptracks { grid-column:1/-1; order:3; }
}
.adsx .blab { font-size:var(--a-small); color:${C.body}; overflow:hidden; text-overflow:ellipsis;
  white-space:nowrap; }
.adsx .btrack { display:block; height:9px; background:${C.parchment}; border-radius:5px; overflow:hidden; }
.adsx .btrack i { display:block; height:100%; border-radius:5px; transition:width .2s ease; }
.adsx .ptracks { display:flex; flex-direction:column; gap:3px; }
.adsx .bval { text-align:right; font-family:Poppins,sans-serif; font-size:var(--a-fig); font-weight:700;
  font-variant-numeric:tabular-nums; }
.adsx .pkey { display:flex; gap:14px; font-size:var(--a-small); color:${C.slate}; }
.adsx .pkey span { display:inline-flex; align-items:center; gap:6px; }
.adsx .pkey i { width:9px; height:9px; border-radius:2px; }

/* ── 07 · Table ──────────────────────────────────────────────────────── */
.adsx .tw { overflow-x:auto; margin:0 calc(-1 * (var(--a-pad) + 2px)) calc(-1 * var(--a-pad));
  padding:0 calc(var(--a-pad) + 2px) var(--a-pad); }
.adsx table { width:100%; border-collapse:collapse; font-family:Inter,sans-serif; font-size:var(--a-body); }
.adsx th { text-align:right; padding:9px; font-family:Poppins,sans-serif; font-size:var(--a-micro); font-weight:700;
  letter-spacing:.08em; text-transform:uppercase; color:${C.muted}; background:${C.parchment};
  cursor:pointer; white-space:nowrap; user-select:none; }
.adsx th:first-child { text-align:left; border-radius:7px 0 0 7px; }
.adsx th:last-child { border-radius:0 7px 7px 0; }
.adsx th:hover { color:${C.ink}; } th.on { color:${C.teal}; }
.adsx td { padding:11px 9px; border-top:1px solid ${C.parchment}; text-align:right;
  font-family:Poppins,sans-serif; font-size:var(--a-body); font-weight:600; color:${C.ink};
  font-variant-numeric:tabular-nums; white-space:nowrap; }
.adsx td.cn { text-align:left; font-family:Inter,sans-serif; font-weight:500; color:${C.body};
  max-width:230px; overflow:hidden; text-overflow:ellipsis; }
.adsx tr:hover td { background:${C.parchment}; }
.adsx .sdot { display:inline-block; width:7px; height:7px; border-radius:50%; margin-right:8px; }
@media (max-width:780px){
  .adsx .tw { overflow:visible; }
  .adsx thead { display:none; }
  .adsx table, .adsx tbody, .adsx tr, .adsx td { display:block; width:100%; }
  .adsx tr { border:1px solid ${C.hair}; border-radius:11px; padding:12px 14px; margin-bottom:9px; }
  .adsx tr:hover td { background:none; }
  .adsx td { border:none; padding:3px 0; text-align:right; display:flex; justify-content:space-between;
    align-items:baseline; }
  .adsx td::before { content:attr(data-l); font-family:Inter,sans-serif; font-size:var(--a-small);
    font-weight:500; color:${C.muted}; }
  .adsx td.cn { max-width:none; white-space:normal; font-weight:600; color:${C.ink}; font-size:var(--a-fig);
    padding-bottom:9px; margin-bottom:6px; border-bottom:1px solid ${C.parchment}; display:block; }
  .adsx td.cn::before { content:none; }
  .adsx td[data-l="CTR"], .adsx td[data-l="Cost/reg"] { display:none; }
}

/* ── 08 · Coverage + books ───────────────────────────────────────────── */
.adsx .cgrid { margin-top:14px; }
.adsx .crow { display:grid; grid-template-columns:1fr 44px 1.1fr; gap:12px; align-items:baseline;
  padding:8px 0; border-top:1px solid ${C.parchment}; font-size:var(--a-body); }
.adsx .crow:first-child { border-top:none; }
.adsx .cl { display:inline-flex; align-items:center; gap:8px; color:${C.body}; }
.adsx .cl i { width:8px; height:8px; border-radius:2px; }
.adsx .crow b { font-family:Poppins,sans-serif; font-size:var(--a-fig); font-weight:700; text-align:right;
  font-variant-numeric:tabular-nums; }
.adsx .crow em { font-style:normal; font-size:var(--a-small); color:${C.muted}; }
.adsx .netstrip { display:flex; justify-content:space-between; align-items:baseline; gap:12px; flex-wrap:wrap;
  border-top:1px solid ${C.hair}; margin-top:15px; padding-top:13px; font-size:var(--a-body);
  color:${C.body}; }
.adsx .netstrip b { font-family:Poppins,sans-serif; font-size:var(--a-fig); color:${C.ink};
  font-variant-numeric:tabular-nums; }

.adsx .rec { display:flex; flex-direction:column; }
.adsx .rec > div { display:flex; justify-content:space-between; align-items:baseline; gap:12px;
  padding:9px 0; font-size:var(--a-body); color:${C.body}; }
.adsx .rec b { font-family:Poppins,sans-serif; font-size:var(--a-fig); font-weight:700; color:${C.ink};
  font-variant-numeric:tabular-nums; }
.adsx .rec .tot { border-top:1px solid ${C.hair}; margin-top:4px; padding-top:11px; }
.adsx .truth { display:flex; flex-direction:column; gap:6px; background:${C.parchment}; border-radius:10px;
  padding:13px 15px; margin-top:13px; }
.adsx .tlab { font-family:Poppins,sans-serif; font-size:var(--a-cap); font-weight:700; letter-spacing:.09em;
  text-transform:uppercase; color:${C.slate}; margin-bottom:2px; }
.adsx .tline { font-size:var(--a-small); color:${C.body}; line-height:1.55; }

/* ── Shared marks ────────────────────────────────────────────────────── */
.adsx .flag { display:inline-flex; align-items:center; gap:6px; background:${C.flagBg}; border-radius:6px;
  padding:2px 8px; font-size:var(--a-cap); font-weight:600; color:${C.flagText}; }
.adsx .drill { display:inline-flex; align-items:center; gap:4px; color:${C.teal}; font-weight:600;
  cursor:pointer; font-size:var(--a-small); }
.adsx .drill:hover { text-decoration:underline; }
.adsx a { color:${C.teal}; text-decoration:none; }
.adsx a:hover { color:${C.ink}; }
.adsx button:focus-visible, .adsx [role=button]:focus-visible, .adsx a:focus-visible {
  outline:2px solid ${C.teal}; outline-offset:2px; border-radius:4px; }
@media (prefers-reduced-motion:reduce){.adsx *{ transition:none !important; } }
`;
