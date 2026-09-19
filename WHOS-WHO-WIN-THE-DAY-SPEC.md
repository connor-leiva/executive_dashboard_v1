# Who's Who and Win the Day: audit, spec and build guide

Written 2026-09-18 from Connor's mockup (`Utah Life Intranet.html`), a read of the code, and the
production database (read-only: counts, role names and list names only, no member data).

> **The ask:** wire up Who's Who and Win the Day correctly, usable for every workspace and every
> agent. Who's Who is configured in the workspace console. Both pages look **exactly** like the
> mockup. If getting there takes a broader audit, do it and write down the phases to follow.

This document is the plan and, as phases ship, the build record (§12).

**Where the mockup lives.** `frontend/brand-src/mockups/utah-life-intranet/` is local only:
`frontend/brand-src/` is gitignored, and the mockup carries real staff names, photos and a phone
number. The folder holds:

- the original bundle;
- `template.html`, the page decoded from the bundle; every inline style this build ports comes
  from it;
- 1440px captures of every screen, and the script that makes them.

Its README gives the line ranges and the capture steps. Connor's copy lives in a Temp folder that
Windows may clear at any time; this copy won't be.

---

## 1. What the mockup specifies

### 1.1 Who's Who (`screens/dir.png`, template lines 1598–1663)

1. **Header.**
   - Eyebrow *Team*.
   - Title *Who's Who*, 40px.
   - Intro: "Eighty-six agents, one team. Start with the people whose whole job is helping you
     close more." The number is the team's size, spelled out.
2. **Featured leader.** A dark band in ink, 20px radius, with two columns.
   - Left:
     - eyebrow *Team Leader*;
     - name, 42px;
     - a subtitle, *Associate Broker · Utah Life Real Estate Group*;
     - an italic Playfair quote, 23px;
     - a **View Profile** button;
     - three stats under a rule: *612, units year to date*; *$268M, volume*; *86, agents on the
       team*.
   - Right: the photo at full height (at least 460px), `cover`. It uses `mix-blend-mode:
     multiply` on the canvas colour, so a white or light studio background melts into the page.
     Spring's photo is an opaque 1466×1114 PNG shot on a light backdrop.
3. **Leadership.**
   - A section heading with a rule.
   - Dark cards, 18px radius. Each has:
     - a 268px photo that fades into the card (a mask from 68% to transparent);
     - eyebrow *Team Leader — SLC*;
     - name, 22px;
     - one line on what to bring them;
     - **View profile →**.
4. **Agents.**
   - A section heading with a rule and *Showing 9 of 86* on the right.
   - White cards, 14px radius. Each has:
     - a 44px initials circle;
     - the name;
     - *Buyer Agent · Salt Lake*;
     - a small uppercase tag on the right: *Bilingual*, *Luxury* and so on, or *You* on your own
       card.
   - Cards highlight on hover.

### 1.2 The profile page (`screens/leader.png`, lines 1665–1717)

- **Back link:** *← Team directory*.
- **Header:** one white card with an ink header:
  - a 104px round photo;
  - eyebrow, name (38px) and subtitle;
  - an outline **Message** button.
- **Left column:**
  - the italic quote;
  - the bio paragraphs;
  - **Bring Her**, a bulleted list.
- **Right column:**
  - **Reach Her**: Direct, Email and Office;
  - **She Owns**: SOPs and standing meetings, each a link.

### 1.3 Win the Day (`screens/wtd_*.png`, lines 1139–1429; data at 2188–2230 and 2440–2611)

**Shared header, on every tab:**

- eyebrow *Agent Playbook*;
- title **Win the Day.**, 44px;
- the lede;
- three meta chips: *Home Base*, *Daily Commitment*, *Who Runs It*;
- **Open Follow Up Boss →**;
- the ink *One Rule That Matters* card;
- six tabs.

**Today's Run**

- **Today's sheet.** The date, and either "3 blocks left" or "Today is won. Check the box."
  Three tallies, each with − and + buttons, against goals: *Dials / 25*, *Conversations Logged /
  8*, *Appointments Set*.
- **Five Blocks.** Each block is a toggle card showing:
  - its number, which becomes ✓ when done;
  - title and a minutes chip;
  - text;
  - the numbers of the lists it covers.
- **The three kinds of list:**
  - *Clear It* ■: done means empty.
  - *Top Down* ↓: done means the clock ran out.
  - *Scan* ◉: done means you looked.
- An amber *Trips People Up* callout.
- *Once a week*: *Meeting Day* and *Once A Week*.

**The 13 Lists**

- A title with the count, an intro, and a *Today* progress bar ("3 of 13 worked").
- Six groups of list cards. Each card has:
  - a checkbox and the list number;
  - the name ↗, which opens the Follow Up Boss smart list;
  - a cadence chip and a kind chip;
  - a minutes box, on Top Down lists only;
  - text and script chips ↗.
- Two *Trips People Up* items.
- An ink pond card with seven pond-list links.

**The Call**

- Five numbered steps.
- A *Compliance* callout.
- Seven habit cards.

**Scripts**

- A title, with **Open the Full Script Library ↗** beside it.
- Three groups, each with a label and a sub-line such as "Lists 1, 2, 3, 10, 13". Each group
  holds script cards: name ↗ and one line.
- An ink *If none of these fit* card.

**The Numbers**

- A scoreboard with Metric, Daily and Weekly columns. *Conversations Logged* is in the brand
  colour.
- A footnote.
- *The New Agent On-ramp*: three cards (*Week One*: 15 dials, 4 conversations; and so on).

**PLACE Tools**

- Four tool cards. Each has a block chip (*Block 1 · Power Up*), the name ↗, a tagline and text.

**What belongs to whom:** each agent's day holds which blocks are done, which lists are worked,
the minutes on each Top Down list, and the three tallies. Everything else is the team's.

---

## 2. Audit: what the portal does today

Found by reading the code, capturing the running portal, and reading the production database.

