/* Books Cleanup · Forecast -- the months after the books end, for each projected company: the one the file
   projects (beCollective: its memberships still to be earned and collected), and the one the app projects from its
   own members' records (the Forum: each member's term, their renewals at the forecast's rate, any new members).
   What the plan and the assumptions cost, its partner's share as the year's profit builds, and what's been paid
   ahead of being earned (GET /cleanup/forward; services/books_cleanup.py Books.forward, services/cleanup_live.py).

   Every figure is the server's. The assumptions are saved like any decision -- owners and admins -- under the
   company's own path (`paths.forward`), and the page re-reads. */
import { useState } from "react";
import { StatePanel } from "../books/ui.jsx";
import { saveChanges, useCleanupForward } from "./useCleanup.js";
import { Bar, Button, Chip, EYEBROW, F, L, Loss, NUM, Note, NumberField, Panel, SaveLine, Sheet, dayLabel, inputStyle,
         keyLabel, money, reasonOf, shortOf } from "./kit.jsx";

/* Save one or more assumptions, then re-read everything (a projection moves the Statement and Cash flow too). */
export function useSaver(onSaved) {
  const [state, setState] = useState(null);
  const save = async (changes) => {
    setState({ busy: true });
    try { onSaved(await saveChanges(changes)); setState({ saved: "Saved." }); }
    catch (e) { setState({ error: reasonOf(e) }); }
  };
  return [state, save];
}

/* Which projected company a page shows: the one picked, else the first (the file's own). */
export function useProjected(fw) {
  const [co, setCo] = useState(null);
  const list = fw.data?.forwards || [];
  const f = list.find((x) => x.company === co) || list[0] || null;
  return [f, list, setCo];
}

/* The projected companies as chips on the page's bar, when there's more than one. */
export function ProjectedChips({ ov, list, f, setCo }) {
  return list.map((x) => (
    <Chip key={x.company} dark on={x.company === f?.company} onClick={list.length > 1 ? () => setCo(x.company) : undefined}
          title={x.source === "live" ? "Projected from the members' records in GHL and Stripe" : "Projected from the audit file"}>
      {shortOf(ov, x.company)}</Chip>));
}

export function Tile({ label, value, sub, loss }) {
  return (
    <div style={{ border: `1px solid ${L.ruleInner}`, borderRadius: 12, background: L.paper, padding: "12px 14px",
                  display: "flex", flexDirection: "column", gap: 4, minWidth: 0 }}>
      <div style={{ ...EYEBROW, color: L.ghost }}>{label}</div>
      <div style={{ fontFamily: F.num, fontVariantNumeric: "tabular-nums", fontSize: 21, color: L.deep, letterSpacing: "-.02em" }}>
        {money(value)}{loss ? <Loss v={value} size={12} /> : null}</div>
      {sub ? <div style={{ fontFamily: F.body, fontSize: 11.5, color: L.muted }}>{sub}</div> : null}
    </div>
  );
}

/* The revenue still to be earned, month by month: the dark part was already paid, the light part is still to come.
   A first month with nothing left in it (the books end on its last day) isn't drawn. */
function Bars({ schedule: all }) {
  const schedule = all.filter((r, i) => i > 0 || r.from_paid + r.from_future > 0);
  const max = Math.max(1, ...schedule.map((r) => r.from_paid + r.from_future));
  const W = 900, H = 190, bw = W / Math.max(1, schedule.length);
  return (
    <svg viewBox={`0 0 ${W} ${H + 34}`} style={{ width: "100%", height: "auto", display: "block" }} role="img"
         aria-label="Revenue still to be earned, month by month: already paid, and payments still to come">
      {schedule.map((r, i) => {
        const hp = (r.from_paid / max) * H, hf = (r.from_future / max) * H;
        return (
          <g key={r.key} transform={`translate(${i * bw + 6},0)`}>
            <rect x="0" y={H - hp - hf} width={bw - 12} height={hf} fill={L.accentMuted} opacity="0.55" rx="3" />
            <rect x="0" y={H - hp} width={bw - 12} height={hp} fill={L.accentInk} rx="3" />
            <text x={(bw - 12) / 2} y={H - hp - hf - 5} textAnchor="middle" fontSize="11" fontFamily={F.num} fill={L.bodyDim}>
              {Math.round((r.from_paid + r.from_future) / 1000)}k</text>
            <text x={(bw - 12) / 2} y={H + 16} textAnchor="middle" fontSize="10.5" fontFamily={F.num} fill={L.faint}>
              {i === 0 ? r.label.split(",")[0] : r.label.replace(" 20", " ’")}</text>
          </g>);
      })}
    </svg>
  );
}

