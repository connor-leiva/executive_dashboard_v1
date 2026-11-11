import { API_BASE, postJSON } from "../../api";
import { C, FONT } from "../adsTokens.js";
import { Btn, Card, Empty, ErrorNote, PageTitle } from "../ui.jsx";
import { useState } from "react";

const ACTION = {
  pause: "Paused",
  activate: "Turned on",
  set_daily_budget: "Daily budget",
  set_lifetime_budget: "Lifetime budget",
  duplicate: "Duplicated",
  fix_tagging: "Tagging",
  set_url_tags: "Tagging",
  create_ad: "New ad",
};

function when(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

export default function Activity({ log, accountId, canUndo, onSaved }) {
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(false);
  const rows = log.data?.writes || [];

  async function undo() {
    setBusy(true);
    setMsg(null);
    try {
      if (!API_BASE) {
        setMsg("This preview has no applied change to undo.");
        return;
      }
      const out = await postJSON("/ads/writes/undo", { account: accountId });
      setMsg(out.meta_error || out.validation_note || (out.status === "applied" ? "Undone." : "Preview only."));
      onSaved?.();
      log.retry?.();
    } catch (e) {
      setMsg(e.message || "Undo failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ display: "grid", gap: 16 }}>
      <PageTitle title="Activity">
        <Btn primary disabled={busy || !canUndo} onClick={undo}>Undo last change</Btn>
      </PageTitle>
      {log.error && <ErrorNote onRetry={log.retry}>Couldn&apos;t load the change log.</ErrorNote>}
      {log.loading && <Empty>Loading the change log…</Empty>}
      {!log.loading && rows.length === 0 && <Empty>No changes yet.</Empty>}
      {rows.length > 0 && (
        <Card style={{ padding: 0 }}>
          {rows.map((row, i) => (
            <div key={row.id} style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap", padding: "12px 16px", borderTop: i ? `1px solid ${C.line}` : "none" }}>
              <div>
                <div style={{ fontFamily: FONT, fontSize: 14, fontWeight: 600, color: C.ink }}>
                  {ACTION[row.action] || row.action} · {row.name || row.external_id}
                </div>
                {row.reason && <div style={{ fontFamily: FONT, fontSize: 13, color: C.slate, marginTop: 4 }}>{row.reason}</div>}
                {row.meta_error && <div style={{ fontFamily: FONT, fontSize: 13, color: C.badInk, marginTop: 4 }}>{row.meta_error}</div>}
              </div>
              <div style={{ fontFamily: FONT, fontSize: 12, color: C.muted, textAlign: "right" }}>
                <div>{row.dry_run ? "Preview" : row.status}</div>
                <div>{row.actor} · {when(row.at)}</div>
              </div>
            </div>
          ))}
        </Card>
      )}
      {msg && <p style={{ fontFamily: FONT, fontSize: 13, color: C.slate, margin: 0 }}>{msg}</p>}
    </div>
  );
}
