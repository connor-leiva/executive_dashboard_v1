import { useEffect } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";

import { viewingAs } from "../api.js";

/* WIN THE DAY (WHOS-WHO-WIN-THE-DAY-SPEC.md, phase 3).
 *
 * PORTED, NOT REDRAWN. Every element below is the mockup's (`brand-src/mockups/utah-life-intranet/
 * template.html`, lines 1139-1429), with its inline styles moved into `ut-wtd-*` classes value for
 * value and its colours moved onto the portal's tokens -- so Utah Life sees the mockup, and a
 * workspace with its own palette sees the same page in its own colours.
 *
 * THE CONTENT IS THE WORKSPACE'S. What used to be a compiled-in checklist (`WTD_BLOCKS`) is the
 * playbook its console writes, handed over resolved: counts filled in, lists numbered, grouped and
 * linked, and this person's targets worked out on the server (services/wtd_playbook).
 *
 * THE DAY IS THE AGENT'S. Blocks done, lists worked, minutes on a Top Down list and the tallies
 * live in their own `wtd` state, one per local date, as the checklist's did. */

export const EMPTY_DAY = { v: 2, blocks: {}, lists: {}, minutes: {}, tally: {} };

const TAB_PATH = { run: "/wtd", lists: "/wtd/lists", call: "/wtd/call", scripts: "/wtd/scripts",
                   numbers: "/wtd/numbers", tools: "/wtd/tools" };
const KIND_CLASS = { clear: "clear", top_down: "top-down", scan: "scan" };

// The sheet's date, in the viewer's own calendar: "Monday, August 17" on the day it is.
function sheetDate() {
  return new Date().toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" });
}

function day(state) {
  const s = state || {};
  return {
    blocks: s.blocks || {}, lists: s.lists || {}, minutes: s.minutes || {}, tally: s.tally || {},
  };
}

function Out({ href, className, children }) {
  return href
    ? <a className={className} href={href} target="_blank" rel="noreferrer noopener">{children}</a>
    : <span className={className}>{children}</span>;
}

// ── the callouts ──────────────────────────────────────────────────────────────────────────

/* Trips People Up: one item reads as a note, two or more as a row of them -- the mockup draws
   Today's Run's single trip and the lists tab's pair differently. */
