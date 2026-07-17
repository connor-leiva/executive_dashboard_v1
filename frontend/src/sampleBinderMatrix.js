/* Representative Binder matrix payload for preview/sample mode (VITE_API_BASE unset).
   Mirrors GET /binder exactly (groups + flags + kinds). SAMPLE DATA. */

const KINDS = [
  { key: "annual_report", label: "Annual report" },
  { key: "registered_agent", label: "Registered agent" },
  { key: "insurance", label: "Insurance" },
  { key: "boi", label: "BOI / FinCEN" },
  { key: "federal_tax", label: "Federal tax" },
  { key: "state_tax", label: "State tax" },
  { key: "estimated_payments", label: "Estimated payments" },
];

const c = (status, label) => ({ status, label });
// cells(ar, ra, ins, boi, fed, st, est)
const cells = (...vals) => Object.fromEntries(KINDS.map((k, i) => [k.key, vals[i]]));

const ROWS = {
  operating: [
    ["s-utahlife", "Utah Life Real Estate Group, LLC", "The Team",
      cells(c("current", "Mar"), c("current", "Jan"), c("due_soon", "14d"), c("current", "filed"),
            c("in_progress", "books"), c("in_progress", "books"), c("due_soon", "Q2"))],
    ["s-forum", "Spring B - The Forum, LLC", "The Forum",
      cells(c("current", "Apr"), c("current", "Feb"), c("current", "filed"), c("current", "filed"),
            c("in_progress", "books"), c("in_progress", "books"), c("none", "—"))],
    ["s-becoll", "Spring B - beCollective, LLC", "beCollective",
      cells(c("current", "Apr"), c("due_soon", "20d"), c("current", "filed"), c("current", "filed"),
            c("in_progress", "books"), c("in_progress", "books"), c("not_applicable", "n/a"))],
  ],
  holding: [
    ["s-snb", "SNB, Inc", "The parent",
      cells(c("current", "Feb"), c("current", "current"), c("current", "current"), c("overdue", "overdue"),
            c("due_soon", "ext"), c("due_soon", "ext"), c("due_soon", "Q2"))],
    ["s-shepard", "1173 Shepard Creek, LLC", null,
      cells(c("current", "current"), c("current", "current"), c("current", "current"), c("current", "filed"),
            c("current", "filed"), c("current", "filed"), c("not_applicable", "n/a"))],
    ["s-zenworth", "Zenworth Holdings, LLC", null,
      cells(c("overdue", "late"), c("current", "current"), c("current", "current"), c("current", "filed"),
            c("current", "filed"), c("current", "filed"), c("not_applicable", "n/a"))],
  ],
};

const groups = Object.entries(ROWS).map(([group, rows]) => ({
  group, entities: rows.map(([id, name, nickname, cs]) => ({ id, name, nickname, cells: cs })),
}));

// Flags computed from the cells so the tile and matrix agree.
let overdue = 0, due_soon = 0;
const attention = [];
groups.forEach((g) => g.entities.forEach((e) => {
  let worst = "none", open = 0;
  KINDS.forEach((k) => {
    const st = e.cells[k.key].status;
    if (st === "overdue") { overdue++; open++; }
    else if (st === "due_soon") { due_soon++; open++; }
    const rank = { overdue: 4, due_soon: 3, in_progress: 2, current: 1, not_applicable: 0, none: -1 };
    if (rank[st] > rank[worst]) worst = st;
  });
  if (worst === "overdue" || worst === "due_soon") attention.push({ id: e.id, name: e.name, open, worst });
}));
attention.sort((a, b) => (a.worst !== "overdue") - (b.worst !== "overdue") || b.open - a.open);

export default {
  updated_at: "2026-07-16",
  groups,
  flags: { overdue, due_soon, attention },
  kinds: KINDS,
};
