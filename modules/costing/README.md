# Costing (`costing`)

Version: 0.2.0 · Last updated: 2026-11-16

## Purpose

The cost structure amounts are booked and planned against: profit and cost centers as one tree,
cost types, orders (CAPEX or OPEX) and keys spreading shared cost centers. Used by controllers,
and by Finance and Budget through the API.

## Depends on

| Module | Versions | What for |
|---|---|---|
| `sys` | `>=0.42,<1` | Hierarchies, files, the SDK |

Core dependency: no

## Tables

| Table | What a record is | Main fields | Notes |
|---|---|---|---|
| `costing.center` | A profit or cost center | code, name, kind, parent, profit_center, responsible, shared, active | A hierarchy (`parent`); `profit_center` is worked out, never typed |
| `costing.cost_type` | A kind of cost or income | code, name, group, kind, chargeable, active | |
| `costing.order` | A piece of work | code, name, kind (capex, opex), center, state, start, end, useful_life | Files attached; CAPEX needs `useful_life` |
| `costing.key` | How a shared center is spread | name, center, unit, active | The center must be shared |
| `costing.key_share` | One center's share in a key | key, center, value | Unique per key and center; to a cost center other than the spread one |

## Roles and access

| Role | Can | Record conditions / personal data |
|---|---|---|
| `costing.controller` | Everything on all tables | None |
| `costing.reader` | Read all tables | None |

## Views, navigation and dashboards

Lists and forms for every table; the navigator's section "Cost structure" (Centers, Cost types,
Orders, Spreading keys). A center's form lists the centers under it, its orders and keys; a key's
form lists its shares.

## Workflows, approvals and jobs

Orders follow a flow: open → done (Mark done), done → open (Reopen), done → settled (Settle,
Controllers only); other moves are refused. Each move is a button on the order. No jobs.

## API

Only the records API. Finance and Budget read centers (and their `profit_center`), cost types
and orders through it; `within` on `center` finds everything under a center.

## Outside systems

None.
