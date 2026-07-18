/* Generic entity-binder detail for preview/sample mode. Mirrors GET /binder/entity/{id}:
   the FULL obligation set (every kind, with not-configured stubs + a status reason + related
   docs) and the document tree split into Current + Historical. SAMPLE DATA. */
export default function sampleEntityBinder(row) {
  const reason = (s) => ({
    current: "Current and up to date.", due_soon: "Due soon, within the reminder window.",
    overdue: "Overdue. The document on file has lapsed.", in_progress: "Waiting on the linked Books close for this tax year.",
    none: "Not configured yet. Set a due date and cadence to start tracking this filing.",
  }[s] || "");
  const ob = (kind, kind_label, extra = {}) => ({
    kind, kind_label, configured: false, status: "none", label: "not configured",
    due_date: null, cadence: null, lead_days: null, applicable: null,
    source_document: null, source_document_id: null, notes: null,
    related_documents: [], ai_summary: null, ai_summary_at: null,
    ...extra, status_reason: reason(extra.status || "none"),
  });
  const doc = (id, filename, category_key, year, expired = false) =>
    ({ id, filename, category_key, can_preview: false, alert: expired, date: `${year}-01-01`, year, expired });
  const insCurrent = [doc("d4", "EO_policy_2026.pdf", "insurance", 2026), doc("d5", "General_Liability_2026.pdf", "insurance", 2026)];
  return {
    entity: {
      id: row?.id || "sample", name: row?.name || "Sample Entity, LLC",
      nickname: row?.nickname || null, type: "s_corp", jurisdiction: "UT", ownership: "100%",
      ein_masked: "87-41•••••", has_ein: true, formation_date: "2019-08-05", description: null,
      entity_group: "operating", business_id: null, business_name: row?.business_name || null,
      tracking_ready: true,
    },
    obligations: [
      ob("annual_report", "Annual Report", { configured: true, status: "current", label: "Mar",
        due_date: "2027-03-31", cadence: "annual", lead_days: 45, applicable: true }),
      ob("registered_agent", "Registered Agent", { configured: true, status: "current", label: "Jan",
        due_date: "2027-01-15", cadence: "annual", lead_days: 45, applicable: true }),
      ob("insurance", "Insurance", { configured: true, status: "due_soon", label: "14d",
        due_date: "2026-06-14", cadence: "annual", lead_days: 45, applicable: true,
        related_documents: insCurrent }),
      ob("boi", "BOI / FinCEN", { configured: true, status: "current", label: "Filed",
        due_date: null, cadence: "one_time", lead_days: 45, applicable: true }),
      ob("federal_tax", "Federal Tax", { configured: true, status: "in_progress", label: "Books",
        due_date: "2027-03-15", cadence: "annual", lead_days: 45, applicable: true }),
      ob("state_tax", "State Tax", { configured: true, status: "in_progress", label: "Books",
        due_date: "2027-03-15", cadence: "annual", lead_days: 45, applicable: true }),
      ob("estimated_payments", "Estimated Payments"),   // not configured yet — an editable stub
    ],
    documents: [
      { category: "Formation", category_key: "formation",
        current: [doc("d1", "Articles_of_Organization.pdf", "formation", 2015),
                  doc("d2", "Operating_Agreement.pdf", "formation", 2015),
                  doc("d3", "EIN_Confirmation_CP575.pdf", "formation", 2015)], historical: [] },
      { category: "Registered Agent", category_key: "registered_agent", current: [], historical: [] },
      { category: "Insurance", category_key: "insurance", current: insCurrent,
        historical: [doc("d6", "EO_policy_2024.pdf", "insurance", 2024, true),
                     doc("d7", "General_Liability_2024.pdf", "insurance", 2024, true)] },
      { category: "Tax", category_key: "tax",
        current: [doc("d8", "2025_return_1120S.pdf", "tax", 2025)],
        historical: [doc("d9", "2024_return_1120S.pdf", "tax", 2024),
                     doc("d10", "2023_return_1120S.pdf", "tax", 2023)] },
      { category: "Lease", category_key: "lease", current: [], historical: [] },
      { category: "Estate", category_key: "estate", current: [], historical: [] },
    ],
  };
}
