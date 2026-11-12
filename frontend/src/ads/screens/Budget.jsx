import { useState } from "react";

import { API_BASE, patchJSON } from "../../api";
import { C, usd } from "../adsTokens.js";
import { accountToday, brandPace, windowPace, windowPhrase } from "../pace.js";
import { Btn, Card, Empty, MoneyInput, PageTitle, Stat, tone } from "../ui.jsx";

export default function Budget({ data, onSaved }) {
  const [draft, setDraft] = useState({});
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(false);
  const brands = (data.groups || []).filter((g) => g.name !== "Other");
  const week = data.week;

  async function save() {
    const next = { ...(data.weekly_budgets || {}) };
    for (const g of brands) {
      const typed = draft[g.name] != null ? draft[g.name] : g.weekly_budget;
      if (typed === "" || typed == null) {
        delete next[g.name];
        continue;
      }
      const n = Number(typed);
      if (!Number.isFinite(n) || n < 0) {
        setMsg(`Enter a weekly dollar amount for ${g.name}.`);
        return;
      }
      next[g.name] = n;
    }
    if (!API_BASE) {
      setMsg("This preview has no API, so the plan stays on screen only.");
      return;
    }
    setBusy(true);
    try {
      await patchJSON(`/ads/accounts/${data.account.id}`, { weekly_budgets: next });
      setDraft({});
      setMsg("Saved the weekly plan.");
      onSaved?.();
    } catch (e) {
      setMsg(e.message || "Could not save the weekly plan.");
    } finally {
      setBusy(false);
    }
  }

  // One helper for the headline and the rows, shared with Today, so the two screens cannot
  // disagree. For a window that runs past today it is the plan so far, not the window's whole.
  const pace = windowPace(data, accountToday(data));
  const planned = pace.expected;
  const remaining = planned ? planned - pace.spend : null;
  const t = tone(pace.status, true);
  const planLabel = pace.partial ? "Plan through today" : "Plan for these days";
  const over = remaining != null && remaining < 0;

  return (
    <div className="ax-stack">
      <PageTitle title="Budget" />
      <div className="ax-stats">
        <Stat label="Spent" value={usd(pace.spend)} />
        <Stat label={planLabel} value={planned ? usd(planned) : "No plan"} />
        {/* The label says what the figure is: "Remaining $11,501 over" asked the reader to
            notice the last word to learn the figure meant the opposite of its label. */}
        <Stat label={over ? "Over plan" : "Remaining"} value={remaining == null ? "—" : usd(Math.abs(remaining))}
              color={over ? C.badInk : undefined} />
        <Stat label={`Pace, ${windowPhrase(data.range?.label)}`}
              value={pace.pace == null ? "—" : `${Math.round(pace.pace)}%`}
              color={pace.pace == null ? undefined : t.ink} />
      </div>

      {week?.days?.length > 0 && <WeekChart days={week.days} />}

      <Card title="Weekly plan by brand" flush
            foot={<>
              <Btn primary disabled={busy || brands.length === 0} onClick={save}>Save weekly plan</Btn>
              {msg && <span className="ax-text">{msg}</span>}
            </>}>
        {brands.length === 0 ? <div className="ax-empty"><Empty>No brands in this window.</Empty></div> : (
          <div className="ax-scroll">
            <table className="ax-table ax-table--stack ax-table--wide" style={{ "--ax-minw": "720px" }}>
              <thead>
                <tr>
                  <th scope="col">Brand</th>
                  <th scope="col" className="num">Spent</th>
                  <th scope="col" className="num">{planLabel}</th>
                  <th scope="col" className="num">Over or left</th>
                  <th scope="col" className="num">Pace</th>
                  <th scope="col" className="num">Weekly plan</th>
                </tr>
              </thead>
              <tbody>
                {brands.map((g) => {
                  const shown = draft[g.name] != null ? draft[g.name] : (g.weekly_budget ?? "");
                  const row = brandPace(g, pace.elapsed);
                  const rowTone = tone(row.status, true);
                  return (
                    <tr key={g.name}>
                      <td className="ax-strong ax-cell-title">{g.name}</td>
                      <td className="num" data-label="Spent">{usd(g.spend)}</td>
                      <td className="num" data-label={planLabel}>{row.plan == null ? "No plan" : usd(row.plan)}</td>
                      <td className="num" data-label="Over or left"
                          style={row.left != null && row.left < 0 ? { color: C.badInk } : undefined}>
                        {row.left == null ? "—" : row.left >= 0 ? `${usd(row.left)} left` : `${usd(-row.left)} over`}
                      </td>
                      <td className="num" data-label="Pace"
                          style={row.pace == null ? undefined : { color: rowTone.ink }}>
                        {row.pace == null ? "—" : `${row.pace.toFixed(1)}%`}
                      </td>
                      <td className="num" data-label="Weekly plan">
                        <MoneyInput label={`Weekly plan for ${g.name}`} value={shown}
                                    onChange={(e) => setDraft((d) => ({ ...d, [g.name]: e.target.value }))} />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}

function WeekChart({ days }) {
  const max = Math.max(1, ...days.map((d) => Math.max(d.spend || 0, d.plan || 0)));
  return (
    <Card title="This week, Monday to Sunday"
          meta={<span className="ax-key">
            <span><i />Spend</span>
            <span><i className="is-line" />Plan</span>
          </span>}>
      <div className="ax-week">
        {days.map((d) => {
          const h = Math.round(((d.spend || 0) / max) * 100);
          const plan = d.plan ? Math.round((d.plan / max) * 100) : 0;
          const bar = d.future || d.open
            ? `color-mix(in srgb, ${C.accent} 58%, ${C.parchment})`
            : C.accent;
          return (
            <div key={d.date}>
              <div className="ax-week__plot">
                <div className="ax-week__col">
                  {plan > 0 && <div className="ax-week__plan" style={{ bottom: `${plan}%` }} />}
                  <div className="ax-week__spend" style={{ height: `${h}%`, background: bar }} />
                </div>
              </div>
              <div className={`ax-week__day${d.open || d.future ? " is-open" : ""}`}>{d.label}</div>
            </div>
          );
        })}
      </div>
    </Card>
  );
}
