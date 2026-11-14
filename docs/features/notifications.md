# Notifications

A module tells people something: an approval waits, an order went through, a job failed. People
see their notifications in the app, under the bell.

## Notification kinds

A module declares what it tells people about:

```python
notification_kinds=(NotificationKind(
    "order_placed", "An order went through", "When a plan's order was placed at the broker.",
),),
```

They become records of `sys.notification_kind` (Platform → Automation → **Notification kinds**,
for administrators), named `<module>.<name>`.

## Sending

```python
await client.notify(
    "dca.order_placed", "Your order went through",
    people=[plan["owner"]], roles=["dca.admin"],
    message="50 CHF of bitcoin.", link=f"/form/dca.plan/{plan['sys_id']}", severity="success",
)
```

- The notification is written in the **same transaction** as the change that sends it: if the
  change is undone, nobody is notified.
- **Recipients** are the people named plus everyone holding one of the roles named (through
  their groups, or a role that includes it). Only active people; never service accounts.
  `notify` returns how many people it reached.
- `title` (up to 200 characters) is required; `message` (up to 2000) is optional.
- `severity`: `info` (the default), `success`, `warning` or `error`.
- `link` is a page of the app (`/form/…`, `/list/…`). Links to other sites are refused.

## The inbox

Each notification is a record of `sys.notification` for one person. It's **personal**: only that
person sees it, administrators included. People can mark their notifications read or unread
(**Mark as read**, **Mark as unread**) and put them away, but not change or create them.

My work → **Notifications** lists them all, read ones too.

## The bell

The bell in the top bar shows how many notifications are unread (99+ at most, nothing at none)
and the latest ten. Opening one marks it read and follows its link. **Mark all as read** marks
the ones shown. **Show all** opens the list.

The bell asks once a minute. These requests don't keep a session alive (`sign-in.md`).

## Cleanup

The daily job `sys.notification_cleanup` (03:40 UTC) puts away read notifications after 90 days
and unread ones after a year.

## The core's kinds

| Kind | To | When |
|---|---|---|
| `sys.job_failed` | Administrators | A schedule starts failing (`jobs.md`) |
