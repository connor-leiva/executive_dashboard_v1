# Resend: moving sending to `mail.axcion.io`

Phase 3.3 of `AXCION-REBRAND-SPEC.md`, written to be followed start to finish in one sitting.
Budget ~20 minutes of work plus up to an hour of waiting for DNS.

**What you are doing:** teaching Resend to send as `mail.axcion.io`, proving it works, and
pointing the API at it. Sending from `mail.acumyn.io` keeps working the whole time, so there
is no window where email is broken.

---

## Before you start: two facts that decide most of this

**1. A subdomain is a different domain to an email provider.** Verifying `axcion.io` would
not let you send from `mail.axcion.io`, and owning the domain at GoDaddy proves nothing to
Resend. `mail.axcion.io` has to be added and verified in Resend on its own. If you skip this
and just point `MAIL_FROM` at it, every send fails `403 not authorized for this domain` —
which reads exactly like a bad API key and is not one.

**2. Your current Resend key is restricted to sending.** I checked: it returns
`401 This API key is restricted to only send emails` when asked to list domains. Restricted
keys are usually *also* scoped to one domain — almost certainly `mail.acumyn.io`. **If so it
will not send from `mail.axcion.io` and you need a new key.** Step 5 settles it.

---

## Step 0 — Back up the database first

Not Resend, but it belongs here, because **Step 6 triggers an API deploy** and that deploy
runs migration `0079`, which rewrites three stored values. Five minutes of insurance.

1. Railway → project `glorious-wholeness` → **Postgres** → **Backups**
2. **Create backup**, wait for it to complete
3. Note the time here: `________________`

---

## Step 1 — Add the domain in Resend

1. Go to **resend.com/domains**
2. **Add Domain**
3. Name: `mail.axcion.io` — exactly that, including `mail.`
4. Region: pick the one closest to your users. **US East (N. Virginia)** is the usual choice
   and matches where the app runs. Whatever you pick shows up inside the MX record's value,
   so note it.
5. **Add**

Resend now shows you a table of DNS records. **Leave this tab open** — you are about to copy
from it, and some of the values are long.

---

## Step 2 — The GoDaddy gotcha, before you paste anything

You will add each record at **GoDaddy → `axcion.io` → DNS → Records → Add New Record**.

> ### ⚠️ GoDaddy appends the domain for you
> Resend shows full hostnames like `send.mail.axcion.io`. GoDaddy's **Name** field wants the
> part *before* the domain. Paste the full hostname and you create
> `send.mail.axcion.io.axcion.io`, which resolves to nothing — and Resend just sits on
> "Pending" with no error telling you why.
>
> | Resend shows | Type in GoDaddy's **Name** |
> |---|---|
> | `send.mail.axcion.io` | `send.mail` |
> | `resend._domainkey.mail.axcion.io` | `resend._domainkey.mail` |
> | `mail.axcion.io` | `mail` |
>
> **Rule: delete the trailing `.axcion.io` from whatever Resend shows.** The **Value** column
> you paste exactly as given, unchanged.

---

## Step 3 — Create the records

Resend will show **three** records (names may differ slightly by region — copy what is on
your screen, use the table above only to translate the Name column):

| # | Type | Name becomes | Value | Notes |
|---|---|---|---|---|
| 1 | MX | `send.mail` | `feedback-smtp.<region>.amazonses.com` | Priority **10**. Handles bounces. |
| 2 | TXT | `send.mail` | `v=spf1 include:amazonses.com ~all` | SPF for the sending subdomain |
| 3 | TXT | `resend._domainkey.mail` | `p=MIGfMA0GCSq…` (very long) | DKIM. Copy with Resend's copy button — a hand-selection that drops a character fails silently. |

For each one: **Add New Record** → choose the Type → Name → Value → (Priority 10 for the MX)
→ **Save**. You can stage all three and hit **Save All Records** once.

### What NOT to touch

Your Google Workspace setup lives on the **apex** (`axcion.io`) and these new records live on
**`mail.axcion.io`**. Different names, no conflict. Leave every one of these alone:

- the **5 MX records on `@`** pointing at `aspmx.l.google.com` and friends — this is what
  makes `hello@axcion.io` receive mail, and Step 6 depends on it
- the `google-site-verification` TXT on `@`
- `google._domainkey`
- the SPF pair: `@` → `v=spf1 include:dc-aa8e722993._spfm.axcion.io ~all`, and
  `dc-aa8e722993._spfm` → `v=spf1 include:_spf.google.com ~all`
- the `*`, `_acme-challenge`, `api` and `_railway-verify` records — that is Railway

**You do not need to touch `_dmarc`.** I checked the existing one:
`v=DMARC1; p=quarantine; adkim=r; aspf=r; …`. Both alignment modes are **relaxed**, which
means DKIM signed by `mail.axcion.io` already aligns with `axcion.io`. Had it been strict
(`adkim=s`), Resend's mail would have failed DMARC and gone to spam. It isn't. Nothing to do.

---

