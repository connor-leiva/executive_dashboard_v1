import React, { useState } from "react";
import { api } from "../api.js";
import { ago, joinAnd, plural } from "../format.js";
import { Btn, Card, Chip, Empty, Eyebrow, Loading, LoadError, Mono, Notice, useAction, useApi } from "../primitives.jsx";
import { A, STATE, TYPE } from "../tokens.js";

const LABEL = { healthy: "Syncing", stale: "Stale", broken: "Broken", paused: "Paused", off: "Disconnected" };
const TONE = { healthy: "healthy", stale: "watch", broken: "broken", paused: undefined, off: undefined };

function Runs({ runs }) {
  if (!runs || !runs.length) return <Mono size={11} c={A.mute}>No runs recorded</Mono>;
  return (
    <div style={{ display: "grid", gap: 4, marginTop: 4 }}>
      {runs.map((r) => (
        <div key={r.started_at} style={{ display: "flex", gap: 10, alignItems: "baseline", flexWrap: "wrap" }}>
          <Chip state={r.status === "ok" ? "healthy" : r.status === "error" ? "broken" : undefined}>{r.status}</Chip>
          <Mono size={10.5} c={A.mute}>{ago(r.started_at)}</Mono>
          {r.seconds != null ? <Mono size={10.5} c={A.mute}>{r.seconds}s</Mono> : null}
          {r.records != null ? <Mono size={10.5} c={A.mute}>{plural(r.records, "record")}</Mono> : null}
          {r.detail ? <span style={{ fontFamily: TYPE.data, fontSize: 10.5, color: A.stop, overflowWrap: "anywhere" }}>{r.detail}</span> : null}
        </div>
      ))}
    </div>
  );
}

