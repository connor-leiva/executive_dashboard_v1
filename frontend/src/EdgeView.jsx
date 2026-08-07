/* The Edge tab — Spring + Justin Nelson's membership product, a full Forum replica. It reuses
   the shared operational ForumView engine, remapping every Forum drill key to The Edge's own
   (edge_*) key so no drill-down or roll-up leaks Forum/beCollective data. Steady membership, so
   there is no cohort-Launch sub-nav (unlike beCollective). */
import ForumView from "./ForumView.jsx";

const EDGE_DRILL_MAP = {
  forum_roster: "edge_roster", forum_arr: "edge_arr", active_members: "edge_members",
  registered: "edge_registered", new_members: "edge_new_members",
  renewal_book: "edge_renewal_book", renewals_due: "edge_renewals_due", unregistered: "edge_unregistered",
  forum_payments: "edge_payments", forum_failed_payments: "edge_failed_payments",
  forum_mrr_subs: "edge_mrr_subs", forum_installments: "edge_installments", forum_next30: "edge_next30",
  forum_streams: "edge_streams", forum_cashflow: "edge_cashflow",
  mrr: "edge_mrr", monthly: "edge_monthly", pastdue: "edge_pastdue",
};

export default function EdgeView({ data, area, onDrill, deckSlots, drillBusiness = "springb",
                                  rosterKey = "edge_roster" }) {
  return (
    <ForumView key="edge" data={data} area={area} onDrill={onDrill}
      title="The Edge" subtitle="Membership" deckSlots={deckSlots}
      drillBusiness={drillBusiness} rosterKey={rosterKey} drillMap={EDGE_DRILL_MAP} />
  );
}
