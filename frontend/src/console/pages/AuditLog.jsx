import { ROUTE_TITLES } from "../constants.js";
import { ParkedScreen } from "../ui.jsx";

export default function AuditLog() {
  return <ParkedScreen title={ROUTE_TITLES["/audit"]} />;
}
