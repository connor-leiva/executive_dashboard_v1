import { useState } from "react";

import { API_BASE, patchJSON, postJSON } from "../../api";
import GroupingRules from "../GroupingRules.jsx";
import { Banner, Btn, Card, MoneyInput, PageTitle } from "../ui.jsx";

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

  const synced = data.freshness?.last_synced_at ? new Date(data.freshness.last_synced_at) : null;
  const syncedText = synced && !Number.isNaN(synced.getTime())
    ? synced.toLocaleString(undefined, { month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" })
    : data.freshness?.last_synced_at;

  return (
    <div className="ax-stack">
      <PageTitle title="Settings" />

      <Card title="Connections"
            foot={<>
              <Btn disabled={busy} onClick={checkAccess}>Check access</Btn>
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
            </>}>
        <div className="ax-list">
          <Connection name="Meta" detail={[
            data.account?.name || "Ad account",
            data.account?.external_id,
            writes.scope ? `Token scope ${writes.scope}` : "Token scope not checked",
          ]} />
          <Connection name="GoHighLevel" detail="Supplies Spring's leads." />
          <Connection name="Zoom" detail="Attendance is on Session Attendance." />
        </div>
        {!writes.can_write && (
          <div style={{ marginTop: 16 }}>
            {/* This screen is itself called Settings, so "in Settings" sent people round in a
                circle. The token goes in the workspace's Integrations page, under Meta Ads. */}
            <Banner title="Reconnect Meta to enable changes">
              Paste a System User token with ads_read and ads_management under Meta Ads
              in <a href="/settings/integrations" className="ax-link">Integrations</a>, then
              come back and press Check access.
            </Banner>
          </div>
        )}
      </Card>
      {msg && <p className="ax-text" role="status">{msg}</p>}

      <Card title="Guardrails"
            foot={<>
              <button type="button" role="switch" aria-checked={killed} className="ax-switch"
                      onClick={() => setKilled((v) => !v)}>
                <span className="ax-switch__track" aria-hidden="true" />
                Stop all ad changes
              </button>
              <Btn primary disabled={busy} onClick={save}>Save guardrails</Btn>
            </>}>
        <div className="ax-scroll">
          <table className="ax-table ax-table--plain ax-table--narrow">
            <thead>
              <tr>
                {["Brand", "Autopilot", "Pause only", "Max daily spend"].map((h) => <th key={h} scope="col">{h}</th>)}
              </tr>
            </thead>
            <tbody>
              {brands.map((name) => {
                const cfg = autopilot[name] || {};
                return (
                  <tr key={name}>
                    <td className="ax-strong ax-cell-title" style={{ verticalAlign: "middle" }}>{name}</td>
                    <td data-label="Autopilot" style={{ verticalAlign: "middle" }}><label className="ax-check"><input type="checkbox" aria-label={`Autopilot for ${name}`} checked={!!cfg.on} onChange={(e) => setAutopilot((a) => ({ ...a, [name]: { ...cfg, on: e.target.checked } }))} /></label></td>
                    <td data-label="Pause only" style={{ verticalAlign: "middle" }}><label className="ax-check"><input type="checkbox" aria-label={`Pause only for ${name}`} checked={!!cfg.pause_only} onChange={(e) => setAutopilot((a) => ({ ...a, [name]: { ...cfg, pause_only: e.target.checked } }))} /></label></td>
                    <td data-label="Max daily spend" style={{ verticalAlign: "middle" }}>
                      <MoneyInput label={`Max daily spend for ${name}`} value={cfg.max_daily_spend ?? ""}
                                  onChange={(e) => setAutopilot((a) => ({ ...a, [name]: { ...cfg, max_daily_spend: e.target.value } }))} />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <p className="ax-meta" style={{ marginTop: 16, maxWidth: "72ch" }}>
          Autopilot is off until you turn it on. It will not raise a budget more than 20% at a time or past the weekly plan, and it waits for $25 and 1,000 impressions. Max daily spend is the most it may spend on that brand in a day.
        </p>
      </Card>

      {otherCount > 0 && (
        <Banner title={`${otherCount} campaign${otherCount === 1 ? "" : "s"} in Other`}>
          These names did not match a brand rule.{" "}
          <button type="button" className="ax-link"
                  onClick={() => document.getElementById("ads-grouping")?.scrollIntoView({ behavior: "smooth" })}>
            Edit grouping rules
          </button>
        </Banner>
      )}

      <Card title="About the numbers">
        <p className="ax-text">
          Meta cost per lead counts Meta lead actions. Spring leads are qualified Forum guests from GoHighLevel. Justin&apos;s guest seats are confirmed seats, which can outnumber Spring&apos;s leads. Revenue is booked only when a payment is matched to an ad.
          {syncedText ? ` Last synced ${syncedText}.` : " Not synced yet."}
        </p>
      </Card>

      <Card id="ads-grouping" title="Grouping and UTM">
        <GroupingRules account={account} onSaved={onSaved} />
      </Card>
    </div>
  );
}

/* A connection and its facts, one fact to a line. */
function Connection({ name, detail }) {
  const parts = (Array.isArray(detail) ? detail : [detail]).filter(Boolean);
  return (
    <div className="ax-kv">
      <span className="ax-kv__name">{name}</span>
      <span className="ax-kv__value">
        {parts.map((p) => <span key={p} className="ax-kv__part">{p}</span>)}
      </span>
    </div>
  );
}