**Production today, utah-life:**

- **Roster:** 3 people, of whom 1 is Active.
- **Profiles:** no titles, bios, phones or photos.
- **Win the Day:** 0 call lists.
- **Roles:** Owner, Manager and Member.
- **Follow Up Boss:** connected to the **`liveutah1`** account, which is the same account every
  list link in the mockup points at.

### Who's Who

| # | Finding | Evidence | Effect |
|---|---|---|---|
| W1 | **The page is a flat grid of cards.** There is no featured leader, no leadership section, no agent grid and no profile page. | `IntranetApp.jsx` `Directory` | None of §1.1 or §1.2 exists. |
| W2 | **Photos can never display.** The page renders `<img src={fileUrl(photo_url)}>`. An `<img>` sends no `Authorization` header, and `/intranet/directory/{id}/photo` requires one. The request fails with 401, and `onError` hides the image. Lesson images already solve this by fetching with `getBlob` first. | `Directory`, `directory_photo` | Any uploaded photo would be invisible. |
| W3 | **Nothing can set a photo.** Only a test writes `photo_key`, and the console has no upload. | `grep photo_key` | Every face is initials. |
| W4 | **The profile fields that exist cannot be edited.** `title`, `bio`, `phone` and `owns` are accepted by `PATCH /console/members/{id}`, but no console screen sends them. | `Roster.jsx`, `patch_member` | 0 of 3 production members have any profile fields. |
| W5 | **The mockup needs fields that don't exist.** No subtitle line, specialty tag, "what to bring me" line, quote, bring list, office, pronoun (*Reach **Her***), message link, list of owned items with links, photo focus point, or placement (featured / leadership / agents / hidden). Nothing page-level either: no featured person, intro or stats. | `models.IntranetMember` | The mockup cannot be filled in. |
| W6 | **Leadership comes from the role alone.** Utah Life's roles make Owner and Manager leadership. Its active Owner is Connor, the platform operator, so he would head their Leadership section, and nobody can be hidden. | `directory_rows`, `is_leadership` | The wrong person leads the page. |
| W7 | **Only Active members are listed.** With the new held invites, a team added quietly shows almost nobody: production utah-life has 1 Active of 3. | `status == "Active"` in the payload and in the photo route | The directory is empty exactly when Connor is testing it. |
| W8 | **The quote face isn't loaded.** Quotes are Playfair Display *italic 400*. The portal loads Playfair only at 600 and 700, upright. | `intranet/index.html` font link | The browser fakes an italic, so the quotes can't match. |
| W9 | **Search and the assistant only know a name.** Both send people to `/directory`, not to a profile, and they don't know tags, subtitles or what anyone owns. | `search.js`, `intranet_assistant.corpus` | "Who handles contracts" can't find the person. |

### Win the Day

| # | Finding | Evidence | Effect |
|---|---|---|---|
| T1 | **The page is a hardcoded checklist.** It renders `WTD_BLOCKS`: five blocks of three generic items such as "Review priorities" and "Check calendar blocks". The build order flagged this constant: *"no backing model: model, keep, or cut?"*. This ask answers it: give it a model. | `constants.js` `WTD_BLOCKS` | Every workspace sees the same fifteen checkboxes. |
| T2 | **The only authored content is the call list:** name, list id, script name and daily target. | `IntranetWtdList`, console `WinTheDay.jsx` | Production utah-life has 0 lists. |
| T3 | **Most of the playbook has no model.** Header, meta chips, CTA, the one rule, blocks (minutes, lists covered), list groups, cadence, kind, descriptions, a script library with links and groups, trips, the weekly callout, ponds, call steps, compliance, habits, the fallback card, a scoreboard, the on-ramp and tools. | §1.3 | The mockup cannot be authored. |
| T4 | **The daily state has the wrong shape.** It records `checked["block:item"]` and four free-typed tallies (calls, conversations, appointments, notes). The mockup tracks blocks done, lists worked, minutes per Top Down list, and three ± tallies against goals. | `DEFAULT_WTD` | Today's sheet can't be drawn. |
| T5 | **No targets per agent.** There's no start date for the on-ramp and no personal targets. The mockup says "your accountability partner confirms or personalises your numbers". | — | A new agent sees the same /25 as a veteran. |
| T6 | **Home counts the hardcoded fifteen.** "0/15 Win the Day" appears in the first-30-days panel. | `Home` | It keeps the constant alive after the page moves on. |
| T7 | **Search and the assistant see list names only.** The mockup's assistant cites *"Win the Day: lists 01 and 02"*. | `search.js`, `corpus` | The playbook can't be asked about. |
| T8 | **The Follow Up Boss account is known and unused for the button.** List links already resolve from the account's subdomain. *Open Follow Up Boss* (`…/2/people`) and the pond links can resolve the same way, with nothing typed. | `_list_url` | One less thing to configure. |

### Both pages

| # | Finding | Effect |
|---|---|---|
| X1 | Both use the generic `Page` header (date eyebrow, small title). The mockup gives each page its own eyebrow and a 40px or 44px title. | Needs page-specific headers. |
| X2 | Eleven colours in these two sections aren't portal tokens yet (§7.2). | Must be added the same way the others were: measured from the mockup, then following a workspace's palette. |
| X3 | *For the build, not a product bug:* `npm run build` has no `VITE_API_BASE`, so a local preview runs the portal's offline sample mode ("Preview User"). Every click-through of this build uses `VITE_API_BASE=http://localhost:8000/api/v1`. | Otherwise a verification can pass against sample data. |

---

## 3. Per workspace and per agent

Everything that is the team's is configured in the workspace's own console. Nothing is compiled
in, and nothing about Utah Life becomes a default for another team.

