import { T, relativeTime } from "./theme.js";
import { useSkyslope } from "./useSkyslope.js";

/* The transactions panel on a SkySlope brokerage's page (backend: services/skyslope_metrics.py).

   What the production tiles above it cannot say: the pipeline by state, coverage, who is past due,
   who has gone quiet. Every number carries the chip the server computed for it, and the source is
   named neutrally ("Transactions"): SkySlope's name is not ours to show without its written OK. */

const CHIP = {
  live: { dot: T.meadow, text: T.tertiary, label: "Live" },
  needs_sync: { dot: T.daffodil, text: T.daffodilText, label: "Needs sync" },
  needs_key_scope: { dot: T.daffodil, text: T.daffodilText, label: "Needs a team key" },
  needs_definition: { dot: T.muted, text: T.muted, label: "Needs definition" },
};

function compact(n) {
  if (n == null) return "—";
  const a = Math.abs(n);
  if (a >= 1e9) return `$${(n / 1e9).toFixed(1)}B`;
  if (a >= 1e6) return `$${(n / 1e6).toFixed(1)}M`;
  if (a >= 1e3) return `$${Math.round(n / 1e3)}K`;
  return `$${Math.round(n)}`;
}

function shortDate(iso) {
  if (!iso) return "—";
  const d = new Date(`${iso}T00:00:00`);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

function Chip({ state, sample }) {
  const c = CHIP[state] || CHIP.needs_sync;
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 5, whiteSpace: "nowrap" }}>
      <span style={{ width: 6, height: 6, borderRadius: 99, background: sample ? T.daffodil : c.dot }} />
      <span style={{ fontFamily: "var(--font-text)", fontSize: 12, fontWeight: 600, color: sample ? T.daffodilText : c.text }}>
        {sample && state === "live" ? "Sample" : c.label}
      </span>
    </span>
  );
}

function Stat({ label, value, sub, state, sample, hint }) {
  return (
    <div style={{ minWidth: 0, boxSizing: "border-box", background: T.parchment, borderRadius: 10, padding: "12px 13px" }} title={hint}>
      <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 6 }}>
        <span style={{ fontFamily: "var(--font-text)", fontSize: 12, color: T.slate, fontWeight: 500, minWidth: 0 }}>{label}</span>
        <span style={{ marginLeft: "auto" }}><Chip state={state} sample={sample} /></span>
      </div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 6, flexWrap: "wrap" }}>
        <span style={{ fontFamily: "var(--font-display)", fontSize: 22, fontWeight: 600, color: T.ink, fontVariantNumeric: "tabular-nums" }}>{value}</span>
        {sub && <span style={{ fontFamily: "var(--font-text)", fontSize: 12, color: T.muted }}>{sub}</span>}
      </div>
    </div>
  );
}

function Label({ children, accent }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      <span style={{ width: 3, height: 14, borderRadius: 2, background: accent || T.evergreen }} />
      <span style={{ fontFamily: "var(--font-display)", fontSize: 12, fontWeight: 600, letterSpacing: 0, textTransform: "none", color: T.slate }}>
        {children}
      </span>
    </div>
  );
}

const shell = { boxSizing: "border-box", background: T.white, border: `1px solid ${T.line}`, borderRadius: 14, padding: 22, minWidth: 0 };
const small = { fontFamily: "var(--font-text)", fontSize: 12, color: T.muted };

export function SampleBanner() {
  return (
    <div role="note" style={{
      boxSizing: "border-box", display: "flex", gap: 12, alignItems: "flex-start", padding: "12px 16px",
      background: T.daffodilBg, border: `1px solid ${T.daffodil}`, borderRadius: 12,
    }}>
      <span style={{ fontFamily: "var(--font-display)", fontSize: 12, fontWeight: 600, letterSpacing: 0, textTransform: "none", color: T.daffodilText, whiteSpace: "nowrap", paddingTop: 2 }}>
        Sample data
      </span>
      <span style={{ fontFamily: "var(--font-text)", fontSize: 13, color: T.ink, lineHeight: 1.5 }}>
        Every listing, sale and agent from SkySlope on this page is invented, to show how transaction data will look
        here once a SkySlope key is connected. None of it came from SkySlope or from anybody's real business.
        Anything from your other connected sources, such as Sisu's leads and appointments, is real.
      </span>
    </div>
  );
}

