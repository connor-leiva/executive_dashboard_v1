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
import { postJSON, putJSON } from "../api.js";
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

/* A list that is allowed to be long, held to a fixed height and scrolled inside itself.
 *
 * Every list on this tab is a slice of a real pipeline, and a real pipeline does not agree to be
 * short. ULRG connected with 1,654 candidates already in it, and the counts are not close to the
 * ones the sample was drawn against: 1,518 candidates past the first meeting in "Path to goal",
 * 96 distinct lead sources, and 3,209 rows in "Cleared today" on the day the rules first
 * reconciled. The page became metres long, which pushes every section BELOW a long one off the
 * bottom of the world -- a tab you cannot scroll past is a tab whose later sections do not exist.
 *
 * The count in the corner is the point, not decoration. Once a list scrolls, its length stops
 * being visible -- and "how many are there" is the first thing anybody asks of a backlog.
 */
function Scroller({ children, max = 360, count, noun, style }) {
  return (
    <>
      {count > 0 && (
        <div style={{ fontFamily: FM, fontSize: 10.5, color: C.muted, textAlign: "right",
                      marginBottom: 4 }}>
          {count} {noun}{count === 1 ? "" : "s"}
        </div>
      )}
      {/* paddingRight keeps the rows off the scrollbar rather than under it. */}
      <div style={{ maxHeight: max, overflowY: "auto", paddingRight: 4, ...style }}>
        {children}
      </div>
    </>
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
            <Scroller max={320} count={(path?.rows || []).length} noun="candidate">
              {(path?.rows || []).map((r) => <PathRow key={r.candidate_id} r={r} />)}
            </Scroller>
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

function DoNext({ data, onChanged }) {
  const { queue, viewer, unavailable, seats } = data;
  const [filter, setFilter] = useState("all");
  const [busy, setBusy] = useState(null);
  const owner = viewer.role === "owner";
  const all = queue?.items || [];
  const items = filter === "all" ? all : all.filter((i) => i.owner?.seat_id === filter);
  const cleared = queue?.cleared || [];
  const total = all.length + cleared.length;
  const donePct = total ? Math.round((cleared.length / total) * 100) : 0;

  async function act(item, verb) {
    if (!API_LIVE) return;                 // the offline sample has nothing to write to
    setBusy(item.id);
    try { await postJSON(`/ulrg/recruiting/queue/${item.id}/${verb}`, {}); onChanged(); }
    finally { setBusy(null); }
  }

  return (
    <Card>
      <SectionTitle right={total ? (
        <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
          <span style={{ width: 110, height: 6, borderRadius: 99, background: C.hairSoft, overflow: "hidden" }}>
            <span style={{ display: "block", width: `${donePct}%`, height: "100%", background: C.meadow }} />
          </span>
          <span style={{ fontFamily: FM, fontSize: 12, color: C.slate, whiteSpace: "nowrap" }}>
            {cleared.length} of {total} cleared
          </span>
        </span>) : null}>
        {owner ? "Do next" : "Your list today"}
      </SectionTitle>
      <div style={{ fontFamily: FB, fontSize: 12.5, color: C.slate, marginTop: -6, marginBottom: 14, lineHeight: 1.5 }}>
        Built each morning from the rules in Settings. Sending from here is logged to GHL from
        Phase 4; today Done clears it on this list only.
      </div>

      {owner && all.length > 0 && (
        <div style={{ display: "flex", gap: 7, flexWrap: "wrap", marginBottom: 12 }}>
          <FilterChip on={filter === "all"} onClick={() => setFilter("all")}>Everyone {all.length}</FilterChip>
          {(seats || []).map((seat) => {
            const n = all.filter((i) => i.owner?.seat_id === seat.seat_id).length;
            if (!n) return null;
            return <FilterChip key={seat.seat_id} on={filter === seat.seat_id}
                               onClick={() => setFilter(seat.seat_id)}>{seat.first} {n}</FilterChip>;
          })}
        </div>
      )}

      {items.length === 0 ? (
        all.length === 0
          ? <NotYet>{unavailable?.queue}</NotYet>
          : <div style={{ fontFamily: FB, fontSize: 12.5, color: C.muted }}>Nothing on that person&rsquo;s list.</div>
      ) : (
        <Scroller max={520} count={items.length} noun="item">
          {items.map((it) => (
            <QueueRow key={it.id} it={it} owner={owner} busy={busy === it.id}
                      onAct={(verb) => act(it, verb)} />
          ))}
        </Scroller>
      )}

      {cleared.length > 0 && (
        <div style={{ background: C.parchment, borderRadius: 9, marginTop: 14, padding: "10px 14px" }}>
          <div style={{ fontFamily: FM, fontSize: 9, letterSpacing: ".07em", textTransform: "uppercase",
                        color: C.muted, marginBottom: 6 }}>Cleared today</div>
          <Scroller max={220} count={cleared.length} noun="item">
            {cleared.map((row) => (
            <div key={row.id} style={{ display: "flex", alignItems: "center", gap: 8, padding: "4px 0" }}>
              <span style={{ color: C.meadowInk, fontFamily: FM, fontSize: 12 }}>✓</span>
              <span style={{ fontFamily: FB, fontSize: 12.5, color: C.body, minWidth: 0, overflow: "hidden",
                             textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{row.name}</span>
              <span style={{ fontFamily: FM, fontSize: 11, color: C.muted, whiteSpace: "nowrap" }}>· {row.how}</span>
              <span style={{ flex: 1 }} />
              {/* Only a person's Done can be undone. Putting back something the RULES cleared
                  would re-assert a claim the data says is false -- and it would come straight
                  back off the next tick. */}
              {row.undoable && (
                <button type="button" disabled={!API_LIVE} onClick={() => act(row, "undo")}
                        style={{ border: "none", background: "transparent", cursor: "pointer",
                                 fontFamily: FB, fontSize: 12, color: C.teal, padding: 0 }}>Undo</button>
              )}
              </div>
            ))}
          </Scroller>
        </div>
      )}
    </Card>
  );
}

function FilterChip({ on, onClick, children }) {
  return (
    <button type="button" onClick={onClick}
            style={{ fontFamily: FB, fontSize: 12, padding: "5px 10px", borderRadius: 99,
                     whiteSpace: "nowrap", cursor: "pointer",
                     background: on ? C.ink : C.surface, color: on ? C.onDark : C.body,
                     border: on ? "1px solid transparent" : `1px solid ${C.hair}` }}>{children}</button>
  );
}

function QueueRow({ it, owner, busy, onAct }) {
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
      <span style={{ display: "inline-flex", alignItems: "center", gap: 8, flexWrap: "wrap",
                     justifyContent: "flex-end" }}
            onClick={(e) => e.stopPropagation()}>
        {owner && it.owner && (
          <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            <Initials t={it.owner.initials} tone={it.owner.role === "sdr" ? "sdr" : "teal"} size={18} />
            <span style={{ fontFamily: FB, fontSize: 12, color: C.slate }}>{it.owner.first}</span>
          </span>
        )}
        {/* The primary action opens the drawer on the right mode. It cannot SEND yet -- the
            outbox is Phase 4 -- so the drawer offers the draft and a link into GHL. A button
            that looked like it sent and did not would be worse than no button. */}
        <button type="button" onClick={() => openDrawer(it.candidate.id)}
                style={{ fontFamily: FB, fontSize: 12.5, fontWeight: 600, padding: "7px 13px",
                         borderRadius: 7, border: "none", cursor: "pointer",
                         background: C.ink, color: C.onDark }}>
          {ACTION_LABEL[it.primary_action] || "Open"}
        </button>
        <button type="button" disabled={busy} onClick={() => onAct("snooze")}
                style={{ fontFamily: FB, fontSize: 12.5, padding: "7px 13px", borderRadius: 7,
                         border: `1px solid ${C.hair}`, background: C.surface, color: C.slate,
                         cursor: busy ? "default" : "pointer" }}>Tomorrow</button>
        <button type="button" disabled={busy} aria-label="Done" onClick={() => onAct("done")}
                style={{ width: 32, height: 32, borderRadius: 99, border: "none",
                         background: "transparent", color: C.meadowInk, fontSize: 15,
                         cursor: busy ? "default" : "pointer" }}>✓</button>
      </span>
    </div>
  );
}

const ACTION_LABEL = { text: "Text", call: "Call", email: "Email", book: "Book" };

/* ── 3. The SDR card ────────────────────────────────────────────────────────────────────── */

function SdrCard({ data, onChanged }) {
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
      {(sdr.week || []).map((row) => (
        <WeekLine key={row.key} row={row} seatId={sdr.seat_id}
                  editable={data.viewer.is_admin || data.viewer.seat_id === sdr.seat_id}
                  onChanged={onChanged} />
      ))}
      {sdr.speed_to_lead && (
        <div style={{ display: "flex", alignItems: "baseline", gap: 8, paddingTop: 10 }}>
          <span style={{ fontFamily: FB, fontSize: 12.5, color: C.body, flex: 1 }}>Speed to lead</span>
          <span style={{ fontFamily: FM, fontSize: 10.5, color: C.muted }}>median, this month</span>
          <span style={{ fontFamily: FM, fontSize: 13, fontWeight: 600,
                         color: sdr.speed_to_lead.median_minutes > (sdr.speed_to_lead.goal_minutes || 15)
                           ? C.poppyInk : C.meadowInk }}>
            {sdr.speed_to_lead.median_minutes} min
          </span>
        </div>
      )}
      {!sdr.speed_to_lead && unavailable?.speed_to_lead && (
        <div style={{ marginTop: 12 }}><NotYet>{unavailable.speed_to_lead}</NotYet></div>
      )}
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

/* −/+ on a weekly commitment. Writes straight through and re-fetches, rather than holding a
   local number: a commitment somebody else changed while you were looking at it should win, and
   optimistic UI on a shared target is how two people end up sure of different numbers. */
function Stepper({ value, onSet, disabled, step = 1 }) {
  const btn = {
    width: 16, height: 16, lineHeight: "14px", borderRadius: 4, border: `1px solid ${C.hair}`,
    background: C.surface, color: C.slate, fontFamily: FM, fontSize: 11, padding: 0,
    cursor: disabled ? "default" : "pointer",
  };
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
      <button type="button" style={btn} disabled={disabled || (value || 0) <= 0}
              onClick={() => onSet(Math.max(0, (value || 0) - step))} aria-label="Less">−</button>
      <span style={{ fontFamily: FM, fontSize: 12, color: C.slate, minWidth: 14, textAlign: "center" }}>
        {value ?? "–"}</span>
      <button type="button" style={btn} disabled={disabled}
              onClick={() => onSet((value || 0) + step)} aria-label="More">+</button>
    </span>
  );
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

function WeekLine({ row, seatId, editable, onChanged }) {
  const [busy, setBusy] = useState(false);
  async function set(value) {
    if (!API_LIVE) return;
    setBusy(true);
    try {
      await putJSON("/ulrg/recruiting/commitments",
                    { seat_id: seatId, metric: row.key, commit: value });
      onChanged();
    } finally { setBusy(false); }
  }
  return (
    <div style={{ padding: "9px 0", borderBottom: `1px solid ${C.hairSoft}` }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
        <span style={{ fontFamily: FB, fontSize: 12.5, color: C.body, minWidth: 0, flex: 1 }}>{row.label}</span>
        <span style={{ fontFamily: FM, fontSize: 10.5, color: C.muted, whiteSpace: "nowrap" }}>{row.when}</span>
        <span style={{ fontFamily: FM, fontSize: 13, fontWeight: 600, whiteSpace: "nowrap",
                       color: row.band_pct === null || row.band_pct === undefined
                         ? C.ink : (band(row.band_pct) || {}).ink }}>
          {row.actual}{row.commit ? ` / ${row.commit}` : ""}
        </span>
        {editable && <Stepper value={row.commit} onSet={set} disabled={busy || !API_LIVE}
                              step={row.key === "dials" ? 5 : 1} />}
      </div>
      <div style={{ marginTop: 6, height: 4 }}>
        {row.band_pct === null || row.band_pct === undefined
          ? <div style={{ height: 4, borderRadius: 99, background: C.hairSoft }} />
          : <Bar pct={row.band_pct} />}
      </div>
    </div>
  );
}

function Commitments({ data, onChanged }) {
  const c = data.commitments;
  if (!c) {
    return (
      <Card>
        <SectionTitle>Team Leader commitments</SectionTitle>
        <NotYet>{data.unavailable?.commitments}</NotYet>
      </Card>
    );
  }
  const weekLabel = new Date(`${c.week_start}T12:00:00`).toLocaleDateString(undefined,
    { month: "short", day: "numeric" });
  return (
    <Card>
      <SectionTitle meta={`Week of ${weekLabel} · day ${c.day} of ${c.of}`}>
        Team Leader commitments
      </SectionTitle>
      <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) 96px 96px", gap: 10,
                    fontFamily: FM, fontSize: 9, letterSpacing: ".07em", textTransform: "uppercase",
                    color: C.muted, borderBottom: `1px solid ${C.hair}`, paddingBottom: 6 }}>
        <span>Team Leader</span><span>Appts held</span><span>Offers sent</span>
      </div>
      {c.team_leaders.map((row) => (
        <div key={row.seat_id} style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) 96px 96px",
                                        gap: 10, alignItems: "center", padding: "10px 0",
                                        borderBottom: `1px solid ${C.hairSoft}` }}>
          <span style={{ minWidth: 0, fontFamily: FB, fontSize: 12.5, color: C.ink, overflow: "hidden",
                         textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {row.first}{data.viewer.seat_id === row.seat_id
              ? <b style={{ fontWeight: 600, color: C.slate }}> (you)</b> : ""}
          </span>
          {["held", "offers"].map((metric) => (
            <CommitCell key={metric} cell={row[metric]} metric={metric} seatId={row.seat_id}
                        editable={row.editable} onChanged={onChanged} />
          ))}
        </div>
      ))}
      <div style={{ background: C.parchment, borderRadius: 9, marginTop: 12, padding: "10px 13px",
                    fontFamily: FB, fontSize: 12, color: C.slate, lineHeight: 1.5 }}>
        <b style={{ fontWeight: 600, color: C.ink }}>
          {c.rollup.held} held, {c.rollup.booked} booked this week.</b>{" "}
        Both roll into the L10 Scorecard on Monday as {c.rollup.scorecard.join(" and ")}.
      </div>
    </Card>
  );
}

function CommitCell({ cell, metric, seatId, editable, onChanged }) {
  const [busy, setBusy] = useState(false);
  async function set(value) {
    if (!API_LIVE) return;
    setBusy(true);
    try {
      await putJSON("/ulrg/recruiting/commitments", { seat_id: seatId, metric, commit: value });
      onChanged();
    } finally { setBusy(false); }
  }
  return (
    <span style={{ minWidth: 0 }}>
      <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <span style={{ fontFamily: FM, fontSize: 12.5, fontWeight: 600, whiteSpace: "nowrap",
                       color: cell.band_pct === null || cell.band_pct === undefined
                         ? C.ink : (band(cell.band_pct) || {}).ink }}>
          {cell.actual}{cell.commit ? ` / ${cell.commit}` : ""}
        </span>
        {editable && <Stepper value={cell.commit} onSet={set} disabled={busy || !API_LIVE} />}
      </span>
      <span style={{ display: "block", marginTop: 5 }}>
        {cell.band_pct === null || cell.band_pct === undefined
          ? <span style={{ display: "block", height: 4, borderRadius: 99, background: C.hairSoft }} />
          : <Bar pct={cell.band_pct} w={72} />}
      </span>
    </span>
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
          <div style={{ minWidth: 560 }}>
            <div style={{ display: "grid", gridTemplateColumns: "22px minmax(150px,1fr) 128px 48px 64px 70px",
                          gap: 8, fontFamily: FM, fontSize: 9, letterSpacing: ".07em",
                          textTransform: "uppercase", color: C.muted,
                          borderBottom: `1px solid ${C.hair}`, paddingBottom: 6 }}>
              <span>#</span><span>Team Leader</span><span>Signed</span><span>Held</span><span>Close</span><span>Queue</span>
            </div>
            {rows.map((r) => (
              <div key={r.seat_id} style={{ display: "grid",
                     gridTemplateColumns: "22px minmax(150px,1fr) 128px 48px 64px 70px", gap: 8,
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
                <span style={{ fontFamily: FM, fontSize: 12.5, whiteSpace: "nowrap",
                               color: r.queue && r.queue.done >= r.queue.total ? C.meadowInk : C.body }}>
                  {r.queue ? `${r.queue.done} of ${r.queue.total}` : "–"}
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
      <SectionTitle meta={`${data.period.label} to date`}
                    right={rows.length > 1 && (
                      <span style={{ fontFamily: FM, fontSize: 10.5, color: C.muted }}>
                        {rows.length} sources
                      </span>)}>Where signings come from</SectionTitle>
      <div style={{ overflowX: "auto" }}>
        <div style={{ minWidth: 560 }}>
          <div style={{ display: "grid", gridTemplateColumns: "minmax(170px,1.6fr) repeat(4,minmax(70px,1fr))",
                        gap: 8, fontFamily: FM, fontSize: 9, letterSpacing: ".07em", textTransform: "uppercase",
                        color: C.muted, borderBottom: `1px solid ${C.hair}`, paddingBottom: 6 }}>
            <span>Source</span><span>Candidates</span><span>Signed</span><span>Rate</span><span>GCI added</span>
          </div>
          <Scroller max={380}>
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
          </Scroller>
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
  const { data, error, reload } = useRecruiting({ as: API_LIVE ? as : null });
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
        <div style={{ flex: "2 1 580px", minWidth: 0 }}><DoNext data={view} onChanged={reload} /></div>
        <div style={{ flex: "1 1 340px", minWidth: 0, display: "flex", flexDirection: "column", gap: 16 }}>
          <SdrCard data={view} onChanged={reload} />
          <Commitments data={view} onChanged={reload} />
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
  // The roster. Empty here meant the owner filter chips could not render offline, so the
  // preview showed a control that production has and it did not — the same subset-sample trap.
  seats: [
    { seat_id: "s1", name: "Jenna Ruiz", first: "Jenna", initials: "JR", role: "team_leader", title: "Team Leader · Draper" },
    { seat_id: "s2", name: "Marcus Bell", first: "Marcus", initials: "MB", role: "team_leader", title: "Team Leader · Sandy" },
    { seat_id: "s3", name: "Cole Whittaker", first: "Cole", initials: "CW", role: "sdr", title: "SDR" },
  ],
  goal: { scope: "team", signed: 5, goal: 9, pace: 6.5, pace_pct: 72, verdict: "stretch", need: 4,
          split: [{ seat_id: "s1", name: "Jenna Ruiz", first: "Jenna", goal: 3 },
                  { seat_id: "s2", name: "Marcus Bell", first: "Marcus", goal: 3 }],
          signings: [
            { candidate_id: "c1", name: "Kara Whitfield", initials: "KW", occurred_on: "2026-09-04", seat_id: "s1" },
            { candidate_id: "c2", name: "Devon Pierce", initials: "DP", occurred_on: "2026-09-09", seat_id: "s1" },
            { candidate_id: "c3", name: "Alina Ross", initials: "AR", occurred_on: "2026-09-12", seat_id: "s2" },
            { candidate_id: "c4", name: "Ben Ortiz", initials: "BO", occurred_on: "2026-09-18", seat_id: "s2" },
            { candidate_id: "c5", name: "Sam Ndiaye", initials: "SN", occurred_on: "2026-09-22", seat_id: "s1" }] },
  path: { line: "Closing every offer out gets you to 7. 2 more from Met gets you to 9.",
          rows: [
            { candidate_id: "c6", name: "Sunny Kaur", stage: "Offer out", days: 7, brokerage: "Summit Ridge Group",
              owner_initials: "MB", owner_seat_id: "s2", status: { label: null, tone: "mute" } },
            { candidate_id: "c7", name: "Trey Molina", stage: "Offer out", days: 3, brokerage: "Canyon & Co",
              owner_initials: "JR", owner_seat_id: "s1", status: { label: null, tone: "mute" } },
            { candidate_id: "c8", name: "Priya Raman", stage: "Met", days: 11, brokerage: "Lakeline Realty",
              owner_initials: "MB", owner_seat_id: "s2", status: { label: null, tone: "mute" } }] },
  sdr: { seat_id: "s3", name: "Cole Whittaker", first: "Cole", initials: "CW", role: "sdr", title: "SDR",
         month: { booked: 17, held: 12, show_rate: 71, goal: 24, pace: 22, show_goal: 75,
                  verdict: "stretch", need: 7 },
         week: [{ key: "booked", label: "Appointments booked", when: "this week", actual: 5, commit: 8, band_pct: 104 },
                { key: "held", label: "Appointments held", when: "this week", actual: 3, commit: 5, band_pct: 100 },
                { key: "dials", label: "Dials", when: "this week", actual: 42, commit: 60, band_pct: 117 },
                { key: "convos", label: "Conversations", when: "this week", actual: 9, commit: 15, band_pct: 100 }],
         speed_to_lead: { median_minutes: 112, n: 14, untouched: 3, goal_minutes: 15 },
         by_calendar: [
           { seat_id: "s1", name: "Jenna Ruiz", first: "Jenna", initials: "JR", role: "team_leader", title: null,
             cells: ["held", "held", "up", "open", "open", "open"], held: 2, booked: 3 },
           { seat_id: "s2", name: "Marcus Bell", first: "Marcus", initials: "MB", role: "team_leader", title: null,
             cells: ["held", "noshow", "up", "up", "open", "open"], held: 1, booked: 4 }] },
  calendars: null,
  queue: {
    counts: { total: 3, cleared: 1, by_seat: { s1: 1, s2: 1, s3: 1 } },
    items: [
      { id: "q1", rule: "offer_out_stale", rule_label: "Offer out, gone quiet",
        why: "Offer has been out 7 days. No reply since Thursday.",
        due: { label: "2 days late", tone: "late", at: new Date(Date.now() - 2 * 864e5).toISOString() },
        primary_action: "call",
        candidate: { id: "c6", name: "Sunny Kaur", stage: "Offer out", gci: 241000 },
        owner: { seat_id: "s2", name: "Marcus Bell", first: "Marcus", initials: "MB", role: "team_leader", title: null },
        draft: { text: "Hi Sunny — Marcus here. Wanted to check in on where things stand.",
                 email: { subject: "Following up, Sunny", body: "Hi Sunny,\n\nJust following up." } } },
      { id: "q2", rule: "appt_24h", rule_label: "Appointment tomorrow",
        why: "Meeting Thursday at 9:00 am and they have not confirmed.",
        due: { label: "Today", tone: "today", at: new Date().toISOString() },
        primary_action: "text",
        candidate: { id: "c7", name: "Trey Molina", stage: "Met", gci: 180000 },
        owner: { seat_id: "s1", name: "Jenna Ruiz", first: "Jenna", initials: "JR", role: "team_leader", title: null },
        draft: { text: "Hi Trey — Jenna here. Still good for Thursday?", email: { subject: "", body: "" } } },
      { id: "q3", rule: "new_lead_untouched", rule_label: "New lead, no contact",
        why: "Came in 4 hours and nobody has reached out yet.",
        due: { label: "Today", tone: "today", at: new Date().toISOString() },
        primary_action: "text",
        candidate: { id: "c9", name: "Dana Cole", stage: "Sourced", gci: 95000 },
        owner: { seat_id: "s3", name: "Cole Whittaker", first: "Cole", initials: "CW", role: "sdr", title: "SDR" },
        draft: { text: "Hi Dana — Cole here.", email: { subject: "", body: "" } } },
    ],
    cleared: [{ id: "q0", name: "Priya Raman", how: "Done",
                at: new Date().toISOString(), undoable: true }],
  },
  commitments: {
    week_start: "2026-09-21", day: 3, of: 5,
    team_leaders: [
      { seat_id: "s1", name: "Jenna Ruiz", first: "Jenna", initials: "JR", role: "team_leader",
        title: "Team Leader · Draper", editable: true,
        held: { actual: 3, commit: 4, band_pct: 125 }, offers: { actual: 1, commit: 2, band_pct: 83 } },
      { seat_id: "s2", name: "Marcus Bell", first: "Marcus", initials: "MB", role: "team_leader",
        title: "Team Leader · Sandy", editable: true,
        held: { actual: 2, commit: 4, band_pct: 83 }, offers: { actual: 2, commit: 2, band_pct: 167 } },
    ],
    rollup: { held: 5, booked: 5, scorecard: ["Recruiting Appts Met", "Recruiting Appts Booked"] },
  },
  leaderboard: [
    { seat_id: "s1", name: "Jenna Ruiz", first: "Jenna", initials: "JR", role: "team_leader",
      title: "Team Leader · Draper", rank: 1, signed: 3, held_mtd: 8, goal: 3, pace: 3.9,
      pace_pct: 130, close_rate_90d: 38, queue: { done: 1, total: 2 } },
    { seat_id: "s2", name: "Marcus Bell", first: "Marcus", initials: "MB", role: "team_leader",
      title: "Team Leader · Sandy", rank: 2, signed: 2, held_mtd: 6, goal: 3, pace: 2.6,
      pace_pct: 87, close_rate_90d: 22, queue: { done: 0, total: 2 } }],
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
    queue: "Nothing is due. Tomorrow's list builds at 6:00 am.",
    commitments: "Weekly commitments arrive with accountability (Phase 5).",
    goal: "Monthly goals are set in Settings › Recruiting › Goals (Phase 5). Signings are counted already.",
    calendars: "Open slots are read live from GHL when booking ships (Phase 4b).",
    sdr_week: "Weekly commitments arrive with accountability (Phase 5).",
    speed_to_lead: "Dials, conversations and replies arrive with the conversation poll (Phase 6).",
  },
};
