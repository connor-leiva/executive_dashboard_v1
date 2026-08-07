/* beCollective tab shell — an [Overview | Launch] sub-nav over the existing operational
   ForumView (Overview) and the cohort Launch section. The Launch tab appears only when an
   active launch exists (SPEC-becollective-launch §2: no empty shell). */
import { useEffect, useState } from "react";
import ForumView from "./ForumView.jsx";
import LaunchSection, { LaunchEmpty } from "./LaunchSection.jsx";
import { useLaunch } from "./useLaunch.js";
import { T } from "./theme.js";
import SubTabs from "./SubTabs.jsx";

/* beCollective reuses the Forum's operational component. This remaps every Forum drill key
   to beCollective's own (bc_*) key, so no drill-down or roll-up leaks Forum data. */
const BC_DRILL_MAP = {
  forum_roster: "bc_roster", forum_arr: "bc_arr", active_members: "bc_members",
  registered: "bc_registered", new_members: "bc_new_members",
  renewal_book: "bc_renewal_book", renewals_due: "bc_renewals_due", unregistered: "bc_unregistered",
  forum_payments: "bc_payments", forum_failed_payments: "bc_failed_payments",
  forum_mrr_subs: "bc_mrr_subs", forum_installments: "bc_installments", forum_next30: "bc_next30",
  forum_streams: "bc_streams", forum_cashflow: "bc_cashflow",
  mrr: "bc_mrr", monthly: "bc_monthly", pastdue: "bc_pastdue",
};

function SubNav({ page, setPage, hasLaunch }) {
  const tabs = [{ k: "overview", label: "Overview" }];
  if (hasLaunch) tabs.push({ k: "launch", label: "Launch" });
  return <SubTabs tabs={tabs} active={page} onChange={setPage} />;   // one shared switcher
}

export default function BecollectiveView({ data, area, onDrill, deckSlots, drillBusiness = "springb",
                                          rosterKey = "bc_roster", role }) {
  const [page, setPage] = useState("overview");
  const launch = useLaunch(drillBusiness);
  const isEditor = !role || role === "owner" || role === "admin";
  // Editors always get the Launch tab (to create one); everyone else only when it exists.
  const showLaunchTab = (launch.exists && (launch.data || launch.loading)) || isEditor;

  // If the launch is absent AND the user can't create one, fall back to Overview.
  useEffect(() => {
    if (page === "launch" && launch.exists === false && !isEditor) setPage("overview");
  }, [page, launch.exists, isEditor]);

  const overview = (
    <ForumView key="becollective" data={data} area={area} onDrill={onDrill}
      title="beCollective" subtitle="Community" deckSlots={deckSlots}
      drillBusiness={drillBusiness} rosterKey={rosterKey} drillMap={BC_DRILL_MAP} />
  );

  let launchPane = overview;
  if (page === "launch") {
    if (launch.data) {
      launchPane = <LaunchSection data={launch.data} usingSample={launch.usingSample} role={role}
        businessKey={drillBusiness} onSaved={launch.reload} />;
    } else if (launch.exists === false && isEditor) {
      launchPane = <LaunchEmpty businessKey={drillBusiness} onCreated={launch.reload} />;
    }
  }

  return (
    <div>
      <SubNav page={page} setPage={setPage} hasLaunch={showLaunchTab} />
      {launchPane}
    </div>
  );
}
