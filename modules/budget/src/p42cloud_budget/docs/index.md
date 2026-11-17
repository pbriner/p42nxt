# Budget

What's planned per cost center, cost type and month, and how it compares to what was spent.

## Versions

A version is the **budget** or a **forecast** for a year, in a currency. It goes through:

| State | Meaning | Who moves it on |
|---|---|---|
| Draft | Being planned; its lines change | Planners: **Submit** |
| Submitted | Waiting for approval | Budget controllers: **Approve**, or **Send back** to draft |
| Approved | In force | Budget controllers: **Lock** |
| Locked | Final; nothing changes any more | |

Changes during the year go into a forecast version; an approved budget stays as it was.

## Lines

A line is one cost center, one cost type and one month, with its amount (in its version's
currency). Only cost centers are planned on, and only a draft version's lines change.

**Spread a year** (on a draft version) asks for a cost center's and a cost type's codes and an
amount for the year, and fills in the twelve months evenly (the cents left over in December);
months planned before are replaced.

## Dashboard

Budget → **Dashboard**, for a version you pick: the share of the plan spent per cost center, and
how much of the whole version is used, naming the centers over budget. What was spent comes from
Finance's bookings (running costs in the main currency) when Finance is installed and you may
read it; otherwise only the plans show.

## Who may do what

- **Planner**: drafts versions and their lines, submits them.
- **Budget controller**: also approves, sends back and locks.
- **Budget reader**: reads budgets and the dashboard.

Each reads Costing's centers and cost types.
