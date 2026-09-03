/* The Ads tab. SPEC-ads-module.md Part 13.
 *
 * Five numbered sections rather than eleven stacked panels, because eleven was too long to scan
 * and the sections give the eye somewhere to rest without deleting anything. The section ledes
 * are part of the design, not filler: "why the number above it is a floor" is doing the same
 * work as the honesty copy inside the panels.
 *
 * PHASE 1 renders the click layer. The chain to closed revenue is Phase 3, and where it is
 * missing this view SAYS so - the whole thesis of the module is that clicks and customers rank
 * campaigns differently, so a click-only tab that implied otherwise would be worse than none.
 *
 * No chart library. Bars are hand-rolled SVG and horizontal, because campaign names here are
 * long and structured and vertical labels destroy the distinguishing part.
 */
import { useEffect, useRef, useState } from "react";

import { ProductIcon, iconFor } from "../brand/productIcons.jsx";
import { BAND, C, FIG, FONT, HEAD, band, compact, mult, num, pct, usd } from "./adsTokens.js";
import CreativeWall from "./CreativeWall.jsx";
import DrillPanel from "./DrillPanel.jsx";
import GroupingRules from "./GroupingRules.jsx";
import Funnel from "./Funnel.jsx";
import { API_BASE, getJSON, postJSON } from "../api";
import { cashNote, Fig as ChromeFig, Hero, Kicker,
         Section as ChromeSection,
         Source } from "./AdsChrome.jsx";
import { adsCss } from "./adsStyles.js";
import { sampleAdsDrill } from "./sampleAds.js";
import { qs, useAdsAccounts, useAdsCreatives, useAdsOverview } from "./useAds.js";

/* The page has a spine: numbered sections and a sticky jump rail. Nothing was deleted to
   shorten it; it was given joints. */
const SECTIONS = [
  { id: "chain", n: "01", title: "The chain" },
  { id: "findings", n: "02", title: "Findings" },
  { id: "sources", n: "03", title: "Where it came from" },
  { id: "cost", n: "04", title: "What a customer costs" },
  { id: "creative", n: "05", title: "The ads themselves" },
  { id: "trust", n: "06", title: "What to trust" },
];

const PERIODS = [
  { k: "7d", label: "7d" }, { k: "30d", label: "30d" }, { k: "60d", label: "60d" },
  { k: "90d", label: "90d" }, { k: "mtd", label: "MTD" },
];

/* ── small pieces ─────────────────────────────────────────────────────────── */

/* Splits the leading currency glyph and the trailing percent/multiplier off the numeral, so a
   column of mixed $47,382 / 9.4x / 1.02% still reads as one hierarchy. */
function Fig({ value, unit, lead, tone }) {
  return (
    <span style={{ fontFamily: FIG, fontVariantNumeric: "tabular-nums",
                   color: tone || C.ink, whiteSpace: "nowrap" }}>
      {lead && <span style={{ color: C.muted, fontWeight: 500 }}>{lead}</span>}
      {value}
      {unit && <span style={{ color: C.muted, fontWeight: 500, fontSize: "0.82em" }}>{unit}</span>}
    </span>
  );
}

/* The section header is the mockup's, so the numbered spine and the jump rail agree. `id` is
   new: the rail scrolls to it. */
function Section({ n, id, title, lede, children }) {
  return <ChromeSection id={id} n={n} title={title} lede={lede}>{children}</ChromeSection>;
}


function Card({ children, pad }) {
  // `.card` carries the mockup's surface, border, radius and padding. `pad` survives for the two
  // callers that deliberately tighten it; everything else takes the design system's spacing.
  return <div className="card" style={pad ? { padding: pad } : undefined}>{children}</div>;
}

function Stat({ label, children, tone, note }) {
  return (
    <div style={{ background: C.surface, border: `1px solid ${C.line}`, borderRadius: 12,
                  padding: "13px 15px" }}>
      <div style={{ fontFamily: FONT, fontSize: 10.5, fontWeight: 600, letterSpacing: ".07em",
                    textTransform: "uppercase", color: C.muted }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 600, marginTop: 5, color: tone || C.ink }}>
        {children}
      </div>
      {note && <div style={{ fontFamily: FONT, fontSize: 11, color: C.muted, marginTop: 3 }}>{note}</div>}
    </div>
  );
}

