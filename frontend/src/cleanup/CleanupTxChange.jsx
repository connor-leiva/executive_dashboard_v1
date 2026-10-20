/* Books Cleanup -- changing one transaction, from the card under its row (CleanupTx.jsx).

   What the owners do most (Oct 19) isn't recategorizing: it's deciding which company a charge belongs to.
   So the card offers Move it to another company, Split it between companies, Personal (not business) and
   Not a real charge, with another line or another month under "More…".

   The SERVER applies a change (PUT /cleanup/inputs, path ["tx", key]): it lays it over the reviewed ledger
   and recomputes the corrected books and the entries, every entry still balancing. This file only says, in
   one sentence and before anything is saved, what the change will do -- including who will owe whom -- and
   once it's saved, shows it with Undo. Owners and admins only; everyone else reads. */
import { useContext, useState } from "react";
import { saveChanges } from "./useCleanup.js";
import { Button, Chip, CleanupCtx, EYEBROW, F, L, NUM, Note, cents, inputStyle, reasonOf, shortOf } from "./kit.jsx";

/* Names, from the overview: a company's short name, a brand's company, and a brand named with its company
   when they differ ("The Edge (SB Coaching)"). */
export function useNames() {
  const ctx = useContext(CleanupCtx) || {};
  const ov = ctx.ov || {};
  const short = (c) => shortOf(ov, c);
  const coOf = (b) => (ov.brand_company || {})[b];
  const brandName = (b) => {
    const c = coOf(b);
    return c && short(c) !== b ? `${b} (${short(c)})` : b;
  };
  return { ctx, ov, short, coOf, brandName };
}

const monthLabel = (ov, m) => ((ov.period?.months || []).find((x) => x.m === Number(m)) || {}).label || String(m);

/* A saved change in two or three words, for the row ("→ The Forum", "Split 2 ways"). */
export function changeLabel(ch, ov) {
  if (!ch) return "";
  switch (ch.do) {
    case "move": return ch.to === "billback" ? `→ ${ov.billback_name || "billed back"}` : `→ ${ch.to}`;
    case "split": return `Split ${Object.keys(ch.parts || {}).length} ways`;
    case "personal": return `${ch.who && ch.who !== "owner" ? ch.who : ov.salary_owner || "The owner"}'s personal`;
    case "remove": return { paid_twice: "Paid twice: owed back", entered_twice: "Entered twice: out",
                            never: "Never happened: out" }[ch.reason] || "Taken out";
    case "line": return `→ ${ch.line}`;
    case "month": return `→ ${monthLabel(ov, ch.month)}`;
    default: return "Your change";
  }
}

/* ...and in a sentence, for the card. */
function changeSaid(ch, ov) {
  const pct = (f) => `${Math.round(f * 1000) / 10}%`;
  switch (ch.do) {
    case "move": return ch.to === "billback" ? `Billed back to ${ov.billback_name}.` : `Moved to ${ch.to}.`;
    case "split": return `Split: ${Object.entries(ch.parts || {}).map(([b, f]) => `${b} ${pct(f)}`).join(", ")}.`;
    case "personal": return ch.who && ch.who !== "owner" ? `${ch.who}'s personal: owed back.`
      : `${ov.salary_owner || "The owner"}'s personal: an owner's draw, not a business cost.`;
    case "remove": return { paid_twice: "Paid twice: the second payment is owed back.",
                            entered_twice: "Entered twice in QuickBooks: the extra copy is out.",
                            never: "Never happened: it's out of the books." }[ch.reason] || "Taken out.";
    case "line": return `Filed to ${ch.line}.`;
    case "month": return `Counted in ${monthLabel(ov, ch.month)}.`;
    default: return "Changed.";
  }
}

/* The amount a change moves: the whole transaction, not just the part on one line. */
const amountOf = (r) => (r.full != null ? r.full : Math.abs(r.gone ? r.was : r.amount));
/* The companies carrying it now (a shared cost sits on the company that paid it). */
const fromOf = (r, coOf) => ((r.brands || []).length ? [...new Set(r.brands.map(coOf))] : [r.company]);
const offText = (from, short) => (from.length === 1 ? `${short(from[0])}'s books` : "the companies sharing it");

/* Who owes whom once `toCo` carries `A` of it: the company that paid (or collected) it is square with
   whoever carries it only through intercompany. */
function owesText(r, toCo, A, short) {
  if (!toCo || toCo === r.company) return "";
  return r.moneyIn
    ? ` ${short(r.company)} collected it, so ${short(r.company)} owes ${short(toCo)} ${cents(A)}.`
    : ` ${short(r.company)} paid it, so ${short(toCo)} owes ${short(r.company)} ${cents(A)}.`;
}
function noLongerText(r, from, toCos, short) {
  if (from.length !== 1 || from[0] === r.company || toCos.includes(from[0])) return "";
  return r.moneyIn ? ` ${short(r.company)} no longer owes ${short(from[0])} for it.`
    : ` ${short(from[0])} no longer owes ${short(r.company)} for it.`;
}

