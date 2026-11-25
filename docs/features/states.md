# States

Some records go through states: an order is open, then done, then settled; an invoice is a
draft, then sent, then paid. A module declares the states a choice field goes through and the
only moves allowed; every change through the records service (the API, the app, modules' code)
is held to them.

## Declaring a flow

```python
flows=(Flow("costing.order", "state", initial="open", transitions=(
    Transition("finish", "Mark done", "open", "done"),
    Transition("reopen", "Reopen", "done", "open"),
    Transition("settle", "Settle", "done", "settled", roles=("costing.controller",)),
    Transition("cancel", "Cancel", None, "cancelled",
               condition={"field": "booked", "op": "eq", "value": 0}),
)),)
```

| Part | Meaning |
|---|---|
| `table`, `field` | One of the module's tables and a choice field of it; one flow per field |
| `initial` | The state every new record starts in |
| `Transition(name, label, from, to)` | An allowed move; `from` None: from any state |
| `roles` | Only people holding one of these roles may make the move; empty: whoever may change the record |
| `condition` | Only while the record matches (the filter language), as it is before the move |
| `icon`, `confirm` | For its button |
| `approval` | The move needs other people's yes first: its button asks for it (`approvals.md`) |

Flows and transitions become records of `sys.state_flow` and `sys.state_transition`
(administrators read them).

## What's held

- A new record starts in the initial state; given another one, it's refused ("A new record starts
  in Open"). Without one, it's set.
- A change of the state is allowed only along a transition from the state the record is in, by
  someone with one of its roles, while the record matches its condition. Otherwise: "From Open
  it can't go to Settled", or "… now, or not by you".
- Changing other fields isn't a move. The system itself (installs, data migrations) isn't held,
  so data can be brought in as it is.
- Each change of state is in the record's history, like any change.

## Buttons

Each transition is also a button on the record's form and its list row (a UI action named
`move_<name>`, setting the state), shown only where the record is in the state it moves from and
matches its condition. A module's own actions can't take those names.