/* Horizontal, hand-rolled. Width transition only, and only when motion is welcome. */
function Bars({ rows, valueKey, format, max }) {
  const top = max || Math.max(...rows.map((r) => r[valueKey] || 0), 1);
  return (
    <div style={{ display: "grid", gap: 9 }}>
      {rows.map((r) => (
        <div key={r.name} style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) 92px",
                                   alignItems: "center", gap: 12 }}>
          <div style={{ minWidth: 0 }}>
            <div style={{ fontFamily: FONT, fontSize: 12.5, color: C.ink, overflow: "hidden",
                          textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.name}</div>
            <div className="ads-track" style={{ background: C.surface2, borderRadius: 4,
                                                height: 8, marginTop: 5, overflow: "hidden" }}>
              <div className="ads-bar" style={{ width: `${((r[valueKey] || 0) / top) * 100}%`,
                                                background: C.accent, height: "100%" }} />
            </div>
          </div>
          <div style={{ textAlign: "right", fontSize: 13 }}>{format(r[valueKey])}</div>
        </div>
      ))}
    </div>
  );
}

function Pill({ tone, children }) {
  const b = band(tone);
  return (
    <span style={{ fontFamily: FONT, fontSize: 11, fontWeight: 600, color: b.ink,
                   background: b.bg, border: `1px solid ${b.bar}`, borderRadius: 999,
                   padding: "2px 9px" }}>{children}</span>
  );
}

