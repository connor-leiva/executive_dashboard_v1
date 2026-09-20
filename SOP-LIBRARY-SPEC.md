# The SOP library: audit, spec and build guide

Connor asked (2026-09-20) for the same treatment Who's Who and Win the Day had: what the mockup
shows, what the product does today, and what it would take to close the gap.

The short version: **the mockup's SOP section is a reading experience, and the product's is a
filing cabinet.** The mockup's procedure is a page you read — numbered steps, a "do not skip"
warning, the tools it needs, the person to ask — and the library around it is browsable by
department with the recently-changed ones at the top. The product stores an uploaded document per
SOP and lists them in one flat table; a member's only way to read a procedure is to download it.
Nothing in the mockup's two screens is drawn from data the product keeps, except the title, the
category, the owner and the version label.

Everything here is a proposal. Nothing has been built. §9 is the list of decisions that are
Connor's, each with what I would choose.

Reference: the mockup is local-only at `frontend/brand-src/mockups/utah-life-intranet/`
(gitignored). The SOP screens are `template.html` lines 791–865 (the library) and 867–930 (one
procedure), with the data at 2133–2142 and 2735–2749. `make_variants.py` now writes `sops.html`
and `sop.html` beside the other screens, so both can be captured at 1440px the way the Win the Day
tabs were.

---

## 1. What the mockup specifies

### 1.1 The library (`sops`, lines 791–865)

- Eyebrow **Learn**, h1 **Standard Operating Procedures** (40px), and a lede that states the
  promise: *"Every procedure has one owner and a version number, so you always know who to ask and
  whether you're reading the current one."*
- **A left rail, 246px:**
  - a search box, *Find a procedure…*, matching title, blurb and owner;
  - **Departments**, one row each with a count, the chosen one in ink and the rest muted:
    All Procedures 9, Listings 2, Lead Gen 3, Transactions 2, Marketing 1, Client Care 1;
  - a card: *Something out of date? Tell the owner. Procedures change because someone in the field
    said so.* with a **Suggest a Change** button.
- **Changed This Month**, a dark panel listing the three most recently updated procedures: version,
  title, `owner · date`, and *Open →*.
- **A card grid** (`auto-fit, minmax(290px, 1fr)`), one card per procedure: the department as a
  chip, the version at the right, the title, a one-line blurb, and a footer with the owner's
  initials in a circle, their name, and the date it was last updated.

### 1.2 One procedure (`sop`, lines 867–930)

- **← All SOPs**, then two columns: the document, and a sidebar that sticks as you scroll.
- **The document:** `Listings · v3.1` as an eyebrow; the title at 36px; a meta strip of **Owner**,
  **Last updated** and **Applies to**; an intro paragraph; then **numbered steps** — a circled
  number, a step title, and a paragraph of detail (eight of them here); then a **Do Not Skip**
  callout in the page colour with a primary left border; then the acknowledgement row: a primary
  button *I've read this* which becomes *Acknowledged ✓*, and beside it either
  *"64 of 86 agents have acknowledged v3.1."* or *"Logged Aug 17, 2026. Sharida can see it."*
- **The sidebar:** *On This Page* (every step as `1 · Title`), *Tools you'll need* (pills: Sisu,
  Utah Life Drive, SkySlope, WFRMLS, Brivity, Media request), and *Questions On This?* — the
  owner's initials, name and role, and a **Send a Message** button.

### 1.3 What the mockup's data says an SOP is

Per procedure: title, department, owner (name + initials), last updated, version, a one-line
blurb, a step count, and an acknowledgement count. The AI context line calls the library
*"All 84 SOPs"*, so the nine on screen are a sample of a library roughly that size — which is the
single most important fact for §3.

---

## 2. Audit: what the product does today

### The member's SOP page (`IntranetApp.jsx:2343-2431`)

