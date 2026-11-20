# Budget

What's planned per cost center and cost type, and how it compares to what was spent.

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

A line is one cost center and one cost type of a version, in the version's currency. Only cost
centers are planned on, and only a draft version's lines change. Its **rhythm** says how the
amount fills the months, starting with **From month** (January if not given):

| Rhythm | The amount | Example |
|---|---|---|
| Monthly | Every month | Communication, 30 a month |
| Quarterly | Every three months | Insurance, 500 in January, April, July and October |
| Yearly, spread over the months | For the year, spread evenly (the cents left over in December) | Energy, 4,000 a year |
| Once | Once, in that month | A new boiler, 7,500 in September |
| By hand | Not used: you type each month | Heating, more in winter |

The line's form shows its months and its **year total**. Changing the rhythm, the amount or the
month it starts in rewrites the months. With **By hand**, the months stay as they are and you add
or change them in the line's form; the year total adds them up.

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
