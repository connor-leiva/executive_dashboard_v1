/* ULRG › Recruiting — the tab (RECRUITING-SPEC §9 Phase 2, docs/recruiting-screens.md).
 *
 * READ-ONLY. Rows open the drawer and the drawer shows facts; no button here writes to
 * GoHighLevel, because nothing can until Phase 4's outbox exists. The queue's Done / Tomorrow
 * controls are Phase 3 for the same reason: they are not styling that is missing, they are a
 * rule engine that is missing.
 *
 * EVERY NUMBER IS THE SERVER'S. This file formats and positions; it does not compute. Pace,
 * bands, verdicts and the path line all arrive decided, so there is exactly one place they can
 * be wrong. The one arithmetic here is `Math.min` on a bar's width, which is layout.
 *
 * DEGRADING IS A FEATURE. Half the payload is null in Phase 1 -- the queue, commitments, goals,
 * dials -- and each null carries its reason in `unavailable`. Those reasons are rendered rather
 * than swallowed: "no data", "not built yet" and "caught up" are three different facts, and a
 * blank card that cannot tell them apart gets read as "we recruited nobody".
 */
import { useState } from "react";
import { Bar, Card, Chip, Eyebrow } from "./Parts.jsx";
import { C, FB, FD, FM, band, verdictStyle } from "./scorecardMath.js";
import { useCandidate, useRecruiting } from "./useRecruiting.js";

const API_LIVE = Boolean(import.meta.env.VITE_API_BASE);

/* Three hexes the design uses once each and the palette has no token for (screens doc,
   "Non-token hexes"). Named here so they are obviously deliberate rather than a drift. */
const DASHED = "#DCD2C2";        // the dashed edge of an unfilled goal tile / open slot
const BANNER_EDGE = "#F3E39A";   // the queue banner's border, against daffodilBg

const TONE = {
  late: { bg: C.poppyBg, ink: C.poppyInk },
  today: { bg: C.amberBg, ink: C.amberInk },
  ok: { bg: C.meadowBg, ink: C.meadowInk },
  mute: { bg: C.hairSoft, ink: C.slate },
};

const money = (n) => (n === null || n === undefined ? null
  : n >= 1000 ? `$${Math.round(n / 1000)}k` : `$${Math.round(n)}`);

function SectionTitle({ children, meta, right }) {
  return (
    <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap", marginBottom: 12 }}>
      <div style={{ fontFamily: FD, fontSize: 16, fontWeight: 600, color: C.ink }}>{children}</div>
      {meta && <div style={{ fontFamily: FM, fontSize: 11, color: C.muted }}>{meta}</div>}
      <span style={{ flex: 1 }} />
      {right}
    </div>
  );
}

/* A block the server could not fill, and why. Quiet on purpose: this is scaffolding that will
   be replaced by content, not an error the reader has to act on. */
function NotYet({ children }) {
  return (
    <div style={{ fontFamily: FB, fontSize: 12.5, color: C.muted, lineHeight: 1.5,
                  background: C.hairSoft, border: `1px solid ${C.hair}`, borderRadius: 9,
                  padding: "11px 13px" }}>{children}</div>
  );
}

function Initials({ t, tone = "teal", size = 22 }) {
  const look = tone === "sdr"
    ? { bg: C.daffodilBg, ink: C.amberInk }
    : { bg: C.mist, ink: C.teal };
  return (
    <span style={{ display: "inline-flex", alignItems: "center", justifyContent: "center",
                   width: size, height: size, borderRadius: 99, flex: "0 0 auto",
                   fontFamily: FM, fontSize: Math.round(size * 0.41),
                   background: look.bg, color: look.ink }}>{t || "?"}</span>
  );
}

/* ── 1a. Hero, close view (owner and Team Leader) ───────────────────────────────────────── */

function GoalTiles({ goal }) {
  // One tile per goal unit, filled by a signing. With no goal set there is nothing to divide
  // into tiles, so the signings are listed as themselves rather than as a fraction of unknown.
  const target = goal.goal;
  if (!target) {
    return (
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 14 }}>
        {goal.signings.map((sg) => (
          <span key={sg.candidate_id} title={`${sg.name || ""} · ${sg.occurred_on}`}
                style={{ width: 38, height: 44, borderRadius: 6, background: C.ink, color: C.onDark,
                         fontFamily: FM, fontSize: 11, display: "inline-flex", alignItems: "center",
                         justifyContent: "center" }}>{sg.initials}</span>
        ))}
        {goal.signings.length === 0 && (
          <span style={{ fontFamily: FB, fontSize: 12.5, color: C.muted }}>No signings recorded this month yet.</span>
        )}
      </div>
    );
  }
  const tiles = [];
  for (let i = 0; i < target; i += 1) {
    const sg = goal.signings[i];
    tiles.push(sg
      ? <span key={i} title={`${sg.name || ""} · ${sg.occurred_on}`}
              style={{ width: 38, height: 44, borderRadius: 6, background: C.ink, color: C.onDark,
                       fontFamily: FM, fontSize: 11, display: "inline-flex", alignItems: "center",
                       justifyContent: "center" }}>{sg.initials}</span>
      : <span key={i} style={{ width: 38, height: 44, borderRadius: 6, background: C.hairSoft,
                               border: `1px dashed ${DASHED}` }} />);
  }
  return <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 14 }}>{tiles}</div>;
}

