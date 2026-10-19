/* Books Cleanup · Decisions -- the workbook's yellow cells, as a form.

   Every decision here moves money between the companies, so the server recomputes the whole year on
   each save and every page follows (the shell's `rev`). Nothing is worked out on this screen beyond
   what a person needs while typing: the sum of a split, and a share's dollars at the split shown.

   A split is saved as ONE set of changes (a person's whole year, an item's four brands), which the
   server applies all or nothing, so a split never sits half-saved at 150%. The screen also refuses to
   save a split that doesn't add to 100%: the server would take it, and every check on the tab would
   then fail on a typo.

   Members read this page. Only an owner or admin changes it (the server enforces that; the inputs
   here are disabled to say so rather than to enforce it). */
import { useEffect, useState } from "react";
import { StatePanel } from "../books/ui.jsx";
import { saveChanges, useCleanupDecisions } from "./useCleanup.js";
import { BODY, Button, EYEBROW, Foot, L, F, NUM, OkPill, Panel, SaveLine, Seg, Sheet, cents, inputStyle, money,
         reasonOf, share, shortOf } from "./kit.jsx";

const EPS = 1e-4;
const sumOf = (o) => Object.values(o).reduce((n, v) => n + (Number(v) || 0), 0);
const isPct = (v) => typeof v === "number" && v >= -EPS && v <= 1 + EPS;

/* A percentage typed as 58.7 and held as 0.587. Keeps its own text while focused, so "58." can be
   typed on the way to "58.7". */
function PctInput({ value, onChange, disabled, label, width = 62 }) {
  const shown = value === null || value === undefined ? "" : String(+(Number(value) * 100).toFixed(2));
  const [text, setText] = useState(shown);
  const [focus, setFocus] = useState(false);
  useEffect(() => { if (!focus) setText(shown); }, [shown, focus]);
  const bad = !isPct(value);
  return (
    <input value={focus ? text : shown} inputMode="decimal" aria-label={label} disabled={disabled}
      onFocus={() => { setFocus(true); setText(shown); }} onBlur={() => setFocus(false)}
      onChange={(e) => {
        setText(e.target.value);
        const t = e.target.value.trim();
        const n = t === "" ? 0 : Number(t);
        onChange(Number.isFinite(n) ? n / 100 : NaN);
      }}
      style={{ ...inputStyle, width, textAlign: "right", borderColor: bad ? L.flagRing : L.pillEdge,
               background: disabled ? L.totalBand : L.paper }} />
  );
}

function SumCell({ total, need = true }) {
  const ok = Math.abs(total - 1) < EPS;
  return (
    <span style={{ ...NUM, fontSize: 12, fontWeight: 600, color: !need ? L.ghost : ok ? L.accentInk : L.flagInk }}>
      {share(total)}{need && !ok ? " ≠ 100%" : ""}</span>
  );
}

/* One group's pending edits, its save and its status line. `base(path)` reads the saved value. */
function useDraft(key, onSaved) {
  const [draft, setDraft] = useState({});
  const [state, setState] = useState(null);
  const dirty = Object.keys(draft).length > 0;
  const set = (path, v) => setDraft((d) => ({ ...d, [JSON.stringify(path)]: v }));
  const get = (path, fallback) => {
    const k = JSON.stringify(path);
    return k in draft ? draft[k] : fallback;
  };
  const save = async (extra = []) => {
    const changes = [...Object.entries(draft).map(([k, v]) => ({ path: JSON.parse(k), value: v })), ...extra];
    if (!changes.length) return;
    setState({ busy: true });
    try {
      const r = await saveChanges(changes);
      setDraft({});
      setState({ saved: "Saved. Every page has the new figures." });
      onSaved(r);
    } catch (e) {
      setState({ error: reasonOf(e) });
    }
  };
  return { draft, dirty, set, get, save, discard: () => { setDraft({}); setState(null); }, state, key };
}

function Actions({ g, canDecide, blocked, label = "Save" }) {
  if (!canDecide) return null;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
      <Button primary disabled={!g.dirty || !!blocked || g.state?.busy} onClick={() => g.save()}>{label}</Button>
      {g.dirty && <Button onClick={g.discard}>Discard</Button>}
      {blocked && g.dirty ? <span style={{ fontFamily: F.body, fontSize: 12, color: L.flagInk }}>{blocked}</span>
                          : <SaveLine state={g.state} />}
    </div>
  );
}

