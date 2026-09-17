/* The operator console's building blocks, from the design file's primitives with its colour and
   type replaced by ../brand/acumyn.jsx through tokens.js. */
import React, { useCallback, useEffect, useRef, useState } from "react";
import { A, STATE, TYPE, WHITE } from "./tokens.js";

export function Dot({ c, size = 7, title }) {
  return (
    <span title={title} style={{ width: size, height: size, borderRadius: 99, background: c, flexShrink: 0, display: "inline-block" }} />
  );
}

/* A status chip. Never shown alone where the reason is not also on screen: that is the console's
   one law, and it is the caller's to keep. */
export function Chip({ state, children, mono, title }) {
  const s = STATE[state] || { c: A.body, bg: A.chip };
  return (
    <span title={title} style={{
      display: "inline-flex", alignItems: "center", gap: 5, flexShrink: 0,
      fontFamily: mono ? TYPE.data : TYPE.text, fontSize: 10.5, fontWeight: 600,
      letterSpacing: mono ? 0 : ".02em", fontVariantNumeric: "tabular-nums", lineHeight: 1.5,
      color: s.c, background: s.bg, borderRadius: 5, padding: "2.5px 7px", whiteSpace: "nowrap",
    }}>
      {state && STATE[state] ? <Dot c={s.c} size={5} /> : null}
      {children || (STATE[state] && STATE[state].label)}
    </span>
  );
}

export function Eyebrow({ children, style }) {
  return (
    <div style={{
      fontFamily: TYPE.text, fontSize: 10, fontWeight: 600, letterSpacing: ".14em",
      textTransform: "uppercase", color: A.mute, lineHeight: 1.4, ...style,
    }}>{children}</div>
  );
}

