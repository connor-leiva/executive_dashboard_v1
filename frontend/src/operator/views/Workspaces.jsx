import React, { useMemo, useState } from "react";
import { api } from "../api.js";
import { ago, daysSince } from "../format.js";
import { byTrouble, healthOf } from "../health.js";
import { Btn, Card, Chip, Empty, Eyebrow, inputStyle, Loading, LoadError, Mono, useApi } from "../primitives.jsx";
import { loadReference, planName } from "../reference.js";
import { A, STATE, TYPE } from "../tokens.js";

const SORTS = {
  trouble: { label: "Trouble first", fn: byTrouble },
  name: { label: "Name", fn: (a, b) => a.name.localeCompare(b.name) },
  newest: { label: "Newest", fn: (a, b) => String(b.created_at).localeCompare(String(a.created_at)) },
  quiet: { label: "Quietest", fn: (a, b) => String(a.last_login_at || "").localeCompare(String(b.last_login_at || "")) },
  people: { label: "Most people", fn: (a, b) => b.people.active - a.people.active },
};

function WorkspaceRow({ w, onOpen, reference }) {
  const h = healthOf(w);
  const s = STATE[h.state];
  const ok = w.sources - w.sources_in_error - w.sources_stale;
  return (
    <div className="ac-wsrow" style={{ display: "flex", borderTop: `1px solid ${A.lineSoft}`, transition: "background .12s ease" }}>
      <span style={{ width: 3, background: s.c, flexShrink: 0 }} aria-hidden />
      <button type="button" onClick={() => onOpen(w.slug)} className="ac-wsbtn" style={{
        flex: 1, minWidth: 0, display: "flex", alignItems: "center", gap: 14, textAlign: "left",
        background: "none", border: "none", padding: "12px 14px", cursor: "pointer",
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
        ) : rows.map((w) => <WorkspaceRow key={w.slug} w={w} onOpen={onOpen} reference={reference.data} />)}
      </Card>
    </>
  );
}
