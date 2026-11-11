/* The campaign grouping editor. SPEC-ads-module.md Part 10.3.
 *
 * Grouping decides which business line a campaign's spend belongs to. It shipped as data on the
 * account with no way to edit it, which meant a new campaign landed in Other the moment somebody
 * created one and stayed there until an engineer opened a database. For a product that is the
 * wrong shape: the person who NAMES the campaigns is the person who should be able to say where
 * they go.
 *
 * The live failure that motivated this is worth stating, because it is what the preview exists
 * to prevent: an earlier default matched the prefix `kb-`, the account names its campaigns
 * `KB - The Shift - August2026`, and two spaces meant all nineteen fell to Other and the whole
 * "where it came from" panel became one bar. Nobody could have seen that by reading the rules.
 * The shipped default is now brand and event (Forum Dallas, Forum Scottsdale, the Shift, Edge).
 *
 * So the preview is the feature. Every edit asks the SERVER what that rule set would do and
 * shows the answer beside the rule - classifying in the browser would mean two implementations
 * of the same logic in two languages, and the one on screen would be the untested one.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { API_BASE, getJSON, patchJSON, postJSON } from "../api";
import { C, usd } from "./adsTokens.js";
import { sampleAdsGrouping } from "./sampleAds.js";
import { Glyph } from "./ui.jsx";

const BLANK = { match: "prefix", value: "", label: "", split: false };

/* The module's own button, so Save here is the same filled button as Save everywhere else in
   the tab rather than a second, teal one. `glyph` makes it a square icon button. */
function Btn({ onClick, children, tone, disabled, title, glyph }) {
  const cls = `ax-btn${tone === "primary" ? " ax-btn--primary" : ""}${glyph ? " ax-btn--icon" : ""}`;
  return (
    <button type="button" className={cls} onClick={onClick} disabled={disabled} title={title}
            aria-label={glyph ? title : undefined}>
      {glyph ? <Glyph name={glyph} /> : children}
    </button>
  );
}

function RuleRow({ rule, index, count, onChange, onRemove, onMove }) {
  const set = (patch) => onChange({ ...rule, ...patch });
  return (
    /* The column template lives in the stylesheet, NOT here. An inline style attribute beats a
       stylesheet rule without !important, so a media query cannot override one - at 375px the
       desktop template stayed in force and both text inputs computed to ZERO WIDTH. Found by
       measuring rather than by looking, because a 0px input is invisible in every sense. */
    <div className="ads-rule"
         style={{ padding: "8px 0",
                  borderTop: index === 0 ? "none" : `1px solid ${C.line}` }}>
      <select value={rule.match} onChange={(e) => set({ match: e.target.value })}
              aria-label={`Rule ${index + 1} match type`} className="ax-input">
        <option value="prefix">starts with</option>
        <option value="contains">contains</option>
      </select>

      <input value={rule.value || ""} onChange={(e) => set({ value: e.target.value })}
             placeholder="KB - " aria-label={`Rule ${index + 1} text to match`}
             className="ax-input ax-num" />

      <input value={rule.label || ""} onChange={(e) => set({ label: e.target.value })}
             placeholder="Group name" aria-label={`Rule ${index + 1} group name`}
             className="ax-input" />

      {/* Only meaningful for a prefix. `split` puts the token AFTER the prefix into the label,
          so one rule yields "KB · The Shift" and "KB · Upgrade" instead of collapsing every KB
          campaign into a single bucket. */}
      <label title={rule.match === "prefix"
        ? "Add the next word to the group name, so one rule can produce several groups"
        : "Only applies to a starts-with rule"}
             className="ax-meta"
             style={{ display: "flex", alignItems: "center", gap: 6,
                      color: rule.match === "prefix" ? C.slate : C.muted, whiteSpace: "nowrap" }}>
        <input type="checkbox" checked={!!rule.split} disabled={rule.match !== "prefix"}
               onChange={(e) => set({ split: e.target.checked })} />
        split
      </label>

      <span style={{ display: "flex", gap: 4 }}>
        <Btn onClick={() => onMove(index, -1)} disabled={index === 0} glyph="up"
             title="Move up: the first matching rule wins" />
        <Btn onClick={() => onMove(index, 1)} disabled={index === count - 1} glyph="down"
             title="Move down" />
        <Btn onClick={() => onRemove(index)} glyph="close" title="Remove this rule" />
      </span>
    </div>
  );
}