/* ── the forms ───────────────────────────────────────────────────────────────────────────── */

function FormShell({ title, r, value, preview, close, children }) {
  const { ctx } = useNames();
  const [why, setWhy] = useState("");
  const [state, setState] = useState(null);
  const save = async () => {
    setState({ busy: true });
    try {
      await saveChanges([{ path: ["tx", r.key], value: { ...value, why: why.trim() } }]);
      close();
      if (ctx.onSaved) ctx.onSaved();
    } catch (e) { setState({ error: reasonOf(e) }); }
  };
  return (
    <div className="cx-form">
      <div style={{ fontFamily: F.num, fontWeight: 700, color: L.deep, fontSize: 13.5 }}>{title}</div>
      {children}
      <label style={{ display: "grid", gap: 4 }}>
        <span style={EYEBROW}>Why (optional: it goes in the record)</span>
        <input value={why} onChange={(e) => setWhy(e.target.value)} maxLength={500}
               placeholder="e.g. Spring, Oct 19: Courtney's project" style={{ ...inputStyle, fontFamily: F.body }} />
      </label>
      {preview ? <Note tone="blue"><strong>What happens:</strong> {preview}</Note> : null}
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <span role="status" style={{ flex: 1, minWidth: 180, fontFamily: F.body, fontSize: 12,
                                     color: state?.error ? L.loss : L.ghost }}>
          {state?.error || "It changes the books for everyone on the tab, and it can be undone."}</span>
        <Button onClick={close}>Cancel</Button>
        <Button primary disabled={!value || !preview || state?.busy} onClick={save}>{state?.busy ? "Saving…" : "Save"}</Button>
      </div>
    </div>
  );
}

function MoveForm({ r, close }) {
  const { ov, short, coOf, brandName } = useNames();
  const [to, setTo] = useState(null);
  const A = amountOf(r);
  const from = fromOf(r, coOf);
  const here = (r.brands || []).length === 1 ? r.brands[0] : null;
  let preview = null;
  if (to === "billback") {
    preview = `It comes off ${offText(from, short)}. ${short(r.company)} bills it to ${ov.billback_name}, which owes `
      + `${short(r.company)} ${cents(A)}.${noLongerText(r, from, [r.company], short)}`;
  } else if (to) {
    const toCo = coOf(to);
    preview = (from.length === 1 && from[0] === toCo
      ? `It stays on ${short(toCo)}'s books, as ${to}'s.`
      : `It comes off ${offText(from, short)} and goes on ${brandName(to)}'s.`)
      + owesText(r, toCo, A, short) + noLongerText(r, from, [toCo], short);
  }
  return (
    <FormShell title="Move it to another company" r={r} close={close} value={to && { do: "move", to }} preview={preview}>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        {(ov.brands || []).filter((b) => b !== here).map((b) => (
          <Chip key={b} on={to === b} onClick={() => setTo(b)}>{brandName(b)}</Chip>))}
        {ov.billback_name && !r.moneyIn ? (
          <Chip on={to === "billback"} onClick={() => setTo("billback")}>{`${ov.billback_name} (bill it back)`}</Chip>) : null}
      </div>
      {r.full != null ? <div style={{ fontFamily: F.body, fontSize: 12, color: L.muted }}>
        The whole transaction moves, {cents(A)}, not just the part on this line.</div> : null}
    </FormShell>
  );
}

