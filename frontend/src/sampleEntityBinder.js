/* Generic entity-binder detail for preview/sample mode. Mirrors GET /binder/entity/{id}.
   Adapts its header to whichever entity was opened. SAMPLE DATA. */
export default function sampleEntityBinder(row) {
  return {
    entity: {
      id: row?.id || "sample", name: row?.name || "Sample Entity, LLC",
      nickname: row?.nickname || null, type: "llc", jurisdiction: "UT",
      ownership: "100%", ein_masked: "87-41•••••", entity_group: "operating", tracking_ready: true,
    },
    obligations: [
      { id: "o1", kind: "annual_report", kind_label: "Annual report", status: "current",
        label: "Mar", due_date: "2027-03-31", cadence: "annual", applicable: true,
        source_document: "Articles_of_Organization.pdf", notes: null },
      { id: "o2", kind: "insurance", kind_label: "Insurance", status: "due_soon",
        label: "14d", due_date: "2026-06-14", cadence: "annual", applicable: true,
        source_document: "EO_policy_2025.pdf", notes: null },
      { id: "o3", kind: "federal_tax", kind_label: "Federal tax", status: "in_progress",
        label: "books", due_date: "2027-03-15", cadence: "annual", applicable: true,
        source_document: "2024_return_1120S.pdf", notes: null },
      { id: "o4", kind: "boi", kind_label: "BOI / FinCEN", status: "current",
        label: "filed", due_date: null, cadence: "one_time", applicable: true,
        source_document: "BOI_confirmation.pdf", notes: null },
    ],
    documents: [
      { category: "Formation", category_key: "formation",
        items: [{ id: "d1", filename: "Articles_of_Organization.pdf", alert: false },
                { id: "d2", filename: "Operating_Agreement.pdf", alert: false },
                { id: "d3", filename: "EIN_Confirmation_CP575.pdf", alert: false }] },
      { category: "Insurance", category_key: "insurance",
        items: [{ id: "d4", filename: "EO_policy_2025.pdf", alert: false }] },
      { category: "Tax", category_key: "tax",
        items: [{ id: "d5", filename: "2024_return_1120S.pdf", alert: false },
                { id: "d6", filename: "BOI_confirmation.pdf", alert: false }] },
    ],
  };
}
