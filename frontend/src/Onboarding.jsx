/* Onboarding — a person's first thirty days, and how far through them they are.
 *
 * ONBOARDING-SPEC.md §7. Five views over one plan: the day in front of you, the week around it,
 * the scoreboard it is measured by, the script library and conversation log it is meant to
 * produce, and the standing rules.
 *
 * It arrived as a hand-built HTML page with its own palette, its own type and its own sidebar.
 * None of that came with it. The palette is theme.js tokens, so a workspace that has chosen its
 * own colours gets them here too; the type is --font-display/text/data like every other tab; and
 * the page's left sidebar is gone, because this app already has one and a second rail inside a
 * tab is a second place to be lost in.
 *
 * NOTHING HERE COMPUTES A TOTAL. Every count on screen -- blocks done, outcomes hit, targets met
 * -- arrives from the server, which derives it from the rows on each read. A checkbox patches
 * its own row optimistically so the click lands immediately, and the numbers catch up from the
 * refetch. Recomputing them here would be the second source of truth that goes wrong quietly.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { ribbedHero } from "./Brand.jsx";
import { ProductIcon, iconFor } from "./brand/productIcons.jsx";
import { T, alpha } from "./theme.js";
import { delJSON, patchJSON, postJSON } from "./api.js";

const VIEWS = [
  ["today", "Today"],
  ["week", "Week"],
  ["score", "Scoreboard"],
  ["library", "Scripts & log"],
  ["rules", "Rules"],
];

const HEATS = [["hot", "Hot"], ["warm", "Warm"], ["no", "No"]];

/* ── small shared UI (house idiom) ────────────────────────────────────────── */
function Card({ children, style, ...rest }) {
  return (
    <div {...rest} style={{ background: T.white, border: `1px solid ${T.line}`, borderRadius: 14, ...style }}>
      {children}
    </div>
  );
}

function Btn({ kind = "ghost", children, onClick, disabled, title, style }) {
  const tone = {
    primary: { color: T.onDark, background: T.evergreen, border: `1px solid ${T.evergreen}` },
    ghost: { color: T.slate, background: T.white, border: `1px solid ${T.line}` },
    quiet: { color: T.muted, background: "transparent", border: "1px solid transparent" },
  }[kind];
  return (
    <button type="button" onClick={onClick} disabled={disabled} title={title} className="cc-nav" style={{
      fontFamily: "var(--font-display)", fontSize: 12.5, fontWeight: 600, borderRadius: 8,
      padding: "8px 14px", cursor: disabled ? "default" : "pointer", opacity: disabled ? 0.5 : 1,
      ...tone, ...style,
    }}>{children}</button>
  );
}

function Label({ children, style }) {
  return (
    <div style={{ fontFamily: "var(--font-text)", fontSize: 11, fontWeight: 600,
                  letterSpacing: ".08em", textTransform: "uppercase", color: T.muted, ...style }}>
      {children}
    </div>
  );
}

function Bar({ pct, tone }) {
  return (
    <div style={{ height: 4, borderRadius: 99, background: T.page, overflow: "hidden" }}>
      <div style={{ width: `${Math.min(100, Math.max(0, pct))}%`, height: "100%",
                    background: tone || T.evergreen, borderRadius: 99 }} />
    </div>
  );
}

/* A block's outcome. Three states and the third is NOT "miss" -- a block nobody has judged is
   different from one that went wrong, and the control has to be able to say so. */
function OutcomeToggle({ state, disabled, onPick }) {
  const opts = [["hit", "Hit", T.meadow], ["miss", "Missed", T.poppyText]];
  return (
    <span style={{ display: "inline-flex", gap: 5 }}>
      {opts.map(([key, label, colour]) => {
        const on = state === key;
        return (
          <button key={key} type="button" disabled={disabled}
                  onClick={() => onPick(on ? "" : key)}
                  className="cc-nav"
                  style={{ fontFamily: "var(--font-text)", fontSize: 11, fontWeight: 600,
                           borderRadius: 999, padding: "3px 10px",
                           cursor: disabled ? "default" : "pointer",
                           color: on ? T.onDark : colour, background: on ? colour : "transparent",
                           border: `1px solid ${on ? colour : alpha(colour, 0.45)}` }}>
            {label}
          </button>
        );
      })}
    </span>
  );
}

