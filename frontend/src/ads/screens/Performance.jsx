import { useMemo, useState } from "react";

import { API_BASE, postJSON } from "../../api";
import { C, num, pct, usd } from "../adsTokens.js";
import {
  Btn, Card, Drawer, Empty, Glyph, MoneyInput, PageTitle, Segmented, Stat, rowKeys, sentenceStatus,
} from "../ui.jsx";

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

  const brandOptions = [["", "All brands"], ...(data.groups || []).map((g) => [g.name, g.name])];
  const openKey = open && (open.external_id || open.id);

  return (
    <div className="ax-stack">
      <PageTitle title="Performance" />
      <div className="ax-stats">
        <Stat label="Spend" value={usd(cards.spend)} />
        <Stat label="Link CTR" value={pct(cards.ctr)} />
        <Stat label="CPC" value={usd(cards.cpc, 2)} />
        <Stat label="Meta cost / lead" value={usd(cards.cpl, 2)}
              note={cards.leads == null ? null : `${num(cards.leads)} Meta lead actions`} />
      </div>

      {forum && (
        <section className="ax-section" aria-labelledby="ax-forum-vip">
          <h3 id="ax-forum-vip" className="ax-card__title">Forum VIP</h3>
          <div className="ax-stats">
            <Stat label="Spring · leads in" value={spring?.missing ? "Not recorded" : num(spring?.n)}
                  hint="See who these leads are"
                  onClick={spring && !spring.missing ? () => onDrill?.(forum, "qualified", spring.label) : undefined} />
            <Stat label="Justin · guest seats" value={justin?.missing ? "Not recorded" : num(justin?.n)}
                  hint="See who holds these seats"
                  onClick={justin && !justin.missing ? () => onDrill?.(forum, "seat", justin.label) : undefined} />
            <Stat label="Cost / Spring lead" value={usd(spring?.cost_per)} />
            <Stat label="Cost / seat" value={usd(justin?.cost_per)} />
          </div>
          {spring && justin && !spring.missing && !justin.missing && Number(justin.n) > Number(spring.n) && (
            <p className="ax-meta">
              Guest seats can outnumber Spring&apos;s leads. A seat is a confirmed guest; a lead is someone Spring brought in during this window.
            </p>
          )}
        </section>
      )}

      <div className="ax-row" style={{ gap: 12 }}>
        <Segmented label="Show" value={mode} onChange={setMode}
                   options={[["campaigns", "Campaigns"], ["ads", "Ads"]]} />
        <input className="ax-input ax-search" aria-label="Filter by name" value={q}
               placeholder="Filter by name" onChange={(e) => setQ(e.target.value)} />
        <Segmented label="Brand" value={brand || ""} onChange={(k) => setBrand(k || null)}
                   options={brandOptions} />
      </div>

      <Card flush>
        <div className="ax-scroll">
          <table className="ax-table" style={{ minWidth: 640 }}>
            <thead>
              <tr>
                {COLS[mode].map((col) => {
                  const on = sort === col.key;
                  return (
                    <th key={col.key} className={col.num ? "num" : undefined}
                        aria-sort={on ? (dir === -1 ? "descending" : "ascending") : undefined}>
                      <button type="button" className="ax-sort" data-on={on ? "" : undefined}
                              onClick={() => head(col)}>
                        {col.label}
                        <Glyph name={on && dir === 1 ? "up" : "down"} size={12} />
                      </button>
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody>
              {tableRows.length === 0 && (
                <tr><td colSpan={COLS[mode].length}><Empty>Nothing matches this filter.</Empty></td></tr>
              )}
              {tableRows.map((row) => {
                const on = openKey === (row.external_id || row.id);
                return (
                  <tr key={row.external_id || row.id} className={`is-link${on ? " is-on" : ""}`}
                      tabIndex={0} onClick={() => setOpen(row)} onKeyDown={rowKeys(() => setOpen(row))}>
                    {COLS[mode].map((col) => (
                      <td key={col.key}
                          className={col.num ? "num" : col.key === "name" ? "ax-strong" : "ax-soft"}>
                        {cell(row, col.key)}
                        {col.key === "name" && row.fatigue ? <span className="ax-tag">Fatigue</span> : null}
                      </td>
                    ))}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Card>

      {open && (
        <Drawer title={open.name} onClose={() => setOpen(null)}>
          <RowDetail row={open} creatives={creatives} level={mode === "ads" ? "ad" : "campaign"} data={data} />
        </Drawer>
      )}
    </div>
  );
}

function blankWhy(label, value, row) {
  if (value != null && value !== "") return null;
  if (String(row.status || "").toUpperCase() === "PAUSED") return `${label} is blank because this is paused.`;
  if (label === "Frequency") return "Frequency was not fetched for this row.";
  if (label === "Daily budget") return "No daily budget is stored for this campaign.";
  return `${label} is not on this row.`;
}

function RowDetail({ row, creatives, level, data }) {
  const extra = (creatives?.ads || []).find((a) => a.external_id && a.external_id === row.external_id) || {};
  const view = { ...extra, ...row, thumbnail_url: row.thumbnail_url || extra.thumbnail_url, people: row.people || extra.people || [] };
  return <RowActions row={view} level={level} data={data} />;
}

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

  const blanks = [blankWhy("Daily budget", row.daily_budget, row), blankWhy("Frequency", row.frequency, row)].filter(Boolean);

  return (
    <div className="ax-stack">
      {row.thumbnail_url && <img className="ax-thumb" src={row.thumbnail_url} alt="" />}
      <p className="ax-meta">{row.group || row.headline || "Ad"}</p>
      <dl className="ax-dl">
        <div><dt>Spend</dt><dd>{usd(row.spend)}</dd></div>
        <div><dt>Leads</dt><dd>{num(row.leads)}</dd></div>
        <div><dt>Seats</dt><dd>{row.seats == null ? "—" : num(row.seats)}</dd></div>
        <div><dt>CTR</dt><dd>{pct(row.ctr)}</dd></div>
        <div><dt>Daily budget</dt><dd>{usd(row.daily_budget)}</dd></div>
        <div><dt>Frequency</dt><dd>{row.frequency == null ? "—" : Number(row.frequency).toFixed(2)}</dd></div>
        {row.status && <div><dt>Status</dt><dd style={{ fontFamily: "var(--font-text)" }}>{sentenceStatus(row.status)}</dd></div>}
      </dl>
      {blanks.map((line) => <p key={line} className="ax-meta">{line}</p>)}
      {(row.people || []).length > 0 && (
        <div>
          <div className="ax-label">People</div>
          {row.people.map((p) => (
            <div key={p.email || p.name} className="ax-text" style={{ color: C.ink }}>{p.name}</div>
          ))}
        </div>
      )}
      <div className="ax-row">
        <Btn disabled={busy} onClick={() => send(paused ? "activate" : "pause")}>{paused ? "Turn on" : "Pause"}</Btn>
        <Btn disabled={busy} onClick={() => send("duplicate")}>Duplicate</Btn>
      </div>
      {level === "campaign" && (
        <div style={{ display: "grid", gap: 6 }}>
          <span className="ax-label">Daily budget</span>
          <span className="ax-row">
            <MoneyInput label="Daily budget" value={budget} onChange={(e) => setBudget(e.target.value)} />
            <Btn disabled={busy || budget === "" || budget == null} onClick={() => send("set_daily_budget", { daily_budget: Number(budget) })}>Save budget</Btn>
          </span>
        </div>
      )}
      {note && <p className="ax-text">{note}</p>}
    </div>
  );
}
