# Changelog

## 0.4.0

- Taking over plans, their tiers and every order from a previous installation: add the export
  under Kraken DCA → **Take over**; within a minute the job `dca.take_over` imports it for you.
  Plans arrive inactive and making dry runs, so nothing buys next to the old installation; orders
  keep their time, figures and Kraken order. Taking over the same export again adds only what's
  new. The export isn't kept.
- Plans, the dashboard and the cycle read every order, however many (before: the first 500).

## 0.3.0

- A dashboard (Kraken DCA → Dashboard) for one plan at a time, chosen above the widgets: running
  mode, Kraken balance, invested, bought, average price, current value, profit or loss after
  fees, fees, last and next buy, planned buys, targets; charts of profit or loss by day (Kraken's
  daily closes), invested per month, buy prices, average buy price, holdings, invested over time
  and buys by type. Real orders, or the dry runs while there are none.
- Profit targets and loss limits on a plan (in % or the currency): checked at every cycle, also
  for inactive plans; the owner is notified once each time one is reached (Telegram by default).
- Needs the core 0.21 or later.

## 0.2.0

- Real orders: a plan's owner switches **Real orders** on after confirming their Kraken key
  can't withdraw and checking the connection; the installation's setting **Real DCA orders
  allowed** (off by default) must allow it too, and only the production installation sends.
- A real order is recorded first with a random reference and sent by the worker as an outside
  call: made at most once, asked about by its reference if an answer is lost, filled with
  Kraken's own volume, price, cost and fee.
- Kraken's refusal, or an order still unclear, puts the plan **on hold** (notified, Telegram by
  default) until its owner presses **Resume**; a plan never buys while one of its orders is
  unclear.
- Needs the core 0.20 or later.

## 0.1.0

- Plans on each person's own Kraken account (pair, amount per buy, until the next deposit day or
  an end date, max price, monthly limit, price tiers), private to their owner.
- The Kraken connection type, with Kraken's signed requests; Check asks Kraken for the balance.
- A cycle every 5 minutes spreads the free balance until the period ends and buys when due, with
  price tiers and Kraken's minimums per pair. Every buy is a dry run: recorded and notified.
- Needs the core 0.19 or later.
