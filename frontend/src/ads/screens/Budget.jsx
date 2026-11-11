import { useState } from "react";

import { API_BASE, patchJSON } from "../../api";
import { C, FIG, FONT, HEAD, usd } from "../adsTokens.js";
import { Btn, Card, Empty, Field, PageTitle, inputStyle, tone } from "../ui.jsx";

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
    <div style={{ display: "grid", gap: 16 }}>
      <PageTitle title="Budget" />
      <Card>
        <div style={{ fontFamily: FONT, fontSize: 12, fontWeight: 600, color: C.muted }}>All brands</div>
        <div style={{ display: "flex", gap: 24, flexWrap: "wrap", marginTop: 8 }}>
          <Sum label="Spent" value={usd(spent)} />
          <Sum label="Plan for these days" value={planned ? usd(planned) : "No plan"} />
          <Sum label="Remaining" value={remaining == null ? "—" : remaining >= 0 ? usd(remaining) : `${usd(-remaining)} over`} />
          <Sum label="Pace" value={pace == null ? "—" : `${Math.round(pace)}%`} color={t.ink} />
        </div>
      </Card>

      {week?.days?.length > 0 && <WeekChart days={week.days} />}

      {brands.length === 0 && <Empty>No brands in this window.</Empty>}
      {brands.map((g) => {
        const shown = draft[g.name] != null ? draft[g.name] : (g.weekly_budget ?? "");
        const left = g.plan_spend == null ? null : g.plan_spend - (g.spend || 0);
        const rowTone = tone(g.pace == null ? "none" : g.pace < 90 ? "behind" : g.pace > 110 ? "ahead" : "on", true);
        return (
          <Card key={g.name}>
            <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
              <div>
                <div style={{ fontFamily: HEAD, fontSize: 16, color: C.ink }}>{g.name}</div>
                <div style={{ fontFamily: FONT, fontSize: 13, color: C.slate, marginTop: 4 }}>
                  {usd(g.spend)} spent
                  {g.weekly_budget ? ` · plan ${usd(g.weekly_budget)} / week` : " · no weekly plan"}
                  {left == null ? "" : left >= 0 ? ` · ${usd(left)} remaining` : ` · ${usd(-left)} over`}
                  {g.pace != null ? <span style={{ color: rowTone.ink }}>{` · ${g.pace}% pace`}</span> : ""}
                </div>
              </div>
              <Field label="Weekly plan">
                <input aria-label={`Weekly plan for ${g.name}`} inputMode="decimal" value={shown}
                       onChange={(e) => setDraft((d) => ({ ...d, [g.name]: e.target.value }))}
                       style={{ ...inputStyle, fontFamily: FIG }} />
              </Field>
            </div>
          </Card>
        );
      })}
      <div>
        <Btn primary disabled={busy || brands.length === 0} onClick={save}>Save weekly plan</Btn>
      </div>
      {msg && <p style={{ fontFamily: FONT, fontSize: 13, color: C.slate, margin: 0 }}>{msg}</p>}
    </div>
  );
}

function Sum({ label, value, color }) {
  return (
    <div>
      <div style={{ fontFamily: FONT, fontSize: 12, color: C.muted }}>{label}</div>
      <div style={{ fontFamily: FIG, fontSize: 18, color: color || C.ink }}>{value}</div>
    </div>
  );
}

function WeekChart({ days }) {
  const max = Math.max(1, ...days.map((d) => Math.max(d.spend || 0, d.plan || 0)));
  return (
    <Card>
      <div style={{ fontFamily: FONT, fontSize: 13, fontWeight: 600, color: C.ink }}>This week, Monday to Sunday</div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(7, 1fr)", gap: 8, alignItems: "end", marginTop: 14, height: 120 }}>
        {days.map((d) => {
          const h = Math.round(((d.spend || 0) / max) * 100);
          const plan = d.plan ? Math.round((d.plan / max) * 100) : 0;
          const bar = d.future || d.open
            ? `color-mix(in srgb, ${C.accent} 58%, ${C.parchment})`
            : C.accent;
          return (
            <div key={d.date} style={{ display: "flex", flexDirection: "column", alignItems: "center", height: "100%", justifyContent: "flex-end" }}>
              <div style={{ position: "relative", width: "70%", height: "100%" }}>
                {plan > 0 && (
                  <div style={{ position: "absolute", left: 0, right: 0, bottom: `${plan}%`, borderTop: `2px solid ${C.slate}` }} />
                )}
                <div style={{ position: "absolute", left: 0, right: 0, bottom: 0, height: `${h}%`, background: bar, borderRadius: 4 }} />
              </div>
            </div>
          );
        })}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(7, 1fr)", gap: 8, marginTop: 6 }}>
        {days.map((d) => (
          <div key={d.date} style={{ textAlign: "center", fontFamily: FONT, fontSize: 11, color: d.open || d.future ? C.slate : C.muted }}>{d.label}</div>
        ))}
      </div>
    </Card>
  );
}