function Check({ on, disabled, onToggle, label }) {
  return (
    <button type="button" aria-pressed={on} aria-label={label} disabled={disabled}
            onClick={onToggle} className="cc-nav"
            style={{ width: 20, height: 20, flexShrink: 0, marginTop: 1, borderRadius: 6,
                     cursor: disabled ? "default" : "pointer",
                     border: `1.5px solid ${on ? T.evergreen : T.line}`,
                     background: on ? T.evergreen : T.white, color: T.onDark,
                     display: "flex", alignItems: "center", justifyContent: "center",
                     fontSize: 12, lineHeight: 1 }}>
      {on ? "✓" : ""}
    </button>
  );
}

/* ── the day ──────────────────────────────────────────────────────────────── */
function DayView({ plan, day, onBlock, onDebrief, index, count, onStep }) {
  const [draft, setDraft] = useState(day.debrief || "");
  const saved = useRef(day.debrief || "");
  const timer = useRef(null);

  // Re-seed when the day changes, but never while the person is mid-sentence on the same one.
  useEffect(() => { setDraft(day.debrief || ""); saved.current = day.debrief || ""; }, [day.id]);

  // Debounced, because a debrief is a paragraph and one request per keystroke is a paragraph's
  // worth of requests. Flushed on unmount so switching day does not drop the last few words.
  useEffect(() => {
    if (draft === saved.current) return undefined;
    clearTimeout(timer.current);
    timer.current = setTimeout(() => { saved.current = draft; onDebrief(day.id, draft); }, 800);
    return () => clearTimeout(timer.current);
  }, [draft]);
  useEffect(() => () => {
    if (draft !== saved.current) onDebrief(day.id, draft);
  }, []);

  const pct = day.total ? (day.done / day.total) * 100 : 0;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <Card style={{ padding: "16px 18px" }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
          <div style={{ fontFamily: "var(--font-display)", fontSize: 19, fontWeight: 700, color: T.ink }}>
            {day.title}
          </div>
          {day.tag && (
            <span style={{ fontFamily: "var(--font-text)", fontSize: 11, fontWeight: 600,
                           color: T.daffodilText, background: T.daffodilBg,
                           borderRadius: 999, padding: "2px 9px" }}>{day.tag}</span>
          )}
          <span style={{ flex: 1 }} />
          <Btn kind="quiet" disabled={index === 0} onClick={() => onStep(-1)}>‹ Previous</Btn>
          <Btn kind="quiet" disabled={index >= count - 1} onClick={() => onStep(1)}>Next ›</Btn>
        </div>
        <div style={{ fontFamily: "var(--font-text)", fontSize: 12.5, color: T.muted, marginTop: 3 }}>
          {day.location}
          {day.offsite ? " · off site" : ""}
        </div>
        <div style={{ marginTop: 12 }}><Bar pct={pct} /></div>
        <div style={{ fontFamily: "var(--font-data)", fontSize: 11.5, color: T.muted, marginTop: 6,
                      fontVariantNumeric: "tabular-nums" }}>
          {day.done} of {day.total} blocks done · {day.hits} hit · {day.misses} missed
        </div>
      </Card>

      {day.due.length > 0 && (
        <Card style={{ padding: "14px 18px", borderColor: alpha(T.daffodilText, 0.35),
                       background: T.daffodilBg }}>
          <Label style={{ color: T.daffodilText }}>Due today</Label>
          {day.due.map((t) => (
            <div key={t.id} style={{ fontFamily: "var(--font-text)", fontSize: 13, color: T.ink, marginTop: 5 }}>
              {t.label}{t.is_count ? ` — ${t.actual} of ${t.target}` : ""}
            </div>
          ))}
        </Card>
      )}

      <Card>
        {day.blocks.map((b, i) => (
          <div key={b.id} style={{ display: "flex", gap: 13, padding: "13px 18px",
                                   borderTop: i ? `1px solid ${T.line}` : "none" }}>
            <Check on={b.done} disabled={!plan.can_write} label={`Mark ${b.time} done`}
                   onToggle={() => onBlock(b.id, { done: !b.done })} />
            <div style={{ minWidth: 0, flex: 1 }}>
              <div style={{ display: "flex", gap: 10, alignItems: "baseline", flexWrap: "wrap" }}>
                <span style={{ fontFamily: "var(--font-data)", fontSize: 12, fontWeight: 600,
                               color: T.tertiary, fontVariantNumeric: "tabular-nums",
                               minWidth: 78 }}>{b.time}</span>
                <span style={{ fontFamily: "var(--font-text)", fontSize: 13.5, flex: 1,
                               color: b.done ? T.muted : T.ink }}>{b.task}</span>
              </div>
              <div style={{ display: "flex", gap: 10, alignItems: "center", marginTop: 7,
                            flexWrap: "wrap", paddingLeft: 88 }}>
                <span style={{ fontFamily: "var(--font-text)", fontSize: 12, color: T.slate, flex: 1 }}>
                  {b.outcome}
                </span>
                <OutcomeToggle state={b.outcome_state} disabled={!plan.can_write}
                               onPick={(v) => onBlock(b.id, { outcome_state: v })} />
              </div>
            </div>
          </div>
        ))}
      </Card>

      {day.notes.length > 0 && (
        <Card style={{ padding: "15px 18px", background: T.page }}>
          <Label>Notes</Label>
          {day.notes.map((n, i) => (
            <p key={i} style={{ fontFamily: "var(--font-text)", fontSize: 13, lineHeight: 1.6,
                                color: T.slate, margin: "7px 0 0" }}>{n}</p>
          ))}
        </Card>
      )}

      <Card style={{ padding: "15px 18px" }}>
        <Label>End of day</Label>
        <textarea value={draft} disabled={!plan.can_write}
                  onChange={(e) => setDraft(e.target.value)}
                  placeholder="What actually happened, while you still remember it."
                  rows={4} maxLength={8000}
                  style={{ width: "100%", boxSizing: "border-box", marginTop: 8, resize: "vertical",
                           fontFamily: "var(--font-text)", fontSize: 13, lineHeight: 1.6,
                           color: T.ink, background: T.white, borderRadius: 10,
                           border: `1px solid ${T.line}`, padding: "10px 12px" }} />
      </Card>
    </div>
  );
}

