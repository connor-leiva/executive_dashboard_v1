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
      { to: "/marketing", label: "Marketing Requests" },
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

export const SOP_STATE_OPTIONS = [
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

export const BRAND_SWATCHES = [
  { key: "ink", label: "Ink" },
  { key: "brand", label: "Brand" },
  { key: "accent", label: "Accent" },
  { key: "canvas", label: "Canvas" },
  { key: "gold", label: "Gold" },
];

export const BRAND_LOGO_SLOTS = [
  { kind: "light", key: "logo_light_key", label: "Light wordmark" },
  { kind: "dark", key: "logo_dark_key", label: "Dark wordmark" },
  { kind: "mark", key: "logo_mark_key", label: "Mark" },
];

export const DEFAULT_BRAND_PALETTE = {
  ink: "#171E22",
  brand: "#395262",
  accent: "#AECBD4",
  canvas: "#EAE7E6",
  gold: "#C9A227",
};

export const CALENDAR_VIEW_OPTIONS = [
  { key: "week", label: "Week" },
  { key: "month", label: "Month" },
  { key: "agenda", label: "Agenda" },
];

export const WEEK_START_OPTIONS = [
  { key: 0, label: "Sunday" },
  { key: 1, label: "Monday" },
  { key: 2, label: "Tuesday" },
  { key: 3, label: "Wednesday" },
  { key: 4, label: "Thursday" },
  { key: 5, label: "Friday" },
  { key: 6, label: "Saturday" },
];

export const TIMEZONE_OPTIONS = [
  "America/Denver",
  "America/Chicago",
  "America/New_York",
  "America/Los_Angeles",
  "America/Phoenix",
];

export const GAP_STATUS_OPTIONS = [
  { key: "Open", label: "Open" },
  { key: "Assigned", label: "Assigned" },
  { key: "Resolved", label: "Resolved" },
  { key: "No Action", label: "No Action" },
];

export const AI_BEHAVIOUR_FIELDS = [
  { key: "always_cite", label: "Always cite sources" },
  { key: "refuse_without_source", label: "Refuse without source" },
  { key: "offer_escalation", label: "Offer escalation" },
  { key: "learn_from_corrections", label: "Learn from corrections" },
];

export const AUDIT_FILTERS = [
  { key: "Everything", label: "Everything" },
  { key: "Publish", label: "Publish" },
  { key: "Access", label: "Access" },
  { key: "Content", label: "Content" },
  { key: "Read", label: "Read" },
];

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
  productName: "Workspace",          // fallback only, until /console/workspace answers
  consoleName: "Admin Console",
  // The PLATFORM, not a brokerage. This said "Powered by PLACE" -- one customer's brokerage --
  // on every tenant's admin console. productName is only a fallback now: the rail reads the
  // workspace's own name from the API.
  poweredBy: "Powered by Acumyn",
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
  sopTitle: "SOP Library",
  sopEmpty: "No SOPs are configured.",
  sopError: "SOP library failed to load.",
  sopCurrent: "Current",
  sopDueSoon: "Due within 30 days",
  sopOverdue: "Overdue",
  sopNew: "New SOP",
  sopCreate: "Create SOP",
  sopSave: "Save SOP",
  sopArchive: "Archive SOP",
  sopSaved: "SOP saved",
  sopCreated: "SOP created",
  sopTitleField: "Title",
  sopCategory: "Category",
  sopOwner: "Owner",
  sopState: "State",
  sopReviewDue: "Review due",
  sopCategories: "Categories",
  sopCategoryName: "Category name",
  sopAddCategory: "Add Category",
  sopSaveCategory: "Save",
  sopDeleteCategory: "Delete",
  sopVersions: "Versions",
  sopUploadVersion: "Upload Version",
  sopVersionLabel: "Version label",
  sopFile: "File",
  sopCurrentVersion: "Current version",
  sopNoVersion: "No version uploaded",
  sopDownload: "Download",
  sopUploaded: "Version uploaded",
  sopSelect: "Select an SOP to edit.",
  brandTitle: "Brand & Identity",
  brandError: "Brand configuration failed to load.",
  brandIdentity: "Identity",
  brandPalette: "Palette",
  brandLogos: "Logo slots",
  brandPreview: "Live preview",
  brandPortalName: "Portal name",
  brandTagline: "Tagline",
  brandSubdomain: "Subdomain",
  brandCustomDomain: "Custom domain",
  brandDomainStatus: "Domain status",
  brandSave: "Save Brand",
  brandSaved: "Brand saved",
  brandUpload: "Upload",
  brandLogoLight: "Light wordmark",
  brandLogoDark: "Dark wordmark",
  brandLogoMark: "Mark",
  brandStoredKey: "Stored key",
  brandNoLogo: "No logo uploaded",
  brandVerified: "Verified",
  brandNotVerified: "Not verified",
  calendarTitle: "Team Calendar",
  calendarError: "Team calendar failed to load.",
  calendarDefaults: "Calendar Defaults",
  calendarCategories: "Calendar Categories",
  calendarPreview: "Week Preview",
  calendarName: "Category name",
  calendarColor: "Colour",
  calendarAddress: "External calendar address",
  calendarVisibility: "Role audience",
  calendarActive: "Active",
  calendarSave: "Save Category",
  calendarSaveDefaults: "Save Defaults",
  calendarCreate: "Add Category",
  calendarRemove: "Set Inactive",
  calendarSaved: "Calendar saved",
  calendarCreated: "Calendar category created",
  calendarDefaultsSaved: "Calendar defaults saved",
  calendarDefaultView: "Default view",
  calendarWeekStart: "Week starts on",
  calendarTimezone: "Timezone",
  calendarDisconnected: "Connect Google Workspace to preview",
  calendarNoEvents: "No calendar events available.",
  integrationsTitle: "Integrations",
  integrationsError: "Integrations failed to load.",
  integrationsEmpty: "No integrations are configured.",
  integrationsSettings: "Connection Settings",
  integrationsSync: "Sync Status",
  integrationsConfig: "Configuration",
  integrationsName: "Name",
  integrationsRole: "Role",
  integrationsDescription: "Description",
  integrationsBaseUrl: "Base URL",
  integrationsConfigKey: "Config key",
  integrationsConfigValue: "Config value",
  integrationsAddConfig: "Add Config",
  integrationsSave: "Save Integration",
  integrationsSaved: "Integration saved",
  integrationsConnect: "Connect",
  integrationsTest: "Run Test",
  integrationsUnavailable: "Not yet available",
  integrationsNeverSynced: "Never synced",
  integrationsNotConnected: "Not Connected",
  integrationsActionNeeded: "Action Needed",
  integrationsConnected: "Connected",
  aiTitle: "AI Assistant",
  aiError: "AI Assistant configuration failed to load.",
  aiBehaviour: "Behaviour",
  aiSources: "Sources",
  aiContentGaps: "Content Gaps",
  aiAlwaysCite: "Always cite sources",
  aiRefuseWithoutSource: "Refuse without source",
  aiOfferEscalation: "Offer escalation",
  aiLearnCorrections: "Learn from corrections",
  aiEscalationChannel: "Escalation channel",
  aiSaveBehaviour: "Save Behaviour",
  aiSaveSource: "Save Source",
  aiSaved: "AI settings saved",
  aiSourceSaved: "Source saved",
  aiMinimumRole: "Minimum role",
  aiEveryUser: "Every user",
  aiEnabled: "Enabled",
  aiIndexed: "Indexed",
  aiNotIndexed: "Not yet indexed",
  aiNoGaps: "No content gaps yet.",
  aiQuestion: "Question",
  aiAsks: "Asks",
  aiAssignee: "Assignee",
  aiGapStatus: "Status",
  aiResolution: "Resolution note",
  aiSaveGap: "Save Gap",
  aiGapSaved: "Gap saved",
  aiNoAssignee: "Unassigned",
  auditTitle: "Audit Log",
  auditError: "Audit log failed to load.",
  auditEmpty: "No audit events match this filter.",
  auditLoadMore: "Load More",
  auditTimestamp: "Timestamp",
  auditActor: "Actor",
  auditSummary: "Summary",
  auditCategory: "Category",
  auditAction: "Action",
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

/* Derived from NAV_SECTIONS, not listed again.
 *
 * This was a second hand-kept map of every route to its heading, and it had to agree with the nav
 * for the page title to be right. It silently did not: adding Marketing Requests to the nav gave
 * it a working screen with "Overview" in the header, because the title map had never heard of it
 * and titleFor() falls back to "/".
 *
 * The nav already carries the route and its label for every screen, so the map is the same facts
 * a second time. Deriving it means a screen added to the nav cannot ship with the wrong heading.
 * Verified lossless against the literal it replaces: all twelve entries were already identical. */
export const ROUTE_TITLES = Object.fromEntries(
  NAV_SECTIONS.flatMap((section) => section.items.map((item) => [item.to, item.label])),
);

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
