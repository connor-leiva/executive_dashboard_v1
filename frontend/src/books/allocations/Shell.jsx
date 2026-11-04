/* The page shell: the dark bar, the month, the tabs and the needs-you strip (UI spec 4, 5, 9.2).
 *
 * The page had no visible month. It took the dashboard's `period`, which defaults to month to
 * date, so on 4 November every figure covered four days and 39 of 46 rules read as doing
 * nothing (U9). The month lives here now, says itself in words, and every view takes it.
 *
 * The strip answers the page's first question -- what needs me this month -- before a reader
 * has chosen a view, and each item is a link to the view that fixes it.
 */
import { LEDGER as L, LEDGER_FONT as F } from "../../theme.js";
import { Select, Tabs } from "./controls.jsx";
import Money from "./Money.jsx";
import { fmtMonth } from "./words.js";

const EYEBROW = { fontFamily: F.num, fontSize: 10, letterSpacing: ".14em",
                  textTransform: "uppercase" };

/** The dark band: which entities carry shared cost, and which month is on screen. */
export function DarkBar({ businesses, entity, onEntity, month, months, onMonth }) {
  const chips = [["", "All entities"], ...businesses.map((b) => [b.id, b.name])];
  return (
    <div style={{ background: L.deep, padding: "11px clamp(14px,3vw,26px)",
                  display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
      <div style={{ display: "flex", gap: 7, flexWrap: "wrap", flex: "1 1 auto",
                    minWidth: 0 }}>
        {chips.map(([id, name]) => {
          const on = entity === id;
          return (
            <button key={id || "all"} onClick={() => onEntity(id)} className="al-ctl"
                    aria-pressed={on}
                    style={{ fontFamily: F.body, fontSize: 13, height: 28, padding: "0 13px",
                             borderRadius: 999, cursor: "pointer",
                             fontWeight: on ? 600 : 500,
                             background: on ? L.paper : "transparent",
                             border: `1px solid ${on ? L.paper : L.onDeepPillEdge}`,
                             color: on ? L.deep : L.onDeepPill }}>
              {name}
            </button>
          );
        })}
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flex: "0 0 auto" }}>
        <span style={{ ...EYEBROW, color: L.onDeepEyebrow }}>Month</span>
        <Select tone="dark" ariaLabel="Month" value={month} onChange={onMonth}
                options={[...months.map((m) => [m, fmtMonth(m)]), ["ytd", "Year to date"]]} />
      </div>
    </div>
  );
}

export function ViewTabs({ view, setView, counts }) {
  return (
    <Tabs value={view} onChange={setView} items={[
      { key: "rules", label: "Rules" },
      { key: "exceptions", label: "Exceptions", count: counts.exceptions },
      { key: "drivers", label: "Drivers", count: counts.drivers },
      { key: "recon", label: "Reconciliation", count: counts.recon },
      { key: "partner", label: "Partner" },
    ]} />
  );
}

function Item({ onClick, label, amount }) {
  return (
    <button onClick={onClick} className="al-ctl al-strip-item"
            style={{ display: "inline-flex", alignItems: "center", gap: 7, cursor: "pointer",
                     background: "transparent", border: "none", padding: 0 }}>
      <span aria-hidden style={{ width: 7, height: 7, borderRadius: "50%", flex: "0 0 auto",
                                 border: `2px solid ${L.flagRing}` }} />
      <span style={{ fontFamily: F.body, fontSize: 12.5, color: L.body }}>{label}</span>
      {amount != null && <Money v={amount} size={12.5} weight={600} tone={L.deep} />}
    </button>
  );
}

/** "Needs you in November: 9 unclaimed charges 4,512.30 . 2 rules not resolved ..." (4).
 *
 * An item with nothing in it is omitted rather than shown as a zero: a strip of zeros is a
 * strip a reader learns to skip. With all three empty it says so in one line, which is a
 * different and better answer than an empty bar.
 */
export function NeedsYou({ summary, month, go }) {
  if (!summary) return null;
  const label = month === "ytd" ? "this year" : `in ${fmtMonth(month)}`;
  const items = [];
  if (summary.unclaimed_count) {
    items.push(
      <Item key="ex" onClick={() => go("exceptions")}
            label={`${summary.unclaimed_count} unclaimed charge${
              summary.unclaimed_count === 1 ? "" : "s"}`}
            amount={summary.unclaimed_total} />);
  }
  if (summary.pending_rules) {
    items.push(
      <Item key="re" onClick={() => go("recon")}
            label={`${summary.pending_rules} rule${
              summary.pending_rules === 1 ? "" : "s"} not resolved`}
            amount={summary.pending_amount} />);
  }
  for (const d of summary.drivers || []) {
    if (!d.committed) {
      items.push(
        <Item key={d.id} onClick={() => go("drivers", d.id)}
              label={`${d.label} not committed`} />);
    }
  }
  return (
    <div style={{ background: L.totalBand, borderBottom: `1px solid ${L.ruleHeader}`,
                  padding: "9px clamp(14px,3vw,26px)", display: "flex", alignItems: "center",
                  gap: "8px 18px", flexWrap: "wrap" }}>
      <span style={{ ...EYEBROW, color: L.flagInk }}>
        {items.length ? `Needs you ${label}` : `Nothing needs you ${label}`}
      </span>
      {items}
    </div>
  );
}
