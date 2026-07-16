/* Representative Binder entities for preview/sample mode (VITE_API_BASE unset).
   Mirrors the GET /binder/entities payload shape exactly (entities + businesses +
   counts). All figures are SAMPLE DATA — masked EINs are illustrative, not real. */

const ent = (o) => ({
  nickname: null, description: null, entity_type: null, jurisdiction: null,
  formation_date: null, ein_masked: null, has_ein: false, entity_group: "operating",
  ownership: "100%", business_id: null, business_key: null, business_name: null,
  active: true, ...o,
  tracking_ready: Boolean(o.entity_type && o.jurisdiction && o.formation_date),
  nudge: (o.entity_type && o.jurisdiction && o.formation_date) ? null
    : "Add " + [!o.jurisdiction && "state", !o.entity_type && "entity type",
                !o.formation_date && "formation date"].filter(Boolean).join(", ")
      + " to start tracking filings.",
});

const entities = [
  // Operating
  ent({ id: "s-utahlife", legal_name: "Utah Life Real Estate Group, LLC", nickname: "The Team",
        description: "The brokerage", entity_type: "llc", jurisdiction: "UT",
        formation_date: "2019-08-05", ein_masked: "87-41•••••", has_ein: true,
        business_key: "ulrg", business_name: "ULRG + Team" }),
  ent({ id: "s-forum", legal_name: "Spring B - The Forum, LLC", nickname: "The Forum",
        description: "The mastermind membership", entity_type: "llc", jurisdiction: "UT",
        formation_date: "2021-02-11", ein_masked: "88-25•••••", has_ein: true,
        business_key: "springb", business_name: "Spring B" }),
  ent({ id: "s-becoll", legal_name: "Spring B - beCollective, LLC", nickname: "beCollective",
        description: "The cohort community", entity_type: "llc", jurisdiction: "UT",
        formation_date: "2023-04-19", ein_masked: "88-26•••••", has_ein: true }),
  ent({ id: "s-shesummit", legal_name: "She Summit, LLC", description: "Women's coaching",
        entity_type: "llc", jurisdiction: "AZ" }),   // dormant: no formation date yet

  // Holding
  ent({ id: "s-snb", legal_name: "SNB, Inc", nickname: "The parent",
        description: "Spring's S Corp", entity_type: "s_corp", jurisdiction: "UT",
        formation_date: "2016-01-04", ein_masked: "86-33•••••", has_ein: true,
        entity_group: "holding" }),
  ent({ id: "s-shepard", legal_name: "1173 Shepard Creek, LLC", description: "Davis office building",
        entity_type: "llc", jurisdiction: "UT", formation_date: "2020-06-14",
        ein_masked: "84-11•••••", has_ein: true, entity_group: "holding" }),
  ent({ id: "s-meraki", legal_name: "Meraki Title Partners, LLC", description: "Owns 70% of Meraki Title",
        entity_type: "partnership", jurisdiction: "UT", formation_date: "2022-09-01",
        ein_masked: "85-70•••••", has_ein: true, entity_group: "holding", ownership: "70%" }),
  ent({ id: "s-earll", legal_name: "5151 Earll, LLC", description: "Scottsdale house",
        entity_group: "holding" }),   // dormant: name only
];

const counts = {
  total: entities.length,
  operating: entities.filter((e) => e.entity_group === "operating").length,
  holding: entities.filter((e) => e.entity_group === "holding").length,
  dormant: entities.filter((e) => !e.tracking_ready).length,
};

export default {
  entities,
  businesses: [
    { id: "b-ulrg", key: "ulrg", name: "ULRG + Team" },
    { id: "b-springb", key: "springb", name: "Spring B" },
    { id: "b-sympli", key: "sympli", name: "Sympli Mortgage" },
  ],
  counts,
};