/* ── the partners and the owner's salary ─────────────────────────────────────────────────── */

function Partners({ d, ov, canDecide, onSaved }) {
  const g = useDraft("partners", onSaved);
  const w = d.words || {};
  const results = Object.fromEntries((ov.headline.partners || []).map((p) => [p.company, p]));
  const through = ov.period.months.find((m) => m.m === ov.headline.partners_through)?.label;
  const salary = g.get(["salary"], d.salary);
  const bad = d.partners.some((p) => !isPct(g.get(["rates", p.company], d.rates[p.company])))
    || !(Number(salary) >= 0);
  return (
    <Sheet title="The partners' shares" right={<Actions g={g} canDecide={canDecide}
           blocked={bad ? "A rate is 0 to 100%, the salary a dollar amount." : null} />}>
      <div style={{ padding: "14px 18px", display: "grid", gap: 14 }}>
        {w.partners_intro && <div style={{ ...BODY, color: L.bodyDim }}>{w.partners_intro}</div>}
        {d.partners.map((p) => {
          const r = results[p.company];
          return (
            <div key={p.company} style={{ display: "grid", gap: 6, paddingTop: 10, borderTop: `1px solid ${L.ruleLine}` }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                <span style={{ fontFamily: F.body, fontSize: 14, fontWeight: 600, color: L.deep }}>{p.person}</span>
                <span style={{ fontFamily: F.body, fontSize: 12.5, color: L.muted }}>
                  share of {shortOf(ov, p.company)}&rsquo;s net operating income</span>
                <div style={{ flex: 1 }} />
                <PctInput label={`${p.person}'s rate`} value={g.get(["rates", p.company], d.rates[p.company])}
                          disabled={!canDecide} onChange={(v) => g.set(["rates", p.company], v)} />
                <span style={{ fontFamily: F.body, fontSize: 12.5, color: L.muted }}>%</span>
              </div>
              {p.note && <div style={{ ...BODY, fontSize: 12.5, color: L.muted }}>{p.note}</div>}
              {r && (
                <div style={{ fontFamily: F.body, fontSize: 12.5, color: L.body, lineHeight: 1.6 }}>
                  January to {through}: operating income {money(r.noi)}
                  {r.salary ? <>, less {d.salary_owner}&rsquo;s salary {money(r.salary)}</> : null}
                  {" "}&rarr; earned <strong>{money(r.earned)}</strong>; paid {money(r.paid)}
                  {r.owed_back > 0 && <span style={{ color: L.flagInk }}> · owes back <strong>{money(r.owed_back)}</strong></span>}
                  {r.owed_to > 0 && <span style={{ color: L.accentInk }}> · still owed <strong>{money(r.owed_to)}</strong></span>}
                  <span style={{ color: L.muted }}> · on the P&amp;L to the last day: {money(r.to_date)}</span>
                </div>
              )}
            </div>
          );
        })}
        {d.partners.some((p) => p.salary) && (
          <div style={{ display: "grid", gap: 6, paddingTop: 10, borderTop: `1px solid ${L.ruleLine}` }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
              <span style={{ fontFamily: F.body, fontSize: 14, fontWeight: 600, color: L.deep }}>
                {w.salary_label || `${d.salary_owner}'s salary, a year`}</span>
              <div style={{ flex: 1 }} />
              <span style={{ fontFamily: F.body, fontSize: 12.5, color: L.muted }}>$</span>
              <input aria-label="The owner's yearly salary" inputMode="decimal" disabled={!canDecide}
                value={salary === null || salary === undefined || Number.isNaN(salary) ? "" : String(salary)}
                onChange={(e) => g.set(["salary"], e.target.value.trim() === "" ? 0 : Number(e.target.value))}
                style={{ ...inputStyle, width: 110, textAlign: "right" }} />
            </div>
            {w.salary_note && <div style={{ ...BODY, fontSize: 12.5, color: L.muted }}>{w.salary_note}</div>}
          </div>
        )}
      </div>
    </Sheet>
  );
}

/* ── the two switches ─────────────────────────────────────────────────────────────────────── */

function Switch({ title, words, options, value, canDecide, onPick, state, children }) {
  return (
    <Sheet title={title} right={<SaveLine state={state} />}>
      <div style={{ padding: "14px 18px", display: "grid", gap: 10 }}>
        <Seg options={options} value={value} onChange={onPick} disabled={!canDecide || state?.busy} />
        {children}
        {words && (
          <div style={{ display: "grid", gap: 6 }}>
            {[["What it means", words.means], ["What the old books did", words.old], ["Recommendation", words.rec]]
              .filter(([, v]) => v).map(([k, v]) => (
                <div key={k} style={{ ...BODY, fontSize: 12.5 }}>
                  <span style={{ ...EYEBROW, marginRight: 8 }}>{k}</span>{v}</div>
              ))}
          </div>
        )}
      </div>
    </Sheet>
  );
}

function useOne(onSaved) {
  const [state, setState] = useState(null);
  const pick = async (path, value) => {
    setState({ busy: true });
    try { onSaved(await saveChanges([{ path, value }])); setState({ saved: "Saved." }); }
    catch (e) { setState({ error: reasonOf(e) }); }
  };
  return [state, pick];
}

function Holdco({ d, ov, canDecide, onSaved }) {
  const [state, pick] = useOne(onSaved);
  const words = (d.words?.decisions || []).find((x) => x.switch === "holdco");
  const a = d.holdco_accounts;
  return (
    <Switch title={words?.title || "Investment or loan?"} words={words} value={d.holdco} canDecide={canDecide}
            state={state} options={d.holdco_choices.map((c) => [c, c])} onPick={(v) => pick(["holdco"], v)}>
      <div style={{ display: "grid", gap: 4 }}>
        {d.holdco_rows.map((r) => (
          <div key={`${r.company}|${r.account}`} style={{ display: "flex", gap: 12, fontFamily: F.body, fontSize: 12.5,
                                                          flexWrap: "wrap" }}>
            <span style={{ flex: 1, minWidth: 220, color: L.body }}>{shortOf(ov, r.company)}
              <span style={{ color: L.muted }}> · booked to {r.account} · {r.count} {r.count === 1 ? "payment" : "payments"}</span>
              <span style={{ color: L.flagInk }}>
                {d.holdco === "Loan" ? ` → ${a.loan}` : r.account === a.invest ? ` → ${a.dist}` : " · stays as booked"}</span>
            </span>
            <span style={{ ...NUM, color: L.deep }}>{r.amount < 0 ? `${money(-r.amount)} in` : `${money(r.amount)} out`}</span>
          </div>
        ))}
      </div>
    </Switch>
  );
}

function Basis({ d, ov, canDecide, onSaved }) {
  const [state, pick] = useOne(onSaved);
  const b = d.basis;
  const chosen = b.choices.find((c) => c.key === d.bc_basis);
  return (
    <Switch title={b.title} value={d.bc_basis} canDecide={canDecide} state={state}
            options={b.choices.map((c) => [c.label, c.key])} onPick={(v) => pick(["bc_basis"], v)}>
      <div style={{ ...BODY, fontSize: 12.5, color: L.muted }}>
        {chosen?.note} {b.company ? `It moves ${shortOf(ov, b.company)}'s program revenue between months, and with it the partner's share.` : ""}
      </div>
    </Switch>
  );
}

/* ── splits: ads, people, items ────────────────────────────────────────────────────────── */

function SplitHead({ brands, extra = [], lead = "", tail = ["Total"] }) {
  return (
    <div className="cd-grid" style={{ gridTemplateColumns: `minmax(160px,1fr) repeat(${brands.length + extra.length}, 92px) 84px`,
                                      padding: "8px 18px", background: L.colHead, borderBottom: `1px solid ${L.ruleHeader}` }}>
      <span style={EYEBROW}>{lead}</span>
      {[...brands, ...extra].map((b) => <span key={b} style={{ ...EYEBROW, textAlign: "right", whiteSpace: "normal" }}>{b}</span>)}
      {tail.map((t) => <span key={t} style={{ ...EYEBROW, textAlign: "right" }}>{t}</span>)}
    </div>
  );
}

function Ads({ d, canDecide, onSaved }) {
  const g = useDraft("ads", onSaved);
  const cols = [...d.brands, "billback"];
  const vals = Object.fromEntries(cols.map((b) => [b, g.get(["ads", b], d.ads.split[b])]));
  const total = sumOf(vals);
  const pool = d.ads.pool.reduce((n, v) => n + v, 0);
  const fees = d.fees.pool.reduce((n, v) => n + v, 0);
  const bad = cols.some((b) => !isPct(vals[b])) ? "Each share is 0 to 100%."
    : Math.abs(total - 1) > EPS ? "The split has to add to 100%." : null;
  const w = d.words || {};
  return (
    <Sheet title="The shared ads and payment fees" right={<Actions g={g} canDecide={canDecide} blocked={bad} />}>
      {w.splits_intro && <div style={{ ...BODY, color: L.bodyDim, padding: "12px 18px 4px" }}>{w.splits_intro}</div>}
      <div style={{ overflowX: "auto" }}>
        <div style={{ minWidth: 780 }}>
          <SplitHead brands={d.brands} extra={[`${d.ads.billback_name} (billed back)`]} lead="Shared cost" />
          <div className="cd-grid" style={{ gridTemplateColumns: `minmax(160px,1fr) repeat(${cols.length}, 92px) 84px`,
                                            padding: "10px 18px", alignItems: "center", borderBottom: `1px solid ${L.ruleLine}` }}>
            <span style={{ fontFamily: F.body, fontSize: 13.5, fontWeight: 600, color: L.deep }}>
              Advertising <span style={{ fontWeight: 400, color: L.muted }}>· {money(pool)} this year</span></span>
            {cols.map((b) => (
              <span key={b} style={{ textAlign: "right" }}>
                <PctInput label={`Ads: ${b}`} value={vals[b]} disabled={!canDecide} onChange={(v) => g.set(["ads", b], v)} />
              </span>
            ))}
            <SumCell total={total} />
          </div>
          <div className="cd-grid" style={{ gridTemplateColumns: `minmax(160px,1fr) repeat(${cols.length}, 92px) 84px`,
                                            padding: "4px 18px 10px", borderBottom: `1px solid ${L.ruleLine}` }}>
            <span style={{ fontFamily: F.body, fontSize: 12, color: L.muted }}>in dollars</span>
            {cols.map((b) => <span key={b} style={{ ...NUM, fontSize: 12, color: L.muted }}>{money(pool * (vals[b] || 0))}</span>)}
            <span />
          </div>
          <div className="cd-grid" style={{ gridTemplateColumns: `minmax(160px,1fr) repeat(${cols.length}, 92px) 84px`,
                                            padding: "10px 18px", alignItems: "center" }}>
            <span style={{ fontFamily: F.body, fontSize: 13.5, fontWeight: 600, color: L.deep }}>
              Payment processing <span style={{ fontWeight: 400, color: L.muted }}>· {money(fees)}</span></span>
            {cols.map((b) => <span key={b} style={{ ...NUM, fontSize: 13, color: L.bodyDim }}>
              {b === "billback" ? "–" : share(d.fees.split[b])}</span>)}
            <SumCell total={sumOf(Object.fromEntries(d.brands.map((b) => [b, d.fees.split[b]])))} />
          </div>
        </div>
      </div>
      <div style={{ padding: "4px 18px 14px", display: "grid", gap: 6 }}>
        {w.ads_note && <Foot><strong style={{ color: L.bodyDim }}>Ads:</strong> {w.ads_note}</Foot>}
        {w.fees_note && <Foot><strong style={{ color: L.bodyDim }}>Payment processing:</strong> {w.fees_note}</Foot>}
      </div>
    </Sheet>
  );
}

function Person({ p, d, canDecide, onSaved, open, onToggle }) {
  const g = useDraft(`staff|${p.name}`, onSaved);
  const months = d.months;
  const val = (m, b) => g.get(["staff", p.name, String(m.m), b], p.split[String(m.m)]?.[b] ?? 0);
  const paid = months.filter((m, i) => Math.abs(p.cost[i]) >= 0.005);
  const badMonth = paid.find((m) => Math.abs(sumOf(Object.fromEntries(d.brands.map((b) => [b, val(m, b)]))) - 1) > EPS);
  const badPct = months.some((m) => d.brands.some((b) => !isPct(val(m, b))));
  const blocked = badPct ? "Each share is 0 to 100%." : badMonth ? `${badMonth.label} doesn't add to 100%.` : null;
  const first = paid[0];
  const fill = () => months.forEach((m) => d.brands.forEach((b) => {
    if (m.m !== first.m) g.set(["staff", p.name, String(m.m), b], val(first, b));
  }));
  const cols = `minmax(160px,1fr) repeat(${d.brands.length}, 96px) 92px 28px`;
  return (
    <>
      <div className="cd-grid cd-click" role="button" tabIndex={0} aria-expanded={open} onClick={onToggle}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onToggle(); } }}
        style={{ gridTemplateColumns: cols, padding: "9px 18px", alignItems: "center", cursor: "pointer",
                 borderBottom: `1px solid ${L.ruleLine}`, background: open ? L.totalBand : L.paper }}>
        <span style={{ minWidth: 0 }}>
          <span style={{ fontFamily: F.body, fontSize: 13.5, fontWeight: 600, color: L.deep }}>{p.name}</span>
          <span style={{ fontFamily: F.body, fontSize: 12, color: L.muted }}>
            {p.follows_wages ? " · follows the W-2 wages" : p.w2 ? " · W-2" : ""}{g.dirty ? " · unsaved" : ""}</span>
        </span>
        {d.brands.map((b) => <span key={b} style={{ ...NUM, fontSize: 13, color: p.brand_totals[b] ? L.body : L.ghost }}>
          {money(p.brand_totals[b])}</span>)}
        <span style={{ ...NUM, fontSize: 13, fontWeight: 600, color: L.deep }}>{money(p.total)}</span>
        <span aria-hidden="true" style={{ justifySelf: "end", width: 0, height: 0, borderTop: "4px solid transparent",
          borderBottom: "4px solid transparent", borderLeft: `5.5px solid ${L.faint}`,
          transform: open ? "rotate(90deg)" : "none" }} />
      </div>
      {open && (
        <div style={{ background: L.totalBand, padding: "12px 18px 16px", borderBottom: `1px solid ${L.ruleInner}`,
                      display: "grid", gap: 10 }}>
          {p.note && <div style={{ ...BODY, fontSize: 12.5, color: L.bodyDim }}>{p.note}</div>}
          {p.follows_wages ? (
            <div style={{ ...BODY, fontSize: 12.5, color: L.muted }}>Not a decision: it splits each month the way the
              W-2 wages that month split.</div>
          ) : (
            <>
              <div className="cd-grid" style={{ gridTemplateColumns: `90px 100px repeat(${d.brands.length}, 92px) 84px` }}>
                <span style={EYEBROW}>Month</span>
                <span style={{ ...EYEBROW, textAlign: "right" }}>Paid</span>
                {d.brands.map((b) => <span key={b} style={{ ...EYEBROW, textAlign: "right" }}>{b}</span>)}
                <span style={{ ...EYEBROW, textAlign: "right" }}>Total</span>
              </div>
              {months.map((m, i) => {
                const was = Math.abs(p.cost[i]) >= 0.005;
                const row = Object.fromEntries(d.brands.map((b) => [b, val(m, b)]));
                return (
                  <div key={m.m} className="cd-grid" style={{ gridTemplateColumns: `90px 100px repeat(${d.brands.length}, 92px) 84px`,
                                                              alignItems: "center", opacity: was ? 1 : 0.6 }}>
                    <span style={{ fontFamily: F.body, fontSize: 12.5, color: L.body }}>{m.label}</span>
                    <span style={{ ...NUM, fontSize: 12.5, color: was ? L.deep : L.ghost }}>{was ? cents(p.cost[i]) : "not paid"}</span>
                    {d.brands.map((b) => (
                      <span key={b} style={{ textAlign: "right" }}>
                        <PctInput label={`${p.name}, ${m.label}: ${b}`} value={row[b]} disabled={!canDecide}
                                  onChange={(v) => g.set(["staff", p.name, String(m.m), b], v)} />
                      </span>
                    ))}
                    <SumCell total={sumOf(row)} need={was} />
                  </div>
                );
              })}
              {canDecide && (
                <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                  {first && <Button onClick={fill}>Use {first.label}&rsquo;s split for every month</Button>}
                  <div style={{ flex: 1 }} />
                  <Actions g={g} canDecide={canDecide} blocked={blocked} label={`Save ${p.name}'s split`} />
                </div>
              )}
            </>
          )}
        </div>
      )}
    </>
  );
}

