export const NAV_GROUPS = [
  {
    label: "Workspace",
    items: [
      { id: "home", label: "Home" },
      { id: "ask", label: "Ask Utah Life" },
      { id: "wtd", label: "Win the Day", capability: "wtd" },
      { id: "follow-ups", label: "Follow-ups" },
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
      // Not gated on a capability. Editing your own entry is not a privilege a workspace grants;
      // it is the minimum a person has over the thing the directory says about them.
      { id: "settings", label: "My Settings" },
    ],
  },
  {
    label: "Marketing",
    items: [
      { id: "brand", label: "Brand Kit" },
      { id: "marketing", label: "Requests", capability: "marketing_requests" },
    ],
  },
  {
    label: "Partners",
    items: [
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

// The reasons somebody is on Needs You Today, most urgent first -- the server's own order
// (services/follow_ups.KINDS). `chip` finishes a count ("3 new"); `plural` heads a group.
export const FOLLOW_UP_KINDS = [
  { key: "new_lead", label: "New lead", plural: "New leads", chip: "new" },
  { key: "overdue", label: "Overdue", plural: "Overdue", chip: "overdue" },
  { key: "due_today", label: "Today", plural: "Due today", chip: "due today" },
  { key: "going_cold", label: "Going cold", plural: "Going cold", chip: "going cold" },
];

// What the floating Ask button names on each page, as the mockup's does ("Ask about Win the Day").
// A page not listed asks about anything.
export const ASK_ABOUT = {
  wtd: "Win the Day",
  sops: "the SOPs",
  training: "training",
  brand: "the brand kit",
  marketing: "a request",
  numbers: "your numbers",
  tools: "a tool",
};
