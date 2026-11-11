import { createRoot } from "react-dom/client";
import AttendanceView from "../src/attendance/AttendanceView.jsx";
import { applyBrand } from "../src/palette.js";

applyBrand();
createRoot(document.getElementById("root")).render(<AttendanceView />);
