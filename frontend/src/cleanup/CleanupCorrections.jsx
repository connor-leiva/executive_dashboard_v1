/* Books Cleanup · Corrections -- the journal entries that make QuickBooks say what the Statement says.

   One entry per company per month, plus January 1 (the prior year's position). Each is shown the way
   it is typed into QuickBooks: account, debit, credit, in cents. The server works them out from the
   review and the decisions; this page only lays them out, and takes back the QuickBooks entry number
   once one is posted. The download is the same entries as a QuickBooks journal-entry import file.

   Posting is the bookkeeper's work, so writing the entry number needs only the tab, not an owner. */
import { useState } from "react";
import { StatePanel } from "../books/ui.jsx";
import { downloadEntries, saveChanges, useCleanupCorrections } from "./useCleanup.js";
import { Bar, BODY, Button, Chip, Controls, EYEBROW, Foot, L, F, NUM, OkPill, Panel, SaveLine, Sheet, Toggle,
         cents, inputStyle, money, reasonOf, shortOf } from "./kit.jsx";

function Posted({ company, k, value, onSaved }) {
  const [text, setText] = useState(value || "");
  const [state, setState] = useState(null);
  const dirty = (text || "") !== (value || "");
  const save = async () => {
    setState({ busy: true });
    try {
      onSaved(await saveChanges([{ path: ["posted", company, String(k)], value: text.trim() }]));
      setState({ saved: text.trim() ? "Saved." : "Cleared." });
    } catch (e) { setState({ error: reasonOf(e) }); }
  };
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
      <span style={{ fontFamily: F.body, fontSize: 12.5, color: L.bodyDim }}>Posted in QuickBooks as entry no.</span>
      <input value={text} onChange={(e) => setText(e.target.value)} maxLength={64} aria-label="QuickBooks entry number"
             onKeyDown={(e) => { if (e.key === "Enter" && dirty) save(); }}
             placeholder="not posted yet" style={{ ...inputStyle, width: 150, fontFamily: F.body }} />
      <Button primary disabled={!dirty || state?.busy} onClick={save}>Save</Button>
      <SaveLine state={state} />
    </div>
  );
}

