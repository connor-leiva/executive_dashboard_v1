import React from "react";
import { api } from "../api.js";
import { ago, plural } from "../format.js";
import { Btn, Card, Chip, Empty, Loading, LoadError, Mono, Row, Stat, useApi } from "../primitives.jsx";
import { A, TYPE } from "../tokens.js";

/* Service states map onto the console's severity vocabulary, so a check reads the same here as a
   broken source does on the fleet list. */
const TONE = { ok: "healthy", idle: undefined, late: "watch", never: "broken", check: "broken", forked: "broken", behind: "watch", unknown: undefined };
const WORD = { ok: "Healthy", idle: "Idle", late: "Late", never: "Not reporting", check: "Check", forked: "Forked", behind: "Behind", unknown: "Unknown" };

function every(minutes) {
  if (minutes == null) return "on demand";
  if (minutes < 1) return `every ${Math.round(minutes * 60)}s`;
  if (minutes < 60) return `every ${plural(minutes, "minute")}`;
  if (minutes < 1440) return `every ${plural(Math.round(minutes / 60), "hour")}`;
  if (minutes < 43200) return `every ${plural(Math.round(minutes / 1440), "day")}`;
  return "monthly";
}

function bytes(n) {
  if (n == null) return null;
  if (n >= 1e9) return `${(n / 1e9).toFixed(1)} GB`;
  if (n >= 1e6) return `${Math.round(n / 1e6)} MB`;
  return `${Math.round(n / 1e3)} KB`;
}

function FlagsCard() {
  const flags = useApi(() => api.flags(), []);
  return (
    <Card title="Environment flags" sub="Each of these changes behaviour for every workspace at once, which is why they are on a page someone will look at.">
      {flags.loading && !flags.data ? <Loading label="Reading flags" />
        : flags.error ? <Empty title="Flags could not be read">{flags.error.message}</Empty>
          : flags.data.flags.map((f) => (
            <div key={f.key} style={{ padding: "11px 0", borderTop: `1px solid ${A.lineSoft}` }}>
              <div style={{ display: "flex", gap: 10, alignItems: "center", justifyContent: "space-between", flexWrap: "wrap" }}>
                <Mono size={11.5} c={A.ink}>{f.key}</Mono>
                <Chip state={f.risk ? "watch" : undefined} mono>{String(f.value)}</Chip>
              </div>
              <div style={{ fontFamily: TYPE.text, fontSize: 11.5, color: f.risk ? A.warn : A.mute, marginTop: 4, lineHeight: 1.55, textWrap: "pretty" }}>{f.note}</div>
            </div>
          ))}
    </Card>
  );
}