/* ── the week ─────────────────────────────────────────────────────────────── */
function WeekView({ plan, week, days, onOpenDay }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <Card style={{ padding: "16px 18px" }}>
        <div style={{ fontFamily: "var(--font-display)", fontSize: 19, fontWeight: 700, color: T.ink }}>
          {week.label}
          <span style={{ fontWeight: 400, color: T.muted }}> · {week.date_range}</span>
          {week.subtitle && <span style={{ fontWeight: 400, color: T.muted }}> · {week.subtitle}</span>}
        </div>
        {week.intro.map((p, i) => (
          <p key={i} style={{ fontFamily: "var(--font-text)", fontSize: 13.5, lineHeight: 1.6,
                              color: T.slate, margin: "9px 0 0" }}>{p}</p>
        ))}
        <div style={{ marginTop: 13 }}>
          <Bar pct={week.total ? (week.done / week.total) * 100 : 0} />
        </div>
        <div style={{ fontFamily: "var(--font-data)", fontSize: 11.5, color: T.muted, marginTop: 6,
                      fontVariantNumeric: "tabular-nums" }}>
          {week.done} of {week.total} blocks done · {week.hits} hit · {week.misses} missed
        </div>
      </Card>

      {days.map((d) => (
        <Card key={d.id} style={{ padding: "14px 18px", cursor: "pointer" }} onClick={() => onOpenDay(d.id)}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
            <span style={{ fontFamily: "var(--font-display)", fontSize: 14.5, fontWeight: 600, color: T.ink }}>
              {d.title}
            </span>
            {d.tag && (
              <span style={{ fontFamily: "var(--font-text)", fontSize: 10.5, fontWeight: 600,
                             color: T.daffodilText }}>{d.tag}</span>
            )}
            <span style={{ flex: 1 }} />
            <span style={{ fontFamily: "var(--font-data)", fontSize: 11.5, color: T.muted,
                           fontVariantNumeric: "tabular-nums" }}>
              {d.done}/{d.total}
              {d.misses ? ` · ${d.misses} missed` : ""}
            </span>
          </div>
          <div style={{ marginTop: 9 }}>
            <Bar pct={d.total ? (d.done / d.total) * 100 : 0}
                 tone={d.misses ? T.poppyText : d.done === d.total && d.total ? T.meadow : T.evergreen} />
          </div>
        </Card>
      ))}

      {week.outro.length > 0 && (
        <Card style={{ padding: "15px 18px", background: T.page }}>
          {week.outro.map((p, i) => (
            <p key={i} style={{ fontFamily: "var(--font-text)", fontSize: 13, lineHeight: 1.6,
                                color: T.slate, margin: i ? "9px 0 0" : 0 }}>{p}</p>
          ))}
        </Card>
      )}

      {week.show_rules && <RulesCard plan={plan} />}
    </div>
  );
}