function Hero({ data }) {
  const { goal, path, period, viewer, unavailable } = data;
  const v = verdictStyle(goal?.verdict);
  // "SEPTEMBER · TEAM · 7 DAYS LEFT" for an owner, "YOUR SEPTEMBER · 7 DAYS LEFT" for a Team
  // Leader — two constructions, not one with a swapped word (screens doc §1a).
  const eyebrow = viewer.role === "owner"
    ? `${period.label} · Team · ${period.days_left} days left`
    : `Your ${period.label} · ${period.days_left} days left`;
  const pct = goal?.goal ? Math.round(((goal.pace ?? goal.signed) / goal.goal) * 100) : null;

  return (
    <Card pad={0}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%,460px),1fr))" }}>
        <div style={{ padding: "22px 26px 24px", minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <Eyebrow>{eyebrow}</Eyebrow>
            {v && <Chip ink={v.ink} bg={v.bg}>{v.t.toUpperCase()}</Chip>}
          </div>
          <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginTop: 10, flexWrap: "wrap" }}>
            <span style={{ fontFamily: FD, fontSize: 60, fontWeight: 700, lineHeight: 1,
                           letterSpacing: "-.02em", color: C.ink }}>{goal?.signed ?? 0}</span>
            <span style={{ fontFamily: FD, fontSize: 20, fontWeight: 500, color: C.slate, whiteSpace: "nowrap" }}>
              {goal?.goal ? `of ${goal.goal} signed` : "signed"}
            </span>
          </div>
          <GoalTiles goal={goal || { signed: 0, signings: [], goal: null }} />
          {pct !== null ? (
            <>
              <div style={{ marginTop: 16, maxWidth: 180 }}><Bar pct={pct} /></div>
              <div style={{ fontFamily: FB, fontSize: 13, lineHeight: 1.45, marginTop: 8 }}>
                <b style={{ fontFamily: FM, fontWeight: 600, color: (band(pct) || {}).ink }}>
                  On pace for {goal.pace} of {goal.goal}.
                </b>{" "}
                {goal.need > 0 ? `${goal.need} more needed by the end of ${period.label}.` : "Goal met."}
              </div>
            </>
          ) : (
            <div style={{ marginTop: 16 }}><NotYet>{unavailable?.goal}</NotYet></div>
          )}
        </div>

        <div style={{ background: C.parchment, borderLeft: `1px solid ${C.hair}`, padding: "22px 24px 20px", minWidth: 0 }}>
          <Eyebrow>Path to goal</Eyebrow>
          <div style={{ fontFamily: FD, fontSize: 16, fontWeight: 600, color: C.ink, marginTop: 8, lineHeight: 1.4 }}>
            {path?.line || "Nothing in flight yet."}
          </div>
          <div style={{ marginTop: 12 }}>
            {(path?.rows || []).map((r) => <PathRow key={r.candidate_id} r={r} />)}
            {(path?.rows || []).length === 0 && (
              <div style={{ fontFamily: FB, fontSize: 12.5, color: C.muted, marginTop: 6 }}>
                Nobody is past the first meeting yet.
              </div>
            )}
          </div>
        </div>
      </div>
    </Card>
  );
}

function PathRow({ r }) {
  const tone = TONE[r.status?.tone || "mute"];
  return (
    <button type="button" onClick={() => openDrawer(r.candidate_id)}
            style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) auto 24px", gap: 10,
                     alignItems: "center", width: "100%", textAlign: "left", background: "transparent",
                     border: "none", borderBottom: `1px solid ${C.hair}`, padding: "9px 4px",
                     cursor: "pointer" }}>
      <span style={{ minWidth: 0 }}>
        <span style={{ display: "block", fontFamily: FD, fontSize: 13, fontWeight: 600, color: C.ink,
                       overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.name}</span>
        <span style={{ display: "block", fontFamily: FM, fontSize: 10.5, color: C.muted,
                       overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {[r.stage, r.days !== null && r.days !== undefined ? `${r.days} days` : null, r.brokerage]
            .filter(Boolean).join(" · ")}
        </span>
      </span>
      {r.status?.label
        ? <span style={{ fontFamily: FM, fontSize: 10, padding: "3px 7px", borderRadius: 4,
                         background: tone.bg, color: tone.ink, whiteSpace: "nowrap" }}>{r.status.label}</span>
        : <span />}
      <Initials t={r.owner_initials} />
    </button>
  );
}

/* Module-level so a row deep in the tree can open the drawer without threading a callback
   through five components. Set once by the page on mount. */
let openDrawer = () => {};

/* ── 1b. Hero, SDR view ─────────────────────────────────────────────────────────────────── */

function HeroSdr({ data }) {
  const { sdr, period, unavailable } = data;
  const m = sdr.month;
  const v = verdictStyle(m.verdict);
  const bookedPct = m.goal ? Math.round((m.booked / m.goal) * 100) : null;
  return (
    <Card pad={0}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%,460px),1fr))" }}>
        <div style={{ padding: "22px 26px 24px", minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <Eyebrow>{`Your ${period.label} · ${period.days_left} days left`}</Eyebrow>
            {v && <Chip ink={v.ink} bg={v.bg}>{v.t.toUpperCase()}</Chip>}
          </div>
          <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginTop: 10, flexWrap: "wrap" }}>
            <span style={{ fontFamily: FD, fontSize: 60, fontWeight: 700, lineHeight: 1,
                           letterSpacing: "-.02em", color: C.ink }}>{m.booked}</span>
            <span style={{ fontFamily: FD, fontSize: 20, fontWeight: 500, color: C.slate, whiteSpace: "nowrap" }}>
              {m.goal ? `of ${m.goal} booked` : "booked"}
            </span>
          </div>
          <div style={{ maxWidth: 440, marginTop: 16 }}>
            <LabelledBar label={m.goal ? `Booked ${m.booked} of ${m.goal}` : `Booked ${m.booked}`}
                         pct={bookedPct} />
            <LabelledBar label={`Held ${m.held} of ${m.booked}${m.show_rate !== null ? ` · ${m.show_rate}% show` : ""}`}
                         pct={m.show_rate} />
          </div>
          {!m.goal && <div style={{ marginTop: 14 }}><NotYet>{unavailable?.goal}</NotYet></div>}
        </div>
        <div style={{ background: C.parchment, borderLeft: `1px solid ${C.hair}`, padding: "22px 24px 20px", minWidth: 0 }}>
          <Eyebrow>Where to book</Eyebrow>
          <div style={{ marginTop: 10 }}>
            <NotYet>{unavailable?.calendars}</NotYet>
          </div>
        </div>
      </div>
    </Card>
  );
}

