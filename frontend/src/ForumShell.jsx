/* Forum tab shell — [Overview | Event | Scorecard].

   Cloned from BecollectiveView rather than added to BrandScorecardTabs, which is SHARED by the
   Forum and The Edge: a tab added there appears in both, and The Edge does not run these events.
   That sharing is also why BrandScorecardTabs stays exactly as it is — The Edge still mounts it.

   The Event tab is conditionally present, like beCollective's Launch tab: absent until an event
   exists, because an empty shell renders as a tab full of zeroes on a workspace that has never
   run one. Editors still see it so they can create the first one. */
import { useEffect, useState } from "react";
import ForumView from "./ForumView.jsx";
import EventSection from "./EventSection.jsx";
import { useEvent } from "./useEvent.js";
import { T } from "./theme.js";
import SubTabs from "./SubTabs.jsx";
import Scorecard from "./ulrg/Scorecard.jsx";

export default function ForumShell({ data, area, onDrill, deckSlots, drillBusiness = "springb",
                                     rosterKey = "forum_roster", role }) {
  const [page, setPage] = useState("overview");
  const ev = useEvent(drillBusiness);
  const isEditor = !role || role === "owner" || role === "admin";
  const showEvent = (ev.exists && (ev.data || ev.loading)) || isEditor;

  // If the Event tab disappears under someone standing on it, fall back rather than render a
  // pane with nothing in it.
  useEffect(() => {
    if (page === "event" && ev.exists === false && !isEditor) setPage("overview");
  }, [page, ev.exists, isEditor]);

  const tabs = [{ k: "overview", label: "Overview" }];
  if (showEvent) tabs.push({ k: "event", label: "Event" });
  tabs.push({ k: "scorecard", label: "Scorecard" });

  const overview = (
    <ForumView key="forum" data={data} area={area} onDrill={onDrill}
      deckSlots={deckSlots} drillBusiness={drillBusiness} rosterKey={rosterKey} role={role} />
  );

  let pane = overview;
  if (page === "event") {
    if (ev.data) {
      pane = <EventSection data={ev.data} usingSample={ev.usingSample} role={role}
        businessKey={drillBusiness} onSaved={ev.reload} />;
    } else {
      pane = (
        <div style={{ padding: 20, color: T.muted, fontSize: 13, lineHeight: 1.6 }}>
          {ev.loading
            ? "Loading the event…"
            : "No event is set up yet. An event is a quarterly in-person gathering: it tracks VIP "
              + "guest registrations against a goal, the members attending beside them, and how "
              + "many guests convert to memberships."}
        </div>
      );
    }
  } else if (page === "scorecard") {
    pane = <Scorecard scope="springb" role={role} />;
  }

  return (
    <div>
      <SubTabs tabs={tabs} active={page} onChange={setPage} />
      {pane}
    </div>
  );
}