export default function GroupingRules({ account, onSaved }) {
  /* Role is resolved here rather than threaded down from the shell, and it FAILS CLOSED: if /me
     cannot be read the editor renders read-only. This gate is courtesy, not security - the PATCH
     is owner/admin on the server and a member gets a 403 whatever the browser believes - but
     letting somebody fill in a form that is going to be refused is its own small cruelty. */
  const [canEdit, setCanEdit] = useState(false);
  const [loaded, setLoaded] = useState(null);      // the saved state, for Cancel
  const [rules, setRules] = useState([]);
  const [usingDefaults, setUsingDefaults] = useState(true);
  const [preview, setPreview] = useState(null);
  const [problems, setProblems] = useState([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [open, setOpen] = useState(false);
  const timer = useRef(null);

  const load = useCallback(async () => {
    try {
      const q = account ? `?account=${encodeURIComponent(account)}` : "";
      // Offline preview has no API. Show the bundled sample so the editor is inspectable
      // rather than a permanent error box; saving is disabled below for the same reason.
      const d = API_BASE ? await getJSON(`/ads/grouping${q}`) : sampleAdsGrouping;
      const start = d.rules ?? d.defaults ?? [];
      setLoaded(d);
      setRules(start.map((r) => ({ ...BLANK, ...r })));
      setUsingDefaults(d.using_defaults !== false);
      setPreview({ campaigns: d.campaigns || [], unmatched: d.unmatched || 0 });
      setError(null);
    } catch (e) {
      setError(String(e?.message || e));
    }
  }, [account]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    let alive = true;
    if (!API_BASE) { setCanEdit(false); return () => { alive = false; }; }
    getJSON("/me")
      .then((u) => { if (alive) setCanEdit(u?.role === "owner" || u?.role === "admin"); })
      .catch(() => { if (alive) setCanEdit(false); });
    return () => { alive = false; };
  }, []);

  /* Debounced, and the timer is cleared on unmount and before each new edit: typing a rule fires
     one request per keystroke otherwise, and the replies can land out of order so an older
     preview overwrites a newer one. */
  useEffect(() => {
    if (!open || loaded === null) return undefined;
    clearTimeout(timer.current);
    let stale = false;
    timer.current = setTimeout(async () => {
      try {
        if (!API_BASE) return;                 // nothing to preview against offline
        const q = account ? `?account=${encodeURIComponent(account)}` : "";
        const d = await postJSON(`/ads/grouping/preview${q}`, { rules });
        if (stale) return;
        setPreview({ campaigns: d.campaigns || [], unmatched: d.unmatched || 0 });
        setProblems(d.problems || []);
      } catch (e) {
        if (!stale) setError(String(e?.message || e));
      }
    }, 300);
    return () => { stale = true; clearTimeout(timer.current); };
  }, [rules, open, account, loaded]);

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      await patchJSON(`/ads/accounts/${loaded.account}`, { group_rules: rules });
      await load();
      setOpen(false);
      onSaved?.();
    } catch (e) {
      setError(String(e?.message || e));
    } finally {
      setSaving(false);
    }
  };

  const revert = async () => {
    setSaving(true);
    try {
      await patchJSON(`/ads/accounts/${loaded.account}`, { group_rules: null });
      await load();
      onSaved?.();
    } catch (e) {
      setError(String(e?.message || e));
    } finally {
      setSaving(false);
    }
  };

  const move = (i, d) => setRules((rs) => {
    const next = [...rs];
    const j = i + d;
    if (j < 0 || j >= next.length) return rs;
    [next[i], next[j]] = [next[j], next[i]];
    return next;
  });

  if (loaded && loaded.connected === false) return null;

  const unmatched = preview?.unmatched ?? 0;
  const groups = {};
  for (const c of preview?.campaigns || []) {
    groups[c.group] = groups[c.group] || { n: 0, spend: 0 };
    groups[c.group].n += 1;
    groups[c.group].spend += c.spend || 0;
  }
  const ordered = Object.entries(groups).sort((a, b) => b[1].spend - a[1].spend);

  return (
    <>
      <style>{`
        .ads-rule { display: grid; gap: 8px; align-items: center;
                    grid-template-columns: 104px minmax(0,1.4fr) minmax(0,1fr) auto auto; }
        /* width:100% plus padding plus a border is WIDER than the track under the default
           content-box, so each field overflowed its own grid cell - 132px of input in a 113px
           column at 375px. There is no global border-box reset in this app to fall back on. */
        .ads-rule select, .ads-rule input { box-sizing: border-box; max-width: 100%; }
        @media (max-width: 720px) {
          /* Two columns, and the match/value pair keeps the top row: a rule is unreadable
             without the text it matches on. */
          .ads-rule { grid-template-columns: minmax(0,1fr) minmax(0,1fr); }
          .ads-rule > *:nth-child(4) { justify-self: start; }
          .ads-rule > *:nth-child(5) { justify-self: end; }
        }
      `}</style>

      <div className="ax-row" style={{ gap: 12 }}>
        <Btn onClick={() => setOpen((o) => !o)}>
          {open ? "Close grouping rules" : "Edit grouping rules"}
        </Btn>
        <span className="ax-meta">
          {usingDefaults ? "Using the built-in rules." : "Using this workspace's own rules."}
          {unmatched > 0 && ` ${unmatched} campaign${unmatched === 1 ? "" : "s"} in Other.`}
        </span>
      </div>

      {open && (
        <div style={{ borderTop: `1px solid ${C.line}`, paddingTop: 16, marginTop: 16 }}>
          {!canEdit && (
            <p className="ax-meta" style={{ margin: "0 0 12px" }}>
              You can see how campaigns are grouped. Changing the rules moves revenue between
              business lines, so it needs an owner or admin.
            </p>
          )}

          <h4 className="ax-label" style={{ margin: 0, color: C.ink, fontWeight: 600 }}>Rules</h4>
          <p className="ax-meta" style={{ margin: "4px 0 8px" }}>
            Checked top to bottom; the first one that matches wins. Anything matching nothing
            falls to Other, which is counted rather than hidden.
          </p>

          {rules.map((r, i) => (
            <RuleRow key={i} rule={r} index={i} count={rules.length}
                     onChange={(next) => setRules((rs) => rs.map((x, j) => (j === i ? next : x)))}
                     onRemove={(j) => setRules((rs) => rs.filter((_, k) => k !== j))}
                     onMove={move} />
          ))}

          <div className="ax-row" style={{ marginTop: 12 }}>
            <Btn onClick={() => setRules((rs) => [...rs, { ...BLANK }])}>Add a rule</Btn>
            {canEdit && API_BASE && (
              <>
                <Btn tone="primary" onClick={save} disabled={saving || problems.length > 0}>
                  {saving ? "Saving…" : "Save"}
                </Btn>
                <Btn onClick={load} disabled={saving}>Cancel</Btn>
                <Btn onClick={revert} disabled={saving || usingDefaults}
                     title="Go back to the built-in rules">Reset to defaults</Btn>
              </>
            )}
          </div>

          {problems.length > 0 && (
            <ul className="ax-meta" style={{ color: C.badInk, margin: "10px 0 0", paddingLeft: 18 }}>
              {problems.map((p) => <li key={p}>{p}</li>)}
            </ul>
          )}
          {error && (
            <div className="ax-meta" style={{ color: C.badInk, marginTop: 10 }}>
              {error}
            </div>
          )}

          <h4 className="ax-label" style={{ margin: "24px 0 0", color: C.ink, fontWeight: 600 }}>
            What this would do
          </h4>
          <p className="ax-meta" style={{ margin: "4px 0 12px" }}>
            Live, and not saved until you press Save.
          </p>

          <div style={{ display: "flex", gap: 7, flexWrap: "wrap", marginBottom: 12 }}>
            {ordered.map(([g, v]) => (
              <span key={g}
                    className="ax-meta"
                    style={{ borderRadius: 999, padding: "2px 10px",
                             color: g === "Other" ? C.warnInk : C.slate,
                             background: g === "Other" ? C.warnBg : C.surface2,
                             border: `1px solid ${g === "Other" ? C.warnBar : C.line}` }}>
                {g} · {v.n}
              </span>
            ))}
          </div>

          <div style={{ display: "grid", gap: 0, maxHeight: 260, overflowY: "auto" }}>
            {(preview?.campaigns || []).map((c) => (
              <div key={c.id}
                   style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) 130px 90px",
                            gap: 10, alignItems: "baseline", padding: "6px 0",
                            borderTop: `1px solid ${C.line}` }}>
                <span title={c.name} className="ax-meta"
                      style={{ color: C.ink, overflow: "hidden",
                               textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{c.name}</span>
                <span className="ax-meta"
                      style={{ color: c.group === "Other" ? C.warnInk : C.slate,
                               overflow: "hidden", textOverflow: "ellipsis",
                               whiteSpace: "nowrap" }}>{c.group}</span>
                <span className="ax-meta ax-num" style={{ color: C.slate, textAlign: "right" }}>{usd(c.spend)}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </>
  );
}
