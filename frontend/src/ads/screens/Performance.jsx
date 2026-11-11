import { useMemo, useState } from "react";

import { API_BASE, postJSON } from "../../api";
import { C, FIG, FONT, num, pct, usd } from "../adsTokens.js";
import { Btn, Card, Drawer, Empty, PageTitle, inputStyle } from "../ui.jsx";

const COLS = {
  campaigns: [
    { key: "name", label: "Campaign" },
    { key: "group", label: "Brand" },
    { key: "spend", label: "Spend", num: true },
    { key: "ctr", label: "CTR", num: true },
    { key: "cpc", label: "CPC", num: true },
    { key: "cpl", label: "Cost / lead", num: true },
  ],
  ads: [
    { key: "name", label: "Ad" },
    { key: "spend", label: "Spend", num: true },
    { key: "ctr", label: "CTR", num: true },
    { key: "frequency", label: "Frequency", num: true },
    { key: "cpl", label: "Cost / lead", num: true },
    { key: "seats", label: "Seats", num: true },
  ],
};

function cell(row, key) {
  if (key === "spend" || key === "cpc" || key === "cpl") return usd(row[key], key === "spend" ? 0 : 2);
  if (key === "ctr") return pct(row[key]);
  if (key === "frequency") return row.frequency == null ? "—" : Number(row.frequency).toFixed(2);
  if (key === "seats") return row.seats == null ? "—" : num(row.seats);
  return row[key] || "—";
}

