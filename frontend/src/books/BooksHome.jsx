/* Books · Home — the pipeline rail, four click-through tiles, and Claude's monthly review.
   Receives the home hook result from Books.jsx (shared with the sub-nav badges). */
import { useState } from "react";
import { relativeTime } from "../theme.js";
import { Card, Eyebrow, Pill, StatePanel, EntityChip, DARK, font, usd, T } from "./ui.jsx";

const CLOSE_STEPS = [["bank_rec", "Bank rec"], ["card_rec", "Card rec"], ["intercompany", "Intercompany"],
  ["accruals", "Accruals"], ["statements", "Statements"]];

function PipelineRail({ rail }) {
  const stages = [
    { label: "Captured", sub: "bank & card feeds", v: rail.captured, tone: "auto" },
    { label: "Auto-categorized", sub: "came in categorized", v: rail.auto_categorized, tone: "auto" },
    { label: "Cleared", sub: "rules + Claude, no flag", v: rail.cleared, tone: "auto" },
    { label: "Needs approval", sub: "bookkeeper decides", v: rail.needs_approval, tone: "human" },
    { label: "Escalated", sub: "CFO decides", v: rail.escalated, tone: "esc" },
  ];
  return (
    // The corner glow this used to append to DARK.backgroundImage is gone. It gave a flat
    // pinstriped band some depth, and the band now carries real artwork -- a ribbed gradient or
    // Acumyn's bokeh -- which is the depth it was imitating. Two texture systems on one panel
    // fight, and the glow held the last two raw hex values in this file.
    <div style={{ position: "relative", overflow: "hidden", borderRadius: 16, padding: "24px 26px",
      ...DARK }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", flexWrap: "wrap", gap: 8 }}>
        <Eyebrow onDark>How every number travels</Eyebrow>
        <span style={{ fontFamily: font.body, fontSize: 11.5, color: T.onDarkMute }}>
          software captures and proposes · people approve and own</span>
      </div>
      <div style={{ display: "flex", gap: 10, marginTop: 18, flexWrap: "wrap" }}>
        {stages.map((s, i) => {
          const human = s.tone !== "auto", esc = s.tone === "esc";
          return (
            <div key={i} style={{ display: "flex", alignItems: "center", gap: 10, flex: "1 1 150px" }}>
              <div style={{ flex: 1, borderRadius: 10, padding: "12px 14px",
                background: human ? "rgba(255,221,31,0.10)" : "rgba(248,245,242,0.07)",
                border: `1px solid ${human ? "rgba(255,221,31,0.28)" : "rgba(248,245,242,0.12)"}` }}>
                <div style={{ fontFamily: font.head, fontSize: 24, fontWeight: 700, lineHeight: 1,
                  fontVariantNumeric: "tabular-nums", color: esc ? T.poppy : human ? T.daffodil : T.onDark }}>
                  {(s.v ?? 0).toLocaleString()}</div>
                <div style={{ fontFamily: font.body, fontSize: 11.5, fontWeight: 600, color: T.onDark, marginTop: 7 }}>{s.label}</div>
                <div style={{ fontFamily: font.body, fontSize: 10.5, color: T.onDarkMute, marginTop: 2 }}>{s.sub}</div>
              </div>
              {i < stages.length - 1 && <span style={{ color: T.onDarkMute, fontSize: 14, flexShrink: 0 }}>→</span>}
            </div>
          );
        })}
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 14 }}>
        <span style={{ width: 8, height: 8, borderRadius: 99, background: T.daffodil }} />
        <span style={{ fontFamily: font.body, fontSize: 11.5, color: T.onDarkMute }}>
          human checkpoint — nothing Claude proposes posts to QuickBooks without approval here</span>
      </div>
    </div>
  );
}

function Tile({ eyebrow, cta, hero, heroColor, sub, salient, onGo }) {
  return (
    <Card onClick={onGo} className="cc-card" style={{ cursor: "pointer", padding: "18px 20px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <Eyebrow>{eyebrow}</Eyebrow>
        <span style={{ fontFamily: font.body, fontSize: 11.5, fontWeight: 700, color: T.meadowInk }}>{cta} →</span>
      </div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginTop: 13 }}>
        <span style={{ fontFamily: font.head, fontSize: 30, fontWeight: 700, color: heroColor || T.ink,
          lineHeight: 1, fontVariantNumeric: "tabular-nums" }}>{hero}</span>
        {sub && <span style={{ fontFamily: font.body, fontSize: 11.5, color: T.muted }}>{sub}</span>}
      </div>
      {salient && (
        <div style={{ fontFamily: font.body, fontSize: 12, color: T.secondary, marginTop: 10, lineHeight: 1.5,
          borderTop: `1px solid ${T.line}`, paddingTop: 10 }}>{salient}</div>
      )}
    </Card>
  );
}

