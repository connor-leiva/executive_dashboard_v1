import React, { useState } from "react";
import { api } from "../api.js";
import { confirmFor, labelFor, perform } from "../actions.js";
import { ago, compact, plural } from "../format.js";
import { Bar, Btn, Card, Chip, Confirm, Empty, Eyebrow, Loading, LoadError, Mono, Notice, Seg, Stat, useApi } from "../primitives.jsx";
import { A, STATE, TYPE } from "../tokens.js";

export function TriageRow({ signal, onOpen, onChanged }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(null);
  const [note, setNote] = useState(null);
  const [arming, setArming] = useState(null);
  const s = STATE[signal.severity];
  const armed = arming === "primary" ? signal.primary_action : arming === "secondary" ? signal.secondary_action : null;

  /* An irreversible action asks first; everything else runs on the first press. */
  function pressOrArm(action, which) {
    if (confirmFor(action)) {
      setNote(null);
      setArming(which);
      return;
    }
    press(action, which);
  }

  async function press(action, which) {
    setBusy(which);
    setNote(null);
    setArming(null);
    try {
      const said = await perform(action, signal.tenant_slug, onOpen);
      /* Reported above the queue, not in the row: clearing a cause clears its row on the reload,
         and a message inside it would vanish with it. */
      if (said) onChanged && onChanged(`${signal.tenant_slug}: ${said}`);
    } catch (e) {
      setNote({ tone: "error", text: e.message });
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="ac-triagerow" style={{ borderTop: `1px solid ${A.lineSoft}`, display: "flex", transition: "background .12s ease" }}>
      <span style={{ width: 3, background: s.c, flexShrink: 0 }} aria-hidden />
      <div style={{ flex: 1, minWidth: 0, padding: "12px 14px" }}>
        <div className="ac-triage" style={{ display: "flex", gap: 14, alignItems: "flex-start", justifyContent: "space-between" }}>
          <div style={{ minWidth: 0, flex: 1 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              <button type="button" onClick={() => onOpen(signal.tenant_slug)} className="ac-tag" title={signal.tenant_name} style={{
                fontFamily: TYPE.data, fontSize: 11, color: A.body, background: A.chip, border: "none",
                borderRadius: 4, padding: "2px 6px", cursor: "pointer", transition: "background .12s ease, color .12s ease",
              }}>{signal.tenant_slug}</button>
              <Chip2 state={signal.severity} />
              <span style={{ fontFamily: TYPE.text, fontSize: 13, fontWeight: 600, color: A.ink }}>{signal.title}</span>
            </div>
            <div style={{ fontFamily: TYPE.text, fontSize: 12, color: A.body, marginTop: 5, lineHeight: 1.55, textWrap: "pretty", overflowWrap: "anywhere" }}>{signal.detail}</div>
            <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} className="ac-link" style={{
              fontFamily: TYPE.data, fontSize: 10.5, color: A.mute, background: "none", border: "none",
              padding: "6px 0 0", cursor: "pointer", textAlign: "left", overflowWrap: "anywhere",
            }}>{signal.meta} {open ? "▴" : "▾"}</button>
            {open ? (
              <div style={{ marginTop: 8, padding: "10px 12px", background: A.ground, borderRadius: 8 }}>
                <Eyebrow>Where this came from</Eyebrow>
                <div style={{ fontFamily: TYPE.text, fontSize: 11.5, color: A.body, marginTop: 6, lineHeight: 1.6, textWrap: "pretty" }}>
                  {signal.derivation}
                </div>
              </div>
            ) : null}
            {armed ? (
              <div style={{ marginTop: 8 }}>
                <Confirm label={labelFor(armed)} busy={busy === arming}
                  onConfirm={() => press(armed, arming)} onCancel={() => setArming(null)}>
                  {signal.title}. {confirmFor(armed)}
                </Confirm>
              </div>
            ) : null}
            {note ? <div style={{ marginTop: 8 }}><Notice tone={note.tone}>{note.text}</Notice></div> : null}
          </div>
          <div style={{ display: "flex", gap: 6, flexShrink: 0, flexWrap: "wrap", justifyContent: "flex-end" }}>
            {signal.secondary_action ? (
              <Btn kind="quiet" small busy={busy === "secondary"} onClick={() => pressOrArm(signal.secondary_action, "secondary")}>
                {labelFor(signal.secondary_action)}
              </Btn>
            ) : null}
            {signal.primary_action ? (
              <Btn kind="solid" small busy={busy === "primary"} disabled={Boolean(armed)} onClick={() => pressOrArm(signal.primary_action, "primary")}>
                {labelFor(signal.primary_action)}
              </Btn>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
}

function Chip2({ state }) {
  const s = STATE[state];
  return (
    <span className="ac-hidesm" style={{
      fontFamily: TYPE.text, fontSize: 10, fontWeight: 600, letterSpacing: ".04em", color: s.c,
      background: s.bg, borderRadius: 4, padding: "1px 6px",
    }}>{s.label}</span>
  );
}

function ProvidersCard() {
  const data = useApi(() => api.providers(), []);
  const rows = data.data ? data.data.providers : [];
  return (
    <Card title="Sources by provider"
      sub="One broken integration usually means one broken credential, not many broken workspaces. This is where that shows.">
      {data.loading && !data.data ? <Loading label="Reading providers" />
        : data.error ? <Empty title="Providers could not be read">{data.error.message}</Empty>
          : rows.length === 0 ? <Empty title="Nothing connected anywhere">No workspace has a source connected.</Empty>
            : rows.map((p) => (
              <div key={p.key} className="ac-provrow" style={{ display: "flex", alignItems: "center", gap: 12, padding: "9px 0", borderTop: `1px solid ${A.lineSoft}` }}>
                <div style={{ width: 128, flexShrink: 0, minWidth: 0 }}>
                  <div style={{ fontFamily: TYPE.text, fontSize: 12.5, fontWeight: 500, color: A.ink, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{p.name}</div>
                  <Mono size={10.5} c={A.mute}>{plural(p.tenants, "workspace")}</Mono>
                </div>
                <div style={{ flex: 1, minWidth: 56 }}>
                  <Bar segs={[{ v: p.ok, c: A.sage, label: "ok" }, { v: p.paused, c: A.lineMid, label: "paused" },
                    { v: p.stale, c: A.warn, label: "stale" }, { v: p.error, c: A.stop, label: "broken" }]} />
                </div>
                <div style={{ width: 92, display: "flex", justifyContent: "flex-end", flexShrink: 0 }}>
                  <Chip state={p.state}>{p.error ? `${p.error} broken` : p.stale ? `${p.stale} stale` : `${p.ok} ok`}</Chip>
                </div>
                <div style={{ width: 70, textAlign: "right", flexShrink: 0 }}>
                  <Mono size={10.5} c={A.mute} title="Oldest successful sync">{p.oldest_sync ? ago(p.oldest_sync) : "never"}</Mono>
                </div>
              </div>
            ))}
    </Card>
  );
}

export default function FleetView({ onOpen }) {
  const fleet = useApi(() => api.fleet(), []);
  const [filter, setFilter] = useState("all");
  const [done, setDone] = useState(null);

  const triage = fleet.data ? fleet.data.triage : [];

  if (fleet.loading && !fleet.data) return <Loading label="Reading the fleet" />;
  if (fleet.error) return <LoadError error={fleet.error} onRetry={fleet.reload} />;

  const counts = triage.reduce((c, t) => ({ ...c, [t.severity]: (c[t.severity] || 0) + 1 }), {});
  const shown = filter === "all" ? triage : triage.filter((t) => t.severity === filter);
  const r = fleet.data.rollup;
  const rows = { length: r.workspaces.total };

  return (
    <>
      <div className="ac-tiles ac-t6" style={{ marginBottom: 16 }}>
        <Stat label="Workspaces" value={r.workspaces.live}
          note={`live · ${r.workspaces.total} total, ${r.workspaces.suspended} suspended`} />
        <Stat label="People" value={r.people.active} note={`active across the fleet · ${r.people.invited} invited, ${r.people.disabled} disabled`} />
        <Stat label="Source health" value={`${r.sources.ok}/${r.sources.configured}`}
          state={r.sources.broken ? "broken" : r.sources.stale ? "watch" : "healthy"}
          note={`${r.sources.broken} broken, ${r.sources.stale} stale`} />
        <Stat label="Failed sync runs" value={r.failed_runs_7d} state={r.failed_runs_7d ? "broken" : "healthy"}
          note="last 7 days, across every source" />
        {/* Zero is unlimited and arrives as null, so a capped total only describes the capped. */}
        <Stat label="AI tokens" value={compact(r.tokens.used)}
          note={r.tokens.capped_workspaces
            ? `this month · ${compact(r.tokens.capped_budget)} capped across ${r.tokens.capped_workspaces} of ${rows.length} workspaces`
            : "this month · no workspace has a cap"} />
        <Stat label="MRR" unsourced note="Platform billing is not connected. Acumyn charges nobody through this console yet." />
      </div>

      {done ? <div style={{ marginBottom: 10 }}><Notice>{done}</Notice></div> : null}
      <Card title="Needs you now" pad={0}
        sub="Ranked by severity. Every row carries the reason it appeared and the action that clears it."
        right={<Seg label="Filter by severity" value={filter} onChange={setFilter} options={[
          ["all", `All ${triage.length}`], ["broken", `Broken ${counts.broken || 0}`],
          ["stalled", `Stalled ${counts.stalled || 0}`], ["watch", `Watch ${counts.watch || 0}`],
        ]} />}>
        {shown.length === 0 ? (
          <Empty title={triage.length ? "Nothing in this bucket" : "Nothing needs you"}>
            {triage.length
              ? "The fleet is clear at this severity. Choose another filter."
              : "No workspace has a broken source, a stuck onboarding or anything going wrong slowly."}
          </Empty>
        ) : shown.map((t) => (
          <TriageRow key={t.key} signal={t} onOpen={onOpen} onChanged={(said) => { setDone(said); fleet.reload(); }} />
        ))}
      </Card>

      <div className="ac-split" style={{ marginTop: 16 }}>
        <ProvidersCard />
      </div>

      <div style={{ marginTop: 12, display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
        <Mono size={10.5} c={A.mute}>{plural(rows.length, "workspace")} read {ago(fleet.data.read_at)}</Mono>
        <Btn small kind="quiet" onClick={fleet.reload} busy={fleet.loading}>Refresh</Btn>
      </div>
    </>
  );
}
