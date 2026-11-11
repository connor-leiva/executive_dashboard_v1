import { useState } from "react";

import { API_BASE, patchJSON, postJSON } from "../../api";
import { C, FONT } from "../adsTokens.js";
import GroupingRules from "../GroupingRules.jsx";
import { Banner, Btn, Card, PageTitle, inputStyle } from "../ui.jsx";

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
  const other = (data.groups || []).find((g) => g.name === "Other");
  const otherCount = other?.campaigns?.length || data.unmatched_count || 0;

  return (
    <div style={{ display: "grid", gap: 16 }}>
      <PageTitle title="Settings" />

      <Card>
        <div style={{ fontFamily: FONT, fontSize: 12, fontWeight: 600, color: C.muted }}>Connections</div>
        <Connection name="Meta" detail={`${data.account?.name || "Ad account"} · ${data.account?.external_id || ""} · ${writes.scope || "not checked"}`} />
        <Connection name="GoHighLevel" detail="Supplies Spring's leads." />
        <Connection name="Zoom" detail="Attendance is on Session Attendance." />
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
        <table style={{ width: "100%", borderCollapse: "collapse", fontFamily: FONT, fontSize: 13 }}>
          <thead>
            <tr>
              {["Brand", "Autopilot", "Pause only", "Max daily spend"].map((h) => (
                <th key={h} style={{ textAlign: "left", padding: "6px 8px", color: C.muted, fontWeight: 600 }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {brands.map((name) => {
              const cfg = autopilot[name] || {};
              return (
                <tr key={name} style={{ borderTop: `1px solid ${C.line}` }}>
                  <td style={{ padding: "8px", color: C.ink }}>{name}</td>
                  <td style={{ padding: "8px" }}><input type="checkbox" aria-label={`Autopilot for ${name}`} checked={!!cfg.on} onChange={(e) => setAutopilot((a) => ({ ...a, [name]: { ...cfg, on: e.target.checked } }))} /></td>
                  <td style={{ padding: "8px" }}><input type="checkbox" aria-label={`Pause only for ${name}`} checked={!!cfg.pause_only} onChange={(e) => setAutopilot((a) => ({ ...a, [name]: { ...cfg, pause_only: e.target.checked } }))} /></td>
                  <td style={{ padding: "8px" }}>
                    <input aria-label={`Max daily spend for ${name}`} value={cfg.max_daily_spend ?? ""} inputMode="decimal"
                           onChange={(e) => setAutopilot((a) => ({ ...a, [name]: { ...cfg, max_daily_spend: e.target.value } }))}
                           style={inputStyle} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <p style={{ fontFamily: FONT, fontSize: 12, color: C.muted, lineHeight: 1.5 }}>
          Autopilot is off until you turn it on. It will not raise a budget more than 20% at a time or past the weekly plan, and it waits for $25 and 1,000 impressions. Max daily spend is the most it may spend on that brand in a day.
        </p>
        <div style={{ display: "flex", gap: 16, alignItems: "center", flexWrap: "wrap" }}>
          <button type="button" role="switch" aria-checked={killed} onClick={() => setKilled((v) => !v)}
                  style={{ display: "inline-flex", alignItems: "center", gap: 8, background: "none", border: "none", padding: 0, cursor: "pointer", fontFamily: FONT, fontSize: 14, color: C.ink }}>
            <span style={{ width: 36, height: 20, borderRadius: 999, background: killed ? C.badBar : C.line, position: "relative" }}>
              <span style={{ position: "absolute", top: 2, left: killed ? 18 : 2, width: 16, height: 16, borderRadius: 999, background: C.surface }} />
            </span>
            Stop all ad changes
          </button>
          <Btn primary disabled={busy} onClick={save}>Save guardrails</Btn>
        </div>
      </Card>

      {otherCount > 0 && (
        <Banner title={`${otherCount} campaign${otherCount === 1 ? "" : "s"} in Other`} tone="bad">
          These names did not match a brand rule.
          <button type="button" onClick={() => document.getElementById("ads-grouping")?.scrollIntoView({ behavior: "smooth" })}
                  style={{ marginLeft: 8, background: "none", border: "none", padding: 0, color: C.accent, cursor: "pointer", font: "inherit" }}>
            Edit grouping rules
          </button>
        </Banner>
      )}

      <Card>
        <div style={{ fontFamily: FONT, fontSize: 14, fontWeight: 600, color: C.ink }}>About the numbers</div>
        <p style={{ fontFamily: FONT, fontSize: 13, color: C.slate, lineHeight: 1.5 }}>
          Meta cost per lead counts Meta lead actions. Spring leads are qualified Forum guests from GoHighLevel. Justin&apos;s guest seats are confirmed seats, which can outnumber Spring&apos;s leads. Revenue is booked only when a payment is matched to an ad.
          {data.freshness?.last_synced_at ? ` Last synced ${new Date(data.freshness.last_synced_at).toLocaleString()}.` : " Not synced yet."}
        </p>
      </Card>

      <Card id="ads-grouping">
        <div style={{ fontFamily: FONT, fontSize: 14, fontWeight: 600, color: C.ink, marginBottom: 8 }}>Grouping and UTM</div>
        <GroupingRules account={account} onSaved={onSaved} />
      </Card>
      {msg && <p style={{ fontFamily: FONT, fontSize: 13, color: C.slate, margin: 0 }}>{msg}</p>}
    </div>
  );
}

function Connection({ name, detail }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", gap: 12, padding: "10px 0", borderTop: `1px solid ${C.line}`, marginTop: 8 }}>
      <span style={{ fontFamily: FONT, fontSize: 14, fontWeight: 600, color: C.ink }}>{name}</span>
      <span style={{ fontFamily: FONT, fontSize: 13, color: C.slate, textAlign: "right" }}>{detail}</span>
    </div>
  );
}