function CloseTile({ close }) {
  return (
    <Card style={{ padding: "18px 20px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
        <Eyebrow>Month-end close</Eyebrow>
        <span style={{ fontFamily: font.body, fontSize: 11.5, color: T.muted }}>+ 12 holdings</span>
      </div>
      {(close || []).map((c, i) => {
        const done = CLOSE_STEPS.filter(([k]) => (c.steps || {})[k]).length === CLOSE_STEPS.length;
        return (
          <div key={c.business} style={{ padding: "8px 0", borderTop: i ? `1px solid ${T.line}` : "none" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
              <EntityChip k={c.business} />
              <span style={{ fontFamily: font.body, fontSize: 10.5, fontWeight: 600,
                color: c.status === "closed" ? T.meadowInk : T.slate }}>
                {c.status === "closed" ? (c.note || "Closed") : "In progress"}</span>
            </div>
            <div style={{ display: "flex", gap: 4 }}>
              {CLOSE_STEPS.map(([k, label]) => (
                <div key={k} title={label} style={{ flex: 1, height: 6, borderRadius: 4,
                  background: (c.steps || {})[k] ? T.meadow : T.line }} />
              ))}
            </div>
          </div>
        );
      })}
    </Card>
  );
}

function MonthlyReview({ review, isCFO }) {
  const [signed, setSigned] = useState(review?.status === "signed");
  if (!review) return null;
  const done = signed || review.status === "signed";
  return (
    <div style={{ borderRadius: 14, padding: 22, ...DARK }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 10 }}>
        <Eyebrow onDark>Claude's monthly review</Eyebrow>
        <span style={{ fontFamily: font.body, fontSize: 11, fontWeight: 700,
          color: done ? T.sprout : T.daffodilText, background: done ? "rgba(184,204,184,0.16)" : T.daffodil,
          borderRadius: 6, padding: "3px 10px" }}>
          {done ? "Signed off" : "Draft · awaiting CFO sign-off"}</span>
      </div>
      <p style={{ fontFamily: font.body, fontSize: 13.5, color: T.onDark, lineHeight: 1.65, margin: "14px 0 4px", maxWidth: 720 }}>
        {review.body}</p>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 14, flexWrap: "wrap", gap: 10 }}>
        <span style={{ fontFamily: font.body, fontSize: 11, color: T.onDarkMute }}>
          Drafted from the closed books · Claude never posts to the official record</span>
        {isCFO && !done && (
          <button onClick={() => setSigned(true)} style={{ fontFamily: font.head, fontSize: 12.5, fontWeight: 600,
            color: T.evergreen, background: T.parchment, border: "none", borderRadius: 8, padding: "9px 18px", cursor: "pointer" }}>
            Review & sign off</button>
        )}
      </div>
    </div>
  );
}

export default function BooksHome({ home, go, isCFO }) {
  const { data, loading, error, retry } = home;
  return (
    <StatePanel loading={loading} error={error} retry={retry}
      empty={data && data.rail && data.rail.captured === 0}
      emptyTitle="No transactions synced yet"
      emptyMsg="Books lights up once QuickBooks is connected in Settings.">
      {data && (
        <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
          <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
            <span style={{ fontFamily: font.body, fontSize: 12.5, color: T.muted }}>
              {data.entities?.active} active {data.entities?.active === 1 ? "entity" : "entities"} · {data.entities?.holdings} holdings
              {data.synced_at && <> · synced {relativeTime(data.synced_at)}</>}
            </span>
            {data.invariants_ok !== undefined && (
              <Pill tone={data.invariants_ok ? "good" : "bad"}>{data.invariants_ok ? "Books tie out" : "Tie-out needs attention"}</Pill>
            )}
          </div>

          <PipelineRail rail={data.rail || {}} />

          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: 18 }}>
            <Tile eyebrow="Booked P&L" cta="View P&L"
              hero={usd(data.tiles?.pl?.noi || 0)}
              sub={`NOI · ${data.tiles?.pl?.margin || 0}% margin`}
              salient={data.tiles?.pl?.note || `Revenue ${usd(data.tiles?.pl?.revenue || 0)} this period.`}
              onGo={() => go("pl")} />
            <Tile eyebrow="Approval queue" cta="Open queue"
              hero={data.tiles?.queue?.count || 0}
              heroColor={data.tiles?.queue?.count ? T.daffodilText : T.meadowInk}
              sub={data.tiles?.queue?.count ? "awaiting the bookkeeper" : "queue clear"}
              salient={data.tiles?.queue?.oldest_label
                ? `Oldest is ${data.tiles.queue.oldest_days} days old (${data.tiles.queue.oldest_label}).`
                : "Everything this week is approved and posted."}
              onGo={() => go("queue")} />
            <Tile eyebrow="Intercompany" cta="View tie-out"
              hero={data.tiles?.ic?.open || 0}
              heroColor={data.tiles?.ic?.blocking ? T.poppyText : T.meadowInk}
              sub={data.tiles?.ic?.blocking ? "open items block the close" : "nets to $0"}
              salient={data.tiles?.ic?.note || "Every due-to has a matching due-from."}
              onGo={() => go("ic")} />
            <CloseTile close={data.tiles?.close} />
          </div>

          <MonthlyReview review={data.review} isCFO={isCFO} />
        </div>
      )}
    </StatePanel>
  );
}
