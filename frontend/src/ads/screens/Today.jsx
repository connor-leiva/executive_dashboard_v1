import { useState } from "react";

import { API_BASE, postJSON } from "../../api";
import { C, num, usd } from "../adsTokens.js";
import { accountToday, weekPace, windowPace, windowPhrase } from "../pace.js";
import {
  Banner, Btn, Card, Drawer, Empty, Glyph, PACE_WORD, PageTitle, Stat, foldRows, sentenceStatus, tone,
} from "../ui.jsx";

const VERDICT = { kill: "Turn off", keep: "Keep", scale: "Scale" };

const PACE_NOTE = { ahead: "Ahead of plan", behind: "Behind plan", on: "On plan" };

/* Where an ad runs, as words rather than a dot-separated string. */
function placement(item) {
  if (item.campaign && item.adset) return `${item.campaign}, ad set ${item.adset}`;
  if (item.campaign) return item.campaign;
  if (item.adset) return `Ad set ${item.adset}`;
  return null;
}

function keepLine(rows) {
  const below = rows.filter((r) => r.metric === "delivery").length;
  const inside = rows.length - below;
  const bits = [];
  if (inside) bits.push("numbers are inside the bands");
  if (below) bits.push("below the minimum of $25 spent and 1,000 impressions");
  return bits.join(", or ") || "nothing to change";
}

const capital = (s) => s.charAt(0).toUpperCase() + s.slice(1);