export default function Performance({ data, creatives, brand, setBrand, onDrill }) {
  const [mode, setMode] = useState("campaigns");
  const [q, setQ] = useState("");
  const [sort, setSort] = useState("spend");
  const [dir, setDir] = useState(-1);
  const [open, setOpen] = useState(null);
  const forum = data.brand_funnels?.forum;
  const rungs = Object.fromEntries((forum?.rungs || []).map((r) => [r.key, r]));
  const spring = rungs.qualified;
  const justin = rungs.seat;

  const rows = useMemo(() => {
    const source = mode === "ads" ? (creatives?.ads || []) : (data.campaigns || []);
    const filtered = source.filter((row) => {
      if (brand && row.group && row.group !== brand) return false;
      if (q && !String(row.name || "").toLowerCase().includes(q.toLowerCase())) return false;
      return true;
    });
    const spend = filtered.reduce((a, r) => a + (Number(r.spend) || 0), 0);
    const impr = filtered.reduce((a, r) => a + (Number(r.impressions) || 0), 0);
    const clicks = filtered.reduce((a, r) => a + (Number(r.link_clicks) || 0), 0);
    const leads = filtered.reduce((a, r) => a + (Number(r.leads) || 0), 0);
    const totals = (brand || q || mode === "ads") ? {
      spend,
      leads,
      ctr: impr ? (clicks / impr) * 100 : null,
      cpc: clicks ? spend / clicks : null,
      cpl: leads ? spend / leads : null,
    } : null;
    return { rows: filtered.slice().sort((a, b) => {
      const av = a[sort]; const bv = b[sort];
      if (typeof av === "string" || typeof bv === "string") return String(av || "").localeCompare(String(bv || "")) * dir;
      return ((av ?? -1) - (bv ?? -1)) * dir;
    }), totals };
  }, [mode, creatives, data.campaigns, data.totals, brand, q, sort, dir]);
  const tableRows = rows.rows;
  const cards = rows.totals || data.totals || {};

  function head(col) {
    setDir(sort === col.key && dir === -1 ? 1 : -1);
    setSort(col.key);
  }

  return (
    <div style={{ display: "grid", gap: 16 }}>
      <PageTitle title="Performance" />
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: 10 }}>
        {[
          ["Spend", usd(cards.spend), null],
          ["Link CTR", pct(cards.ctr), null],
          ["CPC", usd(cards.cpc, 2), null],
          ["Meta cost / lead", usd(cards.cpl, 2), cards.leads == null ? null : `${num(cards.leads)} Meta lead actions`],
        ].map(([label, value, note]) => (
          <Card key={label} style={{ padding: 12 }}>
            <div style={{ fontFamily: FONT, fontSize: 12, fontWeight: 600, color: C.muted }}>{label}</div>
            <div style={{ fontFamily: FIG, fontSize: 22, color: C.ink, marginTop: 4 }}>{value}</div>
            {note && <div style={{ fontFamily: FONT, fontSize: 12, color: C.slate, marginTop: 4 }}>{note}</div>}
          </Card>
        ))}
      </div>

      {forum && (
        <Card>
          <div style={{ fontFamily: FONT, fontSize: 12, fontWeight: 600, color: C.muted }}>Forum VIP</div>
          <div style={{ display: "flex", gap: 18, flexWrap: "wrap", marginTop: 8 }}>
            <button type="button" onClick={() => spring && !spring.missing && onDrill?.(forum, "qualified", spring.label)}
                    style={linkBtn}>
              <div style={{ fontFamily: FONT, fontSize: 12, color: C.slate }}>Spring · leads in</div>
              <div style={{ fontFamily: FIG, fontSize: 22, color: C.ink }}>{spring?.missing ? "Not recorded" : num(spring?.n)}</div>
            </button>
            <button type="button" onClick={() => justin && !justin.missing && onDrill?.(forum, "seat", justin.label)}
                    style={linkBtn}>
              <div style={{ fontFamily: FONT, fontSize: 12, color: C.slate }}>Justin · guest seats</div>
              <div style={{ fontFamily: FIG, fontSize: 22, color: C.ink }}>{justin?.missing ? "Not recorded" : num(justin?.n)}</div>
            </button>
            <div>
              <div style={{ fontFamily: FONT, fontSize: 12, color: C.slate }}>Cost / Spring lead</div>
              <div style={{ fontFamily: FIG, fontSize: 22, color: C.ink }}>{usd(spring?.cost_per)}</div>
            </div>
            <div>
              <div style={{ fontFamily: FONT, fontSize: 12, color: C.slate }}>Cost per seat</div>
              <div style={{ fontFamily: FIG, fontSize: 22, color: C.ink }}>{usd(justin?.cost_per)}</div>
            </div>
          </div>
        </Card>
      )}

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <Btn primary={mode === "campaigns"} onClick={() => setMode("campaigns")}>Campaigns</Btn>
        <Btn primary={mode === "ads"} onClick={() => setMode("ads")}>Ads</Btn>
        <input aria-label="Filter by name" value={q} placeholder="Filter" onChange={(e) => setQ(e.target.value)}
               style={{ fontFamily: FONT, fontSize: 13, padding: "7px 10px", borderRadius: 8, border: `1px solid ${C.line}`, color: C.ink, background: C.surface }} />
        <Btn primary={!brand} onClick={() => setBrand(null)}>All brands</Btn>
        {(data.groups || []).map((g) => (
          <Btn key={g.name} primary={brand === g.name} onClick={() => setBrand(g.name)}>{g.name}</Btn>
        ))}
      </div>

      <Card style={{ padding: 0, overflow: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontFamily: FONT, fontSize: 13 }}>
          <thead>
            <tr>
              {COLS[mode].map((col) => (
                <th key={col.key} style={{ textAlign: col.num ? "right" : "left", padding: "10px 12px", color: C.muted, fontWeight: 600, cursor: "pointer" }}
                    onClick={() => head(col)}>{col.label}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {tableRows.length === 0 && (
              <tr><td colSpan={COLS[mode].length} style={{ padding: 16 }}><Empty>Nothing matches this filter.</Empty></td></tr>
            )}
            {tableRows.map((row) => (
              <tr key={row.external_id || row.id} onClick={() => setOpen(row)}
                  style={{ borderTop: `1px solid ${C.line}`, cursor: "pointer" }}>
                {COLS[mode].map((col) => (
                  <td key={col.key} style={{ padding: "10px 12px", textAlign: col.num ? "right" : "left", fontFamily: col.num ? FIG : FONT, color: C.ink }}>
                    {cell(row, col.key)}
                    {col.key === "name" && row.fatigue ? <span style={{ marginLeft: 8, color: C.warnInk, fontFamily: FONT, fontSize: 12 }}>Fatigue</span> : null}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      {open && (
        <Drawer title={open.name} onClose={() => setOpen(null)}>
          <RowActions row={open} level={mode === "ads" ? "ad" : "campaign"} data={data} />
        </Drawer>
      )}
    </div>
  );
}

const linkBtn = {
  background: "none", border: "none", padding: 0, textAlign: "left", cursor: "pointer",
};

function RowActions({ row, level, data }) {
  const writes = data.writes || {};
  const [budget, setBudget] = useState(row.daily_budget ?? "");
  const [note, setNote] = useState(null);
  const [busy, setBusy] = useState(false);
  const paused = String(row.status || "").toUpperCase() === "PAUSED";

  async function send(action, params) {
    setBusy(true);
    setNote(null);
    try {
      if (!API_BASE) {
        setNote(writes.can_write
          ? "Approved in this preview."
          : "Preview only. Reconnect Meta to enable changes.");
        return;
      }
      const out = await postJSON("/ads/writes/apply", {
        account: data.account?.id,
        action,
        level,
        external_id: row.external_id,
        reason: action.replaceAll("_", " "),
        params: params || {},
        dry_run: !writes.can_write,
      });
      const r = (out.results || [])[0] || {};
      setNote(r.meta_error || out.message || (r.dry_run ? "Preview. Nothing was sent to Meta." : "Applied."));
    } catch (e) {
      setNote(e.message || "Meta did not accept that change.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ display: "grid", gap: 14 }}>
      <p style={{ fontFamily: FONT, fontSize: 13, color: C.slate, margin: 0 }}>{row.group || row.headline || "Ad"}</p>
      <dl style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, fontFamily: FONT, fontSize: 13, margin: 0 }}>
        <div><dt style={{ color: C.muted }}>Spend</dt><dd style={{ fontFamily: FIG, margin: 0 }}>{usd(row.spend)}</dd></div>
        <div><dt style={{ color: C.muted }}>CTR</dt><dd style={{ fontFamily: FIG, margin: 0 }}>{pct(row.ctr)}</dd></div>
        <div><dt style={{ color: C.muted }}>Daily budget</dt><dd style={{ fontFamily: FIG, margin: 0 }}>{usd(row.daily_budget)}</dd></div>
        <div><dt style={{ color: C.muted }}>Frequency</dt><dd style={{ fontFamily: FIG, margin: 0 }}>{row.frequency == null ? "—" : Number(row.frequency).toFixed(2)}</dd></div>
      </dl>
      {row.status && <p style={{ fontFamily: FONT, fontSize: 13, margin: 0 }}>Status {row.status}</p>}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <Btn disabled={busy} onClick={() => send(paused ? "activate" : "pause")}>{paused ? "Turn on" : "Pause"}</Btn>
        <Btn disabled={busy} onClick={() => send("duplicate")}>Duplicate</Btn>
      </div>
      {level === "campaign" && (
        <label style={{ display: "grid", gap: 6, fontFamily: FONT, fontSize: 13, color: C.slate }}>
          Daily budget
          <span style={{ display: "flex", gap: 8 }}>
            <input aria-label="Daily budget" value={budget} onChange={(e) => setBudget(e.target.value)} style={{ ...inputStyle, width: 120 }} />
            <Btn disabled={busy || budget === "" || budget == null} onClick={() => send("set_daily_budget", { daily_budget: Number(budget) })}>Save budget</Btn>
          </span>
        </label>
      )}
      {note && <p style={{ fontFamily: FONT, fontSize: 13, color: C.slate, margin: 0 }}>{note}</p>}
    </div>
  );
}