function LabelledBar({ label, pct }) {
  return (
    <div style={{ marginTop: 10 }}>
      <div style={{ fontFamily: FB, fontSize: 12, color: C.slate, marginBottom: 5 }}>{label}</div>
      {pct === null || pct === undefined
        ? <div style={{ height: 9, borderRadius: 99, background: C.hairSoft }} />
        : <Bar pct={pct} />}
    </div>
  );
}

/* ── 2. Do next ─────────────────────────────────────────────────────────────────────────── */

function DoNext({ data }) {
  const { queue, viewer, unavailable } = data;
  const items = queue?.items || [];
  const mine = viewer.role === "owner" ? "Do next" : "Your list today";
  return (
    <Card>
      <SectionTitle
        meta={queue?.counts?.total ? `${queue.counts.cleared} of ${queue.counts.total} cleared` : null}>
        {mine}
      </SectionTitle>
      <div style={{ fontFamily: FB, fontSize: 12.5, color: C.slate, marginTop: -6, marginBottom: 14, lineHeight: 1.5 }}>
        Built each morning from the rules in Settings. Texts, calls, emails and bookings sent from
        here are logged to GHL.
      </div>
      {items.length === 0
        ? <NotYet>{unavailable?.queue}</NotYet>
        : items.map((it) => <QueueRow key={it.id} it={it} owner={viewer.role === "owner"} />)}
    </Card>
  );
}

function QueueRow({ it, owner }) {
  const tone = TONE[it.due?.tone || "mute"];
  return (
    <div role="button" tabIndex={0} onClick={() => openDrawer(it.candidate.id)}
         onKeyDown={(e) => { if (e.key === "Enter") openDrawer(it.candidate.id); }}
         style={{ display: "grid", gridTemplateColumns: "92px minmax(0,1fr) auto", gap: 14,
                  alignItems: "center", padding: "13px 20px", margin: "0 -20px",
                  borderTop: `1px solid ${C.hairSoft}`, cursor: "pointer" }}>
      <span style={{ fontFamily: FM, fontSize: 10, padding: "3px 7px", borderRadius: 4,
                     background: tone.bg, color: tone.ink, justifySelf: "start", whiteSpace: "nowrap" }}>
        {it.due?.label}
      </span>
      <span style={{ minWidth: 0 }}>
        <span style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
          <span style={{ fontFamily: FD, fontSize: 14, fontWeight: 600, color: C.ink }}>{it.candidate.name}</span>
          <span style={{ fontFamily: FM, fontSize: 10.5, color: C.muted, whiteSpace: "nowrap" }}>
            {[it.candidate.stage, money(it.candidate.gci) ? `${money(it.candidate.gci)} GCI` : null]
              .filter(Boolean).join(" · ")}
          </span>
        </span>
        <span style={{ display: "block", fontFamily: FB, fontSize: 12.5, color: C.body, marginTop: 3,
                       textWrap: "pretty" }}>{it.why}</span>
        <span style={{ display: "block", fontFamily: FM, fontSize: 9.5, letterSpacing: ".06em",
                       textTransform: "uppercase", color: C.teal, marginTop: 4 }}>{it.rule_label}</span>
      </span>
      {owner && it.owner && (
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          <Initials t={it.owner.initials} tone={it.owner.role === "sdr" ? "sdr" : "teal"} size={18} />
          <span style={{ fontFamily: FB, fontSize: 12, color: C.slate }}>{it.owner.first}</span>
        </span>
      )}
    </div>
  );
}

/* ── 3. The SDR card ────────────────────────────────────────────────────────────────────── */