function SourceRow({ src, runs, actions }) {
  const [open, setOpen] = useState(false);
  const st = TONE[src.state] ? STATE[TONE[src.state]] : null;
  return (
    <div style={{ display: "flex", borderTop: `1px solid ${A.lineSoft}` }}>
      <span style={{ width: 3, background: st && src.state !== "healthy" ? st.c : "transparent", flexShrink: 0 }} aria-hidden />
      <div style={{ flex: 1, minWidth: 0, padding: "11px 16px" }}>
        <div style={{ display: "flex", gap: 14, alignItems: "center", flexWrap: "wrap" }}>
          <div style={{ minWidth: 0, flex: "1 1 240px" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              <span style={{ fontFamily: TYPE.text, fontSize: 13, fontWeight: 500, color: A.ink }}>{src.provider_name}</span>
              {src.business ? <Mono size={11} c={A.mute}>{src.business}</Mono> : null}
              <Chip state={TONE[src.state]}>{LABEL[src.state]}</Chip>
            </div>
            <div style={{ fontFamily: TYPE.text, fontSize: 11.5, color: src.state === "broken" ? A.stop : A.mute, marginTop: 3, lineHeight: 1.5, overflowWrap: "anywhere" }}>
              {src.state === "broken" ? (src.last_error || "The last sync failed without an error message.")
                : src.state === "stale" ? `No successful sync in over ${plural(Math.round(src.stale_after_minutes / 60), "hour")}.`
                  : src.state === "paused" ? "Not syncing while this workspace is suspended or frozen."
                    : src.state === "off" ? "Credentials removed. Nothing is pulled until it is reconnected."
                      : `Syncing on schedule${src.failed_runs_7d ? `, with ${plural(src.failed_runs_7d, "failed run")} this week` : ""}.`}
            </div>
            <button type="button" className="ac-link" aria-expanded={open} onClick={() => setOpen(!open)} style={{
              fontFamily: TYPE.data, fontSize: 10.5, color: A.mute, background: "none", border: "none", padding: "5px 0 0", cursor: "pointer",
            }}>Recent runs {open ? "▴" : "▾"}</button>
            {open ? <Runs runs={runs} /> : null}
          </div>
          <div style={{ display: "flex", gap: 14, alignItems: "center", marginLeft: "auto", flexShrink: 0 }}>
            <div style={{ width: 90 }}>
              <Eyebrow style={{ whiteSpace: "nowrap" }}>Last sync</Eyebrow>
              <div style={{ fontFamily: TYPE.text, fontSize: 12.5, color: src.state === "healthy" ? A.ink : st ? st.c : A.body, marginTop: 3 }}>{ago(src.last_synced_at)}</div>
            </div>
            {actions ? <div style={{ display: "flex", gap: 4, justifyContent: "flex-end", minWidth: 150 }}>{actions(src)}</div> : null}
          </div>
        </div>
      </div>
    </div>
  );
}

export default function SourcesPane({ w, reload }) {
  const data = useApi(() => api.sources(w.slug), [w.slug]);
  const action = useAction();
  if (data.loading && !data.data) return <Loading label="Reading connections" />;
  if (data.error) return <LoadError error={data.error} onRetry={data.reload} />;

  const { sources, runs, available, paused, syncs_frozen: frozen } = data.data;
  const configured = sources.filter((src) => src.state !== "off").length;

  async function run(key, fn, said) {
    const out = await action.run(key, fn, said);
    if (out) { data.reload(); reload(); }
  }
  const sentTo = (what) => (r) => `A link to ${what} went to ${joinAnd(r.sent_to)}. The link is their Settings page; they sign in as themselves.`;

  /* A broken or disconnected source needs its owner to reauthorise, which an operator cannot do, so
     its action is the link. Anything else can simply be synced, unless the workspace is paused. */
  const rowActions = (src) => {
    const key = `src:${src.id}`;
    const busyHere = action.busy === key;
    const elsewhere = Boolean(action.busy) && !busyHere;
    if (src.state === "broken" || src.state === "off") {
      return (
        <Btn small kind="solid" busy={busyHere} disabled={elsewhere}
          onClick={() => run(key, () => api.reconnectLink(w.slug, src.id), sentTo(`reconnect ${src.provider_name}`))}>
          Send reconnect link
        </Btn>
      );
    }
    if (paused) return null;
    return (
      <Btn small busy={busyHere} disabled={elsewhere}
        onClick={() => run(key, () => api.syncSource(w.slug, src.id), (r) => `${r.provider_name} is syncing now. Refresh in a minute to see how it went.`)}>
        Sync now
      </Btn>
    );
  };

  return (
    <Card title="Data sources" pad={0}
      sub={frozen ? "Syncs are frozen for this workspace. Nothing is pulled until they are unfrozen."
        : paused ? "This workspace is suspended, so nothing syncs for it."
          : "Each connection with its actual error, not a red dot."}
      right={
        <Btn small kind="primary" busy={action.busy === "all"} disabled={paused || !configured}
          title={paused ? "Nothing syncs while the workspace is suspended or frozen" : !configured ? "Nothing is connected" : "Pull every connected source now"}
          onClick={() => run("all", () => api.syncTenant(w.slug), (r) => `Syncing ${plural(r.sources, "source")} now. Refresh in a minute to see how it went.`)}>
          Sync all
        </Btn>
      }>
      {action.result ? (
        <div style={{ padding: "12px 16px" }}><Notice tone={action.result.tone}>{action.result.text}</Notice></div>
      ) : null}
      {sources.length === 0 ? (
        <Empty title="Nothing is connected yet"
          action={available.length ? (
            <Btn small busy={action.busy === "setup"}
              onClick={() => run("setup", () => api.setupLink(w.slug), sentTo("connect a first source"))}>
              Send setup link
            </Btn>
          ) : null}>
          {available.length
            ? `${plural(available.length, "provider")} ${available.length === 1 ? "is" : "are"} available on this plan (${available.map((a) => a.name).join(", ")}) and none has been connected. Until one is, every panel in the workspace is empty.`
            : "The plan offers no providers, which should not happen. Check the workspace's plan."}
        </Empty>
      ) : sources.map((src) => (
        <SourceRow key={src.id} src={src} runs={runs[src.provider]} actions={rowActions} />
      ))}
      {sources.length && available.length ? (
        <div style={{ padding: "12px 16px", borderTop: `1px solid ${A.lineSoft}`, fontFamily: TYPE.text, fontSize: 11.5, color: A.mute, lineHeight: 1.6 }}>
          Also available on this plan, not connected: {available.map((a) => a.name).join(", ")}.
        </div>
      ) : null}
    </Card>
  );
}
