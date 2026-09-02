/* Settings → Appearance. Five colours, and everything else follows from them.
 *
 * The preview is the real thing rather than a picture of it: choosing a colour writes the derived
 * tokens straight onto the document, so the whole app repaints underneath the panel. A swatch
 * grid would let somebody pick five colours that look fine as five squares and terrible as a
 * dashboard, which is the mistake this screen exists to prevent.
 *
 * That means leaving without saving has to put the old colours back, and it does — see the
 * cleanup in the effect. A settings screen that permanently changes the app when you close it is
 * worse than one that does not preview at all.
 */
import { useEffect, useMemo, useRef, useState } from "react";

import { T, alpha } from "./theme.js";
import {
  SEEDS, SEED_META, applyBrand, applyPalette, contrast, contrastProblems, derive, seedsFromAcumyn,
} from "./palette.js";
import { getJSON, patchJSON } from "./api.js";

const FONT = "Inter,sans-serif";
const HEAD = "Poppins,sans-serif";

function Swatch({ name, value, onChange, disabled }) {
  const meta = SEED_META[name];
  return (
    <label style={{ display: "block", marginBottom: 16, opacity: disabled ? 0.5 : 1 }}>
      <div style={{ fontFamily: FONT, fontSize: 12.5, fontWeight: 600, color: T.ink }}>
        {meta.label}
      </div>
      <div style={{ fontFamily: FONT, fontSize: 11.5, color: T.muted, margin: "2px 0 7px" }}>
        {meta.hint}
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
        <input type="color" value={value} disabled={disabled}
               onChange={(e) => onChange(name, e.target.value.toUpperCase())}
               aria-label={meta.label}
               style={{ width: 42, height: 30, padding: 0, border: `1px solid ${T.line}`,
                        borderRadius: 7, background: "none", cursor: disabled ? "default" : "pointer" }} />
        <input type="text" value={value} disabled={disabled}
               onChange={(e) => {
                 const v = e.target.value.trim().toUpperCase();
                 if (/^#[0-9A-F]{6}$/.test(v)) onChange(name, v);
               }}
               aria-label={`${meta.label} hex`}
               style={{ width: 104, padding: "6px 9px", borderRadius: 7, fontFamily: "Archivo,sans-serif",
                        fontSize: 12.5, border: `1px solid ${T.line}`, color: T.ink,
                        background: T.white }} />
      </div>
    </label>
  );
}

/* What the derived palette looks like as a strip. Not a picker — the twenty-five are not
   editable, and showing them is how somebody understands that five choices reached everywhere. */
function Derived({ tokens }) {
  const shown = ["evergreen", "poppy", "poppyActive", "petal", "mist", "meadow", "meadowBg",
                 "poppyText", "daffodil", "line", "muted", "page"];
  return (
    <div>
      <div style={{ fontFamily: FONT, fontSize: 11, fontWeight: 700, letterSpacing: ".07em",
                    textTransform: "uppercase", color: T.muted, marginBottom: 8 }}>
        Derived from those five
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
        {shown.map((k) => (
          <div key={k} title={`${k} · ${tokens[k]}`} style={{
            width: 34, height: 34, borderRadius: 7, background: tokens[k],
            border: `1px solid ${alpha(T.ink, 0.12)}`,
          }} />
        ))}
      </div>
    </div>
  );
}

export default function Appearance() {
  const [seeds, setSeeds] = useState(seedsFromAcumyn);
  const [allowed, setAllowed] = useState(true);
  const [plan, setPlan] = useState(null);
  const [saved, setSaved] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const original = useRef(null);

  useEffect(() => {
    let live = true;
    getJSON("/settings/appearance").then((r) => {
      if (!live || !r) return;
      setAllowed(!!r.allowed);
      setPlan(r.plan || null);
      const s = { ...seedsFromAcumyn(), ...(r.seeds || {}) };
      setSeeds(s);
      setSaved(JSON.stringify(s));
      original.current = r;
    }).catch(() => setErr("Couldn't load appearance settings."));
    return () => {
      live = false;
      // Leaving without saving puts the workspace's real colours back. The preview writes to the
      // document, so without this a cancelled edit would follow you around the app.
      if (original.current) applyBrand(original.current.brand || { seeds: original.current.seeds });
    };
  }, []);

  // The preview: derived tokens straight onto the document, so the app under this panel repaints.
  useEffect(() => { if (allowed) applyPalette(derive(seeds)); }, [seeds, allowed]);

  const tokens = useMemo(() => derive(seeds), [seeds]);
  const problems = useMemo(() => contrastProblems(seeds), [seeds]);
  const dirty = saved !== null && JSON.stringify(seeds) !== saved;

  function change(name, value) { setSeeds((s) => ({ ...s, [name]: value })); }

  async function save() {
    if (problems.length) return;
    setBusy(true); setErr(null);
    try {
      await patchJSON("/settings/appearance", { seeds });
      setSaved(JSON.stringify(seeds));
      original.current = { brand: { seeds } };
    } catch (e) {
      setErr(String(e && e.message ? e.message : e));
    } finally { setBusy(false); }
  }

  function reset() { setSeeds(seedsFromAcumyn()); }

  if (!allowed) {
    return (
      <div style={{ background: T.white, border: `1px solid ${T.line}`, borderRadius: 14,
                    padding: 22, maxWidth: 620 }}>
        <div style={{ fontFamily: HEAD, fontSize: 16, fontWeight: 700, color: T.ink }}>Appearance</div>
        <p style={{ fontFamily: FONT, fontSize: 13.5, color: T.secondary, lineHeight: 1.6 }}>
          Your workspace uses Acumyn's colours. Choosing your own is included from the Business
          plan{plan ? ` — you're on ${plan.name}` : ""}.
        </p>
      </div>
    );
  }

  return (
    <div style={{ display: "grid", gridTemplateColumns: "minmax(0,320px) minmax(0,1fr)", gap: 26,
                  alignItems: "start" }}>
      <div style={{ background: T.white, border: `1px solid ${T.line}`, borderRadius: 14, padding: 22 }}>
        <div style={{ fontFamily: HEAD, fontSize: 16, fontWeight: 700, color: T.ink, marginBottom: 4 }}>
          Appearance
        </div>
        <p style={{ fontFamily: FONT, fontSize: 12.5, color: T.muted, lineHeight: 1.55,
                    margin: "0 0 18px" }}>
          Pick five colours. Everything else on screen is worked out from them, so the whole
          dashboard stays consistent — including screens you haven't opened.
        </p>

        {SEEDS.map((k) => (
          <Swatch key={k} name={k} value={seeds[k]} onChange={change} />
        ))}

        {problems.length > 0 && (
          <div role="alert" style={{ background: T.daffodilBg, border: `1px solid ${T.daffodil}`,
                                     borderRadius: 10, padding: "11px 13px", marginBottom: 14 }}>
            <div style={{ fontFamily: FONT, fontSize: 12.5, fontWeight: 600, color: T.daffodilText }}>
              {problems.length === 1 ? "One thing" : `${problems.length} things`} would be hard to read
            </div>
            <ul style={{ margin: "6px 0 0 16px", padding: 0, fontFamily: FONT, fontSize: 12,
                         color: T.daffodilText, lineHeight: 1.6 }}>
              {problems.map((p) => (
                <li key={p.what}>{p.what} — {p.ratio}:1, needs {p.min}:1</li>
              ))}
            </ul>
          </div>
        )}

        <div style={{ display: "flex", gap: 9, alignItems: "center" }}>
          <button onClick={save} disabled={busy || !dirty || problems.length > 0}
                  style={{ background: problems.length ? T.sprout : T.poppy, color: T.white,
                           border: "none", borderRadius: 9, padding: "9px 16px", fontFamily: FONT,
                           fontSize: 13, fontWeight: 600,
                           cursor: (busy || !dirty || problems.length) ? "default" : "pointer" }}>
            {busy ? "Saving…" : "Save"}
          </button>
          <button onClick={reset} style={{ background: "none", border: `1px solid ${T.line}`,
                                           borderRadius: 9, padding: "9px 14px", fontFamily: FONT,
                                           fontSize: 13, color: T.slate, cursor: "pointer" }}>
            Use Acumyn's
          </button>
          {problems.length > 0 && (
            <span style={{ fontFamily: FONT, fontSize: 11.5, color: T.muted }}>
              Fix the contrast to save
            </span>
          )}
        </div>
        {err && <div style={{ marginTop: 10, fontFamily: FONT, fontSize: 12.5, color: T.poppyText }}>{err}</div>}
      </div>

      <div style={{ display: "grid", gap: 18 }}>
        <div style={{ background: T.white, border: `1px solid ${T.line}`, borderRadius: 14, padding: 20 }}>
          <Derived tokens={tokens} />
        </div>
        <div style={{ background: T.white, border: `1px solid ${T.line}`, borderRadius: 14,
                      padding: 20 }}>
          <div style={{ fontFamily: FONT, fontSize: 11, fontWeight: 700, letterSpacing: ".07em",
                        textTransform: "uppercase", color: T.muted, marginBottom: 12 }}>
            Contrast
          </div>
          {[["Body text", tokens.ink, tokens.page],
            ["Labels", tokens.muted, tokens.page],
            ["Button text", tokens.white, tokens.poppy],
            ["Links", tokens.teal, tokens.page],
            ["On a dark panel", tokens.onDark, tokens.evergreen]].map(([label, fg, bg]) => {
            const r = contrast(fg, bg);
            return (
              <div key={label} style={{ display: "flex", alignItems: "center", gap: 10,
                                        marginBottom: 7 }}>
                <span style={{ background: bg, color: fg, fontFamily: FONT, fontSize: 12.5,
                               padding: "5px 10px", borderRadius: 7, minWidth: 128,
                               border: `1px solid ${alpha(T.ink, 0.1)}` }}>{label}</span>
                <span style={{ fontFamily: "Archivo,sans-serif", fontSize: 12,
                               color: r >= 4.5 ? T.meadowInk : T.daffodilText }}>
                  {Math.round(r * 100) / 100}:1
                </span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
