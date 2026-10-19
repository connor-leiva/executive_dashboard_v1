/* Books Cleanup · To do and Findings -- the work left, and what the audit found.

   TO DO is the checklist (ticked here, with a note) and the open items (each answered in words).
   Both are the bookkeeper's work, so the tab grant is enough to change them. Ticks and answers are
   audited like every other change on the tab.

   FINDINGS is read-only: the audit's findings and the answers to the owner's questions, as the
   workbook wrote them. */
import { useState } from "react";
import { StatePanel } from "../books/ui.jsx";
import { saveChanges, useCleanupItems } from "./useCleanup.js";
import { BODY, Button, EYEBROW, Foot, L, F, NUM, OkPill, Panel, SaveLine, Sheet, cents, inputStyle, money,
         reasonOf } from "./kit.jsx";

function useSave(onSaved) {
  const [state, setState] = useState(null);
  const save = async (changes, ok = "Saved.") => {
    setState({ busy: true });
    try { onSaved(await saveChanges(changes)); setState({ saved: ok }); return true; }
    catch (e) { setState({ error: reasonOf(e) }); return false; }
  };
  return [state, save];
}

function Step({ c, onSaved }) {
  const [state, save] = useSave(onSaved);
  const [note, setNote] = useState(c.note || "");
  const dirty = note !== (c.note || "");
  return (
    <div style={{ display: "grid", gridTemplateColumns: "26px minmax(0,1fr)", gap: 10, padding: "10px 18px",
                  borderBottom: `1px solid ${L.ruleLine}`, background: c.done ? L.totalBand : L.paper }}>
      <input type="checkbox" checked={!!c.done} disabled={state?.busy} aria-label={c.text}
             onChange={(e) => save([{ path: ["checklist", c.id], value: { done: e.target.checked, note } }],
                                   e.target.checked ? "Done." : "Reopened.")}
             style={{ width: 17, height: 17, marginTop: 2, accentColor: L.accent, cursor: "pointer" }} />
      <div style={{ display: "grid", gap: 6, minWidth: 0 }}>
        <div style={{ display: "flex", gap: 10, alignItems: "baseline", flexWrap: "wrap" }}>
          <span style={{ ...BODY, flex: 1, minWidth: 220, color: c.done ? L.muted : L.deep,
                         textDecoration: c.done ? "line-through" : "none" }}>{c.text}</span>
          <span style={{ fontFamily: F.body, fontSize: 11.5, fontWeight: 600, color: L.bodyDim, background: L.sectionBand,
                         borderRadius: 999, padding: "2px 9px", whiteSpace: "nowrap" }}>{c.who}</span>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <input value={note} onChange={(e) => setNote(e.target.value)} maxLength={2000} aria-label="Note"
                 placeholder="Add a note (what was done, entry numbers…)"
                 onKeyDown={(e) => { if (e.key === "Enter" && dirty) save([{ path: ["checklist", c.id], value: { done: !!c.done, note } }]); }}
                 style={{ ...inputStyle, flex: 1, minWidth: 200, fontFamily: F.body, fontSize: 12.5 }} />
          {dirty && <Button primary disabled={state?.busy}
                            onClick={() => save([{ path: ["checklist", c.id], value: { done: !!c.done, note } }])}>Save note</Button>}
          <SaveLine state={state} />
        </div>
      </div>
    </div>
  );
}

function OpenItem({ x, onSaved }) {
  const [state, save] = useSave(onSaved);
  const [text, setText] = useState(x.answer || "");
  const dirty = text !== (x.answer || "");
  const answered = !!(x.answer || "").trim();
  return (
    <div style={{ padding: "12px 18px", borderBottom: `1px solid ${L.ruleLine}`, display: "grid", gap: 8,
                  background: answered ? L.paper : L.flagBg, boxShadow: answered ? "none" : `inset 3px 0 0 ${L.flagEdge}` }}>
      <div style={{ display: "flex", gap: 10, alignItems: "baseline", flexWrap: "wrap" }}>
        <span style={{ fontFamily: F.num, fontSize: 11.5, color: L.ghost }}>{x.ref}</span>
        <span style={{ fontFamily: F.body, fontSize: 13, fontWeight: 600, color: L.deep }}>{x.who || "(no name)"}</span>
        <span style={{ fontFamily: F.body, fontSize: 12, color: L.muted }}>{x.company}{x.date ? ` · ${x.date}` : ""}</span>
        <div style={{ flex: 1 }} />
        {x.amount !== null && x.amount !== undefined && <span style={{ ...NUM, fontSize: 13, color: L.deep }}>{cents(x.amount)}</span>}
      </div>
      <div style={{ ...BODY, fontSize: 12.5 }}>{x.text}</div>
      <textarea value={text} onChange={(e) => setText(e.target.value)} maxLength={4000} rows={answered || dirty ? 3 : 2}
                aria-label={`Answer to ${x.ref}`} placeholder="The answer: what it was, and where it goes"
                style={{ ...inputStyle, height: "auto", padding: "7px 9px", fontFamily: F.body, fontSize: 12.5,
                         lineHeight: 1.45, resize: "vertical", width: "100%" }} />
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <Button primary disabled={!dirty || state?.busy}
                onClick={() => save([{ path: ["answers", x.ref], value: text }], text.trim() ? "Answered." : "Cleared.")}>
          Save answer</Button>
        {dirty && <Button onClick={() => setText(x.answer || "")}>Discard</Button>}
        <SaveLine state={state} />
      </div>
    </div>
  );
}