/* ── the scoreboard ───────────────────────────────────────────────────────── */
function ScoreView({ plan, onTarget }) {
  const groups = useMemo(() => {
    const out = [];
    plan.targets.forEach((t) => {
      let g = out.find((x) => x.due_on === t.due_on);
      if (!g) out.push(g = { due_on: t.due_on, relative: t.relative, items: [] });
      g.items.push(t);
    });
    return out;
  }, [plan.targets]);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <Card style={{ padding: "16px 18px" }}>
        <div style={{ fontFamily: "var(--font-display)", fontSize: 19, fontWeight: 700, color: T.ink }}>
          {plan.totals.targets_met} of {plan.totals.targets} met
        </div>
        <div style={{ fontFamily: "var(--font-text)", fontSize: 12.5, color: T.muted, marginTop: 3 }}>
          {plan.totals.targets_missed > 0
            ? `${plan.totals.targets_missed} missed — bring them to the review rather than leaving them to be found`
            : "Nothing missed so far."}
        </div>
      </Card>

      {groups.map((g) => (
        <div key={g.due_on} style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <Label>{fmtDate(g.due_on)} · {g.relative === "passed" ? "date passed" : `due ${g.relative}`}</Label>
          {g.items.map((t) => (
            <Card key={t.id} style={{ padding: "13px 18px" }}>
              <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
                <span style={{ fontFamily: "var(--font-text)", fontSize: 13.5, color: T.ink, flex: 1, minWidth: 180 }}>
                  {t.label}
                </span>
                {t.is_count ? (
                  <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
                    <Btn kind="ghost" disabled={!plan.can_write || t.actual === 0}
                         onClick={() => onTarget(t.id, { actual: Math.max(0, t.actual - 1) })}
                         style={{ padding: "4px 10px" }}>−</Btn>
                    <span style={{ fontFamily: "var(--font-data)", fontSize: 14, fontWeight: 700,
                                   color: t.met ? T.meadow : t.missed ? T.poppyText : T.ink,
                                   fontVariantNumeric: "tabular-nums", minWidth: 52, textAlign: "center" }}>
                      {t.actual} / {t.target}
                    </span>
                    <Btn kind="ghost" disabled={!plan.can_write}
                         onClick={() => onTarget(t.id, { actual: t.actual + 1 })}
                         style={{ padding: "4px 10px" }}>+</Btn>
                  </span>
                ) : (
                  <Btn kind={t.done ? "primary" : "ghost"} disabled={!plan.can_write}
                       onClick={() => onTarget(t.id, { done: !t.done })}>
                    {t.done ? "✓ Complete" : "Mark complete"}
                  </Btn>
                )}
              </div>
              {t.is_count && (
                <div style={{ marginTop: 10 }}>
                  <Bar pct={(t.actual / t.target) * 100}
                       tone={t.met ? T.meadow : t.missed ? T.poppyText : T.evergreen} />
                </div>
              )}
            </Card>
          ))}
        </div>
      ))}
    </div>
  );
}