export default function SystemView() {
  const sys = useApi(() => api.system(), []);
  if (sys.loading && !sys.data) return <Loading label="Reading the platform's health" />;
  if (sys.error) return <LoadError error={sys.error} onRetry={sys.reload} />;

  const { release, api: apiState, database: db, migrations: mig, worker } = sys.data;
  const tick = worker.jobs.find((j) => j.job === "tick");

  return (
    <>
      <div className="ac-tiles ac-t4" style={{ marginBottom: 16 }}>
        <Stat label="API" value={WORD[apiState.state]} state={TONE[apiState.state]}
          note={`answering · up ${ago(apiState.started_at).replace(" ago", "")} · requests are not timed, so there is no latency figure`} />
        <Stat label="Worker" value={WORD[worker.state]} state={TONE[worker.state]} note={worker.why} />
        <Stat label="Database" value={WORD[db.state]} state={TONE[db.state]}
          note={[db.latency_ms != null ? `answered in ${db.latency_ms} ms` : null,
            db.connections != null ? `${plural(db.connections, "connection")}` : null,
            bytes(db.size_bytes)].filter(Boolean).join(" · ") || db.dialect} />
        <Stat label="Migrations" value={WORD[mig.state]} state={TONE[mig.state]} note={mig.why} />
      </div>

      <div className="ac-split" style={{ marginBottom: 16 }}>
        <Card title="Release" sub="What is running, and since when.">
          <Row k="Commit">{release.commit ? <Mono c={A.ink}>{release.commit}</Mono> : <span style={{ color: A.mute }}>Not reported by the host</span>}</Row>
          {release.message ? <Row k="Message"><span style={{ color: A.body }}>{release.message}</span></Row> : null}
          <Row k="Branch">{release.branch ? <Mono c={A.ink}>{release.branch}</Mono> : <span style={{ color: A.mute }}>Not reported</span>}</Row>
          <Row k="Running since">{new Date(release.started_at).toLocaleString()} <Mono size={10.5} c={A.mute}>({ago(release.started_at)})</Mono></Row>
          <Row k="ENV"><Chip state={release.env_risk ? "watch" : undefined} mono>{release.env}</Chip></Row>
          <Row k="Migration head" top>
            {mig.heads.length ? mig.heads.map((h) => <div key={h}><Mono c={A.ink}>{h}</Mono></div>) : <span style={{ color: A.mute }}>None found</span>}
          </Row>
          <Row k="Database is at" top>
            {mig.database.length ? mig.database.map((h) => <div key={h}><Mono c={A.ink}>{h}</Mono></div>) : <span style={{ color: A.mute }}>No version recorded</span>}
          </Row>
          <div style={{ marginTop: 12, padding: "10px 12px", background: A.ground, borderRadius: 8, fontFamily: TYPE.text, fontSize: 11.5, color: A.body, lineHeight: 1.6, textWrap: "pretty" }}>
            More than one head means two migrations branched from the same parent, and the next deploy will not apply cleanly.
            It is checked on every load of this page because a fork has taken production down before.
          </div>
        </Card>
        <FlagsCard />
      </div>

      <Card title="Scheduled jobs" pad={0}
        sub={worker.runs_in_api ? "The API process runs the scheduler (RUN_WORKER_IN_API)." : "The scheduler runs in a separate worker process, not in the API."}>
        {worker.jobs.length === 0 ? (
          <Empty title="No job has reported">Heartbeats are written each time a scheduled job starts and finishes. None has been written yet.</Empty>
        ) : worker.jobs.map((j) => (
          <div key={j.job} style={{ display: "flex", borderTop: `1px solid ${A.lineSoft}` }}>
            <span style={{ width: 3, background: j.state === "ok" || j.state === "idle" ? "transparent" : j.state === "late" ? A.warn : A.stop, flexShrink: 0 }} aria-hidden />
            <div style={{ flex: 1, minWidth: 0, padding: "10px 16px", display: "flex", gap: 14, alignItems: "baseline", flexWrap: "wrap" }}>
              <div style={{ flex: "1 1 220px", minWidth: 0 }}>
                <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                  <Mono size={12} c={A.ink}>{j.job}</Mono>
                  <Chip state={TONE[j.state]}>{WORD[j.state]}</Chip>
                  {!j.enabled ? <Chip>Off in this environment</Chip> : null}
                </div>
                <div style={{ fontFamily: TYPE.text, fontSize: 11.5, color: A.mute, marginTop: 3, lineHeight: 1.5 }}>{j.what} · {every(j.every_minutes)}</div>
                {j.last_error && j.last_failed_at && (!j.last_ok_at || j.last_failed_at > j.last_ok_at) ? (
                  <div style={{ fontFamily: TYPE.data, fontSize: 10.5, color: A.stop, marginTop: 4, overflowWrap: "anywhere" }}>{j.last_error}</div>
                ) : null}
              </div>
              <div style={{ display: "flex", gap: 18, flexShrink: 0 }}>
                <div><Mono size={10} c={A.mute}>started</Mono><div style={{ fontFamily: TYPE.data, fontSize: 12, color: A.ink }}>{ago(j.last_started_at)}</div></div>
                <div><Mono size={10} c={A.mute}>last clean run</Mono><div style={{ fontFamily: TYPE.data, fontSize: 12, color: A.ink }}>{ago(j.last_ok_at)}</div></div>
                <div><Mono size={10} c={A.mute}>took</Mono><div style={{ fontFamily: TYPE.data, fontSize: 12, color: A.ink }}>{j.last_seconds != null ? `${j.last_seconds}s` : "—"}</div></div>
              </div>
            </div>
          </div>
        ))}
      </Card>

      <div style={{ marginTop: 12, display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
        <Mono size={10.5} c={A.mute}>read {ago(sys.data.read_at)}{tick ? "" : " · the sync tick has never reported"}</Mono>
        <Btn small kind="quiet" onClick={sys.reload} busy={sys.loading}>Refresh</Btn>
      </div>
    </>
  );
}
