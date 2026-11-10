/* Settings › Lead sources (docs/specs/ulrg-reporting-dashboard-port.md, phase 1 and phase 6).

   Which Follow Up Boss source strings are one channel on the brokerage's reports, when each
   channel's program started, which QuickBooks lines are its spend, and who else bears part of it.
   The weekly digest for a group is configured on the same page (DigestCard).

   SPEND IS A MAPPING, NOT A NUMBER. There is no field for a monthly cost anywhere on this page. The
   rules pick QuickBooks lines, the server sums them, and the preview shows what the rules catch
   before anybody trusts a ROI built on them.

   EVERY NUMBER IS THE SERVER'S. The preview, the overlap warning and the "history starts" note all
   arrive computed; this file lays them out. Built from Settings' own kit (settings/kit.jsx), the
   inputs Recruiting uses, and books/ui's Pill for status. */
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { T } from "../theme.js";
import { getJSON, postJSON, patchJSON, putJSON, delJSON } from "../api.js";
import { Card, SBtn } from "./kit.jsx";
import { Pill } from "../books/ui.jsx";
import DigestCard from "./DigestCard.jsx";

const API_BASE = import.meta.env.VITE_API_BASE;

const FB = "var(--font-text)", FD = "var(--font-display)", FM = "var(--font-data)";
const label = { display: "block", fontFamily: FB, fontSize: 12, fontWeight: 600, color: T.slate, marginTop: 14 };
const input = { width: "100%", boxSizing: "border-box", fontFamily: FB, fontSize: 13, color: T.ink,
                background: T.white, border: `1px solid ${T.line}`, borderRadius: 8, padding: "8px 10px", marginTop: 4 };
const num = { ...input, fontFamily: FM, width: 120 };
const h2 = { fontFamily: FD, fontSize: 16, fontWeight: 600, color: T.ink };
const lede = { fontFamily: FB, fontSize: 13, color: T.secondary, marginTop: 4, lineHeight: 1.5, maxWidth: 640 };
const meta = { fontFamily: FM, fontSize: 12, color: T.muted };
const errText = { fontFamily: FB, fontSize: 13, color: T.poppyText, marginTop: 12 };
const savedText = { fontFamily: FB, fontSize: 12, color: T.meadowInk };

const MODES = [["contains", "Contains"], ["exact", "Exact"], ["regex", "Pattern (regex)"]];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const monthLabel = (ym) => (ym ? `${MONTHS[Number(ym.slice(5, 7)) - 1]} ${ym.slice(0, 4)}` : "");
const dayLabel = (iso) => (iso ? `${MONTHS[Number(iso.slice(5, 7)) - 1]} ${Number(iso.slice(8, 10))}, ${iso.slice(0, 4)}` : "");
const money = (n) => (n == null ? "—" : `${n < 0 ? "−" : ""}$${Math.abs(Math.round(n)).toLocaleString("en-US")}`);
const errOf = (e, fallback) => String((e && (e.detail || e.message)) || fallback);

/* A boxed sentence in the gold the dashboard uses for "look at this" (SampleBanner, the zero-
   referral chip), never red: an overlap is a thing to know, not a failure. */
function Note({ children, tone = "gold" }) {
  const t = tone === "gold"
    ? { bg: T.daffodilBg, edge: T.daffodil, ink: T.ink }
    : { bg: T.parchment, edge: T.line, ink: T.secondary };
  return (
    <div role="note" style={{ boxSizing: "border-box", background: t.bg, border: `1px solid ${t.edge}`,
      borderRadius: 10, padding: "10px 12px", fontFamily: FB, fontSize: 13, color: t.ink, lineHeight: 1.5,
      marginTop: 12 }}>{children}</div>
  );
}

function StatusFor({ g }) {
  if (!g.is_active) return <Pill tone="muted">Inactive</Pill>;
  if (g.matches_nothing) return <Pill tone="warn">No variants</Pill>;
  return <Pill tone="good">Active</Pill>;
}

