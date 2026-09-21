/* Axcion marketing — copy and configuration.
 *
 * Content lives here rather than inside Landing.jsx so that changing a claim, a price or a
 * CTA target does not mean reading JSX. Every capability claim below was checked against
 * this repo; the `status` field records what the CODE supports, not what we would like it
 * to. Anything marked "early" is gated off by default in config.py and must not be
 * described on the page as if it ships today.
 */

/* ─── Configuration you must set before launch ──────────────────────────────────────────
 *
 * !!  SIGNUP DOES NOT EXIST YET.  !!
 *
 * There is no self-serve signup route and no billing anywhere in the backend. Onboarding today
 * is an operator provisioning a workspace, whose owner then invites the team by email.
 *
 * So `signupUrl` below points at a mailto until a real trial flow exists. Point it at the
 * signup route the day that route is built; nothing else on the page has to change.
 *
 * !!  hello@axcion.io CANNOT RECEIVE MAIL YET.  !!
 *
 * axcion.io has no MX record, and mail.axcion.io is Resend's SENDING subdomain, which creates
 * no inbox. Every "Start free trial", "Book a demo", "Talk to us" and "Contact" on this site is
 * a mailto to this address, so until a mailbox exists for it (MX records at the registrar)
 * each of them bounces. Change the address here, or create the inbox, before launch.
 *
 * "Sign in" is not configured here: it goes to the workspace finder on app.<this host> — see
 * hosts.js. A workspace is its own host, so there is no single sign-in page to link to.
 */
export const SITE = {
  signupUrl: "mailto:hello@axcion.io?subject=Axcion%20trial",
  signupLabel: "Start free trial",
  contactEmail: "hello@axcion.io",
};

/* Set to false once real figures replace the placeholders in PRICING. While true, the page
   renders a visible "placeholder pricing" notice, so an unfinished pricing table cannot go
   live unnoticed. This is a one-line flip, deliberately. */
export const PRICING_IS_PLACEHOLDER = true;

export const HERO = {
  eyebrow: "For real estate teams",
  /* The display specimen from the brand guide (§07). It is Axcion's own line. */
  headline: "Track production, not spreadsheets",
  sub: "Axcion connects the systems a real estate team already runs — Sisu, QuickBooks, Follow Up Boss, GoHighLevel — and reconciles them into one production view. Every number on screen traces back to a source record.",
  note: "Read-only connections. Axcion never writes to your books or your CRM.",
};

/* Every one of these has a working client making real HTTP calls in
   backend/app/integrations/. None is a stub. */
export const SOURCES = [
  { name: "QuickBooks Online", detail: "P&L, trial balance, chart of accounts" },
  { name: "Sisu", detail: "Production, commissions, closings" },
  { name: "Follow Up Boss", detail: "Agents and leads" },
  { name: "GoHighLevel", detail: "Contacts, pipelines, subscriptions" },
  { name: "Stripe", detail: "Charges, subscriptions, failed payments" },
  { name: "Arive", detail: "Loan pipeline and referrals" },
];

export const PROBLEM = {
  heading: "The numbers exist. They just don't agree.",
  body: "Production lives in Sisu. Cash lives in QuickBooks. Leads live in the CRM. Every Monday someone exports all three into a spreadsheet, reconciles them by hand, and by the time the meeting starts the figures are already a week old — and nobody can say where any of them came from.",
  points: [
    { k: "Stale by Monday", v: "A hand-built report is out of date the moment it is finished." },
    { k: "Nobody owns the number", v: "Two exports disagree and there is no way to see which source is right." },
    { k: "It doesn't scale", v: "A second entity doubles the reconciliation instead of sharing it." },
  ],
};

export const STEPS = [
  {
    n: "01",
    t: "Connect",
    d: "Authorise QuickBooks and paste credentials for the systems you already run. Connections are stored encrypted and are read-only — Axcion pulls, it never pushes.",
  },
  {
    n: "02",
    t: "Reconcile",
    d: "Accounts map to one chart across every entity. A tie-out check compares the mapped total against the trial balance and blocks the statement rather than showing you a number that does not balance.",
  },
  {
    n: "03",
    t: "Trace",
    d: "Click any figure to see the records behind it, how it was computed in plain English, and a deep link into the source system to verify it yourself.",
  },
];

/* status: "live" — shipped and on by default.
   status: "early" — implemented but flag-gated OFF (config.py). Say so on the page. */
