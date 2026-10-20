/* Books Cleanup · Ask about this -- a question about the thing on screen (a transaction, a line of the statement, a
   pair of companies), answered by Claude from the tab's own figures for it (POST /cleanup/ask). Offered only when
   the server has the key (the overview's can_ask). The answer is words: nothing it says changes the books, and when
   the question is really "change this", it names the tab's own action for it.

   A short conversation per box (the last few turns go back with each question); closing the box forgets it. The
   field stays enabled while an answer is on its way (`send` ignores a second press), so it keeps the focus for a
   follow-up. */
import { useContext, useState } from "react";
import { askCleanup } from "./useCleanup.js";
import { CleanupCtx, F, L, reasonOf } from "./kit.jsx";

export function AskAbout({ about, label = "Ask about this", placeholder = "Why is this here?" }) {
  const ctx = useContext(CleanupCtx);
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [turns, setTurns] = useState([]);           // [{role: "user" | "assistant", content}]
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  if (!ctx?.ov?.can_ask) return null;
  const send = async (e) => {
    e.preventDefault();
    const question = q.trim();
    if (!question || busy) return;
    setBusy(true);
    setError(null);
    try {
      const r = await askCleanup(about, question, turns);
      setTurns([...turns, { role: "user", content: question }, { role: "assistant", content: r.answer }]);
      setQ("");
    } catch (err) {
      setError(reasonOf(err));
    }
    setBusy(false);
  };
  const close = () => { setOpen(false); setTurns([]); setQ(""); setError(null); };
  return (
    <div className="ca-wrap" onClick={(e) => e.stopPropagation()}>
      {!open ? (
        <button type="button" className="ca-open" onClick={() => setOpen(true)}>
          <span aria-hidden="true" style={{ color: L.accentInk }}>✦</span> {label}</button>
      ) : (
        <div className="ca-box">
          {turns.map((t, i) => (
            <div key={i} className={t.role === "user" ? "ca-q" : "ca-a"}>{t.content}</div>))}
          <form onSubmit={send} className="ca-form">
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={turns.length ? "Ask a follow-up" : placeholder}
                   maxLength={1000} aria-label={label} autoFocus className="ca-input" />
            <button type="submit" className="ca-btn ca-primary" disabled={busy || !q.trim()}>{busy ? "Asking…" : "Ask"}</button>
            <button type="button" className="ca-btn" onClick={close}>Close</button>
          </form>
          {error ? <div style={{ fontFamily: F.body, fontSize: 12.5, color: L.loss }}>{error}</div> : null}
          <div style={{ fontFamily: F.body, fontSize: 11.5, color: L.muted }}>
            Answered by Claude from this tab&rsquo;s figures. It can&rsquo;t change anything here.</div>
        </div>
      )}
      <style>{ASK_CSS}</style>
    </div>
  );
}

const ASK_CSS = `
  .ca-wrap { margin-top: 12px; }
  .ca-open { height: 28px; padding: 0 11px; border-radius: 8px; border: 1px dashed ${L.pillEdge}; background: transparent;
    color: ${L.bodyDim}; font-family: ${F.body}; font-size: 12.5px; font-weight: 600; cursor: pointer; }
  .ca-open:hover { border-color: ${L.accentMuted}; color: ${L.deep}; }
  .ca-open:focus-visible, .ca-btn:focus-visible, .ca-input:focus-visible { outline: 2px solid ${L.accent}; outline-offset: 1px; }
  .ca-box { border: 1px solid ${L.ruleInner}; border-radius: 10px; background: ${L.paper}; padding: 12px 14px;
    display: grid; gap: 10px; max-width: 880px; }
  .ca-q { font-family: ${F.body}; font-size: 13px; font-weight: 600; color: ${L.deep}; }
  .ca-a { font-family: ${F.body}; font-size: 13px; color: ${L.body}; line-height: 1.55; white-space: pre-wrap;
    border-left: 2.5px solid ${L.accentEdge}; padding-left: 10px; }
  .ca-form { display: flex; gap: 8px; flex-wrap: wrap; }
  .ca-input { flex: 1; min-width: 180px; height: 32px; padding: 0 10px; border-radius: 8px; border: 1px solid ${L.pillEdge};
    background: ${L.paper}; color: ${L.deep}; font-family: ${F.body}; font-size: 13px; }
  .ca-btn { height: 32px; padding: 0 12px; border-radius: 8px; border: 1px solid ${L.pillEdge}; background: ${L.paper};
    color: ${L.bodyDim}; font-family: ${F.body}; font-size: 12.5px; font-weight: 600; cursor: pointer; }
  .ca-btn:disabled { opacity: .55; cursor: default; }
  .ca-primary { background: ${L.deep}; border-color: ${L.deep}; color: ${L.onDeep}; }
`;
