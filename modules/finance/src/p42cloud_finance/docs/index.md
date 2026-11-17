# Finance

What was spent and earned, booked against the cost structure (Costing), with the partners
(Partners) and the original invoices; and the running costs charged on to tenants.

## Bookings

A booking has its **date**, a **text**, the **partner**, the **amount** in its currency, the
**invoice number** and when it was **paid**. It's booked on a **cost type** and a **cost center**,
or on an **order** (then on the order's center). Attach the invoice; comment on it.

What follows from its answers is worked out, never typed:

- the **profit center** its result counts for, from the cost center;
- **cost or income**, from the cost type (amounts are always above zero);
- **adds value (CAPEX)**, from the order;
- the amount **in the main currency**, with the exchange rate of the booking's day. Without a
  rate for that day (or the 10 days before), it stays empty until a rate is entered.

Nothing is booked on a settled order: its costs are final.

## Rentals

A rental links a **lease** (a contract of kind lease, in Partners) to the **flat** it's for (a
cost center).

## Running-cost statements

A statement covers a rental and a period. **Work out** collects:

- the running costs of the period whose cost type **may be charged on**: those booked on the flat
  itself, and its part of each shared center spread over it by an active key (m², units …);
  adding-value costs (CAPEX) never count;
- the **advances** the tenant paid: the lease's advance times the periods covered (whole months).

The result is **to pay** (or to refund, if negative). Working it out again replaces its lines.
**Make final** when it's sent: then it isn't worked out again.

## Assets

What an order **adding value** became: a roof, a heating, new software. An asset names its order,
when it came **into use**, and the cost type its depreciation is booked as. Its **value** is what
the order's CAPEX bookings cost (in the main currency), written off evenly over the order's useful
life: the **yearly** amount.

**Write off a year** books that year's depreciation as an ordinary cost on the asset's cost center
(on 31 December), so results show it: a part year by the months in use, once per year, never more
than the **book value** left.

## Dashboard

Finance → **Dashboard**: the result of each profit center this year (income less running costs),
running costs and what was spent adding value per profit center, adding value per order (each
investment project), costs per supplier, costs by cost type, by cost center and by month, what was
spent adding value this year, and the invoices not paid yet.

Pick a **center** at the top to see only that center and everything under it: a profit center
such as a house with all its cost centers, or a single cost center. **All** shows everything.
Charts with **3 months**, **1 year** and **All** show that time span.

## Who may do what

- **Bookkeeper**: books, keeps rentals, works out statements; reads Costing and Partners.
- **Finance reader**: reads all of it.
