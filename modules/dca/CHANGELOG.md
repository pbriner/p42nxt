# Changelog

## 0.1.0

- Plans on each person's own Kraken account (pair, amount per buy, until the next deposit day or
  an end date, max price, monthly limit, price tiers), private to their owner.
- The Kraken connection type, with Kraken's signed requests; Check asks Kraken for the balance.
- A cycle every 5 minutes spreads the free balance until the period ends and buys when due, with
  price tiers and Kraken's minimums per pair. Every buy is a dry run: recorded and notified.
- Needs the core 0.19 or later.
