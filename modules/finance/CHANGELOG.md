# Changelog

## 0.3.1

- A partner, cost center, cost type, order, lease or rental that bookings, statements or assets
  use can't be deleted. Deleting a statement deletes its lines, and deleting an asset its
  write-offs, after a confirmation that lists them; restoring brings them back. Needs the core
  0.46.

## 0.3.0

- The cost dashboard offers a center at the top: every chart and figure then shows that center and
  everything under it, so a profit center (a house) can be looked at alone. **All** shows
  everything.
- New charts, each for the last 3 months, the last year or all of it: running costs by profit
  center, adding value (CAPEX) by profit center, adding value by order (each investment project),
  costs by supplier. Needs the core 0.45.

## 0.2.0

- Assets: what an order adding value (CAPEX) became, worth what its CAPEX bookings cost (in the
  main currency) and written off evenly over the order's useful life. **Write off a year** books
  the depreciation as a cost on the asset's center (a part year by months in use, once per year,
  never more than the book value left).
- The cost dashboard (Finance → Dashboard): results per profit center this year, costs by cost
  type, cost center and month, adding value this year, unpaid invoices.

## 0.1.0

- Bookings on a cost type and a cost center or an order, with the partner, the amount in its
  currency, the invoice attached and comments; the profit center, cost or income, CAPEX and the
  amount in the main currency (with the day's rate) are worked out. Nothing is booked on a settled
  order. Rentals link a lease to its flat; running-cost statements collect a period's chargeable
  costs (the flat's own and its part of shared centers by Costing's keys) less the advances paid,
  and are made final when sent. Roles Bookkeeper and Finance reader (each reading Costing and
  Partners). Needs the core 0.43, Costing 0.3 and Partners 0.1.
