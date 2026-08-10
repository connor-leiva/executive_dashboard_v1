/* Public, no-login embed page for a ClickUp iframe (SPEC 5.3 / Step 8). Renders the REAL
   Scorecard component in share mode — identical look + the collapsible cumulative panel — reading
   from the token endpoint. No app nav, no sidebar, no route back in. */
import { useParams } from "react-router-dom";
import Scorecard from "./Scorecard.jsx";
import { T } from "./l10tokens.jsx";

export default function ShareScorecard() {
  const { token } = useParams();
  return (
    <div style={{ minHeight: "100vh", background: T.shell, padding: "20px 18px 48px" }}>
      <div style={{ maxWidth: 1280, margin: "0 auto" }}>
        <Scorecard shareToken={token} />
      </div>
    </div>
  );
}
