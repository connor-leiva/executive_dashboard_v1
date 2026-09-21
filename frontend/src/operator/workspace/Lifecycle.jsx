import React, { useState } from "react";
import { api } from "../api.js";
import { plural } from "../format.js";
import { Btn, Card, Confirm, inputStyle, Loading, Mono, Notice, useAction, useApi } from "../primitives.jsx";
import { A, TYPE } from "../tokens.js";

function Section({ title, children, action, first }) {
  return (
    <div style={{
      display: "flex", gap: 16, alignItems: "flex-start", justifyContent: "space-between", flexWrap: "wrap",
      padding: first ? "2px 0 13px" : "13px 0", borderTop: first ? "none" : `1px solid ${A.lineSoft}`,
    }}>
      <div style={{ minWidth: 0, flex: "1 1 340px" }}>
        <div style={{ fontFamily: TYPE.text, fontSize: 13, fontWeight: 500, color: A.ink }}>{title}</div>
        <div style={{ fontFamily: TYPE.text, fontSize: 11.5, color: A.mute, marginTop: 3, lineHeight: 1.6, textWrap: "pretty" }}>{children}</div>
      </div>
      {action}
    </div>
  );
}

/* Export, transfer ownership and delete: the workspace's lifecycle. Delete names its blast radius in
   counts, then wants the slug typed, then does it. */
export default function Lifecycle({ w, reload, onDeleted }) {
  const people = useApi(() => api.people(w.slug), [w.slug]);
  const radius = useApi(() => api.blastRadius(w.slug), [w.slug]);
  const [to, setTo] = useState("");
  const [arming, setArming] = useState(false);
  const [typed, setTyped] = useState("");
  const action = useAction();

  const candidates = people.data
    ? people.data.people.filter((p) => p.status === "active" && p.role !== "owner" && !p.expires_at)
    : [];
  const target = candidates.find((p) => p.id === to);
  const armed = typed.trim().toLowerCase() === w.slug;
  const r = radius.data;

  async function exportNow() {
    await action.run("export", async () => {
      const res = await api.exportMetadata(w.slug);
      const blob = await res.blob();
      const name = (res.headers.get("content-disposition") || "").match(/filename="([^"]+)"/);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = name ? name[1] : `axcion-${w.slug}-metadata.json`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      return true;
    }, "Exported. The download holds people, businesses, connections, share links and the audit log; no business data and no credentials.");
  }

  return (
    <Card title="Lifecycle" sub="Taking a workspace out, handing it over, and removing it.">
      <Section first title="Export" action={<Btn small busy={action.busy === "export"} onClick={exportNow}>Build export</Btn>}>
        A metadata archive: people, businesses, connections and their status, share links and the full audit log.
        No business data and no credentials; a workspace exports its own numbers from inside itself.
      </Section>

      <Section title="Transfer ownership"
        action={
          <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
            <select aria-label="New owner" value={to} onChange={(e) => setTo(e.target.value)} style={{ ...inputStyle, width: "auto", maxWidth: 260, cursor: "pointer" }}>
              <option value="">{people.loading ? "Reading people…" : candidates.length ? "Choose the new owner" : "Nobody eligible"}</option>
              {candidates.map((p) => <option key={p.id} value={p.id}>{p.name} · {p.email}</option>)}
            </select>
            <Btn small disabled={!target} busy={action.busy === "transfer"}
              onClick={async () => {
                if (await action.run("transfer", () => api.transferOwnership(w.slug, to), (x) => `${x.owner} now owns ${w.slug}.${x.demoted.length ? ` ${x.demoted.join(", ")} ${x.demoted.length === 1 ? "is" : "are"} now admin.` : ""}`)) {
                  setTo(""); people.reload(); reload();
                }
              }}>Transfer</Btn>
          </div>
        }>
        Moves the owner role to another active person in the workspace. Every current owner drops to admin, so nobody loses access and
        there is still exactly one owner.
      </Section>

      <div style={{ marginTop: 6, padding: 15, background: A.stopBg, border: `1px solid ${A.stopLine}`, borderLeft: `3px solid ${A.stop}`, borderRadius: 10 }}>
        <div style={{ fontFamily: TYPE.text, fontSize: 13, fontWeight: 600, color: A.stop }}>Delete this workspace</div>
        {radius.loading && !r ? <Loading label="Counting what would go" /> : r ? (
          <div style={{ fontFamily: TYPE.text, fontSize: 12, color: A.ink, marginTop: 5, lineHeight: 1.6, textWrap: "pretty" }}>
            Removes the workspace and everything scoped to it: {plural(r.accounts, "account")}, {plural(r.businesses, "business", "businesses")},{" "}
            {plural(r.entities, "legal entity", "legal entities")}, {plural(r.documents, "document")}, {plural(r.connections, "connection")} and
            the workspace's own audit trail of {plural(r.audit_entries, "entry", "entries")}.{" "}
            {r.hosts.length ? <>The {r.hosts.map((h) => <Mono key={h} size={11} c={A.ink}>{h} </Mono>)} address is released and becomes claimable. </> : null}
            Take the export first: there is no undo and no soft delete. The record that it was deleted stays in the operator trail.
          </div>
        ) : null}
        {!arming ? (
          <div style={{ marginTop: 12 }}>
            <Btn small kind="danger" disabled={!r} onClick={() => setArming(true)}>Delete permanently…</Btn>
          </div>
        ) : (
          <div style={{ display: "grid", gap: 10, marginTop: 12 }}>
            <input value={typed} onChange={(e) => setTyped(e.target.value)} aria-label={`Type ${w.slug} to confirm deletion`}
              placeholder={`Type ${w.slug} to confirm`} autoFocus
              style={{ ...inputStyle, fontFamily: TYPE.data, maxWidth: 320, borderColor: armed ? A.stop : A.line }} />
            <Confirm label="Delete permanently" busy={action.busy === "delete"} disabled={!armed}
              onCancel={() => { setArming(false); setTyped(""); }}
              onConfirm={async () => {
                if (!armed) return;
                const out = await action.run("delete", () => api.deleteTenant(w.slug, typed.trim().toLowerCase()), (x) => `Deleted ${x.deleted}.`);
                if (out) onDeleted();
              }}>
              {armed ? `${w.slug} and everything above will be deleted.` : `Type ${w.slug} above to arm the button.`}
            </Confirm>
          </div>
        )}
      </div>
      {action.result ? <div style={{ marginTop: 12 }}><Notice tone={action.result.tone}>{action.result.text}</Notice></div> : null}
    </Card>
  );
}
