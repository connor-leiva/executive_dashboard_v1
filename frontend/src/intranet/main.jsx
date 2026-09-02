import React from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import IntranetApp from "./IntranetApp.jsx";
import "./ui.css";

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <BrowserRouter basename="/intranet">
      <IntranetApp />
    </BrowserRouter>
  </React.StrictMode>
);