One panel, one flat table, one row per SOP: the category, the title, `· version · owner`, and
either an **Acknowledge** button or *Acknowledged {date}*. The title is a button that **downloads**
the current version (`Content-Disposition: attachment`, `intranet.py:1524`). That is the whole
feature. There is no search, no department filter, no grouping, no sort, no cards, no
recently-changed panel, no per-procedure page, and no way to read a procedure without leaving the
portal for whatever application opens the file.

Fields the payload already carries and the page never shows: `review_due_on` and `updated_at`.

### The admin's SOP library (`console/pages/SopLibrary.jsx`)

Health tiles (Current / Due within 30 days / Overdue), a list, a category panel, and a detail form
of exactly five fields — Title, Category, Owner, State (Draft / Live / Needs Review), Review due —
plus a version uploader (a label and a file) and Archive. Versions are listed with a Download.

### What exists underneath (`models.py:761-837`)

`intranet_sop_category` (name, sort), `intranet_sop` (title, category, owner, state, review_due_on,
current_version_id, archived_at), `intranet_sop_version` (version_label + **a required file**:
filename, storage key, content type, byte size) and `intranet_sop_acknowledgement` (one row per
member per **version**, unique, no un-acknowledge).

### The gap, item by item

| The mockup shows | Today |
|---|---|
| A readable procedure: intro, numbered steps, a callout | Nothing. An SOP body is an uploaded file; no text column exists on any SOP table |
| Department rail with counts, and filtering | Categories exist as data; the portal neither groups nor filters by them |
| Search within the library | None on the page (global search finds SOPs, and links to the list, not the procedure) |
| *Changed This Month* | Not derived anywhere |
| A card per procedure, with a one-line blurb | No description field exists |
| Owner as a face: initials, name, role, *Send a Message* | Owner is a name fragment in a table row |
| *Applies to* (e.g. Listing Agents) | No role audience on an SOP (courses have one; SOPs do not) |
| *Last updated* | `updated_at` is any row touch, and is not shown |
| *Tools you'll need* | Launchpad tiles exist, but nothing links a tool to a procedure |
| *On This Page* | Needs steps |
| *"64 of 86 agents have acknowledged v3.1"* | The count is computed for the console's API and rendered **nowhere**; `GET /sops/{id}` returns a hardcoded `0` (`console.py:3491`) |
| *Suggest a Change* | Nothing |

### Faults worth fixing whatever else is decided

- **F1 — "Publish" does not stage an SOP change.** Member visibility depends only on
  `published_at` being set, and `draft_dirty` is never read in any query. So once an SOP has been
  published, the next title, category, owner, state or version change is live to the team
  immediately, before anybody presses Publish; *Discard* does not revert it either
  (`console.py:4989-5002`). For a procedures library this is the difference between "being
  rewritten" and "in force".
- **F2 — SOP uploads are not checked.** Lesson handouts enforce a size limit and sniff the bytes;
  SOP versions do neither, and the stored content type is whatever the browser declared
  (`console.py:3614`), replayed to members later. Files are served as attachments, which limits
  the damage, but this is the one upload path in the product without a guard.
- **F3 — Archiving is terminal.** Nothing clears `archived_at`, and the listing hides archived
  rows, so an SOP archived by mistake cannot be brought back from the console.
- **F4 — Acknowledgements are invisible.** Members cannot see how many colleagues have read a
  procedure; leaders cannot see who has not. The data is there.
- **F5 — A new version silently resets everyone.** Uploading v4 correctly makes every v3
  acknowledgement stale, and nobody is told: no notice on the page, no email.
- **F6 — Smaller ones:** categories cannot be reordered in the UI; an older version cannot be made
  current again; a version cannot be deleted or renamed; there is no "mark reviewed" (only a
  forward-looking due date, with no record of who reviewed what); the console page is gated by
  `console_access`, not by `sop_library`.

---

## 3. The question that decides the size of this build

The mockup's procedure page can only exist if a procedure **is** text the product holds. Today it
is a file. There are three honest ways forward.

**A. Author procedures in the console.** Steps, intro and callout become fields; the file becomes
an optional attachment. The portal then matches the mockup exactly. The cost is migration: Utah
Life's library is about 84 documents, and somebody has to move them.

