# Kraken DCA (`dca`)

Version: 0.1.0 · Last updated: 2026-11-14


## Purpose

Dollar cost averaging on each person's own Kraken account: a plan buys a little at a time,
spread evenly until the next deposit (or an end date). In 0.1.0 every buy is a dry run:
recorded and notified, never sent to Kraken.

## Depends on

| Module | Versions | What for |
|---|---|---|
| `sys` | `>=0.19,<1` | Always; connections, jobs, notifications, personal tables |

Core dependency: no

## Tables

| Table | What a record is | Main fields | Notes |
|---|---|---|---|
| `dca.plan` | A plan | name, pair, amount, mode, deposit day, buy hour, end date, max price, monthly limit, price tiers, active; what the cycle found (next buy, buys left, last price, free balance, last said) | Personal (owner) |
| `dca.tier` | A price tier of a plan | plan, price change %, amount (0: skip), on | Personal |
| `dca.order` | A buy (a dry run in 0.1.0) | plan, why, state, amount, price, cost, fee, currency, when | Personal; written by the cycle only |

## Roles and access

| Role | Can | Record conditions / personal data |
|---|---|---|
| `dca.user` (DCA investor) | Make and change their plans and tiers; read their orders | Everything is personal: only the owner sees it, administrators included |
| `dca.keeper` (DCA cycle) | Read plans and tiers, write what the cycle found, write orders | Only the module's service account (the cycle) gets it |

## Views, navigation and dashboards

Finance → Kraken DCA: Plans, Orders, New plan. The plan's form: Plan, When, Limits, Price
tiers, What it found; its tiers and orders as related lists.

## Workflows, approvals and jobs

The job `dca.cycle` (schedule "DCA cycle", every 5 minutes): for each active plan, with its
owner's Kraken connection, it reads the price, the pair's minimums and the free balance, spreads
the balance (less this period's dry runs) until the period ends, applies the price tiers, the max
price and the monthly limit, and records a dry run when a buy is due. The planned next buy is
kept until it's due; one missed by more than 48 hours is planned again instead. A plan that
can't be checked says "Problem: …" and notifies its owner once. Saving a plan never buys; the
next check plans again.

## API

None.

## Outside systems

Kraken (`dca.kraken`, `api.kraken.com` only): the owner's own connection, secrets `api_key` and
`api_secret`, requests signed the Kraken way (HMAC-SHA512, a growing nonce). Read: Ticker,
AssetPairs (minimum volume and cost, volume decimals), BalanceEx (free = balance − held by
orders). Nothing is changed on Kraken in 0.1.0.
