import { createRoot } from "react-dom/client";
import Scorecard from "../src/ulrg/Scorecard.jsx";
import { fixture } from "./fixture.js";

createRoot(document.getElementById("root")).render(
  <Scorecard role="owner" scope="sympli" fixture={fixture} />,
);
