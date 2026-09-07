export const NAV_GROUPS = [
  {
    label: "Workspace",
    items: [
      { id: "home", label: "Home" },
      { id: "ask", label: "Ask Utah Life" },
      { id: "wtd", label: "Win the Day", capability: "wtd" },
      { id: "sunburst", label: "Sunburst Coaching" },
      { id: "calendar", label: "Team Calendar", capability: "team_calendar" },
      { id: "tools", label: "Tool Launchpad" },
      { id: "numbers", label: "My Numbers", capability: "own_numbers" },
    ],
  },
  {
    label: "Learn",
    items: [
      { id: "onboarding", label: "Your First 30 Days" },
      { id: "training", label: "Training Library", capability: "training_library" },
      { id: "sops", label: "SOPs", capability: "sop_library" },
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
      { id: "marketing", label: "Requests", capability: "marketing_requests" },
    ],
  },
  {
    label: "Partners",
    items: [
      { id: "partners", label: "JV Partners" },
    ],
  },
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