export default function Today({ data, onSaved, onBudget }) {
  const writes = data.writes || {};
  const items = data.recommendations || [];
  const [picked, setPicked] = useState({});
  const [openKey, setOpenKey] = useState(null);
  const [showKeeps, setShowKeeps] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const pace = windowPace(data, accountToday(data));
  const paceTone = tone(pace.status, pace.open);
  const spring = (data.brand_funnels?.forum?.rungs || []).find((r) => r.key === "qualified");
  const decisions = items.filter((i) => i.verdict === "kill" || i.verdict === "scale");
  const keeps = items.filter((i) => i.verdict === "keep");
  const folded = foldRows(decisions);
  const selected = decisions.filter((i) => i.action && picked[`${i.level}:${i.external_id}`]);
  // The window is named, because the weekly bars below it measure a different one.
  const paceValue = !pace.plan ? "No plan" : pace.pace == null ? "Not started" : `${Math.round(pace.pace)}%`;
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
    <div className="ax-stack">
      <PageTitle title="Today" />
      <div className="ax-stats">
        <Stat label="Spent" value={usd(pace.spend)} />
        <Stat label="Spring leads" value={spring?.missing ? "Not recorded" : num(spring?.n)} />
        <Stat label="Cost / Spring lead" value={usd(spring?.cost_per)} />
        <Stat label={`Pace, ${windowPhrase(data.range?.label)}`} value={paceValue}
              color={pace.pace == null ? undefined : paceTone.ink} note={PACE_NOTE[pace.status]} />
      </div>

      <WeekPace data={data} onBudget={onBudget} />

      {!writes.can_write && (
        <Banner title="Reconnect Meta to enable changes">
          Approve previews the change until the token includes ads_management.
        </Banner>
      )}
      {writes.killed && <Banner title="Ad changes are stopped">The kill switch is on. Approve stays a preview.</Banner>}

      <div className="ax-actionbar">
        <Btn primary disabled={busy || !!blocked} title={blocked || "Approve sends this change to Meta."}
             onClick={() => apply(selected, false)}>Approve selected</Btn>
        <Btn disabled={busy || !selected.length} onClick={() => apply(selected, true)}>Preview</Btn>
        <Btn disabled={!selected.length} onClick={() => setPicked({})}>Skip</Btn>
        <span className="ax-meta" style={blocked ? { color: C.slate } : undefined}>
          {blocked || "Approve sends this change to Meta."}
        </span>
      </div>

      <Card flush>
        <div className="ax-list-rows">
          {folded.length === 0 && <div className="ax-empty"><Empty>Nothing to turn off or scale in this window.</Empty></div>}
          {folded.map((bucket) => (
            <DecisionGroup key={bucket.key} bucket={bucket} picked={picked} setPicked={setPicked}
                           openKey={openKey} setOpenKey={setOpenKey} keeps={keeps} />
          ))}
          {keeps.length > 0 && (
            <div className="ax-dec">
              <button type="button" className="ax-dec__head" aria-expanded={showKeeps}
                      onClick={() => setShowKeeps((v) => !v)}>
                <Glyph name="chevron" />
                <span className="ax-verdict" data-v="keep">Keep</span>
                <span>
                  <span className="ax-dec__name">
                    {keeps.length} {keeps.length === 1 ? "ad" : "ads"} to keep
                  </span>
                  <span className="ax-dec__line" style={{ display: "block" }}>{capital(keepLine(keeps))}.</span>
                </span>
                <span className="ax-dec__more">{showKeeps ? "Hide list" : "Show list"}</span>
              </button>
            </div>
          )}
          {showKeeps && keeps.map((item) => (
            <div key={`${item.level}:${item.external_id}`}
                 className={`ax-dec ax-dec--nested${openKey === `${item.level}:${item.external_id}` ? " is-on" : ""}`}>
              <span />
              <RowBody item={item} keeps={decisions}
                       selected={openKey === `${item.level}:${item.external_id}`}
                       onOpen={() => setOpenKey(`${item.level}:${item.external_id}`)} />
            </div>
          ))}
        </div>
      </Card>

      {error && <p className="ax-text" style={{ color: C.badInk }}>{error}</p>}
      {result && (
        <Card>
          <div className="ax-stack" style={{ gap: 4 }}>
            {result.message && <p className="ax-text" style={{ color: C.ink, fontWeight: 600 }}>{result.message}</p>}
            {(result.results || []).map((r, i) => (
              <p key={i} className="ax-text" style={{ color: r.meta_error ? C.badInk : C.slate }}>
                {r.name || r.external_id}: {r.dry_run ? "Preview" : "Applied"}. {r.validation_note || ""} {r.meta_error || ""}
              </p>
            ))}
          </div>
        </Card>
      )}
      {data.digest && (
        <p className="ax-meta">
          {data.digest.attendance?.attended != null
            ? `Edge webinar: ${data.digest.attendance.attended} attended.`
            : "Edge webinar attendance is not recorded yet."}
        </p>
      )}

      {open && (
        <Drawer title={open.name} onClose={() => setOpenKey(null)}>
          <div className="ax-stack" style={{ gap: 4 }}>
            <span className="ax-verdict" data-v={open.verdict}>{VERDICT[open.verdict] || open.verdict}</span>
            <p className="ax-text" style={{ color: C.ink }}>{open.reason}</p>
            <p className="ax-meta">{placement(open) || open.group}</p>
          </div>
          <dl className="ax-dl">
            <div><dt>Spend</dt><dd>{usd(open.spend)}</dd></div>
            <div><dt>Leads</dt><dd>{num(open.leads)}</dd></div>
            <div><dt>Seats</dt><dd>{open.seats == null ? "—" : num(open.seats)}</dd></div>
            <div><dt>Status</dt><dd style={{ fontFamily: "var(--font-text)" }}>{sentenceStatus(open.status) || "—"}</dd></div>
          </dl>
          {sameNameKept.length > 0 && (
            <p className="ax-text">
              Another row uses this same name as Keep
              {sameNameKept.map((k) => ` at ${usd(k.spend)} (${k.metric === "delivery" ? "below the minimum" : "inside the bands"})`).join("; ")}.
              This one is {VERDICT[open.verdict]} at {usd(open.spend)}.
            </p>
          )}
          <div className="ax-row">
            <Btn disabled={busy || !open.action} onClick={() => apply([open], true)}>Preview</Btn>
          </div>
        </Drawer>
      )}
    </div>
  );
}

/* This week, Monday to Sunday in the ad account's timezone. Each brand is measured against its
   plan through the end of today, not the whole week's: dividing by the full week made every
   brand read "behind" until Sunday. The bar still shows the whole week, and the dark mark is
   where today ends, so a bar past the mark is ahead. */