function Staff({ d, canDecide, onSaved }) {
  const [open, setOpen] = useState(null);
  const total = d.staff.reduce((n, p) => n + p.total, 0);
  return (
    <Sheet title="Staff and contractors, person by person"
           right={<span style={{ fontFamily: F.body, fontSize: 12, color: L.muted }}>{money(total)} this year</span>}>
      <div style={{ overflowX: "auto" }}>
        <div style={{ minWidth: 720 }}>
          <div className="cd-grid" style={{ gridTemplateColumns: `minmax(160px,1fr) repeat(${d.brands.length}, 96px) 92px 28px`,
                                            padding: "8px 18px", background: L.colHead, borderBottom: `1px solid ${L.ruleHeader}` }}>
            <span style={EYEBROW}>Person</span>
            {d.brands.map((b) => <span key={b} style={{ ...EYEBROW, textAlign: "right" }}>{b}</span>)}
            <span style={{ ...EYEBROW, textAlign: "right" }}>Paid</span>
            <span />
          </div>
          {d.staff.map((p) => (
            <Person key={p.name} p={p} d={d} canDecide={canDecide} onSaved={onSaved} open={open === p.name}
                    onToggle={() => setOpen(open === p.name ? null : p.name)} />
          ))}
        </div>
      </div>
    </Sheet>
  );
}

