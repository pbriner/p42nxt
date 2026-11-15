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
- **Recipients** are the people named, everyone holding one of the roles named (through their
  groups, or a role that includes it) and the members of the groups named (`groups=[…]`, IDs of
  `sys.group`). Only active people; never service accounts.
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

## Telegram

People can get notifications as Telegram messages too, chosen per kind. The app always gets
everything.

**The bot.** One per installation. An administrator creates it with Telegram's @BotFather, then
adds a connection of the type **Telegram bot** (`sys.telegram`) with the secret `token`, and
**Check**s it. It's a shared connection (`connections.md`): only administrators set it up, there
is only one, and only the platform uses it. The token goes into the request's path, so requests
to Telegram are never logged and their errors are kept by kind only.

**Linking a chat.** Profile → Notifications → **Connect Telegram**. The app makes a random
one-time code and shows it as a link (`t.me/<bot>?start=<code>`); only the code's hash is
stored, for 15 minutes (`sys.person_channel`, personal). The person presses Start in Telegram;
the bot links that chat and answers "Connected to … as …". The chat is never shown and is masked
in the audit log. `/stop` in Telegram or **Disconnect** in the profile unlinks it; a chat that
blocked the bot is unlinked at the next message.

**Choosing.** A kind has default channels (`NotificationKind(..., channels=("telegram",))`);
administrators may change them (Automation → Notification kinds), and an upgrade replaces only
channels nobody changed. A person's own choice is a record of `sys.notification_subscription`
(personal), set with the switches in the profile; without one, the kind's default applies.

**What's sent.** The severity, the title and, if the app has a public address
(the setting `sys.public_url`), the link. The message only with **Send the whole message** (off by default:
details such as amounts stay in the app, since Telegram is a third party). Plain text only.

**Delivery.** Sending a notification queues a delivery per person and channel in the same
transaction (`sys.notification_delivery`, Automation → **Notification deliveries**). The worker
sends what's due, trying again after 30 seconds, 2, 10 and 30 minutes, or when Telegram asks
(429). At least once: a stop between sending and saving sends again.

**Receiving.** The worker asks Telegram for the bot's messages (no webhook needed): every 5
seconds while someone waits to link a chat, otherwise every minute.

## Cleanup

The daily job `sys.notification_cleanup` (03:40 UTC) puts away read notifications after 90 days
and unread ones after a year.

## The core's kinds

| Kind | To | When |
|---|---|---|
| `sys.job_failed` | Administrators, also on Telegram by default | A schedule starts failing (`jobs.md`) |
| `sys.message` | A message's audience (everyone, or a group) | A message shown as a notification becomes valid (`screens.md`) |
| `sys.module_roles` | Administrators | A module is installed: the roles it brings, which nobody holds yet, to give to groups |