/* ── scripts and the conversation log ─────────────────────────────────────── */
function LibraryView({ plan, onAddScript, onDeleteScript, onAddConvo, onPatchConvo, onDeleteConvo }) {
  const [motion, setMotion] = useState(plan.motions[0] || "");
  const [text, setText] = useState("");
  const [convo, setConvo] = useState({ name: "", team: "", context: "", next_step: "" });

  const submitScript = () => {
    if (!text.trim()) return;
    onAddScript({ motion, text: text.trim() });
    setText("");
  };
  const submitConvo = () => {
    if (!convo.name.trim() && !convo.context.trim()) return;
    onAddConvo(convo);
    setConvo({ name: "", team: "", context: "", next_step: "" });
  };

  const field = {
    width: "100%", boxSizing: "border-box", fontFamily: "var(--font-text)", fontSize: 13,
    color: T.ink, background: T.white, border: `1px solid ${T.line}`, borderRadius: 9,
    padding: "9px 11px",
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      <div>
        <Label>Scripts · {plan.totals.scripts} saved</Label>
        <Card style={{ padding: "15px 18px", marginTop: 8 }}>
          <div style={{ display: "flex", gap: 7, flexWrap: "wrap" }}>
            {plan.motions.map((m) => (
              <Btn key={m} kind={m === motion ? "primary" : "ghost"} onClick={() => setMotion(m)}>{m}</Btn>
            ))}
          </div>
          <textarea value={text} disabled={!plan.can_write} rows={3} maxLength={8000}
                    onChange={(e) => setText(e.target.value)}
                    placeholder="Their exact words, not your paraphrase of them."
                    style={{ ...field, marginTop: 11, resize: "vertical", lineHeight: 1.6 }} />
          <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 9 }}>
            <Btn kind="primary" disabled={!plan.can_write || !text.trim()} onClick={submitScript}>
              Save to {motion}
            </Btn>
          </div>
        </Card>

        <div style={{ display: "grid", gap: 12, marginTop: 12,
                      gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))" }}>
          {plan.motions.map((m) => {
            const items = plan.scripts.filter((x) => x.motion === m);
            return (
              <Card key={m} style={{ padding: "13px 15px", minWidth: 0 }}>
                <Label>{m} · {items.length}</Label>
                {items.length === 0 && (
                  <div style={{ fontFamily: "var(--font-text)", fontSize: 12.5, color: T.muted, marginTop: 8 }}>
                    Nothing saved yet.
                  </div>
                )}
                {items.map((x) => (
                  <div key={x.id} style={{ marginTop: 10, paddingTop: 10,
                                           borderTop: `1px solid ${T.line}` }}>
                    <div style={{ fontFamily: "var(--font-text)", fontSize: 13, lineHeight: 1.6,
                                  color: T.ink, whiteSpace: "pre-wrap" }}>{x.text}</div>
                    <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 6 }}>
                      <span style={{ fontFamily: "var(--font-data)", fontSize: 11, color: T.muted }}>
                        {fmtDate(x.created_at)}
                      </span>
                      <span style={{ flex: 1 }} />
                      {plan.can_write && (
                        <Btn kind="quiet" onClick={() => onDeleteScript(x.id)} style={{ padding: "3px 8px" }}>
                          Remove
                        </Btn>
                      )}
                    </div>
                  </div>
                ))}
              </Card>
            );
          })}
        </div>
      </div>

      <div>
        <Label>
          Conversations · {plan.totals.conversations} logged · {plan.totals.appointments} appointments set
        </Label>
        <Card style={{ padding: "15px 18px", marginTop: 8 }}>
          <div style={{ display: "grid", gap: 9, gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))" }}>
            <input style={field} placeholder="Name" maxLength={160} value={convo.name}
                   disabled={!plan.can_write}
                   onChange={(e) => setConvo({ ...convo, name: e.target.value })} />
            <input style={field} placeholder="Team or brokerage" maxLength={160} value={convo.team}
                   disabled={!plan.can_write}
                   onChange={(e) => setConvo({ ...convo, team: e.target.value })} />
          </div>
          <input style={{ ...field, marginTop: 9 }} placeholder="What was said" maxLength={4000}
                 value={convo.context} disabled={!plan.can_write}
                 onChange={(e) => setConvo({ ...convo, context: e.target.value })} />
          <input style={{ ...field, marginTop: 9 }} placeholder="Next step" maxLength={4000}
                 value={convo.next_step} disabled={!plan.can_write}
                 onChange={(e) => setConvo({ ...convo, next_step: e.target.value })} />
          <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 9 }}>
            <Btn kind="primary" disabled={!plan.can_write} onClick={submitConvo}>Log it</Btn>
          </div>
        </Card>

        <div style={{ display: "flex", flexDirection: "column", gap: 10, marginTop: 12 }}>
          {plan.conversations.length === 0 && (
            <Card style={{ padding: "15px 18px", fontFamily: "var(--font-text)", fontSize: 13, color: T.muted }}>
              Nothing logged yet. The plan's own instruction is to write it down the same day —
              by Monday the names blur.
            </Card>
          )}
          {plan.conversations.map((c) => (
            <Card key={c.id} style={{ padding: "13px 18px" }}>
              <div style={{ display: "flex", gap: 10, alignItems: "baseline", flexWrap: "wrap" }}>
                <span style={{ fontFamily: "var(--font-display)", fontSize: 14, fontWeight: 600, color: T.ink }}>
                  {c.name || "Unnamed"}
                </span>
                {c.team && (
                  <span style={{ fontFamily: "var(--font-text)", fontSize: 12.5, color: T.muted }}>{c.team}</span>
                )}
                <span style={{ flex: 1 }} />
                <span style={{ fontFamily: "var(--font-data)", fontSize: 11, color: T.muted }}>
                  {fmtDate(c.created_at)}
                </span>
              </div>
              {c.context && (
                <div style={{ fontFamily: "var(--font-text)", fontSize: 13, color: T.slate, marginTop: 6 }}>
                  {c.context}
                </div>
              )}
              <div style={{ fontFamily: "var(--font-text)", fontSize: 12.5, color: T.tertiary, marginTop: 4 }}>
                Next: {c.next_step || "—"}
              </div>
              <div style={{ display: "flex", gap: 6, alignItems: "center", marginTop: 10, flexWrap: "wrap" }}>
                {HEATS.map(([key, label]) => {
                  const on = c.heat === key;
                  const colour = { hot: T.poppyText, warm: T.daffodilText, no: T.muted }[key];
                  return (
                    <button key={key} type="button" disabled={!plan.can_write} className="cc-nav"
                            onClick={() => onPatchConvo(c.id, { heat: on ? "" : key })}
                            style={{ fontFamily: "var(--font-text)", fontSize: 11, fontWeight: 600,
                                     borderRadius: 999, padding: "3px 11px",
                                     cursor: plan.can_write ? "pointer" : "default",
                                     color: on ? T.onDark : colour,
                                     background: on ? colour : "transparent",
                                     border: `1px solid ${on ? colour : alpha(colour, 0.4)}` }}>
                      {label}
                    </button>
                  );
                })}
                <Btn kind={c.appointment_set ? "primary" : "ghost"} disabled={!plan.can_write}
                     onClick={() => onPatchConvo(c.id, { appointment_set: !c.appointment_set })}
                     style={{ padding: "4px 11px", fontSize: 11 }}>
                  {c.appointment_set ? "✓ Appointment set" : "Appointment set?"}
                </Btn>
                <span style={{ flex: 1 }} />
                {plan.can_write && (
                  <Btn kind="quiet" onClick={() => onDeleteConvo(c.id)} style={{ padding: "3px 8px" }}>
                    Remove
                  </Btn>
                )}
              </div>
            </Card>
          ))}
        </div>
      </div>
    </div>
  );
}

