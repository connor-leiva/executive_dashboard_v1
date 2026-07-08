import { useEffect, useRef, useState } from "react";
import { T } from "./theme.js";
import { getJSON, postJSON } from "./api.js";

/* ──────────────────────────────────────────────────────────────
   Ask the dashboard — a global slide-over that sends questions to
   /api/v1/assistant/ask (Claude, server-side key). Answers come back
   scoped to the tabs the current user can see. Only mounts when the
   backend reports the assistant is configured (ANTHROPIC_API_KEY set).
   ────────────────────────────────────────────────────────────── */

const API_BASE = import.meta.env.VITE_API_BASE;

const SUGGESTIONS = [
  "How's The Forum doing this period?",
  "What's our MRR and ARR right now?",
  "Which business is underperforming?",
  "Any money I need to chase?",
];

/* tiny markdown → nodes: **bold**, `code`, bullet lists, line breaks */
function rich(text) {
  const inline = (str, keyBase) => {
    const nodes = [];
    const re = /(\*\*[^*]+\*\*|`[^`]+`)/g;
    let last = 0, m, i = 0;
    while ((m = re.exec(str)) !== null) {
      if (m.index > last) nodes.push(str.slice(last, m.index));
      const tok = m[0];
      if (tok.startsWith("**")) nodes.push(<strong key={`${keyBase}-${i++}`} style={{ color: T.ink, fontWeight: 700 }}>{tok.slice(2, -2)}</strong>);
      else nodes.push(<code key={`${keyBase}-${i++}`} style={{ fontFamily: "ui-monospace,monospace", fontSize: "0.92em", background: T.parchment, borderRadius: 4, padding: "1px 4px" }}>{tok.slice(1, -1)}</code>);
      last = m.index + tok.length;
    }
    if (last < str.length) nodes.push(str.slice(last));
    return nodes;
  };

  const blocks = text.split(/\n{2,}/);
  return blocks.map((block, bi) => {
    const lines = block.split("\n");
    const isList = lines.every((l) => /^\s*[-*•]\s+/.test(l));
    if (isList) {
      return (
        <ul key={bi} style={{ margin: "6px 0", paddingLeft: 18, display: "flex", flexDirection: "column", gap: 4 }}>
          {lines.map((l, li) => <li key={li}>{inline(l.replace(/^\s*[-*•]\s+/, ""), `${bi}-${li}`)}</li>)}
        </ul>
      );
    }
    return (
      <p key={bi} style={{ margin: bi === 0 ? "0 0 6px" : "6px 0" }}>
        {lines.map((l, li) => <span key={li}>{inline(l, `${bi}-${li}`)}{li < lines.length - 1 && <br />}</span>)}
      </p>
    );
  });
}

function Bubble({ role, children }) {
  const me = role === "user";
  return (
    <div style={{ display: "flex", justifyContent: me ? "flex-end" : "flex-start" }}>
      <div style={{
        maxWidth: "86%", fontFamily: "Inter,sans-serif", fontSize: 13, lineHeight: 1.5,
        color: me ? T.onDark : T.secondary,
        background: me ? T.evergreen : T.parchment,
        border: me ? "none" : `1px solid ${T.line}`,
        borderRadius: me ? "12px 12px 4px 12px" : "12px 12px 12px 4px",
        padding: "9px 12px", whiteSpace: "normal", wordBreak: "break-word",
      }}>{children}</div>
    </div>
  );
}

export default function Assistant({ period = "mtd" }) {
  const [enabled, setEnabled] = useState(false);
  const [open, setOpen] = useState(false);
  const [msgs, setMsgs] = useState([]);     // {role, content, error?}
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const scroller = useRef(null);
  const inputRef = useRef(null);

  useEffect(() => {
    if (!API_BASE) return;   // dev sample mode has no backend
    getJSON("/assistant/status").then((s) => setEnabled(!!s.enabled)).catch(() => setEnabled(false));
  }, []);

  useEffect(() => {
    if (open && scroller.current) scroller.current.scrollTop = scroller.current.scrollHeight;
  }, [msgs, busy, open]);

  useEffect(() => {
    if (open) setTimeout(() => inputRef.current?.focus(), 60);
  }, [open]);

  async function send(text) {
    const q = (text ?? input).trim();
    if (!q || busy) return;
    const history = msgs.filter((m) => !m.error).map((m) => ({ role: m.role, content: m.content }));
    setMsgs((m) => [...m, { role: "user", content: q }]);
    setInput("");
    setBusy(true);
    try {
      const r = await postJSON("/assistant/ask", { question: q, period, history });
      setMsgs((m) => [...m, { role: "assistant", content: r.answer || "(no answer)" }]);
    } catch (e) {
      setMsgs((m) => [...m, { role: "assistant", content: e.detail || "Something went wrong reaching the assistant.", error: true }]);
    } finally {
      setBusy(false);
    }
  }

  if (!enabled) return null;

  return (
    <>
      <style>{`
        @keyframes asst-in { from { transform: translateX(24px); opacity: 0; } to { transform: none; opacity: 1; } }
        @keyframes asst-blink { 0%,80%,100% { opacity: .25; } 40% { opacity: 1; } }
        .asst-dot { width: 6px; height: 6px; border-radius: 99px; background: ${T.meadow}; display: inline-block; animation: asst-blink 1.3s infinite both; }
        .asst-chip:hover { border-color: ${T.meadow}; color: ${T.meadow}; }
        .asst-send:not(:disabled):hover { filter: brightness(1.06); }
        .asst-fab:hover { transform: translateY(-1px); box-shadow: 0 14px 34px rgba(0,46,44,.30); }
      `}</style>

      {/* floating launcher */}
      {!open && (
        <button className="asst-fab" onClick={() => setOpen(true)} aria-label="Ask the dashboard" style={{
          position: "fixed", right: 24, bottom: 24, zIndex: 60, display: "inline-flex", alignItems: "center", gap: 9,
          background: T.evergreen, color: T.onDark, border: "none", borderRadius: 999, padding: "12px 18px",
          fontFamily: "Poppins,sans-serif", fontSize: 13.5, fontWeight: 600, cursor: "pointer",
          boxShadow: "0 10px 26px rgba(0,46,44,.24)", transition: "transform .15s ease, box-shadow .15s ease",
        }}>
          <Sparkle /> Ask
        </button>
      )}

      {open && (
        <>
          <div onClick={() => setOpen(false)} style={{ position: "fixed", inset: 0, background: "rgba(0,46,44,0.28)", zIndex: 60 }} />
          <aside style={{
            position: "fixed", top: 0, right: 0, bottom: 0, width: "min(440px, 100vw)", zIndex: 61,
            background: T.white, boxShadow: "-16px 0 44px rgba(0,46,44,.18)", display: "flex", flexDirection: "column",
            animation: "asst-in .22s ease",
          }}>
            {/* header */}
            <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "16px 18px", borderBottom: `1px solid ${T.line}` }}>
              <span style={{ display: "inline-flex", alignItems: "center", justifyContent: "center", width: 30, height: 30, borderRadius: 8, background: T.meadowBg }}><Sparkle color={T.meadow} /></span>
              <div style={{ flex: 1 }}>
                <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 15, fontWeight: 600, color: T.ink }}>Ask the dashboard</div>
                <div style={{ fontFamily: "Inter,sans-serif", fontSize: 11, color: T.muted }}>Answers from the tabs you can see</div>
              </div>
              {msgs.length > 0 && (
                <button onClick={() => setMsgs([])} title="Clear" style={ghostBtn}>Clear</button>
              )}
              <button onClick={() => setOpen(false)} aria-label="Close" style={{ ...ghostBtn, padding: "5px 9px" }}>✕</button>
            </div>

            {/* messages */}
            <div ref={scroller} style={{ flex: 1, overflowY: "auto", padding: 16, display: "flex", flexDirection: "column", gap: 12 }}>
              {msgs.length === 0 && (
                <div style={{ margin: "auto 0", display: "flex", flexDirection: "column", gap: 12 }}>
                  <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, color: T.slate, lineHeight: 1.55 }}>
                    Ask about your numbers in plain English — revenue, MRR, renewals, what needs attention. It reads the live dashboard.
                  </div>
                  <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                    {SUGGESTIONS.map((s) => (
                      <button key={s} className="asst-chip" onClick={() => send(s)} style={{
                        textAlign: "left", fontFamily: "Inter,sans-serif", fontSize: 12.5, color: T.slate,
                        background: T.white, border: `1px solid ${T.line}`, borderRadius: 10, padding: "9px 12px", cursor: "pointer",
                      }}>{s}</button>
                    ))}
                  </div>
                </div>
              )}
              {msgs.map((m, i) => (
                <Bubble key={i} role={m.role}>
                  {m.role === "assistant" && !m.error ? rich(m.content)
                    : m.error ? <span style={{ color: T.gapText }}>{m.content}</span>
                    : m.content}
                </Bubble>
              ))}
              {busy && (
                <Bubble role="assistant">
                  <span style={{ display: "inline-flex", gap: 4, alignItems: "center" }}>
                    <span className="asst-dot" /><span className="asst-dot" style={{ animationDelay: ".2s" }} /><span className="asst-dot" style={{ animationDelay: ".4s" }} />
                  </span>
                </Bubble>
              )}
            </div>

            {/* composer */}
            <div style={{ borderTop: `1px solid ${T.line}`, padding: 12, display: "flex", gap: 8, alignItems: "flex-end" }}>
              <textarea ref={inputRef} value={input} onChange={(e) => setInput(e.target.value)} rows={1}
                onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }}
                placeholder="Ask a question…" style={{
                  flex: 1, resize: "none", maxHeight: 120, fontFamily: "Inter,sans-serif", fontSize: 13, color: T.ink,
                  background: T.parchment, border: `1px solid ${T.line}`, borderRadius: 10, padding: "10px 12px", lineHeight: 1.4, outline: "none",
                }} />
              <button className="asst-send" onClick={() => send()} disabled={busy || !input.trim()} aria-label="Send" style={{
                flexShrink: 0, width: 38, height: 38, borderRadius: 10, border: "none", cursor: busy || !input.trim() ? "not-allowed" : "pointer",
                background: busy || !input.trim() ? T.sprout : T.evergreen, color: T.onDark, fontSize: 16,
                display: "inline-flex", alignItems: "center", justifyContent: "center",
              }}>↑</button>
            </div>
            <div style={{ fontFamily: "Inter,sans-serif", fontSize: 10, color: T.muted, textAlign: "center", padding: "0 0 10px" }}>
              Claude can be wrong — check the numbers on the tabs.
            </div>
          </aside>
        </>
      )}
    </>
  );
}

const ghostBtn = {
  fontFamily: "Inter,sans-serif", fontSize: 11.5, fontWeight: 600, color: T.slate,
  background: T.parchment, border: `1px solid ${T.line}`, borderRadius: 7, padding: "5px 10px", cursor: "pointer",
};

function Sparkle({ color = "#F3EEE7", size = 15 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden>
      <path d="M12 2l1.8 5.6a4 4 0 0 0 2.6 2.6L22 12l-5.6 1.8a4 4 0 0 0-2.6 2.6L12 22l-1.8-5.6a4 4 0 0 0-2.6-2.6L2 12l5.6-1.8a4 4 0 0 0 2.6-2.6L12 2z" fill={color} />
    </svg>
  );
}
