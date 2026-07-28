/* beCollective tab shell — an [Overview | Launch] sub-nav over the existing operational
   ForumView (Overview) and the cohort Launch section. The Launch tab appears only when an
   active launch exists (SPEC-becollective-launch §2: no empty shell). */
import { useEffect, useState } from "react";
import ForumView from "./ForumView.jsx";
import LaunchSection from "./LaunchSection.jsx";
import { useLaunch } from "./useLaunch.js";
import { T } from "./theme.js";

function SubNav({ page, setPage, hasLaunch }) {
  const items = [["overview", "Overview"]];
  if (hasLaunch) items.push(["launch", "Launch"]);
  if (items.length < 2) return null;               // nothing to switch to
  return (
    <div style={{ display: "flex", gap: 4, borderBottom: `1px solid ${T.line}`, marginBottom: 18, flexWrap: "wrap" }}>
      {items.map(([k, l]) => (
        <button key={k} onClick={() => setPage(k)} style={{
          fontFamily: "Poppins,sans-serif", fontSize: 13, fontWeight: 600,
          color: page === k ? T.ink : T.muted, background: "transparent", border: "none",
          borderBottom: page === k ? `2.5px solid ${T.meadow}` : "2.5px solid transparent",
          padding: "9px 15px 11px", cursor: "pointer", marginBottom: -1 }}>{l}</button>
      ))}
    </div>
  );
}

export default function BecollectiveView({ data, area, onDrill, deckSlots, drillBusiness = "springb",
                                          rosterKey = "bc_roster", role }) {
  const [page, setPage] = useState("overview");
  const launch = useLaunch(drillBusiness);
  const hasLaunch = launch.exists && (launch.data || launch.loading);

  // If the launch disappears (404) while the Launch tab is open, fall back to Overview.
  useEffect(() => {
    if (page === "launch" && launch.exists === false) setPage("overview");
  }, [page, launch.exists]);

  const overview = (
    <ForumView key="becollective" data={data} area={area} onDrill={onDrill}
      title="beCollective" subtitle="Community" deckSlots={deckSlots}
      drillBusiness={drillBusiness} rosterKey={rosterKey} />
  );

  return (
    <div>
      <SubNav page={page} setPage={setPage} hasLaunch={hasLaunch} />
      {page === "launch" && launch.data
        ? <LaunchSection data={launch.data} usingSample={launch.usingSample} role={role}
            businessKey={drillBusiness} onSaved={launch.reload} />
        : overview}
    </div>
  );
}
