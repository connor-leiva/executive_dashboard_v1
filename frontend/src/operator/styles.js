/* What inline styles cannot express: hover, focus and the responsive grid. One stylesheet for the
   whole console, every rule under an `ac-` class so nothing leaks into or out of it. */
import { A, WHITE } from "./tokens.js";

export const css = `
  .ac-console *, .ac-console *::before, .ac-console *::after { box-sizing: border-box; }
  .ac-btn:active:not(:disabled) { transform: translateY(.5px); }
  .ac-b-ghost:hover:not(:disabled) { background: ${A.hover}; border-color: ${A.lineMid}; }
  .ac-b-solid:hover:not(:disabled) { background: ${A.body}; border-color: ${A.body}; }
  .ac-b-primary:hover:not(:disabled) { background: ${A.cadetDeep}; border-color: ${A.cadetDeep}; }
  .ac-b-quiet:hover:not(:disabled) { background: ${A.chip}; }
  .ac-b-danger:hover:not(:disabled) { background: ${A.stopBg}; border-color: ${A.stop}; }
  .ac-b-dangerSolid:hover:not(:disabled) { filter: brightness(.92); }
  .ac-b-onGhost:hover:not(:disabled) { background: rgba(239,245,243,.12); border-color: rgba(239,245,243,.55); }
  .ac-b-onDanger:hover:not(:disabled) { background: rgba(240,207,203,.14); border-color: rgba(240,207,203,.7); }
  .ac-b-icon:hover:not(:disabled) { background: ${A.chip}; color: ${A.ink}; }
  .ac-b-onDark:hover:not(:disabled) { background: ${WHITE}; }
  .ac-seg:hover:not(.on) { background: ${A.hover}; color: ${A.ink}; border-color: ${A.lineMid}; }
  .ac-tag:hover { background: ${A.lineMid}; color: ${A.ink}; }
  .ac-tab:hover { color: ${A.ink}; }
  .ac-link:hover { color: ${A.ink}; text-decoration: underline; text-underline-offset: 2px; }
  .ac-wsrow:hover { background: ${A.hover}; }
  .ac-wsrow:hover .ac-chev { transform: translateX(2px); color: ${A.body}; }
  .ac-triagerow:hover { background: ${A.hover}; }
  .ac-row:hover td { background: ${A.hover}; }
  .ac-row2:hover { background: ${A.hover}; }
  .ac-console input:hover:not(:focus), .ac-console select:hover:not(:focus),
  .ac-console textarea:hover:not(:focus) { border-color: ${A.lineMid}; }
  .ac-console input:focus, .ac-console select:focus, .ac-console textarea:focus { border-color: ${A.cadet}; }
  .ac-console input::placeholder { color: ${A.faint}; }
  .ac-wsbtn:focus-visible, .ac-btn:focus-visible, .ac-link:focus-visible, .ac-tag:focus-visible,
  .ac-tab:focus-visible, .ac-console input:focus-visible, .ac-console select:focus-visible,
  .ac-console textarea:focus-visible, .ac-console a:focus-visible {
    outline: 2px solid ${A.cadet}; outline-offset: 2px; border-radius: 4px;
  }
  .ac-tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; }
  .ac-form2 { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px 16px; }
  @media (max-width: 640px) { .ac-form2 { grid-template-columns: minmax(0, 1fr); } }
  .ac-t6 { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  @media (min-width: 1160px) { .ac-t6 { grid-template-columns: repeat(6, minmax(0, 1fr)); } }
  @media (max-width: 560px) { .ac-t6 { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
  .ac-t4 { grid-template-columns: repeat(4, minmax(0, 1fr)); }
  @media (max-width: 900px) { .ac-t4 { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
  .ac-split { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; align-items: start; }
  .ac-shell { display: grid; grid-template-columns: 176px minmax(0, 1fr); gap: 24px;
              max-width: 1240px; margin: 0 auto; padding: 22px 24px 64px; }
  .ac-rail { position: sticky; top: 74px; display: flex; flex-direction: column; gap: 1px; align-self: start; }
  .ac-nav:hover:not(.on) { background: rgba(255,255,255,.6); color: ${A.ink}; }
  .ac-tabs { scrollbar-width: none; }
  .ac-tabs::-webkit-scrollbar { height: 0; }
  .ac-bulk { animation: ac-rise .16s ease-out; }
  @keyframes ac-rise { from { opacity: 0; transform: translateY(-4px); } to { opacity: 1; transform: none; } }
  @media (max-width: 900px) {
    .ac-split { grid-template-columns: 1fr; }
    .ac-shell { grid-template-columns: minmax(0, 1fr); gap: 14px; padding: 14px 14px 48px; }
    .ac-rail { position: static; flex-direction: row; overflow-x: auto; gap: 4px; padding-bottom: 2px; }
    .ac-wsbtn { flex-wrap: wrap; }
    .ac-wscols { gap: 14px; padding-top: 4px; }
    .ac-chev { display: none !important; }
    .ac-triage { flex-direction: column; }
    .ac-hidesm { display: none !important; }
  }
  @media (max-width: 520px) {
    .ac-provrow > div:nth-child(4) { display: none; }
    .ac-headname { display: none; }
  }
  @media (prefers-reduced-motion: reduce) {
    .ac-console *, .ac-console *::before { transition: none !important; animation: none !important; }
  }
`;