function SdrCard({ data }) {
  const { sdr, unavailable } = data;
  if (!sdr) {
    return (
      <Card>
        <SectionTitle>SDR</SectionTitle>
        <NotYet>No SDR seat yet. Add one in Settings › Recruiting to count bookings and held appointments.</NotYet>
      </Card>
    );
  }
  const m = sdr.month;
  return (
    <Card>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 14 }}>
        <Initials t={sdr.initials} tone="sdr" size={30} />
        <div style={{ minWidth: 0 }}>
          <div style={{ fontFamily: FD, fontSize: 15, fontWeight: 600, color: C.ink }}>{sdr.name}</div>
          <div style={{ fontFamily: FM, fontSize: 10.5, color: C.muted }}>SDR · books for Team Leaders</div>
        </div>
      </div>
      <MetricLine label="Appointments booked" when="this month" actual={m.booked} commit={m.goal} />
      <MetricLine label="Appointments held" when="this month" actual={m.held} commit={null} />
      <div style={{ marginTop: 12 }}>
        <NotYet>{unavailable?.sdr_week || unavailable?.commitments}</NotYet>
      </div>
      <div style={{ marginTop: 16 }}>
        <div style={{ fontFamily: FM, fontSize: 9, letterSpacing: ".07em", textTransform: "uppercase",
                      color: C.muted, borderBottom: `1px solid ${C.hair}`, paddingBottom: 6 }}>
          Booked this week, by calendar
        </div>
        {(sdr.by_calendar || []).map((row) => (
          <div key={row.seat_id} style={{ display: "grid", gridTemplateColumns: "64px 1fr auto", gap: 10,
                                          alignItems: "center", padding: "8px 0",
                                          borderBottom: `1px solid ${C.hairSoft}` }}>
            <span style={{ fontFamily: FB, fontSize: 12, color: C.slate, minWidth: 0, overflow: "hidden",
                           textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{row.first}</span>
            <span style={{ display: "flex", gap: 3, minWidth: 0 }}>
              {row.cells.map((cell, i) => <SlotCell key={i} kind={cell} />)}
            </span>
            <span style={{ fontFamily: FM, fontSize: 10.5, color: C.muted, whiteSpace: "nowrap" }}>
              {row.held} held · {row.booked} booked
            </span>
          </div>
        ))}
        {(sdr.by_calendar || []).length === 0 && (
          <div style={{ fontFamily: FB, fontSize: 12, color: C.muted, marginTop: 8 }}>
            No Team Leader calendars are mapped yet.
          </div>
        )}
      </div>
    </Card>
  );
}

function SlotCell({ kind }) {
  const look = kind === "held" ? { background: C.meadow }
    : kind === "noshow" ? { background: C.poppyBg }
    : kind === "up" ? { background: C.mist }
    : { border: `1px dashed ${DASHED}` };
  return <span style={{ flex: "1 1 0", minWidth: 0, height: 14, borderRadius: 3, ...look }} />;
}

