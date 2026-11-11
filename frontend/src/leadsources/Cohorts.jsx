/* Lead sources › Cohorts (docs/specs/ulrg-reporting-dashboard-port.md §2.4, §4.2–4.3, §4.7).

   One row per month a lead ARRIVED, followed for as long as it takes to close. That is the
   difference from the brokerage page's lead-to-close panel, which counts each step in the period it
   happened -- a February lead that closes in May is a May close there and a February close here.

   EVERY FIGURE IS THE SERVER'S. Conversion is hidden under three months and muted to six because
   the server says `show_conversion` / `muted_conversion`; spend and ROI are blank, never $0, when
   the QuickBooks mapping is not set, because the server sends null.

   METHOD NOTES LIVE IN THE INFO GLYPHS beside the labels they explain, not in grey paragraphs
   under the cards; the empty states are LeadSources.jsx's, one for every view. */
import { Link, useNavigate } from "react-router-dom";
import { T, alpha } from "../theme.js";
import { Card, OpTile, PanelLabel, Source, SourceTag, StatTile } from "../panels.jsx";
import { CardHead, ExportButton, FB, FD, FM, Note, OutlineButton, dayLabel, downloadCsv, fmt, monthLabel, th } from "./kit.jsx";

const SPEED_TONES = ["u5min", "u30min", "u2hr", "u24hr", "u72hr", "o72hr"];
// The evergreen ramp, darkest = fastest, and the one accent for leads nobody ever contacted.
const speedColor = (k) => (k === "never" ? T.poppy : alpha(T.evergreen, [1, 0.78, 0.6, 0.44, 0.3, 0.18][SPEED_TONES.indexOf(k)]));

/* How far along a cohort is, as a word under its month. Muted for all of them: "Early" is a fact
   about age, not a warning. */
function maturity(r) {
  if (r.spend_only) return "No leads";
  if (r.is_mtd) return "This month";
  if (r.industry_comparable) return "Comparable";
  if (!r.show_conversion) return "Too new";
  if (r.muted_conversion) return "Early";
  return "Maturing";
}

function conversion(r) {
  if (r.spend_only) return { text: "—", color: T.muted, title: "No leads arrived this month" };
  if (!r.show_conversion) return { text: "—", color: T.muted, title: "Too new to read: conversion shows from three months" };
  return { text: fmt.pct(r.conversion_to_date_pct), color: r.muted_conversion ? T.muted : T.ink,
           title: r.muted_conversion ? "Early: most of this cohort's closes haven't happened yet" : undefined };
}

const COLUMNS = [
  { key: "cohort_month", label: "Cohort" },
  { label: "Maturity", value: (r) => maturity(r) },
  { key: "leads_received", label: "Leads" }, { key: "contacted_count", label: "Contacted" },
  { key: "contacted_pct", label: "Contact %" }, { key: "avg_time_to_contact_minutes", label: "Avg first touch (min)" },
  { key: "pct_contacted_5min", label: "Under 5 min %" }, { key: "pct_contacted_24hr", label: "Within 24 hr %" },
  { key: "appts_set_count", label: "Appts set" }, { key: "appts_set_pct", label: "Set %" },
  { key: "appts_held_count", label: "Held" }, { key: "appts_held_pct", label: "Held %" },
  { key: "under_contract_count", label: "Under contract" }, { key: "closed_count", label: "Closed (FUB stage)" },
  { key: "dead_count", label: "Dead" }, { key: "active_count", label: "Active" },
  { key: "sisu_matched_count", label: "Sisu matched" }, { key: "sisu_closed_count", label: "Sisu closed" },
  { key: "sisu_under_contract_count", label: "Sisu under contract" },
  { key: "sisu_company_dollar_dollars", label: "Company dollar" }, { key: "sisu_company_take_pct", label: "Company take %" },
  { key: "sisu_gci_dollars", label: "GCI" }, { key: "sisu_volume_dollars", label: "Volume" },
  { key: "gross_spend", label: "QuickBooks spend" }, { key: "net_spend", label: "Net spend" },
  { key: "roi_gross", label: "ROI (gross)" }, { key: "roi_net", label: "ROI (net)" },
  { key: "conversion_to_date_pct", label: "Conversion to date %", value: (r) => (r.show_conversion ? r.conversion_to_date_pct : "") },
  { key: "cohort_age_months", label: "Age (months)" },
];