| What | Configured in | Per agent |
|---|---|---|
| Who's Who intro, featured person, 3 stats, preview count | Console → **Who's Who** | Your own card says *You*. |
| Who's Who profile (photo, subtitle, tag, quote, bio, bring list, reach details, owns, pronoun, message link, placement) | Console → Who's Who → a person, or People & Roster → **Edit profile** | Each person's own. Profiles are immediate, like the roster (D2). |
| *She Owns*, the SOP part | Nothing to type: the SOPs whose owner is this person | — |
| Win the Day playbook (header, rule, tab labels, blocks, groups, lists, scripts, call, numbers, on-ramp, tools, ponds, callouts) | Console → **Win the Day**, then Publish, like all content | — |
| List and pond links, *Open Follow Up Boss* | Resolved from the connected Follow Up Boss account | FUB filters a shared smart list to whoever opens it. |
| Daily goals (the /25) | Scoreboard rows in the playbook | Personal targets, then the on-ramp by working day since the agent's start date, then the team's (D5). |
| The day: blocks, lists worked, minutes, tallies | — | The agent's own. The existing `wtd` state, one per local date. |
| Who sees Win the Day | The existing `wtd` permission | — |

---

## 4. Data model

### Migration `0076_whos_who`

**`intranet_member` gains:**

| Column | Type | What it is |
|---|---|---|
| `headline` | text, ≤120 | Subtitle line: *Associate Broker · Utah Life Real Estate Group* |
| `tag` | text, ≤24 | The agent card's tag: *Bilingual* |
| `help_line` | text, ≤140 | Leadership card line: *Pipeline, conversion and the deal you think is dead.* |
| `quote` | text, ≤240 | The italic quote |
| `bring` | JSON list[str], ≤6 × 140 | *Bring her* |
| `office` | text, ≤200 | *Reach her → Office* |
| `pronoun` | `'she'`\|`'he'`\|`'they'`, default `'they'` | Picks *Bring Her / Him / Them*, *She Owns / He Owns / They Own* |
| `message_url` | text, ≤500 | Where **Message** goes. `https:`, `mailto:`, `sms:` or `slack:` only. Blank means `mailto:` the roster email. |
| `owns_items` | JSON list[{label ≤120, url? ≤500}], ≤8 | The typed part of *She Owns*. A data step copies the old `owns` text in as one item. |
| `photo_focus` | text `"50% 12%"` | Where every crop centres |
| `directory_placement` | `'auto'`\|`'leadership'`\|`'agents'`\|`'hidden'`, default `'auto'` | `auto`: a leadership role goes to Leadership, anyone else to Agents |
| `directory_order` | smallint | Order within Leadership |

**New table `intranet_directory_setting`** (one row per workspace):

- `featured_member_id` (SET NULL on delete);
- `featured_label`, the eyebrow; blank means the person's title;
- `intro`, supporting `{N}` for the team size in words and `{n}` in digits;
- `stats`, a JSON list of up to 3 `{label, source, value}`. `source` is `manual`, `team_size`,
  `sisu_units_ytd` or `sisu_volume_ytd`;
- `preview_count`, default 9, where 0 shows everyone.

This table is not publishable (D2).

### Migration `0075_wtd_playbook`

**New table `intranet_wtd_playbook`** (one row per workspace):

- `content`: a JSON document validated server-side section by section (below);
- `published_at` and `draft_dirty`. Having those two columns is what puts a table in the publish
  cycle (`_publishable_models`), so it joins Publish with no further work.

**New table `intranet_wtd_script`:**

- `name` (≤120) and `chip` (≤40; blank means the name);
- `url` and `description` (≤400);
- `group_key` (blank means it's shown only as a chip, like *Sphere Model* and *Lead Ponds* in the
  mockup);
- `position` and `active`;
- `published_at` and `draft_dirty`.

Scripts are rows rather than JSON because lists refer to them, and search and the assistant
index them.

**`intranet_wtd_list` gains:**

- `group_key` and `block_key`;
- `cadence` (≤32);
- `kind`: `clear`, `top_down` or `scan`, default `clear`;
- `description` (≤600);
- `script_ids`: a JSON list, at most 6.

`script_name` and `daily_target` stay for existing rows. The portal falls back to `script_name` as
a plain chip when a list has no `script_ids`.

**`intranet_member` gains:**

- `started_on` (date): the on-ramp's day 1. Blank means the person is not on the on-ramp, and
  the team's targets apply. It does not fall back to the date they accepted the portal invite:
  that would put every existing agent on *Week One* targets the week the portal rolls out.
- `wtd_goals` (JSON `{tally_key: int}`): personal targets.

**The playbook document.** Each section is validated by its own Pydantic model, so a bad section
is refused with the field named.