/* ── the view ─────────────────────────────────────────────────────────────── */
export default function AdsView() {
  const [period, setPeriod] = useState("30d");
  const [basis, setBasis] = useState("cohort");
  const [account, setAccount] = useState(null);
  /* One campaign per launch is how this account is run, so scoping to a campaign IS scoping to a
     launch. It narrows the WHOLE page - headline spend, funnel and creative wall together -
     because a funnel for one launch beside spend for all of them is a wrong CAC wearing a right
     one's clothes. Reset when the account changes: a campaign id from another account resolves
     to a 404 rather than to nothing. */
  const [campaign, setCampaign] = useState(null);

  /* The drill is fetched on demand rather than with the page: it returns names and emails for
     every rung, and pre-loading six lists nobody may open would be both slow and a wider spill
     of personal data than the click actually asked for. */
  const [drill, setDrill] = useState({ stage: null, label: null, data: null,
                                       loading: false, error: null });
  const [activeSection, setActiveSection] = useState(SECTIONS[0].id);
  const pageRef = useRef(null);
  const [recomputing, setRecomputing] = useState(false);
  const [recomputeMsg, setRecomputeMsg] = useState(null);

  const accounts = useAdsAccounts();
  const { data, error, loading, retry } = useAdsOverview({ account, period, basis, campaign });
  const creatives = useAdsCreatives({ account, period, campaign, sort: "spend", limit: 24 });

  /* Scrollspy for the jump rail. Sections are found through the ref rather than document,
     because the tree is committed before it is attached; whichever section has crossed the rail
     owns it. Capture phase so an ancestor scroller counts - this tab renders inside the app
     shell, which is the thing that actually scrolls. One rect read per event: observers are
     silently unavailable in some hosts, and a rail that never lights is worse than a cheap read. */
  useEffect(() => {
    const root = pageRef.current;
    if (!root) return undefined;
    const els = SECTIONS.map((x) => root.querySelector("#" + x.id)).filter(Boolean);
    if (!els.length) return undefined;
    const doc = root.ownerDocument;
    const view = doc.defaultView || window;
    const read = () => {
      let current = els[0].id;
      els.forEach((el) => { if (el.getBoundingClientRect().top <= 96) current = el.id; });
      setActiveSection(current);
    };
    read();
    doc.addEventListener("scroll", read, { passive: true, capture: true });
    view.addEventListener("resize", read, { passive: true });
    return () => {
      doc.removeEventListener("scroll", read, { capture: true });
      view.removeEventListener("resize", read);
    };
  }, [data]);

  const recompute = async () => {
    setRecomputing(true);
    setRecomputeMsg(null);
    try {
      const r = await postJSON("/ads/recompute", {});
      const n = r?.conversions?.written;
      setRecomputeMsg(typeof n === "number" ? `Done — ${n} stage rows rebuilt.` : "Done.");
      retry();                       // re-read the page against what was just derived
    } catch (e) {
      setRecomputeMsg(`Couldn't recompute: ${String(e?.message || e)}`);
    } finally {
      setRecomputing(false);
    }
  };

  /* Passes the SAME account, period, basis and campaign the page is showing. A drill that
     resolved its own window would open a different population than the figure that was clicked,
     which is the one way a drill can be worse than not having one. */
  const openDrill = async (stage, label) => {
    if (drill.stage === stage) {
      setDrill({ stage: null, label: null, data: null, loading: false, error: null });
      return;
    }
    setDrill({ stage, label, data: null, loading: true, error: null });
    if (!API_BASE) {
      setDrill({ stage, label, data: sampleAdsDrill(stage, label), loading: false, error: null });
      return;
    }
    try {
      const d = await getJSON(
        `/ads/drill/funnel.${stage}?${qs({ account, period, basis, campaign })}`);
      setDrill((cur) => (cur.stage === stage
        ? { ...cur, data: d, loading: false } : cur));   // a slower earlier click must not win
    } catch (e) {
      setDrill((cur) => (cur.stage === stage ? { ...cur, error: e, loading: false } : cur));
    }
  };

  if (error) {
    return (
      <Card>
        <div style={{ fontFamily: FONT, fontSize: 13, color: C.slate }}>
          Couldn&rsquo;t load ad performance.{" "}
          <button onClick={retry} style={{ background: "none", border: "none", color: C.accent,
                                           cursor: "pointer", font: "inherit", padding: 0 }}>
            Try again
          </button>
        </div>
      </Card>
    );
  }
  if (loading || !data) {
    return <Card><div style={{ fontFamily: FONT, fontSize: 13, color: C.muted }}>Loading…</div></Card>;
  }

  // Connect-first. A workspace that has never connected Meta is in a NORMAL state, not an error.
  if (!data.connected) {
    return (
      <Card pad={26}>
        <h2 style={{ fontFamily: HEAD, fontSize: 18, margin: 0, color: C.ink }}>
          Connect a Meta ad account
        </h2>
        <p style={{ fontFamily: FONT, fontSize: 13.5, color: C.slate, maxWidth: 560,
                    lineHeight: 1.6 }}>
          {data.reason}
        </p>
        <p style={{ fontFamily: FONT, fontSize: 12.5, color: C.muted, maxWidth: 560,
                    lineHeight: 1.6 }}>
          You&rsquo;ll need a System User token with <strong>ads_read</strong>. Don&rsquo;t grant
          ads_management — this module only reads.
        </p>
      </Card>
    );
  }

  const t = data.totals;
  const acctList = Array.isArray(accounts.data) ? accounts.data : [];

  return (
    <div className="adsx" ref={pageRef}>
      <style>{adsCss()}</style>
      <style>{`
        .adsx .ads-bar { transition: width .2s ease; }
        .adsx .ads-grid { display: grid; gap: 10px;
                          grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); }
        .adsx .ads-table-row { display: grid; gap: 10px; align-items: center;
                               grid-template-columns: minmax(0,2.2fr) repeat(5, minmax(0,1fr)); }
        @media (max-width: 720px) {
          /* Not a horizontally scrolling six-column table called responsive: the row becomes a
             stacked card, name on its own line. */
          .adsx .ads-table-row { grid-template-columns: repeat(2, minmax(0,1fr)); }
          .adsx .ads-table-row .ads-cell-name { grid-column: 1 / -1; }
          .adsx .ads-head { display: none; }
        }
        @media (prefers-reduced-motion: reduce) { .adsx .ads-bar { transition: none; } }
      `}</style>

      <div className="head" id="top">
        <div className="wrap">
          <div className="phead">
            {/* Was a masked bell PNG from the utility icon set. Ads is a platform module and
                carries the module mark, the same one the rail shows for this tab. */}
            <ProductIcon name={iconFor("ads")} size={22} tone={C.accent} />
            <span className="ptitle">Ads · Meta performance</span>
            <span className="psub">what the spend bought, all the way to a signed member</span>
            <span className="spacer" />
            <span className="srcs">
              <Source name={data.account.name} />
              <Source name={data.account.external_id} />
              <Source name="Meta" />
              <Source name="Go High Level" />
            </span>
          </div>

          <div className="ctrl">
            <span className="periods">
              {PERIODS.map((pp) => (
                <button key={pp.k} className={`per${period === pp.k ? " on" : ""}`}
                        onClick={() => setPeriod(pp.k)}>{pp.label}</button>
              ))}
            </span>
            <span className="basis">
              {[["cohort", "Cohort"], ["period", "Period"]].map(([kk, lab]) => (
                <button key={kk} className={`per${basis === kk ? " on" : ""}`}
                        onClick={() => setBasis(kk)}
                        title={kk === "cohort"
                          ? "Revenue from people acquired in this window, whenever it lands"
                          : "Revenue recognised in this window, whoever it came from"}>{lab}</button>
              ))}
            </span>
            <span className="spacer" />
            {acctList.length > 1 && (
              <select value={account || ""}
                      onChange={(e) => { setAccount(e.target.value || null); setCampaign(null); }}
                      className="per" style={{ padding: "6px 10px" }}>
                {acctList.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
              </select>
            )}
            {(data.campaigns_available || []).length > 1 && (
              <select value={campaign || ""} aria-label="Scope to one launch"
                      onChange={(e) => setCampaign(e.target.value || null)}
                      className={`per${campaign ? " on" : ""}`}
                      style={{ padding: "6px 10px", maxWidth: 260 }}>
                <option value="">All campaigns</option>
                {(data.campaigns_available || []).map((c) => (
                  <option key={c.id} value={c.id}>{c.name}</option>
                ))}
              </select>
            )}
          </div>

          <div className="ctrl">
            <span className="range">
              {data.range.label} · {data.range.start} to {data.range.end} · times in{" "}
              {data.account.timezone_name || "account time"}
            </span>
            {data.scope?.kind === "campaign" && (
              /* Named, not implied. Every figure below this line is one launch's, and a reader
                 who missed the dropdown would otherwise take them for the whole account's. */
              <>
                <span className="tag" style={{ color: C.accent, background: C.accentBg }}>
                  {data.scope.name}
                </span>
                <button onClick={() => setCampaign(null)}
                        style={{ background: "none", border: "none", padding: 0, cursor: "pointer",
                                 font: "inherit", fontSize: 11, color: C.slate,
                                 textDecoration: "underline" }}>
                  show the whole account
                </button>
              </>
            )}
          </div>
        </div>
      </div>

      {/* The one dark band on the page. The hero is the thesis - spend, and what it contracted -
          so it is the only thing that gets the evergreen ground. */}
      <div className="band">
        <div className="wrap">
          <Hero data={data} adCount={creatives.data?.total ?? 0} />
        </div>
      </div>

      <nav className="rail" aria-label="Jump to section">
        <div className="railin">
          {SECTIONS.map((sn) => (
            <a key={sn.id} href={`#${sn.id}`} className={`rlink${activeSection === sn.id ? " on" : ""}`}>
              <b>{sn.n}</b>{sn.title}
            </a>
          ))}
          <span className="railsum">
            <b>{data.revenue ? usd(data.revenue.contracted) : "—"}</b> contracted ·{" "}
            <b>{data.revenue ? usd(data.revenue.collected) : "—"}</b> cash on {usd(t.spend)}
          </span>
        </div>
      </nav>

      <div className="wrap">
      {/* 01 The chain */}
      <Section n="01" id="chain" title="The chain"
        lede="Meta owns impressions, clicks and its own lead count. Everything after that — the
              registration, the booked call, the signature, the cash — already lives in Acumyn.
              Joining the two is what this module is for.">
        {data.funnel ? (
          <>
            <Funnel rungs={data.funnel} spend={t.spend} onDrill={openDrill} />
            {drill.stage && (
              <DrillPanel drill={drill.data} loading={drill.loading} error={drill.error}
                          label={drill.label}
                          onClose={() => setDrill({ stage: null, label: null, data: null,
                                                    loading: false, error: null })} />
            )}
          </>
        ) : (
          <Card pad={20}>
            <div style={{ display: "flex", gap: 10, alignItems: "flex-start" }}>
              <div style={{ width: 3, alignSelf: "stretch", background: C.warnBar, borderRadius: 2 }} />
              <div>
                <div style={{ fontFamily: HEAD, fontSize: 14, color: C.ink }}>
                  No funnel for this entity
                </div>
                <p style={{ fontSize: 12.5, color: C.slate, margin: "6px 0 0", lineHeight: 1.6,
                            maxWidth: 660 }}>
                  A funnel is defined for programme and transactional businesses. This account
                  books its spend to {data.archetype ? `a ${data.archetype} entity` : "no entity"},
                  so the tab shows the click layer only — rather than an empty ladder that would
                  read as zero customers.
                </p>
              </div>
            </div>
          </Card>
        )}
      </Section>

      {/* Three revenue figures, always together, never one. */}
      {data.revenue && (
        <div style={{ marginTop: 14 }}>
          <Card pad={20}>
            <div className="ads-grid">
              <Stat label="Contracted" note="signed value of every enrollment traced to Meta">
                <Fig lead="$" value={compact(data.revenue.contracted).replace("$", "")} />
              </Stat>
              <Stat label="Cash received" note={cashNote(data.revenue)}>
                <Fig lead="$" value={compact(data.revenue.collected).replace("$", "")} />
              </Stat>
              <Stat label="Projected" note="suppressed — no curve fitted">
                <Fig value="—" tone={C.muted} />
              </Stat>
              <Stat label="ROAS · contracted">
                <Fig value={mult(data.revenue.roas_contracted)} />
              </Stat>
              <Stat label="ROAS · collected">
                <Fig value={mult(data.revenue.roas_collected)} />
              </Stat>
              {data.cac && (
                <Stat label="Cost per enrollment"
                      note={`${data.cac.attributed_closes} traced of ${data.cac.all_closes}`}>
                  <Fig lead="$" value={data.cac.attributed === null ? "—"
                    : compact(data.cac.attributed).replace("$", "")} />
                </Stat>
              )}
            </div>
            {data.cac && (
              <p style={{ fontSize: 12, color: C.slate, margin: "14px 0 0", lineHeight: 1.6,
                          maxWidth: 720 }}>
                {/* bc_launch_opp holds only the ACTIVE launch's opportunities, so a window
                    before that launch has no population to blend against. No denominator and
                    nobody enrolling look identical as a dash, so the payload separates them and
                    so does this. */}
                {data.cac.blended_available === false ? (
                  <strong style={{ color: C.ink }}>
                    No blended figure for this window — the launch pipeline holds only the
                    current cohort, so there is no all-enrollments denominator to compare
                    against. That is a missing number, not a zero.
                  </strong>
                ) : (
                  <>
                    <strong style={{ color: C.ink }}>
                      Blended is {usd(data.cac.blended)}.
                    </strong>{" "}
                    {data.cac.blended_label}
                  </>
                )}
                {data.unattributed?.closes > 0 && (
                  <> {" "}{num(data.unattributed.closes)} enrollment
                    {data.unattributed.closes === 1 ? "" : "s"} in this window trace to no ad at
                    all and are assigned to no campaign.</>
                )}
                {data.revenue.annualized_closes > 0 && (
                  <> {" "}{num(data.revenue.annualized_closes)} rolling monthly membership
                    {data.revenue.annualized_closes === 1 ? " is" : "s are"} annualized at twelve
                    months — a modelling choice, not a signed number.</>
                )}
                {/* Where the contract values came from. An enrollment nothing has priced reads
                    as a smaller total, not as missing configuration, unless the page says so. */}
                {data.revenue.ghl_priced_closes > 0 && (
                  <> {" "}{num(data.revenue.ghl_priced_closes)} contract
                    {data.revenue.ghl_priced_closes === 1 ? " value comes" : " values come"} from
                    the GHL opportunity amount rather than the launch price sheet — for a financed
                    member that figure is the deposit, so it understates.</>
                )}
                {data.revenue.unpriced_closes > 0 && (
                  <> {" "}{num(data.revenue.unpriced_closes)} enrollment
                    {data.revenue.unpriced_closes === 1 ? " carries" : "s carry"} no contract value
                    at all — unknown, which is not zero. Setting their Payment Type on the Sales
                    Desk brings them into this total.</>
                )}
              </p>
            )}
          </Card>
        </div>
      )}

      {/* The two denominators. Never added, never expressed as a rate. */}
      {data.coverage && (
        <div style={{ marginTop: 14 }}>
          <Card pad={18}>
            <div style={{ display: "grid", gap: 14,
                          gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))" }}>
              <div>
                <div style={{ fontSize: 10.5, fontWeight: 600, letterSpacing: ".07em",
                              textTransform: "uppercase", color: C.muted }}>
                  Leads · Meta&rsquo;s count
                </div>
                <div style={{ fontSize: 24, fontWeight: 600, marginTop: 4 }}>
                  <Fig value={num(data.coverage.meta_leads)} />
                </div>
                <div style={{ fontSize: 11, color: C.muted, marginTop: 2 }}>
                  Meta measuring itself
                </div>
              </div>
              <div>
                <div style={{ fontSize: 10.5, fontWeight: 600, letterSpacing: ".07em",
                              textTransform: "uppercase", color: C.muted }}>
                  Registrations matched
                </div>
                <div style={{ fontSize: 24, fontWeight: 600, marginTop: 4 }}>
                  <Fig value={num(data.coverage.registrations_matched)} />
                </div>
                <div style={{ fontSize: 11, color: C.muted, marginTop: 2 }}>
                  of {num(data.coverage.registrations_total)} in Acumyn
                </div>
              </div>
              <div>
                <div style={{ fontSize: 10.5, fontWeight: 600, letterSpacing: ".07em",
                              textTransform: "uppercase", color: C.muted }}>
                  Campaign grade or better
                </div>
                <div style={{ fontSize: 24, fontWeight: 600, marginTop: 4 }}>
                  <Fig value={num(data.coverage.campaign_grade_or_better)} />
                </div>
                <div style={{ fontSize: 11, color: C.muted, marginTop: 2 }}>
                  ad grade: {num(data.coverage.ad_grade)}
                </div>
              </div>
            </div>
            <p style={{ fontSize: 12, color: C.slate, margin: "14px 0 0", lineHeight: 1.6,
                        maxWidth: 720 }}>
              {data.coverage.gap_note}
            </p>
          </Card>
        </div>
      )}

      {/* 02 Findings */}
      <Section n="02" id="findings" title="Findings"
        lede="Ordered worst first, and computed on the server so the page cannot disagree with
              the API about what counts as a problem.">
        <div style={{ display: "grid", gap: 8 }}>
          {(data.alerts || []).length === 0 && (
            <Card><span style={{ fontSize: 13, color: C.muted }}>Nothing to flag in this window.</span></Card>
          )}
          {(data.alerts || []).map((a, i) => {
            const b = band(a.tone === "bad" ? "bad" : a.tone === "good" ? "good" : "warn");
            return (
              <div key={i} style={{ background: b.bg, border: `1px solid ${b.bar}`,
                                    borderRadius: 12, padding: "11px 15px", display: "flex",
                                    gap: 10, alignItems: "baseline" }}>
                <Pill tone={a.tone === "bad" ? "bad" : a.tone === "good" ? "good" : "warn"}>
                  {a.metric}
                </Pill>
                <span style={{ fontSize: 13, color: C.ink, lineHeight: 1.5 }}>{a.text}</span>
              </div>
            );
          })}
        </div>
      </Section>

      {/* 03 Where it came from */}
      <Section n="03" id="sources" title="Where it came from"
        lede="Groups are resolved live from this workspace&rsquo;s rules, never stored — rename a
              campaign and it regroups on the next read rather than silently staying where it was.">
        <Card>
          <Bars rows={(data.groups || []).map((g) => ({ name: g.name, spend: g.spend }))}
                valueKey="spend" format={(v) => usd(v)} />
          {data.unmatched_count > 0 && (
            <p style={{ fontSize: 12, color: C.warnInk, margin: "14px 0 0" }}>
              {data.unmatched_count} campaign{data.unmatched_count === 1 ? "" : "s"} matched no
              rule and fell to Other.
            </p>
          )}
          {/* The editor lives HERE rather than in Settings, next to the bar that shows the
              problem and the line that names it. A new campaign lands in Other the moment
              somebody creates one, and the person who named it is the person who can say where
              it belongs - sending them to another page to guess at rules without seeing the
              campaigns is how the drift persists. */}
          <GroupingRules account={account} onSaved={retry} />
        </Card>
      </Section>

      {/* 04 What a customer costs */}
      <Section n="04" id="cost" title="What a customer costs"
        lede="Cost per lead is the furthest down the chain this page can currently see. It is a
              floor on what a customer costs, never the number itself — most leads never enroll.">
        <Card pad={0}>
          <div className="ads-table-row ads-head"
               style={{ padding: "11px 16px", borderBottom: `1px solid ${C.line}`,
                        fontSize: 10.5, fontWeight: 600, letterSpacing: ".06em",
                        textTransform: "uppercase", color: C.muted, background: C.surface2 }}>
            <span>Campaign</span><span>Spend</span><span>Link clicks</span>
            <span>CTR</span><span>CPC</span><span>Cost / lead</span>
          </div>
          {(data.campaigns || []).map((c) => (
            <div key={c.external_id} className="ads-table-row"
                 style={{ padding: "12px 16px", borderBottom: `1px solid ${C.hair}`, fontSize: 13 }}>
              <div className="ads-cell-name" style={{ minWidth: 0 }}>
                <div style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {c.name}
                </div>
                <div style={{ fontSize: 11, color: C.muted }}>{c.group}</div>
              </div>
              <Fig lead="$" value={compact(c.spend).replace("$", "")} />
              <Fig value={compact(c.link_clicks)} />
              <Fig value={c.ctr === null ? "—" : c.ctr.toFixed(2)} unit={c.ctr === null ? "" : "%"} />
              <Fig lead="$" value={c.cpc === null ? "—" : c.cpc.toFixed(2)} />
              {/* null renders as an em dash, never as $0 — "no leads yet" and "free leads" are
                  different facts and the API returns null precisely to keep them apart. */}
              <Fig lead={c.cpl === null ? "" : "$"}
                   value={c.cpl === null ? "—" : c.cpl.toFixed(0)}
                   tone={c.cpl === null ? C.muted : C.ink} />
            </div>
          ))}
        </Card>
      </Section>

      {/* 05 The creative wall. Ranked by spend, because that is the lever - the ad taking the
          most money is the one worth recognising first. Revenue per ad is Phase 5 and its
          absence is stated in the lede rather than shown as an empty column. */}
      <Section n="05" id="creative" title="The ads themselves"
        lede={creatives.data?.revenue_reason
          ? `Ranked by spend. Revenue per ad is not here yet — ${creatives.data.revenue_reason.charAt(0).toLowerCase()}${creatives.data.revenue_reason.slice(1)}`
          : "Ranked by spend, because the ad taking the most money is the one worth looking at first."}>
        <CreativeWall data={creatives.data} loading={creatives.loading} />
      </Section>

      {/* 06 What to trust */}
      <Section n="06" id="trust" title="What to trust"
        lede="The grain each number on this page is entitled to, and where it stops.">
        <Card>
          <div style={{ display: "grid", gap: 12, fontSize: 12.5, color: C.slate, lineHeight: 1.6 }}>
            <div>
              <strong style={{ color: C.ink }}>Rates are computed from sums.</strong> CTR here is
              total link clicks over total impressions for the window — never an average of daily
              rates, which would differ and would look just as plausible.
            </div>
            <div>
              <strong style={{ color: C.ink }}>Ad-level revenue is unavailable.</strong>{" "}
              {creatives.data?.revenue_reason ||
                "Ad URLs need utm_content={{ad.id}} and a matching field in the CRM."}
            </div>
            <div>
              <strong style={{ color: C.ink }}>Meta&rsquo;s lead count is a diagnostic.</strong>{" "}
              It is Meta measuring itself. Once registrations are joined, the owned funnel starts
              at registration and Meta&rsquo;s number becomes a cross-check rather than a
              denominator.
            </div>
            {data.freshness?.last_error && (
              <div style={{ color: C.badInk }}>
                <strong>Last sync failed:</strong> {data.freshness.last_error}
              </div>
            )}
            <div style={{ color: C.muted, display: "flex", alignItems: "baseline", gap: 10,
                          flexWrap: "wrap" }}>
              <span>
                Last synced {data.freshness?.last_synced_at
                  ? new Date(data.freshness.last_synced_at).toLocaleString()
                  : "never"}.
              </span>
              {/* The funnel re-derives on a nightly job, which is right for a system nobody is
                  watching and wrong for the moment somebody CHANGES the enrolled definition -
                  a stage map you can edit but cannot see the effect of until tomorrow morning
                  is not really editable. */}
              {API_BASE && (
                <button type="button" onClick={recompute} disabled={recomputing}
                        style={{ background: "none", border: "none", padding: 0,
                                 font: "inherit", color: recomputing ? C.muted : C.accent,
                                 cursor: recomputing ? "default" : "pointer",
                                 textDecoration: "underline" }}>
                  {recomputing ? "Recomputing the funnel…" : "Recompute the funnel now"}
                </button>
              )}
              {recomputeMsg && <span style={{ color: C.slate }}>{recomputeMsg}</span>}
            </div>
          </div>
        </Card>
      </Section>
      </div>
    </div>
  );
}
