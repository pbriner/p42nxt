# Changelog

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
