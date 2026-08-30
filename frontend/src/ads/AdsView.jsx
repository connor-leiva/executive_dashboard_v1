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
import { useState } from "react";

import { BAND, C, FIG, FONT, HEAD, band, compact, mult, num, pct, usd } from "./adsTokens.js";
import Funnel from "./Funnel.jsx";
import { useAdsAccounts, useAdsCreatives, useAdsOverview } from "./useAds.js";

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

function Section({ n, title, lede, children }) {
  return (
    <section style={{ marginTop: 34 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 12 }}>
        <span style={{ fontFamily: FIG, fontSize: 12, fontWeight: 700, color: C.muted,
                       letterSpacing: ".08em" }}>{n}</span>
        <h2 style={{ fontFamily: HEAD, fontSize: 17, fontWeight: 600, color: C.ink, margin: 0 }}>
          {title}
        </h2>
        <span style={{ flex: 1, height: 1, background: C.line }} />
      </div>
      {lede && (
        <p style={{ fontFamily: FONT, fontSize: 12.5, color: C.slate, margin: "7px 0 0",
                    maxWidth: 640, lineHeight: 1.5 }}>{lede}</p>
      )}
      <div style={{ marginTop: 14 }}>{children}</div>
    </section>
  );
}

function Card({ children, pad = 18 }) {
  return (
    <div style={{ background: C.surface, border: `1px solid ${C.line}`, borderRadius: 14,
                  padding: pad }}>{children}</div>
  );
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

  const accounts = useAdsAccounts();
  const { data, error, loading, retry } = useAdsOverview({ account, period, basis });
  const creatives = useAdsCreatives({ account, period, sort: "spend", limit: 12 });

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
    <div style={{ fontFamily: FONT, color: C.ink }}>
      <style>{`
        .ads-bar { transition: width .2s ease; }
        .ads-chip:focus-visible { outline: 2px solid ${C.accent}; outline-offset: 2px; }
        .ads-grid { display: grid; gap: 10px; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); }
        .ads-table-row { display: grid; gap: 10px; align-items: center;
                         grid-template-columns: minmax(0,2.2fr) repeat(5, minmax(0,1fr)); }
        @media (max-width: 720px) {
          /* Not a horizontally scrolling six-column table called responsive: the row becomes a
             stacked card, name on its own line. */
          .ads-table-row { grid-template-columns: repeat(2, minmax(0,1fr)); }
          .ads-table-row .ads-cell-name { grid-column: 1 / -1; }
          .ads-head { display: none; }
        }
        @media (prefers-reduced-motion: reduce) { .ads-bar { transition: none; } }
      `}</style>

      {/* Header + controls */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "center",
                    justifyContent: "space-between" }}>
        <div>
          <h1 style={{ fontFamily: HEAD, fontSize: 21, margin: 0, color: C.ink }}>
            {data.account.name}
          </h1>
          <div style={{ fontSize: 12, color: C.muted, marginTop: 3 }}>
            {data.account.external_id} · {data.account.timezone_name || "account time"} ·{" "}
            {data.range.label} ({data.range.start} to {data.range.end})
          </div>
        </div>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {acctList.length > 1 && (
            <select value={account || ""} onChange={(e) => setAccount(e.target.value || null)}
                    style={{ fontFamily: FONT, fontSize: 12, padding: "5px 8px", borderRadius: 8,
                             border: `1px solid ${C.line}`, background: C.surface, color: C.ink }}>
              {acctList.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
            </select>
          )}
          {PERIODS.map((p) => (
            <button key={p.k} className="ads-chip" onClick={() => setPeriod(p.k)}
                    style={{ fontFamily: FONT, fontSize: 12, fontWeight: period === p.k ? 600 : 500,
                             padding: "5px 11px", borderRadius: 999, cursor: "pointer",
                             border: `1px solid ${period === p.k ? C.accent : C.line}`,
                             background: period === p.k ? C.accentBg : C.surface,
                             color: period === p.k ? C.ink : C.slate }}>
              {p.label}
            </button>
          ))}
        </div>
      </div>

      {/* Hero */}
      <div style={{ marginTop: 18 }}>
        <Card pad={20}>
          <div className="ads-grid">
            <Stat label="Spend"><Fig lead="$" value={compact(t.spend).replace("$", "")} /></Stat>
            <Stat label="Impressions"><Fig value={compact(t.impressions)} /></Stat>
            <Stat label="Link clicks" note="not all clicks">
              <Fig value={compact(t.link_clicks)} />
            </Stat>
            <Stat label="Link CTR" tone={band(data.bands.ctr).ink}>
              <Fig value={t.ctr === null ? "—" : t.ctr.toFixed(2)} unit={t.ctr === null ? "" : "%"} />
            </Stat>
            <Stat label="CPM" tone={band(data.bands.cpm).ink}>
              <Fig lead="$" value={t.cpm === null ? "—" : t.cpm.toFixed(2)} />
            </Stat>
            <Stat label="Leads · Meta" note="diagnostic, not the funnel">
              <Fig value={num(t.leads)} />
            </Stat>
          </div>
          <p style={{ fontSize: 12, color: C.muted, margin: "14px 0 0", lineHeight: 1.6,
                      maxWidth: 720 }}>
            <strong style={{ color: C.slate }}>Link clicks, not all clicks.</strong> Meta&rsquo;s
            headline click count includes reactions, comments, shares and page-name clicks. This
            uses the link measure, so CTR here reads lower than a dashboard built on{" "}
            <code>clicks</code> — that is a correction, not a drop in performance.
          </p>
        </Card>
      </div>

      {/* 01 The chain */}
      <Section n="01" title="The chain"
        lede="Meta owns impressions, clicks and its own lead count. Everything after that — the
              registration, the booked call, the signature, the cash — already lives in Acumyn.
              Joining the two is what this module is for.">
        {data.funnel ? (
          <Funnel rungs={data.funnel} spend={t.spend} />
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
              <Stat label="Contracted" note="what the campaign produced">
                <Fig lead="$" value={compact(data.revenue.contracted).replace("$", "")} />
              </Stat>
              <Stat label="Collected" note={`cash in · joined by ${data.revenue.collected_join}`}>
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
                <strong style={{ color: C.ink }}>Blended is {usd(data.cac.blended)}.</strong>{" "}
                {data.cac.blended_label}
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
      <Section n="02" title="Findings"
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
      <Section n="03" title="Where it came from"
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
        </Card>
      </Section>

      {/* 04 What a customer costs */}
      <Section n="04" title="What a customer costs"
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

      {/* 05 What to trust */}
      <Section n="05" title="What to trust"
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
            <div style={{ color: C.muted }}>
              Last synced {data.freshness?.last_synced_at
                ? new Date(data.freshness.last_synced_at).toLocaleString()
                : "never"}.
            </div>
          </div>
        </Card>
      </Section>
    </div>
  );
}
