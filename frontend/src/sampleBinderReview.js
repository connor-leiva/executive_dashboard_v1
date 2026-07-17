/* Representative Binder review-queue payload for preview/sample mode (VITE_API_BASE unset).
   Mirrors GET /binder/review exactly (stats + proposals + filed_no_obligation). All figures
   are SAMPLE DATA — the entity-name collisions (two "Spring B" entities) are deliberate, to
   show the ambiguous-pick path. */

const proposals = [
  {
    id: "p1", document: "Utah_Life_EO_policy_2025.pdf", via: "upload", kind: "Insurance",
    kind_key: "insurance", entity: "Utah Life Real Estate Group, LLC", entity_id: "s-utahlife",
    entity_confidence: "exact name match", method: "read", confidence: 0.97,
    date: "Jun 14, 2026", cadence: "Annual", flavor: "normal", ambiguous: false, candidates: [],
    basis: "Policy declarations read expiration 2026-06-14, taken directly.",
    fields: [["Entity", "Utah Life Real Estate Group, LLC"], ["Kind", "insurance"],
             ["Method", "read"], ["Due date", "2026-06-14"]],
  },
  {
    id: "p2", document: "Articles_of_Org_Zenworth.pdf", via: "folder", kind: "Annual report",
    kind_key: "annual_report", entity: "Zenworth Holdings, LLC", entity_id: "s-zenworth",
    entity_confidence: "strong match", method: "rule", confidence: 0.85,
    date: "Aug 31, 2026", cadence: "Annual", flavor: "normal", ambiguous: false, candidates: [],
    basis: "Formation 2019-08-05 + UT rule (anniversary_month_end): annual report due 2026-08-31, derived.",
    fields: [["Entity", "Zenworth Holdings, LLC"], ["Kind", "annual_report"], ["Method", "rule"],
             ["Due date", "2026-08-31"], ["Jurisdiction", "UT"]],
  },
  {
    id: "p3", document: "fwd_2024_return_SB_Coaching.pdf", via: "email", kind: "Federal tax",
    kind_key: "federal_tax", entity: "SB Coaching, LLC", entity_id: "s-sbcoaching",
    entity_confidence: "strong match", method: "rule", confidence: 0.8,
    date: "Mar 15, 2027", cadence: "Annual", flavor: "normal", ambiguous: false, candidates: [],
    basis: "Return type 1120-S (s_corp) + entity_type_calendar: federal tax schedule, derived.",
    fields: [["Entity", "SB Coaching, LLC"], ["Kind", "federal_tax"], ["Method", "rule"],
             ["Due date", "2027-03-15"]],
  },
  {
    id: "p4", document: "scan_registered_agent_invoice.pdf", via: "email", kind: "Registered agent",
    kind_key: "registered_agent", entity: "Spring B - The Forum, LLC", entity_id: "s-forum",
    entity_confidence: "needs review", method: "read", confidence: 0.62,
    date: "Feb 20, 2027", cadence: "Annual", flavor: "normal", ambiguous: true,
    candidates: [{ entity_id: "s-forum", name: "Spring B - The Forum, LLC", score: 0.71 },
                 { entity_id: "s-becoll", name: "Spring B - beCollective, LLC", score: 0.69 }],
    basis: "Registered-agent renewal date read clearly, but the entity line just says 'Spring B' — could be The Forum or beCollective. Needs a human to pick.",
    fields: [["Entity", "Spring B (?)"], ["Kind", "registered_agent"], ["Method", "read"],
             ["Due date", "2027-02-20"]],
  },
  {
    id: "p5", document: "CP575_SNB_Inc.pdf", via: "folder", kind: "BOI / FinCEN",
    kind_key: "boi", entity: "SNB, Inc", entity_id: "s-snb",
    entity_confidence: "exact name match", method: "rule", confidence: 0.6,
    date: null, cadence: "One_time", flavor: "gap", ambiguous: false, candidates: [],
    basis: "Entity is active and its type implies a BOI/FinCEN filing; none is on file. Human-set date (BOI posture was legally turbulent 2024-2025; re-verify current requirement).",
    fields: [["Entity", "SNB, Inc"], ["Kind", "boi"], ["Method", "rule"]],
  },
];

export default {
  stats: {
    awaiting: proposals.length,
    confirmed_this_pass: 0,
    entity_unclear: proposals.filter((p) => p.ambiguous).length,
    gaps: proposals.filter((p) => p.flavor === "gap").length,
  },
  proposals,
  filed_no_obligation: [
    { filename: "Operating_Agreement_Utah_Life.pdf", entity: "Utah Life Real Estate Group, LLC",
      note: "Filed under Formation." },
    { filename: "Davis_office_lease.pdf", entity: "Utah Life Real Estate Group, LLC",
      note: "Filed under Lease." },
  ],
};
