import { useState } from "react";

import { API_BASE, postJSON } from "../../api";
import { C, usd } from "../adsTokens.js";
import { Btn, Card, Empty, ErrorNote, PageTitle, Segmented, sentenceStatus } from "../ui.jsx";

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

const FILTERS = [
  ["all", "All"],
  ["applied", "Applied"],
  ["autopilot", "Autopilot"],
  ["preview", "Previews"],
];

function when(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

function money(n) {
  const v = Number(n);
  return Number.isFinite(v) ? usd(v) : null;
}

function word(value, fallback) {
  const raw = value || fallback;
  if (!raw) return "—";
  return /^[A-Z_]+$/.test(String(raw)) ? sentenceStatus(raw) : String(raw);
}

function change(row) {
  const before = row.before || {};
  const after = row.after || {};
  if (row.action === "pause" || row.action === "activate") {
    return `${word(before.status, "—")} → ${word(after.status, row.action === "pause" ? "Paused" : "Active")}`;
  }
  if (row.action === "set_daily_budget") {
    return `${money(before.daily_budget) || "—"} → ${money(after.daily_budget) || "—"}`;
  }
  if (before.url_tags || after.url_tags) return "URL tags updated";
  if (after.created_id) return `Created ${after.created_id}`;
  return "—";
}

export default function Activity({ log, accountId, canUndo, onSaved }) {
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(false);
  const [filter, setFilter] = useState("all");
  const rows = log.data?.writes || [];
  const shown = rows.filter((row) => {
    if (filter === "applied") return row.status === "applied" && row.source !== "autopilot";
    if (filter === "autopilot") return row.source === "autopilot";
    if (filter === "preview") return row.dry_run || row.status === "preview";
    return true;
  });
  const newest = rows[0];
  const undoWhy = canUndo
    ? null
    : newest && (newest.dry_run || newest.status === "preview")
      ? "Nothing to undo: last change was a preview."
      : "Nothing to undo yet.";

  async function undo() {
    setBusy(true);
    setMsg(null);
    try {
      if (!API_BASE) {
        setMsg(undoWhy || "This preview has no applied change to undo.");
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
    <div className="ax-stack">
      <PageTitle title="Activity" />
      <div className="ax-spread">
        <Segmented label="Show changes" value={filter} onChange={setFilter} options={FILTERS} />
        <div className="ax-row" style={{ gap: 12 }}>
          {!canUndo && <span className="ax-meta">{undoWhy}</span>}
          <Btn disabled={busy || !canUndo} onClick={undo}>Undo last change</Btn>
        </div>
      </div>
      {msg && <p className="ax-text">{msg}</p>}
      {log.error && <ErrorNote onRetry={log.retry}>Couldn&apos;t load the change log.</ErrorNote>}
      {log.loading && <Empty>Loading the change log…</Empty>}
      {!log.loading && shown.length === 0 && <Empty>No changes in this filter.</Empty>}
      {shown.length > 0 && (
        <Card flush>
          <div className="ax-scroll">
            <table className="ax-table" style={{ minWidth: 760 }}>
              <thead>
                <tr>
                  {["When", "Who", "What changed", "Before → after", "Why", "Result"].map((h) => (
                    <th key={h}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {shown.map((row) => (
                  <tr key={row.id}>
                    <td className="ax-num ax-nowrap">{when(row.at)}</td>
                    <td className="ax-nowrap">{row.actor || "—"}</td>
                    <td>
                      <span style={{ fontWeight: 600 }}>{ACTION[row.action] || row.action}</span>
                      <span style={{ color: C.slate }}> · {row.name || row.external_id}</span>
                    </td>
                    <td className="ax-num ax-nowrap">{change(row)}</td>
                    <td className="ax-soft">{row.reason || "—"}</td>
                    <td className="ax-nowrap" style={row.meta_error ? { color: C.badInk } : undefined}>
                      {row.meta_error || (row.dry_run ? "Preview" : sentenceStatus(row.status))}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