/* ── the list ───────────────────────────────────────────────────────────────────────────── */

function GroupList({ groups, selected, onSelect, onAdd, canEdit }) {
  return (
    <Card>
      <div style={{ display: "flex", alignItems: "flex-start", gap: 12, flexWrap: "wrap" }}>
        <div style={{ flex: "1 1 320px" }}>
          <div style={h2}>Lead sources</div>
          <div style={lede}>
            Which Follow Up Boss sources count as one channel on the brokerage's cohort, pipeline and
            What's Working reports, and where that channel's spend lives in QuickBooks.
          </div>
        </div>
        {canEdit && <SBtn kind="primary" icon="tune" onClick={onAdd}>Add a lead source</SBtn>}
      </div>
      <div style={{ marginTop: 16, border: `1px solid ${T.line}`, borderRadius: 10, overflow: "hidden" }}>
        {groups.map((g, i) => {
          const on = selected === g.key;
          return (
            <button key={g.key} type="button" onClick={() => onSelect(g.key)} aria-pressed={on}
              style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) auto", gap: 12, alignItems: "center",
                width: "100%", textAlign: "left", font: "inherit", cursor: "pointer", border: "none",
                borderTop: i ? `1px solid ${T.line}` : "none", padding: "12px 12px",
                background: on ? T.parchment : T.white }}>
              <span style={{ minWidth: 0 }}>
                <span style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
                  <span style={{ fontFamily: FB, fontSize: 14, fontWeight: on ? 600 : 500, color: T.ink }}>{g.display_name}</span>
                  <span style={meta}>{g.key}</span>
                </span>
                <span style={{ ...meta, display: "block", marginTop: 4 }}>
                  {[`${g.variants.length} variant${g.variants.length === 1 ? "" : "s"} · ${g.match_mode}`,
                    g.program_start ? `from ${monthLabel(g.program_start.slice(0, 7))}` : "no program start",
                    g.spend_mapped ? "QuickBooks mapped" : "spend not mapped",
                    ...(g.cost_shares || []).map((sh) => `${sh.share_pct}% borne by ${sh.bearer_name}`)].join(" · ")}
                </span>
              </span>
              <StatusFor g={g} />
            </button>
          );
        })}
        {groups.length === 0 && (
          <div style={{ fontFamily: FB, fontSize: 13, color: T.muted, padding: "16px 12px" }}>
            No lead sources yet. Add one to start a cohort report for that channel.
          </div>
        )}
      </div>
    </Card>
  );
}

/* ── one group's settings ───────────────────────────────────────────────────────────────── */

function toDraft(g) {
  return {
    key: g?.key || "", display_name: g?.display_name || "", variants: (g?.variants || []).join("\n"),
    match_mode: g?.match_mode || "contains", program_start: g?.program_start || "",
    is_active: g ? g.is_active : true, display_order: g?.display_order ?? 0,
    exp_risk_team_default: g?.exp_risk_team_default ?? 0, exp_risk_agent_default: g?.exp_risk_agent_default ?? 0,
  };
}

