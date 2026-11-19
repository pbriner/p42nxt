# Budget (`budget`)

Version: 0.1.0 · Last updated: 2026-11-17

## Purpose

Plans per cost center, cost type and month in versions that are approved and locked, compared
with what was spent.

## Depends on

| Module | Versions | What for |
|---|---|---|
| `sys` | `>=0.43,<1` | States, comments, amounts |
| `costing` | `>=0.3,<1` | Cost centers and cost types |

Works with `finance` when it's installed (the actuals), without needing it.

Core dependency: no

## Tables

| Table | What a record is | Main fields | Notes |
|---|---|---|---|
| `budget.version` | A budget or forecast for a year | name, year, kind, state, currency, note | A state flow; comments |
| `budget.line` | One planned amount | version, center, cost_type, month, currency (the version's), amount, note | Unique per version, center, cost type and month; only in drafts; only on cost centers |

Deleting a version deletes its lines with it (after a confirmation listing them; restoring the
version brings them back). A cost center or cost type with budget lines can't be deleted.

## Roles and access

| Role | Can | Record conditions / personal data |
|---|---|---|
| `budget.planner` | Everything on Budget's tables; includes `costing.reader` | Moves of the flow: submit only |
| `budget.controller` | The same; approves, sends back, locks | |
| `budget.reader` | Reads Budget's tables; includes `costing.reader` | |

## Views, navigation and dashboards

Lists and forms (a version's form lists its lines); the navigator's section "Budget". Dashboard
`budget` with a picked version: plan used per cost center (chart), budget used (figure).

## Workflows, approvals and jobs

draft → submitted (Submit) → approved (Approve) → locked (Lock); submitted → draft (Send back).
The action **Spread a year**. No jobs.

## API

The records API, the action `budget.spread`, and the routes
`GET /api/v1/budget/widget/plan_vs_actual?version=<id>` and `/widget/used?version=<id>`.

## Outside systems

None.
