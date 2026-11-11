import { useState } from "react";

import { API_BASE, postJSON } from "../../api";
import { C, FIG, FONT, num, usd } from "../adsTokens.js";
import { Banner, Btn, Card, Empty, PaceBar, tone, weeklyPace } from "../ui.jsx";

const VERDICT = { kill: "Turn off", keep: "Keep", scale: "Scale" };

function Stat({ label, value, valueColor }) {
  return (
    <Card style={{ padding: 14 }}>
      <div style={{ fontFamily: FONT, fontSize: 12, fontWeight: 600, color: C.muted }}>{label}</div>
      <div style={{ fontFamily: FIG, fontSize: 26, color: valueColor || C.ink, marginTop: 6 }}>{value}</div>
    </Card>
  );
}

export default function Today({ data, onSaved }) {
  const writes = data.writes || {};
  const items = data.recommendations || [];
  const [picked, setPicked] = useState({});
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const pace = weeklyPace(data);
  const paceTone = tone(pace.status, pace.open);
  const spring = (data.brand_funnels?.forum?.rungs || []).find((r) => r.key === "qualified");
  const selected = items.filter((i) => i.action && picked[`${i.level}:${i.external_id}`]);
  const paceWord = pace.status === "behind" ? "Behind" : pace.status === "ahead" ? "Ahead" : pace.status === "on" ? "On pace" : "No plan";

  async function apply(list, dry) {
    setBusy(true);
    setError(null);
    try {
      if (!API_BASE) {
        setResult({
          message: writes.can_write ? "Approved in this preview." : (writes.reconnect || "Preview only. Reconnect Meta to enable changes."),
          results: list.map((item) => ({ name: item.name, dry_run: !writes.can_write, validation_note: "Seeded preview. Nothing was sent to Meta." })),
        });
        return;
      }
      const out = await postJSON("/ads/writes/apply", {
        account: data.account?.id, period: "7d", dry_run: dry || !writes.can_write,
        items: list.map((i) => ({ level: i.level, external_id: i.external_id, name: i.name })),
      });
      setResult(out);
      onSaved?.();
    } catch (e) {
      setError(e.message || "Meta did not accept that change.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ display: "grid", gap: 16 }}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 10 }}>
        <Stat label="Spent" value={usd(pace.spend)} />
        <Stat label="Spring leads" value={spring?.missing ? "Not recorded" : num(spring?.n)} />
        <Stat label="Cost / Spring lead" value={usd(spring?.cost_per)} />
        <Stat label="Pace" value={paceWord} valueColor={paceTone.ink} />
      </div>
      <PaceBar pace={pace} />
      {!writes.can_write && (
        <Banner title="Reconnect Meta to enable changes" tone="bad">
          Approve previews the change until the token includes ads_management.
        </Banner>
      )}
      {writes.killed && <Banner title="Ad changes are stopped" tone="bad">The kill switch is on. Approve stays a preview.</Banner>}
      <Card>
        {items.length === 0 && <Empty>Nothing to judge in this window.</Empty>}
        <div style={{ display: "grid", gap: 10 }}>
          {items.map((item) => {
            const key = `${item.level}:${item.external_id}`;
            return (
              <label key={key} style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: 12, alignItems: "start", padding: "8px 0", borderTop: `1px solid ${C.line}` }}>
                {item.action ? (
                  <input type="checkbox" checked={!!picked[key]} aria-label={`Select ${item.name}`}
                         onChange={(e) => setPicked((p) => ({ ...p, [key]: e.target.checked }))} />
                ) : <span />}
                <span>
                  <span style={{ fontFamily: FONT, fontSize: 12, fontWeight: 600, color: C.accent }}>{VERDICT[item.verdict] || item.verdict}</span>
                  <span style={{ fontFamily: FONT, fontSize: 14, fontWeight: 600, color: C.ink, marginLeft: 8 }}>{item.name}</span>
                  <span style={{ fontFamily: FONT, fontSize: 12, color: C.muted, marginLeft: 8 }}>{item.group}</span>
                  <div style={{ fontFamily: FONT, fontSize: 13, color: C.slate, marginTop: 4 }}>{item.reason}</div>
                </span>
              </label>
            );
          })}
        </div>
        <div style={{ display: "flex", gap: 8, marginTop: 14, flexWrap: "wrap" }}>
          <Btn primary disabled={busy || !selected.length} onClick={() => apply(selected, false)}>
            {writes.can_write ? "Approve selected" : "Preview selected"}
          </Btn>
          <Btn disabled={!selected.length} onClick={() => setPicked({})}>Skip</Btn>
        </div>
      </Card>
      {error && <p style={{ fontFamily: FONT, fontSize: 13, color: C.badInk, margin: 0 }}>{error}</p>}
      {result && (
        <Card>
          {result.message && <p style={{ fontFamily: FONT, fontSize: 13, color: C.ink, marginTop: 0 }}>{result.message}</p>}
          {(result.results || []).map((r, i) => (
            <p key={i} style={{ fontFamily: FONT, fontSize: 13, color: r.meta_error ? C.badInk : C.slate, margin: "4px 0" }}>
              {r.name || r.external_id}: {r.dry_run ? "Preview" : "Applied"}. {r.validation_note || ""} {r.meta_error || ""}
            </p>
          ))}
        </Card>
      )}
      {data.digest && (
        <p style={{ fontFamily: FONT, fontSize: 13, color: C.slate, margin: 0 }}>
          {data.digest.attendance?.attended != null
            ? `Edge webinar: ${data.digest.attendance.attended} attended.`
            : "Edge webinar attendance is not recorded yet."}
        </p>
      )}
    </div>
  );
}
