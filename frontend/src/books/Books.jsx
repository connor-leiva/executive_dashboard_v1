/* Books — the bookkeeping module shell. Mounted at /books/* (mirrors Settings): a header
   + sub-nav + nested Routes for Home / P&L / Queue / Intercompany. */
import { useEffect, useState } from "react";
import { Routes, Route, Navigate, NavLink, Link } from "react-router-dom";
import { T } from "../theme.js";
import { getJSON } from "../api";
import { font } from "./ui.jsx";
import BooksHome from "./BooksHome.jsx";
import BooksPL from "./BooksPL.jsx";
import BooksQueue from "./BooksQueue.jsx";
import BooksIC from "./BooksIC.jsx";

const API = import.meta.env.VITE_API_BASE;
const PERIODS = [["mtd", "This month"], ["qtd", "Quarter"], ["ytd", "Year"], ["last_month", "Last month"]];
const SUBNAV = [["/books", "Home", true], ["/books/pl", "P&L"], ["/books/queue", "Queue"], ["/books/intercompany", "Intercompany"]];

function PeriodSelector({ period, setPeriod }) {
  return (
    <div style={{ display: "inline-flex", gap: 3, background: T.white, border: `1px solid ${T.line}`,
                  borderRadius: 9, padding: 3 }}>
      {PERIODS.map(([k, label]) => (
        <button key={k} onClick={() => setPeriod(k)} style={{ fontFamily: font.body, fontSize: 12,
          fontWeight: 600, cursor: "pointer", borderRadius: 7, padding: "5px 10px", border: "none",
          color: period === k ? T.ink : T.muted, background: period === k ? T.parchment : "transparent" }}>
          {label}</button>
      ))}
    </div>
  );
}

function Shell({ period, setPeriod, children }) {
  return (
    <div style={{ minHeight: "100vh", background: T.parchment }}>
      <div style={{ maxWidth: 980, margin: "0 auto", padding: "26px 24px 60px" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: 12 }}>
          <div>
            <Link to="/" style={{ fontFamily: font.body, fontSize: 12.5, color: T.muted, textDecoration: "none" }}>
              ← Command Center</Link>
            <div style={{ fontFamily: font.head, fontSize: 25, fontWeight: 700, color: T.ink, marginTop: 4 }}>
              Acumyn Books</div>
            <div style={{ fontFamily: font.body, fontSize: 13, color: T.muted, marginTop: 2 }}>
              The books, run — captured, categorized, approved, closed.</div>
          </div>
          <PeriodSelector period={period} setPeriod={setPeriod} />
        </div>

        <div style={{ display: "flex", gap: 6, margin: "20px 0 22px", borderBottom: `1px solid ${T.line}`, flexWrap: "wrap" }}>
          {SUBNAV.map(([to, label, end]) => (
            <NavLink key={to} to={to} end={end} style={({ isActive }) => ({
              fontFamily: font.body, fontSize: 13.5, fontWeight: isActive ? 700 : 500,
              color: isActive ? T.ink : T.slate, textDecoration: "none", padding: "8px 12px",
              borderBottom: `2px solid ${isActive ? T.evergreen : "transparent"}`, marginBottom: -1 })}>
              {label}</NavLink>
          ))}
        </div>

        {children}
      </div>
    </div>
  );
}

export default function Books() {
  const [role, setRole] = useState(API ? null : "owner");
  const [period, setPeriod] = useState("mtd");
  useEffect(() => {
    if (!API) return;
    getJSON("/me").then((m) => setRole(m.role)).catch(() => setRole("member"));
  }, []);
  const isCFO = role === "owner" || role === "admin";

  return (
    <Shell period={period} setPeriod={setPeriod}>
      <Routes>
        <Route index element={<BooksHome period={period} />} />
        <Route path="pl" element={<BooksPL period={period} />} />
        <Route path="queue" element={<BooksQueue isCFO={isCFO} />} />
        <Route path="intercompany" element={<BooksIC isCFO={isCFO} />} />
        <Route path="*" element={<Navigate to="/books" replace />} />
      </Routes>
    </Shell>
  );
}