function Entry({ c, k, onSaved, ov }) {
  const mon = c.months.find((m) => m.k === k);
  const rows = c.accounts.filter((r) => Math.abs(r.amounts[k]) >= 0.005);
  const debits = rows.reduce((n, r) => n + Math.max(r.amounts[k], 0), 0);
  const credits = rows.reduce((n, r) => n + Math.max(-r.amounts[k], 0), 0);
  const off = c.balance[k];
  const grid = { display: "grid", gridTemplateColumns: "minmax(0,1.3fr) minmax(0,1fr) 118px 118px", gap: 12 };
  return (
    <div style={{ display: "grid", gap: 14 }}>
      <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
        <span style={{ fontFamily: F.num, fontSize: 18, fontWeight: 700, color: L.deep }}>
          {shortOf(ov, c.company)} · {mon.label}</span>
        <span style={{ fontFamily: F.num, fontSize: 12, color: L.muted }}>{mon.journal_no} · dated {mon.date}</span>
        <div style={{ flex: 1 }} />
        <OkPill ok={Math.abs(off) < 0.05}>{Math.abs(off) < 0.05 ? "Debits equal credits" : `Off by ${cents(off)}`}</OkPill>
      </div>
      {rows.length === 0 ? (
        <Foot>No entry for {mon.label}: nothing here needs correcting.</Foot>
      ) : (
        <Sheet>
          <div style={{ overflowX: "auto" }}>
            <div style={{ minWidth: 640 }}>
              <div style={{ ...grid, padding: "9px 18px", background: L.colHead, borderBottom: `1px solid ${L.ruleHeader}` }}>
                <span style={EYEBROW}>Account</span>
                <span style={EYEBROW}>What it&rsquo;s for</span>
                <span style={{ ...EYEBROW, textAlign: "right" }}>Debit</span>
                <span style={{ ...EYEBROW, textAlign: "right" }}>Credit</span>
              </div>
              {rows.map((r) => {
                const v = r.amounts[k];
                const parts = Object.entries(r.parts_by_month || {}).filter(([, a]) => Math.abs(a[k]) >= 0.005);
                // the server's words for this line: "Part: what it's made of; Part: ..." (whose pay, which
                // subscriptions, the review's reasons, the transactions changed on the tab)
                const named = Object.fromEntries(((r.said || [])[k] || "").split("; ").map((seg) => {
                  const i = seg.indexOf(": ");
                  return i > 0 ? [seg.slice(0, i), seg.slice(i + 2)] : [seg, ""];
                }));
                return (
                  <div key={r.account} style={{ ...grid, padding: "8px 18px", alignItems: "baseline",
                                                borderBottom: `1px solid ${L.ruleLine}`,
                                                background: r.new ? L.flagBg : L.paper }}>
                    <span style={{ fontFamily: F.body, fontSize: 13, color: L.deep, overflowWrap: "anywhere" }}>
                      {r.account}
                      {r.new && <span style={{ color: L.flagInk, fontWeight: 600 }}> · New: create it first</span>}
                      <span style={{ display: "block", fontSize: 11.5, color: L.muted }}>
                        {r.line ? `P&L · ${r.line}` : "Balance sheet"}</span>
                    </span>
                    <span style={{ fontFamily: F.body, fontSize: 12, color: L.bodyDim, lineHeight: 1.5 }}>
                      {parts.map(([comp, a]) => (
                        <span key={comp} style={{ display: "block" }}>{c.labels[comp] || comp}
                          {named[c.labels[comp] || comp] ? <span style={{ color: L.muted }}>: {named[c.labels[comp] || comp]}</span> : null}
                          {parts.length > 1 ? <span style={{ color: L.muted }}> · {cents(a[k])}</span> : null}</span>
                      ))}
                    </span>
                    <span style={{ ...NUM, fontSize: 13, color: L.deep }}>{v > 0 ? cents(v) : ""}</span>
                    <span style={{ ...NUM, fontSize: 13, color: L.deep }}>{v < 0 ? cents(-v) : ""}</span>
                  </div>
                );
              })}
              <div style={{ ...grid, padding: "11px 18px", background: L.totalBand, borderTop: `1.5px solid ${L.deep}` }}>
                <span style={{ fontFamily: F.body, fontSize: 13.5, fontWeight: 700, color: L.deep }}>
                  {rows.length} {rows.length === 1 ? "line" : "lines"}</span>
                <span />
                <span style={{ ...NUM, fontSize: 14, fontWeight: 700, color: L.deep }}>{cents(debits)}</span>
                <span style={{ ...NUM, fontSize: 14, fontWeight: 700, color: L.deep }}>{cents(credits)}</span>
              </div>
            </div>
          </div>
        </Sheet>
      )}
      <div style={{ display: "flex", gap: 18, flexWrap: "wrap", alignItems: "center" }}>
        <span style={{ ...BODY, fontSize: 12.5 }}>
          Net income for {mon.label}: QuickBooks {money(c.qbo_net_income[k])}
          {" "}<span style={{ color: L.flagInk }}>{c.net_income_change[k] >= 0 ? "+" : "−"}{money(Math.abs(c.net_income_change[k]))}</span>
          {" "}= corrected <strong>{money(c.corrected_net_income[k])}</strong></span>
        <div style={{ flex: 1 }} />
        {rows.length > 0 && <Posted key={`${c.company}|${k}|${c.posted[String(k)] || ""}`} company={c.company} k={k}
                                    value={c.posted[String(k)]} onSaved={onSaved} />}
      </div>
    </div>
  );
}

