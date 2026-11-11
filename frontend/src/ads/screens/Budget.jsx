import { useState } from "react";

import { API_BASE, patchJSON } from "../../api";
import { C, usd } from "../adsTokens.js";
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

  const all = data.groups || [];
  const spent = all.reduce((a, g) => a + (Number(g.spend) || 0), 0);
  const planned = brands.reduce((a, g) => a + (Number(g.plan_spend) || 0), 0);
  const remaining = planned ? planned - spent : null;
  const pace = planned ? (spent / planned) * 100 : null;
  const status = pace == null ? "none" : pace < 90 ? "behind" : pace > 110 ? "ahead" : "on";
  const t = tone(status, true);

  return (
    <div className="ax-stack">
      <PageTitle title="Budget" />
      <div className="ax-stats">
        <Stat label="Spent" value={usd(spent)} />
        <Stat label="Plan for these days" value={planned ? usd(planned) : "No plan"} />
        <Stat label="Remaining" value={remaining == null ? "—" : remaining >= 0 ? usd(remaining) : `${usd(-remaining)} over`} />
        <Stat label="Pace" value={pace == null ? "—" : `${Math.round(pace)}%`} color={t.ink} />
      </div>

      {week?.days?.length > 0 && <WeekChart days={week.days} />}

      <Card title="Weekly plan by brand" flush
            foot={<>
              <Btn primary disabled={busy || brands.length === 0} onClick={save}>Save weekly plan</Btn>
              {msg && <span className="ax-text">{msg}</span>}
            </>}>
        {brands.length === 0 && <div className="ax-empty"><Empty>No brands in this window.</Empty></div>}
        <div style={{ padding: "0 var(--ax-pad)" }}>
          {brands.length > 0 && (
            <div className="ax-listhead ax-planrow">
              <span className="ax-label">Brand</span>
              <span className="ax-label">Weekly plan</span>
            </div>
          )}
          <div className="ax-list">
            {brands.map((g) => {
              const shown = draft[g.name] != null ? draft[g.name] : (g.weekly_budget ?? "");
              const left = g.plan_spend == null ? null : g.plan_spend - (g.spend || 0);
              const rowTone = tone(g.pace == null ? "none" : g.pace < 90 ? "behind" : g.pace > 110 ? "ahead" : "on", true);
              return (
                <div key={g.name} className="ax-planrow" style={{ padding: "12px 0" }}>
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontWeight: 600 }}>{g.name}</div>
                    <div className="ax-meta" style={{ color: C.slate }}>
                      <span className="ax-num">{usd(g.spend)}</span> spent
                      {g.weekly_budget ? ` · plan ${usd(g.weekly_budget)} / week` : " · no weekly plan"}
                      {left == null ? "" : left >= 0 ? ` · ${usd(left)} remaining` : ` · ${usd(-left)} over`}
                      {g.pace != null ? <span style={{ color: rowTone.ink }}>{` · ${g.pace}% pace`}</span> : ""}
                    </div>
                  </div>
                  <MoneyInput label={`Weekly plan for ${g.name}`} value={shown}
                              onChange={(e) => setDraft((d) => ({ ...d, [g.name]: e.target.value }))} />
                </div>
              );
            })}
          </div>
        </div>
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
