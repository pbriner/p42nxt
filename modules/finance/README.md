# Finance (`finance`)

Version: 0.1.0 · Last updated: 2026-11-17

## Purpose

Bookings of costs and income against the cost structure, with partners and invoices; rentals and
the running-cost statements charged on to tenants.

## Depends on

| Module | Versions | What for |
|---|---|---|
| `sys` | `>=0.43,<1` | Files, comments, states, amounts and exchange rates |
| `costing` | `>=0.3,<1` | Cost types, centers, orders, spreading keys |
| `partners` | `>=0.1,<1` | Partners and leases |

Core dependency: no

## Tables

| Table | What a record is | Main fields | Notes |
|---|---|---|---|
| `finance.booking` | A cost or income | date, text, partner, cost_type, center, order, currency, amount, invoice_number, paid | Worked out: profit_center, kind, capex, main_currency, amount_main; files and comments |
| `finance.rental` | A lease for a flat | contract (a lease), center (a cost center), active | |
| `finance.statement` | A running-cost statement | rental, start, end, state; worked out: currency, costs, advances, due | Flow draft → final; files and comments |
| `finance.statement_line` | One line of it | statement, kind (cost, advance), cost_type, text, currency, amount | Written by Work out |

## Roles and access

| Role | Can | Record conditions / personal data |
|---|---|---|
| `finance.bookkeeper` | Everything on Finance's tables; includes `costing.reader`, `partners.reader` | |
| `finance.reader` | Read Finance's tables; includes the same readers | |

## Views, navigation and dashboards

Lists and forms for bookings, rentals and statements (a statement's form lists its lines); the
navigator's section "Finance".

## Workflows, approvals and jobs

A statement: draft → final (Make final). The action **Work out** (drafts only) writes its lines
and totals. No jobs.

## API

Only the records API and the action `finance.work_out`.

## Outside systems

None. Amounts in the main currency use the core's exchange rates.