function SplitForm({ r, close }) {
  const { ov, short, coOf, brandName } = useNames();
  const brands = ov.brands || [];
  const here = (r.brands || []).length === 1 ? r.brands[0] : null;
  const [pct, setPct] = useState(() => Object.fromEntries(brands.map((b) => [b, b === here ? 50 : 0])));
  const A = amountOf(r);
  const total = Math.round(brands.reduce((n, b) => n + (Number(pct[b]) || 0), 0) * 10) / 10;
  const on = brands.filter((b) => Number(pct[b]) > 0);
  const part = (b) => Math.round(A * (Number(pct[b]) || 0)) / 100;
  const valid = total === 100 && on.length >= 2;
  const byCo = {};
  on.forEach((b) => { byCo[coOf(b)] = (byCo[coOf(b)] || 0) + part(b); });
  const preview = valid ? `Each company carries its part: ${on.map((b) => `${brandName(b)} ${cents(part(b))}`).join(", ")}.`
    + Object.entries(byCo).map(([c, v]) => owesText(r, c, v, short)).join("")
    + noLongerText(r, fromOf(r, coOf), Object.keys(byCo), short) : null;
  return (
    <FormShell title="Split it between companies" r={r} close={close} preview={preview}
               value={valid && { do: "split", parts: Object.fromEntries(on.map((b) => [b, Number(pct[b]) / 100])) }}>
      <div style={{ display: "grid", gap: 6, maxWidth: 460 }}>
        {brands.map((b) => (
          <div key={b} style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) 92px 104px", gap: 10, alignItems: "center" }}>
            <span style={{ fontFamily: F.body, fontSize: 13, color: L.deep }}>{brandName(b)}</span>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
              <input type="number" min={0} max={100} step={0.1} value={pct[b]} aria-label={`${b}'s share, percent`}
                     onChange={(e) => setPct({ ...pct, [b]: Math.max(0, Math.min(100, Number(e.target.value) || 0)) })}
                     style={{ ...inputStyle, width: 70, textAlign: "right" }} />
              <span style={{ fontFamily: F.body, fontSize: 12.5, color: L.muted }}>%</span>
            </span>
            <span style={{ ...NUM, fontSize: 13, color: Number(pct[b]) > 0 ? L.deep : L.ghost }}>{cents(part(b))}</span>
          </div>))}
        <div style={{ fontFamily: F.body, fontSize: 12, color: total === 100 ? L.accentInk : L.loss }}>
          Total {total}%{total === 100 ? "" : ": it needs to come to 100%"}</div>
      </div>
    </FormShell>
  );
}

function PersonalForm({ r, close }) {
  const { ov, short, coOf } = useNames();
  const [who, setWho] = useState("owner");
  const [name, setName] = useState("");
  const owner = ov.salary_owner || "The owner";
  const person = who === "owner" ? "owner" : name.trim();
  const off = `It comes off ${offText(fromOf(r, coOf), short)}.`;
  const preview = !person ? null : who === "owner"
    ? `${off} It counts as money ${owner} took out of ${short(r.company)} (an owner's draw), not as a business cost.`
    : `${off} ${person} owes ${short(r.company)} ${cents(amountOf(r))} for it.`;
  return (
    <FormShell title="Personal, not a business cost" r={r} close={close} preview={preview}
               value={person && { do: "personal", who: person }}>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
        <Chip on={who === "owner"} onClick={() => setWho("owner")}>{`${owner}'s`}</Chip>
        <Chip on={who === "other"} onClick={() => setWho("other")}>Someone else&rsquo;s</Chip>
        {who === "other" ? <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Their name"
                                  maxLength={80} aria-label="Whose it was"
                                  style={{ ...inputStyle, fontFamily: F.body, width: 180 }} /> : null}
      </div>
    </FormShell>
  );
}

function RemoveForm({ r, close }) {
  const { short, coOf } = useNames();
  const reasons = r.moneyIn
    ? [["entered_twice", "It was entered twice in QuickBooks"], ["never", "It never came in"]]
    : r.cleared ? [["paid_twice", "It was paid twice: the bank shows both"]]
      : [["entered_twice", "It was entered twice in QuickBooks"], ["never", "It never happened"], ["paid_twice", "It was paid twice"]];
  const [reason, setReason] = useState(reasons.length === 1 ? reasons[0][0] : null);
  const off = `It comes off ${offText(fromOf(r, coOf), short)}`;
  const preview = reason === "paid_twice" ? `${off}: the second payment is money ${r.payee || "the payee"} owes ${short(r.company)} back.`
    : reason === "entered_twice" ? `${off}, and the extra copy comes out of ${short(r.company)}'s bank account in the books (the bank shows it once).`
      : reason === "never" ? `${off}, and out of ${short(r.company)}'s bank account in the books.` : null;
  return (
    <FormShell title={r.moneyIn ? "Not real money in" : "Not a real charge"} r={r} close={close} preview={preview}
               value={reason && { do: "remove", reason }}>
      {r.cleared && !r.moneyIn ? <div style={{ fontFamily: F.body, fontSize: 12.5, color: L.muted }}>
        The bank shows this one, so it happened.</div> : null}
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        {reasons.map(([k, label]) => <Chip key={k} on={reason === k} onClick={() => setReason(k)}>{label}</Chip>)}
      </div>
    </FormShell>
  );
}

