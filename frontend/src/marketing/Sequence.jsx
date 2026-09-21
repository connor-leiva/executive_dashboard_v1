import React, { useEffect, useRef } from "react";
import { CORE, NEUTRAL, TYPE } from "../brand/axcion.jsx";
import { SITE, HERO } from "./content.js";

/* The scroll-scrubbed hero.
 *
 * Choreography borrowed from ClickUp's Super Agents section — object alone, object finds
 * its place, scene assembles, annotations draw in — with our own object. Theirs is a mask,
 * because a mask is their metaphor made physical. Ours is a figure landing in the dashboard
 * and revealing its sources, because provenance is the thing nobody else in this category
 * sells and it is the hardest thing in this codebase.
 *
 * Mechanics worth knowing before editing:
 *
 * - ONE progress value. Scroll position through the tall track, clamped 0–1. Every layer
 *   reads from it, so beats can be retimed by changing two numbers rather than rewiring.
 *   No scroll-jacking: the page moves at its own speed and a reader can leave at any point.
 *
 * - The figure travels by FLIP. It is a real <span> inside its real tile; on mount we measure
 *   where it rests and interpolate a transform back out to the centre of the stage. Nothing
 *   is cloned or absolutely positioned, so it lands pixel-exact at every viewport size.
 *
 * - Chrome lives on ::before layers. Fading .panel directly would fade its children — including
 *   the figure, which has to be visible and alone in the first frame. The card therefore
 *   materialises AROUND the number via --panel-a / --kpi-a / --rows-a. For the same reason no
 *   descendant may carry an opaque background: an early version had one on the panel body and
 *   it cut a hard horizontal seam across the bloom.
 *
 * - Transform and opacity only, one rAF per scroll event.
 *
 * - prefers-reduced-motion collapses the whole track to the assembled end state. Nobody is
 *   shown a half-built scene.
 */

const clamp = (v, a, b) => (v < a ? a : v > b ? b : v);
const seg = (p, a, b) => clamp((p - a) / (b - a), 0, 1);
const easeOut = (t) => 1 - Math.pow(1 - t, 3);
const easeInOut = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

const ANNOTATIONS = [
  { key: "sisu", text: "Pulled from Sisu", side: "left", style: { left: -186, top: "24%" }, w: 180, h: 52, d: "M4 14 H120 L170 44" },
  { key: "count", text: "38 source transactions", side: "right", style: { right: -206, top: "44%" }, w: 200, h: 46, d: "M196 14 H80 L18 40" },
  { key: "qbo", text: "Ties to QuickBooks", side: "left", style: { left: -176, bottom: "16%" }, w: 170, h: 44, d: "M4 30 H110 L162 6" },
];

