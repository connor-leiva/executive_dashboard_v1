import { useState } from "react";

import { API_BASE, patchJSON, postJSON } from "../../api";
import { C, FONT } from "../adsTokens.js";
import GroupingRules from "../GroupingRules.jsx";
import { Banner, Btn, Card, Field, PageTitle, inputStyle } from "../ui.jsx";

export default function SettingsScreen({ data, account, onSaved }) {
  const writes = data.writes || {};
  const [autopilot, setAutopilot] = useState(writes.autopilot || {});
  const [killed, setKilled] = useState(!!writes.killed);
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(false);

  async function checkAccess() {
    setBusy(true);
    setMsg(null);
    try {
      if (!API_BASE) {
        setMsg("This preview has no Meta token to check.");
        return;
      }
      const out = await postJSON("/ads/writes/scope", { account: data.account?.id });
      setMsg(out.can_write ? "Meta accepted the check. Changes can be applied." : (out.detail || out.reconnect));
      onSaved?.();
    } catch (e) {
      setMsg(e.message || "Meta did not accept the access check.");
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    setBusy(true);
    setMsg(null);
    try {
      if (!API_BASE) {
        setMsg("This preview has no API, so the guardrails are not saved.");
        return;
      }
      await patchJSON(`/ads/accounts/${data.account.id}`, { autopilot, writes_killed: killed });
      setMsg("Saved.");
      onSaved?.();
    } catch (e) {
      setMsg(e.message || "Could not save.");
    } finally {
      setBusy(false);
    }
  }

  async function fixTags() {
    setBusy(true);
    setMsg(null);
    try {
      if (!API_BASE) {
        setMsg("Fix tagging would write utm_content={{ad.id}}. This preview does not send it.");
        return;
      }
      const out = await postJSON("/ads/writes/apply", {
        account: data.account?.id, action: "fix_tagging", dry_run: !writes.can_write, reason: "Fix tagging",
      });
      setMsg(out.message || `${(out.results || []).length} ads checked.`);
      onSaved?.();
    } catch (e) {
      setMsg(e.message || "Fix tagging failed.");
    } finally {
      setBusy(false);
    }
  }

  const brands = (data.groups || []).map((g) => g.name).filter((n) => n !== "Other");

  return (
    <div style={{ display: "grid", gap: 16 }}>
      <PageTitle title="Settings" lede="Connections, how campaigns are grouped, and the guardrails around autopilot." />

      <Card>
        <div style={{ fontFamily: FONT, fontSize: 12, fontWeight: 600, color: C.muted }}>Connections</div>
        <p style={{ fontFamily: FONT, fontSize: 14, color: C.ink, margin: "8px 0 0" }}>
          Meta · {data.account?.name} · {data.account?.external_id}
        </p>
        <p style={{ fontFamily: FONT, fontSize: 13, color: C.slate, margin: "4px 0 0" }}>
          Scope {writes.scope || "not checked"}. GoHighLevel supplies the leads. Zoom attendance is on Session Attendance.
        </p>
        {!writes.can_write && (
          <Banner title="Reconnect Meta to enable changes" tone="bad">
            Paste a System User token with ads_read and ads_management in Settings, then check access here.
          </Banner>
        )}
        <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
          <Btn primary disabled={busy} onClick={checkAccess}>Check access</Btn>
          <Btn disabled={busy} onClick={fixTags}>Fix tagging</Btn>
          <Btn disabled={busy} onClick={async () => {
            if (!API_BASE) { setMsg("Rebuild attribution needs the API."); return; }
            setBusy(true);
            try {
              const out = await postJSON("/ads/recompute", {});
              setMsg(typeof out?.conversions?.written === "number" ? `Rebuilt ${out.conversions.written} stage rows.` : "Rebuilt.");
              onSaved?.();
            } catch (e) { setMsg(e.message || "Rebuild failed."); }
            finally { setBusy(false); }
          }}>Rebuild attribution</Btn>
        </div>
      </Card>

      <Card>
        <label style={{ fontFamily: FONT, fontSize: 14, color: C.ink, display: "flex", gap: 8 }}>
          <input type="checkbox" checked={killed} onChange={(e) => setKilled(e.target.checked)} />
          Stop all ad changes
        </label>
        <div style={{ display: "grid", gap: 10, marginTop: 14 }}>
          {brands.map((name) => {
            const cfg = autopilot[name] || {};
            return (
              <div key={name} style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center", fontFamily: FONT, fontSize: 13 }}>
                <span style={{ minWidth: 180, color: C.ink }}>{name}</span>
                <label><input type="checkbox" checked={!!cfg.on} onChange={(e) => setAutopilot((a) => ({ ...a, [name]: { ...cfg, on: e.target.checked } }))} /> Autopilot</label>
                <label><input type="checkbox" checked={!!cfg.pause_only} onChange={(e) => setAutopilot((a) => ({ ...a, [name]: { ...cfg, pause_only: e.target.checked } }))} /> Pause only</label>
                <Field label="Max daily $">
                  <input aria-label={`Max daily spend for ${name}`} value={cfg.max_daily_spend ?? ""} inputMode="decimal"
                         onChange={(e) => setAutopilot((a) => ({ ...a, [name]: { ...cfg, max_daily_spend: e.target.value } }))}
                         style={inputStyle} />
                </Field>
              </div>
            );
          })}
        </div>
        <p style={{ fontFamily: FONT, fontSize: 12, color: C.muted, lineHeight: 1.5 }}>
          Autopilot is off until you turn it on. It will not raise a budget more than 20% at a time or past the weekly plan, and it waits for $25 and 1,000 impressions.
        </p>
        <Btn primary disabled={busy} onClick={save}>Save guardrails</Btn>
      </Card>

      <Card>
        <div style={{ fontFamily: FONT, fontSize: 14, fontWeight: 600, color: C.ink, marginBottom: 8 }}>Grouping and UTM</div>
        <GroupingRules account={account} onSaved={onSaved} />
      </Card>
      {msg && <p style={{ fontFamily: FONT, fontSize: 13, color: C.slate, margin: 0 }}>{msg}</p>}
    </div>
  );
}