## Step 4 — Wait for Verified

Back on **resend.com/domains** → click `mail.axcion.io` → **Verify DNS Records**.

- GoDaddy records usually propagate in a few minutes; allow up to an hour.
- **Do not go to Step 6 while it says Pending.** Pointing `MAIL_FROM` at an unverified domain
  makes every invite and password reset fail.
- Still pending after an hour? Almost always the Name column. Check for the doubled domain
  (`…axcion.io.axcion.io`) described in Step 2.

You can check from your side at any point:

```bash
nslookup -type=TXT resend._domainkey.mail.axcion.io 8.8.8.8
```

That returning your DKIM value means GoDaddy has published it and the ball is in Resend's court.

---

## Step 5 — Settle the API key

Go to **resend.com/api-keys** and look at the key the app is using (36 characters, starts
`re_`). Check its **Permission** and its **Domain**.

- **Domain = `mail.acumyn.io`** → it will *not* send from the new domain. Create a new one.
- **Domain = All domains** → it already works. Skip to Step 6 and leave `RESEND_API_KEY` alone.

To create one: **Create API Key** → Name `axcion-sending` → Permission **Sending access** →
Domain **`mail.axcion.io`** (or **All domains** if you want one key to cover both during the
transition — simpler, and it means you are not changing the key and the FROM address in the
same move).

> **Resend shows a key exactly once.** Copy it straight into Railway in Step 6. A partial
> paste produces `400 validation_error: API key is invalid`, which looks like a wrong key and
> is really a truncated one. If you lose it, reissue rather than guess.

---

## Step 6 — Point the API at it

Railway → `glorious-wholeness` → **`executive_dashboard_v1`** → **Variables**.

| Variable | Set to |
|---|---|
| `MAIL_FROM` | `Axcion <hello@mail.axcion.io>` |
| `MAIL_REPLY_TO` | `hello@axcion.io` |
| `RESEND_API_KEY` | the new key — **only if Step 5 said you need one** |

Or from your terminal:

```bash
railway variables --service executive_dashboard_v1 --set "MAIL_FROM=Axcion <hello@mail.axcion.io>" --set "MAIL_REPLY_TO=hello@axcion.io"
```

### `MAIL_REPLY_TO` is new, and it fixes a real hole

It has always been empty, because `acumyn.io` had no MX and nothing could receive. A password
reset deliberately sets no per-message reply-to, so **replies to password resets have been
going nowhere**. `axcion.io` runs Google Workspace, so `hello@axcion.io` is a real inbox and
this closes it. (Invites override it with the inviter's own address, so those already worked.)

### What this deploy actually does

Changing a variable redeploys the service from `main`, so this ships the rebrand backend:

- **migration `0079` runs**, renaming the stored typeface, the Win-the-Day playbook format,
  and the support account's address. Expected: 1 row each. This is why Step 0 exists.
- emails begin saying **Axcion**
- the TOTP issuer becomes **Axcion** — already-enrolled authenticator apps keep working and
  keep showing "Acumyn" until someone re-enrols; nothing breaks
- **workspace addresses do not change.** Everyone still signs in at `{slug}.acumyn.io`. The
  domain move is Phase 9 and is a different set of variables.

---

## Step 7 — Prove it, twice

**First, ask the service what it sees:**

```bash
railway ssh --service executive_dashboard_v1 "python -m scripts.check_mail"
```

Reports the key's length and shape — never its value — plus the FROM domain. Enough to tell
"not set" from "truncated on paste".

**Then send a real one:**

```bash
railway ssh --service executive_dashboard_v1 "python -m scripts.check_mail you@example.com"
```

It prints Resend's exact answer and translates it. The two you might see:

| Error | What it actually means |
|---|---|
| `403 not authorized for this domain` | The key is **fine**. It is not scoped to `mail.axcion.io`. Go back to Step 5. |
| `400 API key is invalid` | Truncated paste. Reissue, do not retry. |

**Accepted is not delivered.** Then:

1. **resend.com/emails** — confirm a **Delivered** event, not just Sent
2. **Check the inbox** — first sends from a new domain are the most likely to be filtered.
   Look in spam; if it is there, that is a DMARC/DKIM signal worth stopping on
3. **Reply to it** — confirm the reply lands in `hello@axcion.io`. This is the bit that has
   never worked before, so it is the bit worth actually testing

---

## Done when

- [ ] Database backed up
- [ ] `mail.axcion.io` shows **Verified** in Resend
- [ ] Google Workspace MX on the apex untouched, `hello@axcion.io` still receives
- [ ] API key covers `mail.axcion.io`
- [ ] `MAIL_FROM` and `MAIL_REPLY_TO` set; the deploy came up clean
- [ ] `check_mail` with an address returns success
- [ ] A real email arrived **and a reply reached `hello@axcion.io`**

Then tick **3.7–3.13** in `AXCION-REBRAND-SPEC.md`. `mail.acumyn.io` stays in Resend until
Phase 10.8 — do not remove it yet; it is what is sending today.