export default function Sequence() {
  const track = useRef(null), stage = useRef(null), copy = useRef(null);
  const scene = useRef(null), fly = useRef(null), bloom = useRef(null), hint = useRef(null);
  const panel = useRef(null), heroKpi = useRef(null), pTop = useRef(null), rowsBox = useRef(null);

  useEffect(() => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    if (mq.matches) return undefined;

    const els = {
      track: track.current, stage: stage.current, copy: copy.current, scene: scene.current,
      fly: fly.current, bloom: bloom.current, hint: hint.current, panel: panel.current,
      heroKpi: heroKpi.current, pTop: pTop.current, rowsBox: rowsBox.current,
    };
    if (Object.values(els).some((e) => !e)) return undefined;

    const kpis = Array.from(els.scene.querySelectorAll("[data-stagger]"));
    const rows = Array.from(els.scene.querySelectorAll("[data-row]"));
    const annos = ANNOTATIONS.map((a) => els.scene.querySelector(`[data-anno="${a.key}"]`)).filter(Boolean);
    const tileParts = Array.from(els.scene.querySelectorAll("[data-tilepart]"));

    const flip = { dx: 0, dy: 0, scale: 3 };
    function measure() {
      els.fly.style.transform = "none";
      const f = els.fly.getBoundingClientRect();
      const s = els.stage.getBoundingClientRect();
      if (!f.width) return;
      flip.scale = Math.max(1.6, Math.min(s.width * 0.5, 460) / f.width);
      flip.dx = s.left + s.width / 2 - (f.left + f.width / 2);
      /* 0.70, not centre: the copy block owns the top of the frame, the way ClickUp stages
         theirs. Landing the object dead-centre collides with it on a short viewport. */
      flip.dy = s.top + s.height * 0.7 - (f.top + f.height / 2);
    }

    let prog = 0, ticking = false;
    function compute() {
      const r = els.track.getBoundingClientRect();
      const total = r.height - window.innerHeight;
      prog = total <= 0 ? 0 : clamp(-r.top / total, 0, 1);
    }

    function draw() {
      ticking = false;
      const p = prog;

      const pCopy = seg(p, 0.03, 0.24);
      els.copy.style.opacity = String(1 - pCopy);
      els.copy.style.transform = `translateY(${-70 * easeOut(pCopy)}px)`;

      const t = easeInOut(seg(p, 0.06, 0.5)), inv = 1 - t;
      els.fly.style.transform =
        `translate(${flip.dx * inv}px, ${flip.dy * inv}px) scale(${1 + (flip.scale - 1) * inv})`;

      const pB = seg(p, 0.02, 0.34), pBout = seg(p, 0.46, 0.78);
      els.bloom.style.opacity = String(easeOut(pB) * (1 - 0.82 * easeOut(pBout)));
      els.bloom.style.transform =
        `translate(-50%,-50%) translateY(${12 - 30 * easeOut(pB)}vh) scale(${0.82 + 0.28 * easeOut(pB)})`;

      const pScene = seg(p, 0.46, 0.68);
      els.panel.style.setProperty("--panel-a", String(easeOut(pScene)));
      els.heroKpi.style.setProperty("--kpi-a", String(easeOut(seg(p, 0.5, 0.7))));
      els.scene.style.transform = `scale(${0.965 + 0.035 * easeOut(pScene)})`;
      els.pTop.style.opacity = String(seg(p, 0.54, 0.72));
      els.rowsBox.style.setProperty("--rows-a", String(seg(p, 0.6, 0.78)));
      tileParts.forEach((el) => { el.style.opacity = String(seg(p, 0.56, 0.74)); });

      kpis.forEach((el, i) => {
        const q = seg(p, 0.56 + i * 0.04, 0.72 + i * 0.04);
        el.style.opacity = String(q);
        el.style.transform = `translateY(${16 * (1 - easeOut(q))}px)`;
      });
      rows.forEach((el, i) => {
        const q = seg(p, 0.6 + i * 0.03, 0.76 + i * 0.03);
        el.style.opacity = String(q);
        el.style.transform = `translateY(${12 * (1 - easeOut(q))}px)`;
      });

      annos.forEach((el, i) => {
        const q = seg(p, 0.74 + i * 0.045, 0.87 + i * 0.045);
        el.style.opacity = String(q);
        const path = el.querySelector("path");
        if (path) {
          const L = path.__len || (path.__len = path.getTotalLength());
          path.style.strokeDasharray = String(L);
          path.style.strokeDashoffset = String(L * (1 - easeOut(q)));
        }
        const lbl = el.querySelector("[data-lbl]");
        if (lbl) lbl.style.opacity = String(seg(p, 0.8 + i * 0.045, 0.9 + i * 0.045));
      });

      els.hint.style.opacity = String(1 - seg(p, 0, 0.08));
    }

    function onScroll() {
      compute();
      if (!ticking) { ticking = true; requestAnimationFrame(draw); }
    }
    function onResize() { measure(); onScroll(); }

    measure(); compute(); draw();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onResize);
    /* Re-measure once the webfonts land — Archivo is wider than the fallback, so measuring
       before it loads puts the landing a few pixels off. */
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(onResize);

    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onResize);
    };
  }, []);

  const cellLabel = {
    fontFamily: TYPE.data, fontWeight: 600, fontSize: 9.5, letterSpacing: "0.13em",
    textTransform: "uppercase", color: NEUTRAL[500],
  };
  const kpiVal = {
    fontFamily: TYPE.data, fontWeight: 600, fontVariantNumeric: "tabular-nums",
    fontSize: 30, lineHeight: 1.08, color: CORE.ink, marginTop: 8,
  };

  return (
    <div ref={track} className="acu-track">
      <div ref={stage} className="acu-stage">
        <div ref={bloom} className="acu-bloom" />
        <div className="acu-grain" aria-hidden>
          <svg width="100%" height="100%"><rect width="100%" height="100%" filter="url(#acu-grain-f)" opacity=".4" /></svg>
        </div>

        <div ref={copy} className="acu-seq-copy">
          <p className="acu-eyebrow">{HERO.eyebrow}</p>
          <h1 className="acu-display">
            Track production, <span style={{ color: NEUTRAL[500] }}>not spreadsheets</span>
          </h1>
          <p className="acu-seq-sub">
            Every number Axcion shows you came from somewhere. Scroll, and watch one land.
          </p>
          <div className="acu-seq-cta">
            <a className="acu-btn-ink" href={`mailto:${SITE.contactEmail}?subject=Axcion%20demo`}>Book a demo</a>
            <p className="acu-seq-support">{HERO.note}</p>
          </div>
        </div>

        <div ref={scene} className="acu-scene">
          <div ref={panel} className="acu-panel">
            <div ref={pTop} className="acu-p-top">
              {/* No mark in the illustrated app bar. The nav lockup is this page's one mark
                  (identity guide §05), and a second, smaller one here is the repetition the
                  guide forbids. */}
              <span className="acu-p-tabs">
                <b>Production</b><span>Books</span><span>Campaigns</span><span>Binder</span>
              </span>
              <span className="acu-p-cta">New campaign</span>
            </div>

            <div className="acu-p-body">
              <div className="acu-kpis">
                <div className="acu-kpi" data-stagger="1">
                  <div style={cellLabel}>Units closed</div>
                  <div style={kpiVal}>38</div>
                  <div className="acu-kpi-d">of 45 target</div>
                </div>

                <div ref={heroKpi} className="acu-kpi acu-hero-kpi">
                  <div style={cellLabel} data-tilepart>GCI, month to date</div>
                  <div style={{ ...kpiVal, textAlign: "center" }}>
                    <span ref={fly} className="acu-fly">$412,900</span>
                  </div>
                  <div className="acu-kpi-d" data-tilepart>+14.2% vs. last month</div>
                </div>

                <div className="acu-kpi" data-stagger="2">
                  <div style={cellLabel}>Pipeline</div>
                  <div style={kpiVal}>$1.9M</div>
                  <div className="acu-kpi-d">62 active</div>
                </div>
              </div>

              <div ref={rowsBox} className="acu-rows">
                <div className="acu-row" data-row="0">
                  <span style={cellLabel}>Transaction</span><span style={cellLabel}>Agent</span>
                  <span style={{ ...cellLabel, textAlign: "right" }}>Volume</span>
                  <span style={{ ...cellLabel, textAlign: "right" }}>Source</span>
                </div>
                {[
                  ["TX-2026-0881", "Dana Whitfield", "$4,120,000", "Sisu"],
                  ["TX-2026-0874", "Marcus Oyelaran", "$3,480,500", "Sisu"],
                  ["TX-2026-0869", "Priya Raghavan", "$2,905,000", "Follow Up Boss"],
                ].map((r, i) => (
                  <div className="acu-row" data-row={i + 1} key={r[0]}>
                    <span>{r[0]}</span><span>{r[1]}</span>
                    <span className="acu-row-n">{r[2]}</span><span className="acu-row-s">{r[3]}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>

          {ANNOTATIONS.map((a) => (
            <div key={a.key} className="acu-anno" data-anno={a.key}
              style={{ ...a.style, textAlign: a.side === "right" ? "right" : "left" }}>
              <div className="acu-anno-lbl" data-lbl>{a.text}</div>
              <svg width={a.w} height={a.h} aria-hidden><path d={a.d} /></svg>
            </div>
          ))}
        </div>

        <div ref={hint} className="acu-scroll-hint">Scroll</div>
      </div>
    </div>
  );
}
