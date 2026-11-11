import { useState } from "react";

import { API_BASE, patchJSON } from "../../api";
import { C, FIG, FONT, HEAD, usd } from "../adsTokens.js";
import { Btn, Card, Empty, Field, PageTitle, inputStyle, weeklyPace, tone } from "../ui.jsx";

export default function Budget({ data, onSaved }) {
  const [draft, setDraft] = useState({});
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(false);
  const pace = weeklyPace(data);
  const t = tone(pace.status, pace.open);
  const groups = (data.groups || []).filter((g) => g.name !== "Other" || g.weekly_budget);

  async function save(name) {
    const typed = draft[name];
    const next = { ...(data.weekly_budgets || {}) };
    if (typed === "" || typed == null) delete next[name];
    else {
      const n = Number(typed);
      if (!Number.isFinite(n) || n < 0) {
        setMsg("Enter a weekly dollar amount.");
        return;
      }
      next[name] = n;
    }
    if (!API_BASE) {
      setMsg("This preview has no API, so the plan stays on screen only.");
      return;
    }
    setBusy(true);
    try {
      await patchJSON(`/ads/accounts/${data.account.id}`, { weekly_budgets: next });
      setDraft((d) => { const c = { ...d }; delete c[name]; return c; });
      setMsg(`Saved the weekly plan for ${name}.`);
      onSaved?.();
    } catch (e) {
      setMsg(e.message || "Could not save the weekly plan.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ display: "grid", gap: 16 }}>
      <PageTitle title="Budget" lede="The weekly plan against what this window actually spent. The open week is shown softer, not grey." />
      <Card>
        <div style={{ display: "flex", justifyContent: "space-between" }}>
          <span style={{ fontFamily: FONT, fontSize: 13, color: C.slate }}>All brands</span>
          <span style={{ fontFamily: FONT, fontSize: 13, fontWeight: 600, color: t.ink }}>
            {pace.status === "none" ? "No plan" : `${Math.round(pace.pace)}% of the plan for these days`}
            {pace.open ? " · week still open" : ""}
          </span>
        </div>
      </Card>
      {groups.length === 0 && <Empty>No campaigns in this window.</Empty>}
      {groups.map((g) => {
        const shown = draft[g.name] != null ? draft[g.name] : (g.weekly_budget ?? "");
        return (
          <Card key={g.name}>
            <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
              <div>
                <div style={{ fontFamily: HEAD, fontSize: 16, color: C.ink }}>{g.name}</div>
                <div style={{ fontFamily: FONT, fontSize: 13, color: C.slate, marginTop: 4 }}>
                  {usd(g.spend)} spent
                  {g.weekly_budget ? ` · plan ${usd(g.weekly_budget)} / week` : " · no weekly plan"}
                  {g.pace != null ? ` · ${g.pace}% of the plan for these days` : ""}
                </div>
              </div>
              <Field label="Weekly plan">
                <span style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  <input aria-label={`Weekly plan for ${g.name}`} inputMode="decimal" value={shown}
                         onChange={(e) => setDraft((d) => ({ ...d, [g.name]: e.target.value }))}
                         style={{ ...inputStyle, fontFamily: FIG }} />
                  <Btn primary disabled={busy} onClick={() => save(g.name)}>Save</Btn>
                </span>
              </Field>
            </div>
          </Card>
        );
      })}
      {msg && <p style={{ fontFamily: FONT, fontSize: 13, color: C.slate, margin: 0 }}>{msg}</p>}
    </div>
  );
}