function Trips({ items, className = "" }) {
  if (!items?.length) return null;
  const single = items.length === 1;
  return (
    <div className={`ut-wtd-trips ${single ? "single" : ""} ${className}`}>
      <div className="ut-wtd-trips-eyebrow">{"⚠ Trips People Up"}</div>
      {single ? (
        <>
          <div className="ut-wtd-trip-title">{items[0].title}</div>
          <div className="ut-wtd-trip-text">{items[0].text}</div>
        </>
      ) : (
        <div className="ut-wtd-trip-grid">
          {items.map((t) => (
            <div key={t.title}>
              <div className="ut-wtd-trip-title">{t.title}</div>
              <div className="ut-wtd-trip-text">{t.text}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function Weekly({ weekly }) {
  if (!weekly?.items?.length) return null;
  return (
    <div className="ut-wtd-weekly">
      {weekly.eyebrow ? <div className="ut-wtd-note-eyebrow">{weekly.eyebrow}</div> : null}
      <div className="ut-wtd-weekly-grid">
        {weekly.items.map((item) => (
          <div key={item.title}>
            <div className="ut-wtd-weekly-title">{item.title}</div>
            <div className="ut-wtd-weekly-text">{item.text}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

function DarkCard({ eyebrow, text, children, className = "" }) {
  return (
    <div className={`ut-wtd-dark ${className}`}>
      {eyebrow ? <div className="ut-wtd-dark-eyebrow">{eyebrow}</div> : null}
      {text ? <p>{text}</p> : null}
      {children}
    </div>
  );
}

// ── Today's Run ───────────────────────────────────────────────────────────────────────────

function Kinds({ kinds }) {
  return (
    <div className="ut-wtd-kinds">
      {kinds.map((k) => (
        <div className="ut-wtd-kind-card" key={k.key}>
          <div className={`ut-wtd-kind-head ${KIND_CLASS[k.key]}`}>
            <span className="ut-wtd-kind-glyph">{k.glyph}</span>
            <span>{k.label}</span>
          </div>
          <div className="ut-wtd-kind-text">{k.text}</div>
        </div>
      ))}
    </div>
  );
}

function Run({ wtd, today, change, readOnly }) {
  const { run, sheet } = wtd;
  const blocks = run.blocks;
  const hit = blocks.filter((b) => today.blocks[b.key]).length;
  const left = blocks.length - hit;
  const tallies = sheet.tallies;

  const bump = (key, by) => change((d) => ({
    ...d, tally: { ...d.tally, [key]: Math.max(0, (Number(d.tally[key]) || 0) + by) },
  }));
  const toggle = (key) => change((d) => ({ ...d, blocks: { ...d.blocks, [key]: !d.blocks[key] } }));

  return (
    <>
      {tallies.length || blocks.length ? (
        <div className="ut-wtd-sheet">
          <div className="ut-wtd-sheet-head">
            <span className="ut-wtd-label">Today&apos;s sheet</span>
            <span className="ut-wtd-sheet-date">{sheetDate()}</span>
            {blocks.length ? (
              <span className={`ut-wtd-won ${left === 0 ? "won" : ""}`}>
                {left === 0 ? "Today is won. Check the box." : `${left} ${left === 1 ? "block" : "blocks"} left`}
              </span>
            ) : null}
          </div>
          {tallies.length ? (
            <div className="ut-wtd-tallies">
              {tallies.map((t) => {
                const value = Number(today.tally[t.key]) || 0;
                const hitGoal = t.goal !== null && t.goal !== undefined ? value >= t.goal : value > 0;
                return (
                  <div className="ut-wtd-tally" key={t.key}>
                    <div className="ut-wtd-label-sm">{t.label}</div>
                    <div className="ut-wtd-tally-row">
                      <button type="button" className="ut-wtd-step" aria-label={`One fewer: ${t.label}`}
                              aria-disabled={readOnly || undefined} onClick={() => bump(t.key, -1)}>{"−"}</button>
                      <span className={`ut-wtd-count ${hitGoal ? "hit" : ""}`}>{value}</span>
                      <button type="button" className="ut-wtd-step" aria-label={`One more: ${t.label}`}
                              aria-disabled={readOnly || undefined} onClick={() => bump(t.key, 1)}>+</button>
                      <span className="ut-wtd-goal">{t.goal !== null && t.goal !== undefined ? `/ ${t.goal}` : ""}</span>
                    </div>
                  </div>
                );
              })}
            </div>
          ) : null}
        </div>
      ) : null}

      {blocks.length ? (
        <>
          <div className="ut-wtd-h2row">
            <h2>{run.title}</h2>
            <span className="ut-wtd-done">{`${hit} of ${blocks.length} blocks done today`}</span>
          </div>
          {run.intro ? <p className="ut-wtd-intro narrow">{run.intro}</p> : null}
          <div className="ut-wtd-blocks">
            {blocks.map((b, i) => {
              const on = Boolean(today.blocks[b.key]);
              return (
                <button type="button" key={b.key} className={`ut-wtd-block ${on ? "done" : ""}`}
                        aria-pressed={on} aria-disabled={readOnly || undefined} onClick={() => toggle(b.key)}>
                  <span className="ut-wtd-block-mark">{on ? "✓" : i + 1}</span>
                  <span className="ut-wtd-block-body">
                    <span className="ut-wtd-block-head">
                      <span className="ut-wtd-block-title">{b.title}</span>
                      {b.minutes ? <span className="ut-wtd-chip">{`${b.minutes} min`}</span> : null}
                    </span>
                    {b.text ? <span className="ut-wtd-block-text">{b.text}</span> : null}
                    {/* Always there, like the mockup's: a block that covers no lists keeps the
                        row's 13px, which is what makes every card the same height below its text. */}
                    <span className="ut-wtd-block-lists">
                      {b.lists.length ? <span className="ut-wtd-label-sm">Lists</span> : null}
                      {b.lists.map((n) => <span className="ut-wtd-listno" key={n}>{n}</span>)}
                    </span>
                  </span>
                </button>
              );
            })}
          </div>
        </>
      ) : null}

      {wtd.lists.items.length ? <Kinds kinds={wtd.kinds} /> : null}
      <Trips items={run.trips} className="run" />
      <Weekly weekly={run.weekly} />
    </>
  );
}

// ── the lists ─────────────────────────────────────────────────────────────────────────────

function ListCard({ item, kind, today, change, readOnly }) {
  const on = Boolean(today.lists[item.id]);
  const minutes = today.minutes[item.id] ?? "";
  const toggle = () => change((d) => ({ ...d, lists: { ...d.lists, [item.id]: !d.lists[item.id] } }));
  const setMinutes = (value) => change((d) => ({
    ...d, minutes: { ...d.minutes, [item.id]: value.replace(/[^0-9]/g, "").slice(0, 3) },
  }));
  return (
    <div id={`list-${item.no}`} className={`ut-wtd-list ${on ? "done" : ""}`}>
      <button type="button" className="ut-wtd-check" aria-pressed={on}
              aria-label={`${item.name}: worked today`} aria-disabled={readOnly || undefined}
              onClick={toggle}>{on ? "✓" : ""}</button>
      <Out href={item.url} className="ut-wtd-no">{item.no}</Out>
      <div className="ut-wtd-list-body">
        <div className="ut-wtd-list-head">
          <Out href={item.url} className="ut-wtd-list-name">{item.url ? `${item.name} ↗` : item.name}</Out>
          {item.cadence ? <span className="ut-wtd-cadence">{item.cadence}</span> : null}
          {kind ? (
            <span className={`ut-wtd-kind ${KIND_CLASS[item.kind]}`}>
              <span className="ut-wtd-kind-g">{kind.glyph}</span>{kind.label}
            </span>
          ) : null}
          {item.kind === "top_down" ? (
            <span className="ut-wtd-min">
              <input value={minutes} placeholder="__" inputMode="numeric" readOnly={readOnly}
                     aria-label={`Minutes on ${item.name}`} onChange={(e) => setMinutes(e.target.value)} />min
            </span>
          ) : null}
        </div>
        {item.description ? <div className="ut-wtd-list-text">{item.description}</div> : null}
        {item.scripts.length ? (
          <div className="ut-wtd-list-scripts">
            <span className="ut-wtd-label-sm">Scripts</span>
            {item.scripts.map((sc) => (
              <Out key={sc.name} href={sc.url} className="ut-wtd-script-chip">
                {sc.url ? `${sc.name} ↗` : sc.name}
              </Out>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function Lists({ wtd, today, change, readOnly }) {
  const { lists } = wtd;
  const items = lists.items;
  const kinds = Object.fromEntries(wtd.kinds.map((k) => [k.key, k]));
  const hit = items.filter((it) => today.lists[it.id]).length;
  const pct = items.length ? Math.round((hit / items.length) * 100) : 0;
  return (
    <>
      {lists.title ? <h2 className="ut-wtd-h2">{lists.title}</h2> : null}
      {lists.intro ? <p className="ut-wtd-intro wide">{lists.intro}</p> : null}
      <div className="ut-wtd-progress">
        <span className="ut-wtd-label">Today</span>
        <span className="ut-wtd-bar"><span style={{ width: `${pct}%` }} /></span>
        <span className="ut-wtd-done">{`${hit} of ${items.length} worked`}</span>
      </div>
      <div className="ut-wtd-groups">
        {lists.groups.map((g) => {
          const mine = items.filter((it) => it.group_key === g.key);
          if (!mine.length) return null;
          return (
            <div key={g.key || "ungrouped"}>
              {g.label ? (
                <div className="ut-wtd-grouphead">
                  <h3>{g.label}</h3>
                  <span />
                </div>
              ) : null}
              <div className={`ut-wtd-listcol ${g.label ? "" : "flush"}`}>
                {mine.map((it) => (
                  <ListCard key={it.id} item={it} kind={kinds[it.kind]} today={today} change={change}
                            readOnly={readOnly} />
                ))}
              </div>
            </div>
          );
        })}
      </div>
      <Trips items={lists.trips} className="lists" />
      {lists.ponds ? (
        <DarkCard eyebrow={lists.ponds.eyebrow} text={lists.ponds.text}
                  className={lists.trips.length ? "after-trips" : "after-lists"}>
          {lists.ponds.links.length ? (
            <div className="ut-wtd-ponds">
              {lists.ponds.links.map((p) => (
                <Out key={p.label} href={p.url} className="ut-wtd-pond">{p.url ? `${p.label} ↗` : p.label}</Out>
              ))}
            </div>
          ) : null}
        </DarkCard>
      ) : null}
    </>
  );
}

// ── The Call ──────────────────────────────────────────────────────────────────────────────

function Call({ wtd }) {
  const { call } = wtd;
  return (
    <>
      {call.title ? <h2 className="ut-wtd-h2">{call.title}</h2> : null}
      {call.intro ? <p className="ut-wtd-intro call">{call.intro}</p> : null}
      {call.steps.length ? (
        <div className="ut-wtd-steps">
          {call.steps.map((step, i) => (
            <div className="ut-wtd-step-row" key={`${i}-${step.title}`}>
              <span className="ut-wtd-step-no">{i + 1}</span>
              <div>
                <div className="ut-wtd-step-title">{step.title}</div>
                {step.text ? <div className="ut-wtd-step-text">{step.text}</div> : null}
              </div>
            </div>
          ))}
        </div>
      ) : null}
      {call.compliance?.text ? (
        <div className="ut-wtd-compliance">
          {call.compliance.eyebrow ? <div className="ut-wtd-note-eyebrow">{call.compliance.eyebrow}</div> : null}
          <p>{call.compliance.text}</p>
        </div>
      ) : null}
      {call.habits.length ? (
        <>
          {call.habits_title ? <h3 className="ut-wtd-h3">{call.habits_title}</h3> : null}
          <div className="ut-wtd-habits">
            {call.habits.map((h) => (
              <div className="ut-wtd-habit" key={h.title}>
                <div className="ut-wtd-habit-title">{h.title}</div>
                {h.text ? <div className="ut-wtd-habit-text">{h.text}</div> : null}
              </div>
            ))}
          </div>
        </>
      ) : null}
    </>
  );
}

// ── Scripts ───────────────────────────────────────────────────────────────────────────────

function Scripts({ wtd }) {
  const { scripts } = wtd;
  return (
    <>
      <div className="ut-wtd-h2row scripts">
        {scripts.title ? <h2>{scripts.title}</h2> : <span />}
        {scripts.library?.url ? (
          <a className="ut-wtd-library" href={scripts.library.url} target="_blank" rel="noreferrer noopener">
            {`${scripts.library.label || "Open the script library"} ↗`}
          </a>
        ) : null}
      </div>
      {scripts.intro ? <p className="ut-wtd-intro narrow">{scripts.intro}</p> : null}
      {scripts.groups.length ? (
        <div className="ut-wtd-script-groups">
          {scripts.groups.map((g) => (
            <div key={g.key}>
              <div className="ut-wtd-grouphead">
                <h3>{g.label}</h3>
                {g.sub ? <span className="ut-wtd-group-sub">{g.sub}</span> : null}
                <span />
              </div>
              <div className="ut-wtd-script-grid">
                {g.items.map((s) => (
                  <Out key={s.id} href={s.url} className="ut-wtd-script-card">
                    <span className="ut-wtd-script-name">{s.url ? `${s.name} ↗` : s.name}</span>
                    {s.description ? <span className="ut-wtd-script-text">{s.description}</span> : null}
                  </Out>
                ))}
              </div>
            </div>
          ))}
        </div>
      ) : null}
      {scripts.fallback?.text ? (
        <DarkCard eyebrow={scripts.fallback.eyebrow} text={scripts.fallback.text} className="fallback" />
      ) : null}
    </>
  );
}

// ── The Numbers ───────────────────────────────────────────────────────────────────────────

function Numbers({ wtd }) {
  const { numbers, sheet } = wtd;
  const shortOf = Object.fromEntries(sheet.tallies.map((t) => [t.key, t.short]));
  const onramp = numbers.onramp || { phases: [] };
  return (
    <>
      {numbers.title ? <h2 className="ut-wtd-h2">{numbers.title}</h2> : null}
      {numbers.intro ? <p className="ut-wtd-intro narrow">{numbers.intro}</p> : null}
      {numbers.rows.length ? (
        <div className="ut-wtd-board">
          <div className="ut-wtd-board-row head">
            <div>Metric</div><div>Daily Target</div><div>Weekly Target</div>
          </div>
          {numbers.rows.map((r) => (
            <div className="ut-wtd-board-row" key={r.label}>
              <div className={`ut-wtd-metric ${r.highlight ? "hi" : ""}`}>{r.label}</div>
              <div>{r.daily}</div>
              <div>{r.weekly}</div>
            </div>
          ))}
          {numbers.footnote ? <div className="ut-wtd-board-foot">{numbers.footnote}</div> : null}
        </div>
      ) : null}
      {onramp.phases.length ? (
        <>
          {onramp.title ? <h3 className="ut-wtd-h3">{onramp.title}</h3> : null}
          {onramp.intro ? <p className="ut-wtd-intro onramp">{onramp.intro}</p> : null}
          <div className="ut-wtd-onramp">
            {onramp.phases.map((p) => (
              <div className="ut-wtd-phase" key={p.label}>
                <div className="ut-wtd-phase-label">{p.label}</div>
                <div className="ut-wtd-phase-goals">
                  {Object.entries(p.goals || {}).map(([key, value]) => (
                    <div key={key}>
                      <div className="ut-wtd-phase-n">{value}</div>
                      <div className="ut-wtd-phase-k">{shortOf[key] || key}</div>
                    </div>
                  ))}
                </div>
                {p.focus ? <div className="ut-wtd-phase-focus">{p.focus}</div> : null}
              </div>
            ))}
          </div>
        </>
      ) : null}
    </>
  );
}

// ── Tools ─────────────────────────────────────────────────────────────────────────────────

function Tools({ wtd }) {
  const { tools } = wtd;
  return (
    <>
      {tools.title ? <h2 className="ut-wtd-h2">{tools.title}</h2> : null}
      {tools.intro ? <p className="ut-wtd-intro wide">{tools.intro}</p> : null}
      <div className="ut-wtd-tools">
        {tools.items.map((t) => (
          <div className="ut-wtd-tool" key={t.name}>
            {t.block ? <div className="ut-wtd-chip block">{t.block}</div> : null}
            <Out href={t.url} className="ut-wtd-tool-name">{t.url ? `${t.name} ↗` : t.name}</Out>
            {t.tagline ? <div className="ut-wtd-tool-tag">{t.tagline}</div> : null}
            {t.text ? <div className="ut-wtd-tool-text">{t.text}</div> : null}
          </div>
        ))}
      </div>
    </>
  );
}

// ── the page ──────────────────────────────────────────────────────────────────────────────

export default function WinTheDay({ state, setState, config, canConfigure }) {
  const wtd = config?.content?.wtd;
  const { tab } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  // A support view reads somebody's day; it does not keep it for them.
  const readOnly = viewingAs();

  const keys = (wtd?.tabs || []).map((t) => t.key);
  const active = keys.includes(tab) ? tab : (keys.includes("run") ? "run" : keys[0]);

  // A link into a list (the assistant cites "#list-01") lands on it.
  useEffect(() => {
    if (!location.hash) return;
    const el = document.getElementById(location.hash.slice(1));
    if (el) el.scrollIntoView({ block: "center" });
  }, [location.hash, active]);

  if (wtd === null) {
    return (
      <div className="ut-wtd">
        <h1 className="ut-wtd-title">Win the Day</h1>
        <p className="ut-wtd-lede">Your role does not have access to Win the Day.</p>
      </div>
    );
  }
  if (!wtd || !keys.length) {
    return (
      <div className="ut-wtd">
        <h1 className="ut-wtd-title">Win the Day</h1>
        <p className="ut-wtd-lede">
          {canConfigure
            ? "Nothing is here yet. Write the playbook in the console under Win the Day, or import one, then publish it."
            : "Your team has not set up Win the Day yet."}
        </p>
      </div>
    );
  }

  const today = day(state);
  const change = (fn) => {
    if (!readOnly) setState((s) => ({ ...(s || {}), v: 2, ...fn(day(s)) }));
  };
  const header = wtd.header || {};

  return (
    <div className="ut-wtd">
      {header.eyebrow ? <div className="ut-wtd-eyebrow">{header.eyebrow}</div> : null}
      <h1 className="ut-wtd-title">{header.title || "Win the Day"}</h1>
      {header.lede ? <p className="ut-wtd-lede">{header.lede}</p> : null}
      {header.meta?.length || (header.cta?.url && header.cta?.label) ? (
        <div className="ut-wtd-meta">
          {(header.meta || []).map((m) => (
            <div className="ut-wtd-fact" key={m.k}>
              <div className="ut-wtd-label-sm">{m.k}</div>
              <div className="ut-wtd-fact-v">{m.v}</div>
            </div>
          ))}
          {header.cta?.url && header.cta?.label ? (
            <a className="ut-wtd-cta" href={header.cta.url} target="_blank" rel="noreferrer noopener">{header.cta.label}</a>
          ) : null}
        </div>
      ) : null}

      {wtd.rule ? (
        <div className="ut-wtd-rule">
          {wtd.rule.eyebrow ? <div className="ut-wtd-dark-eyebrow">{wtd.rule.eyebrow}</div> : null}
          <p className="ut-wtd-rule-text">{wtd.rule.text}</p>
          {wtd.rule.sub ? <p className="ut-wtd-rule-sub">{wtd.rule.sub}</p> : null}
        </div>
      ) : null}

      <nav className="ut-wtd-tabs" aria-label="Win the Day">
        {wtd.tabs.map((t) => (
          <button key={t.key} type="button" className={t.key === active ? "on" : ""}
                  aria-current={t.key === active ? "page" : undefined}
                  onClick={() => navigate(TAB_PATH[t.key])}>
            {t.label}
          </button>
        ))}
      </nav>

      {active === "run" ? <Run wtd={wtd} today={today} change={change} readOnly={readOnly} /> : null}
      {active === "lists" ? <Lists wtd={wtd} today={today} change={change} readOnly={readOnly} /> : null}
      {active === "call" ? <Call wtd={wtd} /> : null}
      {active === "scripts" ? <Scripts wtd={wtd} /> : null}
      {active === "numbers" ? <Numbers wtd={wtd} /> : null}
      {active === "tools" ? <Tools wtd={wtd} /> : null}
    </div>
  );
}