```jsonc
{
  "version": 1,
  "header":  { "eyebrow": "Agent Playbook", "title": "Win the Day.", "lede": "…",
               "meta": [{ "k": "Home Base", "v": "Follow Up Boss" }],          // ≤ 3
               "cta":  { "label": "Open Follow Up Boss →", "url": null } },    // null → the FUB account
  "rule":    { "eyebrow": "The One Rule That Matters", "text": "…", "sub": "…" },
  "tabs":    { "run": "Today’s Run", "lists": "The {n} Lists", "call": "The Call",
               "scripts": "Scripts", "numbers": "The Numbers", "tools": "PLACE Tools" },
  "run":     { "title": "{N} Blocks. Same Shape Every Day.", "intro": "…",
               "blocks": [{ "key": "b1", "title": "Power Up", "minutes": 15, "text": "…" }],   // ≤ 8
               "trips":  [{ "title": "…", "text": "…" }],
               "weekly": { "eyebrow": "…", "items": [{ "title": "Meeting Day", "text": "…" }] } },
  "lists":   { "title": "Lists 1 Through {n}, in the Order You Run Them.", "intro": "…",
               "groups": [{ "key": "g1", "label": "Time-sensitive — contact today, not tomorrow" }],
               "trips":  [ … ],
               "ponds":  { "eyebrow": "…", "text": "…", "links": [{ "label": "New Leads", "list_id": "76" }] } },
  "call":    { "title": "How to Work a List.", "intro": "…", "steps": [{ "title": "…", "text": "…" }],
               "compliance": { "eyebrow": "…", "text": "…" },
               "habits_title": "Keep These Habits Specifically", "habits": [{ "title": "…", "text": "…" }] },
  "scripts": { "title": "…", "intro": "…", "library": { "label": "Open the Full Script Library", "url": "…" },
               "groups": [{ "key": "s1", "label": "Leads and web enquiries", "sub": "Lists 1, 2, 3, 10, 13" }],
               "fallback": { "eyebrow": "If none of these fit the call", "text": "…" } },
  "numbers": { "title": "…", "intro": "…",
               "rows": [{ "label": "Dials", "daily": "25 / day", "weekly": "125 / week", "highlight": false,
                          "tally": { "key": "dials", "short": "dials", "goal": 25 } }],   // rows with a tally → Today's sheet
               "footnote": "…",
               "onramp": { "title": "The New Agent On-ramp", "intro": "…",
                           "phases": [{ "label": "Week One", "through_day": 5,
                                        "goals": { "dials": 15, "convos": 4 }, "focus": "…" }] } },
  "tools":   { "title": "…", "intro": "…",
               "items": [{ "block": "Block 1 · Power Up", "name": "AI Call Coach", "url": "…",
                           "tagline": "…", "text": "…" }] }
}
```

**Two rules about the document:**

- A tab whose section is empty is not shown.
- `{n}` is a count in digits and `{N}` is the same count spelled out with a capital. That's how
  *"Five Blocks"* and *"The 13 Lists"* stay true when a block or a list is added.

**The kinds are the product's, not the workspace's.** *Clear It*, *Top Down* and *Scan* change
behaviour: only Top Down gets a minutes box. Their glyphs and definitions are fixed, like
`FOLLOW_UP_KINDS` (D4).

**The day, version 2.** Same endpoint, same key (the local date). Anything from version 1 is simply
ignored.

```json
{ "v": 2, "blocks": { "b1": true }, "lists": { "<list id>": true },
  "minutes": { "<list id>": 20 }, "tally": { "dials": 14, "convos": 5 } }
```

---

## 5. API

### Console (`/api/console`, console access, every change audited)

**Profiles:**

- `PATCH /members/{id}` also accepts every §4 member column, each with its own limits and URL-scheme
  checks. These changes are immediate, as the roster already is.
- `POST /members/{id}/photo` takes a multipart upload:
  - the file type is checked from its bytes, and only JPEG, PNG or WebP are accepted, up to
    15 MB;
  - it is re-encoded server-side with Pillow to at most 2000px on the long edge;
  - EXIF is stripped, so a phone photo's GPS never reaches the portal;
  - transparency is flattened onto white. A transparent cut-out then blends into the featured
    band the way the mockup's light-backdrop photo does, instead of turning black.
- `DELETE /members/{id}/photo` removes it. `GET /members/{id}/photo` gives the console its
  preview.

**The Who's Who page:**

- `GET /directory` returns the settings and every roster person's placement, order, photo and tag.
- `PATCH /directory` saves the settings.
- `POST /directory/leadership/order` saves the leadership order.

**The playbook:**

- `GET /wtd/playbook` returns the content, the publish state, the lists and the scripts.
- `PATCH /wtd/playbook` saves one section per request as a pending change.

**Scripts:**

- `GET /wtd/scripts` and `POST /wtd/scripts` list and create;
- `PATCH /wtd/scripts/{id}` and `DELETE /wtd/scripts/{id}` edit and remove; removing a script
  also takes it off every list;
- `POST /wtd/scripts/order` saves the order.

**Lists:**

- The existing list routes take the new columns.
- `GET /wtd/lists/check` asks Follow Up Boss for the account's smart lists and names every list or
  pond id that doesn't exist. It uses the stored key on the server and returns ids and names only.
  This is how the mockup's ids get verified: never by reading the key anywhere else.

**Moving a playbook:**

- `GET /wtd/export` gives the playbook, its lists and its scripts as one JSON file, with ids made
  portable.
- `POST /wtd/import` validates a file and writes it as a draft. If a playbook already exists, it
  refuses unless the request says `replace`.
- This is how Utah Life's playbook arrives (D6), and how a second PLACE team starts from it.

### Portal

**`config.content.directory`, version 2**, card fields only, so bios never ride in the bootstrap:

- `intro`, with the count already filled in;
- `team_size` and `stats`, with values resolved;
- `featured`, `leadership[]` and `agents[]`;
- `preview_count`.

Each person carries `{id, name, title, market, headline, tag, help_line, photo_url, photo_focus,
is_you}`.

**`GET /intranet/directory/{id}`** returns one profile: the card fields plus:

- `quote`, `bio`, `bring` and `pronoun`;
- `phone`, `email` and `office`;
- `message_url`;
- `owns`: the SOPs the person owns (title · version, linking to the SOP) followed by the typed
  items.

**`GET /intranet/directory/{id}/photo`** uses the same rule as the directory (listed and not
hidden) rather than `Active` only.

**`config.content.wtd`** is the published playbook, resolved:

- tab labels with their counts filled in;
- blocks with the numbers of the lists they cover;
- lists numbered, grouped and linked, with their scripts;
- pond and CTA links from the Follow Up Boss account;
- `my_goals` for this person, resolved per D5.

`config.content.wtd_lists` goes away; search and the assistant move to `content.wtd`.

---

## 6. Console

**Who's Who** is a new page in the console's Content group, beside Win the Day. It has four
panels:

1. **Page:**
   - the intro, with `{N}` explained;
   - how many agents to show before *Show all*.
2. **Featured:**
   - pick the person;
   - optionally override the eyebrow;
   - edit the three stats. Each stat is *Typed* or *Team size*. The two Sisu totals are offered
     only when Sisu is connected, and choosing one is opting in (D3).
