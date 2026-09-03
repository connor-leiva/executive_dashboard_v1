export const TOKEN_KEY = "cc_token";
export const TENANT_HOST_KEY = "cc_console_tenant_host";

export const NAV_SECTIONS = [
  {
    label: "Workspace",
    items: [
      { to: "/", label: "Overview" },
      { to: "/brand", label: "Brand & Identity" },
    ],
  },
  {
    label: "Access",
    items: [
      { to: "/roster", label: "People & Roster" },
      { to: "/perms", label: "Roles & Permissions" },
    ],
  },
  {
    label: "Content",
    items: [
      { to: "/training", label: "Training Library" },
      { to: "/sops", label: "SOP Library" },
      { to: "/wtd", label: "Win the Day" },
      { to: "/launchpad", label: "Tool Launchpad" },
    ],
  },
  {
    label: "Connections",
    items: [
      { to: "/calendar", label: "Team Calendar" },
      { to: "/integrations", label: "Integrations" },
    ],
  },
  {
    label: "Intelligence",
    items: [
      { to: "/assistant", label: "AI Assistant" },
      { to: "/audit", label: "Audit Log" },
    ],
  },
];

export const ROLE_OPTIONS = [
  { key: "buyer_agent", label: "Buyer Agent" },
  { key: "listing_agent", label: "Listing Agent" },
  { key: "ops_admin", label: "Ops / Admin" },
  { key: "team_leader", label: "Team Leader" },
  { key: "jv_partner", label: "JV Partner" },
];

export const ROSTER_FILTERS = [
  { key: "active", label: "Active" },
  { key: "pending", label: "Pending" },
  { key: "guests", label: "Guests" },
  { key: "leadership", label: "Leadership" },
  { key: "everyone", label: "Everyone" },
];

export const AUTH_SOURCE_OPTIONS = [
  { key: "Manual", label: "Manual" },
  { key: "Guest", label: "Guest" },
  { key: "SSO", label: "SSO" },
];

export const TILE_AUTH_OPTIONS = [
  { key: "SSO", label: "SSO" },
  { key: "Deeplink", label: "Deeplink" },
  { key: "Invite", label: "Invite" },
  { key: "Link", label: "Link" },
];

export const COURSE_STATE_OPTIONS = [
  { key: "Draft", label: "Draft" },
  { key: "Live", label: "Live" },
  { key: "Needs Review", label: "Needs Review" },
];

export const LESSON_SOURCE_OPTIONS = [
  { key: "HERE", label: "Hosted" },
  { key: "LOOM", label: "Loom" },
  { key: "SKOOL", label: "Skool" },
  { key: "PLACE", label: "PLACE" },
  { key: "EXP", label: "eXp" },
  { key: "PDF", label: "PDF" },
];

export const LESSON_SOURCE_COLORS = {
  HERE: "#395262",
  LOOM: "#8E4EA8",
  SKOOL: "#2F6444",
  PLACE: "#C9A227",
  EXP: "#4D6FB3",
  PDF: "#A44A33",
};

export const DEFAULT_ROSTER_FILTER = "active";
export const GUEST_AUTH_SOURCE = "Guest";
export const GUEST_ROLE_KEY = "jv_partner";
export const REMOVED_MEMBER_STATUS = "Removed";
export const DEFAULT_TILE_GROUP = "Tools";
export const DEFAULT_TILE_AUTH = "Link";

export const PERMISSION_LEVELS = ["Full", "View", "Limited", "None"];
export const DEFAULT_PERMISSION_LEVEL = "None";
export const CONSOLE_ACCESS_KEY = "console_access";
export const CONSOLE_ACCESS_LEVELS = ["Full", "None"];
export const LEVEL_CLASS = {
  Full: "full",
  View: "view",
  Limited: "limited",
  None: "none",
};

export const OVERVIEW_COUNTS = [
  { key: "members", label: "People" },
  { key: "courses", label: "Courses" },
  { key: "sops", label: "SOPs" },
  { key: "tiles", label: "Tiles" },
  { key: "wtd_lists", label: "WTD lists" },
];

