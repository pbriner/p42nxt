# Kraken DCA (`dca`)

Version: 0.4.0 · Last updated: 2026-11-14


## Purpose

Dollar cost averaging on each person's own Kraken account: a plan buys a little at a time,
spread evenly until the next deposit (or an end date). Dry runs until the owner switches on
real orders, which the installation must allow too. A test installation reads Kraken but holds
real orders (they end as failed, with the reason).

## Depends on

| Module | Versions | What for |
|---|---|---|
| `sys` | `>=0.37.3,<1` | Always; connections (one in particular), jobs, notifications, personal tables |

Core dependency: no

## Tables

| Table | What a record is | Main fields | Notes |
|---|---|---|---|
| `dca.pair` | A pair Kraken trades, paid in CHF, EUR or USD | Kraken's name, pair, buys, pays with, kind (coin, tokenized stock), offered | Read by investors; written by the module only (the six BTC and ETH pairs from the install, the rest read daily) |
| `dca.plan` | A plan | name, pair (a `dca.pair`), amount, amount is in (what it buys or what it pays with), mode, deposit day, buy hour, end date, max price, monthly limit, price tiers, active; what the cycle found (next buy, buys left, last price, free balance, last said) | Personal (owner) |
| `dca.tier` | A price tier of a plan | plan, price change %, amount (0: skip), on | Personal |
| `dca.order` | A buy: a dry run or a real order (sending, bought, answer lost, failed) | plan, why, state, amount, price, cost, fee, currency, when | Personal; written by the cycle only |

## Roles and access

| Role | Can | Record conditions / personal data |
|---|---|---|
| `dca.user` (DCA investor) | Make and change their plans and tiers; read their orders and the pairs | Everything is personal: only the owner sees it, administrators included |
| `dca.keeper` (DCA cycle) | Read plans and tiers, write what the cycle found, write orders and pairs | Only the module's service account (the cycle) gets it |

## Views, navigation and dashboards

Finance → Kraken DCA: Dashboard, Plans, Orders, Pairs, New plan. The dashboard (`/dashboard/dca`)
shows one plan at a time, chosen above the widgets (kept in the address): 12 figures and 7
charts, all source widgets of the module's routes, each also available for the home dashboard.
They count real orders, or the dry runs while there are none. The plan's form: Plan, When, Limits, Price
tiers, What it found; its tiers and orders as related lists.

## Workflows, approvals and jobs

The job `dca.pairs` (schedule "Kraken's pairs, daily"): reads Kraken's pairs (AssetPairs,
through the first plan's connection that answers) and keeps `dca.pair` up to date: new ones
added, gone ones marked as no longer offered (a plan can't choose those). Upgrade 0.9.0 links
plans to the pair record of their former fixed choice.

The job `dca.cycle` (schedule "DCA cycle", every 5 minutes): a plan whose amount is in what it
pays with buys what that amount (and its tiers' amounts) pays for at the current price. for each active plan, with the
plan's Kraken connection (`connection`: one of its owner's own; empty: the owner's only one), it reads the price, the pair's minimums and the free balance, spreads
the balance (less this period's dry runs) until the period ends, applies the price tiers, the max
price and the monthly limit, and records a dry run when a buy is due. A buy the free
balance doesn't cover is cut to what it covers, less a 1 % margin for the fee and the price
moving; below Kraken's minimum it's skipped. The planned next buy is
kept until it's due; one missed by more than 48 hours is planned again instead. A plan that
can't be checked says "Problem: …" and notifies its owner once. Saving a plan never buys; the
next check plans again.

Real orders (the plan's **Real orders**, its owner's **My Kraken key can't withdraw**, a
connection whose check passed, the setting `dca.live_allowed`, and the stage `production`): the
cycle records the order (state "sending") and the outside call `dca.place_order` in one
transaction. The worker sends a market buy with the order's ID as Kraken's client order ID
(`cl_ord_id`; calls queued before 0.6.0 carry a number, `userref`); a lost answer is reconciled
by that reference (Open and Closed orders) before anything is sent again;
the fill takes Kraken's volume, price, cost and fee. Kraken's refusal, an order still unclear
after the last try, or an order open for more than 30 minutes puts the plan on hold (`dca.hold`)
until its owner presses **Resume**. While an order is unclear the plan doesn't buy.

Right before each real buy:

- **Another system:** a buy on the pair, open or done, that isn't one of the owner's orders (by
  Kraken's order ID or client order ID), within the cooldown (24 hours by default) and since the
  last **Resume** (`others_from`), puts the plan on hold. Sells don't count.
- **Market:** if buying and selling prices are more than 2 % apart, or the last price is more
  than 10 % from yesterday's close (daily candles), the buy waits for the next check ("Waiting:
  …"; the owner is told once, `dca.problem`).

Targets: a plan's profit target and loss limit (in % or the currency, on profit after fees) are
checked after each cycle, inactive plans too; each one reached notifies the owner once
(`dca.target`) and is kept in `targets_hit` until it's no longer reached.

Plans and orders imported from a previous installation keep where they came from (`source_ref`,
read only).

Setting: `dca.live_allowed` ("Real DCA orders allowed"), off by default, public.

## API

`GET /api/v1/dca/widget/<name>?plan=<id>`: the dashboard's figures and charts (source widgets).
The plan is read as the caller, so only its owner gets an answer. Names: running_mode, balance,
invested, bought, average_price, current_value, profit, fees, last_buy, next_buy, planned_buys,
targets, profit_over_time, invested_per_month, buy_prices, average_price_over_time, holdings,
invested_over_time, buys_by_type.

## Outside systems

Kraken (`dca.kraken`, `api.kraken.com` only): the owner's own connection, secrets `api_key` and
`api_secret`, requests signed the Kraken way (HMAC-SHA512, a growing nonce). Read: Ticker,
AssetPairs (minimum volume and cost, volume decimals), BalanceEx (free = balance − held by
orders). Changed: AddOrder (market buys, with a reference of ours); read about orders: QueryOrders,
OpenOrders and ClosedOrders (by our reference). The key needs Query Funds and Create & Modify
Orders, never Withdraw.