function ItemRow({ kind, r, d, canDecide, onSaved, showLine }) {
  const g = useDraft(`items|${kind}|${r.name}`, onSaved);
  const vals = Object.fromEntries(d.brands.map((b) => [b, g.get(["items", kind, r.name, b], r.split[b])]));
  const total = sumOf(vals);
  const blocked = d.brands.some((b) => !isPct(vals[b])) ? "0 to 100%."
    : Math.abs(total - 1) > EPS ? "Has to add to 100%." : null;
  return (
    <div style={{ borderBottom: `1px solid ${L.ruleLine}`, padding: "8px 18px" }}>
      <div className="cd-grid" style={{ gridTemplateColumns: `minmax(160px,1fr) 92px repeat(${d.brands.length}, 92px) 84px`,
                                        alignItems: "center" }}>
        <span style={{ minWidth: 0 }} title={r.note || undefined}>
          <span style={{ fontFamily: F.body, fontSize: 13, fontWeight: 600, color: L.deep, overflowWrap: "anywhere" }}>
            {r.name}</span>
          {showLine && <span style={{ fontFamily: F.body, fontSize: 12, color: L.muted }}> · {r.line}</span>}
        </span>
        <span style={{ ...NUM, fontSize: 13, color: L.body }}>{money(r.total)}</span>
        {d.brands.map((b) => (
          <span key={b} style={{ textAlign: "right" }}>
            <PctInput label={`${r.name}: ${b}`} value={vals[b]} disabled={!canDecide}
                      onChange={(v) => g.set(["items", kind, r.name, b], v)} />
          </span>
        ))}
        <SumCell total={total} />
      </div>
      {r.note && <div style={{ fontFamily: F.body, fontSize: 12, color: L.muted, marginTop: 4, lineHeight: 1.45 }}>{r.note}</div>}
      {(g.dirty || g.state) && (
        <div style={{ marginTop: 6 }}><Actions g={g} canDecide={canDecide} blocked={blocked} /></div>
      )}
    </div>
  );
}