const TABLE_NOTE = "Leads stay in the month they arrived, including junk and Trash, and keep their close however late it "
  + "lands. Conversion shows from three months and is muted until six; a cohort is comparable to industry rates at twelve. "
  + "Gold marks a month slower than 30 minutes on average, or under 85% reached within a day. Select a row for its leads.";

/* Eighteen columns, sized to fit a 1440 screen without scrolling: maturity rides under the month,
   and QuickBooks shows net spend, with the gross figure in the cell's tooltip. The CSV keeps all of it. */
function CohortTable({ rows, header, spend, onRow, filename, whole }) {
  const sticky = { position: "sticky", left: 0, zIndex: 1, background: T.white, textAlign: "left" };
  const groups = [["", 2], ["Contact", 4], ["Appointments", 2], ["Follow Up Boss stage", 4], ["Sisu", 3], ["QuickBooks", 2], ["", 1]];
  const heads = ["Cohort", "Leads", "Contacted", "Avg first touch", "Under 5 min", "Within 24 hr", "Set", "Held",
                 "Under contract", "Closed", "Dead", "Active", "Matched", "Closed", "Company dollar",
                 spend.share_pct ? "Net spend" : "Spend", "ROI", "Conversion"];
  const watch = (on) => (on ? { color: T.daffodilText, fontWeight: 600 } : {});
  const dash = (r, v) => (r.spend_only ? "—" : v);
  const tdc = { fontFamily: FB, fontSize: 13, color: T.ink, textAlign: "right", fontVariantNumeric: "tabular-nums",
                whiteSpace: "nowrap", borderBottom: `1px solid ${T.line}`, padding: "8px 6px" };
  const thc = { ...th, padding: "6px 6px" };
  const last = heads.length - 1;
  return (
    <Card style={{ padding: 0, overflow: "hidden" }}>
      <div style={{ padding: "20px 20px 0" }}>
        <CardHead right={<>
          <Source name="Follow Up Boss" /><Source name="Sisu" /><Source name="QuickBooks" />
          <ExportButton onClick={() => downloadCsv(filename, COLUMNS, rows)} disabled={!rows.length} />
        </>}>
          <PanelLabel accent={T.meadow} info={TABLE_NOTE}>Cohort performance</PanelLabel>
        </CardHead>
      </div>
      <div style={{ overflowX: "auto", WebkitOverflowScrolling: "touch" }}>
        <table style={{ width: "100%", minWidth: 1000, borderCollapse: "separate", borderSpacing: 0 }}>
          <thead>
            <tr>
              {groups.map(([g, span], i) => (
                <th key={i} colSpan={span} style={{ ...thc, ...(i === 0 ? sticky : {}), textAlign: "center", color: T.tertiary,
                  fontWeight: 500, borderBottom: g ? `1px solid ${T.line}` : "none", padding: "0 6px 6px" }}>{g}</th>
              ))}
            </tr>
            <tr>
              {heads.map((h, i) => (
                <th key={i} style={{ ...thc, ...(i === 0 ? { ...sticky, paddingLeft: 20 } : {}), textAlign: i === 0 ? "left" : "right",
                  whiteSpace: "normal", verticalAlign: "bottom", lineHeight: 1.3, paddingRight: i === last ? 20 : 6 }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const c = conversion(r);
              return (
                <tr key={r.cohort_key} onClick={r.spend_only ? undefined : () => onRow(r)} className={r.spend_only ? undefined : "ls-row"}
                    style={{ cursor: r.spend_only ? "default" : "pointer" }} title={r.spend_only ? undefined : `The ${r.leads_received} leads behind ${r.cohort_month}`}>
                  <td style={{ ...tdc, ...sticky, paddingLeft: 20 }} title={r.spend_only ? undefined : `${r.cohort_age_months} months old`}>
                    <div style={{ fontWeight: 600 }}>{r.cohort_month}</div>
                    <div style={{ fontSize: 12, color: T.muted, marginTop: 2 }}>{maturity(r)}</div>
                  </td>
                  <td style={tdc}>{dash(r, fmt.count(r.leads_received))}</td>
                  <td style={tdc} title={`${r.contacted_count} of ${r.leads_received}`}>{dash(r, fmt.pct(r.contacted_pct))}</td>
                  <td style={{ ...tdc, ...watch(r.flag_slow_contact) }} title={r.flag_slow_contact ? "Average first touch is over 30 minutes" : undefined}>{fmt.minutes(r.avg_time_to_contact_minutes)}</td>
                  <td style={tdc}>{dash(r, fmt.pct(r.pct_contacted_5min))}</td>
                  <td style={{ ...tdc, ...watch(r.flag_low_24hr_contact) }} title={r.flag_low_24hr_contact ? "Under 85% contacted within a day" : undefined}>{dash(r, fmt.pct(r.pct_contacted_24hr))}</td>
                  <td style={tdc} title={`${r.appts_set_count} set · ${fmt.pct(r.appts_set_pct)} of leads`}>{dash(r, r.appts_set_count)}</td>
                  <td style={tdc} title={`${r.appts_held_count} held · ${fmt.pct(r.appts_held_pct)} of set`}>{dash(r, r.appts_held_count)}</td>
                  <td style={tdc}>{dash(r, r.under_contract_count)}</td>
                  <td style={tdc}>{dash(r, r.closed_count)}</td>
                  <td style={tdc}>{dash(r, r.dead_count)}</td>
                  <td style={tdc}>{dash(r, r.active_count)}</td>
                  <td style={tdc} title={`${r.sisu_matched_count} people · ${fmt.pct(r.sisu_match_rate)}`}>{dash(r, fmt.pct(r.sisu_match_rate))}</td>
                  <td style={tdc}>{r.sisu_closed_count ?? 0}</td>
                  <td style={{ ...tdc, fontFamily: FM }} title={r.sisu_gci_dollars ? `${fmt.plainMoney(r.sisu_gci_dollars)} GCI` : undefined}>{fmt.plainMoney(r.sisu_company_dollar_dollars)}</td>
                  <td style={{ ...tdc, fontFamily: FM }} title={spend.mapped && r.gross_spend != null ? `Gross ${fmt.plainMoney(r.gross_spend)}` : undefined}>{spend.mapped ? fmt.plainMoney(r.net_spend) : "—"}</td>
                  <td style={{ ...tdc, fontFamily: FM }} title={r.roi_gross != null ? "Company dollar over the full QuickBooks spend" : undefined}>{fmt.mult(r.roi_gross)}</td>
                  <td style={{ ...tdc, color: c.color, paddingRight: 20 }} title={c.title}>{c.text}</td>
                </tr>
              );
            })}
          </tbody>
          {rows.length > 1 && (
            <tfoot>
              <tr>
                {heads.map((_, i) => {
                  const v = { 0: whole ? "Program" : "Total", 1: fmt.count(header.leads), 12: fmt.pct(header.match_rate),
                              13: fmt.count(header.sisu_closed), 14: fmt.plainMoney(header.company_dollar),
                              15: header.net_spend == null ? "—" : fmt.plainMoney(header.net_spend), 16: fmt.mult(header.roi_gross) }[i];
                  return <td key={i} style={{ ...tdc, ...(i === 0 ? { ...sticky, paddingLeft: 20 } : {}), borderBottom: "none",
                    borderTop: `2px solid ${T.evergreen}`, fontWeight: 600, fontFamily: i >= 14 && i <= 16 ? FM : FB }}
                    title={i === 15 && header.gross_spend != null ? `Gross ${fmt.plainMoney(header.gross_spend)}` : undefined}>{v ?? ""}</td>;
                })}
              </tr>
            </tfoot>
          )}
        </table>
      </div>
      <div style={{ height: 8 }} />
    </Card>
  );
}

const SPEED_NOTE = "From arrival to the first outbound call, text, email or appointment. Each bar is that month's leads; "
  + "never contacted is gold for this month and last, while those leads can still be worked.";

/* Speed to lead and the never-contacted count, one row per month: the second table this used to
   be is the right-hand column now, and how long the never-contacted have waited is its tooltip. */
function SpeedCard({ rows, uncontacted, order, labels, today }) {
  const unc = Object.fromEntries(uncontacted.map((u) => [u.cohort_key, u]));
  const thisMonth = today.slice(0, 7);
  const lastMonth = (() => { const [y, m] = thisMonth.split("-").map(Number); return m === 1 ? `${y - 1}-12` : `${y}-${String(m - 1).padStart(2, "0")}`; })();
  const head = { fontFamily: FB, fontSize: 12, fontWeight: 600, color: T.muted, textAlign: "right" };
  const num = { fontFamily: FM, fontSize: 12, textAlign: "right", fontVariantNumeric: "tabular-nums" };
  return (
    <Card>
      <CardHead right={<Source name="Follow Up Boss" />}><PanelLabel accent={T.meadow} info={SPEED_NOTE}>Speed to lead</PanelLabel></CardHead>
      <div style={{ display: "grid", gridTemplateColumns: "72px minmax(0,1fr) 52px 104px", gap: "8px 16px", alignItems: "center" }}>
        <span /><span />
        <span style={head}>Leads</span>
        <span style={head}>Never contacted</span>
        {rows.map((r) => {
          const u = unc[r.cohort_key];
          const never = u ? u.uncontacted_total : r.buckets.never || 0;
          const recent = never > 0 && (r.cohort_key === thisMonth || r.cohort_key === lastMonth);
          const waited = u && never ? `${u.u24_count} waiting 24 hr+ · ${u.u48_count} 48 hr+ · ${u.u72_count} 72 hr+` : undefined;
          return [
            <span key={`${r.cohort_key}-m`} style={{ fontFamily: FB, fontSize: 12, color: T.slate }}>{r.cohort_month}</span>,
            <div key={`${r.cohort_key}-b`} style={{ display: "flex", height: 14, gap: 2, borderRadius: 4, overflow: "hidden", background: T.parchment }}
                 role="img" aria-label={`${r.cohort_month}: ${order.map((k) => `${labels[k]} ${r.buckets[k]}`).join(", ")}`}>
              {order.map((k) => r.buckets[k] > 0 && (
                <div key={k} title={`${labels[k]}: ${r.buckets[k]} (${fmt.pct(r.bucket_pcts[k])})`}
                     style={{ width: `${r.bucket_pcts[k]}%`, background: speedColor(k) }} />
              ))}
            </div>,
            <span key={`${r.cohort_key}-t`} style={{ ...num, color: T.muted }}>{r.total}</span>,
            <span key={`${r.cohort_key}-n`} title={waited}
              style={{ ...num, color: recent ? T.daffodilText : T.ink, fontWeight: recent ? 600 : 400 }}>{never}</span>,
          ];
        })}
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "8px 16px", marginTop: 16 }}>
        {order.map((k) => (
          <span key={k} style={{ display: "inline-flex", alignItems: "center", gap: 6, fontFamily: FB, fontSize: 12, color: T.slate }}>
            <span style={{ width: 8, height: 8, borderRadius: 2, background: speedColor(k) }} />{labels[k]}
          </span>
        ))}
      </div>
    </Card>
  );
}

