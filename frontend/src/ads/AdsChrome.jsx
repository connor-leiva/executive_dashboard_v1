/* The mockup's primitives and page chrome, ported. SPEC-ads-module.md Part 13.
 *
 * These are the pieces the whole tab is assembled from - the icon tinting, the figure treatment,
 * the section header, the hero. They live apart from AdsView so the view reads as a layout and
 * not as a layout plus a design system.
 *
 * The spec's rule for this file: the mockup wins on LAYOUT, this document wins on data and
 * behaviour. So the classes, the type scale and the structure are the mockup's; every number
 * bound into them comes from the API, and anything the API cannot yet supply says so rather than
 * rendering a placeholder that looks like a measurement.
 */
import { ASSET, C, mult, usd } from "./adsTokens.js";

/* The brand icons are opaque black-on-white PNGs with no alpha channel, so masking them paints a
   filled square in every browser. Alpha is derived from luminance by one feColorMatrix per tint.
   The tints are a FIXED set: a colour outside it has no filter to reference and would silently
   render the raw PNG, so Icon falls back rather than failing open. */
export const ICON_TINTS = [C.teal, C.muted, C.slate, C.ink, C.flagText, C.onDark, C.sprout];
const tintId = (hex) => "tint" + hex.replace("#", "");
const rgb01 = (hex) => [1, 3, 5].map((i) => (parseInt(hex.slice(i, i + 2), 16) / 255).toFixed(4));

export function IconFilters() {
  return (
    <svg aria-hidden focusable="false" width="0" height="0"
         style={{ position: "absolute", width: 0, height: 0, overflow: "hidden" }}>
      <defs>
        {ICON_TINTS.map((hex) => {
          const [r, g, b] = rgb01(hex);
          return (
            <filter key={hex} id={tintId(hex)} colorInterpolationFilters="sRGB">
              {/* Luminance -> alpha, then flood the tint through it. */}
              <feColorMatrix type="matrix" values={`0 0 0 0 0
                0 0 0 0 0
                0 0 0 0 0
                -1 -1 -1 0 1`} />
              <feFlood floodColor={hex} result="f" />
              <feComposite in="f" in2="SourceGraphic" operator="in" />
              <feComponentTransfer>
                <feFuncR type="linear" slope="0" intercept={r} />
                <feFuncG type="linear" slope="0" intercept={g} />
                <feFuncB type="linear" slope="0" intercept={b} />
              </feComponentTransfer>
            </filter>
          );
        })}
      </defs>
    </svg>
  );
}

export function Icon({ src, size = 15, color = C.slate, style }) {
  const hex = ICON_TINTS.includes(color) ? color : C.slate;
  return <img src={src} alt="" aria-hidden
              style={{ width: size, height: size, display: "block", flexShrink: 0,
                       filter: `url(#${tintId(hex)})`, ...style }} />;
}

/* Headline figures set the unit smaller than the digits, so a column of them reads as quantities
   first and currency second. Purely presentational: the string is not touched. */
export function Fig({ v }) {
  const s = String(v);
  const pre = s.charAt(0) === "$" ? "$" : "";
  const suf = /[x%]$/.test(s) ? s.slice(-1) : "";
  const core = s.slice(pre.length, s.length - suf.length);
  return <>{pre ? <i className="unit">{pre}</i> : null}{core}{suf ? <i className="unit">{suf}</i> : null}</>;
}

export const Source = ({ name }) => <span className="src">{name}</span>;

export const Kicker = ({ children, extra }) => (
  <div className="kick"><span className="klead"><i />{children}</span>{extra}</div>
);

export function Section({ id, n, title, lede, children }) {
  return (
    <section id={id} className="sect">
      <div className="shead">
        <span className="sn">{n}</span>
        <h2 className="st">{title}</h2>
        <i className="srule" />
        <span className="slede">{lede}</span>
      </div>
      <div className="sbody">{children}</div>
    </section>
  );
}

/* The hero: spend, and what it contracted.
 *
 * THREE REVENUE LENSES, NEVER ONE. A financed enrollment contracts $14,000 and collects $1,167
 * in month one; reporting either alone is a lie in one direction or the other. They sit in one
 * instrument with three readings rather than three floating cards, and the lens the headline
 * figure comes from says which it is.
 *
 * Projected is the third reading and it is SUPPRESSED until a cohort curve has been fitted. The
 * row still renders, saying so - deleting it would hide that a third lens exists, and filling it
 * with an estimate would be worse, because a guessed projection looks exactly like a measured one.
 */
export function Hero({ data, adCount }) {
  const t = data.totals || {};
  const rev = data.revenue || null;
  const cac = data.cac || null;
  const mat = data.maturity || null;
  const fitted = !!(mat && mat.fitted);

  const lenses = [
    { l: "Collected", v: rev?.collected, r: rev?.roas_collected, note: "cash received to date" },
    { l: "Contracted", v: rev?.contracted, r: rev?.roas_contracted, note: "signed contract value",
      on: true },
    { l: "Projected", v: fitted ? rev?.projected : null, r: fitted ? rev?.roas_projected : null,
      note: fitted
        ? `+${mat.expected_additional} expected from the cohort still in flight`
        : "no cohort curve fitted yet — nothing to project from" },
  ];

  return (
    <div className="hero">
      <img className="sigmark" src={ASSET.sig} alt="" aria-hidden />
      <div className="hgrid">
        <div className="hleft">
          <div className="eyebrow">Contracted from {usd(t.spend)} of spend</div>
          <div className="heronum">
            <Fig v={rev ? usd(rev.contracted) : "—"} />
          </div>
          <div className="hroas">
            <b><Fig v={rev ? mult(rev.roas_contracted) : "—"} /></b>
            <em>return on ad spend</em>
          </div>
          <div className="hline">
            {cac
              ? `${cac.attributed_closes} enrollment${cac.attributed_closes === 1 ? "" : "s"} traced to Meta at ${usd(cac.attributed)} each`
              : "No funnel for this entity"}
            {" · "}{(data.campaigns || []).length} campaigns, {adCount} ads
          </div>
        </div>
        <div className="lenses">
          <div className="lhead">The same spend, three ways</div>
          {lenses.map((x) => (
            <div key={x.l} className={`lens${x.on ? " on" : ""}`}>
              <span className="llab">{x.l}{x.on ? <em>shown left</em> : null}</span>
              <span className="lnum">
                {x.v === null || x.v === undefined ? "—" : usd(x.v)}
                <i>{x.r === null || x.r === undefined ? "—" : mult(x.r)}</i>
              </span>
              <span className="lsub">{x.note}</span>
            </div>
          ))}
        </div>
      </div>
      <div className="herofoot">
        <span className="hfoot-l">
          <span className="hdot" />
          <span>
            {data.basis === "period"
              ? "Period basis: revenue recognised in this window, whoever it came from. Right for accounting, and the wrong lens for judging an ad."
              : "Cohort basis: spend in this window against revenue from the people it acquired, whenever that lands."}
            {" "}
            {fitted
              ? `The window is ${Math.round((mat.pct || 0) * 100)}% mature against a ${mat.median_lag_days}-day median close, so contracted is a floor, not a final number.`
              : "No cohort curve has been fitted yet, so contracted is a floor with no measured ceiling."}
          </span>
        </span>
        <span className="hfoot-r">First touch · frozen</span>
      </div>
    </div>
  );
}
