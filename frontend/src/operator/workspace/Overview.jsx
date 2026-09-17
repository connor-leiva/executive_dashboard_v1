import React, { useState } from "react";
import { labelFor, perform } from "../actions.js";
import { ago, compact, daysSince, pct, plural } from "../format.js";
import { healthOf } from "../health.js";
import { Btn, Card, Chip, Eyebrow, Mono, Notice, Row, Stat } from "../primitives.jsx";
import { planName } from "../reference.js";
import { A, STATE, TYPE } from "../tokens.js";

export default function OverviewPane({ w, reference, reload, open }) {
  const h = healthOf(w);
  const s = STATE[h.state];
  const first = (w.signals || [])[0];
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState(null);
  const ok = w.sources - w.sources_in_error - w.sources_stale;

  async function fix() {
    setBusy(true);
    setNote(null);
    try {
      const said = await perform(first.primary_action, w.slug, (slug, pane) => open(pane));
      if (said) { setNote({ tone: "info", text: said }); reload(); }
    } catch (e) {
      setNote({ tone: "error", text: e.message });
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      {/* The derivation panel: the one thing a status chip on its own can never do. */}
      <div style={{
        background: s.bg, border: `1px solid ${A.line}`, borderLeft: `3px solid ${s.c}`,
        borderRadius: 10, padding: 15, marginBottom: 16, boxShadow: A.lift,
      }}>
        <div style={{ display: "flex", gap: 14, justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap" }}>
          <div style={{ minWidth: 0, flex: "1 1 320px" }}>
            <Eyebrow style={{ color: s.c }}>Why this workspace is {s.label.toLowerCase()}</Eyebrow>
            <ul style={{ margin: "8px 0 0", padding: 0, listStyle: "none" }}>
              {h.why.map((line) => (
                <li key={line} style={{ fontFamily: TYPE.text, fontSize: 13, color: A.ink, lineHeight: 1.6, display: "flex", gap: 8 }}>
                  <span style={{ color: s.c, flexShrink: 0 }} aria-hidden>—</span>{line}
                </li>
              ))}
            </ul>
            <div style={{ fontFamily: TYPE.text, fontSize: 11, color: A.mute, marginTop: 9, lineHeight: 1.55, textWrap: "pretty" }}>
              {h.derivation} Computed on read: there is no stored status, so a workspace cannot keep a label whose cause was fixed.
            </div>
            {note ? <div style={{ marginTop: 10 }}><Notice tone={note.tone}>{note.text}</Notice></div> : null}
          </div>
          {first && first.primary_action ? (
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap", flexShrink: 0 }}>
              <Btn small kind="primary" busy={busy} onClick={fix}>{labelFor(first.primary_action)}</Btn>
            </div>
          ) : null}
        </div>
      </div>

      <div className="ac-tiles ac-t6" style={{ marginBottom: 16 }}>
        <Stat label="People" value={w.people.active}
          note={`${w.people.invited} invited · ${w.people.disabled} disabled · ${w.people.two_factor} with two-factor`}
          state={w.people.invited > 1 ? "watch" : undefined} />
        <Stat label="Sources" value={`${ok}/${w.sources}`}
          state={w.sources_in_error ? "broken" : w.sources_stale ? "watch" : w.sources ? "healthy" : undefined}
          note={w.sources ? `${w.sources_in_error} broken, ${w.sources_stale} stale` : "nothing connected"} />
        <Stat label="Businesses" value={w.businesses} note="each one a tab and a connection point" />
        <Stat label="Last sync" value={ago(w.last_synced_at)} state={w.sync_failures_7d ? "broken" : undefined}
          note={`${plural(w.sync_failures_7d, "failed run")} in 7 days${w.syncs_frozen ? " · syncs frozen" : ""}`} />
        <Stat label="AI tokens" value={compact(w.tokens.used)}
          note={w.tokens.budget ? `${pct(w.tokens.used, w.tokens.budget)}% of this month's budget` : "this month · no budget cap, unlimited"} />
        <Stat label="Documents" unsourced note="Binder uploads are counted but never sized, so there is no storage figure to show." />
      </div>

      <div className="ac-split">
        <Card title="Identity">
          <Row k="Workspace name">{w.name}</Row>
          <Row k="Slug"><Mono c={A.ink}>{w.slug}</Mono></Row>
          <Row k="Web addresses" top>
            {w.hosts.length ? w.hosts.map((host, i) => (
              <div key={host} style={{ marginBottom: 4, display: "flex", gap: 6, alignItems: "center", justifyContent: "flex-end", flexWrap: "wrap" }}>
                <Mono c={A.ink}>{host}</Mono>
                {i === 0 ? <Chip>Primary</Chip> : null}
              </div>
            )) : <span style={{ color: A.stop }}>No domain row: links fall back to the platform address</span>}
          </Row>
          <Row k="Owner">{w.owner_email ? <Mono c={A.ink}>{w.owner_email}</Mono> : <span style={{ color: A.stop }}>None</span>}</Row>
          <Row k="Created">{daysSince(w.created_at) === 0 ? "Today" : `${daysSince(w.created_at)} days ago`}</Row>
          <Row k="Plan" top>
            {planName(reference, w.plan)}
            <div style={{ marginTop: 4 }}>
              {w.plan_set ? <Chip>As stored</Chip> : <Chip state="watch">No plan set, defaulted</Chip>}
            </div>
          </Row>
        </Card>

        <Card title="What you can see here"
          sub="Support needs the shape of a workspace, not its contents. The console is built so the second list is impossible, not merely discouraged.">
          <Eyebrow>Visible to you</Eyebrow>
          <div style={{ fontFamily: TYPE.text, fontSize: 12.5, color: A.body, lineHeight: 1.7, margin: "7px 0 16px", textWrap: "pretty" }}>
            Who was invited and when they signed in · which sources are connected and whether they are erroring ·
            how many businesses exist · counts of runs and tokens · every configuration change, with its actor
          </div>
          <Eyebrow>Never visible to you</Eyebrow>
          <div style={{ fontFamily: TYPE.text, fontSize: 12.5, color: A.mute, lineHeight: 1.7, marginTop: 7, textWrap: "pretty" }}>
            Any figure on their dashboard · P&amp;L, revenue, cash · member and contact records · document contents ·
            call transcripts · anything a workspace would call its numbers
          </div>
        </Card>
      </div>
    </>
  );
}
