# Google sign-in: turning it on

Phase 3.2 of `AXCION-REBRAND-SPEC.md`. ~15 minutes, all in Google Cloud Console plus two
Railway variables. Nothing here is risky and nothing here is irreversible.

**Current state:** `GOOGLE_REDIRECT_URI` is already set to the new domain.
`GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` are **not set**, so Google sign-in is off
everywhere and no workspace is offered the button.

---

## Two things to understand first, because they decide the setup

**1. One Google app, owned by Axcion — not one per customer.** Every workspace signs in
through the same OAuth client. A team buying a portal should not have to open Google Cloud
before their agents can log in. The consequence, stated plainly: **the consent screen will say
Axcion**, and if Google ever suspended the app, Google sign-in would stop for every workspace
at once. Password sign-in does not depend on it and keeps working.

What a workspace still chooses is whether the button appears at all, and which email domains
may use it. Those live in that workspace's own console, not in Google Cloud.

**2. Signing in with Google MATCHES an account; it never creates one.** A successful Google
sign-in finds a user somebody already invited to that workspace, or it fails. It will not
provision anyone. So turning this on cannot let a stranger into a workspace, even one with an
address at an allowed domain.

---

## Step 1 — Pick or create the Google Cloud project

1. Go to **console.cloud.google.com**, signed in as your `@axcion.io` Workspace admin.
2. Create a project (or reuse one) named **Axcion**. The project name is internal — nobody
   signing in ever sees it.

---

## Step 2 — The consent screen

**APIs & Services → OAuth consent screen.**

> ### ⚠️ Choose **External**, not Internal
> You have a Google Workspace on `axcion.io`, so Google will offer **Internal** and it looks
> like the tidier answer. It is the wrong one. Internal restricts sign-in to `@axcion.io`
> accounts only — which means you could sign in and **no customer ever could**. Utah Life's
> agents have their own email domains. It must be External.

Fill in:

| Field | Value |
|---|---|
| App name | `Axcion` — this is the name on the consent screen |
| User support email | `hello@axcion.io` |
| App logo | optional; skip for now |
| Application home page | `https://www.axcion.io` |
| Privacy policy link | `https://www.axcion.io/privacy` |
| Terms of service link | `https://www.axcion.io/terms` |
| Authorized domain | `axcion.io` |
| Developer contact | `hello@axcion.io` |

**Scopes: add nothing.** The app asks for `openid`, `email` and `profile`, which Google
treats as non-sensitive and grants by default. You do not need to add them by hand and you do
not need to justify them.

**Publishing status: click Publish App.** In *Testing* only accounts you list by hand can sign
in, which is a confusing failure for a customer — they get "access blocked" with no way to act
on it.

> **You do not need Google's verification review.** That is required for *sensitive* and
> *restricted* scopes. `openid email profile` is neither, so an app using only those can be
> published without going through review.

---

## Step 3 — Create the OAuth client

**APIs & Services → Credentials → Create Credentials → OAuth client ID.**

- **Application type:** Web application
- **Name:** `Axcion portal` (internal label)
- **Authorized JavaScript origins:** **leave empty.** The browser never talks to Google
  directly — it asks the API for a URL and follows the redirect. Adding origins here is
  harmless but pointless, and their absence is not what is wrong if something fails.
- **Authorized redirect URIs:** exactly one, character for character:

```
https://api.axcion.io/api/v1/auth/google/callback
```

> No trailing slash. No `www`. It must match `GOOGLE_REDIRECT_URI` on the API service byte for
> byte — Google compares it as a string, and a mismatch fails with `redirect_uri_mismatch`,
> which names the problem clearly if you read it.

Click Create. Google shows the **client ID** and **client secret**. Keep the tab open.

---

## Step 4 — Two variables on Railway

Railway → `glorious-wholeness` → **`executive_dashboard_v1`** → Variables:

| Variable | Value |
|---|---|
| `GOOGLE_CLIENT_ID` | the client ID (ends `.apps.googleusercontent.com`) |
| `GOOGLE_CLIENT_SECRET` | the client secret |

Or:

```bash
railway variables --service executive_dashboard_v1 --set "GOOGLE_CLIENT_ID=...apps.googleusercontent.com" --set "GOOGLE_CLIENT_SECRET=..."
```

> **Both, or neither.** The app checks for both halves together. One set and the other blank
> counts as not configured: no button appears and nothing errors, which looks exactly like
> having done nothing at all.

`GOOGLE_REDIRECT_URI` is already correct — don't touch it.

Setting these redeploys the API. Wait for it to come up.

---

## Step 5 — Turn it on for a workspace

Being configured at the platform makes the button *possible*; each workspace still opts in.

In that workspace's console → **Integrations → Google sign-in**:

- **It is already on.** Every workspace has Google sign-in enabled by default, so there is no
  Enable button to find — the panel offers **Turn off Google sign-in**, which is the whole
  control. If the button is missing from the sign-in page, the workspace is not why.
- **Allowed email domains** — comma separated; blank allows any.

There are no credentials to enter here, and the form refuses a body that carries one rather
than quietly ignoring it — so an old form cannot look as though it saved something.

---

## Step 6 — Test it

1. Open a workspace sign-in page, e.g. `https://springb.axcion.io`. A **Continue with Google**
   button should now appear.
2. Click it. The consent screen should say **Axcion** and ask only for your name and email.
3. Sign in with an address that **already has an account in that workspace**. You should land
   in the dashboard.
4. Then try one that does **not** have an account. It should fail cleanly — that is the
   matching rule doing its job, not a bug.

### If something goes wrong

| What you see | What it means |
|---|---|
| No button, and the panel says "Google sign-in isn't available yet" | The platform half is not configured. **Check the variable NAMES, not just their values** — a misspelling reads as unset, and the app then treats the pair as absent. This happened for real: `GOOGLE_CLIENT_SECTRET`, with an extra T, looked completely set in the Railway list and meant nothing. Confirm with `curl https://api.axcion.io/api/v1/auth/google/config -H "X-Tenant-Host: <slug>.axcion.io"` — `{"enabled":false}` is the platform half, every time. |
| No button, panel does NOT say "isn't available yet" | Then it is the workspace: somebody turned it off here. |
| `redirect_uri_mismatch` | The URI in Google Cloud differs from `GOOGLE_REDIRECT_URI`. Compare them character by character — a trailing slash does it. |
| "Access blocked: this app is not verified" | The consent screen is still in *Testing*. Publish it (Step 2). |
| Sign-in completes but bounces back | The address has no account in that workspace, or its domain is not in the allowed list. |

A failed Google round trip returns to the sign-in page with `?google_error=<code>`, and the
page translates it into something readable rather than showing the raw code.

---

## Done when

- [ ] Consent screen is **External**, published, and says Axcion
- [ ] Redirect URI matches `GOOGLE_REDIRECT_URI` exactly
- [ ] Both Railway variables set; API redeployed
- [ ] Button appears on a workspace that enabled it
- [ ] An invited address signs in; an uninvited one is refused

Then tick **3.2–3.6** in `AXCION-REBRAND-SPEC.md`.

---

## One thing to come back to

The old `acumyn.io` redirect URI does not exist in Google Cloud — this client is being created
fresh, after the cutover, so there is nothing to clean up at Phase 10. If you ever *do* add the
old URI for testing, remove it when you retire the domain.
