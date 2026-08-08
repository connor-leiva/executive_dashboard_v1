/* ULRG sub-tab shell (SPEC 1.1): Overview | Scorecard | <one tab per team>. The team tabs are
   generated from the API (a tenant with two teams shows two), each with its leader as the sub
   line. Overview renders the existing ULRG view unchanged. Team Rooms are Step 6. */
import { useState } from "react";
import SubTabs from "../SubTabs.jsx";
import Scorecard from "./Scorecard.jsx";
import { useScorecard } from "./useScorecard.js";
import { Card } from "./Parts.jsx";
import { C, FD, FB } from "./scorecardMath.js";

export default function UlrgTabs({ overview, role }) {
  const [sub, setSub] = useState("overview");
  const { data } = useScorecard(13);                       // for the team list (generated from API)
  const teams = ((data && data.groups) || []).filter((g) => g.is_team_room);

  const tabs = [
    { k: "overview", label: "Overview" },
    { k: "scorecard", label: "Scorecard" },
    ...teams.map((g) => {
      const leader = g.owner && g.owner.name;
      return { k: `team:${g.key}`, label: g.name, sub: leader || "Unassigned", unassigned: !leader };
    }),
  ];
  const team = sub.startsWith("team:") && teams.find((t) => `team:${t.key}` === sub);

  return (
    <div>
      <SubTabs tabs={tabs} active={sub} onChange={setSub} />
      {sub === "overview" && overview}
      {sub === "scorecard" && <Scorecard role={role} />}
      {team && (
        <Card style={{ textAlign: "center", padding: "40px 28px" }}>
          <div style={{ fontFamily: FD, fontSize: 16, fontWeight: 600, color: C.ink }}>{team.name} Team Room</div>
          <div style={{ fontFamily: FB, fontSize: 12.5, color: C.slate, marginTop: 8, maxWidth: 460, marginInline: "auto" }}>
            {(team.owner && team.owner.name) || "This team"}’s monthly pace metric, rocks, and worklists land here next.
            The Scorecard tab is live now.
          </div>
        </Card>
      )}
    </div>
  );
}
