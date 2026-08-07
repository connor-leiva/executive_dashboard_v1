# ULRG Scorecard — Sourcing Discovery (SPEC Part 6, Step 0)

Maps every measurable to a data source and a feasibility verdict, so resolvers (Step 5) are
built on evidence, not guesses. Derived from SPEC Part 3.2, the mockup's `src` hints, and the
integrations already in the codebase (Sisu `Transaction`, Follow Up Boss, GoHighLevel, the
Flywheel vendor-directory config). **Until a resolver is built and validated against a parallel
hand-entered week (Step 5), every metric stays `source="manual"` and shows the `HAND` chip** —
that chip is the honest list of what breaks first.

| Measurable | Type | Intended source | Verdict | Note |
| --- | --- | --- | --- | --- |
| Appointments Met | flow | sisu | needs config | Confirm the Sisu "met" vs "set" field |
| Clients Signed / Signed Units | flow | sisu | needs config | Needs the field marking a signed buyer/listing agreement |
| Under Contract | flow | sisu | **resolver feasible** | `Transaction` carries pending + date |
| Homes Sold | flow | sisu | **resolver feasible** | `Transaction` carries closed + date |
| Recruiting Appts Met | flow | manual | manual | Confirm whether recruiting lives in a GHL pipeline |
| Sympli Attach Rate | rate | sisu/vendor | needs config | Num+denom from the Flywheel vendor pick; averages weekly % (Part 8 caveat) |
| Meraki Attach Rate | rate | sisu/vendor | needs config | Same vendor-directory config as the Flywheel |
| ZHL Referral Rate | rate | sisu/vendor | needs config | Same vendor-directory config as the Flywheel |
| New Recruitment Leads | flow | manual/ghl | manual | Confirm a GHL recruiting pipeline exists |
| Mastermind RSVPs | flow | ghl | needs config | GHL registrations, same pattern as `reg_count` |
| Met to Signed Ratio YTD | snapshot | sisu | needs config | Already-cumulative ratio — never summed |
| Database HealthScore | snapshot | fub | manual | Confirm FUB exposes this vs typed |
| Q2 Homes (250 target) | flow | sisu | **resolver feasible** | Closed `Transaction` count QTD |
| QTD Agents Recruited | snapshot | manual | manual | Already-cumulative — likely typed |

## The two gating questions (must be answered before Step 5)

**(a) Can Sisu produce a buyer-agreement *signed date*, joined against "no contract record"?**
This is the join that makes the BBA worklist (Step 7) real. If **no**, the BBA worklist ships as
`manual` and loses most of its value — raise it rather than shipping a stub. **STATUS: open, needs
Connor / a Sisu field check.**

**(b) Does "weekly check-in" have any system record at all?**
Drives whether the Davis "50% of agents on weekly check-ins" rock can be auto-computed or is
hand-tracked. **STATUS: open — assume hand-tracked (`team_commitment_progress`) until confirmed.**

## Build implication
- **Step 5 first resolvers** (high-confidence, closed-`Transaction`-backed): `Homes Sold`,
  `Under Contract`, `Q2 Homes`. These validate against the existing Sisu sync with no new config.
- Everything else stays `manual` (seeded + backfilled by hand) until its question is answered.
