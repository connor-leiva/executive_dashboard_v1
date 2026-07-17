/* Generic entity-binder detail for preview/sample mode. Mirrors GET /binder/entity/{id}:
   the FULL obligation set (every kind, with not-configured stubs) + the full document tree.
   Adapts its header to whichever entity was opened. SAMPLE DATA. */
export default function sampleEntityBinder(row) {
  const ob = (kind, kind_label, extra) => ({
    kind, kind_label, configured: false, status: "none", label: "not configured",
    due_date: null, cadence: null, lead_days: null, applicable: null,
    source_document: null, source_document_id: null, notes: null, ...extra,
  });
  const doc = (id, filename, category_key) => ({ id, filename, category_key, can_preview: false, alert: false });
  return {
    entity: {
      id: row?.id || "sample", name: row?.name || "Sample Entity, LLC",
      nickname: row?.nickname || null, type: "llc", jurisdiction: "UT", ownership: "100%",
      ein_masked: "87-41•••••", has_ein: true, formation_date: "2019-08-05", description: null,
      entity_group: "operating", business_id: null, business_name: row?.business_name || null,
      tracking_ready: true,
    },
    obligations: [
      ob("annual_report", "Annual report", { configured: true, status: "current", label: "Mar",
        due_date: "2027-03-31", cadence: "annual", lead_days: 45, applicable: true,
        source_document: "Articles_of_Organization.pdf" }),
      ob("registered_agent", "Registered agent", { configured: true, status: "current", label: "Jan",
        due_date: "2027-01-15", cadence: "annual", lead_days: 45, applicable: true }),
      ob("insurance", "Insurance", { configured: true, status: "due_soon", label: "14d",
        due_date: "2026-06-14", cadence: "annual", lead_days: 45, applicable: true,
        source_document: "EO_policy_2025.pdf" }),
      ob("boi", "BOI / FinCEN", { configured: true, status: "current", label: "filed",
        due_date: null, cadence: "one_time", lead_days: 45, applicable: true,
        source_document: "BOI_confirmation.pdf" }),
      ob("federal_tax", "Federal tax", { configured: true, status: "in_progress", label: "books",
        due_date: "2027-03-15", cadence: "annual", lead_days: 45, applicable: true,
        source_document: "2024_return_1120S.pdf" }),
      ob("state_tax", "State tax", { configured: true, status: "in_progress", label: "books",
        due_date: "2027-03-15", cadence: "annual", lead_days: 45, applicable: true }),
      ob("estimated_payments", "Estimated payments"),   // not configured yet — an editable stub
    ],
    documents: [
      { category: "Formation", category_key: "formation",
        items: [doc("d1", "Articles_of_Organization.pdf", "formation"),
                doc("d2", "Operating_Agreement.pdf", "formation"),
                doc("d3", "EIN_Confirmation_CP575.pdf", "formation")] },
      { category: "Registered Agent", category_key: "registered_agent", items: [] },
      { category: "Insurance", category_key: "insurance",
        items: [doc("d4", "EO_policy_2025.pdf", "insurance")] },
      { category: "Tax", category_key: "tax",
        items: [doc("d5", "2024_return_1120S.pdf", "tax"), doc("d6", "BOI_confirmation.pdf", "tax")] },
      { category: "Lease", category_key: "lease", items: [] },
      { category: "Estate", category_key: "estate", items: [] },
    ],
  };
}
