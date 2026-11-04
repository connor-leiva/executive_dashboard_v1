/* The driver month as a matrix: rows are subjects, columns are entities (UI spec 6.3).
 *
 * The form was one list of dropdown-and-input pairs -- every subject repeated all five
 * entities, each with a "share of the pool" select and a "0 to 1" box, so six subjects was
 * sixty controls with Commit somewhere below all of them (U14). Reading a split meant
 * scrolling; comparing two people meant remembering.
 *
 * Three other things it did not say, and now does:
 *   - PERCENTS. The card said "percent" and every box wanted a fraction. They are percents
 *     here and the server converts them (7.6). The browser never divides.
 *   - WHAT EACH ROW MOVES. A split for somebody half remembered is worth getting right in
 *     proportion to the dollars behind it, and the form showed none.
 *   - WHAT IS BLOCKING THE COMMIT. The button was simply disabled.
 *
 * The browser adds the typed percents for the row total. That is the one sum the parent
 * spec allows, and it is a sum of what somebody just typed, not of money.
 */
import { useMemo, useState } from "react";
import { LEDGER as L, LEDGER_FONT as F } from "../../theme.js";
import { postJSON } from "../../api";
import { Button, Input, Select } from "./controls.jsx";
import Money from "./Money.jsx";
import { fmtMonth } from "./words.js";

const EYEBROW = { fontFamily: F.num, fontSize: 10, letterSpacing: ".14em",
                  textTransform: "uppercase", color: L.faint };

/* A row is answered in ONE of two ways, chosen per subject:
     percent  - every cell is a percent and they come to 100
     mixed    - cells can be a percent, a flat amount, or "the rest"
   The words are the reader's. `value_kind` is the server's and stays on the wire. */
const MODES = [["percent", "Percent"], ["mixed", "Fixed and rest"]];
const UNITS = [["share", "%"], ["amount", "$"], ["remainder", "rest"]];

function modeOf(cellsForSubject) {
  return cellsForSubject.some((c) => c.kind === "amount" || c.kind === "remainder")
    ? "mixed" : "percent";
}

