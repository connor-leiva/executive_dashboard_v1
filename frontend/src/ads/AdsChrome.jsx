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
import { HeroMark } from "../Brand.jsx";

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

/* WHERE THE CASH FIGURE CAME FROM, said out loud. "upfront" is the launch price sheet's
   due-at-signing, modelled; "payments" is succeeded charges matched by email, measured. They are
   different kinds of fact and the difference changes what you do about a low number - a
   collections problem and a matching problem look identical as a dollar amount. */
export function cashNote(rev) {
  if (!rev) return "cash received to date";
  const n = rev.cash_people || 0;
  const who = `${n} ${n === 1 ? "person" : "people"} who have paid`;
  if (rev.collected_source === "upfront") {
    return `due at signing across ${who}, from the launch price sheet`;
  }
  if (rev.collected_source === "payments") {
    return `matched in the payment records for ${who}, joined by ${rev.collected_join}`;
  }
  return "no cash matched yet — no price sheet and no payment rows";
}


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

/* The hero: spend, and what it bought - as TWO totals, because one is always a lie.
 *
 * A financed enrollment CONTRACTS the full plan price and puts a deposit down. Headline the
 * contract alone and the tab claims cash the business does not have; headline the cash alone and
 * a $14,000 member looks like a $5,000 one. So both stand at the top, contracted as the headline
 * and cash received as the check on it, with the rule between them saying they are two readings
 * and not two parts of a sum. They must never be added.
 *
 * The cash figure spans EVERYONE WHO HAS PAID - the Cash received rung as well as the Enrolled
 * one - which is the same population the Launch tab's cash line uses, and for the reason stated
 * there: committed IS paid by definition. `collected_source` says whether it came from the price
 * sheet or from matched payment records, because those are different kinds of fact.
 *
 * THREE REVENUE LENSES BELOW, NEVER ONE. Projected is the third reading and it is SUPPRESSED
 * until a cohort curve has been fitted. The row still renders, saying so - deleting it would hide
 * that a third lens exists, and filling it with an estimate would be worse, because a guessed
 * projection looks exactly like a measured one.
 */
export function Hero({ data, adCount }) {
  const t = data.totals || {};
  const rev = data.revenue || null;
  const cac = data.cac || null;
  const mat = data.maturity || null;
  const fitted = !!(mat && mat.fitted);

  // Which lenses the headline is showing. Derived, not hard-coded: the "shown left" chip is a
  // claim about this component's own layout, and a literal would go stale the moment it moved.
  const shown = new Set(["Collected", "Contracted"]);
  const lenses = [
    { l: "Collected", v: rev?.collected, r: rev?.roas_collected,
      note: cashNote(rev) },
    { l: "Contracted", v: rev?.contracted, r: rev?.roas_contracted,
      note: "signed contract value, priced off the launch" },
    { l: "Projected", v: fitted ? rev?.projected : null, r: fitted ? rev?.roas_projected : null,
      note: fitted
        ? `+${mat.expected_additional} expected from the cohort still in flight`
        : "no cohort curve fitted yet — nothing to project from" },
  ].map((x) => ({ ...x, on: shown.has(x.l) }));

  return (
    <div className="hero">
      {/* Was an <img> at one customer's LOGOMARK — the bare circle, not the wordmark —
          hardcoded, at 30%, tinted by a CSS invert. The shared watermark instead: this
          workspace's own wordmark, at the treatment every other hero uses. */}
      <HeroMark top={-6} right={0} />
      <div className="hgrid">
        <div className="hleft">
          <div className="eyebrow">From {usd(t.spend)} of spend</div>
          <div className="hduo">
            <div className="hfig">
              <span className="hflab">Contracted · enrolled</span>
              <div className="heronum">
                <Fig v={rev ? usd(rev.contracted) : "—"} />
              </div>
              <div className="hroas">
                <b><Fig v={rev ? mult(rev.roas_contracted) : "—"} /></b>
                <em>return on ad spend</em>
              </div>
            </div>
            <i className="hsplit" />
            <div className="hfig alt">
              <span className="hflab">Cash received</span>
              <div className="heronum">
                <Fig v={rev ? usd(rev.collected) : "—"} />
              </div>
              <span className="hfsub">{cashNote(rev)}</span>
            </div>
          </div>
          <div className="hline">
            {cac
              ? `${cac.attributed_closes} enrollment${cac.attributed_closes === 1 ? "" : "s"} traced to Meta at ${usd(cac.attributed)} each`
              : "No funnel for this entity"}
            {" · "}{(data.campaigns || []).length} campaigns, {adCount} ads
            {rev?.unpriced_closes > 0 && (
              <> {" · "}<b style={{ color: C.flagText }}>
                {rev.unpriced_closes} enrollment{rev.unpriced_closes === 1 ? " has" : "s have"} no
                price — set their Payment Type to bring them into the contracted total
              </b></>
            )}
          </div>
        </div>
        <div className="lenses">
          <div className="lhead">The same spend, {["one", "two", "three", "four"][lenses.length - 1] || lenses.length} ways</div>
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
