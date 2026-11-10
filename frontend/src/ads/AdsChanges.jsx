/* Changes to the ad account. Preview is the default until the token can manage ads.

Apply asks the server, which asks Meta with validate_only when the token cannot write.
The server's answer, including Meta's error, is shown under the button that sent it.
 */
import { useState } from "react";

import { API_BASE, patchJSON, postJSON } from "../api";
import { C, FIG, FONT, HEAD, usd } from "./adsTokens.js";

const VERDICT = {
  kill: { label: "Kill", bg: "#F8E6E1", ink: "#8C3A2A" },
  keep: { label: "Keep", bg: "#E7F0EA", ink: "#1F4D38" },
  scale: { label: "Scale", bg: "#E4EEF6", ink: "#1D4E73" },
};

function Pill({ verdict }) {
  const tone = VERDICT[verdict] || VERDICT.keep;
  return (
    <span style={{ fontFamily: FONT, fontSize: 12, fontWeight: 600, color: tone.ink,
                   background: tone.bg, borderRadius: 999, padding: "2px 8px" }}>{tone.label}</span>
  );
}

export default function AdsChanges({ data, onSaved }) {
  const writes = data.writes || {};
  const items = data.recommendations || [];
  const [picked, setPicked] = useState({});
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [autopilot, setAutopilot] = useState(writes.autopilot || {});
  const [killed, setKilled] = useState(!!writes.killed);

  const actionable = items.filter((i) => i.action);
  const selected = actionable.filter((i) => picked[`${i.level}:${i.external_id}`]);

  async function send(body) {
    setBusy(true);
    setError(null);
    try {
      if (!API_BASE) {
        setResult({
          message: writes.message || writes.reconnect || "Preview only.",
          forced_dry_run: !writes.can_write,
          results: (body.items || [{ name: body.external_id, action: body.action }]).map((item) => ({
            name: item.name || item.external_id,
            action: item.action || body.action,
            dry_run: true,
            validated: false,
            validation_note: "This preview has no Meta token, so nothing was sent.",
          })),
        });
        return;
      }
      const out = await postJSON("/ads/writes/apply", {
        account: data.account?.id, period: "30d", ...body,
      });
      setResult(out);
      onSaved?.();
    } catch (e) {
      setError(e.message || "Meta did not accept that change.");
    } finally {
      setBusy(false);
    }
  }

  async function checkAccess() {
    setBusy(true);
    setError(null);
    try {
      if (!API_BASE) {
        setResult({ message: "This preview has no Meta token to check.", results: [] });
        return;
      }
      const out = await postJSON("/ads/writes/scope", { account: data.account?.id });
      setResult({
        message: out.can_write
          ? "Meta accepted the check. Changes can be applied."
          : (out.detail || out.reconnect || "Reconnect Meta to enable changes"),
        results: [],
      });
      onSaved?.();
    } catch (e) {
      setError(e.message || "Meta did not accept the access check.");
    } finally {
      setBusy(false);
    }
  }

  async function undo() {
    setBusy(true);
    setError(null);
    try {
      if (!API_BASE) {
        setResult({ message: "Nothing has been applied from this preview.", results: [] });
        return;
      }
      const out = await postJSON("/ads/writes/undo", { account: data.account?.id });
      setResult({ message: out.meta_error || out.validation_note, results: [out] });
      onSaved?.();
    } catch (e) {
      setError(e.message || "Undo failed.");
    } finally {
      setBusy(false);
    }
  }

  async function saveControls() {
    setBusy(true);
    setError(null);
    try {
      if (!API_BASE) {
        setError("This preview has no API, so the switch is not saved.");
        return;
      }
      await patchJSON(`/ads/accounts/${data.account.id}`, { autopilot, writes_killed: killed });
      onSaved?.();
    } catch (e) {
      setError(e.message || "Could not save.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ display: "grid", gap: 12 }}>
      <div style={{ background: writes.can_write ? "#E7F0EA" : "#F8E6E1",
                    border: `1px solid ${writes.can_write ? "#1F4D38" : "#8C3A2A"}`,
                    borderRadius: 12, padding: "12px 14px" }}>
        <div style={{ fontFamily: HEAD, fontSize: 15, fontWeight: 600, color: C.ink }}>
          {writes.can_write ? "Changes go to Meta" : "Reconnect Meta to enable changes"}
        </div>
        <p style={{ fontFamily: FONT, fontSize: 13, color: C.slate, margin: "6px 0 0", lineHeight: 1.5 }}>
          {writes.can_write
            ? "This token includes ads_management. Preview checks the change with Meta before Apply sends it."
            : (writes.detail || "The saved token can read this account. Apply runs as a preview until you reconnect a token that includes ads_management.")}
        </p>
        {!writes.can_write && (
          <button type="button" className="per" style={{ marginTop: 10 }} disabled={busy} onClick={checkAccess}>
            Check access
          </button>
        )}
      </div>

      {items.map((item) => {
        const key = `${item.level}:${item.external_id}`;
        return (
          <div key={key} style={{ display: "grid", gridTemplateColumns: "auto 1fr auto",
                                  gap: 10, alignItems: "start", background: C.surface,
                                  border: `1px solid ${C.line}`, borderRadius: 12, padding: "12px 14px" }}>
            <input type="checkbox" aria-label={`Select ${item.name}`}
                   disabled={!item.action} checked={!!picked[key]}
                   onChange={(e) => setPicked((p) => ({ ...p, [key]: e.target.checked }))} />
            <div>
              <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                <Pill verdict={item.verdict} />
                <span style={{ fontFamily: FONT, fontSize: 13, fontWeight: 600, color: C.ink }}>{item.name}</span>
                <span style={{ fontFamily: FONT, fontSize: 12, color: C.muted }}>{item.group}</span>
              </div>
              <p style={{ fontFamily: FONT, fontSize: 13, color: C.slate, margin: "4px 0 0" }}>{item.reason}</p>
            </div>
            {item.action && (
              <button type="button" className="per" disabled={busy}
                      onClick={() => send({ items: [{ level: item.level, external_id: item.external_id, name: item.name }], dry_run: !writes.can_write })}>
                {writes.can_write ? "Apply" : "Preview"}
              </button>
            )}
          </div>
        );
      })}

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <button type="button" className="per" disabled={busy || !selected.length}
                onClick={() => send({ items: selected.map((i) => ({ level: i.level, external_id: i.external_id, name: i.name })), dry_run: true })}>
          Preview selected
        </button>
        <button type="button" className="per" disabled={busy || !selected.length}
                onClick={() => send({ items: selected.map((i) => ({ level: i.level, external_id: i.external_id, name: i.name })), dry_run: !writes.can_write })}>
          {writes.can_write ? "Apply selected" : "Preview selected changes"}
        </button>
        <button type="button" className="per" disabled={busy}
                onClick={() => send({ action: "fix_tagging", dry_run: !writes.can_write, reason: "Fix tagging" })}>
          Fix tagging
        </button>
        <button type="button" className="per" disabled={busy || !writes.can_undo}
                onClick={undo}>
          Undo last change
        </button>
      </div>

      {writes.can_manage !== false && (
        <div style={{ borderTop: `1px solid ${C.line}`, paddingTop: 12, display: "grid", gap: 8 }}>
          <label style={{ fontFamily: FONT, fontSize: 13, color: C.ink, display: "flex", gap: 8 }}>
            <input type="checkbox" checked={killed} onChange={(e) => setKilled(e.target.checked)} />
            Stop all ad changes
          </label>
          {Object.keys(autopilot).concat((data.groups || []).map((g) => g.name).filter((n) => n !== "Other" && !(n in autopilot))).slice(0, 6).map((name) => {
            const cfg = autopilot[name] || {};
            return (
              <div key={name} style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center",
                                       fontFamily: FONT, fontSize: 13 }}>
                <span style={{ minWidth: 180 }}>{name}</span>
                <label><input type="checkbox" checked={!!cfg.on}
                              onChange={(e) => setAutopilot((a) => ({ ...a, [name]: { ...cfg, on: e.target.checked } }))} /> Autopilot</label>
                <label><input type="checkbox" checked={!!cfg.pause_only}
                              onChange={(e) => setAutopilot((a) => ({ ...a, [name]: { ...cfg, pause_only: e.target.checked } }))} /> Pause only</label>
                <label>Max daily $
                  <input aria-label={`Max daily spend for ${name}`} inputMode="decimal" value={cfg.max_daily_spend ?? ""}
                         onChange={(e) => setAutopilot((a) => ({ ...a, [name]: { ...cfg, max_daily_spend: e.target.value } }))}
                         style={{ width: 80, marginLeft: 6, fontFamily: FIG, padding: "4px 6px",
                                  border: `1px solid ${C.line}`, borderRadius: 6 }} />
                </label>
              </div>
            );
          })}
          <button type="button" className="per" disabled={busy} onClick={saveControls}>Save guardrails</button>
          <p style={{ fontFamily: FONT, fontSize: 12, color: C.muted, margin: 0, lineHeight: 1.5 }}>
            Autopilot is off until you turn it on for a brand. It will not raise a budget more than 20% at a time,
            more than 20% in a day, or past the weekly plan, and it waits for {usd(25)} and 1,000 impressions.
          </p>
        </div>
      )}

      {error && <p style={{ fontFamily: FONT, fontSize: 13, color: "#8C3A2A", margin: 0 }}>{error}</p>}
      {result && (
        <div style={{ background: C.surface2, borderRadius: 12, padding: "12px 14px" }}>
          {result.message && <p style={{ fontFamily: FONT, fontSize: 13, color: C.ink, margin: "0 0 8px" }}>{result.message}</p>}
          {(result.results || []).map((r, i) => (
            <p key={i} style={{ fontFamily: FONT, fontSize: 13, color: r.meta_error ? "#8C3A2A" : C.slate, margin: "4px 0" }}>
              {(r.name || r.external_id || "Change")}: {r.dry_run ? "Preview" : "Applied"}.
              {r.validated ? " Meta validated it." : ""}
              {r.validation_note ? ` ${r.validation_note}` : ""}
              {r.meta_error ? ` ${r.meta_error}` : ""}
            </p>
          ))}
          {(result.results || []).length === 0 && (
            <p style={{ fontFamily: FONT, fontSize: 13, color: C.muted, margin: 0 }}>Nothing to change.</p>
          )}
        </div>
      )}
    </div>
  );
}