const row = { display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10, flexWrap: "wrap" };

function Assumptions({ f, can, onSaved }) {
  const [state, save] = useSaver(onSaved);
  const a = f.assumptions;
  const at = (field) => [...f.paths.forward, field];
  const months = f.months;
  const live = f.source === "live";
  const setRetreats = (list) => save([{ path: at("retreats"), value: list }]);
  const sel = { ...inputStyle, fontFamily: F.body };
  const ret = f.retention;
  return (
    <Sheet title="Assumptions" right={can && f.meeting ? (
      <Button onClick={() => save([{ path: at("commission_pct"), value: f.meeting.commission_pct },
                                  { path: at("retreats"), value: a.retreats.map((r) => ({ ...r, on: !!f.meeting.retreats_on })) }])}>
        {f.meeting.label}</Button>) : null}>
      <div style={{ padding: "12px 18px", display: "grid", gap: 12, fontFamily: F.body, fontSize: 13, color: L.body }}>
        <div style={row}><span>Monthly costs, from Budget</span>
          <strong style={{ ...NUM, color: L.deep }}>{money(f.running)}
            {f.cost_per_member ? <span style={{ fontWeight: 400, color: L.muted }}> · {money(f.cost_per_member)} a member</span> : null}
          </strong></div>
        {"renew_pct" in a ? (
          <div style={{ display: "grid", gap: 3 }}>
            <div style={row}><span>Members who renew, of those due</span>
              <NumberField value={a.renew_pct} suffix="%" width={64} disabled={!can} label="Renewal rate"
                           onSave={(v) => save([{ path: at("renew_pct"), value: v }])} /></div>
            {ret ? <span style={{ fontSize: 11.5, color: L.ghost }}>
              Started from the last twelve months: {ret.lost12} of {ret.members + ret.lost12} members left.</span> : null}
          </div>) : null}
        {"new_members" in a ? (
          <div style={row}><span>New members a month</span>
            <span style={{ display: "inline-flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
              <NumberField value={a.new_members} width={60} disabled={!can} label="New members a month"
                           onSave={(v) => save([{ path: at("new_members"), value: v }])} /> at
              <NumberField value={a.new_value} prefix="$" width={86} disabled={!can} label="A new member's year"
                           onSave={(v) => save([{ path: at("new_value"), value: v }])} /> a year</span></div>) : null}
        {"commission_pct" in a ? (
          <div style={row}><span>Sales commissions, on installments collected</span>
            <NumberField value={a.commission_pct} suffix="%" width={64} disabled={!can} label="Commission percent"
                         onSave={(v) => save([{ path: at("commission_pct"), value: v }])} /></div>) : null}
        {a.retreats ? (
          <div style={{ display: "grid", gap: 6 }}>
            <span>Retreats</span>
            {a.retreats.map((r, i) => (
              <div key={i} style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                <input type="checkbox" checked={!!r.on} disabled={!can} aria-label={`Count ${r.label}`}
                       onChange={(e) => setRetreats(a.retreats.map((x, j) => (j === i ? { ...x, on: e.target.checked } : x)))} />
                <span style={{ flex: 1, minWidth: 140 }}>{r.label}</span>
                <select value={r.key} disabled={!can} aria-label={`${r.label}: month`} style={{ ...sel, width: 120 }}
                        onChange={(e) => setRetreats(a.retreats.map((x, j) => (j === i ? { ...x, key: e.target.value } : x)))}>
                  {months.map((m) => <option key={m.key} value={m.key}>{keyLabel(m.key)}</option>)}</select>
                <NumberField value={r.amount} prefix="$" width={86} disabled={!can} label={`${r.label}: cost`}
                             onSave={(v) => setRetreats(a.retreats.map((x, j) => (j === i ? { ...x, amount: v } : x)))} />
              </div>))}
          </div>) : null}
        {f.partner ? (
          <div style={row}><span>{f.partner}&rsquo;s pay</span>
            <span style={{ display: "inline-flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
              <NumberField value={a.partner_pay} prefix="$" width={86} disabled={!can} label={`${f.partner}'s pay a month`}
                           onSave={(v) => save([{ path: at("partner_pay"), value: v }])} /> a month through
              <select value={a.partner_end} disabled={!can} aria-label={`${f.partner}'s pay ends`} style={{ ...sel, width: 120 }}
                      onChange={(e) => save([{ path: at("partner_end"), value: e.target.value }])}>
                {months.map((m) => <option key={m.key} value={m.key}>{keyLabel(m.key)}</option>)}</select>
            </span></div>) : null}
        <div style={{ ...row, color: L.ghost, fontSize: 11.5 }}>
          <span>{live ? "Each renewal is paid the way that member pays today; new members pay the way today's do."
                      : "No new members are assumed. Everyone on a plan keeps paying."}</span><SaveLine state={state} /></div>
        {!can ? <div style={{ fontSize: 11.5, color: L.ghost }}>Only an owner or admin changes these.</div> : null}
      </div>
    </Sheet>
  );
}

/* Paid ahead of being earned -- the deferred revenue -- month by month from the contracts' own position, beside what
   the corrected books carry on their last day. For a projection from the members' records the two are different
   counts of the same thing, so the difference is shown: it's what to reconcile. */
function Deferred({ f }) {
  const d = f.deferred;
  const b = d.books;
  const live = f.source === "live";
  const booksNet = (b.deferred || 0) - (b.accrued || 0);
  const diff = d.start - booksNet;
  return (
    <Sheet title="Deferred revenue: paid, not yet earned">
      <div style={{ padding: "12px 18px 4px", display: "grid", gap: 10 }}>
        <Note tone={live && Math.abs(diff) >= 1 ? "gold" : "plain"}>
          On {dayLabel(b.as_of)} the corrected books carry <strong>{money(b.deferred)}</strong> of deferred revenue
          {b.accrued ? <> and {money(b.accrued)} earned but not yet received</> : null}.{" "}
          {live
            ? <>{d.start >= 0
                  ? <>By the members&rsquo; records, <strong>{money(d.start)}</strong> had been paid ahead of being earned</>
                  : <>By the members&rsquo; records, they had been counted as earning <strong>{money(-d.start)}</strong> more
                      than they&rsquo;d paid</>}
                {Math.abs(diff) >= 1 ? <>: <strong>{money(Math.abs(diff))}</strong> apart from the books, to reconcile. The
                  members below show where it comes from.</> : <>: the same as the books.</>}</>
            : <>The contracts here start from {money(d.start)} paid ahead of earned.</>}
        </Note>
      </div>
      <div style={{ overflowX: "auto" }}>
        <table style={{ borderCollapse: "collapse", width: "100%", minWidth: 620, fontFamily: F.body, fontSize: 12.5 }}>
          <thead><tr>{["Month", "Starts with", `+ ${f.labels?.in || "Paid in"}`, "− Earned", "Ends with"].map((h, i) => (
            <th key={h} style={{ ...EYEBROW, textAlign: i ? "right" : "left", padding: "8px 12px", background: L.colHead }}>{h}</th>))}
          </tr></thead>
          <tbody>{d.rows.map((r) => (
            <tr key={r.key} style={{ borderTop: `1px solid ${L.ruleLine}` }}>
              <td style={{ padding: "6px 12px", color: L.deep, fontWeight: 600 }}>{keyLabel(r.key)}</td>
              {[r.open, r.in, -r.earned, r.close].map((v, i) => (
                <td key={i} style={{ ...NUM, padding: "6px 12px", fontWeight: i === 3 ? 700 : 400,
                                     color: Math.abs(v) < 0.5 ? L.ghost : L.deep }}>{money(v)}</td>))}
            </tr>))}</tbody>
        </table>
      </div>
      <div style={{ padding: "8px 16px 12px", fontFamily: F.body, fontSize: 12, color: L.muted }}>
        Below zero, members have been counted as earning more than they&rsquo;ve paid: money still owed for time already
        served.</div>
    </Sheet>
  );
}

/* The members a projection from the records is made of, worst first: what each owes on their term, what they paid by
   the books' last day and since, what's still scheduled, and their renewal -- and who couldn't be placed. */
function Members({ f }) {
  const det = f.detail;
  const n = det.counts;
  const asOf = dayLabel(f.as_of);
  const plan = { pif: "In full", monthly: "Monthly", quarterly: "Quarterly", installments: "Installments" };
  const cell = { padding: "6px 10px", borderTop: `1px solid ${L.ruleLine}` };
  return (
    <Sheet title="The members behind this" right={<span style={{ fontFamily: F.body, fontSize: 12, color: L.muted }}>
      {det.members.length} counted{det.left_out.length ? ` · ${det.left_out.length} left out` : ""}
      {n.nothing ? ` · ${n.nothing} with nothing paid or scheduled` : ""}</span>}>
      <div style={{ padding: "10px 18px", display: "grid", gap: 8, fontFamily: F.body, fontSize: 12.5, color: L.body }}>
        <span>From the members&rsquo; records (GHL and Stripe) as they stand today. {n.joined ? `${n.joined} joined since the books end
          (${money(n.joined_value)} a year), counted in full. ` : ""}{n.renewed ? `${n.renewed} already renewed (${money(n.renewed_value)}). ` : ""}
          {n.due ? `${n.due} renewals are due in the year ahead (${money(n.due_value)} a year), counted at the renewal rate.` : ""}</span>
        {det.left_out.length ? (
          <Note tone="gold">Left out, for want of a renewal date or a contract value in the CRM:{" "}
            {det.left_out.map((x, i) => <span key={x.name + i}>{i ? ", " : ""}{x.source_url
              ? <a href={x.source_url} target="_blank" rel="noreferrer" style={{ color: "inherit" }}>{x.name}</a> : x.name} ({x.why})</span>)}.
          </Note>) : null}
      </div>
      <details>
        <summary style={{ cursor: "pointer", padding: "4px 18px 12px", fontFamily: F.body, fontSize: 12.5, color: L.accentInk,
                          fontWeight: 600 }}>Show every member</summary>
        <div style={{ overflowX: "auto" }}>
          <table style={{ borderCollapse: "collapse", width: "100%", minWidth: 960, fontFamily: F.body, fontSize: 12 }}>
            <thead><tr>{["Member", "Pays", "A year", "Term", `Paid by ${asOf}`, `Earned by ${asOf}`, "Paid since",
                         "Still scheduled", "Renewal", ""].map((h, i) => (
              <th key={h + i} style={{ ...EYEBROW, textAlign: [0, 1, 3, 8, 9].includes(i) ? "left" : "right",
                                       padding: "8px 10px", background: L.colHead }}>{h}</th>))}</tr></thead>
            <tbody>{det.members.map((m, i) => {
              const r = m.renewal;
              return (
                <tr key={m.name + i} style={{ background: m.flag ? L.flagBg : L.paper }}>
                  <td style={{ ...cell, color: L.deep, fontWeight: 600 }}>{m.source_url
                    ? <a href={m.source_url} target="_blank" rel="noreferrer" style={{ color: "inherit" }}>{m.name}</a> : m.name}</td>
                  <td style={cell}>{plan[m.plan] || "—"}</td>
                  <td style={{ ...cell, ...NUM }}>{money(m.value)}</td>
                  <td style={{ ...cell, whiteSpace: "nowrap" }}>{m.term ? `${dayLabel(m.term[0])} ’${m.term[0].slice(2, 4)} – ${dayLabel(m.term[1])} ’${m.term[1].slice(2, 4)}` : "—"}</td>
                  {[m.paid_by_end, m.earned_by_end, m.since, m.scheduled].map((v, j) => (
                    <td key={j} style={{ ...cell, ...NUM, color: v ? L.deep : L.ghost }}>{v === undefined ? "—" : money(v)}</td>))}
                  <td style={{ ...cell, whiteSpace: "nowrap" }}>{r ? `${dayLabel(r.date)} · ${r.state === "due" ? "due" : "renewed"}` : m.joined ? `Joined ${dayLabel(m.joined)}` : "—"}</td>
                  <td style={{ ...cell, color: L.flagInk }}>{m.flag || ""}</td>
                </tr>);
            })}</tbody>
          </table>
        </div>
      </details>
    </Sheet>
  );
}

export default function CleanupForecast({ ov, rev, onSaved }) {
  const fw = useCleanupForward(rev);
  const [f, list, setCo] = useProjected(fw);
  const can = !!fw.data?.can_decide;
  const short = (c) => shortOf(ov, c);
  const s = f?.summary;
  const through = f?.statement?.through;
  const live = f?.source === "live";
  return (
    <Panel>
      <Bar label="Forecast" right={f ? `${short(f.company)} · through ${through}` : ""}>
        <ProjectedChips ov={ov} list={list} f={f} setCo={setCo} />
      </Bar>
      <StatePanel loading={fw.loading} error={fw.error} retry={fw.retry}>
        {fw.data?.live_error ? <div style={{ padding: "14px 26px 0" }}><Note tone="red">{fw.data.live_error}</Note></div> : null}
        {fw.data && !f ? <div style={{ padding: 24, fontFamily: F.body, color: L.muted }}>This file projects nothing ahead.</div> : null}
        {f && (
          <div className="cf-page" style={{ padding: "18px 26px 26px", display: "grid", gap: 16 }}>
            <div className="cf-tiles5">
              <Tile label="Contracted" value={f.contracts.contracted} sub={`${f.contracts.members} members`} />
              <Tile label="Collected" value={f.contracts.collected} sub={live ? `by ${dayLabel(f.as_of)}` : "so far"} />
              <Tile label="Still to collect" value={f.contracts.still_to_collect}
                    sub={f.labels?.still_to_collect || "installments still to come"} />
              <Tile label="Earned so far" value={f.contracts.earned_so_far} sub="counted as income" />
              <Tile label="Still to earn" value={f.contracts.still_to_earn} sub="in the months ahead" />
            </div>
            <Note>
              <strong>In plain words:</strong> a membership counts as income a little each day for its 12 months. Of the
              {" "}{money(f.contracts.still_to_earn)} still to be counted, the dark part of each bar is money members already
              paid (it&rsquo;s in the bank, or was spent); the light part comes from {live ? "payments they haven’t made"
                : "installments they haven’t paid"} yet.
              {live ? <> These are the memberships in hand on {dayLabel(f.as_of)} and since; renewals and new members come on
                top, at the rates under Assumptions.</> : null}
            </Note>
            <Sheet title="Revenue still to be earned, month by month">
              <div style={{ padding: "8px 14px 4px", overflowX: "auto" }}>
                <div style={{ minWidth: 560 }}><Bars schedule={f.schedule} /></div></div>
              <div style={{ display: "flex", gap: 16, padding: "0 18px 12px", fontFamily: F.body, fontSize: 12, color: L.bodyDim }}>
                <span><span style={{ display: "inline-block", width: 10, height: 10, background: L.accentInk, borderRadius: 2 }} /> already paid</span>
                <span><span style={{ display: "inline-block", width: 10, height: 10, background: L.accentMuted, opacity: 0.55, borderRadius: 2 }} />
                  {" "}{live ? "payments still to come" : "installments still to come"}</span>
              </div>
            </Sheet>
            <div className="cf-two">
              <Assumptions f={f} can={can} onSaved={onSaved} />
              <Sheet title={`By ${through}`}>
                <div style={{ padding: "12px 18px", display: "grid", gap: 8, fontFamily: F.body, fontSize: 13.5, color: L.deep }}>
                  {[[`${short(f.company)}'s operating profit, ${keyLabel(f.rows[0]?.key)} – ${through}`, s.profit, true],
                    ...(f.partner ? [[`${f.partner} earns (${Math.round(f.rate * 100)}% of the profit)`, s.earned],
                                     [`${f.partner} is paid`, s.paid], ["Paid beyond the share earned", s.ahead]] : [])
                  ].map(([label, v, loss], i, all) => (
                    <div key={label} style={{ ...row, borderTop: i === all.length - 1 && f.partner ? `1.5px solid ${L.deep}` : "none",
                                              paddingTop: i === all.length - 1 && f.partner ? 8 : 0 }}>
                      <span style={{ fontWeight: i === all.length - 1 && f.partner ? 700 : 400 }}>{label}</span>
                      <span style={{ ...NUM, fontWeight: 600, color: i === all.length - 1 && v > 0 ? L.loss : L.deep }}>
                        {money(v)}{loss ? <Loss v={v} /> : null}</span>
                    </div>))}
                  {f.partner ? (
                    <Note tone={s.ahead > 0 ? "red" : "plain"} style={{ marginTop: 6 }}>
                      {s.ahead > 0
                        ? `At these assumptions ${f.partner} would be paid ${money(s.ahead)} more than the share earned by ${through}: an advance to be paid back.`
                        : `At these assumptions ${f.partner}'s share covers what's been paid.`}</Note>) : null}
                  {f.renewals ? (
                    <div style={{ ...row, fontSize: 12.5, color: L.bodyDim, borderTop: `1px solid ${L.ruleLine}`, paddingTop: 8 }}>
                      <span>{f.renewals.count} renewals due ({money(f.renewals.value)} a year), at {f.assumptions.renew_pct}%: earned in these months</span>
                      <span style={NUM}>{money(f.renewals.earned)}</span></div>) : null}
                  {f.joins && f.joins.members ? (
                    <div style={{ ...row, fontSize: 12.5, color: L.bodyDim }}>
                      <span>{Math.round(f.joins.members)} new members: earned in these months</span>
                      <span style={NUM}>{money(f.joins.earned)}</span></div>) : null}
                </div>
              </Sheet>
            </div>
            <Sheet title="Month by month" right={<span style={{ fontFamily: F.body, fontSize: 12, color: L.muted }}>
              shaded rows are projected</span>}>
              <div style={{ overflowX: "auto" }}>
                <table style={{ borderCollapse: "collapse", width: "100%", minWidth: 880, fontFamily: F.body, fontSize: 12.5 }}>
                  <thead><tr>{["Month", "Revenue", "Costs", "Profit", "Year so far",
                               ...(f.partner ? [`${f.partner}'s ${Math.round(f.rate * 100)}%`, "Earned so far", "Paid so far", "Paid beyond earned"] : [])]
                    .map((h, i) => <th key={h} style={{ ...EYEBROW, textAlign: i ? "right" : "left", padding: "8px 12px",
                                                       background: L.colHead }}>{h}</th>)}</tr></thead>
                  <tbody>{f.rows.map((r) => (
                    <tr key={r.key} style={{ borderTop: `1px solid ${L.ruleLine}`, background: r.projected ? L.infoBg : L.paper }}>
                      <td style={{ padding: "6px 12px", color: L.deep, fontWeight: 600 }}>{keyLabel(r.key)}</td>
                      {[r.rev, r.cost, r.profit, r.ytd, ...(f.partner ? [r.share, r.earned, r.paid, r.ahead] : [])].map((v, i) => (
                        <td key={i} style={{ ...NUM, padding: "6px 12px", color: [2, 3].includes(i) && v < 0 ? L.loss : L.deep }}>
                          {money(v)}</td>))}
                    </tr>))}</tbody>
                </table>
              </div>
              <div style={{ padding: "8px 16px 12px", fontFamily: F.body, fontSize: 12, color: L.muted }}>
                The month the books end in is actual so far and projected after. {f.partner ? `${f.partner}'s share counts only once the year's profit is above zero; each new year starts fresh.` : ""}</div>
            </Sheet>
            <Deferred f={f} />
            {f.launches.length ? (
              <Sheet title="What each launch cost, and what it contracted">
                <div className="cf-two" style={{ padding: "12px 16px" }}>
                  {f.launches.map((l) => (
                    <div key={l.name} style={{ border: `1px solid ${L.ruleInner}`, borderRadius: 12, padding: "12px 14px",
                                               fontFamily: F.body, fontSize: 13, color: L.body }}>
                      <div style={{ fontFamily: F.num, fontWeight: 700, color: L.deep }}>{l.name}</div>
                      <div style={{ fontSize: 12, color: L.muted }}>{l.window} · {l.members} members</div>
                      <div style={{ ...row, marginTop: 8 }}><span>Contracted</span><strong style={NUM}>{money(l.contracted)}</strong></div>
                      {Object.entries(l.costs).map(([k, v]) => (
                        <div key={k} style={{ ...row, fontSize: 12.5, color: L.bodyDim }}><span>{k}</span><span style={NUM}>{money(v)}</span></div>))}
                      <div style={{ ...row, borderTop: `1px solid ${L.ruleInner}`, marginTop: 6, paddingTop: 6 }}>
                        <span>Launch costs</span><strong style={NUM}>{money(l.cost_total)}</strong></div>
                      {l.per_dollar ? <div style={{ fontSize: 12.5, color: L.accentInk, marginTop: 6 }}>
                        ${l.per_dollar.toFixed(2)} contracted for every $1 of launch cost</div> : null}
                    </div>))}
                </div>
                <div style={{ padding: "0 16px 12px", fontFamily: F.body, fontSize: 12, color: L.muted }}>
                  Launches by date window: members&rsquo; first payments, and the ads, commissions, gifts and events in the
                  same months.</div>
              </Sheet>) : null}
            {f.detail ? <Members f={f} /> : null}
          </div>
        )}
      </StatePanel>
      <style>{CF_CSS}</style>
    </Panel>
  );
}

export const CF_CSS = `
  .cf-tiles5 { display:grid; grid-template-columns: repeat(5, minmax(0,1fr)); gap:12px; }
  .cf-tiles4 { display:grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap:12px; }
  .cf-two { display:grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap:14px; }
  @media (max-width: 1080px) { .cf-tiles5, .cf-tiles4 { grid-template-columns: repeat(2, minmax(0,1fr)); }
                               .cf-two { grid-template-columns: minmax(0,1fr); } }
  @media (max-width: 560px) { .cf-page { padding: 14px 10px 18px !important; } .cf-tiles5, .cf-tiles4 { grid-template-columns: minmax(0,1fr); } }
`;
