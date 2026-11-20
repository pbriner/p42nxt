# Budget (`budget`)

Version: 0.2.0 · Last updated: 2026-11-20

## Purpose

Plans per cost center and cost type, with a rhythm that fills the months, in versions that are
approved and locked, compared with what was spent.

## Depends on

| Module | Versions | What for |
|---|---|---|
| `sys` | `>=0.46,<1` | States, comments, amounts, deleting with links |
| `costing` | `>=0.3,<1` | Cost centers and cost types |

Works with `finance` when it's installed (the actuals), without needing it.

Core dependency: no

## Tables

| Table | What a record is | Main fields | Notes |
|---|---|---|---|
| `budget.version` | A budget or forecast for a year | name, year, kind, state, currency, note | A state flow; comments |
| `budget.plan_line` | One cost center and cost type of a version | version, center, cost_type, rhythm, amount, from_month, currency (the version's), year_total, note | Unique per version, center and cost type; only in drafts; only on cost centers. The year total is worked out |
| `budget.month` | One month of a line | line, month, currency, amount | Unique per line and month; written by the line's rhythm, typed only when it's "by hand" |
| `budget.line` | Retired (0.1: one line per month) | | Moved to the two above on the upgrade to 0.2; kept as it was |

Deleting a version deletes its lines and their months with it (after a confirmation listing them; restoring the
version brings them back). A cost center or cost type with budget lines can't be deleted.

## Roles and access

| Role | Can | Record conditions / personal data |
|---|---|---|
| `budget.planner` | Everything on Budget's tables; includes `costing.reader` | Moves of the flow: submit only |
| `budget.controller` | The same; approves, sends back, locks | |
| `budget.reader` | Reads Budget's tables; includes `costing.reader` | |

## Views, navigation and dashboards

Lists and forms (a version's form lists its lines, a line's its months); the navigator's section
"Budget". Dashboard `budget` with a picked version: plan used per cost center (chart), budget used
(figure).

## Workflows, approvals and jobs

draft → submitted (Submit) → approved (Approve) → locked (Lock); submitted → draft (Send back).
No jobs.

## API

The records API and the routes
`GET /api/v1/budget/widget/plan_vs_actual?version=<id>` and `/widget/used?version=<id>`.

## Outside systems

None.