3. **Leadership:** the people shown there, in order, with up and down arrows. People can be added
   or removed.
4. **Everyone:** the roster, showing placement (Auto / Leadership / Agents / Hidden), tag and
   whether a photo is set. Each row has **Edit profile**.

**Edit profile** is the same drawer whether it's opened from here or from People & Roster:

- the photo, with a focus point you click on the image. Previews show the leadership card
  (268px), the round avatar (104px) and the featured band.
- title and market (already on the roster);
- subtitle, tag, pronoun and the "bring me" line;
- quote, bio and the bring list;
- phone, office and the message link;
- **Owns.** SOPs owned are listed read-only, since they come from the SOP library. Typed items
  can be added with a link.
- **Win the Day:** the start date and personal targets, one per tally.

**Win the Day** is rebuilt into sections that mirror the portal's tabs. *Needs You Today*
settings stay where they are.

- **Page:**
  - header, meta chips, CTA and the one rule;
  - tab labels;
  - **Import** and **Export**.
- **Today's Run:**
  - blocks (title, minutes, text; reorder);
  - trips and the weekly callout.
- **Lists:**
  - groups;
  - each list, with its fields:
    - the smart-list picker (it already exists);
    - group and block;
    - cadence and kind;
    - text;
    - scripts, picked from the library.
  - ponds, and the account check from `/wtd/lists/check` with any missing ids flagged beside the
    list;
  - trips.
- **The Call:** steps, compliance and habits.
- **Scripts:**
  - the library (name, chip label, link, one line, group; reorder);
  - groups;
  - the library link and the fallback card.
- **Numbers:**
  - scoreboard rows. A row can drive a tally on Today's sheet, with its key, short label and
    goal.
  - the footnote;
  - the on-ramp phases.
- **Tools:** the tool cards.

Every playbook edit is a pending change, published with the console's existing publish bar.

---

## 7. Portal: how "exactly" is reached and checked

### 7.1 Method

1. **Port, don't redraw.** Each component is written from its block in `template.html`:
   - every inline declaration becomes a class in `ui.css`, under a `ut-wtd-` or `ut-who-` prefix;
   - pixel values are copied as they are;
   - every hex becomes a token;
   - every `style-hover` becomes a `:hover` rule.
2. **Page-specific headers.** Win the Day and Who's Who each draw their own header block (X1)
   instead of the generic `Page` header.
3. **Fonts.** Add Playfair Display `ital,wght@0,400;0,500;1,400` to the portal's font link (W8).
   Keep the 600 and 700 it already loads.
4. **Photos load with the session** (W2). A small `useAuthedImage(url)` fetches through `getBlob`
   and hands back an object URL, revoked on unmount. The console and the portal share it.
5. **Tabs live in the URL:** `/wtd`, `/wtd/lists`, `/wtd/call` and so on, with anchors
   `#list-01`. An answer from the assistant can then open the list it cites. The profile page is
   `/directory/:id`.
6. **Below 760px**, follow the portal's existing mobile rules (see the Mobile memory note):
   - one column and 16px side margins;
   - the tab bar scrolls sideways;
   - no page-level horizontal scroll;
   - checked at 375px.

### 7.2 New tokens

Each token is measured from the mockup. For Utah Life's palette each must reproduce the mockup's
hex exactly. For another workspace each is derived from its palette, the way `RAIL_SHADES`
already is.

| Token | Mockup | Where |
|---|---|---|
| `--hair` | `#F4F1F0` | Row rules inside cards (scoreboard, bring list, sheet dividers) |
| `--line-done` | `#E4DFDD` | Border of a done block or list |
| `--steel` | `#5E7784` | The *Top Down* kind |
| `--accent-line` | `#C6D8E0` | The *Clear It* chip border |
| `--avatar` | `#DDE6EA` | The initials circle |
| `--input-line` | `#C9C3C0` | Underline of the minutes box |
| `--photo` | `#0F1417` | Ground behind leadership photos |
| `--dark-edge` | `#3A454B` | Outline button on ink (**Message**) |
| `--warm`, `--warm-edge`, `--warm-ink` | `#F7EFE3`, `#C08A3E`, `#9A6A22` | *Trips People Up*. A fixed semantic amber, like `--warn`, not taken from the palette. |

### 7.3 Deliberate differences from the mockup

Each of these is where the mockup is a picture and the product has to work.

1. **Agents** says *Showing 9 of 82 · Show all*, and *Show all* expands the grid. The mockup has
   no way to reach the other 77. The count is the Agents section's own; *86* in the intro and
   stats is the whole team.
2. **Agent cards open that person's profile.** They already have a hover state in the mockup, just
   no destination.
3. **Pronouns come from the profile.** The mockup always says *Her*. The default is *Them*.
4. **An empty *Scripts* label is hidden.** List 12 shows it bare in the mockup.
5. **Empty states** for no playbook, no leadership and no featured person, drawn in the same
   visual language. The mockup has none.
6. The mockup's demo role switcher (*Viewing As*) is outside this build.

### 7.4 How it's checked

- **Capture the same content.** A local workspace is loaded with the mockup's content: the Utah
  Life playbook import file, plus the four leader profiles typed in, with their photos taken from
  the bundle, **locally only**. The portal is then captured at 1440px with the same headless-Chrome
  command as the mockup (README), one image per tab and per screen.
- **Review side by side.** Each pair is read at full size. Anything that differs is either fixed
  or added to §7.3.
- **Check computed styles.** `getComputedStyle` is compared against the template's declarations
  on one sample of every component: font family, size and weight; colours; radius; padding.
- **Check mobile.** The portal at 375px, with no sideways scroll.
- **Build for production** with `VITE_API_BASE` set (X3), in a fresh session, per the
  *verify on the prod build* rule.

---

## 8. Phases

