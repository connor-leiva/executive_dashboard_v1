/* Books · Mapping — each entity's QuickBooks chart on the left, the standard chart on the
   right, and the subtree rules that stop this screen from needing a visit every time
   somebody gets hired (SPEC-coa-mapping-provenance 5.5).

   It lives under Books rather than under Settings, where the spec put it. This is not a
   configuration toggle, it is a working surface with 271 rows on ULRG, and it is where the
   unmapped-guard error in Phase 3 will send people from. Settings is already 1,500 lines of
   things you set once.

   QuickBooks is never written to. Everything here changes how Acumyn READS those books. */
import { useMemo, useState } from "react";
import { postJSON, patchJSON, delJSON } from "../api";
import { useCoaEntities, useCoaMapping } from "./useBooks.js";
import { Card, Eyebrow, Pill, StatePanel, DARK, font, T } from "./ui.jsx";

const API = import.meta.env.VITE_API_BASE;

/* Statement sections, in P&L order. The standard chart is grouped by these so the right-hand
   column reads the way a statement does rather than as one 136-row list. */
const SECTIONS = [
  ["revenue", "Revenue"], ["cogs", "Cost of Sale"], ["opex", "Operating Expenses"],
  ["other_income", "Other Income"], ["other_expense", "Other Expense"],
  ["asset", "Assets"], ["liability", "Liabilities"], ["equity", "Equity"],
];

const FILTERS = [["unmapped", "Unmapped"], ["mapped", "Mapped"], ["ignored", "Ignored"], ["all", "All"]];

const CONFIDENCE = { exact: "good", keyword: "warn", weak: "muted" };

const parentPath = (fqn) => {
  const i = (fqn || "").lastIndexOf(":");
  return i > 0 ? fqn.slice(0, i + 1) : "";
};

const btn = (tone = "quiet") => ({
  fontFamily: font.head, fontSize: 12, fontWeight: 600, borderRadius: 8, padding: "6px 12px",
  cursor: "pointer", whiteSpace: "nowrap",
  ...(tone === "solid"
    ? { color: T.white, background: T.evergreen, border: "none" }
    : { color: T.slate, background: T.parchment, border: `1px solid ${T.line}` }),
});

function Count({ label, value, tone }) {
  return (
    <div style={{ minWidth: 78 }}>
      <div style={{ fontFamily: font.head, fontSize: 20, fontWeight: 700, color: tone || T.ink,
                    fontVariantNumeric: "tabular-nums" }}>{value}</div>
      <div style={{ fontFamily: font.body, fontSize: 11, color: T.muted, marginTop: 2 }}>{label}</div>
    </div>
  );
}

