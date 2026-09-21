import React from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import IntranetApp from "./IntranetApp.jsx";
import { adoptViewAs } from "../api.js";
import "./ui.css";

// Before the first request: an Axcion support view arrives as `#view-as=` from the operator
// console, and every call this tab makes must carry it (see api.js).
adoptViewAs();

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <BrowserRouter basename="/intranet">
      <IntranetApp />
    </BrowserRouter>
  </React.StrictMode>
);
