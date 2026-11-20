# Changelog

## 0.2.0

- A budget line is one cost center and cost type of a version, with a **rhythm**: monthly,
  quarterly, yearly (spread over the months), once or by hand, an amount and the month it starts
  in. Its months follow from the rhythm and show in the line's form; by hand, they're typed
  there. Each line shows its year total, and the dashboard compares those with what was spent.
- **Spread a year** is gone: the rhythm "yearly" does the same.
- The lines of 0.1 (one per month) are moved on the upgrade: one line per cost center and cost
  type, monthly when its twelve months are equal, else by hand with the months kept. Lines are
  now at `/api/v1/budget/plan_line`, their months at `/api/v1/budget/month`.

## 0.1.1

- Deleting a budget version deletes its lines with it, after a confirmation that lists them;
  restoring the version brings them back. A cost center or cost type with budget lines can't be
  deleted. Needs the core 0.46.

## 0.1.0

- Budget versions (budget or forecast for a year) going from draft to submitted, approved and
  locked (approving, sending back and locking by Budget controllers); lines per cost center, cost
  type and month, changing only in drafts; **Spread a year** fills the months from a yearly
  amount. The dashboard compares a version with what Finance booked, when Finance is there.
  Roles Planner, Budget controller and Budget reader. Needs the core 0.43 and Costing 0.3.
