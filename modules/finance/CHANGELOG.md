# Changelog

## 0.1.0

- Bookings on a cost type and a cost center or an order, with the partner, the amount in its
  currency, the invoice attached and comments; the profit center, cost or income, CAPEX and the
  amount in the main currency (with the day's rate) are worked out. Nothing is booked on a settled
  order. Rentals link a lease to its flat; running-cost statements collect a period's chargeable
  costs (the flat's own and its part of shared centers by Costing's keys) less the advances paid,
  and are made final when sent. Roles Bookkeeper and Finance reader (each reading Costing and
  Partners). Needs the core 0.43, Costing 0.3 and Partners 0.1.
