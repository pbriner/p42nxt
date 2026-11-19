# Partners (`partners`)

Version: 0.1.0 · Last updated: 2026-11-17

## Purpose

The companies and people one works with (suppliers, customers, tenants), their contact persons
and contracts, kept once for every module.

## Depends on

| Module | Versions | What for |
|---|---|---|
| `sys` | `>=0.43,<1` | Files, comments, states, amounts with a currency |

Core dependency: no

## Tables

| Table | What a record is | Main fields | Notes |
|---|---|---|---|
| `partners.partner` | A company or person | name, kind, supplier, customer, tenant, email, phone, address, vat_number, iban, active | Files and comments; IBAN checked; personal data marked |
| `partners.contact` | A contact person | partner, name, role, email, phone, main | Personal data |
| `partners.contract` | A contract | partner, name, kind, state, start, end, notice_months, currency, amount, period, advance | Files and comments; a state flow |

Deleting a partner deletes its contact persons with it (after a confirmation listing them); a
partner with contracts can't be deleted.

## Roles and access

| Role | Can | Record conditions / personal data |
|---|---|---|
| `partners.manager` | Everything on all tables | Personal data: addresses, emails, phones, IBANs |
| `partners.reader` | Read all tables | |

## Views, navigation and dashboards

Lists and forms for every table; the navigator's section "Partners". A partner's form lists its
contact persons and contracts.

## Workflows, approvals and jobs

A contract goes draft → in force (Put in force) → ended (End). No jobs.

## API

Only the records API.

## Outside systems

None.