function Items({ kind, title, d, canDecide, onSaved }) {
  const rows = d.items[kind] || [];
  const total = rows.reduce((n, r) => n + r.total, 0);
  const off = rows.filter((r) => !r.ok).length;
  return (
    <Sheet title={title} right={<span style={{ fontFamily: F.body, fontSize: 12, color: off ? L.flagInk : L.muted }}>
      {rows.length} items · {money(total)}{off ? ` · ${off} not at 100%` : ""}</span>}>
      <div style={{ overflowX: "auto" }}>
        <div style={{ minWidth: 780 }}>
          <div className="cd-grid" style={{ gridTemplateColumns: `minmax(160px,1fr) 92px repeat(${d.brands.length}, 92px) 84px`,
                                            padding: "8px 18px", background: L.colHead, borderBottom: `1px solid ${L.ruleHeader}` }}>
            <span style={EYEBROW}>{kind === "software" ? "Subscription" : "Cost"}</span>
            <span style={{ ...EYEBROW, textAlign: "right" }}>This year</span>
            {d.brands.map((b) => <span key={b} style={{ ...EYEBROW, textAlign: "right" }}>{b}</span>)}
            <span style={{ ...EYEBROW, textAlign: "right" }}>Total</span>
          </div>
          {rows.map((r) => <ItemRow key={r.name} kind={kind} r={r} d={d} canDecide={canDecide} onSaved={onSaved}
                                    showLine={kind !== "software"} />)}
        </div>
      </div>
    </Sheet>
  );
}

