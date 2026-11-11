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

  async function save() {
    const next = { ...(data.weekly_budgets || {}) };
    for (const g of groups) {
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

  return (
    <div style={{ display: "grid", gap: 16 }}>
      <PageTitle title="Budget" />
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
                  {g.pace != null ? <span style={{ color: t.ink }}>{` · ${g.pace}% of the plan`}</span> : null}
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
        <Btn primary disabled={busy || groups.length === 0} onClick={save}>Save weekly plan</Btn>
      </div>
      {msg && <p style={{ fontFamily: FONT, fontSize: 13, color: C.slate, margin: 0 }}>{msg}</p>}
    </div>
  );
}