export function CleanupTodo({ rev, onSaved }) {
  const it = useCleanupItems(rev);
  const d = it.data;
  const [showAnswered, setShowAnswered] = useState(false);
  const groups = d ? (d.checklist_groups?.length ? d.checklist_groups
    : [...new Set(d.checklist.map((c) => c.group))].map((g) => ({ key: g, label: g }))) : [];
  const done = d ? d.checklist.filter((c) => c.done).length : 0;
  const open = d ? d.open.filter((x) => !(x.answer || "").trim()).length : 0;
  return (
    <StatePanel loading={it.loading} error={it.error} retry={it.retry}>
      {d && (
        <Panel>
          <div style={{ background: L.deep, padding: "14px 26px", display: "flex", gap: 18, flexWrap: "wrap", alignItems: "center" }}>
            <span style={{ fontFamily: F.num, fontSize: 10, letterSpacing: ".14em", textTransform: "uppercase",
                           color: L.onDeepEyebrow }}>What&rsquo;s left</span>
            <span style={{ fontFamily: F.body, fontSize: 13.5, color: L.onDeepPill }}>
              <strong style={{ color: L.paper, fontFamily: F.num }}>{done}</strong> of {d.checklist.length} steps done</span>
            <span style={{ fontFamily: F.body, fontSize: 13.5, color: L.onDeepPill }}>
              <strong style={{ color: L.paper, fontFamily: F.num }}>{open}</strong> of {d.open.length} open items unanswered</span>
            <div style={{ flex: 1, minWidth: 120, height: 5, borderRadius: 999, background: L.onDeepPillEdge, overflow: "hidden" }}>
              <div style={{ width: `${d.checklist.length ? done / d.checklist.length * 100 : 0}%`, height: "100%",
                            background: L.accent }} />
            </div>
          </div>
          <div style={{ padding: "20px 26px 26px", display: "grid", gap: 18 }}>
            {groups.map((g, i) => {
              const steps = d.checklist.filter((c) => c.group === g.key);
              const n = steps.filter((c) => c.done).length;
              return (
                <Sheet key={g.key} title={`${i + 1}. ${g.label}`}
                       right={<OkPill ok={n === steps.length}>{n} of {steps.length} done</OkPill>}>
                  {steps.map((c) => <Step key={`${c.id}|${c.note || ""}`} c={c} onSaved={onSaved} />)}
                </Sheet>
              );
            })}
            <Sheet title="Open items" right={<span style={{ fontFamily: F.body, fontSize: 12, color: open ? L.flagInk : L.accentInk }}>
              {open ? `${open} to answer` : "All answered"}</span>}>
              {d.open.map((x) => <OpenItem key={`${x.ref}|${x.answer || ""}`} x={x} onSaved={onSaved} />)}
            </Sheet>
            {d.answered.length > 0 && (
              <Sheet title="Answered before"
                     right={<Button onClick={() => setShowAnswered(!showAnswered)}>{showAnswered ? "Hide" : `Show ${d.answered.length}`}</Button>}>
                {showAnswered && d.answered.map((x) => (
                  <div key={x.ref} style={{ padding: "10px 18px", borderBottom: `1px solid ${L.ruleLine}`, display: "grid", gap: 4 }}>
                    <div style={{ display: "flex", gap: 10, alignItems: "baseline", flexWrap: "wrap" }}>
                      <span style={{ fontFamily: F.num, fontSize: 11.5, color: L.ghost }}>{x.ref}</span>
                      <span style={{ fontFamily: F.body, fontSize: 13, fontWeight: 600, color: L.deep }}>{x.who}</span>
                      <span style={{ fontFamily: F.body, fontSize: 12, color: L.muted }}>{x.company}{x.date ? ` · ${x.date}` : ""}</span>
                      <div style={{ flex: 1 }} />
                      {x.amount !== null && x.amount !== undefined && <span style={{ ...NUM, fontSize: 13, color: L.deep }}>{cents(x.amount)}</span>}
                    </div>
                    <div style={{ ...BODY, fontSize: 12.5 }}>{x.text}</div>
                  </div>
                ))}
              </Sheet>
            )}
          </div>
        </Panel>
      )}
    </StatePanel>
  );
}