function GroupEditor({ bizKey, group, creating, onSaved, onCancel, onDeleted }) {
  const [d, setD] = useState(() => toDraft(group));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const [saved, setSaved] = useState(false);
  useEffect(() => { setD(toDraft(group)); setErr(null); setSaved(false); }, [group?.key, creating]);
  const set = (k) => (e) => { setD({ ...d, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value }); setSaved(false); };
  const variants = d.variants.split("\n").map((v) => v.trim()).filter(Boolean);

  async function save() {
    setBusy(true); setErr(null);
    const body = { display_name: d.display_name, variants, match_mode: d.match_mode,
                   program_start: d.program_start || null, is_active: d.is_active,
                   display_order: Number(d.display_order) || 0,
                   exp_risk_team_default: Number(d.exp_risk_team_default) || 0,
                   exp_risk_agent_default: Number(d.exp_risk_agent_default) || 0 };
    try {
      const out = creating
        ? await postJSON(`/businesses/${bizKey}/lead-sources`, { ...body, key: d.key.trim() })
        : await patchJSON(`/businesses/${bizKey}/lead-sources/${group.key}`, body);
      setSaved(true);
      onSaved(out.key);
    } catch (e) {
      setErr(errOf(e, "Couldn't save the lead source."));
    } finally { setBusy(false); }
  }

  async function remove() {
    if (!window.confirm(`Delete ${group.display_name}? Its reports stop showing. The rows behind them stay.`)) return;
    setBusy(true); setErr(null);
    try { await delJSON(`/businesses/${bizKey}/lead-sources/${group.key}`); onDeleted(); }
    catch (e) { setErr(errOf(e, "Couldn't delete the lead source.")); }
    finally { setBusy(false); }
  }

  return (
    <Card>
      <div style={h2}>{creating ? "New lead source" : group.display_name}</div>
      <div style={lede}>
        A Follow Up Boss person is in this channel when their source matches a variant. Cohorts start at the
        program start month, read in the workspace's calendar.
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 220px), 1fr))", columnGap: 16 }}>
        <label style={label}>Display name
          <input style={input} value={d.display_name} onChange={set("display_name")} maxLength={120} />
        </label>
        <label style={label}>Key
          <input style={{ ...input, fontFamily: FM, background: creating ? T.white : T.parchment }} value={d.key}
            onChange={set("key")} disabled={!creating} placeholder="realtor_com" maxLength={64} />
        </label>
        <label style={label}>Match mode
          <select style={input} value={d.match_mode} onChange={set("match_mode")}>
            {MODES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
        </label>
        <label style={label}>Program start
          <input type="date" style={input} value={d.program_start} onChange={set("program_start")} />
        </label>
      </div>

      <label style={label}>Source variants
        <textarea rows={6} style={{ ...input, fontFamily: FM, lineHeight: 1.6 }} value={d.variants}
          onChange={set("variants")} placeholder={"One per line, as Follow Up Boss spells it"} />
      </label>
      <div style={{ ...meta, marginTop: 6 }}>
        {variants.length} of 50 · {d.match_mode === "contains"
          ? "a source containing any line matches, so Realtor.com also matches Realtor.com MVIP"
          : d.match_mode === "exact" ? "a source must equal a line, ignoring case"
            : "each line is a pattern, ignoring case"}
      </div>

      <div style={{ display: "flex", gap: 16, flexWrap: "wrap", alignItems: "flex-end" }}>
        <label style={label}>EXP risk fee, team side
          <input type="number" min="0" step="1" style={num} value={d.exp_risk_team_default} onChange={set("exp_risk_team_default")} />
        </label>
        <label style={label}>EXP risk fee, agent side
          <input type="number" min="0" step="1" style={num} value={d.exp_risk_agent_default} onChange={set("exp_risk_agent_default")} />
        </label>
        <label style={label}>Display order
          <input type="number" min="0" max="999" style={{ ...num, width: 96 }} value={d.display_order} onChange={set("display_order")} />
        </label>
        <label style={{ ...label, display: "inline-flex", alignItems: "center", gap: 8, paddingBottom: 10 }}>
          <input type="checkbox" checked={d.is_active} onChange={set("is_active")} /> Active
        </label>
      </div>
      <div style={{ ...meta, fontFamily: FB, marginTop: 8, lineHeight: 1.5 }}>
        The EXP fees stand in when Sisu's commission record has no EXP risk adjustment. A line that matches two
        groups' spend rules counts in the lower display order only.
      </div>

      {err && <div style={errText}>{err}</div>}
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 16, flexWrap: "wrap" }}>
        <SBtn kind="primary" disabled={busy} onClick={save}>{busy ? "Saving…" : creating ? "Add lead source" : "Save"}</SBtn>
        {creating && <SBtn onClick={onCancel}>Cancel</SBtn>}
        {saved && <span style={savedText}>Saved.</span>}
        <span style={{ flex: 1 }} />
        {!creating && <SBtn disabled={busy} onClick={remove}>Delete</SBtn>}
      </div>
    </Card>
  );
}