/* ── the owner's charges ────────────────────────────────────────────────────────────────── */

function Charge({ c, d, canDecide, onSaved }) {
  const [state, setState] = useState(null);
  const put = async (changes) => {
    setState({ busy: true });
    try { onSaved(await saveChanges(changes)); setState({ saved: "Saved." }); }
    catch (e) { setState({ error: reasonOf(e) }); }
  };
  return (
    <div style={{ padding: "10px 18px", borderBottom: `1px solid ${L.ruleLine}`, display: "grid", gap: 6 }}>
      <div style={{ display: "flex", gap: 12, alignItems: "baseline", flexWrap: "wrap" }}>
        <span style={{ fontFamily: F.num, fontSize: 11.5, color: L.ghost, width: 76, flexShrink: 0 }}>{c.date}</span>
        <span style={{ flex: 1, minWidth: 220, fontFamily: F.body, fontSize: 13, color: L.body }}>
          <strong style={{ color: L.deep, fontWeight: 600 }}>{c.who}</strong> · {c.what}
          <span style={{ color: L.muted }}> · booked as {c.booked_line} ({c.account}) · {c.sid}</span>
        </span>
        <span style={{ ...NUM, fontSize: 13, color: L.deep }}>{cents(c.amount)}</span>
      </div>
      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", paddingLeft: 88 }}>
        <Seg value={c.treat} disabled={!canDecide || state?.busy}
             options={[["The owner's own", "own"], ["A business cost", "business"]]}
             onChange={(v) => put([{ path: ["charges", c.ref, "treat"], value: v }])} />
        {c.treat === "business" && (
          <select aria-label="Which P&L line" disabled={!canDecide || state?.busy} value={c.line}
                  onChange={(e) => put([{ path: ["charges", c.ref, "line"], value: e.target.value }])}
                  style={{ ...inputStyle, height: 32, fontFamily: F.body }}>
            {d.charge_lines.map((l) => <option key={l} value={l}>{l}</option>)}
          </select>
        )}
        <SaveLine state={state} />
      </div>
    </div>
  );
}