export default function BooksMapping({ isCFO = false }) {
  const entities = useCoaEntities();
  const [bizId, setBizId] = useState(null);
  const list = entities.data?.entities || [];
  const active = bizId || list[0]?.id || null;
  const view = useCoaMapping(active);

  const [filter, setFilter] = useState("unmapped");
  const [type, setType] = useState("all");
  const [q, setQ] = useState("");
  const [stdQ, setStdQ] = useState("");
  const [picked, setPicked] = useState(() => new Set());
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const [ruleFor, setRuleFor] = useState(null);        // {pattern, standard_account_id}

  const d = view.data;
  const accounts = d?.accounts || [];

  const types = useMemo(
    () => [...new Set(accounts.map((a) => a.type).filter(Boolean))].sort(), [accounts]);

  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return accounts.filter((a) => {
      if (filter === "unmapped" && (a.standard_account_id || a.is_ignored)) return false;
      if (filter === "mapped" && !a.standard_account_id) return false;
      if (filter === "ignored" && !a.is_ignored) return false;
      if (type !== "all" && a.type !== type) return false;
      if (needle && !(`${a.fqn} ${a.code || ""} ${a.standard_name || ""}`).toLowerCase().includes(needle)) return false;
      return true;
    });
  }, [accounts, filter, type, q]);

  const std = useMemo(() => {
    const needle = stdQ.trim().toLowerCase();
    return (d?.standard || []).filter(
      (a) => a.is_active && (!needle || `${a.code} ${a.name}`.toLowerCase().includes(needle)));
  }, [d, stdQ]);

  /* Every mutation re-reads the server rather than patching the local copy. The header's
     counts are the thing people trust on this screen; they must never be an optimistic guess. */
  const run = async (fn) => {
    setBusy(true); setErr(null);
    try {
      if (API) await fn();
      setPicked(new Set());
      view.refresh();
      entities.retry();
      return true;
    } catch (e) {
      setErr(e.detail || e.message || "That didn't save.");
      return false;                       // callers keep their form open on a failure
    } finally { setBusy(false); }
  };

  const mapTo = (ids, standard_account_id) => run(() => postJSON("/books/coa/map", {
    business_id: active, qbo_account_ids: ids, standard_account_id }));

  const ignore = (ids) => {
    const reason = window.prompt(
      "Why is this account excluded from the statement?\n\nThe reason is stored with the decision — it is the answer to \"why is this not in the P&L\" six months from now.");
    if (!reason || !reason.trim()) return;
    return run(() => postJSON("/books/coa/ignore", {
      business_id: active, qbo_account_ids: ids, reason: reason.trim() }));
  };

  const toggle = (id) => setPicked((p) => {
    const n = new Set(p);
    n.has(id) ? n.delete(id) : n.add(id);
    return n;
  });

  const selected = [...picked];
  const entity = list.find((e) => e.id === active);

  return (
    <StatePanel loading={entities.loading} error={entities.error} retry={entities.retry}
      empty={!entities.loading && list.length === 0}
      emptyTitle="No entities yet"
      emptyMsg="Connect a QuickBooks company in Settings and the chart arrives on the next sync.">
      <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>

        {/* ── entity picker + the persistent header (SPEC 5.5) ── */}
        <div style={{ borderRadius: 14, padding: "18px 22px", ...DARK }}>
          <Eyebrow onDark>Chart of accounts</Eyebrow>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 12 }}>
            {list.map((e) => {
              const on = e.id === active;
              return (
                <button key={e.id} onClick={() => { setBizId(e.id); setPicked(new Set()); }}
                  style={{ fontFamily: font.head, fontSize: 12.5, fontWeight: 600, borderRadius: 99,
                    padding: "7px 14px", cursor: "pointer", border: `1px solid ${on ? "transparent" : "rgba(243,238,231,0.28)"}`,
                    background: on ? T.onDark : "transparent", color: on ? T.evergreen : T.onDark }}>
                  {e.name}
                  <span style={{ marginLeft: 8, opacity: 0.72, fontVariantNumeric: "tabular-nums" }}>
                    {e.counts.unmapped}</span>
                </button>
              );
            })}
          </div>
          {entity && !entity.qbo_connected && (
            <div style={{ fontFamily: font.body, fontSize: 12, color: T.onDarkMute, marginTop: 12 }}>
              No live QuickBooks connection on this entity — the chart below is whatever the last sync left.
            </div>
          )}
        </div>

        <StatePanel loading={view.loading} error={view.error} retry={view.retry}>
          {d && (
            <>
              <Card style={{ padding: "16px 22px" }}>
                <div style={{ display: "flex", gap: 26, flexWrap: "wrap", alignItems: "flex-start" }}>
                  <Count label="Accounts" value={d.counts.total} />
                  <Count label="Mapped" value={d.counts.mapped} tone={T.meadowInk} />
                  <Count label="Unmapped" value={d.counts.unmapped}
                         tone={d.counts.unmapped ? T.poppyText : T.meadowInk} />
                  <Count label="Ignored" value={d.counts.ignored} tone={T.muted} />
                  <Count label="By rule" value={d.counts.by_rule} tone={T.teal} />
                  <div style={{ flex: 1, minWidth: 220, fontFamily: font.body, fontSize: 11.5,
                                color: T.muted, lineHeight: 1.55, paddingTop: 2 }}>
                    Mapping changes how Acumyn reads these books. It does not touch QuickBooks, and it
                    does not fix a transaction coded to the wrong account.
                  </div>
                </div>
              </Card>

              {err && (
                <Card style={{ padding: "12px 18px", borderColor: T.poppy }}>
                  <span style={{ fontFamily: font.body, fontSize: 12.5, color: T.poppyText }}>{err}</span>
                </Card>
              )}

              <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1.35fr) minmax(0,1fr)",
                            gap: 18, alignItems: "start" }} className="coa-cols">

                {/* ── left: this entity's QuickBooks accounts ── */}
                <Card style={{ padding: 0, overflow: "hidden" }}>
                  <div style={{ padding: "16px 20px 12px", borderBottom: `1px solid ${T.line}` }}>
                    <Eyebrow>{d.business.name} · QuickBooks</Eyebrow>
                    <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 10 }}>
                      {FILTERS.map(([k, l]) => (
                        <button key={k} onClick={() => setFilter(k)} style={{
                          ...btn(), background: filter === k ? T.meadowBg : T.parchment,
                          color: filter === k ? T.meadowInk : T.slate,
                          borderColor: filter === k ? T.sprout : T.line }}>{l}</button>
                      ))}
                    </div>
                    <div style={{ display: "flex", gap: 8, marginTop: 10, flexWrap: "wrap" }}>
                      <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search accounts"
                        style={{ flex: 1, minWidth: 150, fontFamily: font.body, fontSize: 12.5, padding: "7px 10px",
                          border: `1px solid ${T.line}`, borderRadius: 8, color: T.ink, background: T.white }} />
                      {/* The bulk-map-by-type first pass: filter to one QBO type, select all, map once. */}
                      <select value={type} onChange={(e) => setType(e.target.value)}
                        style={{ fontFamily: font.body, fontSize: 12.5, padding: "7px 10px", color: T.ink,
                          border: `1px solid ${T.line}`, borderRadius: 8, background: T.white }}>
                        <option value="all">Every QBO type</option>
                        {types.map((t) => <option key={t} value={t}>{t}</option>)}
                      </select>
                    </div>
                    <div style={{ display: "flex", gap: 8, marginTop: 10, alignItems: "center", flexWrap: "wrap" }}>
                      <button onClick={() => setPicked(new Set(shown.map((a) => a.qbo_account_id)))}
                        style={btn()}>Select all {shown.length}</button>
                      {selected.length > 0 && (
                        <>
                          <button onClick={() => setPicked(new Set())} style={btn()}>Clear</button>
                          <button disabled={busy} onClick={() => ignore(selected)} style={btn()}>Ignore…</button>
                          <span style={{ fontFamily: font.body, fontSize: 12, color: T.teal, fontWeight: 600 }}>
                            {selected.length} selected — pick a standard account →</span>
                        </>
                      )}
                    </div>
                  </div>

                  <div style={{ maxHeight: 560, overflowY: "auto" }}>
                    {shown.length === 0 && (
                      <div style={{ fontFamily: font.body, fontSize: 13, color: T.muted, padding: "22px 20px" }}>
                        {filter === "unmapped" ? "Every account here is mapped or ignored." : "Nothing matches."}
                      </div>
                    )}
                    {shown.map((a, i) => {
                      const on = picked.has(a.qbo_account_id);
                      return (
                        <div key={a.qbo_account_id} style={{ display: "flex", gap: 10, padding: "10px 20px",
                          borderTop: i ? `1px solid ${T.line}` : "none",
                          background: on ? T.meadowBg : "transparent" }}>
                          <input type="checkbox" checked={on} onChange={() => toggle(a.qbo_account_id)}
                            aria-label={`Select ${a.fqn}`} style={{ marginTop: 3, accentColor: T.meadow }} />
                          <div style={{ flex: 1, minWidth: 0 }}>
                            <div style={{ fontFamily: font.body, fontSize: 12.5, color: T.ink, fontWeight: 600,
                                          wordBreak: "break-word" }}>
                              {a.fqn}
                              {!a.qbo_active && <span style={{ color: T.muted, fontWeight: 500 }}> · inactive in QBO</span>}
                            </div>
                            <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", marginTop: 4 }}>
                              <span style={{ fontFamily: font.body, fontSize: 11, color: T.muted }}>{a.type}</span>
                              {a.code && (
                                <Pill tone={a.mapped_via === "rule" ? "muted" : "good"}>
                                  {a.code} {a.standard_name}{a.mapped_via === "rule" ? " · by rule" : ""}
                                </Pill>
                              )}
                              {a.is_ignored && <Pill tone="muted">Ignored · {a.ignore_reason}</Pill>}
                              {!a.code && !a.is_ignored && a.suggestion && (
                                <>
                                  <Pill tone={CONFIDENCE[a.suggestion.confidence] || "muted"}>
                                    Suggests {a.suggestion.code} · {a.suggestion.why}</Pill>
                                  <button disabled={busy}
                                    onClick={() => mapTo([a.qbo_account_id], a.suggestion.standard_account_id)}
                                    style={btn()}>Accept</button>
                                </>
                              )}
                              {(a.code || a.is_ignored) && (
                                <button disabled={busy} onClick={() => mapTo([a.qbo_account_id], null)}
                                  style={{ ...btn(), padding: "4px 9px", fontSize: 11 }}>Clear</button>
                              )}
                            </div>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </Card>

                {/* ── right: the standard chart ── */}
                <Card style={{ padding: 0, overflow: "hidden" }}>
                  <div style={{ padding: "16px 20px 12px", borderBottom: `1px solid ${T.line}` }}>
                    <Eyebrow>Standard chart</Eyebrow>
                    <input value={stdQ} onChange={(e) => setStdQ(e.target.value)} placeholder="Search by code or name"
                      style={{ width: "100%", marginTop: 10, fontFamily: font.body, fontSize: 12.5, padding: "7px 10px",
                        border: `1px solid ${T.line}`, borderRadius: 8, color: T.ink, background: T.white,
                        boxSizing: "border-box" }} />
                    <div style={{ fontFamily: font.body, fontSize: 11.5, color: T.muted, marginTop: 8 }}>
                      {selected.length
                        ? `Click an account to map the ${selected.length} selected.`
                        : "Select accounts on the left, then click a target here."}
                    </div>
                  </div>
                  <div style={{ maxHeight: 560, overflowY: "auto" }}>
                    {SECTIONS.map(([key, label]) => {
                      const rows = std.filter((a) => a.section === key);
                      if (!rows.length) return null;
                      return (
                        <div key={key}>
                          <div style={{ fontFamily: font.head, fontSize: 11, fontWeight: 700, letterSpacing: "0.1em",
                            textTransform: "uppercase", color: T.tertiary, background: T.parchment,
                            padding: "7px 20px", position: "sticky", top: 0 }}>{label}</div>
                          {rows.map((a) => (
                            <button key={a.id} title={a.definition || undefined}
                              disabled={busy || !selected.length}
                              onClick={() => mapTo(selected, a.id)}
                              style={{ display: "flex", gap: 10, width: "100%", textAlign: "left",
                                padding: "8px 20px", border: "none", borderTop: `1px solid ${T.line}`,
                                background: "transparent", cursor: selected.length ? "pointer" : "default",
                                opacity: selected.length ? 1 : 0.55 }}>
                              <span style={{ fontFamily: font.body, fontSize: 12, fontWeight: 700, color: T.teal,
                                fontVariantNumeric: "tabular-nums", minWidth: 34 }}>{a.code}</span>
                              <span style={{ fontFamily: font.body, fontSize: 12.5, color: T.ink }}>{a.name}</span>
                            </button>
                          ))}
                        </div>
                      );
                    })}
                  </div>
                </Card>
              </div>

              <Rules d={d} active={active} isCFO={isCFO} busy={busy} run={run}
                     accounts={accounts} picked={selected} ruleFor={ruleFor} setRuleFor={setRuleFor} />
            </>
          )}
        </StatePanel>
      </div>
      <style>{`@media (max-width: 900px) { .coa-cols { grid-template-columns: minmax(0,1fr) !important; } }`}</style>
    </StatePanel>
  );
}