/* ── QuickBooks spend ───────────────────────────────────────────────────────────────────── */

function SpendCard({ bizKey, group, bizName }) {
  const [view, setView] = useState(null);
  const [rules, setRules] = useState([]);
  const [shares, setShares] = useState([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const [saved, setSaved] = useState(false);

  const load = useCallback(() => {
    setView(null);
    getJSON(`/businesses/${bizKey}/lead-sources/${group.key}/spend`)
      .then((v) => { setView(v); setRules(v.rules); setShares(v.cost_shares); setErr(null); })
      .catch((e) => setErr(errOf(e, "Couldn't load the spend mapping.")));
  }, [bizKey, group.key]);
  useEffect(load, [load]);

  if (!view) {
    return <Card><div style={h2}>QuickBooks spend</div>
      <div style={{ ...lede, color: err ? T.poppyText : T.muted }}>{err || "Loading…"}</div></Card>;
  }

  const companyId = view.businesses.find((b) => b.key === bizKey)?.id;
  const accounts = view.options.accounts;
  const vendors = view.options.vendors;
  const accountName = (id) => accounts.find((a) => a.id === id)?.path || `Account ${id}`;
  const vendorName = (id) => vendors.find((v) => v.id === id)?.name || `Vendor ${id}`;
  const shareTotal = shares.reduce((a, sh) => a + (Number(sh.share_pct) || 0), 0);
  const bearerOptions = view.businesses.filter((b) => b.key !== bizKey);
  const qb = view.quickbooks;
  // By MONTH, as the report reads spend: history from 3 February covers a 1 February program.
  const startsLate = qb.history_starts && group.program_start && qb.history_starts.slice(0, 7) > group.program_start.slice(0, 7);
  const accountLabel = (a) => {
    const parts = (a.path || "").split(":").map((x) => x.trim()).filter(Boolean);
    const parent = parts.length > 1 ? parts[parts.length - 2] : null;
    return [a.name, parent, a.id].filter(Boolean).join(" · ");
  };

  const setRule = (i, patch) => { setRules(rules.map((r, j) => (j === i ? { ...r, ...patch } : r))); setSaved(false); };
  const setShare = (i, patch) => { setShares(shares.map((r, j) => (j === i ? { ...r, ...patch } : r))); setSaved(false); };

  async function save() {
    setBusy(true); setErr(null);
    try {
      const v = await putJSON(`/businesses/${bizKey}/lead-sources/${group.key}/spend`, {
        rules: rules.map((r) => ({ business_id: r.business_id || companyId, account_qbo_id: r.account_qbo_id || null,
                                   vendor_qbo_id: r.vendor_qbo_id || null, class_label: r.class_label || null })),
        cost_shares: shares.map((sh) => ({ bearer_business_id: sh.bearer_business_id, share_pct: Number(sh.share_pct) })),
      });
      setView(v); setRules(v.rules); setShares(v.cost_shares); setSaved(true);
    } catch (e) {
      setErr(errOf(e, "Couldn't save the spend mapping."));
    } finally { setBusy(false); }
  }

  const cell = { ...input, marginTop: 0 };
  return (
    <Card>
      <div style={h2}>QuickBooks spend · {group.display_name}</div>
      <div style={lede}>
        Spend is every line in {bizName}'s QuickBooks that one of these rules matches, in the month of its books
        date. Within a rule, everything set must match. A line that matches several rules counts once.
      </div>

      {!qb.connected && (
        <Note>QuickBooks isn't connected for {bizName}, so no spend can be read yet. <Link to="/settings/integrations" style={{ color: T.teal, fontWeight: 600 }}>Connect it in Integrations</Link>.</Note>
      )}
      {qb.connected && !qb.synced && <Note>QuickBooks is connected but hasn't finished its first sync. Spend appears after it does.</Note>}
      {startsLate && (
        <Note>
          {bizName}'s stored QuickBooks history starts {dayLabel(qb.history_starts)}, after this program's start
          ({dayLabel(group.program_start)}). Months before then read as no spend. Set the books backfill start to the
          program start in Integrations and re-sync to count them.
        </Note>
      )}

      <div style={{ marginTop: 16, border: `1px solid ${T.line}`, borderRadius: 10, overflow: "hidden" }}>
        <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1.4fr) minmax(0,1fr) minmax(0,0.7fr) 32px", gap: 8,
          padding: "8px 12px", background: T.parchment, borderBottom: `1px solid ${T.line}` }}>
          {["Expense account (the line's)", "Vendor (the payee's)", "Class contains", ""].map((h) => (
            <span key={h} style={{ fontFamily: FB, fontSize: 12, fontWeight: 600, color: T.muted }}>{h}</span>
          ))}
        </div>
        {rules.map((r, i) => (
          <div key={i} style={{ display: "grid", gridTemplateColumns: "minmax(0,1.4fr) minmax(0,1fr) minmax(0,0.7fr) 32px",
            gap: 8, alignItems: "center", padding: "8px 12px", borderTop: i ? `1px solid ${T.line}` : "none" }}>
            <select style={cell} value={r.account_qbo_id || ""} disabled={!view.can_edit}
              onChange={(e) => setRule(i, { account_qbo_id: e.target.value || null })} aria-label="Expense account">
              <option value="">Any account</option>
              {r.account_qbo_id && !accounts.some((a) => a.id === r.account_qbo_id) && (
                <option value={r.account_qbo_id}>{accountName(r.account_qbo_id)}</option>)}
              {accounts.map((a) => <option key={`${a.business_id}:${a.id}`} value={a.id} title={a.path}>{accountLabel(a)}</option>)}
            </select>
            <select style={cell} value={r.vendor_qbo_id || ""} disabled={!view.can_edit}
              onChange={(e) => setRule(i, { vendor_qbo_id: e.target.value || null })} aria-label="Vendor">
              <option value="">Any vendor</option>
              {r.vendor_qbo_id && !vendors.some((v) => v.id === r.vendor_qbo_id) && (
                <option value={r.vendor_qbo_id}>{vendorName(r.vendor_qbo_id)}</option>)}
              {vendors.map((v) => <option key={`${v.business_id}:${v.id}`} value={v.id}>{v.name} · {v.id}</option>)}
            </select>
            <input style={cell} value={r.class_label || ""} disabled={!view.can_edit} placeholder="optional"
              onChange={(e) => setRule(i, { class_label: e.target.value })} aria-label="Class contains" />
            {view.can_edit
              ? <button type="button" onClick={() => { setRules(rules.filter((_, j) => j !== i)); setSaved(false); }}
                  aria-label="Remove rule" style={{ border: "none", background: "transparent", cursor: "pointer",
                    color: T.muted, fontSize: 16, lineHeight: 1 }}>✕</button>
              : <span />}
          </div>
        ))}
        {rules.length === 0 && (
          <div style={{ fontFamily: FB, fontSize: 13, color: T.muted, padding: "16px 12px" }}>
            No rules yet, so the report shows company dollar without spend or ROI.
          </div>
        )}
      </div>
      {view.can_edit && (
        <div style={{ marginTop: 8 }}>
          <SBtn small onClick={() => { setRules([...rules, { business_id: companyId, account_qbo_id: null, vendor_qbo_id: null, class_label: "" }]); setSaved(false); }}>
            Add a rule
          </SBtn>
        </div>
      )}

      {Object.keys(view.overlap_with_other_groups || {}).length > 0 && (
        <Note>
          {Object.entries(view.overlap_with_other_groups).map(([k, n]) => `${n} line${n === 1 ? "" : "s"} also match ${k}`).join(", ")}.
          Those count in the group with the lower display order, not here.
        </Note>
      )}

      <div style={{ marginTop: 24, fontFamily: FB, fontSize: 13, fontWeight: 600, color: T.ink }}>Cost share</div>
      <div style={{ ...lede, marginTop: 4 }}>
        Another business bearing part of this spend, by agreement. Net spend is gross spend less the shares. Nothing
        is read from the other business's books.
      </div>
      {shares.map((sh, i) => (
        <div key={i} style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 8, flexWrap: "wrap" }}>
          <select style={{ ...input, marginTop: 0, width: "auto", minWidth: 220 }} value={sh.bearer_business_id}
            disabled={!view.can_edit} onChange={(e) => setShare(i, { bearer_business_id: e.target.value })} aria-label="Business">
            {bearerOptions.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
          </select>
          <input type="number" min="0" max="100" step="0.5" style={{ ...num, marginTop: 0, width: 96 }}
            value={sh.share_pct} disabled={!view.can_edit} onChange={(e) => setShare(i, { share_pct: e.target.value })}
            aria-label="Share percent" />
          <span style={meta}>%</span>
          {view.can_edit && (
            <button type="button" onClick={() => { setShares(shares.filter((_, j) => j !== i)); setSaved(false); }}
              aria-label="Remove share" style={{ border: "none", background: "transparent", cursor: "pointer", color: T.muted, fontSize: 16 }}>✕</button>
          )}
        </div>
      ))}
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 8, flexWrap: "wrap" }}>
        {view.can_edit && bearerOptions.length > 0 && (
          <SBtn small onClick={() => { setShares([...shares, { bearer_business_id: bearerOptions[0].id, share_pct: 0 }]); setSaved(false); }}>
            Add a share
          </SBtn>
        )}
        <span style={{ ...meta, color: shareTotal > 100 ? T.poppyText : T.muted }}>
          {shares.length ? `${shareTotal}% borne elsewhere · ${Math.max(0, 100 - shareTotal)}% by ${bizName}` : `All of it borne by ${bizName}`}
        </span>
      </div>

      {err && <div style={errText}>{err}</div>}
      {view.can_edit && (
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 16 }}>
          <SBtn kind="primary" disabled={busy} onClick={save}>{busy ? "Saving…" : "Save spend mapping"}</SBtn>
          {saved && <span style={savedText}>Saved. The report reads it on its next load.</span>}
        </div>
      )}

      <div style={{ marginTop: 24, paddingTop: 16, borderTop: `1px solid ${T.line}` }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
          <span style={{ fontFamily: FB, fontSize: 13, fontWeight: 600, color: T.ink }}>What the saved rules catch</span>
          <span style={meta}>{view.preview.lines} line{view.preview.lines === 1 ? "" : "s"} · {money(view.preview.gross)} gross</span>
        </div>
        {view.preview.months.length > 0 ? (
          <table style={{ borderCollapse: "collapse", marginTop: 8, fontFamily: FB, fontSize: 13 }}>
            <thead>
              <tr>{["Month", "Gross", `Net of ${view.share_pct}%`].map((h, i) => (
                <th key={h} style={{ fontSize: 12, fontWeight: 600, color: T.muted, padding: "4px 16px 4px 0", textAlign: i ? "right" : "left" }}>{h}</th>))}</tr>
            </thead>
            <tbody>
              {view.preview.months.map((m) => (
                <tr key={m.month} style={{ borderTop: `1px solid ${T.line}` }}>
                  <td style={{ padding: "6px 16px 6px 0", color: T.slate }}>{monthLabel(m.month)}</td>
                  <td style={{ padding: "6px 16px 6px 0", textAlign: "right", fontFamily: FM, color: T.ink, fontVariantNumeric: "tabular-nums" }}>{money(m.gross)}</td>
                  <td style={{ padding: "6px 0", textAlign: "right", fontFamily: FM, color: T.ink, fontVariantNumeric: "tabular-nums" }}>{money(m.net)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <div style={{ fontFamily: FB, fontSize: 13, color: T.muted, marginTop: 8 }}>
            {view.rules.length ? "No QuickBooks lines match these rules yet." : "Nothing, because there are no rules."}
          </div>
        )}
        {view.lines_matching_several_rules > 0 && (
          <div style={{ ...meta, marginTop: 8 }}>{view.lines_matching_several_rules} line{view.lines_matching_several_rules === 1 ? " matches" : "s match"} more than one rule and {view.lines_matching_several_rules === 1 ? "is" : "are"} counted once.</div>
        )}
      </div>
    </Card>
  );
}

/* ── the page ───────────────────────────────────────────────────────────────────────────── */

export default function LeadSourceSettings() {
  const [params, setParams] = useSearchParams();
  const [businesses, setBusinesses] = useState(null);
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  const [creating, setCreating] = useState(false);
  const brokerages = useMemo(() => (businesses || []).filter((b) => b.kind === "real_estate"), [businesses]);
  const bizKey = params.get("business") || brokerages[0]?.key;
  const selected = params.get("group");

  useEffect(() => {
    if (!API_BASE) return;
    getJSON("/businesses").then(setBusinesses).catch((e) => setErr(errOf(e, "Couldn't load the businesses.")));
  }, []);

  const load = useCallback((keep) => {
    if (!bizKey) return;
    getJSON(`/businesses/${bizKey}/lead-sources?include_inactive=true`)
      .then((d) => {
        setData(d);
        const want = keep || selected;
        if (!want || !d.groups.some((g) => g.key === want)) {
          const first = d.groups[0]?.key;
          setParams((p) => { const n = new URLSearchParams(p); if (first) n.set("group", first); else n.delete("group"); return n; }, { replace: true });
        }
      })
      .catch((e) => setErr(errOf(e, "Couldn't load the lead sources.")));
  }, [bizKey]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { load(); }, [load]);

  const select = (key) => { setCreating(false); setParams((p) => { const n = new URLSearchParams(p); n.set("group", key); return n; }); };

  if (!API_BASE) {
    return <Card title="Lead sources" hint="Lead sources are set on the live app, against your own Follow Up Boss and QuickBooks data." />;
  }
  if (err && !data) return <Card><div style={{ fontFamily: FB, fontSize: 13, color: T.poppyText }}>{err}</div></Card>;
  if (businesses && !brokerages.length) {
    return <Card title="Lead sources" hint="Lead-source reports run on a brokerage, and this workspace has none. Add one under Businesses first." />;
  }
  if (!data) return <Card><div style={{ fontFamily: FB, fontSize: 13, color: T.muted }}>Loading…</div></Card>;

  const group = data.groups.find((g) => g.key === selected);
  const biz = brokerages.find((b) => b.key === bizKey) || data.business;

  return (
    <>
      {brokerages.length > 1 && (
        <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 16 }}>
          <span style={{ fontFamily: FB, fontSize: 12, fontWeight: 600, color: T.slate }}>Brokerage</span>
          <select style={{ ...input, marginTop: 0, width: "auto" }} value={bizKey}
            onChange={(e) => setParams({ business: e.target.value })}>
            {brokerages.map((b) => <option key={b.key} value={b.key}>{b.name}</option>)}
          </select>
        </div>
      )}
      <GroupList groups={data.groups} selected={creating ? null : selected} onSelect={select}
        onAdd={() => setCreating(true)} canEdit={data.can_edit} />
      {creating && (
        <GroupEditor bizKey={bizKey} group={null} creating onCancel={() => setCreating(false)}
          onSaved={(key) => { setCreating(false); load(key); select(key); }} onDeleted={() => {}} />
      )}
      {!creating && group && (
        <>
          <GroupEditor bizKey={bizKey} group={group} creating={false} onSaved={(key) => load(key)}
            onDeleted={() => { setParams({ business: bizKey }); load(); }} />
          <SpendCard bizKey={bizKey} group={group} bizName={biz.name} />
          <DigestCard bizKey={bizKey} group={group} />
        </>
      )}
    </>
  );
}
