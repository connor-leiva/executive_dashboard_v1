/* Books Cleanup data hooks -- the audited year, recomputed by the server from the decisions made on
   the tab (backend/app/services/books_cleanup.py). Nothing here computes a figure.

   Same reading rule as Books (useEndpoint): a payload is only shown for the path it came from, so
   switching company never leaves one company's name above another company's numbers.

   `rev` is the shell's save counter. Every page passes it, so a decision saved anywhere re-reads
   every figure on screen -- a split moves money between three companies at once, and a page still
   showing the old split would be wrong about all three.

   No sample data, deliberately: the tab exists only where the server says a file is loaded, so
   there is no offline version of it to fake. */
import { useEndpoint } from "../books/useBooks.js";
import { getBlob, postJSON, putJSON, uploadFile } from "../api.js";

const qs = (o) => new URLSearchParams(
  Object.entries(o).filter(([, v]) => v !== null && v !== undefined && v !== "")).toString();

export function useCleanupOverview(rev) {
  return useEndpoint("/cleanup", null, [rev]);
}

export function useCleanupStatement(view, month, rev) {
  return useEndpoint(view ? `/cleanup/statement?${qs({ view, month })}` : null, null, [view, month, rev]);
}

/* The dollars behind one line. Fetched on expand: a line can carry hundreds of rows. */
export function useCleanupLine(view, line, month, rev) {
  return useEndpoint(view && line ? `/cleanup/statement/line?${qs({ view, line, month })}` : null, null,
                     [view, line, month, rev]);
}

/* The months after the books end (Forecast, Cash flow, the Statement's future months) and every company's
   budget lines. */
export function useCleanupForward(rev) {
  return useEndpoint("/cleanup/forward", null, [rev]);
}

/* Every transaction in the year, with its words and where the books put it (one gzipped read). */
export function useCleanupTransactions(rev) {
  return useEndpoint("/cleanup/transactions", null, [rev]);
}

export function useCleanupCorrections(company, rev) {
  return useEndpoint(company ? `/cleanup/corrections?${qs({ company })}` : null, null, [company, rev]);
}

export function useCleanupBalance(company, rev) {
  return useEndpoint(company ? `/cleanup/balance?${qs({ company })}` : null, null, [company, rev]);
}

export function useCleanupInterco(rev) {
  return useEndpoint("/cleanup/intercompany", null, [rev]);
}

/* Two companies, item by item: every transaction, share and change behind the cleanup's change to what one owes
   the other. Fetched on expand: the busiest pair is a few hundred rows. */
export function useCleanupPair(pair, rev) {
  return useEndpoint(pair ? `/cleanup/intercompany/pair?${qs({ pair })}` : null, null, [pair, rev]);
}

/* Ask about this. `about` is {kind: "tx", key} | {kind: "line", view, line, month} | {kind: "pair", pair}.
   Resolves to {answer, model}. */
export function askCleanup(about, question, history) {
  return postJSON("/cleanup/ask", { about, question, history });
}

export function useCleanupDecisions(rev) {
  return useEndpoint("/cleanup/decisions", null, [rev]);
}

export function useCleanupItems(rev) {
  return useEndpoint("/cleanup/items", null, [rev]);
}

/* One set of changes, applied all or nothing by the server: a person's whole month, say, so a split
   never sits at 150% between two saves. Resolves to {version, checks, net_income}. */
export function saveChanges(changes) {
  return putJSON("/cleanup/inputs", { changes });
}

export function uploadCleanupFile(file) {
  const fd = new FormData();
  fd.append("file", file);
  return uploadFile("/cleanup/dataset", fd);
}

/* The entries as a QuickBooks journal-entry import. Fetched with the session's headers and handed
   over as a blob: a plain link is a navigation with no Authorization header (see BooksPayables). */
export async function downloadEntries(company, filename) {
  saveBlob(await getBlob(`/cleanup/corrections.csv?${qs({ company })}`), filename);
}

/* Every change saved on the tab, as one JSON file (owners and admins; the server audits it): the file they
   apply to plus these changes rebuild every figure, so an analysis outside the app starts from them. */
export async function downloadSavedChanges(filename) {
  saveBlob(await getBlob("/cleanup/saved.json"), filename);
}

function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