function Charges({ d, canDecide, onSaved }) {
  const own = d.charges.filter((c) => c.treat === "own");
  const words = (d.words?.decisions || []).find((x) => /clothing|charges/i.test(x.title));
  return (
    <Sheet title="The owner's charges, one by one" right={<span style={{ fontFamily: F.body, fontSize: 12, color: L.muted }}>
      {own.length} of {d.charges.length} the owner&rsquo;s own · {money(own.reduce((n, c) => n + c.amount, 0))}</span>}>
      {words?.rec && <div style={{ ...BODY, fontSize: 12.5, color: L.bodyDim, padding: "12px 18px 4px" }}>{words.means} {words.rec}</div>}
      {d.charges.map((c) => <Charge key={c.ref} c={c} d={d} canDecide={canDecide} onSaved={onSaved} />)}
    </Sheet>
  );
}

/* ── what else was decided ──────────────────────────────────────────────────────────────── */

function OnRecord({ d }) {
  const w = d.words || {};
  const rest = (w.decisions || []).filter((x) => !x.switch);
  if (!rest.length && !(w.decided || []).length) return null;
  return (
    <Sheet title="Decided, and on record">
      <div style={{ padding: "12px 18px 16px", display: "grid", gap: 12 }}>
        {(w.decided || []).map((x, i) => <div key={i} style={{ ...BODY, fontSize: 12.5 }}>{x}</div>)}
        {rest.map((x) => (
          <details key={x.title} style={{ borderTop: `1px solid ${L.ruleLine}`, paddingTop: 8 }}>
            <summary style={{ cursor: "pointer", fontFamily: F.body, fontSize: 13.5, fontWeight: 600, color: L.deep }}>
              {x.title}</summary>
            <div style={{ display: "grid", gap: 6, marginTop: 8 }}>
              {[["What it means", x.means], ["What the old books did", x.old], ["Recommendation", x.rec],
                ["Decision", x.answer], ["Notes", x.notes]].filter(([, v]) => v).map(([k, v]) => (
                <div key={k} style={{ ...BODY, fontSize: 12.5 }}><span style={{ ...EYEBROW, marginRight: 8 }}>{k}</span>{v}</div>
              ))}
            </div>
          </details>
        ))}
      </div>
    </Sheet>
  );
}

