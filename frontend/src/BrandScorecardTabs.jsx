import { useState } from "react";
import SubTabs from "./SubTabs.jsx";
import Scorecard from "./ulrg/Scorecard.jsx";

/* Wraps a bare brand tab (Forum, The Edge) with a sub-tab switcher so the shared Spring B L10
   Scorecard is reachable from inside it — the SAME board shows in every brand tab (beCollective adds
   the same "Scorecard" tab to its own sub-nav). One source of truth, rendered wherever you are. */
export default function BrandScorecardTabs({ overview, role }) {
  const [sub, setSub] = useState("overview");
  const tabs = [{ k: "overview", label: "Overview" }, { k: "scorecard", label: "Scorecard" }];
  return (
    <div>
      <SubTabs tabs={tabs} active={sub} onChange={setSub} />
      {sub === "overview" ? overview : <Scorecard scope="springb" role={role} />}
    </div>
  );
}