/* ── the standing rules ───────────────────────────────────────────────────── */
function RulesCard({ plan }) {
  if (!plan.rules.length) return null;
  return (
    <Card style={{ padding: "15px 18px" }}>
      <Label>On the floor</Label>
      {plan.rules.map((r, i) => (
        <div key={i} style={{ marginTop: 12 }}>
          <div style={{ fontFamily: "var(--font-display)", fontSize: 14, fontWeight: 600, color: T.ink }}>
            {r.h}
          </div>
          <div style={{ fontFamily: "var(--font-text)", fontSize: 13, lineHeight: 1.6,
                        color: T.slate, marginTop: 3 }}>{r.p}</div>
        </div>
      ))}
    </Card>
  );
}

function RulesView({ plan }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <RulesCard plan={plan} />
      <Card style={{ padding: "15px 18px" }}>
        <Label>The four motions</Label>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 10 }}>
          {plan.motions.map((m) => (
            <span key={m} style={{ fontFamily: "var(--font-text)", fontSize: 12.5, fontWeight: 600,
                                   color: T.tertiary, background: T.page, borderRadius: 999,
                                   padding: "5px 13px" }}>{m}</span>
          ))}
        </div>
        <div style={{ fontFamily: "var(--font-text)", fontSize: 12.5, color: T.muted, marginTop: 11 }}>
          Every script you save is filed under one of these.
        </div>
      </Card>
    </div>
  );
}

/* ── helpers ──────────────────────────────────────────────────────────────── */
function fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(iso.length <= 10 ? `${iso}T00:00:00` : iso);
  return d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
}

