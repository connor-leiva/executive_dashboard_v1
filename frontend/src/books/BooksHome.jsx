/* Books · Home — the pipeline rail, the four tiles, and Claude's monthly review. */
import { T, relativeTime } from "../theme.js";
import { useBooksHome } from "./useBooks.js";
import { Card, Eyebrow, Pill, StatePanel, ent, font, usd } from "./ui.jsx";

const RAIL = [
  { k: "captured", label: "Captured", color: T.secondary },
  { k: "auto_categorized", label: "Auto-categorized", color: T.meadow },
  { k: "cleared", label: "Cleared", color: T.meadow },
  { k: "needs_approval", label: "Needs approval", color: T.daffodilText },
  { k: "escalated", label: "Escalated", color: T.poppyText },
];

const CLOSE_STEPS = [
  ["bank_rec", "Bank rec"], ["card_rec", "Card rec"], ["intercompany", "Intercompany"],
  ["accruals", "Accruals"], ["statements", "Statements"],
];

function PipelineRail({ rail }) {
  return (
    <Card style={{ padding: "18px 22px" }}>
      <Eyebrow>This month's pipeline</Eyebrow>
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 6 }}>
        {RAIL.map((st, i) => (
          <div key={st.k} style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-start",
                          padding: "6px 12px", borderRadius: 10, background: T.parchment,
                          border: `1px solid ${T.line}`, minWidth: 92 }}>
              <span style={{ fontFamily: font.head, fontSize: 20, fontWeight: 700, color: st.color }}>
                {(rail[st.k] ?? 0).toLocaleString()}</span>
              <span style={{ fontFamily: font.body, fontSize: 11, color: T.muted }}>{st.label}</span>
            </div>
            {i < RAIL.length - 1 && <span style={{ color: T.sprout, fontSize: 14 }}>→</span>}
          </div>
        ))}
      </div>
    </Card>
  );
}

function Tile({ eyebrow, children, footer }) {
  return (
    <Card style={{ display: "flex", flexDirection: "column", gap: 8, minHeight: 132 }}>
      <Eyebrow>{eyebrow}</Eyebrow>
      <div style={{ flex: 1 }}>{children}</div>
      {footer && <div style={{ fontFamily: font.body, fontSize: 12, color: T.muted }}>{footer}</div>}
    </Card>
  );
}

function Big({ children, color }) {
  return <div style={{ fontFamily: font.head, fontSize: 27, fontWeight: 700, color: color || T.ink, lineHeight: 1.1 }}>{children}</div>;
}

function CloseTile({ close }) {
  return (
    <Tile eyebrow="Month-end close">
      <div style={{ display: "grid", gap: 10 }}>
        {(close || []).map((c) => {
          const done = CLOSE_STEPS.filter(([k]) => (c.steps || {})[k]).length;
          return (
            <div key={c.business} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10 }}>
              <span style={{ display: "inline-flex", alignItems: "center", gap: 6, fontFamily: font.body, fontSize: 12.5, fontWeight: 600, color: T.secondary }}>
                <span style={{ width: 7, height: 7, borderRadius: 9, background: ent(c.business).dot }} />
                {ent(c.business).label}
              </span>
              <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <span style={{ display: "flex", gap: 3 }}>
                  {CLOSE_STEPS.map(([k]) => (
                    <span key={k} style={{ width: 8, height: 8, borderRadius: 9,
                      background: (c.steps || {})[k] ? T.meadow : T.line }} />
                  ))}
                </span>
                <Pill tone={c.status === "closed" ? "good" : "muted"}>
                  {c.status === "closed" ? (c.note || "Closed") : `${done}/5`}</Pill>
              </span>
            </div>
          );
        })}
      </div>
    </Tile>
  );
}

function MonthlyReview({ review }) {
  if (!review) return null;
  return (
    <Card>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}>
        <Eyebrow>Claude's monthly review</Eyebrow>
        <Pill tone={review.status === "signed" ? "good" : "warn"}>
          {review.status === "signed" ? "Signed off" : "Draft — awaiting sign-off"}</Pill>
      </div>
      <p style={{ fontFamily: font.body, fontSize: 14, lineHeight: 1.65, color: T.secondary, margin: 0 }}>
        {review.body}</p>
      <div style={{ fontFamily: font.body, fontSize: 11.5, color: T.muted, marginTop: 12 }}>
        Drafted from the period's books. A person signs off before it's official.</div>
    </Card>
  );
}

export default function BooksHome({ period = "mtd" }) {
  const { data, loading, error, retry } = useBooksHome(period);
  return (
    <StatePanel loading={loading} error={error} retry={retry}
      empty={data && data.rail && data.rail.captured === 0}
      emptyTitle="No transactions synced yet"
      emptyMsg="Books lights up once QuickBooks is connected in Settings.">
      {data && (
        <div style={{ display: "grid", gap: 16 }}>
          <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
            <div style={{ fontFamily: font.body, fontSize: 12.5, color: T.muted }}>
              {data.entities?.active} active {data.entities?.active === 1 ? "entity" : "entities"} · {data.entities?.holdings} holdings
              {data.synced_at && <> · synced {relativeTime(data.synced_at)}</>}
            </div>
            {data.invariants_ok !== undefined && (
              <Pill tone={data.invariants_ok ? "good" : "bad"}>
                {data.invariants_ok ? "Books tie out" : "Tie-out needs attention"}</Pill>
            )}
          </div>

          <PipelineRail rail={data.rail || {}} />

          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(230px, 1fr))", gap: 16 }}>
            <Tile eyebrow="Booked P&L" footer={data.tiles?.pl?.note}>
              <Big>{usd(data.tiles?.pl?.noi || 0)}</Big>
              <div style={{ fontFamily: font.body, fontSize: 12.5, color: T.muted, marginTop: 4 }}>
                net operating income · {data.tiles?.pl?.margin || 0}% margin</div>
            </Tile>

            <Tile eyebrow="Approval queue"
              footer={data.tiles?.queue?.oldest_label
                ? `Oldest: ${data.tiles.queue.oldest_label} (${data.tiles.queue.oldest_days}d)` : "Nothing waiting"}>
              <Big color={data.tiles?.queue?.count ? T.daffodilText : T.meadowInk}>
                {data.tiles?.queue?.count || 0}</Big>
              <div style={{ fontFamily: font.body, fontSize: 12.5, color: T.muted, marginTop: 4 }}>
                awaiting a decision</div>
            </Tile>

            <Tile eyebrow="Intercompany"
              footer={data.tiles?.ic?.note}>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <Big color={data.tiles?.ic?.blocking ? T.poppyText : T.meadowInk}>
                  {data.tiles?.ic?.open || 0}</Big>
                {data.tiles?.ic?.blocking && <Pill tone="bad">Blocks close</Pill>}
              </div>
              <div style={{ fontFamily: font.body, fontSize: 12.5, color: T.muted, marginTop: 4 }}>
                open {data.tiles?.ic?.open === 1 ? "item" : "items"}</div>
            </Tile>

            <CloseTile close={data.tiles?.close} />
          </div>

          <MonthlyReview review={data.review} />
        </div>
      )}
    </StatePanel>
  );
}
