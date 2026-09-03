import { ROUTE_TITLES } from "../constants.js";
import { ParkedScreen } from "../ui.jsx";

export default function Roster() {
  return <ParkedScreen title={ROUTE_TITLES["/roster"]} />;
}
