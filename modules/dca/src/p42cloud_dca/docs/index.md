# Kraken DCA

Buy a little at a time, spread evenly until your next deposit, on your own Kraken account:
coins like bitcoin, or tokenized stocks like NVDAx where Kraken offers them to you. A plan makes dry runs until you switch on real orders.

## Connect your Kraken account

1. On Kraken, make an API key that may only **Query Funds** (and later **Create & Modify
   Orders**). Leave **Withdraw Funds** off.
2. In the app, open **Connections**, add a connection of the type **Kraken**, and add its two
   secrets: `api_key` and `api_secret`.
3. Press **Check**: it should say "Kraken accepts the keys."

Nobody else sees your connection or your plans, administrators included.

## Make a plan

Kraken DCA → **New plan**: the pair (type to find it, for example BTC / CHF or NVDAx / USD),
the amount per buy, and until when the money should last: your next **deposit day** (at the hour you choose,
UTC), or an **end date**. Optional: a price it never buys above, and the most it spends per month
(from deposit day to deposit day).

**Amount is in** says what the amount counts: what it buys (0.001 BTC) or what it pays with
(50 USD). A plan for a stock starts with what it pays with, since Kraken sells stocks in
fractions for an amount of money; at each buy the app works out how much that buys now.

The pairs come from Kraken: **Pairs** lists them, read every day (the job **Read Kraken's
pairs**, which an administrator can also start by hand once you have a plan). A tokenized stock
follows the share's price but isn't the share itself.

## When does it buy?

Every 5 minutes each active plan is checked. The free balance on Kraken (in the plan's currency)
is spread evenly until the period ends, so the last buy lands on the deposit day or the end
date. **Next buy** shows when the next one is due, and **Last said** what the last check found.
Changing the plan plans again at the next check; saving never buys.

If a buy (or a tier's bigger amount) costs more than the free balance, it buys what the
balance covers, keeping 1 % free for the fee and the price moving. If even that is below
Kraken's minimum, the buy is skipped.

## Price tiers

Turn on **Price tiers** and add tiers to the plan: a price change against your last buy (or your
average buy price) and the amount to buy then. A tier of −10 % with a bigger amount buys more
when the price drops; a tier of +10 % with 0 skips buys when it rose. The deepest drop reached
wins, or the highest rise. An extra buy happens when a tier asks for more than the usual amount,
at most once per cooldown (24 hours unless you set it).

## Orders

Every buy is an order under Kraken DCA → **Orders**, and a notification. Dry runs use your real
balance and real prices, so you see what the plan would do.

## Dashboard

Kraken DCA → **Dashboard** shows one plan at a time: choose it above the widgets. Its figures
(invested, bought, average price, current value, profit or loss after fees, fees, next buy …) and
charts (profit or loss by day, invested per month, holdings …) count your real orders, or your dry
runs while there are none. **Customize** adds or arranges widgets; each can go on your home
dashboard too.

## Targets

Set a **profit target** or a **loss limit** on a plan, in % or in its currency, on the profit
after fees. You're notified once when one is reached, and again if it's reached after having been
left. Inactive plans are checked too.

## Real orders

1. Make sure your Kraken key can't withdraw money, and tick **My Kraken key can't withdraw**.
2. Check your Kraken connection (Connections → **Check**).
   With several Kraken accounts, make a connection for each and choose it on the plan
   (**Kraken connection**). With one, plans use it without asking.
3. Switch on **Real orders** on the plan.

An administrator must also allow real orders for the whole installation (Settings → **Real DCA
orders allowed**); until then the plan says so instead of buying.

Each order is recorded before it goes to Kraken, with a reference of its own. If Kraken's answer
is lost, the app asks Kraken about that reference before sending anything again, so an order is
never placed twice. The order then shows Kraken's own price, cost and fee.

Right before each real buy, the app also checks:

- **Nobody else buys the same pair.** If Kraken shows a buy on the pair that this app didn't
  place, for example from another program or one you made by hand, the plan goes on hold. Sells
  don't count.
- **The market looks normal.** If buying and selling prices are more than 2 % apart, or the price
  is more than 10 % away from yesterday's close, the buy waits and is tried again at the next
  check. You're told once.

## A plan is on hold

When Kraken refuses an order (for example, not enough money) or an order stays unclear, the plan
goes **on hold** and you're notified. It doesn't buy until you've looked: read **Why on hold**,
check the order on Kraken, then press **Resume**. Buys by others from before you pressed Resume
don't hold the plan again.