function MetricLine({ label, when, actual, commit }) {
  const pct = commit ? Math.round((actual / commit) * 100) : null;
  return (
    <div style={{ padding: "9px 0", borderBottom: `1px solid ${C.hairSoft}` }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
        <span style={{ fontFamily: FB, fontSize: 12.5, color: C.body, minWidth: 0, flex: 1 }}>{label}</span>
        <span style={{ fontFamily: FM, fontSize: 10.5, color: C.muted, whiteSpace: "nowrap" }}>{when}</span>
        <span style={{ fontFamily: FM, fontSize: 13, fontWeight: 600,
                       color: pct === null ? C.ink : (band(pct) || {}).ink, whiteSpace: "nowrap" }}>
          {actual}{commit ? ` / ${commit}` : ""}
        </span>
      </div>
      <div style={{ marginTop: 6, height: 4 }}>
        {pct === null
          ? <div style={{ height: 4, borderRadius: 99, background: C.hairSoft }} />
          : <Bar pct={pct} />}
      </div>
    </div>
  );
}

/* ── 4. Team Leader commitments ─────────────────────────────────────────────────────────── */

function Commitments({ data }) {
  return (
    <Card>
      <SectionTitle>Team Leader commitments</SectionTitle>
      <NotYet>{data.unavailable?.commitments}</NotYet>
    </Card>
  );
}

/* ── 5. Leaderboard ─────────────────────────────────────────────────────────────────────── */

function Leaderboard({ data }) {
  const rows = data.leaderboard || [];
  const mine = data.viewer?.seat_id;
  return (
    <Card>
      <SectionTitle meta={`${data.period.label} to date`}>Team Leader leaderboard</SectionTitle>
      {rows.length === 0 ? (
        <NotYet>No Team Leader seats yet. Add them in Settings › Recruiting.</NotYet>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <div style={{ minWidth: 520 }}>
            <div style={{ display: "grid", gridTemplateColumns: "22px minmax(150px,1fr) 128px 48px 64px",
                          gap: 8, fontFamily: FM, fontSize: 9, letterSpacing: ".07em",
                          textTransform: "uppercase", color: C.muted,
                          borderBottom: `1px solid ${C.hair}`, paddingBottom: 6 }}>
              <span>#</span><span>Team Leader</span><span>Signed</span><span>Held</span><span>Close</span>
            </div>
            {rows.map((r) => (
              <div key={r.seat_id} style={{ display: "grid",
                     gridTemplateColumns: "22px minmax(150px,1fr) 128px 48px 64px", gap: 8,
                     alignItems: "center", padding: "10px 0", borderBottom: `1px solid ${C.hairSoft}`,
                     background: r.seat_id === mine ? C.daffodilBg : "transparent" }}>
                <span style={{ fontFamily: FD, fontSize: 14, fontWeight: 700,
                               color: r.rank === 1 ? C.ink : C.muted }}>{r.rank}</span>
                <span style={{ display: "inline-flex", alignItems: "center", gap: 8, minWidth: 0 }}>
                  <Initials t={r.initials} size={26} />
                  <span style={{ minWidth: 0 }}>
                    <span style={{ display: "block", fontFamily: FD, fontSize: 13, fontWeight: 600,
                                   color: C.ink, overflow: "hidden", textOverflow: "ellipsis",
                                   whiteSpace: "nowrap" }}>{r.name}</span>
                    <span style={{ display: "block", fontFamily: FB, fontSize: 11, color: C.muted,
                                   overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {r.title || "Team Leader"}</span>
                  </span>
                </span>
                <span style={{ minWidth: 0 }}>
                  <span style={{ fontFamily: FM, fontSize: 12, color: C.ink, whiteSpace: "nowrap" }}>
                    {r.goal ? `${r.signed} of ${r.goal}` : `${r.signed} signed`}
                  </span>
                  <span style={{ display: "block", marginTop: 5 }}>
                    {r.pace_pct === null || r.pace_pct === undefined
                      ? <span style={{ display: "block", height: 6, borderRadius: 99, background: C.hairSoft }} />
                      : <Bar pct={r.pace_pct} w={110} />}
                  </span>
                </span>
                <span style={{ fontFamily: FM, fontSize: 12.5, color: C.body }}>{r.held_mtd}</span>
                <span style={{ fontFamily: FM, fontSize: 12.5,
                               color: r.close_rate_90d === null || r.close_rate_90d === undefined ? C.muted
                                 : r.close_rate_90d < 25 ? C.poppyInk : C.body }}>
                  {r.close_rate_90d === null || r.close_rate_90d === undefined ? "–" : `${r.close_rate_90d}%`}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
      <div style={{ fontFamily: FB, fontSize: 11.5, color: C.muted, marginTop: 10, lineHeight: 1.5 }}>
        Close is signed ÷ held, last 90 days. It needs 90 days of stage history before it can say anything.
      </div>
    </Card>
  );
}

/* ── 6. Pipeline ────────────────────────────────────────────────────────────────────────── */

function Pipeline({ data }) {
  const p = data.pipeline;
  if (!p) return <Card><SectionTitle>Pipeline</SectionTitle><NotYet>{data.connection?.reason}</NotYet></Card>;
  const max = Math.max(1, ...p.stages.map((s) => s.n));
  return (
    <Card>
      <SectionTitle meta={`Active now · ${p.active} people · ${p.nurture} in nurture`}>Pipeline</SectionTitle>
      {p.stages.length === 0 && <NotYet>No stages are mapped yet. Map them in Settings › Recruiting.</NotYet>}
      {p.stages.map((st) => (
        <div key={st.label} style={{ display: "grid", gridTemplateColumns: "120px minmax(0,1fr) 84px",
                                     gap: 10, alignItems: "center", padding: "9px 0",
                                     borderBottom: `1px solid ${C.hairSoft}` }}>
          <span style={{ minWidth: 0 }}>
            <span style={{ display: "block", fontFamily: FD, fontSize: 13, fontWeight: 600, color: C.ink,
                           overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{st.label}</span>
            <span style={{ display: "block", fontFamily: FM, fontSize: 9.5, textTransform: "uppercase",
                           letterSpacing: ".06em",
                           color: st.owner_role === "sdr" ? C.amberInk : C.teal }}>
              {st.owner_role === "sdr" ? "SDR" : "Team Leaders"}
            </span>
          </span>
          <span style={{ minWidth: 0 }}>
            <span style={{ display: "block", height: 18, borderRadius: 4,
                           width: `${Math.max(2, (st.n / max) * 100)}%`,
                           background: st.label === "Signed" ? C.meadow
                             : st.owner_role === "sdr" ? C.daffodilBg : C.mist }} />
          </span>
          <span style={{ textAlign: "right" }}>
            <span style={{ display: "block", fontFamily: FM, fontSize: 12.5, fontWeight: 600, color: C.ink }}>{st.n}</span>
            {money(st.gci) && <span style={{ display: "block", fontFamily: FM, fontSize: 10.5, color: C.muted }}>{money(st.gci)}</span>}
            {st.stuck > 0 && <span style={{ display: "block", fontFamily: FM, fontSize: 10, color: C.amberInk }}>{st.stuck} stuck</span>}
          </span>
        </div>
      ))}
      {p.unmapped > 0 && (
        <div style={{ fontFamily: FB, fontSize: 11.5, color: C.amberInk, marginTop: 10 }}>
          {p.unmapped} candidate{p.unmapped === 1 ? "" : "s"} sit in a stage that is not mapped to a group,
          so they are not counted anywhere. Map it in Settings › Recruiting.
        </div>
      )}
      <div style={{ fontFamily: FB, fontSize: 11.5, color: C.muted, marginTop: 10, lineHeight: 1.5 }}>
        The SDR owns candidates through Appointment set. Team Leaders own them from the meeting on.
      </div>
    </Card>
  );
}

/* ── 7. Where signings come from (owner only) ───────────────────────────────────────────── */

function Sources({ data }) {
  const rows = data.sources;
  if (!rows) return null;
  return (
    <Card>
      <SectionTitle meta={`${data.period.label} to date`}>Where signings come from</SectionTitle>
      <div style={{ overflowX: "auto" }}>
        <div style={{ minWidth: 560 }}>
          <div style={{ display: "grid", gridTemplateColumns: "minmax(170px,1.6fr) repeat(4,minmax(70px,1fr))",
                        gap: 8, fontFamily: FM, fontSize: 9, letterSpacing: ".07em", textTransform: "uppercase",
                        color: C.muted, borderBottom: `1px solid ${C.hair}`, paddingBottom: 6 }}>
            <span>Source</span><span>Candidates</span><span>Signed</span><span>Rate</span><span>GCI added</span>
          </div>
          {rows.map((r) => (
            <div key={r.name} style={{ display: "grid",
                   gridTemplateColumns: "minmax(170px,1.6fr) repeat(4,minmax(70px,1fr))", gap: 8,
                   alignItems: "center", padding: "9px 0", borderBottom: `1px solid ${C.hairSoft}` }}>
              <span style={{ fontFamily: FB, fontSize: 12.5, color: C.ink, minWidth: 0, overflow: "hidden",
                             textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.name}</span>
              <span style={{ fontFamily: FM, fontSize: 12.5, color: C.body }}>{r.candidates}</span>
              <span style={{ fontFamily: FM, fontSize: 12.5, color: C.body }}>{r.signed}</span>
              <span style={{ fontFamily: FM, fontSize: 12.5, color: C.body }}>{r.rate}%</span>
              <span style={{ fontFamily: FM, fontSize: 12.5, color: C.body }}>{money(r.gci) || "–"}</span>
            </div>
          ))}
        </div>
      </div>
      <div style={{ fontFamily: FB, fontSize: 11.5, color: C.muted, marginTop: 10 }}>
        No ad spend is joined to recruiting, so cost per signing is time rather than money.
      </div>
    </Card>
  );
}

/* ── 8. The drawer (read-only in Phase 2) ───────────────────────────────────────────────── */

function Drawer({ candidateId, onClose }) {
  const { data: live, error } = useCandidate(candidateId);
  // Offline there is no API to ask, and a drawer stuck on "…" is the SAMPLE_VIEW lesson
  // repeating itself: a preview that cannot reach the shape production sends is a preview of
  // something else. Synthesised from the same sample rows the list is drawn from.
  const data = live || (API_LIVE ? null : sampleCandidate(candidateId));
  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, zIndex: 60, background: "rgba(18,41,31,.35)",
                                    display: "flex", justifyContent: "flex-end" }}>
      <div onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Candidate"
           style={{ width: "min(500px, 94vw)", background: C.page, height: "100%", overflowY: "auto",
                    boxShadow: "-8px 0 30px -12px rgba(20,35,28,.45)" }}>
        <div style={{ background: C.surface, borderBottom: `1px solid ${C.hair}`, padding: "18px 20px" }}>
          <div style={{ display: "flex", alignItems: "flex-start", gap: 10 }}>
            <div style={{ minWidth: 0, flex: 1 }}>
              <div style={{ fontFamily: FD, fontSize: 19, fontWeight: 700, color: C.ink }}>
                {data?.name || (error ? "Not available" : "…")}
              </div>
              <div style={{ fontFamily: FB, fontSize: 12.5, color: C.slate, marginTop: 2 }}>
                {[data?.brokerage, data?.city].filter(Boolean).join(", ") || " "}
              </div>
            </div>
            <button type="button" onClick={onClose} aria-label="Close"
                    style={{ border: "none", background: "transparent", cursor: "pointer",
                             fontFamily: FD, fontSize: 18, color: C.muted, lineHeight: 1 }}>×</button>
          </div>
          {error && (
            <div style={{ fontFamily: FB, fontSize: 12.5, color: C.slate, marginTop: 10 }}>
              This candidate is not on your list. Ask an owner if you need their detail.
            </div>
          )}
          {data && (
            <div style={{ display: "flex", gap: 18, flexWrap: "wrap", marginTop: 14 }}>
              <Fact label="Stage">{[data.stage, data.days_in_stage !== null ? `${data.days_in_stage}d` : null].filter(Boolean).join(" · ") || "–"}</Fact>
              <Fact label="Trailing GCI">{money(data.gci_ttm) || "–"}</Fact>
              <Fact label="Owner">{data.owner?.first || "–"}</Fact>
              <Fact label="Booked by">{data.booker?.first || "–"}</Fact>
              <Fact label="Source">{data.source || "–"}</Fact>
            </div>
          )}
        </div>

        {data && (
          <div style={{ padding: "16px 20px 28px" }}>
            <div style={{ fontFamily: FB, fontSize: 12.5, color: C.slate, background: C.daffodilBg,
                          border: `1px solid ${BANNER_EDGE}`, borderRadius: 10, padding: "11px 13px",
                          lineHeight: 1.5 }}>
              Texting, calling, booking and stage moves from here arrive in Phase 4. Until then this
              is the record, and GHL is where it is changed.
            </div>
            {data.source_url && (
              <a href={data.source_url} target="_blank" rel="noopener noreferrer"
                 style={{ display: "inline-block", marginTop: 14, fontFamily: FB, fontSize: 12.5,
                          color: C.teal, textDecoration: "none", fontWeight: 600 }}>
                Open contact in GHL →
              </a>
            )}
            <div style={{ fontFamily: FM, fontSize: 9, letterSpacing: ".07em", textTransform: "uppercase",
                          color: C.muted, marginTop: 20, borderBottom: `1px solid ${C.hair}`,
                          paddingBottom: 6 }}>Activity</div>
            {(data.timeline || []).length === 0 && (
              <div style={{ fontFamily: FB, fontSize: 12.5, color: C.muted, marginTop: 10 }}>
                Nothing recorded yet. Messages and calls start landing here with the conversation poll.
              </div>
            )}
            {(data.timeline || []).map((t, i) => (
              <div key={i} style={{ display: "grid", gridTemplateColumns: "10px minmax(0,1fr) auto",
                                    gap: 10, alignItems: "start", padding: "10px 0",
                                    borderBottom: `1px solid ${C.hairSoft}` }}>
                <span style={{ width: 8, height: 8, borderRadius: 99, marginTop: 5,
                               background: t.kind === "stage_move" ? C.teal
                                 : t.source === "axcion" ? C.meadow : DASHED }} />
                <span style={{ minWidth: 0 }}>
                  <span style={{ display: "block", fontFamily: FB, fontSize: 12.5, fontWeight: 500, color: C.ink }}>
                    {t.summary || t.kind}
                  </span>
                </span>
                <span style={{ fontFamily: FM, fontSize: 10.5, color: C.muted, whiteSpace: "nowrap" }}>
                  {new Date(t.at).toLocaleDateString()}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function Fact({ label, children }) {
  return (
    <span style={{ minWidth: 0 }}>
      <span style={{ display: "block", fontFamily: FM, fontSize: 9, letterSpacing: ".07em",
                     textTransform: "uppercase", color: C.muted }}>{label}</span>
      <span style={{ display: "block", fontFamily: FB, fontSize: 12.5, color: C.ink, marginTop: 2 }}>{children}</span>
    </span>
  );
}

/* ── the page ───────────────────────────────────────────────────────────────────────────── */

export default function Recruiting() {
  const [open, setOpen] = useState(null);
  // `?as=` previews another seat. Live, the SERVER resolves it and enforces that only an
  // owner may; offline it picks which sample viewer to draw, so the same URL shows the same
  // three roles in a design review as it does against real data. Phase 2 has to be checked in
  // all three (§9) and there is no server here to ask.
  const as = new URLSearchParams(window.location.search).get("as");
  const { data, error } = useRecruiting({ as: API_LIVE ? as : null });
  openDrawer = setOpen;

  const view = data || sampleAs(as);

  if (error) {
    return (
      <Card>
        <div style={{ fontFamily: FD, fontSize: 15.5, fontWeight: 600, color: C.ink }}>Recruiting is unavailable</div>
        <div style={{ fontFamily: FB, fontSize: 12.5, color: C.slate, marginTop: 6 }}>
          {error.status === 403
            ? "This workspace has the tab but not the access behind it."
            : "The recruiting payload did not load. A refresh usually settles it."}
        </div>
      </Card>
    );
  }

  const conn = view.connection || {};
  if (conn.state && conn.state !== "ready") {
    return (
      <Card>
        <div style={{ fontFamily: FD, fontSize: 15.5, fontWeight: 600, color: C.ink }}>
          {conn.state === "not_connected" ? "No recruiting location connected"
            : conn.state === "not_configured" ? "Almost there"
            : "First sync hasn’t finished"}
        </div>
        <div style={{ fontFamily: FB, fontSize: 13, color: C.slate, marginTop: 6, maxWidth: 560, lineHeight: 1.55 }}>
          {conn.reason}
        </div>
        {conn.error && (
          <div style={{ fontFamily: FM, fontSize: 11.5, color: C.poppyInk, marginTop: 10 }}>{conn.error}</div>
        )}
      </Card>
    );
  }

  const isSdr = view.viewer?.role === "sdr";
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16, maxWidth: 1240 }}>
      {isSdr ? <HeroSdr data={view} /> : <Hero data={view} />}

      <div style={{ display: "flex", flexWrap: "wrap", gap: 16 }}>
        <div style={{ flex: "2 1 580px", minWidth: 0 }}><DoNext data={view} /></div>
        <div style={{ flex: "1 1 340px", minWidth: 0, display: "flex", flexDirection: "column", gap: 16 }}>
          <SdrCard data={view} />
          <Commitments data={view} />
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%,460px),1fr))", gap: 16 }}>
        <Leaderboard data={view} />
        <Pipeline data={view} />
      </div>

      <Sources data={view} />

      {open && <Drawer candidateId={open} onClose={() => setOpen(null)} />}
    </div>
  );
}

/* The sample as one of the three roles. Only the viewer changes: the server sends every seat
   the same blocks and scopes what is IN them, so a preview that hid whole sections by role
   would be previewing a design the API does not implement. */
function sampleAs(as) {
  const role = as === "sdr" ? "sdr" : as === "team_leader" ? "team_leader" : "owner";
  if (role === "owner") return SAMPLE;
  const seat = role === "sdr"
    ? { seat_id: "s3", name: "Cole Whittaker" }
    : { seat_id: "s1", name: "Jenna Ruiz" };
  return {
    ...SAMPLE,
    viewer: { ...seat, role, previewing: true, is_admin: false },
    // Source attribution is owners-only, and the server omits it rather than blanking it.
    sources: null,
  };
}

/* One candidate's detail, offline. Built from the sample list so the two cannot disagree about
   who Sunny Kaur is -- which is the failure mode of a second hand-written fixture. */
function sampleCandidate(id) {
  const row = (SAMPLE.path.rows || []).find((r) => r.candidate_id === id);
  const signing = (SAMPLE.goal.signings || []).find((r) => r.candidate_id === id);
  const seat = (SAMPLE.leaderboard || []).find((l) => l.seat_id === row?.owner_seat_id);
  if (!row && !signing) return null;
  return {
    candidate_id: id,
    name: row?.name || signing?.name,
    brokerage: row?.brokerage || null,
    city: row ? "Salt Lake City" : null,
    source: "Sphere of influence",
    gci_ttm: 241000,
    stage: row?.stage || "Signed",
    stage_id: "st_offer",
    status: "open",
    days_in_stage: row?.days ?? null,
    owner: seat ? { seat_id: seat.seat_id, name: seat.name, first: seat.first, initials: seat.initials,
                    role: "team_leader", title: seat.title } : null,
    booker: { seat_id: "s3", name: "Cole Whittaker", first: "Cole", initials: "CW", role: "sdr", title: "SDR" },
    dnd: {},
    source_url: null,
    next_step: null,
    timeline: [
      { kind: "stage_move", at: new Date(Date.now() - 7 * 864e5).toISOString(),
        summary: `Moved to ${row?.stage || "Signed"}`, seat_id: row?.owner_seat_id || null, source: "sync" },
      { kind: "note", at: new Date(Date.now() - 9 * 864e5).toISOString(),
        summary: "Asked about the cap", seat_id: null, source: "ghl_poll" },
    ],
    unavailable: { next_step: "Dials, conversations and replies arrive with the conversation poll (Phase 6).",
                   actions: "Write-back ships in Phase 4." },
  };
}

/* The offline payload, field for field with the live one — including the nulls and their
   reasons, which are most of Phase 1. The Integrations lesson: a preview built from a
   convenient subset renders fine while production white-screens on the key it left out. */
const SAMPLE = {
  as_of: new Date().toISOString(),
  connection: { state: "ready", synced_at: new Date().toISOString(), sync_failed: false, error: null,
                reason: null, writeback: { open: false, reason: "Write-back ships in Phase 4." } },
  viewer: { seat_id: "s1", role: "owner", name: "Owner", previewing: false, is_admin: true },
  period: { key: "2026-09", label: "September", days_left: 7, elapsed: 0.77 },
  seats: [],
  goal: { scope: "team", signed: 5, goal: null, pace: null, pace_pct: null, verdict: null, need: null,
          split: null,
          signings: [
            { candidate_id: "c1", name: "Kara Whitfield", initials: "KW", occurred_on: "2026-09-04", seat_id: "s1" },
            { candidate_id: "c2", name: "Devon Pierce", initials: "DP", occurred_on: "2026-09-09", seat_id: "s1" },
            { candidate_id: "c3", name: "Alina Ross", initials: "AR", occurred_on: "2026-09-12", seat_id: "s2" },
            { candidate_id: "c4", name: "Ben Ortiz", initials: "BO", occurred_on: "2026-09-18", seat_id: "s2" },
            { candidate_id: "c5", name: "Sam Ndiaye", initials: "SN", occurred_on: "2026-09-22", seat_id: "s1" }] },
  path: { line: "2 offers out · 1 met and deciding. 5 signed so far this month.",
          rows: [
            { candidate_id: "c6", name: "Sunny Kaur", stage: "Offer out", days: 7, brokerage: "Summit Ridge Group",
              owner_initials: "MB", owner_seat_id: "s2", status: { label: null, tone: "mute" } },
            { candidate_id: "c7", name: "Trey Molina", stage: "Offer out", days: 3, brokerage: "Canyon & Co",
              owner_initials: "JR", owner_seat_id: "s1", status: { label: null, tone: "mute" } },
            { candidate_id: "c8", name: "Priya Raman", stage: "Met", days: 11, brokerage: "Lakeline Realty",
              owner_initials: "MB", owner_seat_id: "s2", status: { label: null, tone: "mute" } }] },
  sdr: { seat_id: "s3", name: "Cole Whittaker", first: "Cole", initials: "CW", role: "sdr", title: "SDR",
         month: { booked: 17, held: 12, show_rate: 71, goal: null, pace: null, show_goal: null, verdict: null },
         week: null, speed_to_lead: null,
         by_calendar: [
           { seat_id: "s1", name: "Jenna Ruiz", first: "Jenna", initials: "JR", role: "team_leader", title: null,
             cells: ["held", "held", "up", "open", "open", "open"], held: 2, booked: 3 },
           { seat_id: "s2", name: "Marcus Bell", first: "Marcus", initials: "MB", role: "team_leader", title: null,
             cells: ["held", "noshow", "up", "up", "open", "open"], held: 1, booked: 4 }] },
  calendars: null,
  queue: { counts: { total: 0, cleared: 0, by_seat: {} }, items: [], cleared: [] },
  commitments: null,
  leaderboard: [
    { seat_id: "s1", name: "Jenna Ruiz", first: "Jenna", initials: "JR", role: "team_leader",
      title: "Team Leader · Draper", rank: 1, signed: 3, held_mtd: 8, goal: null, pace: null,
      pace_pct: null, close_rate_90d: null, queue: null },
    { seat_id: "s2", name: "Marcus Bell", first: "Marcus", initials: "MB", role: "team_leader",
      title: "Team Leader · Sandy", rank: 2, signed: 2, held_mtd: 6, goal: null, pace: null,
      pace_pct: null, close_rate_90d: null, queue: null }],
  pipeline: { active: 22, nurture: 3, unmapped: 0, stages: [
    { label: "Sourced", owner_role: "sdr", n: 6, gci: 985000, stuck: 1, conv_90d: null },
    { label: "Appointment set", owner_role: "sdr", n: 5, gci: 740000, stuck: 0, conv_90d: null },
    { label: "Met", owner_role: "team_leader", n: 6, gci: 1120000, stuck: 2, conv_90d: null },
    { label: "Offer out", owner_role: "team_leader", n: 2, gci: 430000, stuck: 0, conv_90d: null },
    { label: "Signed", owner_role: "team_leader", n: 3, gci: 610000, stuck: 0, conv_90d: null }] },
  sources: [
    { name: "Sphere of influence", candidates: 14, signed: 3, rate: 21, cost_each: null, gci: 410000 },
    { name: "Referral", candidates: 6, signed: 2, rate: 33, cost_each: null, gci: 200000 },
    { name: "Unattributed", candidates: 5, signed: 0, rate: 0, cost_each: null, gci: 0 }],
  unavailable: {
    queue: "The Do next queue arrives with the rule engine (Phase 3).",
    commitments: "Weekly commitments arrive with accountability (Phase 5).",
    goal: "Monthly goals are set in Settings › Recruiting › Goals (Phase 5). Signings are counted already.",
    calendars: "Open slots are read live from GHL when booking ships (Phase 4b).",
    sdr_week: "Weekly commitments arrive with accountability (Phase 5).",
    speed_to_lead: "Dials, conversations and replies arrive with the conversation poll (Phase 6).",
  },
};
