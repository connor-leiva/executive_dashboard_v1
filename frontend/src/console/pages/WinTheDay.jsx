import { ROUTE_TITLES } from "../constants.js";
import { ParkedScreen } from "../ui.jsx";

export default function WinTheDay() {
  return <ParkedScreen title={ROUTE_TITLES["/wtd"]} />;
}
