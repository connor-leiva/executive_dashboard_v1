export const NAV = [
  { id: "home", label: "Home", icon: "H" },
  { id: "tools", label: "Tool Launchpad", icon: "T" },
  { id: "wtd", label: "Win the Day", icon: "W" },
  { id: "training", label: "Training", icon: "R" },
  { id: "onboarding", label: "First 30 Days", icon: "30" },
  { id: "sops", label: "SOP Library", icon: "S" },
  { id: "numbers", label: "My Numbers", icon: "#" },
  { id: "calendar", label: "Team Calendar", icon: "C" },
  { id: "marketing", label: "Marketing Requests", icon: "M" },
  { id: "directory", label: "Who's Who", icon: "P" },
  { id: "brand", label: "Brand Kit", icon: "B" },
  { id: "ask", label: "Ask", icon: "A" },
];

export const TOOL_GROUPS = [
  {
    id: "crm",
    label: "Client Work",
    tools: [
      { key: "follow_up_boss", name: "Follow Up Boss", note: "CRM and lead follow-up" },
      { key: "brivity", name: "Brivity", note: "Listings and home search" },
      { key: "sisu", name: "Sisu", note: "Scorecards and transactions" },
      { key: "skyslope", name: "SkySlope", note: "Transaction documents" },
    ],
  },
  {
    id: "learning",
    label: "Learning",
    tools: [
      { key: "place", name: "PLACE", note: "Training and resources" },
      { key: "skool", name: "Skool", note: "Community learning" },
      { key: "sunburst", name: "Coaching", note: "Coaching resources" },
    ],
  },
  {
    id: "ops",
    label: "Operations",
    tools: [
      { key: "slack", name: "Slack", note: "Team communication" },
      { key: "canva", name: "Canva", note: "Design templates" },
      { key: "brand_guide", name: "Brand Guide", note: "Approved assets" },
    ],
  },
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