/* ── the page ───────────────────────────────────────────────────────────────────────────── */

export default function CleanupDecisions({ ov, rev, onSaved }) {
  const dec = useCleanupDecisions(rev);
  const d = dec.data;
  const canDecide = !!d?.can_decide;
  const failing = (ov.checks || []).filter((c) => !c.ok);
  return (
    <StatePanel loading={dec.loading} error={dec.error} retry={dec.retry}>
      {d && (
        <Panel>
          <div style={{ background: L.deep, padding: "14px 26px", display: "flex", gap: 18, flexWrap: "wrap",
                        alignItems: "baseline" }}>
            <span style={{ fontFamily: F.num, fontSize: 10, letterSpacing: ".14em", textTransform: "uppercase",
                           color: L.onDeepEyebrow }}>Net income, corrected, with these decisions</span>
            {ov.companies.map((c) => (
              <span key={c} style={{ fontFamily: F.body, fontSize: 13, color: L.onDeepPill }}>
                {shortOf(ov, c)} <strong style={{ fontFamily: F.num, color: L.paper, fontWeight: 600 }}>
                  {money(ov.headline.net_income[c])}</strong></span>
            ))}
          </div>
          <div style={{ padding: "18px 26px 26px", display: "grid", gap: 18 }}>
            <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
              <OkPill ok={!failing.length}>
                {failing.length ? `${failing.length} ${failing.length === 1 ? "check fails" : "checks fail"}: ${failing[0].label}`
                                : "Every check passes"}</OkPill>
              <Foot style={{ flex: 1, minWidth: 260 }}>
                Each decision moves money between the companies. Save one and the Statement, Corrections, Balance
                sheet and Intercompany all follow.
                {!canDecide && " Only an owner or admin changes these; you can read them."}
              </Foot>
            </div>
            <Partners d={d} ov={ov} canDecide={canDecide} onSaved={onSaved} />
            <div className="cd-two" style={{ display: "grid", gap: 18 }}>
              <Holdco d={d} ov={ov} canDecide={canDecide} onSaved={onSaved} />
              <Basis d={d} ov={ov} canDecide={canDecide} onSaved={onSaved} />
            </div>
            <Ads d={d} canDecide={canDecide} onSaved={onSaved} />
            <Staff d={d} canDecide={canDecide} onSaved={onSaved} />
            <Items kind="software" title="Software and subscriptions" d={d} canDecide={canDecide} onSaved={onSaved} />
            <Items kind="other" title="Other shared costs" d={d} canDecide={canDecide} onSaved={onSaved} />
            <Charges d={d} canDecide={canDecide} onSaved={onSaved} />
            <OnRecord d={d} />
          </div>
          <style>{`
            .cd-grid { display:grid; gap:10px; }
            .cd-two { grid-template-columns: repeat(2, minmax(0,1fr)); }
            .cd-click:hover { background:${L.totalBand} !important; }
            @media (max-width: 980px) { .cd-two { grid-template-columns: minmax(0,1fr); } }
          `}</style>
        </Panel>
      )}
    </StatePanel>
  );
}