Win the Day comes first. Agents run it every working day, and Utah Life's content already exists
in the mockup, so it's useful the day it ships. Who's Who is only as good as the profiles and
photos entered into it. If Connor wants Who's Who first, the phases swap cleanly; nothing in one
depends on the other beyond Phase 1.

### Phase 1: groundwork shared by both pages

- The §7.2 tokens and their palette derivation.
- The Playfair italic.
- `useAuthedImage`, used by the existing directory straight away.
- The mockup reference folder (done while writing this spec).
- **Done when:** photos load in the current directory, and the default palette reproduces all
  eleven hexes.

### Phase 2: Win the Day, the model and the console

- Migration `0075`.
- The playbook, script and list API, including the check, import and export routes.
- The rebuilt console page.
- The per-member start date and targets.
- The Utah Life import file, transcribed from the mockup's data and kept beside the mockup (not in
  the repo).
- Tests:
  - validation of every section;
  - the publish cycle picking up the new tables;
  - import refusing to overwrite;
  - removing a script taking it off every list;
  - the account check never returning the key;
  - goal resolution: personal beats the on-ramp, which beats the team default, counted in working
    days.
- **Done when:** importing the file into a local workspace and publishing it gives the content of
  every mockup tab, and the check flags a made-up list id.

### Phase 3: Win the Day, the portal

- The page, all six tabs, ported per §7.
- The version 2 day state.
- Home's panel counting blocks (T6).
- Search and the assistant indexing the playbook (T7).
- `WTD_BLOCKS` and `DEFAULT_WTD` deleted.
- **Done when:** side-by-side captures of all six tabs match per §7.4 at 1440px; the page works at
  375px; a view-as session shows an agent's own day, read-only; and the full backend suite passes.
- **Ship:**
  - run `alembic heads` first: it must show one head;
  - commit to main and push;
  - watch the deploy;
  - check production read-only.

### Phase 4: Who's Who, the model and the console

- Migration `0076`.
- Profile fields and photo upload/removal.
- Directory settings and leadership order.
- The Who's Who console page and the **Edit profile** drawer, linked from People & Roster.
- Tests:
  - file-type check on upload;
  - EXIF stripped;
  - transparency flattened;
  - URL schemes refused;
  - the old `owns` data carried across;
  - placement rules;
  - a photo route that follows the listing rule;
  - a hidden person absent everywhere, including search, the assistant and the photo route.

### Phase 5: Who's Who, the portal

- The page and the profile route, ported per §7.
- Pronoun-aware headings.
- *Show all*.
- *She Owns* from SOPs.
- Search and the assistant sending people to profiles (W9).
- **Done when:** side-by-side captures of both screens match per §7.4, and the full suite passes.
  Then **ship**, with the same checks as Phase 3.

### Phase 6: Utah Life, live

With Connor:

- he imports the playbook file in the console, reads it, and presses Publish;
- he fills in the leader profiles and photos, and picks the featured person and the stats;
- then a view-as session as an agent, in production, to see both pages the way an agent does.

### Phase 7 (next, not in this build): designed for, needs a decision

See §10.

---

## 9. Decisions made for Connor (change any of them)

| # | Decision | Why |
|---|---|---|
| D1 | **The directory lists everyone on the roster who isn't Removed or Hidden, whether or not they've signed in.** This reverses the old "Active only" rule. | A colleague is a colleague before they accept a portal invite. With held invites, "Active only" empties the page exactly while you're testing it. *Hidden* covers anyone who shouldn't appear, including you on Utah Life. |
| D2 | **Who's Who changes are immediate. The Win the Day playbook is published.** | Profiles are roster facts, which the roster already treats as immediate on purpose ("a new hire's phone number should not wait for somebody to press Publish"). The playbook is content, like training. |
| D3 | **Stats are typed, or the team size, by default.** Sisu's team totals appear only when Sisu is connected, and choosing one is an explicit opt-in. | Team production is behind `team_production`. An aggregate shown to every agent should be the admin's choice, not a side effect. |
| D4 | **The three list kinds are fixed product vocabulary.** | They change behaviour (the minutes box). A workspace renaming them would break the "done means…" contract. |
| D5 | **Goals: personal target, then the on-ramp phase, then the team default.** The on-ramp counts **working days** (Monday to Friday) from the start date, in the workspace's timezone. | The mockup's phases are *Week One*, *Week Two* and *Day 11 Onward*: ten working days. |
| D6 | **Utah Life's playbook arrives as an import file, not a seed script.** It's transcribed from the mockup, kept beside it (not in the repo), and imported in the console as a draft for you to read and publish. | Nothing about Utah Life becomes code. You configure it in the UI, as you asked for integrations. Import/export also lets the next PLACE team start from this one. |
| D7 | **Photos are re-encoded on upload** (EXIF stripped, at most 2000px, transparency flattened onto white). | Privacy (a phone photo's GPS), size (the mockup's hero alone is about 1.7 MB), and the multiply blend. |
| D8 | **Show 9 agents before *Show all*, as the mockup does.** Configurable per workspace. | It's what's drawn. 12 fills rows more evenly if you prefer it. |
| D9 | **The tallies stay manual (− and +), as in the mockup.** | Automatic counts need Follow Up Boss calls synced, which isn't built (§10). |
| D10 | **The Scripts tab's "Lists 1, 2, 3, 10, 13" lines are typed, not derived.** | Derived from what the lists reference, the mockup's first group would read "1, 2, 3, 5, 6, 10, 13": deriving would change Utah Life's copy. |

---

## 10. Not built, designed for

- **Tallies from Follow Up Boss.** Dials would be outbound calls the agent logged today, and
  conversations would be calls over a chosen length or with a chosen outcome. This needs FUB
  calls synced (the *calls → conversations* phase of the FUB spec) and a decision on what counts
  as a conversation. Today's sheet would then fill itself and still allow a correction.
- **A leader's view of the team's day.** Who has won the day, and tallies by agent this week.
  Behind `team_production`. The daily state is already one row per person per date.
