# Events

An event tells other modules that something happened: a device was found, a job failed. The
module that publishes it doesn't know who listens; modules that listen don't import it. Each
listening module gets each event **once**, and only if the change that published it was kept.

## Declaring event types

A module declares what it publishes:

```python
events=(EventType("device_found", "A device is found", about="cmdb.device"),),
```

| Part | Meaning |
|---|---|
| `name` | Unique in the module; the type is named `<module>.<name>` |
| `label`, `description` | What it means, for administrators |
| `about` | Optional; the table its events are about |

They become records of `sys.event_type`. The core publishes:

| Type | About | Data |
|---|---|---|
| `sys.job_failed` | The schedule | `schedule`, `job`, `run`, `message`: a scheduled job failed after its last try |
| `sys.job_recovered` | The schedule | `schedule`, `job`, `run`: it worked again after that |

## Publishing

```python
await client.publish("cmdb.device_found", about=("cmdb.device", device["sys_id"]),
                     data={"serial": serial})
```

The event is a record of `sys.event`, written in the same transaction as the change that
publishes it: if the change is undone, so is the event. `data` is JSON of at most 16 KB; it's
readable by administrators, so it holds no secrets. Only declared types of installed modules can
be published.

## Listening

A module declares what it does when an event of a type is published, by the type's full name
(any module's, also one that isn't installed yet):

```python
subscriptions=(Subscription("sys.job_failed", open_task, retries=4),),
```

The handler gets an `EventRun`: `records` (for `Client(run.records)`), the event's ID, `type`,
`data`, `about_table` and `about_id`, when and by whom it was published, and the attempt.
Subscriptions are records of `sys.event_subscription`, one per module and type.

## Delivering

Publishing makes a delivery (`sys.event_delivery`) for each module listening for the type. The
worker hands them out in the order the events were published:

| State | Meaning |
|---|---|
| Waiting | To be handled, now or after a wait |
| Working | Being handled now |
| Done | Handled |
| Failed | The handler failed on its last try |
| Skipped | The module or its subscription was removed |

- The handler runs **as its module's service account** (the core's: the system), in one
  transaction with marking the delivery done: what it changes and "done" are kept together, or
  neither. So an event is never lost and never handled twice.
- When the handler fails, nothing of what it did is kept; it's tried again after 1, 5 and 30
  minutes, then every 2 hours, `retries` times (0 to 10). A failing delivery doesn't hold up
  others.
- After the last try the delivery fails and administrators are notified (`sys.event_failed`).
  **Try again** on a failed delivery starts its tries again.
- A delivery left *Working* by a stopped worker for ten minutes is taken up again; its
  handler's work was undone with it.
- A switched-off module's deliveries wait until it's switched on; a removed module's are
  skipped, and it gets no new ones.

## Seeing them

Administrators read event types, subscriptions, events and deliveries under Automation.
Events whose deliveries are all over are put away after 90 days, with their deliveries, by a
daily job.