**B. Keep documents, read them in place.** Serve PDFs inline instead of as downloads and embed the
current version in a reader page, with the mockup's frame around it (meta strip, sidebar, the
acknowledgement row). Cheap, no migration, and the middle of the page is a PDF rather than the
mockup's typography. Steps, *On This Page* and the callout cannot exist.

**C. Both, per procedure (recommended).** An SOP can have an authored body *or* an attached
document *or* both. Authored ones draw exactly as the mockup does; document-only ones draw the same
page with the document embedded (B) instead of steps. The library screen is identical either way,
because the card only needs title, department, version, blurb, owner and date. Utah Life can author
the procedures that matter most and leave the rest as documents, and convert over time.

With C there is an optional accelerator: **draft the steps from the document.** The workspace
already has an assistant with a server-side Anthropic key; a "Draft from this document" button
could turn an uploaded procedure into a first set of steps for the owner to correct. That is one
phase on its own (§8, Phase 6) and easy to cut.

---

## 4. Data model (proposal, migration `0077_sop_library`)

Additive, in the shape 0075 and 0076 used.

`intranet_sop` gains:

| column | why |
|---|---|
| `summary` Text | the card's one-line blurb |
| `body` JSON | the authored procedure: `{intro, steps: [{title, text}], callout: {label, text}}`, validated by a service the way the Win the Day playbook is |
| `published_body` JSON | what members see, so an edit is staged until Publish (F1) |
| `applies_to` Text | the meta strip's *Applies to*, free text ("Listing Agents") |
| `tool_ids` JSON | Launchpad tiles for *Tools you'll need*, filtered by each viewer's role |
| `last_reviewed_on` Date | what *Last updated* should mean, set by a **Mark reviewed** action |
| `suggestions_open` SmallInt | denormalised count for the console list (optional) |

New `intranet_sop_suggestion`: `sop_id`, `member_id`, `text`, `status` (New / Read / Done),
`created_at`, `resolved_at`, `resolution_note` — the *Suggest a Change* queue, modelled on
`intranet_content_gap`.

Nothing is removed. Versions, acknowledgements and categories stay exactly as they are; an
authored body versions with the SOP's `version_label` (a body change is a new version, with no
file, which is why `intranet_sop_version`'s file columns must become nullable — the one destructive
edge of this migration and the reason it needs its own review).

---

## 5. API

**Console** — `PATCH /sops/{id}` accepts the new fields; `PUT /sops/{id}/body` validates and saves
the authored body as a draft; `POST /sops/{id}/review` records a review; `GET /sops/{id}` returns
the real acknowledgement count and the roster of who has and has not; `GET/PATCH
/sops/{id}/suggestions` works the queue; `POST /sops/{id}/versions` gains a size limit and byte
sniffing (F2); `POST /sops/{id}/restore` un-archives (F3).

**Portal** — `content.sops` gains `summary`, `department`, `updated_on`, `has_body`, `acknowledged`
counts and `owner` as a card (id, name, initials, title, photo, message link, reusing
`services/whos_who.card`); a new `GET /intranet/sops/{id}` returns one procedure with its body,
tools and acknowledgement counts; `POST /intranet/sops/{id}/suggest` files a suggestion;
the file route learns to serve PDFs inline for the reader.

---

## 6. Console

A rebuilt SOP page in the shape the Win the Day page took: the list, then per procedure — **About**
(title, department, owner, applies to, summary, state, review), **The procedure** (intro, steps
with add/remove/reorder, the callout), **Tools** (pick from the Launchpad), **The document**
(versions, upload, make current), **Who has read it** (the count, the roster, and who has not), and
**Suggestions**. Categories gain reordering.

---

## 7. Portal: how "exactly" is reached and checked

Same method as Who's Who: port each block from `template.html` into `ut-sop-*` classes, value for
value, every colour on a token; capture the built portal at 1440px with the same headless Chrome
used for the mockup (`cdp_shot.mjs`), and compare region by region against `sops.html` and
`sop.html`; check computed styles on one sample of each component; check 375px for sideways
scroll. No new tokens are needed — the SOP screens reuse `--hair`, `--line-done`, `--avatar`,
`--soft-line`, `--page`, `--primary`, `--rail-line` and `--sun-dim`, all of which exist.