export const MODULES = [
  {
    name: "Production",
    status: "live",
    d: "GCI, units, pipeline and per-agent volume from Sisu and Follow Up Boss, next to an EOS-style weekly scorecard. Share a read-only scorecard link, or embed the live scorecard directly in ClickUp.",
  },
  {
    name: "Books",
    status: "live",
    d: "A consolidated P&L across every entity, a queue for categorising transactions, intercompany links, and shared-cost allocations that account for charges already sitting in the books instead of double-counting them.",
  },
  {
    name: "Campaigns",
    status: "live",
    d: "Track a launch from lead to enrolment against plan, with a sales desk built on an event log — so a rebooked call cannot erase the no-show that came before it. Recordings, transcripts and search included.",
  },
  {
    name: "Binder",
    status: "live",
    d: "Every entity, document and filing obligation in one matrix. Uploads are read for obligations, a person confirms each one, and jurisdiction rules carry a freshness date because compliance law moves. Sits behind a second factor.",
  },
  {
    name: "Agents",
    status: "early",
    d: "AI teammates that draft campaign work on a schedule — audits, briefs, scripts, tagging. Everything lands as a draft for a human to approve; nothing ships on its own. Off by default, available on request.",
  },
];

export const TRACE = {
  eyebrow: "Lineage",
  heading: "Every number is a question you can answer",
  body: "Most dashboards give you a figure and ask you to trust it. Axcion maps every metric to the records it was computed from, a plain-English description of the calculation, and a link straight into QuickBooks, Sisu, Stripe or the CRM. A drill-down also respects permissions — someone who cannot see a tab cannot reach its records through a number.",
  example: {
    metric: "GCI, month to date",
    value: "$412,900",
    computed: "Sum of company dollar on 38 closed transactions with a closing date in the period.",
    rows: [
      ["TX-2026-0881", "Dana Whitfield", "$4,120,000", "Sisu"],
      ["TX-2026-0874", "Marcus Oyelaran", "$3,480,500", "Sisu"],
      ["TX-2026-0869", "Priya Raghavan", "$2,905,000", "FUB"],
    ],
  },
};

/* Each claim below maps to something real in the backend. Nothing aspirational. */
export const TRUST = [
  { t: "Read-only by design", d: "Axcion pulls data and never writes it back. No integration has a write path to your books or your CRM, so connecting it cannot change your source of truth." },
  { t: "Credentials encrypted at rest", d: "Integration secrets and two-factor seeds are Fernet-encrypted in the database, and the application refuses to start if it is still holding a default key." },
  { t: "Two-factor step-up", d: "Sensitive areas ask for a second factor again on a short-lived grant, held only for the browser session. Closing the tab re-locks them." },
  { t: "Permissions down to the number", d: "Members are granted individual tabs, and every metric is mapped to the tab that owns it — so a drill-down cannot leak past what someone is allowed to see." },
  { t: "Separate workspaces", d: "Each customer is its own tenant on its own subdomain, and every query is scoped to it. The console we use to provision and support workspaces runs in a different token realm that cannot reach your financial or operational data — it answers whether a workspace exists and whether its syncs are healthy, never what your numbers are." },
  { t: "Audit trail", d: "Changes to people, access and connections are recorded in an audit log that the people who run your workspace can review, so any change to who can see what can be accounted for." },
];

/* ⚠️  PLACEHOLDER PRICING — invented to show the layout. Nothing in the repo defines
       Axcion's pricing. Replace every `price` and `priceNote`, then set
       PRICING_IS_PLACEHOLDER to false above. */
export const PRICING = [
  {
    name: "Team",
    price: "$—",
    priceNote: "per month",
    blurb: "One brokerage or team, one set of books.",
    features: ["Production and scorecard", "Books for a single entity", "Up to 10 seats", "All source connections", "Email support"],
    cta: "secondary",
  },
  {
    name: "Portfolio",
    price: "$—",
    priceNote: "per month",
    blurb: "Several entities that need to roll up into one view.",
    features: ["Everything in Team", "Unlimited entities and consolidation", "Intercompany and allocations", "Campaigns and sales desk", "Up to 40 seats", "Priority support"],
    cta: "primary",
    featured: true,
  },
  {
    name: "Enterprise",
    price: "Talk to us",
    priceNote: "",
    blurb: "Custom domains, bespoke sources, and hands-on onboarding.",
    features: ["Everything in Portfolio", "Binder and compliance", "Agents early access", "Custom integrations", "Unlimited seats", "Named onboarding"],
    cta: "secondary",
  },
];

