# Lead sources redesign: before and after

The Nov 11 audit's fixes, as approved in the mockup. "Before" is `docs/lead-source-audit-2026-11-11/`, taken on
`main` at the audit; "after" is this folder, the same states on this branch. Both use the development fixture's
fictional people, at 1440 × 900 and 2× density. The digest recipient is shown as `owner@example.com` in both.

Audit numbers are in brackets.

| Screen | Before | After | What changed |
| --- | --- | --- | --- |
| Cohorts, default range | — | [R00](R00-cohorts-month-default.jpg) | The app's date bar drives the report (H1). Month is the bar's default, so the cohort view opens on November, and the meta line offers "Show since Feb 2026". |
| Cohorts, whole program | [R01](../lead-source-audit-2026-11-11/R01-cohorts-loaded.jpg) | [R01](R01-cohorts-loaded.jpg) | No second month range (H1). Source picked from the title (M4). 18 columns fit 1440: maturity under the month, net spend with gross in the tooltip (H2, L1). Method notes in info glyphs (H4). Outlined Export CSV (M3). "Volume closed $6.5M" (M7). Level tile titles (L5). Never-contacted counts in the speed card, gold only for this month and last (M10). |
| Spend not mapped | [R02](../lead-source-audit-2026-11-11/R02-cohorts-spend-not-mapped.jpg) | [R02](R02-cohorts-spend-not-mapped.jpg) | One wide "Spend, ROI and net profit · Not mapped" tile with "Map QuickBooks lines", in place of three tiles saying it (M2). |
| No leads at all | [R03](../lead-source-audit-2026-11-11/R03-cohorts-empty-no-leads.jpg) | [R03](R03-cohorts-empty-no-leads.jpg) | One empty state for every view (M1). |
| None in the range | — | [R10b](R10b-team-pipeline-none-in-range.jpg) | The date-range empty state, with "Show since Feb 2026" (M1). |
| Loading | [R04](../lead-source-audit-2026-11-11/R04-cohorts-loading.jpg) | [R04](R04-cohorts-loading.jpg) | Unchanged apart from the header. |
| Error | [R05](../lead-source-audit-2026-11-11/R05-cohorts-error.jpg) | [R05](R05-cohorts-error.jpg) | Says what happened: a server error, no answer, or no access (L4). |
| No groups | [R06](../lead-source-audit-2026-11-11/R06-report-no-groups.jpg) | [R06](R06-report-no-groups.jpg) | Unchanged. |
| Cohort leads drawer | [R07](../lead-source-audit-2026-11-11/R07-cohort-leads-drawer.jpg) | [R07](R07-cohort-leads-drawer.jpg) | Unchanged. |
| Closings drawer | [R08](../lead-source-audit-2026-11-11/R08-closed-deals-drawer.jpg) | [R08](R08-closed-deals-drawer.jpg) | Each term on one line, both EXP fees one figure, "Feb 2026 cohort" (M6). |
| Team pipeline | [R09](../lead-source-audit-2026-11-11/R09-team-pipeline-loaded.jpg) | [R09](R09-team-pipeline-loaded.jpg) | No Coaching card; each agent's reason under their name (H5). Short stage names (M5). Gap in ink, red only on the critical row, "Contacted, 51 pts below" (M8, L4). |
| Team pipeline, no leads | [R10](../lead-source-audit-2026-11-11/R10-team-pipeline-empty.jpg) | [R10](R10-team-pipeline-empty.jpg) | The shared empty state (M1). |
| Agent page | [R11](../lead-source-audit-2026-11-11/R11-agent-pipeline-loaded.jpg) | [R11](R11-agent-pipeline-loaded.jpg) | Predicted close tile in place of a second speed figure (M2). Stuck filter lists only stages that hold someone (M11). Chart caption in words, "1 met from 1 appointment" (L4). |
| What's working | [R12](../lead-source-audit-2026-11-11/R12-whats-working-agent-vs-team.jpg) | [R12](R12-whats-working-agent-vs-team.jpg) | "3 touches in the first day" (L4). |
| What's working, no leads | [R15](../lead-source-audit-2026-11-11/R15-whats-working-empty.jpg) | [R15](R15-whats-working-empty.jpg) | The shared empty state (M1). |
| Settings, group | [S01](../lead-source-audit-2026-11-11/S01-settings-loaded.jpg) | [S01](S01-settings-loaded.jpg) | Four tabs under the group, one save each (M9). Labels above fields (M12). "Add a lead source" without the slider icon (L6). |
| Settings, new group | [S02](../lead-source-audit-2026-11-11/S02-settings-new-group.jpg) | [S02](S02-settings-new-group.jpg) | One wording, labels above fields (L6, M12). |
| Follow Up Boss sources | [S05](../lead-source-audit-2026-11-11/S05-fub-card-loaded.jpg) | [S05](S05-fub-sources-tab.jpg) | Now a tab (M9). |
| QuickBooks spend | [S08](../lead-source-audit-2026-11-11/S08-spend-card-loaded.jpg) | [S08](S08-spend-tab.jpg) | Now a tab (M9). |
| Weekly digest | [S09](../lead-source-audit-2026-11-11/S09-digest-card.jpg) | [S09](S09-digest-tab.jpg) | Now a tab; labels above fields (M9, M12). |
| Save error | [S10](../lead-source-audit-2026-11-11/S10-group-save-error.jpg) | [S10](S10-group-save-error.jpg) | The reason in words, under the field, naming the line (H3). |
