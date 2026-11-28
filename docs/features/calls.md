# Outside calls

An outside call is a change a module makes in another system: an order at a broker, a ticket
elsewhere. It's made **only if the module's own change is kept**, and **at most once**, even if
an answer is lost on the way.

## Declaring a call

```python
calls=(OutsideCall(
    "place_order", "Place an order", connection_type="kraken",
    send=place, reconcile=find_by_key, done=mark_placed, retries=3,
),),
```

| Part | Meaning |
|---|---|
| `connection_type` | One of the module's own connection types; the owner's connection of it is used |
| `send` | Makes the call with a ready client and returns the answer; raises `CallRefused` when the other side says no for good |
| `reconcile` | Asks the other side, by the call's key, whether the call happened: the answer if it did, `None` if it didn't |
| `done` | Optional; reacts to the answer in one transaction, as the module's service account |
| `retries` | How often it's tried again after the first attempt (0 to 10) |
| `gave_up` | Optional; reacts when the call ends without an answer (below) |

They become records of `sys.call_type`. Each call carries a unique `key`; `send` passes it to
the other side (a client reference, an idempotency key) so that `reconcile` can find it.

## Recording a call

```python
await client.call("dca.place_order", {"pair": "XBTCHF", "amount": "50"},
                  owner=plan["owner"], about=("dca.plan", plan["sys_id"]))
```

The call is a record of `sys.outside_call`, written in the same transaction as the change that
asks for it: if the change is undone, so is the call. A person records calls for themselves; a
module's jobs may record its calls for the people they work for. `about` links the call to the
record it's about.

## Delivering

The worker delivers calls in turn with job runs:

| State | Meaning |
|---|---|
| Waiting | Recorded, or to be sent again: nothing reached the other side |
| Sending | Being sent now |
| Answer lost | Sent, but no answer came (a timeout, a worker stopping mid-call) |
| Answered | The other side answered; the module's `done` hasn't finished yet |
| Done | Answered, and `done` has run |
| Failed | The other side said no for good, or there were no tries left |
| Held | A test installation: never sent ([Silent mode](silent-mode.md)); the module's `gave_up` hears `not_sent` |

- When **nothing could reach** the other side (no connection), the call waits and is sent again.
- When **the answer is lost**, the next attempt first asks with `reconcile`. Only if the call
  didn't happen is it sent again; if it did, its answer is used. A call left *Sending* by a
  stopped worker for five minutes counts as lost.
- When **`done` fails**, the answer is kept and only `done` is tried again.
- Each try waits longer: 30 seconds, then 1, 2, 4 minutes and so on.
- **Ending without an answer**: the module's `gave_up` hears why (`GaveUp`): `refused` (the
  other side said no; failed), `not_sent` (no try reached the other side; failed) or `unclear`
  (the last try's answer was lost: it may have happened; it stays *Answer lost* and isn't tried
  again), for example to stop until a person has looked.

Each try runs as the module's service account, with at most 60 seconds for the call.

## Seeing calls

In the app: Automation → **Outside calls**. Administrators see every call; everyone else sees
the calls made for them, with their state, tries, last attempt and what was last said.