export const FAQ = [
  { q: "Can Axcion change anything in QuickBooks?", a: "No. Every integration is read-only. Axcion pulls data and reconciles it; writeback does not exist in the product today." },
  { q: "How long does connecting take?", a: "QuickBooks is an OAuth authorisation and takes about a minute per entity. The others are an API key or a username and token pasted into settings. Your first full sync usually completes the same day." },
  { q: "What if a number looks wrong?", a: "Click it. You get the records it came from, the calculation in plain English, and a link into the source system so you can check it against the original." },
  { q: "Where do I sign in?", id: "signin", a: "Each workspace has its own address — your-team.axcion.io — and that is where you and your team sign in. It is in your invitation email. If you cannot find it, enter your work email at app.axcion.io and we will email you a link to every workspace it belongs to." },
  { q: "Do you support single sign-on?", a: "Every workspace can sign in with Google, and the people who run the workspace can limit that to their company's email domains. SAML single sign-on is not available yet. Sensitive areas such as Binder ask for a second factor from an authenticator app, with recovery codes." },
  { q: "Where does my data live?", a: "Your reconciled data sits in a Postgres database, scoped to your own workspace, with integration credentials encrypted at rest. Files you upload — Binder documents, logos, lesson and request attachments — are stored in Cloudflare R2. Call recording, which is off unless you turn it on, runs through Recall.ai: Recall holds the recordings under its own retention, and we delete stored transcripts after a year. The assistants, Binder's document reading and call chapters send the text they work on to Anthropic, which can mean a whole uploaded document or a call transcript. The privacy policy lists every provider." },
];

/* ─── Site structure ───────────────────────────────────────────────────────────────────── */
export const NAV = [
  { label: "Home", path: "/" },
  { label: "Features", path: "/features" },
  { label: "About", path: "/about" },
  { label: "Pricing", path: "/pricing" },
];

export const META = {
  "/":         { title: "Axcion — one production view for real estate teams" },
  "/features": { title: "Features — Axcion" },
  "/about":    { title: "About — Axcion" },
  "/pricing":  { title: "Pricing — Axcion" },
  "/privacy":  { title: "Privacy Policy — Axcion" },
  "/terms":    { title: "Terms of Service — Axcion" },
};

/* ─── About ────────────────────────────────────────────────────────────────────────────────
 *
 * DELIBERATELY QUALITATIVE. This page names a real company — Utah Life Real Estate Group, LLC
 * (`s-utahlife` in the Binder fixtures, nickname "The Team") — so it carries no production
 * figures, no headcount, no founding date and no quotes. Every sentence below is either
 * something the codebase demonstrates or something the team can say about itself without a
 * fact-check. If you want numbers on this page, they have to come from Connor, not from here:
 * inventing a top-producing brokerage's volume would be fabricating a record about a real
 * business, and it is the kind of claim a competitor or a regulator would check first.
 */
export const ABOUT = {
  eyebrow: "About",
  headline: "We built this for our own brokerage first",
  standfirst: "Axcion started as an internal tool at Utah Life Real Estate Group. It was not a product idea. It was a Monday morning problem that would not go away.",

  story: [
    {
      h: "The spreadsheet that would not die",
      p: "A brokerage runs on more systems than anyone plans for. Production lived in Sisu. Leads lived in Follow Up Boss. The books lived in QuickBooks. None of them agreed, and none of them were wrong exactly — they were each answering a slightly different question. So every Monday someone exported all three, reconciled them by hand, and walked into the leadership meeting with a number that was already a week old and could not be defended if anyone pushed on it.",
    },
    {
      h: "Then it multiplied",
      p: "One brokerage is a manageable amount of reconciliation. What broke the spreadsheet was growth: a mortgage joint venture, then membership businesses, each legally separate, each with its own books and its own operating system. Every new entity did not add work — it multiplied it, because now the question was not just what each business did, but what they added up to, and which shared costs had already been booked where.",
    },
    {
      h: "Charts were never the hard part",
      p: "The first version was a dashboard, and it was not enough. Anyone can put a figure on a screen. The problem was that nobody trusted the figure — and they were right not to, because there was no way to check it. What actually mattered turned out to be unglamorous: mapping every account to one chart across every entity, refusing to render a statement that does not tie out to the trial balance, and making every number on every screen clickable down to the transactions it came from and the system it came from.",
    },
    {
      h: "Which is why it works for other teams",
      p: "Nothing above is specific to us. Any team running production in one system and books in another has the same Monday. So the tool got a second tenant, then the multi-tenant infrastructure to make that safe, and then a name. We still run our own brokerage on it every week, which is the only product feedback loop we have ever fully trusted.",
    },
  ],

  /* Each of these is enforced somewhere in the codebase, not aspirational. */
  principles: [
    { t: "Read-only, always", d: "Axcion pulls data and never writes it back. Your books and your CRM stay the source of truth, so connecting it can never be the thing that breaks them." },
    { t: "A number you cannot trace is a rumour", d: "Every figure maps to the records behind it, the calculation in plain English, and a link into the system it came from. If we cannot show you where it came from, we would rather not show it." },
    { t: "Refuse rather than mislead", d: "When a mapped statement does not tie out to the trial balance, it does not render. A blocked statement is an afternoon of work. A wrong one that looked right is a quarter." },
    { t: "A person approves anything that ships", d: "Where the product drafts work — reading a document for filing obligations, preparing campaign material — it lands as a draft for review. Nothing commits itself." },
  ],

  closing: {
    h: "Still an operating company",
    p: "Axcion is built by people who close transactions, file entity paperwork and sit in the same Monday meeting the product exists to serve. That is the whole reason it is shaped the way it is — and it is why the roadmap tends to be short, specific and drawn from things that annoyed us last week.",
  },
};