/* ── the drawers' rows, in AuditDrawer's shape ─────────────────────────────────────────────── */

/* Company dollar's arithmetic, one term per piece that never breaks inside itself. Both EXP risk
   fees are one figure: the tooltip keeps the split. */
function Terms({ r }) {
  const exp = (Number(r.exp_risk_team) || 0) + (Number(r.exp_risk_agent) || 0);
  const terms = [
    [`GCI ${fmt.plainMoney(r.gci)}`],
    [`+ fee ${fmt.plainMoney(r.trans_fee)}`],
    [`− EXP ${fmt.plainMoney(exp)}${r.exp_risk_source === "DEFAULT" ? " (default)" : ""}`,
     `Team side ${fmt.plainMoney(r.exp_risk_team)}, agent side ${fmt.plainMoney(r.exp_risk_agent)}`],
    [`− agent ${fmt.plainMoney(r.agent_payment)}`],
  ];
  return (
    <span style={{ display: "flex", flexWrap: "wrap", columnGap: 8, rowGap: 2 }}>
      {terms.map(([text, title]) => <span key={text} title={title} style={{ whiteSpace: "nowrap" }}>{text}</span>)}
    </span>
  );
}

export function closedRows(list) {
  return list.map((r) => ({
    id: r.transaction_id, name: r.name || "Unnamed client", seg: undefined,
    l2: [r.address, r.close_date ? `closed ${dayLabel(r.close_date)}` : null, r.agent].filter(Boolean).join(" · "),
    l3: r.company_dollar == null ? "Waiting for Sisu's commission lines" : <Terms r={r} />,
    r1: r.company_dollar == null ? "—" : fmt.plainMoney(r.company_dollar), r2: `${monthLabel(r.cohort_month)} cohort`,
    source_url: r.fub_url,
  }));
}

