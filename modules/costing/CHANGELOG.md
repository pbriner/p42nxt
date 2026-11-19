# Changelog

## 0.3.1

- A cost center in use by an order, a key or a key's share can't be deleted; deleting a key
  deletes its shares with it, after a confirmation that lists them. Needs the core 0.46.

## 0.3.0

- Centers and orders have comments: a conversation kept with the record. Needs the core 0.43.

## 0.2.0

- An order's state follows its flow: open, then done (Mark done), back to open (Reopen), or
  settled (Settle, Controllers only). Other moves are refused; each move is a button. Needs the
  core 0.42.

## 0.1.0

- Profit and cost centers as one tree, each with its responsible person; a cost center knows
  the profit center it belongs to. Cost types (costs and income, grouped, chargeable or not),
  orders for pieces of work (adding value or upkeep, with their files), and keys for spreading
  a shared cost center over the ones it serves. Roles Controller and Costing reader. Needs the
  core 0.40.
