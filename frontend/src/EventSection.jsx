/* The Forum — Event section (FORUM-EVENT-SPEC.md Phase 5).

   The Forum runs four in-person events a year and they are its sales funnel: guests register as
   $2,500 VIP attendees, experience the room, and are upsold into a yearly membership. This tab
   answers the two questions that follow — are we filling the room, and are the guests
   converting.

   Everything is server-computed (compute_event); nothing is derived here. Colours come from
   theme.js tokens only. The hero number deliberately disagrees with the funnel row beneath it:
   guests are counted from the RSVP TAG and the funnel counts opportunity STAGES, and the gap is
   people with no sale open — which is the point, so it is labelled rather than reconciled away. */
import { createContext, useContext, useEffect, useState } from "react";
import { T, alpha, usd } from "./theme.js";
import { getJSON, putJSON } from "./api.js";

/* Every number is a drill target. The context carries the opener so a nested figure can trigger
   it without prop-threading. */
const DrillCtx = createContext(null);
function Num({ metric, children, title }) {
  const open = useContext(DrillCtx);
  if (!open || !metric) return <>{children}</>;
  return (
    <span className="num" role="button" tabIndex={0} title={title || "Drill in"}
      onClick={(e) => { e.stopPropagation(); open(metric); }}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(metric); } }}>
      {children}
    </span>
  );
}

/* A money figure with no price configured is an EM DASH, never $0. Zero reads as a result
   ("we booked nothing"); a dash reads as an absence ("nobody has priced this"). */
const money = (v) => (v == null ? "—" : usd(v));
const pct = (v) => (v == null ? "—" : `${Math.round(v * 100)}%`);

const STATE_TONE = {
  behind: { bg: T.poppy, fg: T.white, label: "Behind pace" },
  onpace: { bg: T.meadowBg, fg: T.meadowInk, label: "On pace" },
  ahead: { bg: T.meadowBg, fg: T.meadowInk, label: "Ahead of pace" },
  done: { bg: T.page, fg: T.tertiary, label: "Event has passed" },
  pending: { bg: T.page, fg: T.tertiary, label: "No pace curve set" },
};

function Stat({ label, value, sub, metric }) {
  return (
    <div className="ev-stat">
      <div className="ev-stat-l">{label}</div>
      <div className="ev-stat-v"><Num metric={metric}>{value}</Num></div>
      {sub && <div className="ev-stat-s">{sub}</div>}
    </div>
  );
}