/* ── rules ─────────────────────────────────────────────────────────────────────────────────
   The reason this screen is finishable. People are accounts: ULRG's VAs, Spring B's
   contractors, beCollective's closers all live in named subtrees, and without a rule every
   new hire arrives unmapped and blocks the close. One prefix covers everyone who joins it. */
function Rules({ d, active, isCFO, busy, run, accounts, picked, ruleFor, setRuleFor }) {
  const [std, setStd] = useState("");
  const [note, setNote] = useState("");
  const [scope, setScope] = useState("entity");

  // A rule is nearly always "the subtree this account sits in", so offer exactly that rather
  // than a blank text box — a hand-typed prefix that is subtly wrong matches nothing and says
  // nothing about why.
  const suggestedPatterns = useMemo(() => {
    const seen = new Map();
    accounts.filter((a) => picked.includes(a.qbo_account_id) || (!a.standard_account_id && !a.is_ignored))
      .forEach((a) => {
        const p = parentPath(a.fqn);
        if (p) seen.set(p, (seen.get(p) || 0) + 1);
      });
    return [...seen.entries()].filter(([, n]) => n > 1).sort((a, b) => b[1] - a[1]).slice(0, 8);
  }, [accounts, picked]);

  const create = async () => {
    const ok = await run(() => postJSON("/books/coa/rules", {
      pattern: ruleFor, standard_account_id: std,
      business_id: scope === "entity" ? active : null, note: note.trim() || null,
    }));
    if (ok) { setRuleFor(null); setStd(""); setNote(""); }
  };

  return (
    <Card>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
        <Eyebrow>Subtree rules</Eyebrow>
        <span style={{ fontFamily: font.body, fontSize: 11.5, color: T.muted }}>
          A rule maps a whole branch, including the accounts that do not exist yet.
        </span>
      </div>

      <div style={{ marginTop: 10 }}>
        {(d.rules || []).length === 0 && (
          <div style={{ fontFamily: font.body, fontSize: 13, color: T.muted, padding: "8px 0" }}>
            No rules yet. The accounts that are really people — VAs, contractors, closers — sit in
            named branches; one rule each keeps them off this screen for good.
          </div>
        )}
        {(d.rules || []).map((r, i) => (
          <div key={r.id} style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap",
            padding: "10px 0", borderTop: i ? `1px solid ${T.line}` : "none" }}>
            <span style={{ width: 8, height: 8, borderRadius: 2, flexShrink: 0,
                           background: r.is_active ? T.teal : T.muted }} />
            <div style={{ flex: 1, minWidth: 180 }}>
              <div style={{ fontFamily: font.body, fontSize: 12.5, fontWeight: 600, color: T.ink,
                            overflowWrap: "anywhere" }}>
                {r.pattern}<span style={{ color: T.muted, fontWeight: 500 }}> → {r.code}</span>
              </div>
              <div style={{ fontFamily: font.body, fontSize: 11.5, color: T.muted, marginTop: 3 }}>
                {r.business_key ? r.business_key : "every entity"} · covers {r.covers} here
                {r.is_active ? "" : " · off"}{r.note ? ` · ${r.note}` : ""}
              </div>
            </div>
            {isCFO && (
              <>
                <button disabled={busy} onClick={() => run(() =>
                  patchJSON(`/books/coa/rules/${r.id}`, { is_active: !r.is_active }))}
                  style={btn()}>{r.is_active ? "Turn off" : "Turn on"}</button>
                <button disabled={busy} onClick={() => run(() => delJSON(`/books/coa/rules/${r.id}`))}
                  style={btn()}>Delete</button>
              </>
            )}
          </div>
        ))}
      </div>

      {!isCFO && (
        <div style={{ fontFamily: font.body, fontSize: 11.5, color: T.muted, marginTop: 12 }}>
          Rules are a CFO decision — one pattern silently maps every account a future hire creates.
        </div>
      )}

      {isCFO && (
        <div style={{ marginTop: 14, borderTop: `1px solid ${T.line}`, paddingTop: 14 }}>
          {!ruleFor ? (
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
              <span style={{ fontFamily: font.body, fontSize: 12, color: T.muted }}>New rule for a branch:</span>
              {suggestedPatterns.length === 0 && (
                <span style={{ fontFamily: font.body, fontSize: 12, color: T.muted }}>
                  no branch here holds more than one unmapped account.</span>
              )}
              {suggestedPatterns.map(([p, n]) => (
                // A branch path runs to ninety characters on ULRG, so these buttons wrap
                // rather than pushing the page sideways on a phone.
                <button key={p} onClick={() => setRuleFor(p)} style={{ ...btn(),
                  whiteSpace: "normal", overflowWrap: "anywhere", textAlign: "left",
                  maxWidth: "100%" }}>
                  {p} <span style={{ color: T.muted }}>· {n}</span></button>
              ))}
            </div>
          ) : (
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
              <span style={{ fontFamily: font.body, fontSize: 12.5, fontWeight: 600, color: T.ink,
                             overflowWrap: "anywhere", maxWidth: "100%" }}>{ruleFor}</span>
              <span style={{ fontFamily: font.body, fontSize: 12, color: T.muted }}>→</span>
              <select value={std} onChange={(e) => setStd(e.target.value)}
                style={{ fontFamily: font.body, fontSize: 12.5, padding: "7px 10px", color: T.ink,
                  border: `1px solid ${T.line}`, borderRadius: 8, background: T.white }}>
                <option value="">Standard account…</option>
                {(d.standard || []).filter((a) => a.is_active).map((a) => (
                  <option key={a.id} value={a.id}>{a.code} · {a.name}</option>
                ))}
              </select>
              <select value={scope} onChange={(e) => setScope(e.target.value)}
                style={{ fontFamily: font.body, fontSize: 12.5, padding: "7px 10px", color: T.ink,
                  border: `1px solid ${T.line}`, borderRadius: 8, background: T.white }}>
                <option value="entity">{d.business.name} only</option>
                <option value="tenant">Every entity</option>
              </select>
              <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Why (optional)"
                style={{ flex: 1, minWidth: 140, fontFamily: font.body, fontSize: 12.5, padding: "7px 10px",
                  border: `1px solid ${T.line}`, borderRadius: 8, color: T.ink, background: T.white }} />
              <button disabled={busy || !std} onClick={create}
                style={{ ...btn("solid"), opacity: std ? 1 : 0.5 }}>Create rule</button>
              <button onClick={() => setRuleFor(null)} style={btn()}>Cancel</button>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}
