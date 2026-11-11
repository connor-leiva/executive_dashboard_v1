import { useState } from "react";

import { API_BASE, postJSON } from "../../api";
import { C, FIG, FONT, num, usd } from "../adsTokens.js";
import { Banner, Btn, Card, Drawer, Empty, foldRows, sentenceStatus, tone, weeklyPace } from "../ui.jsx";

const VERDICT = { kill: "Turn off", keep: "Keep", scale: "Scale" };

function Stat({ label, value, valueColor }) {
  return (
    <Card style={{ padding: 14 }}>
      <div style={{ fontFamily: FONT, fontSize: 12, fontWeight: 600, color: C.muted }}>{label}</div>
      <div style={{ fontFamily: FIG, fontSize: 26, color: valueColor || C.ink, marginTop: 6 }}>{value}</div>
    </Card>
  );
}

function keepLine(rows) {
  const below = rows.filter((r) => r.metric === "delivery").length;
  const inside = rows.length - below;
  const bits = [];
  if (inside) bits.push("numbers are inside the bands");
  if (below) bits.push("below the $25 / 1,000-impression minimum");
  return bits.join(", or ") || "nothing to change";
}

export default function Today({ data, onSaved, onBudget }) {
  const writes = data.writes || {};
  const items = data.recommendations || [];
  const [picked, setPicked] = useState({});
  const [openKey, setOpenKey] = useState(null);
  const [showKeeps, setShowKeeps] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const pace = weeklyPace(data);
  const paceTone = tone(pace.status, pace.open);
  const spring = (data.brand_funnels?.forum?.rungs || []).find((r) => r.key === "qualified");
  const decisions = items.filter((i) => i.verdict === "kill" || i.verdict === "scale");
  const keeps = items.filter((i) => i.verdict === "keep");
  const folded = foldRows(decisions);
  const selected = decisions.filter((i) => i.action && picked[`${i.level}:${i.external_id}`]);
  const paceWord = pace.status === "behind" ? "Behind" : pace.status === "ahead" ? "Ahead" : pace.status === "on" ? "On pace" : "No plan";
  const open = items.find((i) => `${i.level}:${i.external_id}` === openKey) || null;
  const sameNameKept = open ? keeps.filter((k) => k.name === open.name) : [];

  async function apply(list, dry) {
    setBusy(true);
    setError(null);
    try {
      if (!API_BASE) {
        setResult({
          message: dry || !writes.can_write
            ? "Preview only. Nothing was sent to Meta."
            : "Approved in this preview.",
          results: list.map((item) => ({ name: item.name, dry_run: dry || !writes.can_write })),
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

  const blocked = !selected.length
    ? "Select a Turn off or Scale row first."
    : !writes.can_write
      ? "Reconnect Meta to send this change."
      : null;

  return (
    <div style={{ display: "grid", gap: 16 }}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 10 }}>
        <Stat label="Spent" value={usd(pace.spend)} />
        <Stat label="Spring leads" value={spring?.missing ? "Not recorded" : num(spring?.n)} />
        <Stat label="Cost / Spring lead" value={usd(spring?.cost_per)} />
        <Stat label="Pace" value={paceWord} valueColor={paceTone.ink} />
      </div>

      <WeekPace data={data} onBudget={onBudget} />

      {!writes.can_write && (
        <Banner title="Reconnect Meta to enable changes" tone="bad">
          Approve previews the change until the token includes ads_management.
        </Banner>
      )}
      {writes.killed && <Banner title="Ad changes are stopped" tone="bad">The kill switch is on. Approve stays a preview.</Banner>}

      <div style={{
        position: "sticky", top: 0, zIndex: 5, background: C.bg,
        display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", padding: "8px 0",
      }}>
        <Btn primary disabled={busy || !!blocked} title={blocked || "Approve sends this change to Meta."}
             onClick={() => apply(selected, false)}>Approve selected</Btn>
        <Btn disabled={busy || !selected.length} onClick={() => apply(selected, true)}>Preview</Btn>
        <Btn disabled={!selected.length} onClick={() => setPicked({})}>Skip</Btn>
        <span style={{ fontFamily: FONT, fontSize: 13, color: blocked ? C.slate : C.muted }}>
          {blocked || "Approve sends this change to Meta."}
        </span>
      </div>

      <Card>
        {folded.length === 0 && <Empty>Nothing to turn off or scale in this window.</Empty>}
        {folded.map((bucket) => (
          <DecisionGroup key={bucket.key} bucket={bucket} picked={picked} setPicked={setPicked}
                         openKey={openKey} setOpenKey={setOpenKey} keeps={keeps} />
        ))}
        {keeps.length > 0 && (
          <div style={{ borderTop: `1px solid ${C.line}`, paddingTop: 12, marginTop: 8 }}>
            <button type="button" onClick={() => setShowKeeps((v) => !v)}
                    style={{ background: "none", border: "none", padding: 0, cursor: "pointer", textAlign: "left", fontFamily: FONT, fontSize: 14, color: C.ink }}>
              {keeps.length} {keeps.length === 1 ? "ad" : "ads"} to keep: {keepLine(keeps)}. {showKeeps ? "Hide the list." : "Show the list."}
            </button>
            {showKeeps && keeps.map((item) => (
              <RowBody key={`${item.level}:${item.external_id}`} item={item} keeps={decisions}
                       selected={openKey === `${item.level}:${item.external_id}`}
                       onOpen={() => setOpenKey(`${item.level}:${item.external_id}`)} />
            ))}
          </div>
        )}
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

      {open && (
        <Drawer title={open.name} onClose={() => setOpenKey(null)}>
          <p style={{ fontFamily: FONT, fontSize: 14, color: C.ink, marginTop: 0 }}>{open.reason}</p>
          <p style={{ fontFamily: FONT, fontSize: 13, color: C.slate }}>
            {[open.campaign, open.adset].filter(Boolean).join(" · ") || open.group}
          </p>
          <dl style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, fontFamily: FONT, fontSize: 13 }}>
            <div><dt style={{ color: C.muted }}>Spend</dt><dd style={{ fontFamily: FIG, margin: 0 }}>{usd(open.spend)}</dd></div>
            <div><dt style={{ color: C.muted }}>Leads</dt><dd style={{ fontFamily: FIG, margin: 0 }}>{num(open.leads)}</dd></div>
            <div><dt style={{ color: C.muted }}>Seats</dt><dd style={{ fontFamily: FIG, margin: 0 }}>{open.seats == null ? "—" : num(open.seats)}</dd></div>
            <div><dt style={{ color: C.muted }}>Status</dt><dd style={{ fontFamily: FIG, margin: 0 }}>{sentenceStatus(open.status) || "—"}</dd></div>
          </dl>
          {sameNameKept.length > 0 && (
            <p style={{ fontFamily: FONT, fontSize: 13, color: C.slate }}>
              Another row uses this same name as Keep
              {sameNameKept.map((k) => ` at ${usd(k.spend)} (${k.metric === "delivery" ? "below the minimum" : "inside the bands"})`).join("; ")}.
              This one is {VERDICT[open.verdict]} at {usd(open.spend)}.
            </p>
          )}
          <Btn disabled={busy || !open.action} onClick={() => apply([open], true)}>Preview</Btn>
        </Drawer>
      )}
    </div>
  );
}

function WeekPace({ data, onBudget }) {
  const week = data.week;
  const brands = (week?.brands || []).filter((b) => b.plan);
  if (!week?.has_plan && brands.length === 0) {
    return (
      <Card>
        <p style={{ fontFamily: FONT, fontSize: 14, color: C.ink, margin: 0 }}>No weekly plan yet.</p>
        <div style={{ marginTop: 10 }}>
          <Btn primary onClick={onBudget}>Set this week&apos;s plan</Btn>
        </div>
      </Card>
    );
  }
  return (
    <Card>
      <div style={{ fontFamily: FONT, fontSize: 13, fontWeight: 600, color: C.ink, marginBottom: 10 }}>Weekly pace</div>
      <div style={{ display: "grid", gap: 10 }}>
        {brands.map((b) => {
          const pct = b.plan ? (b.spend / b.plan) * 100 : 0;
          const status = pct < 90 ? "behind" : pct > 110 ? "ahead" : "on";
          const t = tone(status, true);
          return (
            <div key={b.name}>
              <div style={{ display: "flex", justifyContent: "space-between", fontFamily: FONT, fontSize: 13, color: C.ink }}>
                <span>{b.name}</span>
                <span style={{ color: t.ink }}>{usd(b.spend)} of {usd(b.plan)} · {Math.round(pct)}%</span>
              </div>
              <div style={{ marginTop: 4, height: 8, borderRadius: 999, background: C.surface2 }}>
                <div style={{ width: `${Math.max(4, Math.min(100, pct))}%`, height: "100%", borderRadius: 999, background: t.bar }} />
              </div>
            </div>
          );
        })}
      </div>
    </Card>
  );
}

function DecisionGroup({ bucket, picked, setPicked, openKey, setOpenKey, keeps }) {
  const [open, setOpen] = useState(bucket.rows.length === 1);
  const head = bucket.rows[0];
  if (bucket.rows.length === 1) {
    return (
      <DecisionRow item={head} picked={picked} setPicked={setPicked} keeps={keeps}
                   selected={openKey === `${head.level}:${head.external_id}`}
                   onOpen={() => setOpenKey(`${head.level}:${head.external_id}`)} nested={false} />
    );
  }
  return (
    <div style={{ borderTop: `1px solid ${C.line}` }}>
      <button type="button" onClick={() => setOpen((v) => !v)}
              style={{ background: "none", border: "none", padding: "10px 0", cursor: "pointer", textAlign: "left", width: "100%" }}>
        <span style={{ fontFamily: FONT, fontSize: 12, fontWeight: 600, color: C.accent }}>{VERDICT[head.verdict]}</span>
        <span style={{ fontFamily: FONT, fontSize: 14, fontWeight: 600, color: C.ink, marginLeft: 8 }}>{head.name.replace(/\s*[-–—]?\s*ResultsH\d+/i, "")}</span>
        <span style={{ fontFamily: FONT, fontSize: 12, color: C.muted, marginLeft: 8 }}>{bucket.rows.length} variants</span>
      </button>
      {open && bucket.rows.map((item) => (
        <DecisionRow key={`${item.level}:${item.external_id}`} item={item} picked={picked} setPicked={setPicked}
                     keeps={keeps} selected={openKey === `${item.level}:${item.external_id}`}
                     onOpen={() => setOpenKey(`${item.level}:${item.external_id}`)} nested />
      ))}
    </div>
  );
}

function DecisionRow({ item, picked, setPicked, keeps, selected, onOpen, nested }) {
  const key = `${item.level}:${item.external_id}`;
  const twin = keeps.find((k) => k.name === item.name);
  return (
    <div style={{
      display: "grid", gridTemplateColumns: "auto 1fr", gap: 12, alignItems: "start",
      padding: "8px 8px", borderTop: `1px solid ${C.line}`, background: selected ? C.accentBg : "transparent",
    }}>
      <input type="checkbox" checked={!!picked[key]} aria-label={`Select ${item.name}`} disabled={!item.action}
             onClick={(e) => e.stopPropagation()}
             onChange={(e) => setPicked((p) => ({ ...p, [key]: e.target.checked }))} />
      <RowBody item={item} keeps={twin ? [twin] : []} selected={selected} onOpen={onOpen} nested={nested} />
    </div>
  );
}

function RowBody({ item, keeps, onOpen, nested }) {
  const twin = (keeps || []).find((k) => k.name === item.name && k.verdict === "keep");
  return (
    <button type="button" onClick={onOpen} style={{
      background: "none", border: "none", padding: nested ? 0 : "8px 0", textAlign: "left", cursor: "pointer", width: "100%",
    }}>
      {!nested && (
        <span style={{ fontFamily: FONT, fontSize: 12, fontWeight: 600, color: C.accent }}>{VERDICT[item.verdict] || item.verdict}</span>
      )}
      <span style={{ fontFamily: FONT, fontSize: 14, fontWeight: 600, color: C.ink, marginLeft: nested ? 0 : 8 }}>{item.name}</span>
      <span style={{ fontFamily: FONT, fontSize: 12, color: C.muted, marginLeft: 8 }}>{item.group}</span>
      <div style={{ fontFamily: FONT, fontSize: 13, color: C.slate, marginTop: 4 }}>
        {[item.campaign, item.adset].filter(Boolean).join(" · ") || "Campaign not on this row"}
        {twin ? ` · ${VERDICT[item.verdict]} at ${usd(item.spend)}; a Keep row with this name is ${usd(twin.spend)}` : ""}
      </div>
      <div style={{ fontFamily: FONT, fontSize: 13, color: C.slate, marginTop: 2 }}>{item.reason}</div>
    </button>
  );
}
