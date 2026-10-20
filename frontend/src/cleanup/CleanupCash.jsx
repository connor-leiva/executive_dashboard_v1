/* Books Cleanup · Cash flow -- the projected company's bank balance, month by month, from the balance the file
   starts from: the installments still to come in, against the plan's monthly costs and the forecast's partner
   pay, commissions and retreats (and a repayment, if one is planned). Every figure is the server's.

   Illustrative until the bank's own feed is connected: it starts from QuickBooks' balance, and the monthly
   costs include shares another company pays and bills back. */
import { StatePanel } from "../books/ui.jsx";
import { useCleanupForward } from "./useCleanup.js";
import { Bar, Chip, EYEBROW, F, L, NUM, Note, NumberField, Panel, SaveLine, Sheet, inputStyle, keyLabel, money,
         shortOf } from "./kit.jsx";
import { CF_CSS, useSaver } from "./CleanupForecast.jsx";

export default function CleanupCash({ ov, rev, onSaved }) {
  const fw = useCleanupForward(rev);
  const f = fw.data?.forward;
  const can = !!fw.data?.can_decide;
  const [state, save] = useSaver(onSaved);
  const cash = f?.cash;
  const repay = f?.assumptions?.repay || null;
  const setRepay = (patch) => save([{ path: ["forward", "repay"], value: { ...repay, ...patch } }]);
  return (
    <Panel>
      <Bar label="Cash flow" right={f ? `${shortOf(ov, f.company)} · through ${f.statement?.through}` : ""}>
        {f ? <Chip dark on>{shortOf(ov, f.company)}</Chip> : null}
      </Bar>
      <StatePanel loading={fw.loading} error={fw.error} retry={fw.retry}>
        {fw.data && !f ? <div style={{ padding: 24, fontFamily: F.body, color: L.muted }}>This file projects nothing ahead.</div> : null}
        {f && cash && (
          <div className="cf-page" style={{ padding: "18px 26px 26px", display: "grid", gap: 14 }}>
            <Note tone="gold"><strong>Starts from {cash.account} at {cash.as_of ? keyLabel(cash.as_of.slice(0, 7)) : ""}: </strong>
              {money(cash.balance)}. {cash.note} Illustrative: the money in is the installments still to come; the money out
              is the Budget and Forecast assumptions.</Note>
            {repay ? (
              <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", fontFamily: F.body, fontSize: 13,
                            color: L.body }}>
                <label style={{ display: "flex", gap: 6, alignItems: "center" }}>
                  <input type="checkbox" checked={!!repay.on} disabled={!can} onChange={(e) => setRepay({ on: e.target.checked })} />
                  {repay.label}:</label>
                <NumberField value={repay.amount} prefix="$" width={100} disabled={!can} label={`${repay.label}: amount`}
                             onSave={(v) => setRepay({ amount: v })} /> in
                <select value={repay.key} disabled={!can} aria-label={`${repay.label}: month`}
                        style={{ ...inputStyle, fontFamily: F.body, width: 130 }}
                        onChange={(e) => setRepay({ key: e.target.value })}>
                  {f.months.map((m) => <option key={m.key} value={m.key}>{keyLabel(m.key)}</option>)}</select>
                <SaveLine state={state} />
              </div>) : null}
            <Sheet>
              <div style={{ overflowX: "auto" }}>
                <table style={{ borderCollapse: "collapse", width: "100%", minWidth: 900, fontFamily: F.body, fontSize: 12.5 }}>
                  <thead><tr>{["Month", "Starts with", "+ Installments in", `− ${f.partner || "Partner"}`, "− Monthly costs",
                               "− Commissions", "− Retreats", "− Repayment", "Ends with"].map((h, i) => (
                    <th key={h} style={{ ...EYEBROW, textAlign: i ? "right" : "left", padding: "9px 12px", background: L.colHead }}>{h}</th>))}
                  </tr></thead>
                  <tbody>{cash.rows.map((r) => (
                    <tr key={r.key} style={{ borderTop: `1px solid ${L.ruleLine}`, background: r.close < 0 ? L.lossBg : L.paper }}>
                      <td style={{ padding: "7px 12px", fontWeight: 600, color: L.deep }}>{keyLabel(r.key)}</td>
                      {[r.open, r.in, -r.partner, -r.running, -r.commissions, -r.retreats, -r.repay, r.close].map((v, i) => (
                        <td key={i} style={{ ...NUM, padding: "7px 12px", fontWeight: i === 7 ? 700 : 400,
                                             color: i === 7 && v < 0 ? L.loss : Math.abs(v) < 0.5 ? L.ghost : L.deep }}>
                          {money(v)}</td>))}
                    </tr>))}</tbody>
                </table>
              </div>
            </Sheet>
            <Note tone={cash.runs_out ? "red" : "plain"}>
              {cash.runs_out
                ? <>At these assumptions {shortOf(ov, f.company)}&rsquo;s account runs out in <strong>{keyLabel(cash.runs_out)}</strong>.
                    The revenue still to be earned that members already paid isn&rsquo;t new cash: it&rsquo;s part of the starting
                    balance, or was spent.</>
                : `At these assumptions the account stays above zero through ${f.statement?.through}.`}
              {" "}The monthly costs include shares another company pays and bills back.
            </Note>
          </div>
        )}
      </StatePanel>
      <style>{CF_CSS}</style>
    </Panel>
  );
}