const CLOSED_CSV = [
  { key: "cohort_month", label: "Cohort" }, { key: "transaction_id", label: "Sisu transaction" }, { key: "name", label: "Client" },
  { key: "address", label: "Address" }, { key: "close_date", label: "Closed" }, { key: "agent", label: "Agent" },
  { key: "gci", label: "GCI" }, { key: "trans_fee", label: "Transaction fee" }, { key: "exp_risk_team", label: "EXP risk (team)" },
  { key: "exp_risk_agent", label: "EXP risk (agent)" }, { key: "exp_risk_source", label: "EXP risk from" },
  { key: "agent_payment", label: "Agent payment" }, { key: "company_dollar", label: "Company dollar" },
  { key: "volume", label: "Volume" }, { key: "fub_person_id", label: "Follow Up Boss id" },
];

const COMPANY_DOLLAR_NOTE = "GCI plus the transaction fee, less EXP risk and the agent's payment, from Sisu's commission "
  + "record. Not the brokerage page's net GCI, which is GCI less the agent's split; the two are never added together.";

export default function Cohorts({ data, groupName, settingsHref, openDrawer }) {
  const navigate = useNavigate();
  const { header: h, spend, cohort_rows: rows } = data;
  const bearer = spend.bearer_short || spend.bearer_name;
  const file = (what) => `${data.group.key}-${what}-${data.today}.csv`;
  // Whether the date bar covers the whole program: the totals are "program to date" only then.
  const whole = !data.window?.start || !data.program?.since || data.window.start <= data.program.since;

  const openClosed = () => openDrawer({
    preset: { label: `${groupName} closings`, source: "Sisu", computed_as:
      "Company dollar is GCI plus the transaction fee, less both EXP risk fees and the agent's payment. A fee Sisu didn't record uses the lead source's default.",
      count: data.closed_transactions.length, rows: closedRows(data.closed_transactions) },
    onExport: () => downloadCsv(file("closings"), CLOSED_CSV, data.closed_transactions) });
  const openUc = () => openDrawer({
    preset: { label: "Under contract", source: "Sisu", computed_as: "Linked Sisu deals with a contract date and no close yet.",
      count: data.under_contract.length,
      rows: data.under_contract.map((r) => ({ id: r.transaction_id, name: r.name || "Unnamed client",
        l2: [r.uc_date ? `under contract ${dayLabel(r.uc_date)}` : null, r.expected_close_date ? `closing ${dayLabel(r.expected_close_date)}` : null, r.agent].filter(Boolean).join(" · "),
        r1: fmt.plainMoney(r.gci), r2: `${monthLabel(r.cohort_month)} cohort` })) },
    onExport: () => downloadCsv(file("under-contract"), Object.keys(data.under_contract[0] || {}).map((k) => ({ key: k, label: k })), data.under_contract) });
  const openUnmatched = () => openDrawer({
    preset: { label: "Not matched in Sisu", source: "Follow Up Boss",
      computed_as: `Leads with no Sisu contact or deal found by Follow Up Boss id or email.${data.unmatched_total > data.unmatched.length ? ` The first ${data.unmatched.length} of ${data.unmatched_total}.` : ""}`,
      count: data.unmatched.length,
      rows: data.unmatched.map((r) => ({ id: r.fub_person_id, name: r.full_name || "Unnamed lead", tone: r.email ? undefined : "watch",
        l2: `${monthLabel(r.cohort_key)} · ${r.agent} · ${r.reason}`, r1: "", source_url: r.fub_url })) },
    onExport: () => downloadCsv(file("unmatched"), [{ key: "fub_person_id", label: "Follow Up Boss id" }, { key: "full_name", label: "Name" },
      { key: "email", label: "Email" }, { key: "cohort_key", label: "Cohort" }, { key: "agent", label: "Agent" }, { key: "reason", label: "Why" }], data.unmatched) });

  const mapped = spend.mapped;
  const closes = `${h.sisu_closed} close${h.sisu_closed === 1 ? "" : "s"}`;
  const companyDollar = (
    <StatTile label="Company dollar" source="Sisu" value={fmt.plainMoney(h.company_dollar)} info={COMPANY_DOLLAR_NOTE}
      sub={`${closes} · ${fmt.plainMoney(h.gci)} GCI`}
      onClick={data.closed_transactions.length ? openClosed : undefined} title="The closings behind this figure" />
  );
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {!mapped && (
        <Note>
          {groupName}'s QuickBooks spend isn't mapped, so spend and ROI are left blank rather than shown as $0.{" "}
          <Link to={settingsHref} style={{ color: T.teal, fontWeight: 600 }}>Map its QuickBooks lines</Link>.
        </Note>
      )}
      {mapped && !spend.synced && <Note>QuickBooks hasn't finished syncing for {data.business.name}, so spend isn't read yet.</Note>}
      {mapped && spend.synced && h.spend_months_known < h.spend_months && (
        <Note>
          Stored QuickBooks history starts {dayLabel(spend.history_starts)}, {spend.history_starts?.slice(0, 4)}. ROI covers the {h.spend_months_known} of {h.spend_months} months
          from then; earlier months show no spend rather than a guess.
        </Note>
      )}
      {h.closes_awaiting_commission > 0 && (
        <Note tone="plain">
          {h.closes_awaiting_commission} close{h.closes_awaiting_commission === 1 ? " is" : "s are"} waiting for Sisu's commission lines and
          {h.closes_awaiting_commission === 1 ? " isn't" : " aren't"} in company dollar yet. They are read on the next Sisu sync.
        </Note>
      )}

      {mapped ? (
        <div className="cc-score">
          {companyDollar}
          <StatTile asIs label="QuickBooks spend" source="QBO" value={fmt.plainMoney(h.gross_spend)}
            sub={bearer ? `${fmt.plainMoney(h.net_spend)} after ${bearer}'s ${spend.share_pct}%` : "no cost share"} />
          <StatTile asIs label="ROI on full spend" value={fmt.mult(h.roi_gross)} sub={`net ${fmt.money(h.net_profit_gross)}`} />
          <StatTile asIs label={bearer ? `ROI after ${bearer}'s share` : "Net profit"}
            value={bearer ? fmt.mult(h.roi_net) : fmt.money(h.net_profit_gross)}
            sub={bearer ? `net ${fmt.money(h.net_profit_net)}` : "company dollar less spend"} />
        </div>
      ) : (
        // Not mapped is said once, by one wide tile that carries the fix -- not by three tiles in a row.
        <div className="cc-score">
          {companyDollar}
          <div className="ls-wide" style={{ boxSizing: "border-box", minWidth: 0, background: T.white, border: `1px solid ${T.line}`,
            borderRadius: 14, padding: 20, display: "flex", alignItems: "center", gap: 20, flexWrap: "wrap" }}>
            <div style={{ flex: "1 1 280px", minWidth: 0 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12, minHeight: 18 }}>
                <span style={{ fontFamily: FB, fontSize: 13, color: T.tertiary, fontWeight: 500 }}>Spend, ROI and net profit</span>
                <SourceTag of="QBO" />
              </div>
              <div style={{ fontFamily: FD, fontSize: 20, fontWeight: 600, color: T.muted }}>Not mapped</div>
            </div>
            <OutlineButton onClick={() => navigate(settingsHref)}>Map QuickBooks lines</OutlineButton>
          </div>
        </div>
      )}

      <Card>
        <CardHead right={<Source name="Follow Up Boss · Sisu" />}>
          <PanelLabel accent={T.meadow}>{whole ? "Program to date" : "These cohorts"}</PanelLabel>
        </CardHead>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(min(100%, 150px), 1fr))", gap: 10 }}>
          <OpTile d={{ label: "Leads received", value: fmt.count(h.leads), sub: `${rows.filter((r) => !r.spend_only).length} cohorts` }} />
          <OpTile d={{ key: "unmatched", asIs: true, label: "Matched in Sisu", value: fmt.pct(h.match_rate), sub: `${fmt.count(h.sisu_matched)} people` }}
                  onDrill={data.unmatched_total ? openUnmatched : undefined} />
          <OpTile d={{ key: "uc", label: "Under contract", value: fmt.count(data.under_contract.length), sub: "in Sisu", asIs: true }}
                  onDrill={data.under_contract.length ? openUc : undefined} />
          <OpTile d={{ label: "Volume closed", value: fmt.compactMoney(h.volume), sub: closes }} />
          <OpTile d={{ label: "Cost per lead", value: mapped ? fmt.plainMoney(h.cost_per_lead_gross) : "—", sub: "gross" }} />
          <OpTile d={{ label: "Cost per close", value: mapped ? fmt.plainMoney(h.cost_per_close_gross) : "—", sub: "gross" }} />
        </div>
      </Card>

      <CohortTable rows={rows} header={h} spend={spend} filename={file("cohorts")} whole={whole}
        onRow={(r) => openDrawer({ leadsMonth: r.cohort_key, label: `${r.cohort_month} leads` })} />

      <SpeedCard rows={data.speed_table} uncontacted={data.uncontacted_table} order={data.speed_bucket_order}
        labels={data.speed_bucket_labels} today={data.today} />
    </div>
  );
}