export default function CleanupCorrections({ ov, rev, onSaved }) {
  const [company, setCompany] = useState(ov.companies[0]);
  const [k, setK] = useState(null);                       // null = every month, at a glance
  const cr = useCleanupCorrections(company, rev);
  const c = cr.data;
  const [dl, setDl] = useState(null);
  const hasEntry = (cc, kk) => cc.accounts.some((r) => Math.abs(r.amounts[kk]) >= 0.005);
  const download = async () => {
    setDl({ busy: true });
    try {
      await downloadEntries(company, `cleanup-entries-${shortOf(ov, company).toLowerCase().replace(/\W+/g, "-")}.csv`);
      setDl(null);
    } catch (e) { setDl({ error: reasonOf(e) }); }
  };
  const entries = c ? c.months.filter((m) => hasEntry(c, m.k)) : [];
  const posted = c ? entries.filter((m) => (c.posted[String(m.k)] || "").trim()).length : 0;
  return (
    <Panel>
      <Bar label="Company" right={c ? `${posted} of ${entries.length} entries posted` : ""}>
        {ov.companies.map((x) => (
          <Chip key={x} dark on={x === company} onClick={() => setCompany(x)}>{shortOf(ov, x)}</Chip>
        ))}
      </Bar>
      <Controls>
        <div style={{ display: "flex", alignItems: "center", gap: 4, flexWrap: "wrap" }}>
          <Toggle on={k === null} onClick={() => setK(null)}>Every month</Toggle>
          {(c?.months || []).map((m) => {
            const done = (c.posted[String(m.k)] || "").trim();
            return (
              <Toggle key={m.k} on={k === m.k} onClick={() => setK(m.k)} disabled={!hasEntry(c, m.k)}
                      title={done ? `Posted as ${done}` : undefined}>{m.label}{done ? " ✓" : ""}</Toggle>
            );
          })}
        </div>
        <div style={{ flex: 1 }} />
        {c && <OkPill ok={c.balanced && c.ties}>
          {c.balanced && c.ties ? "Every entry balances and ties" : !c.balanced ? "An entry doesn't balance" : "An entry doesn't tie"}</OkPill>}
        <Button onClick={download} disabled={dl?.busy}>Download for QuickBooks import</Button>
        <SaveLine state={dl} />
      </Controls>
      <StatePanel loading={cr.loading} error={cr.error} retry={cr.retry}>
        {c && (
          <div style={{ padding: "20px 26px 26px", display: "grid", gap: 18 }}>
            {k === null ? (
              <Sheet title={`${shortOf(ov, company)}'s entries`}>
                <div style={{ overflowX: "auto" }}>
                  <div style={{ minWidth: 700 }}>
                    <div className="cc-grid" style={{ padding: "9px 18px", background: L.colHead,
                                                      borderBottom: `1px solid ${L.ruleHeader}` }}>
                      {["Entry", "Lines", "Debits", "QuickBooks NI", "Change", "Corrected NI", "Posted as"].map((h, i) => (
                        <span key={h} style={{ ...EYEBROW, textAlign: i === 0 || i === 6 ? "left" : "right" }}>{h}</span>
                      ))}
                    </div>
                    {c.months.map((m) => {
                      const rows = c.accounts.filter((r) => Math.abs(r.amounts[m.k]) >= 0.005);
                      const deb = rows.reduce((n, r) => n + Math.max(r.amounts[m.k], 0), 0);
                      const done = (c.posted[String(m.k)] || "").trim();
                      return (
                        <button key={m.k} className="cc-grid cc-month" onClick={() => rows.length && setK(m.k)}
                          disabled={!rows.length} style={{ width: "100%", border: 0, textAlign: "left", padding: "9px 18px",
                            borderBottom: `1px solid ${L.ruleLine}`, background: L.paper,
                            cursor: rows.length ? "pointer" : "default", alignItems: "baseline" }}>
                          <span style={{ fontFamily: F.body, fontSize: 13, fontWeight: 600, color: L.deep }}>
                            {m.label} <span style={{ fontWeight: 400, color: L.muted, fontFamily: F.num, fontSize: 11 }}>
                              {m.journal_no}</span></span>
                          <span style={{ ...NUM, fontSize: 13, color: L.body }}>{rows.length || "–"}</span>
                          <span style={{ ...NUM, fontSize: 13, color: L.body }}>{rows.length ? cents(deb) : "–"}</span>
                          <span style={{ ...NUM, fontSize: 13, color: L.muted }}>{money(c.qbo_net_income[m.k])}</span>
                          <span style={{ ...NUM, fontSize: 13, color: Math.abs(c.net_income_change[m.k]) >= 1 ? L.flagInk : L.ghost }}>
                            {Math.abs(c.net_income_change[m.k]) < 0.5 ? "–" : money(c.net_income_change[m.k])}</span>
                          <span style={{ ...NUM, fontSize: 13, fontWeight: 600, color: L.deep }}>{money(c.corrected_net_income[m.k])}</span>
                          <span style={{ fontFamily: F.body, fontSize: 12.5, color: done ? L.accentInk : L.ghost }}>
                            {rows.length ? (done || "not yet") : ""}</span>
                        </button>
                      );
                    })}
                  </div>
                </div>
              </Sheet>
            ) : (
              <Entry c={c} k={k} onSaved={onSaved} ov={ov} />
            )}
            <Foot>
              Post each entry as a journal entry in {company}&rsquo;s QuickBooks, then write its number here.
              {c.accounts.some((r) => r.new) && " Accounts marked New don't exist yet: create them first (the Balance sheet says which type)."}
              {" "}The download holds every month&rsquo;s entry in QuickBooks&rsquo; journal-entry import format; check the mapping screen when you import it.
              Don&rsquo;t also edit the old transactions by hand: the entries already fix them.
            </Foot>
          </div>
        )}
      </StatePanel>
      <style>{`
        .cc-grid { display:grid; grid-template-columns: minmax(150px,1.2fr) 56px 120px 120px 110px 120px minmax(110px,1fr); gap:12px; }
        .cc-month:hover:not(:disabled) { background:${L.totalBand} !important; }
      `}</style>
    </Panel>
  );
}