Deliberate differences to expect: the department rail filters live rather than re-rendering a
static list; *Changed This Month* is derived from version dates and hidden when nothing changed
this month; a document-only procedure shows the embedded document where the steps would be; and
the acknowledgement note names the real count for the workspace.

---

## 8. Phases

| # | Phase | What it gets you | Rough size |
|---|---|---|---|
| 1 | **The faults** (F1–F6), no new features | Edits stop going live before Publish; uploads are checked; archive can be undone; counts appear in the console | Small |
| 2 | **Model + console authoring** (§4, §6 minus suggestions) | An SOP can hold a procedure, a summary, tools, applies-to; reviews recorded | Medium |
| 3 | **The portal library** (§1.1) | The mockup's library screen: rail, search, departments, changed-this-month, cards | Medium |
| 4 | **The portal procedure** (§1.2) | The reader: steps, callout, on-this-page, tools, owner card, acknowledgement with counts | Medium |
| 5 | **Suggest a Change** | The member button, the console queue, an email to the owner | Small |
| 6 | **Draft from a document** (optional) | The assistant turns an uploaded SOP into draft steps for the owner to fix | Small–medium |
| 7 | **Utah Life, live** | Connor's: departments, the procedures worth authoring, the rest as documents | Connor |

Phases 1 and 2 are worth doing even if the portal work waits: they fix a correctness problem and
make the data real.

---

## 9. Decisions for Connor

| # | Decision | What I would choose |
|---|---|---|
| D1 | **Authored procedures, documents, or both?** (§3) | **Both (C).** The library looks right from day one, and nobody has to retype 84 documents to get there. |
| D2 | **Should an edit to a live SOP wait for Publish?** (F1) | **Yes.** A procedure being rewritten should not be in force. Authored bodies get a published copy; the title and category follow the same rule. |
| D3 | **Who sees acknowledgement numbers?** | Every member sees the count ("64 of 86"); only the console sees **who** has not read it. |
| D4 | **Does *Applies to* restrict who can see the SOP?** | No — a label. A procedure nobody can find is worse than one that says it is not for you. Worth knowing: courses, pages, launchpad tiles and calendar categories all already restrict by role through a join table, so if you want SOPs to work that way instead, the pattern is there and it is a small change. |
| D5 | **Where does *Suggest a Change* go?** | A queue in the console under the SOP, plus an email to the owner (Resend is live). Not a public comment thread. |
| D6 | **What is *Last updated*?** | The date the current version or the body last changed — not any row edit, which is what `updated_at` is today. |
| D7 | **Do new versions notify the team?** | Yes, for procedures marked required: an email when a new version lands, because the acknowledgement silently resets. |
| D8 | **Tools you'll need** | Pick from the Launchpad tiles, so the links stay correct and role restrictions are respected. Free text as a fallback. |
| D9 | **Does the portal reader replace the download?** | No. The reader is the page; the document stays downloadable from it. |
| D10 | **Assistant** | Once bodies exist, the assistant should read them (today it is told the file's contents are not available). Same permission gate. |

---

## 10. Not built, designed for

- Required reading with a due date per role, and a leader's view of who is behind.
- Approval before a version goes live (a second pair of eyes).
- Full-text search inside uploaded documents.
- Related procedures, and "used by" links from training lessons.
- A public change log per procedure.

---

## 11. Only Connor can do

- Decide D1–D10, or say "your call" as before.
- Say where Utah Life's 84 procedures live now and which ones matter most, so Phase 7 is a list
  rather than a guess.
- Departments: the mockup's five (Listings, Lead Gen, Transactions, Marketing, Client Care) are a
  sample — confirm the real set.

---

## 12. Build record

Nothing built yet. This document is the audit Connor asked for on 2026-09-20.