/* ── the tab ──────────────────────────────────────────────────────────────── */
export default function Onboarding({ data, loading, error, reload, apply, onPickPlan }) {
  const [view, setView] = useState("today");
  const [dayId, setDayId] = useState(null);
  const plan = data && data.plan;

  /* Which day opens first: the one today falls in, else the last one before today, else the
     first. A plan that has not started opens on day one rather than on nothing. */
  const days = plan ? plan.days : [];
  const defaultDayId = useMemo(() => {
    if (!days.length) return null;
    const today = days.find((d) => d.is_today);
    if (today) return today.id;
    const past = days.filter((d) => d.is_past);
    return past.length ? past[past.length - 1].id : days[0].id;
  }, [plan && plan.id, days.length]);

  useEffect(() => { setDayId(null); }, [plan && plan.id]);
  const activeId = dayId || defaultDayId;
  const dayIndex = Math.max(0, days.findIndex((d) => d.id === activeId));
  const day = days[dayIndex];
  const week = plan && day ? plan.weeks.find((w) => w.day_ids.includes(day.id)) : null;

  if (loading) {
    return <div style={{ fontFamily: "var(--font-text)", fontSize: 13, color: T.muted, padding: 26 }}>Loading…</div>;
  }
  if (error) {
    return (
      <Card style={{ padding: 22, margin: 26 }}>
        <div style={{ fontFamily: "var(--font-display)", fontSize: 15, fontWeight: 600, color: T.ink }}>
          Onboarding could not be loaded
        </div>
        <div style={{ fontFamily: "var(--font-text)", fontSize: 13, color: T.muted, marginTop: 6 }}>
          {String((error && error.message) || error)}
        </div>
        <div style={{ marginTop: 12 }}><Btn onClick={reload}>Try again</Btn></div>
      </Card>
    );
  }

  if (!plan) {
    return (
      <div style={{ padding: "26px 0" }}>
        <Card style={{ padding: 26 }}>
          <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
            <ProductIcon name={iconFor("onboarding")} size={24} tone={T.teal} />
            <div style={{ fontFamily: "var(--font-display)", fontSize: 17, fontWeight: 700, color: T.ink }}>
              No onboarding plan yet
            </div>
          </div>
          <div style={{ fontFamily: "var(--font-text)", fontSize: 13.5, lineHeight: 1.65,
                        color: T.slate, marginTop: 10, maxWidth: 620 }}>
            A plan is a dated programme for one person — the days, the blocks within each day, the
            outcome each block is meant to produce, and the scoreboard it is measured by. Once one
            exists, whoever it belongs to works through it here and the numbers are the same ones
            you see.
          </div>
          <div style={{ fontFamily: "var(--font-data)", fontSize: 12, color: T.muted, marginTop: 14 }}>
            Seed one with{" "}
            <code style={{ background: T.page, borderRadius: 5, padding: "2px 6px" }}>
              python -m scripts.seed_onboarding --tenant &lt;slug&gt; --spec &lt;plan.json&gt;
            </code>
          </div>
        </Card>
      </div>
    );
  }

  /* Every mutation: patch the row that was clicked so the control responds at once, send the
     write, and reload only once it has COMMITTED. Reloading first races the write -- ticking a
     block and judging its outcome quickly showed "2 hit, 0 missed" over a database holding one
     of each. No total is recomputed here; they all come back from the reload. */
  const onBlock = (id, body) => {
    apply((p) => {
      p.days.forEach((d) => d.blocks.forEach((b) => {
        if (b.id !== id) return;
        if (body.done !== undefined) b.done = body.done;
        if (body.outcome_state !== undefined) b.outcome_state = body.outcome_state || null;
      }));
    });
    patchJSON(`/onboarding/blocks/${id}`, body).then(reload, reload);
  };
  const onDebrief = (id, text) => {
    patchJSON(`/onboarding/days/${id}/debrief`, { debrief: text }).then(reload, reload);
  };
  const onTarget = (id, body) => {
    apply((p) => { p.targets.forEach((t) => { if (t.id === id) Object.assign(t, body); }); });
    patchJSON(`/onboarding/targets/${id}`, body).then(reload, reload);
  };
  const onAddScript = (body) => postJSON(`/onboarding/plans/${plan.id}/scripts`, body).then(reload, reload);
  const onDeleteScript = (id) => delJSON(`/onboarding/scripts/${id}`).then(reload, reload);
  const onAddConvo = (body) => postJSON(`/onboarding/plans/${plan.id}/conversations`, body).then(reload, reload);
  const onPatchConvo = (id, body) => {
    apply((p) => { p.conversations.forEach((c) => { if (c.id === id) Object.assign(c, { ...body, heat: body.heat === "" ? null : body.heat ?? c.heat }); }); });
    patchJSON(`/onboarding/conversations/${id}`, body).then(reload, reload);
  };
  const onDeleteConvo = (id) => delJSON(`/onboarding/conversations/${id}`).then(reload, reload);

  const pct = plan.totals.blocks ? (plan.totals.blocks_done / plan.totals.blocks) * 100 : 0;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 18, paddingBottom: 30 }}>
      {/* The hero band asks for its surface rather than drawing one — see Brand.jsx. */}
      <div style={{ ...ribbedHero("evergreen"), borderRadius: 16, padding: "22px 26px", color: T.onDark }}>
        <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
          <ProductIcon name={iconFor("onboarding")} size={22} tone={T.onDark} />
          <div style={{ fontFamily: "var(--font-display)", fontSize: 20, fontWeight: 700 }}>
            {plan.subject_name}
          </div>
          <span style={{ fontFamily: "var(--font-text)", fontSize: 13, opacity: 0.78 }}>
            {plan.title} · {fmtDate(plan.starts_on)} to {fmtDate(plan.ends_on)}
          </span>
          <span style={{ flex: 1 }} />
          {data.plans.length > 1 && (
            <select value={plan.id} onChange={(e) => onPickPlan(e.target.value)}
                    style={{ fontFamily: "var(--font-text)", fontSize: 12.5, borderRadius: 8,
                             padding: "6px 10px", color: T.ink, background: T.white,
                             border: `1px solid ${T.line}` }}>
              {data.plans.map((p) => (
                <option key={p.id} value={p.id}>{p.subject_name}{p.is_mine ? " (you)" : ""}</option>
              ))}
            </select>
          )}
        </div>
        <div style={{ display: "flex", gap: 22, marginTop: 16, flexWrap: "wrap" }}>
          {[["Blocks done", `${plan.totals.blocks_done} / ${plan.totals.blocks}`],
            ["Outcomes hit", plan.totals.hits],
            ["Missed", plan.totals.misses],
            ["Targets met", `${plan.totals.targets_met} / ${plan.totals.targets}`],
            ["Appointments", plan.totals.appointments]].map(([k, v]) => (
              <div key={k}>
                <div style={{ fontFamily: "var(--font-text)", fontSize: 10.5, fontWeight: 600,
                              letterSpacing: ".08em", textTransform: "uppercase", opacity: 0.68 }}>{k}</div>
                <div style={{ fontFamily: "var(--font-data)", fontSize: 19, fontWeight: 700,
                              fontVariantNumeric: "tabular-nums", marginTop: 2 }}>{v}</div>
              </div>
            ))}
        </div>
        <div style={{ marginTop: 16, height: 4, borderRadius: 99,
                      background: "rgba(255,255,255,.22)", overflow: "hidden" }}>
          <div style={{ width: `${pct}%`, height: "100%", background: T.onDark, opacity: 0.9 }} />
        </div>
      </div>

      {/* A coach sees every control disabled. Without being told which of the three they are, a
          read-only page is indistinguishable from a broken one — which is the whole reason the
          payload carries `relationship`. */}
      {!plan.can_write && (
        <Card style={{ padding: "13px 18px", background: T.page }}>
          <div style={{ fontFamily: "var(--font-text)", fontSize: 13, color: T.slate }}>
            You are reading {plan.subject_name}&rsquo;s plan. Only {plan.subject_name.split(" ")[0]}
            {" "}and an admin can tick it off &mdash; the record of the month is theirs.
          </div>
        </Card>
      )}

      {plan.not_started && (
        <Card style={{ padding: "13px 18px", background: T.daffodilBg,
                       borderColor: alpha(T.daffodilText, 0.3) }}>
          <div style={{ fontFamily: "var(--font-text)", fontSize: 13, color: T.daffodilText }}>
            This plan starts {fmtDate(plan.starts_on)}. Nothing below is late yet.
          </div>
        </Card>
      )}

      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        {VIEWS.map(([k, label]) => (
          <Btn key={k} kind={view === k ? "primary" : "ghost"} onClick={() => setView(k)}>
            {label}
            {k === "score" && ` · ${plan.totals.targets_met}/${plan.totals.targets}`}
          </Btn>
        ))}
      </div>

      {view === "today" && days.length > 0 && (
        <div style={{ display: "flex", gap: 6, overflowX: "auto", paddingBottom: 4 }}>
          {days.map((d) => {
            const on = d.id === activeId;
            return (
              <button key={d.id} type="button" onClick={() => setDayId(d.id)} className="cc-nav"
                      style={{ flexShrink: 0, borderRadius: 9, padding: "7px 12px", cursor: "pointer",
                               fontFamily: "var(--font-data)", fontSize: 11.5, fontWeight: on ? 700 : 500,
                               color: on ? T.onDark : T.slate,
                               background: on ? T.evergreen : T.white,
                               border: `1px solid ${on ? T.evergreen : T.line}` }}>
                {fmtDate(d.date)}
                {d.misses > 0 && !on && <span style={{ color: T.poppyText }}> ·</span>}
              </button>
            );
          })}
        </div>
      )}

      {view === "today" && day && (
        <DayView plan={plan} day={day} index={dayIndex} count={days.length}
                 onStep={(n) => setDayId(days[Math.min(days.length - 1, Math.max(0, dayIndex + n))].id)}
                 onBlock={onBlock} onDebrief={onDebrief} />
      )}
      {view === "week" && week && (
        <WeekView plan={plan} week={week}
                  days={days.filter((d) => week.day_ids.includes(d.id))}
                  onOpenDay={(id) => { setDayId(id); setView("today"); }} />
      )}
      {view === "score" && <ScoreView plan={plan} onTarget={onTarget} />}
      {view === "library" && (
        <LibraryView plan={plan} onAddScript={onAddScript} onDeleteScript={onDeleteScript}
                     onAddConvo={onAddConvo} onPatchConvo={onPatchConvo} onDeleteConvo={onDeleteConvo} />
      )}
      {view === "rules" && <RulesView plan={plan} />}
    </div>
  );
}