export default function DriverMatrix({ driver, month, businesses, subjects, prefill,
                                       isCFO, onCommitted }) {
  const split = driver.kind === "split";
  /* The rows come from the PAYLOAD, never from a list held here. That is what lets a
     subject appear or disappear as the rules change -- including the day payroll taxes
     stops being a typed row and becomes a derived rule (ALLOCATION-RULES-SPEC 10.1). */
  const rows = split
    ? (subjects || []).map((s) => (typeof s === "string" ? { name: s } : s))
    : [{ name: "" }];

  const [cells, setCells] = useState({});
  const [modes, setModes] = useState({});
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  const key = (subject, bid) => `${subject || ""}|${bid}`;
  const cell = (subject, bid) =>
    cells[key(subject, bid)] || { kind: split ? "share" : "count", value: "" };
  const set = (subject, bid, patch) =>
    setCells((c) => ({ ...c, [key(subject, bid)]: { ...cell(subject, bid), ...patch } }));

  const mode = (subject) => modes[subject] ?? modeOf(
    (businesses || []).map((b) => cell(subject, b.id)));
  const setMode = (subject, m) => {
    setModes((x) => ({ ...x, [subject]: m }));
    // Leaving Fixed and rest turns every cell back into a percent, so a flat amount cannot
    // survive invisibly under a row the header calls a percent row.
    if (m === "percent") {
      setCells((c) => {
        const next = { ...c };
        for (const b of businesses || []) {
          const k = key(subject, b.id);
          if (next[k] && next[k].kind !== "share") {
            next[k] = { kind: "share", value: next[k].kind === "remainder" ? "" : next[k].value };
          }
        }
        return next;
      });
    }
  };

  const filled = (c) => c.kind === "remainder" || String(c.value ?? "").trim() !== "";
  const liveCells = (subject) => (businesses || [])
    .map((b) => ({ b, c: cell(subject, b.id) })).filter(({ c }) => filled(c));

  /* The row total, for display only. Percents are added as typed; a row with a flat amount
     or a remainder is not supposed to reach 100, so it says where the rest goes instead. */
  const rowState = (subject) => {
    const live = liveCells(subject);
    if (!live.length) return { kind: "empty", label: "Not set" };
    const rest = live.find(({ c }) => c.kind === "remainder");
    if (rest) return { kind: "rest", label: `rest to ${rest.b.name}` };
    const pcts = live.filter(({ c }) => c.kind === "share");
    if (pcts.length !== live.length) {
      return { kind: "mixed", label: "Fixed amounts only" };
    }
    const total = pcts.reduce((t, { c }) => t + (Number(c.value) || 0), 0);
    const ok = Math.abs(total - 100) < 0.005;
    return { kind: ok ? "ok" : "off",
             label: `${Number(total.toFixed(2))}%`, total };
  };

  const payload = () => rows.flatMap((r) =>
    liveCells(r.name).map(({ b, c }) => ({
      subject: split ? r.name : null,
      business_id: b.id,
      value_kind: c.kind,
      // PERCENT ON THE WIRE for a share. `pct_to_share` converts, once, on the server.
      ...(c.kind === "remainder"
        ? { value: null }
        : c.kind === "share"
          ? { pct: String(c.value).trim() }
          : { value: String(c.value).trim() }),
    })));

  /* The first blocker, in words. A disabled button with no reason is the version of this
     that made somebody hunt for the row that was wrong (6.3). */
  const blocker = useMemo(() => {
    const answered = rows.filter((r) => liveCells(r.name).length);
    if (!answered.length) return "Nothing answered yet.";
    for (const r of rows) {
      const st = rowState(r.name);
      if (st.kind === "off") {
        return `${r.name || "This driver"} adds up to ${st.label}, not 100%.`;
      }
    }
    return null;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cells, modes, rows.length]);

  const answeredCount = rows.filter((r) => liveCells(r.name).length).length;

  const seedFromPrefill = () => {
    const next = {};
    for (const v of prefill?.rows || []) {
      next[`${v.subject || ""}|${v.business_id}`] = {
        kind: v.value_kind,
        // Stored as a fraction; typed as a percent. Seeding the raw 0.7 into a box
        // labelled % is the mismatch this phase exists to remove.
        value: v.value == null ? ""
          : (v.value_kind === "share" ? String(Number(v.value) * 100) : String(v.value)),
      };
    }
    setCells(next);
    setModes({});
  };

  const submit = async () => {
    setBusy(true); setErr(null);
    try {
      await postJSON(`/books/allocations/drivers/${driver.id}/commits`,
                     { period: month, rows: payload() });
      onCommitted();
    } catch (e) {
      setErr(e?.message || "That did not commit.");
    } finally { setBusy(false); }
  };

  if (!isCFO) return null;
  if (split && !rows.length) {
    return (
      <div style={{ fontFamily: F.body, fontSize: 12.5, color: L.body, padding: "4px 2px" }}>
        No rule reads this driver this month, so there is nobody to answer for. A driven rule
        names the person it is about, and this form follows that list.
      </div>
    );
  }

  const cols = businesses || [];
  return (
    <div>
      <div className="al-mx al-mx-head" style={{ display: "grid", gap: 10,
                    alignItems: "center",
                    gridTemplateColumns: `minmax(0,1fr) repeat(${cols.length}, 76px) 184px`,
                    padding: "9px 2px", borderBottom: `1px solid ${L.ruleHeader}` }}>
        <span style={EYEBROW}>{split ? "Person" : "Entity count"}</span>
        {cols.map((b) => (
          <span key={b.id} style={{ ...EYEBROW, textAlign: "right", overflow: "hidden",
                                    textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                title={b.name}>{b.name}</span>
        ))}
        <span style={{ ...EYEBROW, textAlign: "right" }}>Total</span>
      </div>

      {rows.map((r) => {
        const st = rowState(r.name);
        const m = mode(r.name);
        return (
          <div key={r.name || "count"} className="al-mx-wrap"
               style={{ borderBottom: `1px solid ${L.ruleLine}`,
                        background: st.kind === "empty" ? L.flagBg : "transparent",
                        boxShadow: st.kind === "empty" ? `inset 3px 0 0 0 ${L.flagEdge}`
                                                       : "none" }}>
            <div className="al-mx" style={{ display: "grid", gap: 10, alignItems: "center",
                          gridTemplateColumns:
                            `minmax(0,1fr) repeat(${cols.length}, 76px) 184px`,
                          padding: "10px 2px" }}>
              <span style={{ minWidth: 0 }}>
                <span style={{ fontFamily: F.body, fontSize: 13.5, fontWeight: 500,
                               color: L.deep, display: "block" }}>
                  {r.name || driver.label}
                </span>
                {/* WHAT THIS ROW MOVES, from the server. Both lines, because 6.3 asks for
                    the dollars and the mockup shows the mode: the mockup wins on look and
                    this document wins on data (principle 8), so the row carries each. */}
                <span style={{ fontFamily: F.body, fontSize: 11.5, color: L.faint,
                               display: "block" }}>
                  {MODES.find(([k]) => k === m)?.[1]}
                  {r.rules?.length
                    ? ` · feeds ${r.rules.map((x) => x.pool_name).join(", ")}`
                    : ""}
                </span>
                {r.pool_amount != null && (
                  <span style={{ fontFamily: F.num, fontSize: 11.5, color: L.code,
                                 display: "block" }}>
                    <Money v={r.pool_amount} size={11.5} className="al-inline" /> this month
                  </span>
                )}
              </span>

              {cols.map((b) => {
                const c = cell(r.name, b.id);
                const isRest = c.kind === "remainder";
                return (
                  <span key={b.id} style={{ display: "flex", gap: 3, alignItems: "center",
                                            justifyContent: "flex-end" }}>
                    <span className="al-mx-cell-label"
                          style={{ fontFamily: F.body, fontSize: 11, color: L.faint,
                                   flex: "1 1 auto", minWidth: 0 }}>{b.name}</span>
                    {m === "mixed" && split && (
                      <Select size="card" width={54} ariaLabel={`How ${r.name} ${b.name}`}
                              value={c.kind}
                              onChange={(v) => set(r.name, b.id,
                                { kind: v, value: v === "remainder" ? "" : c.value })}
                              options={UNITS} />
                    )}
                    <Input size="card" width={m === "mixed" && split ? 58 : 72} numeric
                           inputMode="decimal" disabled={isRest}
                           flag={st.kind === "empty"}
                           ariaLabel={`${r.name || driver.label}, ${b.name}`}
                           placeholder={isRest ? "rest" : (split && c.kind === "share"
                                                            ? "%" : "")}
                           value={isRest ? "" : c.value}
                           onChange={(v) => set(r.name, b.id, { value: v })} />
                  </span>
                );
              })}

              <span style={{ display: "flex", justifyContent: "flex-end", gap: 6,
                             alignItems: "center" }}>
                {split && (
                  <Select size="card" width={112} ariaLabel={`How ${r.name} is answered`}
                          value={m} onChange={(v) => setMode(r.name, v)} options={MODES} />
                )}
                <span style={{ fontFamily: F.num, fontSize: 12, fontWeight: 700,
                               padding: "4px 10px", borderRadius: 999, whiteSpace: "nowrap",
                               background: st.kind === "ok" || st.kind === "rest"
                                 ? L.accentBg : L.flagBg,
                               color: st.kind === "ok" || st.kind === "rest"
                                 ? L.accentInk : L.flagInk }}>
                  {st.label}
                </span>
              </span>
            </div>
          </div>
        );
      })}

      {/* The commit bar. Always visible, says how far along the month is, and names the
          first thing stopping it rather than greying out in silence. */}
      <div style={{ position: "sticky", bottom: 0, display: "flex", gap: 12,
                    alignItems: "center", flexWrap: "wrap", marginTop: 12,
                    padding: "11px 2px", background: L.card,
                    borderTop: `1px solid ${L.ruleHeader}` }}>
        <Button kind="primary" onClick={submit} disabled={busy || !!blocker}>
          {busy ? "Committing" : `Commit ${month ? fmtMonth(month) : "this month"}`}
        </Button>
        <span style={{ fontFamily: F.body, fontSize: 12.5, color: L.muted }}>
          {answeredCount} of {rows.length} {rows.length === 1 ? "row" : "rows"} answered
        </span>
        {blocker && (
          <span style={{ fontFamily: F.body, fontSize: 12.5, color: L.flagInk }}>
            {blocker}
          </span>
        )}
        {!!prefill?.rows?.length && (
          /* Nothing is prefilled without the click. D8: a number nobody looked at would
             read as one somebody decided. */
          <Button kind="quiet" onClick={seedFromPrefill}>
            Start from {prefill.from_month ? fmtMonth(prefill.from_month) : "last month"}
          </Button>
        )}
        {err && (
          <span style={{ fontFamily: F.body, fontSize: 12.5, color: L.flagInk }}>{err}</span>
        )}
      </div>
    </div>
  );
}
