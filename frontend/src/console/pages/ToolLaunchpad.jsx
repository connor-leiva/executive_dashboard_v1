import { ROUTE_TITLES } from "../constants.js";
import { ParkedScreen } from "../ui.jsx";

export default function ToolLaunchpad() {
  return <ParkedScreen title={ROUTE_TITLES["/launchpad"]} />;
}
