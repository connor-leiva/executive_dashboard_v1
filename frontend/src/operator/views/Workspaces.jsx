import React, { useMemo, useState } from "react";
import { api } from "../api.js";
import { ago, daysSince, plural } from "../format.js";
import { byTrouble, healthOf } from "../health.js";
import { Btn, Card, Chip, Empty, Eyebrow, inputStyle, Loading, LoadError, Mono, Notice, useApi } from "../primitives.jsx";
import { loadReference, planName } from "../reference.js";
import { A, STATE, TYPE } from "../tokens.js";

const SORTS = {
  trouble: { label: "Trouble first", fn: byTrouble },
  name: { label: "Name", fn: (a, b) => a.name.localeCompare(b.name) },
  newest: { label: "Newest", fn: (a, b) => String(b.created_at).localeCompare(String(a.created_at)) },
  quiet: { label: "Quietest", fn: (a, b) => String(a.last_login_at || "").localeCompare(String(b.last_login_at || "")) },
  people: { label: "Most people", fn: (a, b) => b.people.active - a.people.active },
};

function WorkspaceRow({ w, onOpen, reference, checked, onCheck }) {
  const h = healthOf(w);
  const s = STATE[h.state];
  const ok = w.sources - w.sources_in_error - w.sources_stale;
  return (
    <div className="ac-wsrow" style={{ display: "flex", borderTop: `1px solid ${A.lineSoft}`, transition: "background .12s ease" }}>
      <span style={{ width: 3, background: s.c, flexShrink: 0 }} aria-hidden />
      <label style={{ display: "flex", alignItems: "center", padding: "0 10px 0 12px", cursor: "pointer" }}>
        <input type="checkbox" checked={checked} onChange={onCheck} aria-label={`Select ${w.name}`}
          style={{ width: 14, height: 14, accentColor: A.ink, cursor: "pointer" }} />
      </label>
      <button type="button" onClick={() => onOpen(w.slug)} className="ac-wsbtn" style={{
        flex: 1, minWidth: 0, display: "flex", alignItems: "center", gap: 14, textAlign: "left",
        background: "none", border: "none", padding: "12px 14px 12px 2px", cursor: "pointer",
      }}>
        <div style={{ minWidth: 0, flex: "2 1 220px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <span style={{ fontFamily: TYPE.text, fontSize: 14, fontWeight: 600, color: A.ink, letterSpacing: "-.01em" }}>{w.name}</span>
            <Chip state={h.state} />
            {w.syncs_frozen ? <Chip state="watch">Syncs frozen</Chip> : null}
          </div>
          {/* The law: the chip never appears without its reason. */}
          <div style={{ fontFamily: TYPE.text, fontSize: 11.5, color: A.body, marginTop: 4, lineHeight: 1.5, textWrap: "pretty" }}>
            {h.why.slice(0, 2).join(" · ")}
          </div>
          <Mono size={10.5} c={A.mute} style={{ display: "block", marginTop: 4 }}>
            {w.hosts[0] || w.slug}{w.hosts.length > 1 ? ` +${w.hosts.length - 1}` : ""}
          </Mono>
        </div>

        <div className="ac-wscols" style={{ display: "flex", gap: 18, flexShrink: 0, flexWrap: "wrap" }}>
          <div style={{ width: 66 }}>
            <Eyebrow style={{ whiteSpace: "nowrap" }}>People</Eyebrow>
            <div style={{ fontFamily: TYPE.data, fontSize: 15, fontWeight: 600, color: A.ink, marginTop: 3, fontVariantNumeric: "tabular-nums" }}>{w.people.active}</div>
            <Mono size={10} c={w.people.invited ? A.warn : A.mute}>{w.people.invited ? `${w.people.invited} invited` : "settled"}</Mono>
          </div>
          <div style={{ width: 76 }}>
            <Eyebrow style={{ whiteSpace: "nowrap" }}>Sources</Eyebrow>
            <div style={{ fontFamily: TYPE.data, fontSize: 15, fontWeight: 600, marginTop: 3, fontVariantNumeric: "tabular-nums",
              color: w.sources_in_error ? A.stop : w.sources_stale ? A.warn : A.ink }}>
              {ok}<span style={{ color: A.mute, fontSize: 12 }}>/{w.sources}</span>
            </div>
            <Mono size={10} c={A.mute}>{w.sources ? (w.sources_in_error ? `${w.sources_in_error} broken` : w.sources_stale ? `${w.sources_stale} stale` : "all syncing") : "none connected"}</Mono>
          </div>
          <div style={{ width: 90 }}>
            <Eyebrow style={{ whiteSpace: "nowrap" }}>Last sign-in</Eyebrow>
            <div style={{ fontFamily: TYPE.data, fontSize: 15, fontWeight: 600, marginTop: 3, fontVariantNumeric: "tabular-nums",
              color: w.last_login_at ? A.ink : A.stop }}>{ago(w.last_login_at)}</div>
            <Mono size={10} c={A.mute}>sync {ago(w.last_synced_at)}</Mono>
          </div>
          <div style={{ width: 70 }}>
            <Eyebrow style={{ whiteSpace: "nowrap" }}>Plan</Eyebrow>
            <div style={{ fontFamily: TYPE.text, fontSize: 12.5, color: w.plan_set ? A.ink : A.warn, marginTop: 5 }}>
              {planName(reference, w.plan)}{w.plan_set ? "" : "*"}
            </div>
            <Mono size={10} c={A.mute}>{daysSince(w.created_at) ?? "?"}d old</Mono>
          </div>
        </div>
        <span className="ac-chev" aria-hidden style={{ fontFamily: TYPE.data, fontSize: 13, color: A.faint, flexShrink: 0, transition: "transform .14s ease, color .14s ease" }}>›</span>
      </button>
    </div>
  );
}

export default function WorkspacesView({ onOpen, onNew }) {
  const tenants = useApi(() => api.tenants(), []);
  const reference = useApi(loadReference, []);
  const [q, setQ] = useState("");
  const [sort, setSort] = useState("trouble");
  const [only, setOnly] = useState("all");
  const [sel, setSel] = useState([]);
  const [bulk, setBulk] = useState(null);          // null | "suspend": the step that needs a reason
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(null);
  const [report, setReport] = useState(null);
  const toggle = (slug) => setSel((s) => (s.includes(slug) ? s.filter((x) => x !== slug) : [...s, slug]));

  /* One call per workspace, in order, reporting each refusal in the server's own words: a bulk action
     that fails silently for two of five workspaces is worse than no bulk action. */
  async function each(key, fn, verb) {
    setBusy(key);
    setReport(null);
    const done = [];
    const refused = [];
    for (const slug of sel) {
      try {
        await fn(slug);
        done.push(slug);
      } catch (e) {
        refused.push(`${slug}: ${e.message}`);
      }
    }
    setBusy(null);
    setBulk(null);
    setReason("");
    setSel((s) => s.filter((slug) => !done.includes(slug)));
    setReport({
      tone: refused.length ? (done.length ? "warn" : "error") : "info",
      text: [done.length ? `${verb} ${plural(done.length, "workspace")}: ${done.join(", ")}.` : "",
        refused.length ? `Not done for ${refused.join(" · ")}` : ""].filter(Boolean).join(" "),
    });
    tenants.reload();
  }

  const all = tenants.data ? tenants.data.tenants : [];
  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return all
      .filter((w) => {
        const state = healthOf(w).state;
        return only === "all"
          || (only === "trouble" && ["broken", "stalled", "watch"].includes(state))
          || (only === "live" && w.status !== "suspended")
          || (only === "suspended" && w.status === "suspended");
      })
      .filter((w) => !needle || [w.name, w.slug, w.owner_email, ...w.hosts].join(" ").toLowerCase().includes(needle))
      .sort(SORTS[sort].fn);
  }, [all, q, sort, only]);

  if (tenants.loading && !tenants.data) return <Loading label="Reading workspaces" />;
  if (tenants.error) return <LoadError error={tenants.error} onRetry={tenants.reload} />;

  const unset = all.filter((w) => !w.plan_set).length;
  return (
    <>
      <div className="ac-toolbar" style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", marginBottom: 14 }}>
        <input value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search workspaces"
          placeholder="Search name, slug, host or owner email"
          style={{ ...inputStyle, flex: "1 1 240px", minWidth: 180, width: "auto" }} />
        <select value={only} onChange={(e) => setOnly(e.target.value)} aria-label="Filter workspaces" style={{ ...inputStyle, width: "auto", cursor: "pointer" }}>
          <option value="all">All workspaces</option>
          <option value="trouble">Needs attention</option>
          <option value="live">Live only</option>
          <option value="suspended">Suspended</option>
        </select>
        <select value={sort} onChange={(e) => setSort(e.target.value)} aria-label="Sort workspaces" style={{ ...inputStyle, width: "auto", cursor: "pointer" }}>
          {Object.entries(SORTS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
        </select>
        <Btn kind="primary" onClick={onNew}>New workspace</Btn>
      </div>

      {sel.length > 0 ? (
        <div className="ac-bulk" style={{
          display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", marginBottom: 12,
          padding: "9px 14px", background: A.ink, borderRadius: 9,
        }}>
          <span style={{ fontFamily: TYPE.text, fontSize: 12, fontWeight: 600, color: A.onInk }}>{sel.length} selected</span>
          {bulk === "suspend" ? (
            <>
              <label htmlFor="ac-bulk-reason" style={{ fontFamily: TYPE.text, fontSize: 11.5, color: A.onInkMute }}>Reason</label>
              <input id="ac-bulk-reason" value={reason} onChange={(e) => setReason(e.target.value)} autoFocus
                placeholder="Recorded on each workspace"
                style={{ ...inputStyle, flex: "1 1 220px", width: "auto", padding: "5px 9px" }} />
              <Btn small kind="onGhost" onClick={() => { setBulk(null); setReason(""); }} disabled={busy === "suspend"}>Cancel</Btn>
              <Btn small kind="dangerSolid" disabled={!reason.trim()} busy={busy === "suspend"}
                onClick={() => each("suspend", (slug) => api.suspend(slug, reason.trim()), "Suspended")}>
                Suspend {plural(sel.length, "workspace")}
              </Btn>
            </>
          ) : (
            <>
              <div style={{ flex: 1 }} />
              <Btn small kind="onGhost" busy={busy === "sync"} onClick={() => each("sync", (slug) => api.syncTenant(slug), "Started a sync for")}>Run sync</Btn>
              <Btn small kind="onDanger" disabled={Boolean(busy)} onClick={() => setBulk("suspend")}>Suspend</Btn>
              <button type="button" onClick={() => setSel([])} className="ac-link" style={{
                background: "none", border: "none", color: A.onInkMute, fontFamily: TYPE.text, fontSize: 11.5, cursor: "pointer",
              }}>Clear</button>
            </>
          )}
        </div>
      ) : null}
      {report ? <div style={{ marginBottom: 12 }}><Notice tone={report.tone}>{report.text}</Notice></div> : null}

      <Card pad={0}>
        <div style={{
          display: "flex", alignItems: "center", justifyContent: "space-between", gap: "6px 14px", flexWrap: "wrap",
          padding: "11px 16px", borderBottom: `1px solid ${A.lineSoft}`,
        }}>
          <Eyebrow>{rows.length} of {all.length} workspaces · sorted by {SORTS[sort].label.toLowerCase()}</Eyebrow>
          <Eyebrow>{unset ? `* no plan set: defaulted to Portfolio (${unset})` : "Colour rail = worst open signal"}</Eyebrow>
        </div>
        {rows.length === 0 ? (
          <Empty title={all.length ? "No workspaces match" : "No workspaces yet"}
            action={all.length ? <Btn small onClick={() => { setQ(""); setOnly("all"); }}>Clear filters</Btn>
              : <Btn small kind="primary" onClick={onNew}>Create the first</Btn>}>
            {all.length ? "Nothing matches that search. Clear the filters to see the whole fleet." : "Nothing has been provisioned."}
          </Empty>
        ) : rows.map((w) => (
          <WorkspaceRow key={w.slug} w={w} onOpen={onOpen} reference={reference.data}
            checked={sel.includes(w.slug)} onCheck={() => toggle(w.slug)} />
        ))}
      </Card>
    </>
  );
}
