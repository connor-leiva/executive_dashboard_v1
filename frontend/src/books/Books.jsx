/* Books — the bookkeeping module, rendered as a Command Center view (a side-nav tab).
   Header + sub-nav + the active page, all internal state (mirrors the mockup). */
import { useState } from "react";
import { ProductIcon, iconFor } from "../brand/productIcons.jsx";
import { useBooksHome } from "./useBooks.js";
import BooksHome from "./BooksHome.jsx";
import BooksPL from "./BooksPL.jsx";
import BooksQueue from "./BooksQueue.jsx";
import BooksIC from "./BooksIC.jsx";
import BooksMapping from "./BooksMapping.jsx";
import BooksStatement from "./BooksStatement.jsx";
import { font, T } from "./ui.jsx";

const PAGE_TITLE = { home: null, pl: "Profit & loss", queue: "Approval queue",
  ic: "Intercompany", mapping: "Chart mapping", statement: "Statement" };

function SubNav({ page, setPage, counts }) {
  const items = [["home", "Home"], ["pl", "P&L"],
    ["queue", `Queue${counts.queue ? ` · ${counts.queue}` : ""}`],
    ["ic", `Intercompany${counts.ic ? ` · ${counts.ic}` : ""}`],
    ["mapping", "Mapping"], ["statement", "Statement"]];
  return (
    <div style={{ display: "flex", gap: 4, borderBottom: `1px solid ${T.line}`, marginBottom: 18, flexWrap: "wrap" }}>
      {items.map(([k, l]) => (
        <button key={k} onClick={() => setPage(k)} style={{ fontFamily: font.head, fontSize: 13, fontWeight: 600,
          color: page === k ? T.ink : T.muted, background: "transparent", border: "none",
          borderBottom: page === k ? `2.5px solid ${T.meadow}` : "2.5px solid transparent",
          padding: "9px 15px 11px", cursor: "pointer", marginBottom: -1 }}>{l}</button>
      ))}
    </div>
  );
}

export default function Books({ period = "mtd", role }) {
  const [page, setPage] = useState("home");
  const home = useBooksHome(period);
  const isCFO = !role || role === "owner" || role === "admin";
  const counts = {
    queue: home.data?.tiles?.queue?.count || 0,
    ic: home.data?.tiles?.ic?.open || 0,
  };
  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap", marginBottom: 16 }}>
        {/* The module's own mark, where a business page carries an accent bar. The rail draws
            the same distinction; a header that disagreed with it would be worse than either. */}
        <ProductIcon name={iconFor("books")} size={24} tone={T.meadow} />
        <span style={{ fontFamily: font.head, fontSize: 22, fontWeight: 600, color: T.ink }}>
          Books{PAGE_TITLE[page] && <span style={{ color: T.muted, fontWeight: 500 }}> / {PAGE_TITLE[page]}</span>}</span>
      </div>

      <SubNav page={page} setPage={setPage} counts={counts} />

      {page === "home" && <BooksHome home={home} go={setPage} isCFO={isCFO} />}
      {page === "pl" && <BooksPL period={period} />}
      {page === "queue" && <BooksQueue isCFO={isCFO} />}
      {page === "ic" && <BooksIC isCFO={isCFO} />}
      {page === "mapping" && <BooksMapping isCFO={isCFO} />}
      {page === "statement" && <BooksStatement period={period} />}
    </div>
  );
}
