# Changelog

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