export function Card({ title, sub, right, children, pad = 16, style }) {
  return (
    <section style={{ background: A.paper, border: `1px solid ${A.line}`, borderRadius: 10, boxShadow: A.lift, ...style }}>
      {(title || right) ? (
        <header style={{
          display: "flex", alignItems: "flex-start", justifyContent: "space-between",
          gap: "10px 14px", flexWrap: "wrap",
          padding: `15px 16px ${sub ? 11 : 13}px`, borderBottom: `1px solid ${A.lineSoft}`,
        }}>
          <div style={{ minWidth: 0, flex: "1 1 220px" }}>
            <h2 style={{ margin: 0, fontFamily: TYPE.text, fontSize: 13.5, fontWeight: 600, color: A.ink, letterSpacing: "-.005em" }}>{title}</h2>
            {sub ? <div style={{ fontFamily: TYPE.text, fontSize: 11.5, color: A.mute, marginTop: 3, lineHeight: 1.5, textWrap: "pretty" }}>{sub}</div> : null}
          </div>
          {right ? <div style={{ flexShrink: 0, display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>{right}</div> : null}
        </header>
      ) : null}
      <div style={{ padding: pad }}>{children}</div>
    </section>
  );
}

/* Space Grotesk is never set below 16px (the guide's rule), and console buttons run 11-12px, so
   Instrument Sans 600 carries them. Cadet (`primary`) is the one action colour per view; row-level
   actions are `solid` Ink. The on-dark pair keeps secondary actions on the ink bar from shouting. */
export function Btn({ kind = "ghost", small, onClick, children, disabled, title, type = "button", busy }) {
  const base = {
    fontFamily: TYPE.text, fontSize: small ? 11 : 12, fontWeight: 600, letterSpacing: ".01em",
    lineHeight: 1.35, borderRadius: 7, padding: small ? "5px 9px" : "7px 13px",
    cursor: disabled || busy ? "not-allowed" : "pointer", whiteSpace: "nowrap",
    opacity: disabled ? 0.42 : 1,
    transition: "background .12s ease, border-color .12s ease, color .12s ease, transform .06s ease",
  };
  const kinds = {
    solid: { background: A.ink, color: A.onInk, border: `1px solid ${A.ink}` },
    primary: { background: A.cadet, color: WHITE, border: `1px solid ${A.cadet}` },
    ghost: { background: A.paper, color: A.ink80, border: `1px solid ${A.line}` },
    quiet: { background: "transparent", color: A.body, border: "1px solid transparent" },
    danger: { background: A.paper, color: A.stop, border: `1px solid ${A.stopLine}` },
    dangerSolid: { background: A.stop, color: WHITE, border: `1px solid ${A.stop}` },
    onGhost: { background: "transparent", color: A.onInk, border: "1px solid rgba(239,245,243,.3)" },
    onDanger: { background: "transparent", color: A.stopLine, border: "1px solid rgba(240,207,203,.36)" },
    onDark: { background: A.onInk, color: A.ink, border: `1px solid ${A.onInk}` },
    icon: {
      background: "transparent", color: A.mute, border: "1px solid transparent",
      padding: 0, width: 26, height: 26, borderRadius: 6, fontSize: 15, lineHeight: 1,
      display: "inline-flex", alignItems: "center", justifyContent: "center",
    },
  };
  return (
    <button type={type} className={`ac-btn ac-b-${kind}`} title={title} disabled={disabled || busy}
      aria-busy={busy || undefined} onClick={onClick} style={{ ...base, ...kinds[kind] }}>
      {busy ? "Working…" : children}
    </button>
  );
}

export function Seg({ options, value, onChange, label }) {
  return (
    <div role="group" aria-label={label} style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
      {options.map(([k, l]) => {
        const on = value === k;
        return (
          <button key={k} type="button" aria-pressed={on} onClick={() => onChange(k)}
            className={`ac-btn ac-seg${on ? " on" : ""}`} style={{
              fontFamily: TYPE.text, fontSize: 11, fontWeight: 600, lineHeight: 1.35, borderRadius: 6,
              padding: "5px 9px", cursor: "pointer", whiteSpace: "nowrap",
              transition: "background .12s ease, color .12s ease, border-color .12s ease",
              background: on ? A.ink : "transparent", color: on ? A.onInk : A.mute,
              border: `1px solid ${on ? A.ink : A.line}`,
            }}>{l}</button>
        );
      })}
    </div>
  );
}

export function Mono({ children, c = A.body, size = 11.5, style }) {
  return (
    <span style={{ fontFamily: TYPE.data, fontWeight: 500, fontVariantNumeric: "tabular-nums", fontSize: size, color: c, ...style }}>{children}</span>
  );
}

export function Ellipsis() {
  return (
    <span aria-hidden style={{ display: "inline-flex", gap: 2.5, alignItems: "center" }}>
      {[0, 1, 2].map((i) => <span key={i} style={{ width: 2.5, height: 2.5, borderRadius: 99, background: "currentColor" }} />)}
    </span>
  );
}

/* A metric that can say "there is no source for this" instead of inventing a number. */
export function Stat({ label, value, note, state, unsourced }) {
  return (
    <div style={{ minWidth: 0, padding: "13px 14px", background: A.paper, border: `1px solid ${A.line}`, borderRadius: 10, boxShadow: A.lift }}>
      <Eyebrow>{label}</Eyebrow>
      {unsourced ? (
        <>
          <div aria-label="No figure" style={{ fontFamily: TYPE.data, fontSize: 22, fontWeight: 600, color: A.faint, marginTop: 6, lineHeight: 1.1 }}>—</div>
          <div style={{ fontFamily: TYPE.text, fontSize: 10.5, color: A.mute, marginTop: 4, lineHeight: 1.45, textWrap: "pretty" }}>{note}</div>
        </>
      ) : (
        <>
          <div style={{
            fontFamily: TYPE.data, fontSize: 24, fontWeight: 600, lineHeight: 1.1, letterSpacing: "-.01em",
            marginTop: 6, color: state && STATE[state] ? STATE[state].c : A.ink, fontVariantNumeric: "tabular-nums",
          }}>{value}</div>
          {note ? <div style={{ fontFamily: TYPE.text, fontSize: 11, color: A.mute, marginTop: 4, lineHeight: 1.45, textWrap: "pretty" }}>{note}</div> : null}
        </>
      )}
    </div>
  );
}

export function Bar({ segs, h = 6 }) {
  const total = segs.reduce((a, s) => a + s.v, 0) || 1;
  return (
    <div style={{ display: "flex", gap: 1, height: h, borderRadius: 99, overflow: "hidden", background: A.lineSoft }}>
      {segs.filter((s) => s.v > 0).map((s) => (
        <span key={s.label} title={`${s.label}: ${s.v}`} style={{ width: `${(s.v / total) * 100}%`, minWidth: 3, background: s.c }} />
      ))}
    </div>
  );
}

export function Empty({ title, action, children }) {
  return (
    <div style={{ padding: "30px 18px", textAlign: "center" }}>
      <div style={{ fontFamily: TYPE.text, fontSize: 13, fontWeight: 600, color: A.ink }}>{title}</div>
      {children ? (
        <div style={{ fontFamily: TYPE.text, fontSize: 12, color: A.mute, marginTop: 6, lineHeight: 1.55, maxWidth: 440, marginLeft: "auto", marginRight: "auto", textWrap: "pretty" }}>{children}</div>
      ) : null}
      {action ? <div style={{ marginTop: 13, display: "flex", justifyContent: "center", gap: 6, flexWrap: "wrap" }}>{action}</div> : null}
    </div>
  );
}

export function Row({ k, children, top }) {
  return (
    <div style={{
      display: "flex", gap: 14, justifyContent: "space-between", alignItems: top ? "flex-start" : "baseline",
      padding: "8px 0", borderTop: `1px solid ${A.lineSoft}`,
    }}>
      <span style={{ fontFamily: TYPE.text, fontSize: 12, color: A.mute, flexShrink: 0 }}>{k}</span>
      <span style={{ fontFamily: TYPE.text, fontSize: 12.5, color: A.ink, textAlign: "right", minWidth: 0, overflowWrap: "anywhere" }}>{children}</span>
    </div>
  );
}

export function Field({ label, note, htmlFor, children, wide, error }) {
  return (
    <div style={{ minWidth: 0, gridColumn: wide ? "1 / -1" : "auto" }}>
      <label htmlFor={htmlFor} style={{ display: "block", fontFamily: TYPE.text, fontSize: 11, fontWeight: 600, color: A.body, marginBottom: 6, letterSpacing: ".02em" }}>{label}</label>
      {children}
      {error ? <div role="alert" style={{ fontFamily: TYPE.text, fontSize: 11.5, color: A.stop, marginTop: 5, lineHeight: 1.5 }}>{error}</div> : null}
      {!error && note ? <div style={{ fontFamily: TYPE.text, fontSize: 10.5, color: A.mute, marginTop: 5, lineHeight: 1.5, textWrap: "pretty" }}>{note}</div> : null}
    </div>
  );
}

export const inputStyle = {
  width: "100%", boxSizing: "border-box", fontFamily: TYPE.text, fontSize: 12.5, color: A.ink,
  background: A.paper, border: `1px solid ${A.line}`, borderRadius: 7, padding: "8px 10px", outline: "none",
  transition: "border-color .12s ease",
};

/* The second step of an action that cannot be undone: what it will do, in words and counts, then
   the button that does it. Cancel is first and quiet; the destructive button is last and solid. */
export function Confirm({ children, label, onConfirm, onCancel, busy, disabled }) {
  return (
    <div role="group" aria-label={label} style={{
      display: "flex", gap: "8px 12px", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap",
      background: A.stopBg, border: `1px solid ${A.stopLine}`, borderRadius: 8, padding: "9px 11px",
    }}>
      <span style={{ fontFamily: TYPE.text, fontSize: 12, color: A.ink, lineHeight: 1.55, flex: "1 1 240px", textWrap: "pretty" }}>{children}</span>
      <span style={{ display: "flex", gap: 6, flexShrink: 0 }}>
        <Btn small kind="quiet" onClick={onCancel} disabled={busy}>Cancel</Btn>
        <Btn small kind="dangerSolid" onClick={onConfirm} busy={busy} disabled={disabled}>{label}</Btn>
      </span>
    </div>
  );
}

/* An inline result line: what an action did, or why it did not. */
export function Notice({ tone = "info", children }) {
  if (!children) return null;
  const palette = tone === "error"
    ? { c: A.stop, bg: A.stopBg, line: A.stopLine }
    : tone === "warn" ? { c: A.warn, bg: A.warnBg, line: A.warnLine }
      : { c: A.body, bg: A.ground, line: A.line };
  return (
    <div role={tone === "error" ? "alert" : "status"} style={{
      fontFamily: TYPE.text, fontSize: 12, color: palette.c, background: palette.bg,
      border: `1px solid ${palette.line}`, borderRadius: 8, padding: "9px 11px", lineHeight: 1.55, textWrap: "pretty",
    }}>{children}</div>
  );
}

export function Loading({ label = "Loading" }) {
  return (
    <div role="status" style={{ padding: "28px 16px", fontFamily: TYPE.text, fontSize: 12, color: A.mute }}>{label}…</div>
  );
}

export function LoadError({ error, onRetry }) {
  return (
    <Card>
      <Empty title="This could not be loaded" action={onRetry ? <Btn small onClick={onRetry}>Try again</Btn> : null}>
        {error?.message || String(error)}
      </Empty>
    </Card>
  );
}

export function TabBar({ tabs, active, onPick }) {
  return (
    <div className="ac-tabs" role="tablist" style={{ display: "flex", gap: 2, overflowX: "auto", borderBottom: `1px solid ${A.line}`, marginBottom: 16 }}>
      {tabs.map((t) => (
        <button key={t.key} type="button" role="tab" aria-selected={active === t.key} onClick={() => onPick(t.key)} className="ac-tab" style={{
          fontFamily: TYPE.text, fontSize: 12.5, fontWeight: 600, whiteSpace: "nowrap", cursor: "pointer",
          background: "none", border: "none", borderBottom: `2px solid ${active === t.key ? A.ink : "transparent"}`,
          color: active === t.key ? A.ink : A.mute, padding: "9px 12px", marginBottom: -1,
          transition: "color .12s ease, border-color .12s ease",
        }}>{t.label}</button>
      ))}
    </div>
  );
}

/* Load once, reload on demand. Deliberately tiny: the console reads a handful of endpoints and
   refreshes after its own actions, which does not need a caching layer. */
export function useApi(fn, deps = []) {
  const [state, setState] = useState({ data: null, error: null, loading: true });
  const seq = useRef(0);
  const load = useCallback(() => {
    const mine = ++seq.current;
    setState((s) => ({ ...s, loading: true, error: null }));
    fn().then(
      (data) => { if (mine === seq.current) setState({ data, error: null, loading: false }); },
      (error) => { if (mine === seq.current) setState({ data: null, error, loading: false }); },
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  useEffect(() => { load(); }, [load]);
  return { ...state, reload: load };
}

/* An action button's lifecycle: busy while it runs, then a line saying what happened. */
export function useAction() {
  const [busy, setBusy] = useState(null);
  const [result, setResult] = useState(null);
  const run = useCallback(async (key, fn, success) => {
    setBusy(key);
    setResult(null);
    try {
      const out = await fn();
      setResult({ tone: "info", text: typeof success === "function" ? success(out) : success });
      return out;
    } catch (e) {
      setResult({ tone: "error", text: e?.message || String(e) });
      return null;
    } finally {
      setBusy(null);
    }
  }, []);
  return { busy, result, run, clear: () => setResult(null) };
}