- **The weekly total.** *"Total the week on the scoreboard"* as a figure computed from the week's
  sheets on The Numbers tab.
- **Agents editing their own profile** (photo, phone, tag), as a portal form limited to those
  fields.
- **Deriving the Scripts tab's sub-lines** once the lists are the source of truth (D10).

---

## 11. Only Connor can do

- **Put the team on the roster.** People & Roster → *Add Person*. Nothing is sent until *Send
  invite*.
- **Profiles and photos** for the featured leader and the Leadership section, at least. Or say the
  word and the four leaders' profiles are typed in from the mockup, with their photos taken from
  the bundle: that's content about real people, so it's your call.
- **Import the playbook, read it, publish it** (Phase 6).
- **Mark yourself Hidden** on Utah Life's Who's Who (D1).
- **Answer any of D1–D10** you disagree with, before the phase that uses it.

---

## 12. Build record

Connor approved every decision (D1–D10) and asked for all phases, 2026-09-18.

### Phases 1–3: Win the Day

**Shipped as `9409ca4`, 2026-09-19, and checked in production:** alembic at `0075`, the two new
tables present and empty, the new list columns and the kind check in place, the new routes
refusing a request without a session, and the built portal carrying the new page, the `opsz` and
italic fonts, and none of the dashboard's type.

**Built:**

- **Phase 1, groundwork.**
  - The eleven tokens of §7.2. `intranet/palette.js` derives them from a workspace's own swatches,
    but only when a swatch differs from the default, so Utah Life gets the mockup's exact hexes.
  - Playfair Display upright 400/500 and italic 400.
  - `useAuthedImage`, used by the directory straight away (W2).
- **Phase 2, the model and the console.**
  - Migration `0075_wtd_playbook`.
  - `services/wtd_playbook.py`: one Pydantic model per section, `{n}`/`{N}`, working days,
    goals, `resolve`, and export/import.
  - The console routes: playbook, scripts, list fields, list delete, the account check,
    import/export and per-person targets.
  - The rebuilt console page, with eight sections.
  - The Utah Life import file: generated by `make_playbook.mjs`, which evaluates the mockup's own
    data literals, so nothing is retyped. It lives beside the mockup, not in the repo.
- **Phase 3, the portal.**
  - `intranet/WinTheDay.jsx`, ported block by block from the template.
  - `/wtd/:tab` routes and `#list-NN` anchors.
  - The version 2 day state.
  - Home counting blocks.
  - Search and the assistant reading the playbook.
  - `WTD_BLOCKS` and `DEFAULT_WTD` deleted.

**Found on the way, beyond this spec:**

- **The whole portal rendered in the dashboard's fonts.**
  - The cause: `intranet/palette.js` imported four colour helpers from the dashboard's
    `palette.js`, and that module paints the platform identity (Instrument Sans, Space Grotesk and
    its palette) onto the page root the moment it is imported. The portal's DM Sans and Playfair
    were declared in `ui.css` and overridden by an inline style nobody asked for.
  - The fix: the arithmetic moved to a side-effect-free `color.js`. The portal imports that; the
    dashboard's `palette.js` re-exports it unchanged. The portal bundle no longer contains the
    dashboard's type.
- **DM Sans needs its optical-size axis.**
  - The mockup's DM Sans is the variable font. At 44px it draws *Win the Day.* 218px wide; the
    portal's static request drew it 241px.
  - The font link now asks for `opsz`. Measured widths are identical to the mockup's at 15px,
    24px and 44px, to the sub-pixel.
- **An empty lists row still takes up space.** The mockup renders a block's lists row even when
  empty, and its 13px margin is part of every card's height. It is rendered the same way here.
- **The floating Ask button names the page**, as the mockup's does: *Ask about Win the Day*.
- **Corrected in §4:** a blank start date means not on the on-ramp. It does not fall back to the
  date someone was activated, which would put every existing agent on Week One targets.
- **For the next build:** `npm run build` does not build the console or the operator app. Use
  `npm run build:console` for those.

**Verified:**

- 25 new tests, the touched suites, and the full suite before shipping.
- **Side by side at 1440px against the mockup, all six tabs**, captured with the same headless
  Chrome as the mockup's, using Utah Life's playbook imported through the console route and
  published. The pages are identical apart from:
  - the mockup's demo state: its date, 14 dials, two blocks done;
  - the empty *Scripts* label on list 12 (§7.3.4);
  - *Your REMO* has no ↗ because the mockup's link is `#`. Add the real address in the console.
- **Clicked through in a browser:**
  - a block, a tally, a list and the minutes each persist across a reload;
  - `#list-03` lands on list 03;
  - at 375px no tab scrolls sideways.

### Phases 4–5: Who's Who

**Shipped as `2591e03`, 2026-09-19, and checked in production:** alembic at `0076`, the twelve
new member columns and both checks in place (existing members read *they* and *auto*),
`intranet_directory_setting` present and empty, the new portal and console routes refusing a
request without a session, and the built portal and console carrying the new pages, the 1152px
width and the drawer's styles.

**Built:**

