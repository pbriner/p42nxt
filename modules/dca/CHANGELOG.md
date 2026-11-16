# Changelog

## 0.8.1

- Says who makes it (publisher `p42`), so installations take its updates only from that
  publisher. Needs the core 0.38.

## 0.8.0

- A plan names its Kraken connection (**Kraken connection**, on the form under Plan): one of its
  owner's own, never anyone else's. A new plan takes its owner's only one; plans from before
  keep trading through their owner's only connection until one is chosen. Real orders need the
  plan's own connection to have passed its check, and the cycle trades through exactly that
  account. So someone with several Kraken accounts runs a plan on each. Needs the core 0.37.3.

## 0.7.2

- The module says its own version again: 0.6.0 to 0.7.1 still called themselves 0.5.0 inside,
  so they were published as 0.5.0 and installations never saw them. The version now comes from
  the package, in one place. 0.7.2 brings everything since 0.5.0 (the checks before real buys,
  Kraken client order IDs, German). Needs the core 0.37.2.

## 0.7.1

- The same as 0.7.0, published again: 0.6.0 and 0.7.0 were left out of the registry's list, so
  installations didn't see them. 0.7.1 brings both (0.5.0 updates straight to it).

## 0.7.0

- German translations of everything the module defines (tables, fields, choices, form
  sections, menus, actions, widgets, roles, notification kinds), shipped in `translations/de.json`. Needs the core 0.37.

## 0.6.0

- Real orders carry the order's own ID as Kraken's client order ID (`cl_ord_id`) and are found
  again by it; calls queued before keep their number (`userref`).
- Before each real buy: a buy on the pair by another system or by hand (open or done, within the
  cooldown, since the last Resume) holds the plan; a gap of more than 2 % between buying and
  selling, or a price more than 10 % from yesterday's close, makes the buy wait for the next
  check (the owner is told once).
- New field `others_from` ("Other orders checked from", set by Resume).
- "At most per month" is called "Monthly limit".

## 0.5.0

- The takeover from a previous installation is gone (its table `dca.takeover` is retired with its
  data, its job and menu entry removed): it was for one migration, done. Imported plans and orders
  keep where they came from (`source_ref`, now read only, labelled "Imported from").
- What the cycle finds (status, next buy, last price, balance, message, targets reached) is shown
  read only in forms. Needs the core 0.34.

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
