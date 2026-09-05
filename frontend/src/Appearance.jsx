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
  SEEDS, SEED_META, applyBrand, applyPalette, applyType, contrast, contrastProblems, derive,
  seedsFromAcumyn, seedsFromPalette, resetBrandOnce,
} from "./palette.js";
import { getJSON, patchJSON, API_BASE, authHeaders, fileUrl } from "./api.js";
import { PAIRINGS, DEFAULT_PAIRING, loadTypeface, stacks } from "./typefaces.js";

const FONT = "var(--font-text)";
const HEAD = "var(--font-display)";

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
               style={{ width: 104, padding: "6px 9px", borderRadius: 7, fontFamily: "var(--font-data)",
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

/* One mark. Shows what is actually there rather than an empty file input — the previous version
   offered "Choose File / No file chosen" and a Remove button that was present even when there
   was nothing to remove, so the panel could not tell you whether an upload had worked. */
function MarkSlot({ kind, label, hint, url, busy, onPick, onClear }) {
  const [over, setOver] = useState(false);
  const input = useRef(null);
  const accept = "image/png,image/svg+xml,image/jpeg,image/webp";

  function pick(files) {
    const f = files && files[0];
    if (f) onPick(f);
  }

  return (
    <div style={{ display: "flex", gap: 12, alignItems: "center", padding: "10px 0",
                  borderTop: `1px solid ${T.line}` }}>
      {/* The preview is masked exactly the way the app renders it, so what you see here is what
          the header will show — including the tint. A raw <img> would look right and then
          surprise somebody the moment it landed on a dark band. */}
      <div aria-hidden style={{
        width: 66, height: 40, borderRadius: 8, flexShrink: 0,
        border: `1px dashed ${over ? T.poppy : T.line}`,
        background: over ? T.mist : T.parchment,
        display: "flex", alignItems: "center", justifyContent: "center", overflow: "hidden",
      }}>
        {url ? (
          <span style={{
            width: 52, height: 26, backgroundColor: T.evergreen,
            WebkitMaskImage: `url(${url})`, maskImage: `url(${url})`,
            WebkitMaskSize: "contain", maskSize: "contain",
            WebkitMaskRepeat: "no-repeat", maskRepeat: "no-repeat",
            WebkitMaskPosition: "center", maskPosition: "center",
          }} />
        ) : (
          <span style={{ fontFamily: FONT, fontSize: 10.5, color: T.muted }}>none</span>
        )}
      </div>

      <div style={{ minWidth: 0, flex: 1 }}>
        <div style={{ fontFamily: FONT, fontSize: 12.5, fontWeight: 600, color: T.ink }}>{label}</div>
        <div style={{ fontFamily: FONT, fontSize: 11, color: T.muted }}>{hint}</div>
      </div>

      <div style={{ display: "flex", gap: 6, alignItems: "center", flexShrink: 0 }}
           onDragOver={(e) => { e.preventDefault(); setOver(true); }}
           onDragLeave={() => setOver(false)}
           onDrop={(e) => { e.preventDefault(); setOver(false); pick(e.dataTransfer.files); }}>
        <input ref={input} type="file" accept={accept} hidden
               onChange={(e) => { pick(e.target.files); e.target.value = ""; }} />
        <button type="button" onClick={() => input.current && input.current.click()} disabled={busy}
                style={{ background: T.white, border: `1px solid ${T.line}`, borderRadius: 8,
                         padding: "6px 11px", fontFamily: FONT, fontSize: 12, color: T.slate,
                         cursor: busy ? "default" : "pointer" }}>
          {busy ? "Uploading…" : url ? "Replace" : "Upload"}
        </button>
        {/* Only when there IS one. A Remove button next to nothing is a button that lies. */}
        {url && !busy && (
          <button type="button" onClick={onClear}
                  style={{ background: "none", border: "none", fontFamily: FONT, fontSize: 11.5,
                           color: T.muted, cursor: "pointer", padding: "6px 2px" }}>
            Remove
          </button>
        )}
      </div>
    </div>
  );
}

export default function Appearance() {
  const [seeds, setSeeds] = useState(seedsFromAcumyn);
  const [typeface, setTypeface] = useState(DEFAULT_PAIRING);
  // The sign-in screen. It renders before there is a session, so none of this can come from
  // /me — it rides on /public/brand and is set here.
  const [signIn, setSignIn] = useState(
    { tagline: "", plate_side: "left", button_shape: "pill", remember_me: true });
  const [marks, setMarks] = useState({ logo: null, logomark: null });
  const [upErr, setUpErr] = useState(null);
  const [upBusy, setUpBusy] = useState(null);
  const [hadExplicit, setHadExplicit] = useState(false);
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
      // Open on THIS workspace's colours. A workspace with a hand-built palette and no seeds
      // gets seeds read back out of that palette, so the panel shows what they actually have
      // rather than the platform's defaults with a Save button next to them.
      const explicit = r.palette && Object.keys(r.palette).length ? r.palette : null;
      const s = (r.seeds && Object.keys(r.seeds).length)
        ? { ...seedsFromAcumyn(), ...r.seeds }
        : seedsFromPalette(explicit);
      setHadExplicit(!!explicit);
      setSeeds(s);
      setTypeface(r.typeface || DEFAULT_PAIRING);
      const sign = {
        tagline: r.tagline || "",
        plate_side: r.plate_side || "left",
        button_shape: r.button_shape || "pill",
        remember_me: r.remember_me !== false,
      };
      setSignIn(sign);
      setMarks({ logo: fileUrl(r.logo), logomark: fileUrl(r.logomark) });
      setSaved(JSON.stringify({ seeds: s, typeface: r.typeface || DEFAULT_PAIRING, sign }));
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
  // Type previews the same way the colours do, and requests the faces so the preview is the
  // real thing rather than a fallback pretending to be it.
  useEffect(() => {
    if (!allowed) return;
    applyType(stacks(typeface));
    loadTypeface(typeface);
  }, [typeface, allowed]);

  const tokens = useMemo(() => derive(seeds), [seeds]);
  // Contrast is only a reason to block the SAVE when the seeds are what is being saved.
  //
  // A workspace with a hand-built palette renders from that palette, not from these five. The
  // five are a read-back approximation, so judging them and then disabling Save meant the panel
  // refused to change ANYTHING — including the typeface, which has nothing to do with colour.
  // The workspace was locked out of its own settings by a warning about a palette it does not use.
  const problems = useMemo(() => contrastProblems(seeds), [seeds]);
  const savedSeeds = saved ? JSON.parse(saved).seeds : null;
  const seedsTouched = !!savedSeeds && JSON.stringify(seeds) !== JSON.stringify(savedSeeds);
  const blocking = problems.length > 0 && (seedsTouched || !hadExplicit);
  const dirty = saved !== null
    && JSON.stringify({ seeds, typeface, sign: signIn }) !== saved;

  function change(name, value) { setSeeds((s) => ({ ...s, [name]: value })); }

  async function save() {
    if (blocking) return;
    setBusy(true); setErr(null);
    try {
      await patchJSON("/settings/appearance", { seeds, typeface, ...signIn });
      setSaved(JSON.stringify({ seeds, typeface, sign: signIn }));
      original.current = { brand: { seeds, typeface } };
      // The root memoised the OLD identity. Without this the next route would re-apply it and
      // the change would appear to undo itself.
      resetBrandOnce();
    } catch (e) {
      setErr(String(e && e.message ? e.message : e));
    } finally { setBusy(false); }
  }

  function reset() {
    setSeeds(seedsFromAcumyn());
    setTypeface(DEFAULT_PAIRING);
    setSignIn({ tagline: "", plate_side: "left", button_shape: "pill", remember_me: true });
  }

  const setSign = (k, v) => setSignIn((s) => ({ ...s, [k]: v }));

  async function uploadMark(kind, file) {
    setUpErr(null);
    setUpBusy(kind);
    const form = new FormData();
    form.append("kind", kind);
    form.append("file", file);
    try {
      const r = await fetch(`${API_BASE}/settings/appearance/logo`,
                            { method: "POST", headers: authHeaders("/settings/appearance/logo"), body: form });
      const body = await r.json();
      if (!r.ok) throw new Error(body.detail || "Upload failed");
      // The API returns a server-relative path; the preview needs an absolute one, same as the
      // header does.
      setMarks((m) => ({ ...m, [kind]: fileUrl(body.url) }));
    } catch (e) { setUpErr(String(e.message || e)); }
    finally { setUpBusy(null); }
  }

  async function clearMark(kind) {
    try {
      await fetch(`${API_BASE}/settings/appearance/logo?kind=${kind}`,
                  { method: "DELETE", headers: authHeaders("/settings/appearance/logo") });
      setMarks((m) => ({ ...m, [kind]: null }));
    } catch { /* leave it */ }
  }

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

        <div style={{ margin: "6px 0 18px" }}>
          <div style={{ fontFamily: FONT, fontSize: 12.5, fontWeight: 600, color: T.ink }}>
            Typeface
          </div>
          <div style={{ fontFamily: FONT, fontSize: 11.5, color: T.muted, margin: "2px 0 8px" }}>
            Figures always stay in Archivo — it has the tabular numbers that keep a column
            aligned.
          </div>
          {Object.entries(PAIRINGS).map(([key, p]) => (
            <label key={key} style={{ display: "flex", gap: 9, alignItems: "flex-start",
                                      padding: "7px 9px", borderRadius: 9, cursor: "pointer",
                                      border: `1px solid ${typeface === key ? T.poppy : T.line}`,
                                      background: typeface === key ? T.mist : T.white,
                                      marginBottom: 6 }}>
              <input type="radio" name="typeface" checked={typeface === key}
                     onChange={() => setTypeface(key)} style={{ marginTop: 3 }} />
              <span>
                <span style={{ fontFamily: p.display, fontSize: 14.5, fontWeight: 700,
                               color: T.ink }}>{p.label}</span>
                <span style={{ display: "block", fontFamily: FONT, fontSize: 11.5,
                               color: T.muted }}>{p.note}</span>
              </span>
            </label>
          ))}
        </div>

        <div style={{ margin: "0 0 18px" }}>
          <div style={{ fontFamily: FONT, fontSize: 12.5, fontWeight: 600, color: T.ink }}>
            Sign-in screen
          </div>
          <div style={{ fontFamily: FONT, fontSize: 11.5, color: T.muted, margin: "2px 0 10px",
                        lineHeight: 1.5 }}>
            The first thing anyone sees. Your mark and hero image already fill the dark plate;
            these are the rest of it.
          </div>

          <label style={{ display: "block", marginBottom: 12 }}>
            <div style={{ fontFamily: FONT, fontSize: 12, fontWeight: 600, color: T.slate }}>
              Tagline <span style={{ fontWeight: 400, color: T.muted }}>· optional</span>
            </div>
            <div style={{ fontFamily: FONT, fontSize: 11.5, color: T.muted, margin: "2px 0 6px" }}>
              One line on the plate. Left empty, no line is shown — better than a claim we
              invented for you.
            </div>
            <input value={signIn.tagline} maxLength={90}
                   onChange={(e) => setSign("tagline", e.target.value)}
                   placeholder="Track production, not spreadsheets"
                   style={{ width: "100%", boxSizing: "border-box", padding: "9px 11px",
                            borderRadius: 8, fontFamily: FONT, fontSize: 13,
                            border: `1px solid ${T.line}`, color: T.ink, background: T.white }} />
          </label>

          {[["plate_side", "Plate on the", [["left", "Left"], ["right", "Right"]]],
            ["button_shape", "Buttons", [["pill", "Pill"], ["square", "Square"]]]].map(
            ([key, label, options]) => (
              <div key={key} style={{ display: "flex", alignItems: "center", gap: 10,
                                      marginBottom: 10 }}>
                <div style={{ fontFamily: FONT, fontSize: 12, fontWeight: 600, color: T.slate,
                              width: 96 }}>{label}</div>
                <div style={{ display: "flex", gap: 6 }}>
                  {options.map(([value, text]) => (
                    <button key={value} type="button" onClick={() => setSign(key, value)}
                            style={{ fontFamily: FONT, fontSize: 12, padding: "6px 12px",
                                     borderRadius: key === "button_shape" && value === "pill"
                                       ? 999 : 8,
                                     cursor: "pointer",
                                     border: `1px solid ${signIn[key] === value ? T.poppy : T.line}`,
                                     background: signIn[key] === value ? T.mist : T.white,
                                     color: T.ink }}>{text}</button>
                  ))}
                </div>
              </div>
            ))}

          <label style={{ display: "flex", alignItems: "flex-start", gap: 9, cursor: "pointer",
                          fontFamily: FONT, fontSize: 12.5, color: T.ink, marginTop: 4 }}>
            <input type="checkbox" checked={signIn.remember_me} style={{ marginTop: 2 }}
                   onChange={(e) => setSign("remember_me", e.target.checked)} />
            <span>
              Offer &ldquo;Remember me&rdquo;
              <span style={{ display: "block", fontFamily: FONT, fontSize: 11.5, color: T.muted }}>
                Ticked, a session lasts a month; left unticked by the user, it ends when the
                browser closes. Hide this and every session is remembered.
              </span>
            </span>
          </label>
        </div>

        <div style={{ margin: "0 0 18px" }}>
          <div style={{ fontFamily: FONT, fontSize: 12.5, fontWeight: 600, color: T.ink }}>
            Your mark
          </div>
          <div style={{ fontFamily: FONT, fontSize: 11.5, color: T.muted, margin: "2px 0 10px",
                        lineHeight: 1.5 }}>
            A transparent PNG or SVG, under 512KB. It is tinted to your palette, so one file
            works on every background. Without one, your workspace name is used.
          </div>
          {[["logo", "Wordmark", "the full name, for headers and hero bands"],
            ["logomark", "Logomark", "the bare mark, for tight spaces"]].map(([kind, label, hint]) => (
            <MarkSlot key={kind} kind={kind} label={label} hint={hint}
                      url={marks[kind]} busy={upBusy === kind}
                      onPick={(f) => uploadMark(kind, f)} onClear={() => clearMark(kind)} />
          ))}
          {upErr && (
            <div role="alert" style={{ fontFamily: FONT, fontSize: 12, color: T.poppyText,
                                       marginTop: 6 }}>{upErr}</div>
          )}
        </div>

        {problems.length > 0 && (
          <div role="alert" style={{ background: T.daffodilBg, border: `1px solid ${T.daffodil}`,
                                     borderRadius: 10, padding: "11px 13px", marginBottom: 14 }}>
            <div style={{ fontFamily: FONT, fontSize: 12.5, fontWeight: 600, color: T.daffodilText }}>
              {problems.length === 1 ? "One thing" : `${problems.length} things`} would be hard to read
              {hadExplicit && !seedsTouched ? " if you switched to these five" : ""}
            </div>
            <ul style={{ margin: "6px 0 0 16px", padding: 0, fontFamily: FONT, fontSize: 12,
                         color: T.daffodilText, lineHeight: 1.6 }}>
              {problems.map((p) => (
                <li key={p.what}>{p.what} — {p.ratio}:1, needs {p.min}:1</li>
              ))}
            </ul>
          </div>
        )}

        {hadExplicit && dirty && (
          <div role="note" style={{ background: T.mist, border: `1px solid ${T.petal}`,
                                    borderRadius: 10, padding: "10px 12px", marginBottom: 12,
                                    fontFamily: FONT, fontSize: 12, color: T.slate,
                                    lineHeight: 1.55 }}>
            This workspace has a hand-built palette. Saving replaces it with these five colours
            and the twenty-five worked out from them — close, but not identical.
          </div>
        )}
        <div style={{ display: "flex", gap: 9, alignItems: "center" }}>
          <button onClick={save} disabled={busy || !dirty || blocking}
                  style={{ background: blocking ? T.sprout : T.poppy, color: T.white,
                           border: "none", borderRadius: 9, padding: "9px 16px", fontFamily: FONT,
                           fontSize: 13, fontWeight: 600,
                           cursor: (busy || !dirty || blocking) ? "default" : "pointer" }}>
            {busy ? "Saving…" : "Save"}
          </button>
          <button onClick={reset} style={{ background: "none", border: `1px solid ${T.line}`,
                                           borderRadius: 9, padding: "9px 14px", fontFamily: FONT,
                                           fontSize: 13, color: T.slate, cursor: "pointer" }}>
            Use Acumyn's
          </button>
          {blocking && (
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
                <span style={{ fontFamily: "var(--font-data)", fontSize: 12,
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