function LineForm({ r, close }) {
  const { ov } = useNames();
  const [line, setLine] = useState("");
  const lines = ((r.moneyIn ? ov.lines?.revenue : ov.lines?.costs) || []).filter((l) => l !== r.line);
  const preview = line ? `Same company. It moves from “${r.line}” to “${line}”.` : null;
  return (
    <FormShell title="Change the category" r={r} close={close} preview={preview} value={line && { do: "line", line }}>
      <select value={line} onChange={(e) => setLine(e.target.value)} aria-label="The line it belongs on"
              style={{ ...inputStyle, fontFamily: F.body, maxWidth: 360 }}>
        <option value="">Pick a line…</option>
        {lines.map((l) => <option key={l} value={l}>{l}</option>)}
      </select>
    </FormShell>
  );
}

function MonthForm({ r, close }) {
  const { ov } = useNames();
  const [m, setM] = useState("");
  const preview = m ? `Same company and line. It counts in ${monthLabel(ov, m)} instead of ${monthLabel(ov, r.month)}.` : null;
  return (
    <FormShell title="Change the month" r={r} close={close} preview={preview} value={m && { do: "month", month: Number(m) }}>
      <select value={m} onChange={(e) => setM(e.target.value)} aria-label="The month it counts in"
              style={{ ...inputStyle, fontFamily: F.body, maxWidth: 220 }}>
        <option value="">Pick a month…</option>
        {(ov.period?.months || []).filter((x) => x.m !== r.month).map((x) => <option key={x.m} value={x.m}>{x.label}</option>)}
      </select>
    </FormShell>
  );
}

const FORMS = { move: MoveForm, split: SplitForm, personal: PersonalForm, remove: RemoveForm, line: LineForm,
                month: MonthForm };

/* ── what the card shows: a saved change with Undo, or the actions ─────────────────────── */

export function ChangeArea({ r }) {
  const { ctx, ov } = useNames();
  const [act, setAct] = useState(null);
  const [more, setMore] = useState(false);
  const [state, setState] = useState(null);
  const can = !!ctx.canDecide;
  if (r.kind === "adj") {
    return ["staff", "item", "ads", "fees", "billback"].includes(r.src) && ctx.goto ? (
      <div className="cx-acts">
        <button className="cx-act" onClick={() => ctx.goto("decisions")}>Change this split on Decisions →</button>
      </div>) : null;
  }
  if (!r.key) return null;
  if (r.transfer && !r.change) {
    return <div style={{ marginTop: 10, fontFamily: F.body, fontSize: 12.5, color: L.muted }}>
      Money moving between accounts isn&rsquo;t income or a cost, so there&rsquo;s no company&rsquo;s results to move it on.</div>;
  }
  const undo = async () => {
    setState({ busy: true });
    try {
      await saveChanges([{ path: ["tx", r.key], value: null }]);
      setState(null);
      if (ctx.onSaved) ctx.onSaved();
    } catch (e) { setState({ error: reasonOf(e) }); }
  };
  if (r.change) {
    return (
      <Note tone="green" style={{ marginTop: 12, display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
        <span style={{ flex: 1, minWidth: 220 }}>
          <strong>Your change:</strong> {changeSaid(r.change, ov)}{r.change.why ? ` Why: “${r.change.why}”.` : ""}
          <span style={{ display: "block", fontSize: 11.5, color: L.muted, marginTop: 2 }}>
            {[r.change.by, r.change.at ? r.change.at.slice(0, 10) : ""].filter(Boolean).join(", ")}</span>
          {state?.error ? <span style={{ display: "block", color: L.loss }}>{state.error}</span> : null}
        </span>
        {can ? <Button onClick={undo} disabled={state?.busy}>{state?.busy ? "Undoing…" : "Undo"}</Button> : null}
      </Note>
    );
  }
  if (!can) {
    return <div style={{ marginTop: 10, fontFamily: F.body, fontSize: 12.5, color: L.muted }}>
      Moving a transaction is an owner&rsquo;s or admin&rsquo;s to do.</div>;
  }
  const acts = [["move", "Move to another company"], ["split", "Split between companies"]];
  if (!r.moneyIn) acts.push(["personal", "Personal, not business"]);
  if (!(r.moneyIn && r.cleared)) acts.push(["remove", r.moneyIn ? "Not real money in" : "Not a real charge"]);
  if (more) {
    if (!r.shared && !r.pooled) acts.push(["line", "Change the category"]);
    if (ov.can_move_month) acts.push(["month", "Change the month"]);
  }
  const Form = act ? FORMS[act] : null;
  return (
    <>
      <div className="cx-acts">
        {acts.map(([k, label]) => (
          <button key={k} className={`cx-act${act === k ? " on" : ""}`} aria-pressed={act === k}
                  onClick={() => setAct(act === k ? null : k)}>{label}</button>))}
        {!more ? <button className="cx-act ghost" onClick={() => setMore(true)}>More…</button> : null}
      </div>
      {Form ? <Form key={act} r={r} close={() => setAct(null)} /> : null}
    </>
  );
}