- **Phase 4, the model and the console.**
  - Migration `0076_whos_who`: the profile columns on `intranet_member` (subtitle, tag, what to
    bring them, quote, bring list, office, pronoun, message link, owned items, photo focus,
    placement and Leadership order), with the old free-text `owns` carried into the first owned
    item; and `intranet_directory_setting` for the page itself.
  - `services/whos_who.py`: the field rules, placement, the stats (typed, the team's size, or a
    Sisu total only when Sisu is connected), the card and the profile, and photo processing.
  - Console routes: the page's settings, the Leadership order, and a photo's upload, removal and
    preview. The member update takes every profile field.
  - The console's Who's Who page (the page, Featured, Leadership in order, everyone's placement)
    and one profile drawer, opened from there and from People & Roster, with click-to-focus and
    previews of the three crops the portal draws.
- **Phase 5, the portal.**
  - `intranet/WhosWho.jsx`: the directory and the profile page, ported from the template (lines
    1598–1717) into `ut-who-*` classes, as Win the Day was.
  - `/directory/:id`. Agent cards, search results and the assistant open a person's profile. SOP
    rows carry `#sop-<id>` anchors, so an owned SOP opens on its row, marked.
  - The old flat directory and its styles are gone.

**Found on the way, beyond this spec:**

- **Every portal page was 88px too wide on a large monitor.** The mockup's `max-width: 1240px` is
  its main's *outer* width (border-box, 44px of padding each side), so its content stops at
  1152px. Measured in the mockup at 1920px. Home, the pages and Win the Day now stop at 1152;
  nothing changes below about 1530px.
- **Archivo has no arrow.** *View profile →* falls back for the arrow, and through the portal's
  UI font stack it fell back to Segoe UI, whose taller line made every leadership card a pixel
  taller than the mockup's. That one label uses the mockup's own stack.
- **Photos.**
  - A replaced photo kept its address, so the portal's five-minute cache kept showing the old
    one. The address now carries a version.
  - The console's preview is never cached, or *Replace photo* would preview the old photo.
  - An image over 50 megapixels is refused from its header, before it is decoded: 15 MB of PNG
    can decode into gigabytes. A JPEG decodes at a reduced scale on its way to 2000px.
- **The migration's data step** follows 0046's rule (`CAST(… AS jsonb)` on Postgres) and is
  compiled against the Postgres dialect in a test. Production had no free-text `owns` to carry.
- **Contact details stay on the card.** Phase 4 had moved email and phone to the profile route,
  and the assistant could no longer answer *"what's Justin's number"*. They are back on the card;
  the bio is the profile's alone.
- **Console wording.**
  - The *Automatic* option names where Automatic would put someone: *Automatic (Leadership)* for
    a manager moved into Agents. It named where they sat.
  - A refused field is named as the page labels it: *Owned item 2, link*, not
    `owns_items.1.url`.
  - A featured person who is later hidden stays in the select as *(hidden, so no band shows)*,
    and saving the eyebrow no longer re-sends them and gets refused.
- **Deliberate differences, beyond §7.3:**
  - the agents grid keeps the mockup's initials, because a photo there would be a full-size
    fetch per agent for a 44px circle;
  - a profile with nothing to read keeps its contact box in the left column, where the eye
    starts, instead of leaving that column empty;
  - the card's *what to bring them* line stands in for a bring list nobody has written, so a
    profile never says less than the card that led to it.

**Verified:**

- 21 new tests (18 for Who's Who, 3 for the migration). The full suite passed, 1,875 tests,
  before the last round of fixes, and every file those fixes touched passed again after them.
  One migration head. The migration ran up, down and up again on a copy of a real database,
  carrying an old `owns` over.
- **Side by side at 1440px against the mockup**, with the mockup's content seeded locally only:
  the four leaders with the bundle's photos, 82 agents, and Spring's SOP.
  - The header, the featured band, and the whole profile page are pixel-identical to the mockup.
    The only differing pixels on the profile are the local account's email address.
  - Leadership cards are identical apart from JPEG noise inside the photos.
  - The remaining differences are the workspace's own shell, the agents' order (by name), and
    *Showing 9 of 82 · Show all* (§7.3.1).
- **Clicked through in a browser:**
  - Show all and Show fewer; an agent card, a leadership card and the band each open the right
    profile; back to the directory; Message is the person's email;
  - an owned SOP opens the SOP library on its row, marked and in view;
  - searching a name opens that person's profile;
  - a hidden person's address says they are not on Who's Who;
  - the console page and the drawer from both screens: placement, order, featured, stats, every
    profile field, photo upload, focus and removal, and a refused field named.
- **At 375px**, neither page scrolls sideways.

### Phase 6: Utah Life, live

Connor approved entering the four leaders from the mockup, 2026-09-19. Done in his signed-in
console on `utah-life`, through the console's own forms, so every field was checked by the server
and the audit log records it under his account:

- **Justin Nelson, Lauren Griner and Jace Gillies added to the roster** as Managers, sign-in
  *Manual*, invites **held**: nobody was emailed. Their addresses came from the workspace's own
  synced Sisu and Follow Up Boss users, not typed from memory.
- **All four profiles and photos**, from the mockup: Spring's whole profile (subtitle, quote, bio,
  what to bring her, phone, office, what she owns, photo) and the other three's cards (title,
  market, what to bring them, photo). Spring's *She Owns* has no SOP line: Utah Life has no SOPs
  in the library yet.
- **The page:** Spring featured, with one number that counts itself (*Agents on the team*); the
  mockup's 612 units and $268M are sample figures. Leadership in the mockup's order. Connor's own
  entry is hidden.
- Checked in the live portal: the band, the Leadership cards and Spring's profile look as the
  mockup does.

**Found doing it, fixed in `74205fc`:** with one agent (Utah Life has one so far) the agents grid
stretched that card across the page; one or two leaders did the same. And the drawer's example
placeholders were bold enough to read as values.

**Win the Day, the same day, at Connor's request:**

- Before importing: Utah Life had no playbook, lists or scripts (so the import replaced nothing)
  and no other drafts pending (so Publish took only the playbook live).
- Imported `utah-life-playbook.json` in the console (13 lists, 12 scripts) and published it. The
  playbook, all 13 lists and all 12 scripts are live, with nothing left pending.
- *Check the ids against Follow Up Boss*: all 20 ids are smart lists in the account. The live
  page's list links open `liveutah1`'s smart lists.
- **Found doing it, fixed in `464014b`:** after Publish the console went on saying *Draft · not yet
  published* until a reload. Publish and Discard only refreshed the overview; they now refresh
  every page.

**Still Connor's:** add the REMO link (the mockup's is `#`), put real production figures in the
band if wanted, send the invites when ready, and look at both pages as an agent.