export default function TransactionsPanel({ businessKey, period, accent }) {
  const { data: d, error, loading, retry } = useSkyslope(businessKey, period);

  if (error) {
    return (
      <div style={shell}>
        <Label accent={accent}>Transactions · listings and sales</Label>
        <div style={{ ...small, marginTop: 12 }}>
          Couldn't load transactions. <button onClick={retry} style={{ font: "inherit", color: T.evergreen, background: "none", border: "none", padding: 0, cursor: "pointer", textDecoration: "underline" }}>Try again</button>
        </div>
      </div>
    );
  }
  if (loading || !d) {
    return <div style={shell}><Label accent={accent}>Transactions · listings and sales</Label><div style={{ ...small, marginTop: 12 }}>Loading…</div></div>;
  }

  const m = Object.fromEntries(d.metrics.map((x) => [x.key, x]));
  const synced = relativeTime(d.connection.last_synced_at);
  const sample = d.sample;
  const quiet = d.not_producing;

  return (
    <div style={{ ...shell, display: "flex", flexDirection: "column", gap: 18 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <Label accent={accent}>Transactions · listings and sales</Label>
        <span style={{ ...small, display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
          <span>{d.coverage.label}</span>
          {synced && <span>· synced {synced}</span>}
          <span>· {d.period_label}</span>
        </span>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(min(100%, 170px), 1fr))", gap: 10 }}>
        <Stat label="Active listings" value={m.active_listings.count} sub={compact(m.active_listings.volume)} state={m.active_listings.chip} sample={sample} hint="On the market now" />
        <Stat label="Under contract" value={m.under_contract.count} sub={[compact(m.under_contract.volume), m.under_contract.note].filter(Boolean).join(" · ")}
          state={m.under_contract.chip} sample={sample} hint={`Now. ${d.pre_contract} pre-contract file(s) are not counted as under contract.`} />
        <Stat label="New contracts" value={m.new_contracts.count} sub={compact(m.new_contracts.volume)} state={m.new_contracts.chip} sample={sample} hint={`Contracts accepted, ${d.period_label}`} />
        <Stat label="Closed" value={m.closed.count} sub={compact(m.closed.volume)} state={m.closed.chip} sample={sample} hint={`Closings, ${d.period_label}`} />
        <Stat label="Canceled" value={m.canceled.count} state={m.canceled.chip} sample={sample} hint={`Listings and sales that fell through, ${d.period_label}`} />
        <Stat label="Not producing" value={quiet.count} sub={`no new file in ${quiet.days} days`} state={quiet.chip} sample={sample} hint={quiet.agents.join(", ")} />
        <Stat label="GCI" value="—" sub="needs a definition" state={d.gci.chip} hint={d.gci.note} />
      </div>

      {d.pre_contract > 0 && (
        <div style={small}>{d.pre_contract} pre-contract file{d.pre_contract === 1 ? " is" : "s are"} shown apart and not counted as under contract.</div>
      )}

      {d.past_due.length > 0 && (
        <div>
          <div style={{ fontFamily: "var(--font-text)", fontSize: 13, fontWeight: 600, color: T.poppyText, marginBottom: 8 }}>
            Past their closing date ({d.past_due.length})
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {d.past_due.map((p, i) => (
              <div key={i} style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "baseline", fontFamily: "var(--font-text)", fontSize: 13, color: T.ink }}>
                <span style={{ fontWeight: 600, minWidth: 0 }}>{p.name || "Unknown agent"}</span>
                <span style={{ color: T.muted }}>{p.city || ""}</span>
                <span style={{ color: T.muted }}>was due {shortDate(p.expected_close)}</span>
                <span style={{ marginLeft: "auto", fontVariantNumeric: "tabular-nums" }}>{compact(p.sale_price)}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      <div>
        <div style={{ fontFamily: "var(--font-text)", fontSize: 13, fontWeight: 600, color: T.ink, marginBottom: 8 }}>By agent</div>
        {/* Scrolls inside the card on a phone rather than widening the page. */}
        <div style={{ overflowX: "auto", WebkitOverflowScrolling: "touch" }}>
          <table style={{ width: "100%", minWidth: 620, borderCollapse: "collapse", fontFamily: "var(--font-text)", fontSize: 13 }}>
            <thead>
              <tr style={{ color: T.muted, textAlign: "right" }}>
                {["Agent", "Active", "Under contract", "New contracts", "Closed", "Volume", "Co-agent", "Last new file"].map((h, i) => (
                  <th key={h} style={{ fontWeight: 600, fontSize: 12, padding: "6px 8px", borderBottom: `1px solid ${T.line}`, textAlign: i === 0 ? "left" : "right", whiteSpace: "nowrap" }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {d.agents.map((a) => (
                <tr key={a.user_guid} style={{ borderBottom: `1px solid ${T.line}` }}>
                  <td style={{ padding: "7px 8px", color: T.ink, fontWeight: 600, whiteSpace: "nowrap" }}>
                    {a.name}
                    {a.not_producing && (
                      <span style={{ marginLeft: 8, fontSize: 12, fontWeight: 600, color: T.daffodilText, background: T.daffodilBg, borderRadius: 5, padding: "1px 6px" }}>Not producing</span>
                    )}
                  </td>
                  {[a.active_listings, a.under_contract + (a.past_due ? ` (${a.past_due} late)` : ""), a.new_contracts, a.closed, compact(a.closed_volume), a.co_agent_files, shortDate(a.last_activity)].map((v, i) => (
                    <td key={i} style={{ padding: "7px 8px", textAlign: "right", color: T.ink, fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap" }}>{v}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div style={{ ...small, marginTop: 8 }}>
          A file counts in full for its primary agent. Co-agent files are listed beside it, never added to the team total.
          {d.period_label ? ` New contracts, closings and volume are ${d.period_label.toLowerCase()}; active and under contract are as of now.` : ""}
        </div>
      </div>

      {d.unmapped_statuses.length > 0 && (
        <div style={{ ...small, color: T.daffodilText }}>
          Statuses not yet mapped, so not counted anywhere: {d.unmapped_statuses.join(", ")}
        </div>
      )}
    </div>
  );
}