const SEVERITY = { high: "High", medium: "Medium", low: "Low" };

export function CleanupFindings({ rev }) {
  const it = useCleanupItems(rev);
  const d = it.data;
  const [level, setLevel] = useState("all");
  const findings = d ? d.findings.filter((f) => level === "all" || f.severity === level) : [];
  return (
    <StatePanel loading={it.loading} error={it.error} retry={it.retry}>
      {d && (
        <Panel>
          <div style={{ padding: "20px 26px 26px", display: "grid", gap: 18 }}>
            <Sheet title={d.questions_title || "Questions, answered"}>
              {d.questions.map((q, i) => (
                <details key={i} style={{ padding: "11px 18px", borderBottom: `1px solid ${L.ruleLine}` }}>
                  <summary style={{ cursor: "pointer", fontFamily: F.body, fontSize: 13.5, fontWeight: 600, color: L.deep }}>
                    {q.question}</summary>
                  <div style={{ display: "grid", gap: 6, marginTop: 8 }}>
                    <div style={{ ...BODY, fontSize: 13, color: L.deep }}>{q.short}</div>
                    {q.behind && <div style={{ ...BODY, fontSize: 12.5, color: L.bodyDim }}>{q.behind}</div>}
                    {q.where && <Foot>In the workbook: {q.where}</Foot>}
                  </div>
                </details>
              ))}
            </Sheet>
            <Sheet title="Findings" right={
              <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                {["all", "high", "medium", "low"].map((k) => (
                  <button key={k} onClick={() => setLevel(k)} aria-pressed={level === k} style={{
                    height: 26, padding: "0 10px", borderRadius: 8, cursor: "pointer", fontFamily: F.body, fontSize: 12,
                    fontWeight: level === k ? 600 : 500, border: `1px solid ${level === k ? L.deep : L.pillEdge}`,
                    background: level === k ? L.deep : L.paper, color: level === k ? L.onDeep : L.bodyDim }}>
                    {k === "all" ? `All ${d.findings.length}` : `${SEVERITY[k]} ${d.findings.filter((f) => f.severity === k).length}`}
                  </button>
                ))}
              </div>}>
              {findings.map((f) => (
                <details key={f.id} style={{ padding: "11px 18px", borderBottom: `1px solid ${L.ruleLine}`,
                                             boxShadow: f.severity === "high" ? `inset 3px 0 0 ${L.flagEdge}` : "none" }}>
                  <summary style={{ cursor: "pointer", display: "flex", gap: 10, alignItems: "baseline", flexWrap: "wrap" }}>
                    <span style={{ ...EYEBROW, color: f.severity === "high" ? L.flagInk : L.faint, minWidth: 54 }}>
                      {SEVERITY[f.severity] || f.severity}</span>
                    <span style={{ fontFamily: F.body, fontSize: 13.5, fontWeight: 600, color: L.deep, flex: 1, minWidth: 220 }}>
                      {f.title}</span>
                    {typeof f.amount === "number" && <span style={{ ...NUM, fontSize: 13, color: L.deep }}>{money(f.amount)}</span>}
                  </summary>
                  <div style={{ display: "grid", gap: 6, marginTop: 8 }}>
                    {[["What we saw", f.saw], ["Why it matters", f.matters], ["What to do", f.action]].filter(([, v]) => v).map(([k, v]) => (
                      <div key={k} style={{ ...BODY, fontSize: 12.5 }}><span style={{ ...EYEBROW, marginRight: 8 }}>{k}</span>{v}</div>
                    ))}
                  </div>
                </details>
              ))}
            </Sheet>
          </div>
        </Panel>
      )}
    </StatePanel>
  );
}