/* ── the drill drawer ───────────────────────────────────────────────────────────────────── */
function Drawer({ businessKey, eventId, metric, onClose, usingSample }) {
  const [d, setD] = useState(null);
  const [err, setErr] = useState(null);

  useEffect(() => {
    if (!metric) return;
    if (usingSample) { setErr("sample"); return; }
    setD(null); setErr(null);
    getJSON(`/businesses/${businessKey}/events/${eventId}/drill/${encodeURIComponent(metric)}`)
      .then(setD)
      .catch((e) => setErr(e?.detail || e?.message || "Could not open this one."));
  }, [metric, businessKey, eventId, usingSample]);

  if (!metric) return null;
  return (
    <div className="drx-scrim" onClick={onClose}>
      <div className="drx" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true">
        <div className="drx-head">
          <span className="drx-title">{d?.title || "Loading…"}</span>
          <button className="drx-x" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <div className="drx-body">
          {err === "sample" && <div className="drx-note">Sample data — connect the API to drill in.</div>}
          {err && err !== "sample" && <div className="drx-note">{err}</div>}
          {d && (
            <>
              <div className="drx-sub">{d.subtitle}</div>
              {d.rows?.length === 0 && <div className="drx-empty">Nobody here yet.</div>}
              {d.rows?.length > 0 && (
                <div className="drx-tblwrap">
                  <table className="drx-tbl">
                    <thead><tr>{["Name", "Stage", "Rep", "Invited by"].map((c) => <th key={c}>{c}</th>)}</tr></thead>
                    <tbody>
                      {d.rows.map((r, i) => (
                        <tr key={i}>
                          <td>{r.name}{r.comped ? " · comped" : ""}</td>
                          <td>{r.stage}</td>
                          <td>{r.rep}</td>
                          <td>{r.invited_by}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

/* ── settings ───────────────────────────────────────────────────────────────────────────── */
const TAG_HELP = "One per line. Guest tags match as substrings, so “…guest rsvp” also catches " +
                 "“…guest rsvp paid”. Member tags match exactly.";

function SettingsDrawer({ businessKey, cfg, onSaved, onClose, canPersist }) {
  const [f, setF] = useState(() => ({ ...cfg }));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const set = (k) => (e) => setF((p) => ({ ...p, [k]: e.target.value }));
  const lines = (v) => (Array.isArray(v) ? v.join("\n") : "");
  const toLines = (k) => (e) =>
    setF((p) => ({ ...p, [k]: e.target.value.split("\n").map((x) => x.trim()).filter(Boolean) }));

  const save = async () => {
    setBusy(true); setErr(null);
    try {
      await putJSON(`/businesses/${businessKey}/events/${cfg.id}`, {
        name: f.name, slug: f.slug, status: f.status,
        starts_on: f.starts_on || null, ends_on: f.ends_on || null,
        window_start: f.window_start || null, window_end: f.window_end || null,
        venue: f.venue || null,
        guest_goal: f.guest_goal === "" || f.guest_goal == null ? null : Number(f.guest_goal),
        member_goal: f.member_goal === "" || f.member_goal == null ? null : Number(f.member_goal),
        vip_price: f.vip_price === "" || f.vip_price == null ? null : Number(f.vip_price),
        guest_tags: f.guest_tags, member_tags: f.member_tags, declined_tags: f.declined_tags,
        pipeline_match: f.pipeline_match, comp_tag_match: f.comp_tag_match,
      });
      onSaved?.(); onClose();
    } catch (e) {
      setErr(e?.detail || e?.message || "Could not save.");
    } finally { setBusy(false); }
  };

  const Field = ({ label, help, children }) => (
    <label className="ev-field">
      <span className="ev-field-l">{label}</span>
      {children}
      {help && <span className="ev-field-h">{help}</span>}
    </label>
  );

  return (
    <div className="drx-scrim" onClick={onClose}>
      <div className="drx" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true">
        <div className="drx-head">
          <span className="drx-title">Event settings</span>
          <button className="drx-x" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <div className="drx-body">
          <Field label="Name"><input value={f.name || ""} onChange={set("name")} /></Field>
          <Field label="Key" help="Short identifier, e.g. q4-2026.">
            <input value={f.slug || ""} onChange={set("slug")} />
          </Field>
          <Field label="Venue"><input value={f.venue || ""} onChange={set("venue")} /></Field>
          <div className="ev-row2">
            <Field label="Starts"><input type="date" value={f.starts_on || ""} onChange={set("starts_on")} /></Field>
            <Field label="Ends"><input type="date" value={f.ends_on || ""} onChange={set("ends_on")} /></Field>
          </div>
          <div className="ev-row2">
            <Field label="Selling from"><input type="date" value={f.window_start || ""} onChange={set("window_start")} /></Field>
            <Field label="Selling until"><input type="date" value={f.window_end || ""} onChange={set("window_end")} /></Field>
          </div>
          <Field label="Guest goal" help="How many VIP guests you are aiming for.">
            <input type="number" value={f.guest_goal ?? ""} onChange={set("guest_goal")} />
          </Field>
          <Field label="VIP guest tags" help={TAG_HELP}>
            <textarea rows={2} value={lines(f.guest_tags)} onChange={toLines("guest_tags")} />
          </Field>
          <Field label="Member registration tags" help="Members attending. Never derived by subtracting guests.">
            <textarea rows={2} value={lines(f.member_tags)} onChange={toLines("member_tags")} />
          </Field>
          <Field label="Not-attending tags" help="Optional. Drives the “declined” figure only.">
            <textarea rows={2} value={lines(f.declined_tags)} onChange={toLines("declined_tags")} />
          </Field>
          <Field label="Pipelines" help="Substrings of the GHL pipeline name. More than one may feed an event.">
            <textarea rows={2} value={lines(f.pipeline_match)} onChange={toLines("pipeline_match")} />
          </Field>
          <Field label="Comped tag contains" help="A tag containing this marks a guest as comped.">
            <input value={f.comp_tag_match || ""} onChange={set("comp_tag_match")} />
          </Field>
          <Field label="VIP ticket price"
                 help="Optional. Leave empty and every money figure shows a dash — the room count never needs it.">
            <input type="number" value={f.vip_price ?? ""} onChange={set("vip_price")} />
          </Field>
          {err && <div className="ev-err">{err}</div>}
          <div className="ev-actions">
            <button className="ev-btn" onClick={onClose}>Cancel</button>
            <button className="ev-btn ev-btn-go" onClick={save}
                    disabled={!canPersist || busy}
                    title={canPersist ? "" : "Read-only — sample data, or you do not have edit access."}>
              {busy ? "Saving…" : canPersist ? "Save" : "Read-only"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ── the section ────────────────────────────────────────────────────────────────────────── */
export default function EventSection({ data, usingSample, role, businessKey = "springb", onSaved }) {
  const [metric, setMetric] = useState(null);
  const [settings, setSettings] = useState(false);
  const isEditor = !role || role === "owner" || role === "admin";
  const canPersist = isEditor && !usingSample;

  const ev = data.event || {};
  const r = data.registration || {};
  const rev = data.revenue || {};
  const tone = STATE_TONE[r.state] || STATE_TONE.pending;
  const barPct = Math.min(100, Math.round((r.pct_to_goal || 0) * 100));
  const expectedPct = r.expected_pct == null ? null : Math.min(100, Math.round(r.expected_pct * 100));

  return (
    <DrillCtx.Provider value={setMetric}>
      <div className="evt">
        <style>{`
        .evt { padding:18px 20px 28px; }
        .evt .num { cursor:pointer; border-radius:3px; box-shadow:inset 0 -1px 0 ${alpha(T.muted, 0)};
          transition:box-shadow .12s ease; }
        .evt .num:hover { box-shadow:inset 0 -1.5px 0 currentColor; }
        .evt .num:focus-visible { outline:2px solid ${T.petal}; outline-offset:2px; }

        .evt .ev-head { display:flex; align-items:flex-start; justify-content:space-between; gap:14px;
          flex-wrap:wrap; margin-bottom:14px; }
        .evt .ev-title { font-family:var(--font-display); font-size:20px; font-weight:700; color:${T.ink};
          letter-spacing:-.02em; }
        .evt .ev-when { font-size:12.5px; color:${T.muted}; margin-top:2px; }
        .evt .ev-gear { border:1px solid ${T.line}; background:${T.white}; border-radius:9px; height:32px;
          padding:0 12px; font-size:12.5px; color:${T.secondary}; cursor:pointer; }

        .evt .ev-hero { border:1px solid ${T.line}; border-radius:16px; background:${T.white};
          padding:20px 22px; margin-bottom:14px; }
        .evt .ev-big { font-family:var(--font-display); font-size:44px; font-weight:700; color:${T.ink};
          letter-spacing:-.03em; line-height:1; font-variant-numeric:tabular-nums; }
        .evt .ev-of { font-size:15px; color:${T.muted}; font-weight:500; }
        .evt .ev-pill { display:inline-block; border-radius:999px; padding:3px 10px; font-size:11.5px;
          font-weight:600; }
        .evt .ev-track { position:relative; height:10px; border-radius:999px; background:${T.page};
          margin:14px 0 6px; overflow:hidden; }
        .evt .ev-fill { position:absolute; inset:0 auto 0 0; border-radius:999px; background:${T.evergreen}; }
        .evt .ev-exp { position:absolute; top:-3px; bottom:-3px; width:2px; background:${T.poppy}; }
        .evt .ev-legend { display:flex; justify-content:space-between; font-size:11.5px; color:${T.muted}; }

        .evt .ev-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px;
          margin-bottom:14px; }
        .evt .ev-stat { border:1px solid ${T.line}; border-radius:12px; background:${T.white};
          padding:12px 14px; min-width:0; }
        .evt .ev-stat-l { font-size:10px; font-weight:700; letter-spacing:.14em; text-transform:uppercase;
          color:${T.muted}; }
        .evt .ev-stat-v { font-family:var(--font-display); font-size:22px; font-weight:700; color:${T.ink};
          letter-spacing:-.02em; margin-top:3px; font-variant-numeric:tabular-nums; }
        .evt .ev-stat-s { font-size:11.5px; color:${T.muted}; margin-top:2px; }

        .evt .ev-card { border:1px solid ${T.line}; border-radius:14px; background:${T.white};
          padding:16px 18px; margin-bottom:14px; }
        .evt .ev-card-h { font-size:10.5px; font-weight:700; letter-spacing:.14em; text-transform:uppercase;
          color:${T.muted}; margin-bottom:10px; }
        .evt .ev-step { display:flex; align-items:center; gap:10px; padding:7px 0; }
        .evt .ev-step-l { flex:0 0 132px; font-size:12.5px; color:${T.secondary}; }
        .evt .ev-step-bar { flex:1; height:8px; border-radius:999px; background:${T.page}; overflow:hidden; min-width:0; }
        .evt .ev-step-fill { height:100%; border-radius:999px; background:${T.meadow}; }
        .evt .ev-step-n { flex:0 0 46px; text-align:right; font-family:var(--font-display); font-weight:600;
          color:${T.ink}; font-variant-numeric:tabular-nums; }
        .evt .ev-note { font-size:11.5px; color:${T.muted}; line-height:1.55; margin-top:8px; }
        .evt .ev-warn { border-left:3px solid ${T.daffodil}; background:${T.daffodilBg}; border-radius:8px;
          padding:9px 12px; font-size:12px; color:${T.daffodilText}; margin-bottom:8px; }

        .evt .ev-field { display:block; margin-bottom:12px; }
        .evt .ev-field-l { display:block; font-size:11px; font-weight:700; letter-spacing:.08em;
          text-transform:uppercase; color:${T.muted}; margin-bottom:4px; }
        .evt .ev-field input, .evt .ev-field textarea { width:100%; box-sizing:border-box;
          font-family:var(--font-text); font-size:13px; padding:7px 10px; border:1px solid ${T.line};
          border-radius:8px; color:${T.ink}; background:${T.white}; }
        .evt .ev-field-h { display:block; font-size:11px; color:${T.muted}; margin-top:4px; line-height:1.45; }
        .evt .ev-row2 { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
        .evt .ev-actions { display:flex; justify-content:flex-end; gap:8px; margin-top:14px; }
        .evt .ev-btn { border:1px solid ${T.line}; background:${T.white}; border-radius:9px; height:32px;
          padding:0 14px; font-size:12.5px; color:${T.secondary}; cursor:pointer; }
        .evt .ev-btn-go { background:${T.evergreen}; color:${T.onDark}; border-color:${T.evergreen}; }
        .evt .ev-btn-go:disabled { opacity:.55; cursor:not-allowed; }
        .evt .ev-err { font-size:12px; color:${T.gapText}; margin-top:8px; }

        /* drill drawer — scoped under .evt so it cannot collide with the other sections'
           copies of these class names. */
        .evt .drx-scrim { position:fixed; inset:0; background:${alpha(T.evergreen, .32)}; z-index:60;
          display:flex; justify-content:flex-end; }
        .evt .drx { width:min(460px,92vw); height:100%; background:${T.white}; display:flex;
          flex-direction:column; box-shadow:-12px 0 40px ${alpha(T.evergreen, .2)}; }
        .evt .drx-head { display:flex; justify-content:space-between; align-items:center; padding:16px 20px;
          border-bottom:1px solid ${T.line}; background:${T.parchment}; }
        .evt .drx-title { font-family:var(--font-display); font-size:14px; font-weight:700; color:${T.ink}; }
        .evt .drx-x { border:none; background:none; font-size:15px; color:${T.slate}; cursor:pointer; }
        .evt .drx-body { padding:18px 20px; overflow-y:auto; }
        .evt .drx-sub { font-size:12px; color:${T.muted}; margin-bottom:10px; }
        .evt .drx-note { font-size:12px; color:${T.muted}; line-height:1.5; }
        .evt .drx-empty { font-size:12.5px; color:${T.muted}; padding:12px 0; }
        .evt .drx-tblwrap { overflow-x:auto; }
        .evt .drx-tbl { width:100%; border-collapse:collapse; font-size:12px; }
        .evt .drx-tbl th { text-align:left; font-size:10px; font-weight:700; letter-spacing:.05em;
          text-transform:uppercase; color:${T.muted}; padding:6px 8px; border-bottom:1px solid ${T.line}; }
        .evt .drx-tbl td { padding:7px 8px; border-bottom:1px solid ${T.parchment}; color:${T.secondary}; }
        `}</style>

        <div className="ev-head">
          <div>
            <div className="ev-title">{ev.name || "Event"}</div>
            <div className="ev-when">
              {ev.starts_on ? new Date(`${ev.starts_on}T12:00:00`).toLocaleDateString("en-US",
                { weekday: "long", month: "long", day: "numeric" }) : "No date set"}
              {ev.venue ? ` · ${ev.venue}` : ""}
              {r.days_to_event != null && r.days_to_event >= 0 ? ` · ${r.days_to_event} days out` : ""}
            </div>
          </div>
          <button className="ev-gear" onClick={() => setSettings(true)}>Settings</button>
        </div>

        {/* the hero: are we filling the room */}
        <div className="ev-hero">
          <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
            <span className="ev-big"><Num metric="event_guests">{r.guests ?? 0}</Num></span>
            <span className="ev-of">of {r.goal ?? "—"} VIP guests</span>
            <span className="ev-pill" style={{ background: tone.bg, color: tone.fg }}>{tone.label}</span>
          </div>
          <div className="ev-track">
            <div className="ev-fill" style={{ width: `${barPct}%` }} />
            {expectedPct != null && <div className="ev-exp" style={{ left: `${expectedPct}%` }}
                                         title={`Expected ${r.expected} by now`} />}
          </div>
          <div className="ev-legend">
            <span>{pct(r.pct_to_goal)} of goal</span>
            <span>{r.expected != null ? `expected ${r.expected} by now` : "no pace curve set"}</span>
          </div>
        </div>

        <div className="ev-grid">
          <Stat label="In the room" value={r.room ?? 0} metric="event_room"
                sub={`${r.guests ?? 0} guests + ${r.members_registered ?? 0} members`} />
          <Stat label="Members registered" value={r.members_registered ?? 0}
                metric="event_members_registered"
                sub={r.members_declined != null ? `${r.members_declined} said no` : null} />
          <Stat label="Paid / comped" value={`${r.paid ?? 0} / ${r.comped ?? 0}`} metric="event_paid" />
          <Stat label="Ticket revenue" value={money(rev.ticket_booked)} metric="event_paid"
                sub={ev.vip_price == null ? "no price set" : `${r.paid ?? 0} × ${usd(ev.vip_price)}`} />
        </div>

        {(data.warnings || []).length > 0 && (
          <div className="ev-card">
            <div className="ev-card-h">Needs a look</div>
            {r.guests_without_opp > 0 && (
              <div className="ev-warn">
                <Num metric="event_without_opp">{r.guests_without_opp}</Num> RSVP’d with no sale
                open — nobody is working them.
              </div>
            )}
            {r.guests_stage_conflict > 0 && (
              <div className="ev-warn">
                <Num metric="event_stage_conflict">{r.guests_stage_conflict}</Num> tagged as
                RSVP’d but parked or lost in the funnel.
              </div>
            )}
          </div>
        )}

        {/* the funnel: where the sale is, which is a different question from who is coming */}
        <div className="ev-card">
          <div className="ev-card-h">The sale</div>
          {(data.funnel || []).map((f) => {
            const max = Math.max(...(data.funnel || []).map((x) => x.count), 1);
            return (
              <div className="ev-step" key={f.key}>
                <span className="ev-step-l">{f.label}</span>
                <span className="ev-step-bar">
                  <span className="ev-step-fill" style={{ width: `${(f.count / max) * 100}%` }} />
                </span>
                <span className="ev-step-n">
                  <Num metric={`event_group_${f.key}`}>{f.count}</Num>
                </span>
              </div>
            );
          })}
          <div className="ev-note">
            The hero counts RSVP <b>tags</b>; this counts opportunity <b>stages</b>. They differ by
            the {r.guests_without_opp ?? 0} with no sale open and the {r.guests_stage_conflict ?? 0}
            {" "}parked in the funnel — which is the gap worth closing, not an error.
          </div>
        </div>

        <div className="ev-grid">
          <Stat label="Became members" value={rev.members ?? 0} metric="event_converted"
                sub={rev.conversion?.rate != null ? `${pct(rev.conversion.rate)} of guests` : "—"} />
          <Stat label="Membership ARR" value={money(rev.member_arr)}
                sub={ev.price_map && Object.keys(ev.price_map).length ? null : "no pricing set"} />
          <Stat label="Guests this week" value={data.momentum?.guests?.now ?? 0}
                sub={data.momentum?.guests?.was != null ? `was ${data.momentum.guests.was}` : null} />
        </div>

        <Drawer businessKey={businessKey} eventId={ev.id} metric={metric} usingSample={usingSample}
                onClose={() => setMetric(null)} />
        {settings && (
          <SettingsDrawer businessKey={businessKey} cfg={ev} canPersist={canPersist}
                          onSaved={onSaved} onClose={() => setSettings(false)} />
        )}
      </div>
    </DrillCtx.Provider>
  );
}