/* ─── Features page ───────────────────────────────────────────────────────────────────────
 * The deep version of MODULES. Same `status` discipline: "early" is flag-gated OFF in
 * config.py and must be labelled as such.
 */
export const FEATURE_DETAIL = [
  {
    name: "Production",
    status: "live",
    lead: "What the team actually did this period, without waiting for the books to close.",
    points: [
      "GCI, units, volume and pipeline pulled from Sisu and Follow Up Boss",
      "Per-agent leaderboards with commission detail behind every row",
      "A weekly EOS-style scorecard with goals, pace and trend",
      "Hand-entered metrics sit alongside computed ones, and a disconnected feed reads as \u201ccould not look\u201d rather than silently as zero",
      "Share a read-only scorecard link, or embed the live scorecard in ClickUp",
    ],
  },
  {
    name: "Books",
    status: "live",
    lead: "One consolidated P&L across every entity, tied out to QuickBooks.",
    points: [
      "Accounts from every entity map to one standard chart, with subtree rules so a new account does not block the close",
      "A tie-out check compares the mapped total to the trial balance and blocks the statement rather than showing a number that does not balance",
      "An approval queue for categorising transactions, with intercompany pairs linked and reconciled",
      "Shared-cost allocations that recognise charges already sitting in the books instead of double-counting them",
      "Period close, and a statement that ties to the source",
    ],
  },
  {
    name: "Campaigns",
    status: "live",
    lead: "A launch tracked from first lead to enrolment, against plan.",
    points: [
      "A five-stage funnel with ARR added as the headline and pacing against the launch curve",
      "A sales desk built on an append-only event log, so a rebooked call cannot erase the no-show before it",
      "Show, no-show, cancel and reschedule rates computed from that log rather than from whatever the CRM currently says",
      "Call recordings with transcripts, full-text search and AI-generated chapters",
      "Per-rep share links that show a rep their own numbers and nothing else",
    ],
  },
  {
    name: "Binder",
    status: "live",
    lead: "Every entity, document and filing obligation in one matrix.",
    points: [
      "Entities of every common type across all fifty states",
      "Upload a document and it is read for filing obligations — which a person then confirms before anything is recorded",
      "A status matrix of entity against obligation: current, due soon, overdue, not applicable",
      "Jurisdiction rules carry a last-verified date and surface when stale, because compliance law moves",
      "Sits behind a second factor on a short-lived grant",
    ],
  },
  {
    name: "Agents",
    status: "early",
    lead: "AI teammates that draft campaign work on a schedule. Off by default.",
    points: [
      "An employee is an identity, a set of skills and a cadence",
      "v1 archetype is a social media manager: audit, trend brief, strategy, carousel, reel script, attribution tagging",
      "Every run produces drafts and stops for human approval — nothing ships on its own",
      "A per-tenant token budget, so a scheduled agent cannot run up an unbounded bill",
      "Disabled unless explicitly enabled for your workspace",
    ],
  },
];

export const PLATFORM = [
  { t: "Connect once", d: "QuickBooks authorises per entity over OAuth; the rest take an API key or a token pasted into settings. Credentials are encrypted at rest and refresh automatically." },
  { t: "Sync on a schedule", d: "A worker keeps every source current in the background, with per-integration status and a manual sync when you need one now." },
  { t: "Ask in English", d: "A built-in assistant answers questions about your own numbers, scoped to exactly what the person asking is allowed to see." },
  { t: "Roles and tab grants", d: "Owners, admins and members, with individual tabs granted per person — and every metric mapped to the tab that owns it, so a drill-down cannot leak past someone's access." },
]