function WeekPace({ data, onBudget }) {
  const wk = weekPace(data.week);
  if (!data.week?.has_plan && wk.brands.length === 0) {
    return (
      <Card title="This week’s pace">
        <div className="ax-spread">
          <p className="ax-text">No weekly plan yet.</p>
          <Btn onClick={onBudget}>Set this week&apos;s plan</Btn>
        </div>
      </Card>
    );
  }
  return (
    <Card title="This week’s pace" meta={wk.day ? `${wk.dayName}, day ${wk.day} of 7` : null}>
      <div className="ax-pace">
        {wk.brands.map((b) => {
          const t = tone(b.status, true);
          const through = b.pace == null ? "" : `; ${Math.round(b.pace)} percent of the plan through today`;
          return (
            <div key={b.name}>
              <div className="ax-pace__head">
                <span>{b.name}</span>
                <span className="ax-pace__fig">
                  <span className="ax-pace__word" style={{ color: t.ink }}>{PACE_WORD[b.status]}</span>
                  <span className="ax-num ax-meta">{usd(b.spend)} of {usd(b.plan)}</span>
                </span>
              </div>
              <div className="ax-bar" role="img"
                   aria-label={`${b.name}: ${usd(b.spend)} spent of a ${usd(b.plan)} week${through}`}>
                {b.spend > 0 && <i style={{ width: `${Math.max(2, b.fill)}%`, background: t.bar }} />}
                {wk.share != null && <b style={{ left: `${wk.share * 100}%` }} />}
              </div>
            </div>
          );
        })}
      </div>
      <p className="ax-meta" style={{ marginTop: "var(--ax-4)" }}>
        The dark mark is the plan through the end of today. A bar past it is ahead.
      </p>
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
    <>
      <div className="ax-dec">
        <button type="button" className="ax-dec__head" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
          <Glyph name="chevron" />
          <span className="ax-verdict" data-v={head.verdict}>{VERDICT[head.verdict]}</span>
          <span className="ax-dec__name">
            {head.name.replace(/\s*[-–—]?\s*ResultsH\d+/i, "")}
            <span className="ax-dec__group">{bucket.rows.length} variants</span>
          </span>
          <span className="ax-dec__more">{open ? "Hide" : "Show"}</span>
        </button>
      </div>
      {open && bucket.rows.map((item) => (
        <DecisionRow key={`${item.level}:${item.external_id}`} item={item} picked={picked} setPicked={setPicked}
                     keeps={keeps} selected={openKey === `${item.level}:${item.external_id}`}
                     onOpen={() => setOpenKey(`${item.level}:${item.external_id}`)} nested />
      ))}
    </>
  );
}

function DecisionRow({ item, picked, setPicked, keeps, selected, onOpen, nested }) {
  const key = `${item.level}:${item.external_id}`;
  const twin = keeps.find((k) => k.name === item.name);
  return (
    <div className={`ax-dec${nested ? " ax-dec--nested" : ""}${selected ? " is-on" : ""}`}>
      <label className="ax-check">
        <input type="checkbox" checked={!!picked[key]} aria-label={`Select ${item.name}`} disabled={!item.action}
               onClick={(e) => e.stopPropagation()}
               onChange={(e) => setPicked((p) => ({ ...p, [key]: e.target.checked }))} />
      </label>
      <RowBody item={item} keeps={twin ? [twin] : []} selected={selected} onOpen={onOpen} nested={nested} />
    </div>
  );
}

function RowBody({ item, keeps, onOpen, nested }) {
  const twin = (keeps || []).find((k) => k.name === item.name && k.verdict === "keep");
  return (
    <button type="button" className="ax-dec__open" onClick={onOpen}>
      <span className="ax-verdict" data-v={item.verdict}>
        {nested ? "" : (VERDICT[item.verdict] || item.verdict)}
      </span>
      <span>
        <span className="ax-dec__name">{item.name}</span>
        <span className="ax-dec__group">{item.group}</span>
        <span className="ax-dec__line" style={{ display: "block" }}>
          {placement(item) || "Campaign not on this row"}
        </span>
        {twin && (
          <span className="ax-dec__line" style={{ display: "block" }}>
            {VERDICT[item.verdict]} at {usd(item.spend)}. A Keep row with this name is {usd(twin.spend)}.
          </span>
        )}
        <span className="ax-dec__line" style={{ display: "block" }}>{item.reason}</span>
      </span>
    </button>
  );
}
