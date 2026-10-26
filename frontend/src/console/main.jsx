import React from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";

import Console from "./Console.jsx";
import { writeToken } from "../session.js";
import "./console.css";

/* COMING BACK FROM GOOGLE. The session arrives in the URL FRAGMENT rather than the query string,
 * because a fragment is never sent to a server -- it stays out of access logs, Referer headers
 * and anything sitting in front of the app.
 *
 * Consumed at module load, before <Console> reads hasToken(), so the first render is already
 * signed in rather than flashing the sign-in page. `remember: true` matches the token the server
 * minted for this round trip, which it issues remembered.
 *
 * replaceState, not pushState: a token left in history would come back on the back button and
 * survive in a bookmark. */
function consumeGoogleRedirect() {
  const found = (window.location.hash || "").match(/[#&](?:google_token|session)=([^&]+)/);
  if (!found) return;
  try {
    writeToken(decodeURIComponent(found[1]), true);
  } catch { /* a malformed fragment is just not a sign-in */ }
  window.history.replaceState({}, "", window.location.pathname + window.location.search);
}
consumeGoogleRedirect();

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: false,
      refetchOnWindowFocus: false,
    },
  },
});

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter basename="/console">
        <Console />
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>
);
