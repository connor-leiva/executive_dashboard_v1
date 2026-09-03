export const ROLE_OPTIONS = ["Buyer Agent", "Listing Agent", "Ops / Admin", "Team Leader"];

export const NAV_GROUPS = [
  {
    label: "Workspace",
    items: [
      { id: "home", label: "Home" },
      { id: "ask", label: "Ask Utah Life" },
      { id: "wtd", label: "Win the Day" },
      { id: "sunburst", label: "Sunburst Coaching" },
      { id: "calendar", label: "Team Calendar" },
      { id: "tools", label: "Tool Launchpad" },
      { id: "numbers", label: "My Numbers" },
    ],
  },
  {
    label: "Learn",
    items: [
      { id: "onboarding", label: "Your First 30 Days" },
      { id: "training", label: "Training Library" },
      { id: "sops", label: "SOPs" },
    ],
  },
  {
    label: "Team",
    items: [
      { id: "directory", label: "Who's Who" },
      { id: "phone", label: "On The Phone" },
    ],
  },
  {
    label: "Marketing",
    items: [
      { id: "brand", label: "Brand Kit" },
      { id: "listing", label: "Listing Marketing" },
      { id: "marketing", label: "Requests" },
    ],
  },
  {
    label: "Partners",
    items: [
      { id: "partners", label: "JV Partners" },
    ],
  },
];

export const TOOL_GROUPS = [
  {
    id: "daily",
    label: "Daily Work",
    tools: [
      { key: "follow_up_boss", name: "Follow Up Boss", note: "CRM and follow-up" },
      { key: "sisu", name: "Sisu", note: "Production numbers" },
      { key: "slack", name: "Slack", note: "Team communication" },
      { key: "google_calendar", name: "Google Calendar", note: "Team schedule" },
    ],
  },
  {
    id: "learning",
    label: "Coaching and Learning",
    tools: [
      { key: "sunburst", name: "Sunburst", note: "Weekly coaching" },
      { key: "place", name: "PLACE", note: "Training resources" },
      { key: "skool", name: "Skool", note: "Community learning" },
      { key: "training_library", name: "Training Library", note: "Internal lessons" },
    ],
  },
  {
    id: "marketing",
    label: "Marketing",
    tools: [
      { key: "canva", name: "Canva" },
      { key: "brand_kit", name: "Brand Kit" },
      { key: "listing_marketing", name: "Listing Marketing" },
      { key: "marketing_requests", name: "Requests" },
    ],
  },
];

export const QUICK_LAUNCH = [
  { key: "sunburst", name: "Sunburst", note: "Weekly plan" },
  { key: "slack", name: "Slack", note: "Team channels" },
  { key: "sisu", name: "Sisu", note: "Numbers" },
  { key: "follow_up_boss", name: "Follow Up Boss", note: "CRM" },
];

export const PRIORITY_ITEMS = [
  { title: "New lead follow-up", source: "Follow Up Boss", note: "Source not connected" },
  { title: "Appointment prep", source: "Calendar", note: "Calendar not connected" },
  { title: "Marketing request", source: "Requests", note: "Destination not connected" },
];

export const WTD_BLOCKS = [
  {
    id: "power",
    title: "Power Up",
    items: [
      "Review priorities",
      "Check calendar blocks",
      "Confirm follow-up list",
    ],
  },
  {
    id: "time",
    title: "Time-Sensitive",
    items: [
      "Return urgent messages",
      "Review active client needs",
      "Confirm today's appointments",
    ],
  },
  {
    id: "market",
    title: "Market Prep",
    items: [
      "Review hot sheets",
      "Check pricing changes",
      "Identify client opportunities",
    ],
  },
  {
    id: "lead",
    title: "Lead Generation",
    items: [
      "Work contact list",
      "Log call outcomes",
      "Send next-step messages",
    ],
  },
  {
    id: "close",
    title: "Close Out",
    items: [
      "Update CRM",
      "Record numbers",
      "Set tomorrow's first action",
    ],
  },
];

export const FUB_LISTS = [
  { key: "new_leads", name: "New leads" },
  { key: "hot_leads", name: "Hot leads" },
  { key: "sphere", name: "Sphere" },
  { key: "past_clients", name: "Past clients" },
  { key: "buyers", name: "Active buyers" },
  { key: "sellers", name: "Active sellers" },
  { key: "nurture", name: "Nurture" },
  { key: "database", name: "Database" },
  { key: "open_house", name: "Open house" },
  { key: "investors", name: "Investors" },
  { key: "vendors", name: "Vendors" },
  { key: "referrals", name: "Referral partners" },
  { key: "birthday", name: "Birthdays" },
];

export const TRAINING = [
  { key: "orientation", title: "Orientation", lessons: ["Team overview", "Systems tour", "First-week standards"] },
  { key: "crm", title: "CRM Workflows", lessons: ["Daily follow-up", "Lead stages", "Database care"] },
  { key: "sales", title: "Sales Practice", lessons: ["Discovery", "Consultation", "Objection handling"] },
  { key: "contracts", title: "Contracts", lessons: ["Buyer path", "Listing path", "Compliance review"] },
];

export const ONBOARDING = [
  { key: "profile", title: "Profile and accounts" },
  { key: "tools", title: "Core tools access" },
  { key: "calendar", title: "Calendar connected" },
  { key: "brand", title: "Brand assets reviewed" },
  { key: "crm", title: "CRM setup complete" },
  { key: "training", title: "Required training started" },
  { key: "numbers", title: "Production goals entered" },
  { key: "mentor", title: "Mentor check-in scheduled" },
];

export const SOPS = [
  { key: "lead-intake", title: "Lead Intake", area: "Sales" },
  { key: "buyer-consult", title: "Buyer Consultation", area: "Sales" },
  { key: "listing-launch", title: "Listing Launch", area: "Listings" },
  { key: "contract-to-close", title: "Contract to Close", area: "Transactions" },
  { key: "open-house", title: "Open House", area: "Marketing" },
  { key: "referral", title: "Referral Handoff", area: "Partners" },
];
