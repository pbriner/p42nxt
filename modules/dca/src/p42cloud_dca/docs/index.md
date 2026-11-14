# Kraken DCA

Buy a little bitcoin or ether at a time, spread evenly until your next deposit, on your own
Kraken account. In this version every buy is a dry run: it's recorded and you're notified, but
nothing is sent to Kraken yet.

## Connect your Kraken account

1. On Kraken, make an API key that may only **Query Funds** (and later **Create & Modify
   Orders**). Leave **Withdraw Funds** off.
2. In the app, open **Connections**, add a connection of the type **Kraken**, and add its two
   secrets: `api_key` and `api_secret`.
3. Press **Check**: it should say "Kraken accepts the keys."

Nobody else sees your connection or your plans, administrators included.

## Make a plan

Kraken DCA → **New plan**: the pair (for example BTC / CHF), the amount per buy (in bitcoin or
ether), and until when the money should last: your next **deposit day** (at the hour you choose,
UTC), or an **end date**. Optional: a price it never buys above, and the most it spends per month
(from deposit day to deposit day).

## When does it buy?

Every 5 minutes each active plan is checked. The free balance on Kraken (in the plan's currency)
is spread evenly until the period ends, so the last buy lands on the deposit day or the end
date. **Next buy** shows when the next one is due, and **Last said** what the last check found.
Changing the plan plans again at the next check; saving never buys.

## Price tiers

Turn on **Price tiers** and add tiers to the plan: a price change against your last buy (or your
average buy price) and the amount to buy then. A tier of −10 % with a bigger amount buys more
when the price drops; a tier of +10 % with 0 skips buys when it rose. The deepest drop reached
wins, or the highest rise. An extra buy happens when a tier asks for more than the usual amount,
at most once per cooldown (24 hours unless you set it).

## Orders

Every buy is an order under Kraken DCA → **Orders**, and a notification. Dry runs use your real
balance and real prices, so you see what the plan would do.