export const COPY = {
  productName: "Utah Life",
  consoleName: "Admin Console",
  poweredBy: "Powered by PLACE",
  loginTitle: "Sign in to continue",
  loginButton: "Sign in",
  tenantHostLabel: "Tenant host",
  emailLabel: "Email",
  passwordLabel: "Password",
  signOut: "Sign out",
  loading: "Loading",
  retry: "Retry",
  noAccess: "You don't have access to this console.",
  loadFailed: "Console data failed to load.",
  overview: "Overview",
  publish: "Publish",
  discard: "Discard",
  pending: "pending",
  noPending: "No draft changes",
  setupTitle: "Setup Checklist",
  recentTitle: "Recent Activity",
  rosterTitle: "People & Roster",
  rosterSearch: "Search name or email",
  rosterInvite: "Invite Person",
  rosterSync: "Sync Roster",
  rosterEmpty: "No people match this view.",
  rosterName: "Full name",
  rosterEmail: "Email",
  rosterMarket: "Market",
  rosterRole: "Role",
  rosterAuth: "Auth source",
  rosterStatus: "Status",
  rosterActive: "Active",
  rosterPending: "Pending",
  rosterGuests: "Guests",
  rosterLeadership: "Leadership",
  rosterRemoved: "Removed this month",
  rosterLastSync: "Last sync",
  rosterNeverSynced: "Not synced",
  rosterRemove: "Remove",
  rosterInvited: "Invited",
  rosterSaved: "Saved",
  permissionsTitle: "Roles & Permissions",
  permissionsEmpty: "No permission matrix is configured.",
  permissionsSave: "Save Matrix",
  permissionsSaving: "Saving",
  permissionsDirty: "Unsaved permission changes",
  permissionsClean: "Matrix is current",
  permissionsCapability: "Capability",
  permissionsDescription: "Description",
  permissionsError: "Permission matrix failed to load.",
  permissionsSaved: "Permissions saved",
  launchpadTitle: "Tool Launchpad",
  launchpadEmpty: "No launchpad tiles are configured.",
  launchpadPreview: "Role Preview",
  launchpadVisible: "visible",
  launchpadNew: "New Tile",
  launchpadSave: "Save Tile",
  launchpadCreate: "Create Tile",
  launchpadRemove: "Set Inactive",
  launchpadName: "Name",
  launchpadLogo: "Logo key",
  launchpadGroup: "Group",
  launchpadUrl: "URL",
  launchpadAuth: "Auth type",
  launchpadActive: "Active",
  launchpadRoles: "Role visibility",
  launchpadSaved: "Tile saved",
  launchpadCreated: "Tile created",
  launchpadError: "Launchpad tiles failed to load.",
  launchpadNoSelection: "Select a tile or create a new one.",
  moveUp: "Up",
  moveDown: "Down",
  wtdTitle: "Win the Day",
  wtdEmpty: "No Win the Day lists are configured.",
  wtdError: "Win the Day lists failed to load.",
  wtdListsInRun: "Lists in run",
  wtdPairedScripts: "Paired scripts",
  wtdDailyTarget: "Daily touch target",
  wtdName: "List name",
  wtdExternalId: "External list id",
  wtdScript: "Paired script",
  wtdTarget: "Daily target",
  wtdActive: "Active",
  wtdProvider: "Provider",
  wtdLink: "Open list",
  wtdDisconnected: "Disconnected",
  wtdSave: "Save List",
  wtdSaved: "List saved",
  trainingTitle: "Training Library",
  trainingEmpty: "No courses are configured.",
  trainingError: "Training library failed to load.",
  trainingNewCourse: "New Course",
  trainingCreateCourse: "Create Course",
  trainingSaveCourse: "Save Course",
  trainingArchiveCourse: "Archive Course",
  trainingCourseSaved: "Course saved",
  trainingCourseCreated: "Course created",
  trainingLessonSaved: "Lesson saved",
  trainingLessonCreated: "Lesson created",
  trainingCourseTitle: "Title",
  trainingCategory: "Category",
  trainingDescription: "Description",
  trainingState: "State",
  trainingTrackProgress: "Track progress",
  trainingRequiredOnboarding: "Required for onboarding",
  trainingCertificate: "Issue certificate",
  trainingSequential: "Lock lessons in order",
  trainingVisibility: "Role visibility",
  trainingLessons: "Lessons",
  trainingAddLesson: "Add Lesson",
  trainingSaveLesson: "Save Lesson",
  trainingRemoveLesson: "Remove",
  trainingImportSkool: "Import from Skool",
  trainingImportUnavailable: "Import from Skool is out of scope for this phase.",
  trainingLessonTitle: "Lesson title",
  trainingSourceType: "Source",
  trainingSourceRef: "URL or storage key",
  trainingSourceLabel: "Source label",
  trainingDuration: "Minutes",
  trainingRequiredLesson: "Required",
  trainingSelectCourse: "Select a course to edit.",
  emptyActivity: "No activity yet.",
  emptySetup: "No setup tasks yet.",
  previewingAs: "Viewing as",
  ask: "Ask Utah Life",
  search: "Search configuration",
  tenant: "Tenant",
  updateTenant: "Use tenant",
  configured: "configured",
  complete: "complete",
  draftChanges: "Draft changes",
  publishReady: "Ready to publish",
  allClear: "All clear",
  signedOut: "Signed out",
  overviewEmpty: "No console configuration exists yet.",
  incompleteScreen: "This screen will be wired in its build phase.",
};

export const DEFAULT_TENANT_HOST = "utah-life.acumyn.io";

export const ROUTE_TITLES = {
  "/": "Overview",
  "/brand": "Brand & Identity",
  "/roster": "People & Roster",
  "/perms": "Roles & Permissions",
  "/training": "Training Library",
  "/sops": "SOP Library",
  "/wtd": "Win the Day",
  "/launchpad": "Tool Launchpad",
  "/calendar": "Team Calendar",
  "/integrations": "Integrations",
  "/assistant": "AI Assistant",
  "/audit": "Audit Log",
};

export const COUNT_ROUTES = {
  members: "/roster",
  courses: "/training",
  sops: "/sops",
  tiles: "/launchpad",
  wtd_lists: "/wtd",
};

export const PARKED_ROUTES = {
  "/brand": "Brand & Identity",
  "/roster": "People & Roster",
  "/perms": "Roles & Permissions",
  "/training": "Training Library",
  "/sops": "SOP Library",
  "/wtd": "Win the Day",
  "/launchpad": "Tool Launchpad",
  "/calendar": "Team Calendar",
  "/integrations": "Integrations",
  "/assistant": "AI Assistant",
  "/audit": "Audit Log",
};
